"""人事異動的抓取、篩選與渲染（含每週精選）。"""

import re
from datetime import timedelta

from . import htmlutil, net, timeutil
from .config import (
    DATE_KEY_RE,
    TEAM_META,
    TRANSACTIONS_RANGE_URL,
    WEEKLY_TX_DAYS,
    WEEKLY_TX_EXTENSION_RE,
    WEEKLY_TX_LIMIT,
    WEEKLY_TX_TYPE_CODES,
)


def _is_mlb_team(team_obj):
    """toTeam／fromTeam 物件的 id 是否為 30 支大聯盟球隊之一。"""
    if not isinstance(team_obj, dict):
        return False
    return htmlutil._int_id(team_obj.get("id")) in TEAM_META


def filter_major_league_transactions(transactions):
    """對 transactions API 的 transactions 陣列套用大聯盟層級過濾，回傳整理後的 list（保持 API 順序）。description 為
    API 原文（未 escape），輸出 HTML 前一律經 render_tx_description() 處理。

    API 對一筆交易的每位球員各回一筆紀錄（description 相同，toTeam／fromTeam 方向各異；多隊交易尤其如此），
    故先依 description 分組（保持首次出現順序），再對整組套用過濾：排除 minor league 關鍵字；signed 類需有大聯盟
    特徵詞；整組任一紀錄的 toTeam／fromTeam 為 30 隊之一（排除冬季聯盟指派、小聯盟球隊之間的異動）。

    每筆輸出：to_team_id／from_team_id／person_id／person_name 取第一筆涉及 30 隊的紀錄（該筆無 person 時改採第一筆有
    person 的紀錄）；persons 為全部球員；legs 為各紀錄的 {to_team_id, from_team_id, person_id}（去重）；team_ids 為 legs
    依序出現過的球隊 id（去重、不限 30 隊）。"""
    groups = {}  # description -> 該描述的所有紀錄（dict 保持首次出現順序）
    for tx in transactions or []:
        desc = tx.get("description")
        if desc:
            groups.setdefault(desc, []).append(tx)

    tx_list = []
    for desc, records in groups.items():
        desc_lower = desc.lower()
        if re.search(r"\bminor league", desc_lower):
            continue
        if re.search(r"\bsigned\b", desc_lower):  # 單字比對，避免 assigned 命中
            indicators = ["free agent", "major league", "extension", "arbitration", "-year"]
            if not any(ind in desc_lower for ind in indicators):
                continue
        mlb_records = [r for r in records if _is_mlb_team(r.get("toTeam")) or _is_mlb_team(r.get("fromTeam"))]
        if not mlb_records:
            continue
        lead = mlb_records[0]  # 第一筆涉及 30 隊的紀錄（與逐筆過濾時的行為一致）
        if (lead.get("person") or {}).get("id") is None:
            # 先出現的是無 person 的反向紀錄時，改採第一筆有 person 的紀錄的球員與 to/from 方向
            lead = next((r for r in records if (r.get("person") or {}).get("id") is not None), lead)
        person_info = lead.get("person") or {}
        entry = {
            "to_team_id": (lead.get("toTeam") or {}).get("id"),
            "from_team_id": (lead.get("fromTeam") or {}).get("id"),
            "person_id": person_info.get("id"),
            "person_name": person_info.get("fullName"),
            "persons": [],
            "legs": [],
            "team_ids": [],
            "description": desc,
            "date": records[0].get("date"),
            "type_code": records[0].get("typeCode"),
        }
        seen_legs = set()
        for r in records:
            _add_person(entry, r.get("person"))
            leg = {"to_team_id": (r.get("toTeam") or {}).get("id"),
                   "from_team_id": (r.get("fromTeam") or {}).get("id"),
                   "person_id": (r.get("person") or {}).get("id")}
            key = (leg["to_team_id"], leg["from_team_id"], leg["person_id"])
            if key in seen_legs:
                continue
            seen_legs.add(key)
            entry["legs"].append(leg)
            for tid in (leg["to_team_id"], leg["from_team_id"]):
                if tid is not None and tid not in entry["team_ids"]:
                    entry["team_ids"].append(tid)
        tx_list.append(entry)

    return tx_list


def _add_person(entry, person):
    pid, name = (person or {}).get("id"), (person or {}).get("fullName")
    if pid is None or not name:
        return
    if all(p["id"] != pid for p in entry["persons"]):
        entry["persons"].append({"id": pid, "name": name})


