"""Gemini 翻譯。"""

import json
import os
import sys

from . import net, transactions as transactions_mod
from .config import (
    GEMINI_API_REVISION,
    GEMINI_DEFAULT_MODEL,
    GEMINI_ENDPOINT,
    GEMINI_FALLBACK_MODEL,
    TEAM_META,
    TEAM_NAMES_EN,
    TEAM_SLANG_ALIASES,
    TRANSLATE_BATCH_SIZE,
)


def _interaction_text(resp):
    """取出 Interactions 回應中最後一個 model_output step 的文字（串接其 text 區塊）；找不到回傳 None。"""
    steps = resp.get("steps") if isinstance(resp, dict) else None
    if not isinstance(steps, list):
        return None
    for step in reversed(steps):
        if isinstance(step, dict) and step.get("type") == "model_output":
            parts = [
                b.get("text") for b in (step.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
            ]
            return "".join(parts) if parts else None
    return None


def build_team_glossary(extra_names=()):
    """英文隊名 -> 中文譯名對照（TEAM_META 中文名＋暱稱簡稱＋當日比賽資料中的英文隊名）。"""
    lines = []
    for tid, (full_en, nick_en, short_zh) in TEAM_NAMES_EN.items():
        lines.append(f"{full_en} -> {TEAM_META[tid]['name']}")
        if nick_en != full_en:
            lines.append(f"{nick_en} -> {short_zh}")
    for tid, aliases in TEAM_SLANG_ALIASES.items():
        short_zh = TEAM_NAMES_EN[tid][2]
        for alias in aliases:
            lines.append(f"{alias} -> {short_zh}")
    seen = {ln.split(" -> ")[0] for ln in lines}
    for tid, name_en in extra_names:
        if tid in TEAM_META and name_en and name_en not in seen:
            seen.add(name_en)
            lines.append(f"{name_en} -> {TEAM_META[tid]['name']}")
    return lines


TRANSLATE_SYSTEM = (
    "你是專業的美國職棒大聯盟（MLB）新聞編譯。請把使用者提供的英文文字翻成繁體中文（台灣用語、體育新聞語氣）。"
    "規則：每則的 keep 清單內的字串必須逐字保留英文原樣出現在譯文中；球員姓名一律保留英文原名；"
    "數據與縮寫（如 HR、RBI、IP、ERA、MVP）保留；球隊名稱必須依下列對照表翻譯；"
    "標題中以逗號或頓號列舉的球隊名稱與簡稱也必須依對照表翻成中文；球員姓名仍保留英文。"
    "不要加入原文沒有的內容，也不要附上英文原文。只輸出 JSON。\n"
    "球隊名稱對照（英文 -> 中文）：\n"
)


def _translate_chunk(chunk, glossary, api_key, model):
    """翻譯一批；回傳與 chunk 等長的 list（單則驗證失敗者為原文），整批失敗回傳 None。"""
    request_items = [{"id": i, "text": it["text"], "keep": list(it.get("keep") or [])} for i, it in enumerate(chunk)]
    payload = {
        "model": model,
        "system_instruction": TRANSLATE_SYSTEM + "\n".join(glossary),
        "input": (
            f"請翻譯以下 {len(request_items)} 則文字。回傳 JSON：{{\"translations\": [字串, ...]}}，"
            "長度與順序必須與輸入完全相同。輸入：\n" + json.dumps(request_items, ensure_ascii=False)
        ),
        "store": False,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": {
                "type": "object",
                "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
                "required": ["translations"],
            },
        },
    }
    headers = {"x-goog-api-key": api_key, "Api-Revision": GEMINI_API_REVISION}
    net.LAST_POST["retryable"] = False
    resp = net.post_json(GEMINI_ENDPOINT, payload, headers)
    if resp is None:
        return None  # post_json 已印出失敗原因；LAST_POST["retryable"] 記錄是否為可重試類型
    text = _interaction_text(resp)
    if not text:
        steps = resp.get("steps")
        step_types = [st.get("type") if isinstance(st, dict) else type(st).__name__ for st in steps] if isinstance(steps, list) else steps
        print(f"Warning: translation response has no model_output text; top-level keys={sorted(resp.keys())}, "
              f"step types={step_types}", file=sys.stderr)
        return None
    try:
        translations = json.loads(text).get("translations")
    except (ValueError, AttributeError):
        print("Warning: translation response text is not valid JSON with a translations field", file=sys.stderr)
        return None
    if not isinstance(translations, list) or len(translations) != len(chunk):
        actual = len(translations) if isinstance(translations, list) else type(translations).__name__
        print(f"Warning: translation length mismatch: expected {len(chunk)}, got {actual}", file=sys.stderr)
        return None
    out = []
    for it, tr in zip(chunk, translations):
        ok = isinstance(tr, str) and tr.strip() and all(k in tr for k in (it.get("keep") or []))
        out.append(tr if ok else it["text"])
    return out


def _fallback_model(primary):
    """備用模型名稱；環境變數 GEMINI_FALLBACK_MODEL 設為空字串則停用；與主模型相同視為無備用。"""
    fb = os.environ.get("GEMINI_FALLBACK_MODEL")
    fb = GEMINI_FALLBACK_MODEL if fb is None else fb.strip()
    return fb if fb and fb != primary else None


# 最近一次 translate_texts 實際用過備用模型的批次數與使用的模型（供 apply_translations 顯示）
LAST_TRANSLATION = {"primary": None, "fallback": None, "fallback_ok": False}


# 本次執行已翻譯成功的人事異動：{英文 description: 中文}（供首頁本週人事異動沿用）
TX_TRANSLATION_CACHE = {}


def translate_texts(items, extra_team_names=()):
    """批次翻譯。items: [{"text": 原文, "keep": [逐字保留的英文詞]}]。
    無金鑰、items 為空、或所有批次皆失敗 -> None（呼叫端用原文）；
    否則回傳等長 list，單則驗證失敗者為原文。"""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not items:
        return None
    model = os.environ.get("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    fallback = _fallback_model(model)
    LAST_TRANSLATION.update(primary=model, fallback=fallback, fallback_ok=False)
    glossary = build_team_glossary(extra_team_names)
    results = []
    any_ok = False
    for start in range(0, len(items), TRANSLATE_BATCH_SIZE):
        chunk = items[start:start + TRANSLATE_BATCH_SIZE]
        out = _translate_chunk(chunk, glossary, api_key, model)
        if out is None and fallback and net.LAST_POST["retryable"]:
            print(f"Warning: 主模型 {model} 持續失敗，改用備用模型 {fallback}", file=sys.stderr)
            out = _translate_chunk(chunk, glossary, api_key, fallback)
            if out is not None:
                LAST_TRANSLATION["fallback_ok"] = True
        if out is None:
            out = [it["text"] for it in chunk]
        else:
            any_ok = True
        results.extend(out)
    return results if any_ok else None


def _keep_in(text, names):
    return [n for n in dict.fromkeys(names) if n and n in text]


def apply_translations(news, views, tx_items, extra_team_names=(), extra_news=()):
    """收集整頁需翻譯的文字，只呼叫一次 translate_texts，譯文寫入 *_zh 欄位。回傳是否成功套用。
    extra_news：不在顯示清單內、但台日韓專區要用的新聞（只翻標題）。相同原文只送翻一次，結果套用到所有對應物件。"""
    targets = []  # (物件, 目標鍵, 原文, keep)
    for n in list(news or []) + list(extra_news or []):
        targets.append((n, "title_zh", n["title"], []))
    for view in views:
        box = view.get("news")
        if not box or not box.get("translatable"):
            continue
        names = [p["name"] for p in view.get("batters", []) + view.get("pitchers", [])]
        for src, dst in (("headline", "headline_zh"), ("blurb", "blurb_zh")):
            targets.append((box, dst, box[src], _keep_in(box[src], names)))
    for tx in tx_items:
        targets.append((tx, "description_zh", tx["description"], _keep_in(tx["description"], [p.get("name") for p in transactions_mod._tx_persons(tx)] or [tx.get("person_name")])))
    items = []  # 相同原文只送一次（keep 取聯集）
    index_of = {}
    for _, _, text, keep in targets:
        if text in index_of:
            merged = items[index_of[text]]["keep"]
            merged.extend(k for k in keep if k not in merged)
        else:
            index_of[text] = len(items)
            items.append({"text": text, "keep": list(keep)})
    results = translate_texts(items, extra_team_names)
    if results is None:
        if not os.environ.get("GEMINI_API_KEY"):
            print("Translation: 未啟用（無金鑰）")
        elif not items:
            print("Translation: 無需翻譯的內容")
        else:
            print("Translation: 失敗，使用原文")
        return False
    done = sum(1 for it, tr in zip(items, results) if tr != it["text"])
    for obj, dst, text, _ in targets:
        tr = results[index_of[text]]
        if tr != text:
            obj[dst] = tr
            if dst == "description_zh":
                TX_TRANSLATION_CACHE[text] = tr  # 首頁本週人事異動可沿用同一則的戰報譯文
    model = os.environ.get("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    if LAST_TRANSLATION["fallback_ok"]:
        print(f"Translation: 成功 {done}/{len(items)} 則（模型 {LAST_TRANSLATION['fallback']}，主模型 {model} 失敗後改用）")
    else:
        print(f"Translation: 成功 {done}/{len(items)} 則（模型 {model}）")
    return True


def translate_weekly_transactions(weekly):
    """首頁「本週人事異動精選」翻譯。與當日戰報相同的 description 直接沿用戰報譯文；
    同一次執行中戰報翻譯已因可重試錯誤（429/5xx/逾時）耗盡重試而失敗時，不再呼叫 Gemini（避免重複等待）。"""
    for tx in weekly:
        zh = TX_TRANSLATION_CACHE.get(tx["description"])
        if zh:
            tx["description_zh"] = zh
    pending = [tx for tx in weekly if "description_zh" not in tx]
    if not pending:
        print("Translation: 本週人事異動沿用戰報譯文")
        return
    if net.LAST_POST["retryable"]:
        print("Translation: 戰報翻譯已因暫時性錯誤失敗，本週人事異動直接使用原文（不再重試）")
        return
    apply_translations(None, [], pending)