def tx_team_ids(tx):
    """tx 涉及的球隊 id 清單；有 team_ids 就用；舊快照（無 team_ids）退回 [to_team_id, from_team_id] 中非 None 且去重者。"""
    ids = tx.get("team_ids")
    if ids:
        return list(ids)
    out = []
    for tid in (tx.get("to_team_id"), tx.get("from_team_id")):
        if tid is not None and tid not in out:
            out.append(tid)
    return out


def fetch_raw_transactions(date_str):
    """抓取當日人事異動原始資料（API 的 transactions 陣列，不過濾）；抓取失敗或無資料回傳 []。"""
    url = f"https://statsapi.mlb.com/api/v1/transactions?date={date_str}&sportId=1"
    data = net.fetch_json(url)
    if not data or not data.get("transactions"):
        return []
    return data["transactions"]


def fetch_daily_transactions(date_str):
    """抓取當日大聯盟層級人事異動（過濾規則見 filter_major_league_transactions）。"""
    return filter_major_league_transactions(fetch_raw_transactions(date_str))


def _tx_persons(tx):
    """tx 的球員清單 [{"id", "name"}]；舊資料（無 persons）退回 person_id/person_name 單元素。"""
    persons = tx.get("persons")
    if persons:
        return persons
    if tx.get("person_name") and htmlutil._int_id(tx.get("person_id")) is not None:
        return [{"id": tx["person_id"], "name": tx["person_name"]}]
    return []


def render_tx_description(description, person_id=None, person_name=None, persons=None):
    """對原文以球員名切分，各段分別 escape，再以 Savant 連結串接（避免名字命中 &amp; 等實體）。
    persons 為 [{"id","name"}]；未給時以 person_id/person_name 組成單元素。"""
    if persons is None:
        persons = [{"id": person_id, "name": person_name}] if person_name else []
    by_name = {}
    for p in persons:
        name = p.get("name")
        if name and htmlutil._int_id(p.get("id")) is not None and name in description:
            by_name.setdefault(name, p["id"])
    if not by_name:
        return htmlutil.esc(description)
    names = sorted(by_name, key=len, reverse=True)
    pattern = "(" + "|".join(re.escape(n) for n in names) + ")"
    out = []
    for i, part in enumerate(re.split(pattern, description)):
        if i % 2 == 1:
            out.append(htmlutil.player_link(by_name[part], part))
        elif part:
            out.append(htmlutil.esc(part))
    return "".join(out)


def _tx_display_text(tx):
    """優先用譯文；譯文遺失任一人名（理論上已被驗證擋下）時退回原文。"""
    zh = tx.get("description_zh")
    names = [p.get("name") for p in _tx_persons(tx)] + [tx.get("person_name")]
    if zh and all(n in zh for n in names if n and n in tx["description"]):
        return zh
    return tx["description"]


def render_tx_items(tx_list):
    return "".join(
        f"<li>{render_tx_description(_tx_display_text(tx), persons=_tx_persons(tx))}</li>"
        for tx in tx_list
    )


def render_transactions_section(tx_list, heading="其他球隊人事異動", empty_text="本日無其他球隊人事異動。"):
    """依球隊分組顯示人事異動：每筆放進 tx_team_ids(tx) 中每一支 30 隊的分組（多隊交易每隊都列出，同組只出現一次），
    都不是 30 隊者歸入「其他異動」。標題筆數為異動數，不是分組內項目總數。"""
    grouped = {}
    for tx in tx_list:
        keys = [t for t in dict.fromkeys(tx_team_ids(tx)) if t in TEAM_META] or ["Other"]
        for key in keys:
            grouped.setdefault(key, []).append(tx)

    head = (
        f'<div class="section-head"><h2 id="tx-h">{htmlutil.esc(heading)}</h2>'
        f'<span class="count">{len(tx_list)} 筆</span></div>'
    )
    if not tx_list:
        body = f'<p class="section-empty">{htmlutil.esc(empty_text)}</p>'
    else:
        keys = sorted(k for k in grouped if k != "Other")
        if "Other" in grouped:
            keys.append("Other")
        groups = []
        for key in keys:
            items = render_tx_items(grouped[key])
            if key == "Other":
                groups.append(
                    f'<div class="tx-group"><h3>其他異動</h3><ul class="tx-list">{items}</ul></div>'
                )
            else:
                meta = TEAM_META[key]
                groups.append(
                    f'<div class="tx-group{htmlutil.stripe_class(meta["color"])}" style="--team:{htmlutil.safe_color(meta["color"])}">'
                    f'<h3>{htmlutil.logo_picture(key, meta["abbr"], 22)}{htmlutil.esc(meta["name"])}</h3>'
                    f'<ul class="tx-list">{items}</ul></div>'
                )
        body = '<div class="tx-grid">' + "".join(groups) + "</div>"
    return f'<section class="section wrap" aria-labelledby="tx-h">{head}{body}</section>'


def split_game_transactions(tx_list, away, home):
    """把與本場兩隊相關的異動分出來。away/home 為 {'id','abbr','name','color'}。
    回傳 (本場球隊分組, 其餘異動)。"""
    groups = []
    matched = set()
    for side in (away, home):
        items = [tx for tx in tx_list if side["id"] in tx_team_ids(tx)]
        if items:
            groups.append({"team": side, "items": items})
            matched.update(tx["description"] for tx in items)
    remaining = [tx for tx in tx_list if tx["description"] not in matched]
    return groups, remaining


def render_card_transactions(groups):
    if not groups:
        return ""
    parts = []
    for g in groups:
        team = g["team"]
        parts.append(
            f'<div class="tx-group{htmlutil.stripe_class(team["color"])}" style="--team:{htmlutil.safe_color(team["color"])}">'
            f'<h5>{htmlutil.logo_picture(team["id"], team["abbr"], 18)}{htmlutil.esc(team["name"])}</h5>'
            f'<ul class="tx-list">{render_tx_items(g["items"])}</ul></div>'
        )
    return '<section><h4 class="sub">本場相關人事異動</h4>' + "".join(parts) + "</section>"


def select_weekly_highlights(tx_list, limit=WEEKLY_TX_LIMIT):
    """已過大聯盟過濾的異動中，保留交易（TR）、自由球員簽約（SFA）與描述含延長合約字樣者；
    日期新到舊（同日維持 API 順序），最多 limit 筆。"""
    picked = [
        tx for tx in tx_list
        if tx.get("type_code") in WEEKLY_TX_TYPE_CODES or WEEKLY_TX_EXTENSION_RE.search(tx["description"].lower())
    ]
    picked.sort(key=lambda tx: str(tx.get("date") or ""), reverse=True)  # sort 穩定：同日保留原順序
    return picked[:limit]


def fetch_weekly_transactions(today):
    """近 7 天（today-7 至 today-1，台北日期）大聯盟層級異動精選；抓取失敗回傳 None。"""
    start = (today - timedelta(days=WEEKLY_TX_DAYS)).isoformat()
    end = (today - timedelta(days=1)).isoformat()
    data = net.fetch_json(TRANSACTIONS_RANGE_URL.format(start=start, end=end))
    if not isinstance(data, dict):
        return None
    transactions = data.get("transactions")
    if not isinstance(transactions, list):  # 缺欄位視為抓取失敗；真的沒有異動時 API 回傳空陣列
        return None
    return select_weekly_highlights(filter_major_league_transactions(transactions))


def render_weekly_transactions(tx_list):
    if tx_list is None:
        return htmlutil._section_message("wk-h", "本週人事異動精選", "人事異動暫時無法取得", True)
    if not tx_list:
        return htmlutil._section_message("wk-h", "本週人事異動精選", "本週無重大人事異動")
    items = []
    for tx in tx_list:
        team_id = next((t for t in (htmlutil._int_id(tx.get("to_team_id")), htmlutil._int_id(tx.get("from_team_id")))
                        if t in TEAM_META), None)
        style = cls = team_html = ""
        if team_id is not None:
            meta = TEAM_META[team_id]
            cls = htmlutil.stripe_class(meta["color"])
            style = f' style="--team:{htmlutil.safe_color(meta["color"])}"'
            team_html = f'{htmlutil.logo_picture(team_id, meta["abbr"], 18, alt="")}<span class="wk-team">{htmlutil.esc(meta["name"])}</span>'
        date_str = str(tx.get("date") or "")
        d = timeutil._parse_iso_date(date_str) if DATE_KEY_RE.match(date_str) else None
        date_html = f'<time class="num" datetime="{d.isoformat()}">{d.month}/{d.day}</time>' if d else ""
        text = render_tx_description(_tx_display_text(tx), persons=_tx_persons(tx))
        items.append(
            f'      <li class="wk-tx{cls}"{style}><span class="wk-meta">{date_html}{team_html}</span>'
            f'<span class="wk-txt">{text}</span></li>'
        )
    count = f'<span class="count">近 {WEEKLY_TX_DAYS} 天 · {len(tx_list)} 筆</span>'
    return ('  <section class="section" aria-labelledby="wk-h">\n'
            f'    <div class="section-head"><h2 id="wk-h">本週人事異動精選</h2>{count}</div>\n'
            '    <ul class="wk-list">\n' + "\n".join(items) + '\n    </ul>\n  </section>')
