#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# Reconfigure standard I/O encoding to UTF-8 to avoid encoding errors on Windows (CP950)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# MLB Team Metadata: Chinese Name, Abbreviation, Official Color Hex, League (AL/NL), Division (E/C/W)
TEAM_META = {
    108: {"name": "洛杉磯天使", "abbr": "LAA", "color": "#BA0021", "league": "AL", "division": "W"},
    109: {"name": "亞利桑那響尾蛇", "abbr": "AZ", "color": "#A71930", "league": "NL", "division": "W"},
    110: {"name": "巴爾的摩金鶯", "abbr": "BAL", "color": "#DF4601", "league": "AL", "division": "E"},
    111: {"name": "波士頓紅襪", "abbr": "BOS", "color": "#BD3039", "league": "AL", "division": "E"},
    112: {"name": "芝加哥小熊", "abbr": "CHC", "color": "#0E3386", "league": "NL", "division": "C"},
    113: {"name": "辛辛那提紅人", "abbr": "CIN", "color": "#C6011F", "league": "NL", "division": "C"},
    114: {"name": "克里夫蘭守護者", "abbr": "CLE", "color": "#0C2340", "league": "AL", "division": "C"},
    115: {"name": "科羅拉多洛磯", "abbr": "COL", "color": "#333366", "league": "NL", "division": "W"},
    116: {"name": "底特律老虎", "abbr": "DET", "color": "#0C2340", "league": "AL", "division": "C"},
    117: {"name": "休士頓太空人", "abbr": "HOU", "color": "#EB6E1F", "league": "AL", "division": "W"},
    118: {"name": "堪薩斯皇家", "abbr": "KC", "color": "#004687", "league": "AL", "division": "C"},
    119: {"name": "洛杉磯道奇", "abbr": "LAD", "color": "#005A9C", "league": "NL", "division": "W"},
    120: {"name": "華盛頓國民", "abbr": "WSH", "color": "#AB0003", "league": "NL", "division": "E"},
    121: {"name": "紐約大都會", "abbr": "NYM", "color": "#FF5910", "league": "NL", "division": "E"},
    133: {"name": "運動家", "abbr": "ATH", "color": "#003831", "league": "AL", "division": "W"},
    134: {"name": "匹茲堡海盜", "abbr": "PIT", "color": "#FDB827", "league": "NL", "division": "C"},
    135: {"name": "聖地牙哥教士", "abbr": "SD", "color": "#2F241D", "league": "NL", "division": "W"},
    136: {"name": "西雅圖水手", "abbr": "SEA", "color": "#0C2C56", "league": "AL", "division": "W"},
    137: {"name": "舊金山巨人", "abbr": "SF", "color": "#FD5A1E", "league": "NL", "division": "W"},
    138: {"name": "聖路易紅雀", "abbr": "STL", "color": "#C41E3A", "league": "NL", "division": "C"},
    139: {"name": "坦帕灣光芒", "abbr": "TB", "color": "#092C5C", "league": "AL", "division": "E"},
    140: {"name": "德州遊騎兵", "abbr": "TEX", "color": "#003278", "league": "AL", "division": "W"},
    141: {"name": "多倫多藍鳥", "abbr": "TOR", "color": "#134A8E", "league": "AL", "division": "E"},
    142: {"name": "明尼蘇達雙城", "abbr": "MIN", "color": "#002B5C", "league": "AL", "division": "C"},
    143: {"name": "費城費城人", "abbr": "PHI", "color": "#E81828", "league": "NL", "division": "E"},
    144: {"name": "亞特蘭大勇士", "abbr": "ATL", "color": "#CE1141", "league": "NL", "division": "E"},
    145: {"name": "芝加哥白襪", "abbr": "CWS", "color": "#27251F", "league": "AL", "division": "C"},
    146: {"name": "邁阿密馬林魚", "abbr": "MIA", "color": "#00A3E0", "league": "NL", "division": "E"},
    147: {"name": "紐約洋基", "abbr": "NYY", "color": "#0C2340", "league": "AL", "division": "E"},
    158: {"name": "密爾瓦基釀酒人", "abbr": "MIL", "color": "#122853", "league": "NL", "division": "C"}
}

# 舊縮寫別名（搜尋時舊縮寫仍可命中；縮寫已統一為 MLB 官方 API）
LEGACY_ABBR_ALIASES = {"AZ": "ARI", "KC": "KCR", "ATH": "OAK"}

POSTPONED_REASONS = {
    "Rain": "因雨延期",
    "Wet Grounds": "場地濕滑延期",
    "Cold": "低溫延期",
    "Inclement Weather": "惡劣天氣延期",
    "Snow": "因雪延期",
    "Postponed": "賽事延期"
}

# 取消（Cancelled）原因對照；不沿用延期文案以免語句不通順
CANCELLED_REASONS = {
    "Rain": "因雨取消",
    "Wet Grounds": "因場地濕滑取消",
    "Cold": "因低溫取消",
    "Inclement Weather": "因惡劣天氣取消",
    "Snow": "因雪取消",
}

# 等待迴圈：每次檢查未完成時睡 30 分鐘，最多檢查 MAX_WAIT_CHECKS 次
MAX_WAIT_CHECKS = 8
WAIT_SLEEP_SECONDS = 1800

GAME_TYPE_LABELS = {
    "R": "例行賽",
    "F": "外卡賽",
    "D": "分區系列賽",
    "L": "聯盟冠軍賽",
    "W": "世界大賽",
    "S": "春訓",
    "E": "熱身賽",
    "A": "明星賽",
}

# 渲染用常數
TEMPLATE_DIR = "templates"
SAVANT_URL = "https://baseballsavant.mlb.com/savant-player/{}"
LOGO_URL = "https://www.mlbstatic.com/team-logos/team-cap-on-{}/{}.svg"
WEEKDAYS_ZH = "一二三四五六日"
DARK_LUMINANCE_THRESHOLD = 0.08  # 隊色相對亮度低於此值，色條加淺色外框
SLOT_RE = re.compile(r"<!--SLOT:([A-Z_]+)-->")

# 每日新聞（MLB 官方 RSS）
NEWS_RSS_URL = "https://www.mlb.com/feeds/news/rss.xml"
NEWS_SOURCE = "MLB.com"
TAIPEI_TZ = timezone(timedelta(hours=8))

# Gemini 翻譯（Interactions API，只用標準函式庫；金鑰只從環境變數讀取、只放 header）
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_REVISION = "2026-05-20"
TRANSLATE_BATCH_SIZE = 60

# 球隊英文全名／暱稱／中文簡稱（供翻譯對照表；中文全名取自 TEAM_META）
TEAM_NAMES_EN = {
    108: ("Los Angeles Angels", "Angels", "天使"),
    109: ("Arizona Diamondbacks", "Diamondbacks", "響尾蛇"),
    110: ("Baltimore Orioles", "Orioles", "金鶯"),
    111: ("Boston Red Sox", "Red Sox", "紅襪"),
    112: ("Chicago Cubs", "Cubs", "小熊"),
    113: ("Cincinnati Reds", "Reds", "紅人"),
    114: ("Cleveland Guardians", "Guardians", "守護者"),
    115: ("Colorado Rockies", "Rockies", "洛磯"),
    116: ("Detroit Tigers", "Tigers", "老虎"),
    117: ("Houston Astros", "Astros", "太空人"),
    118: ("Kansas City Royals", "Royals", "皇家"),
    119: ("Los Angeles Dodgers", "Dodgers", "道奇"),
    120: ("Washington Nationals", "Nationals", "國民"),
    121: ("New York Mets", "Mets", "大都會"),
    133: ("Athletics", "Athletics", "運動家"),
    134: ("Pittsburgh Pirates", "Pirates", "海盜"),
    135: ("San Diego Padres", "Padres", "教士"),
    136: ("Seattle Mariners", "Mariners", "水手"),
    137: ("San Francisco Giants", "Giants", "巨人"),
    138: ("St. Louis Cardinals", "Cardinals", "紅雀"),
    139: ("Tampa Bay Rays", "Rays", "光芒"),
    140: ("Texas Rangers", "Rangers", "遊騎兵"),
    141: ("Toronto Blue Jays", "Blue Jays", "藍鳥"),
    142: ("Minnesota Twins", "Twins", "雙城"),
    143: ("Philadelphia Phillies", "Phillies", "費城人"),
    144: ("Atlanta Braves", "Braves", "勇士"),
    145: ("Chicago White Sox", "White Sox", "白襪"),
    146: ("Miami Marlins", "Marlins", "馬林魚"),
    147: ("New York Yankees", "Yankees", "洋基"),
    158: ("Milwaukee Brewers", "Brewers", "釀酒人"),
}

def get_taipei_now():
    tz_taipei = timezone(timedelta(hours=8))
    return datetime.now(timezone.utc).astimezone(tz_taipei)

def get_yesterday_str():
    now = get_taipei_now()
    yesterday = now - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")

def fetch_json(url, retries=3, delay=5):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"Error fetching {url} (attempt {attempt + 1}/{retries}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def fetch_text(url, retries=3, delay=5):
    """與 fetch_json 相同的重試與 User-Agent，回傳回應文字（str）；全部失敗回傳 None。"""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.read().decode('utf-8')
        except Exception as e:
            print(f"Error fetching {url} (attempt {attempt + 1}/{retries}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                time.sleep(delay)
    return None


class ApiError(Exception):
    """API 抓取失敗，或回應缺少預期結構（與「確實無賽事」不同）。"""


def check_all_games_finalized(date_str):
    """回傳 (finalized, games)。

    - API 失敗（fetch 回 None、回應結構不符）：拋出 ApiError。
    - 回應成功且確實無賽事（totalGames == 0 或 dates 為空）：回傳 (True, [])。
    - 有賽事：回傳 (是否全數完賽, games)。
    """
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=linescore,decisions"
    data = fetch_json(url)
    if data is None:
        raise ApiError(f"無法取得 {date_str} 的賽程（API 無回應）")
    if not isinstance(data, dict) or not isinstance(data.get("dates"), list):
        raise ApiError(f"{date_str} 的賽程回應缺少預期結構（dates）")

    dates = data["dates"]
    if not dates:
        return True, []
    if not isinstance(dates[0], dict):
        raise ApiError(f"{date_str} 的賽程回應結構異常（dates[0]）")
    games = dates[0].get("games")
    if games is None:
        if data.get("totalGames") == 0:
            return True, []
        raise ApiError(f"{date_str} 的賽程回應缺少預期結構（games）")
    if not isinstance(games, list):
        raise ApiError(f"{date_str} 的賽程回應結構異常（games）")
    if not games:
        return True, []

    for game in games:
        state = game.get("status", {}).get("abstractGameState")
        detailed = game.get("status", {}).get("detailedState")
        # Games are NOT finalized if they are Preview or Live (excluding Postponed/Cancelled,
        # and Suspended games which will not resume on the same day)
        if state in ["Preview", "Live"] and detailed not in ["Postponed", "Cancelled"] and not (detailed or "").startswith("Suspended"):
            return False, games

    return True, games

def fetch_game_news(game_pk, away_abbr, home_abbr, away_r, home_r):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/content"
    backup_headline = f"{away_abbr} @ {home_abbr} 賽事總結"
    backup_blurb = f"終場比分 {away_abbr} {away_r} - {home_abbr} {home_r}。本場比賽目前無官方文字摘要。"
    
    data = fetch_json(url)
    if not data:
        return {"headline": backup_headline, "blurb": backup_blurb}
        
    try:
        recap = data.get("editorial", {}).get("recap", {}).get("mlb", {})
        headline = recap.get("headline")
        blurb = recap.get("blurb")
        if headline and blurb:
            return {"headline": headline, "blurb": blurb, "translatable": True}
    except Exception as e:
        print(f"Warning: Error parsing news for game {game_pk} ({e})", file=sys.stderr)
        
    return {"headline": backup_headline, "blurb": backup_blurb}


def _is_mlb_team(team_obj):
    """toTeam／fromTeam 物件的 id 是否為 30 支大聯盟球隊之一。"""
    if not isinstance(team_obj, dict):
        return False
    return _int_id(team_obj.get("id")) in TEAM_META


def fetch_daily_transactions(date_str):
    """抓取當日大聯盟層級人事異動（已套用過濾規則）。description 為 API 原文（未 escape），
    輸出 HTML 前一律經 render_tx_description() 處理。

    過濾：排除 minor league 關鍵字；signed 類需有大聯盟特徵詞；toTeam／fromTeam 至少一方為 30 隊之一
    （排除冬季聯盟指派、小聯盟球隊之間的異動）；描述去重。"""
    url = f"https://statsapi.mlb.com/api/v1/transactions?date={date_str}&sportId=1"
    data = fetch_json(url)
    if not data or not data.get("transactions"):
        return []

    tx_list = []
    seen_descriptions = set()

    for tx in data["transactions"]:
        desc = tx.get("description")
        if not desc or desc in seen_descriptions:
            continue
        desc_lower = desc.lower()
        if re.search(r"\bminor league", desc_lower):
            continue
        if re.search(r"\bsigned\b", desc_lower):  # 單字比對，避免 assigned 命中
            indicators = ["free agent", "major league", "extension", "arbitration", "-year"]
            if not any(ind in desc_lower for ind in indicators):
                continue
        if not (_is_mlb_team(tx.get("toTeam")) or _is_mlb_team(tx.get("fromTeam"))):
            continue
        seen_descriptions.add(desc)

        person_info = tx.get("person") or {}
        tx_list.append({
            "to_team_id": (tx.get("toTeam") or {}).get("id"),
            "from_team_id": (tx.get("fromTeam") or {}).get("id"),
            "person_id": person_info.get("id"),
            "person_name": person_info.get("fullName"),
            "description": desc,
        })

    return tx_list


def _safe_mlb_url(url):
    """只接受 https 且主機為 www.mlb.com 或 *.mlbstatic.com 的網址；否則回傳 None。"""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or any(ch.isspace() or ord(ch) < 32 for ch in url):
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        return None
    if host == "www.mlb.com" or host == "mlbstatic.com" or host.endswith(".mlbstatic.com"):
        return url
    return None


def fetch_daily_news(date_str, limit=12):
    """抓取 MLB 官方 RSS，回傳 date_str 當天（台北時間 00:00–24:00）發布的新聞，新到舊、最多 limit 則。

    每則為 {"title", "link", "published"（台北時間 datetime）, "image"（網址或 None）}。
    link 不安全的新聞整則捨棄；image 不安全則設為 None。
    抓取或解析失敗回傳 None（與「成功但 0 則」的 [] 區分）。"""
    text = fetch_text(NEWS_RSS_URL)
    if not text:
        return None
    try:
        day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TAIPEI_TZ)
        day_end = day_start + timedelta(days=1)
        root = ET.fromstring(text)
        if root.tag != "rss":
            raise ValueError(f"unexpected root element: {root.tag}")
        found = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = _safe_mlb_url(item.findtext("link"))
            pub = (item.findtext("pubDate") or "").strip()
            if not title or not link or not pub:
                continue
            try:
                published = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            published = published.astimezone(TAIPEI_TZ)
            if not (day_start <= published < day_end):
                continue
            image_el = item.find("image")
            image = _safe_mlb_url(image_el.get("href")) if image_el is not None else None
            found.append({"title": title, "link": link, "published": published, "image": image})
    except Exception as e:
        print(f"Warning: Could not parse news RSS ({e}). Skipping news section.", file=sys.stderr)
        return None
    found.sort(key=lambda n: n["published"], reverse=True)
    return found[:limit]


# ---------------------------------------------------------------- Gemini 翻譯
def _redact(text, secrets):
    for sec in secrets:
        text = text.replace(sec, "***")
    return text


def _http_error_detail(err, secrets):
    """讀取 HTTPError body，若為 Google 錯誤 JSON 回傳 ' status=... message=...'（遮蔽金鑰、message 前 200 字），否則空字串。"""
    try:
        body = json.loads(err.read().decode("utf-8", errors="replace"))
        info = body.get("error") if isinstance(body, dict) else None
        if not isinstance(info, dict):
            return ""
        status = _redact(str(info.get("status", "")), secrets)[:100]
        message = _redact(str(info.get("message", "")), secrets)[:200]
        return f" status={status} message={message}"
    except Exception:
        return ""


GEMINI_RETRY_DELAYS = (5, 15, 45)  # 首次失敗後各次重試前的等待秒數（共最多 4 次嘗試）
GEMINI_RETRY_AFTER_CAP = 120
GEMINI_RETRYABLE_HTTP = (429, 500, 502, 503, 504)
GEMINI_FALLBACK_MODEL = "gemini-3.6-flash"

# 最後一次 post_json 的結果狀態：retryable=True 表示失敗屬於可重試類型（429/5xx/逾時/連線錯誤）且重試已耗盡；
# 不可重試（其他 4xx、解析失敗）或成功時為 False。呼叫端據此決定是否改用備用模型。
LAST_POST = {"retryable": False}


def _retry_after_seconds(err):
    try:
        value = err.headers.get("Retry-After") if err.headers else None
        return float(value) if value is not None and str(value).strip().replace(".", "", 1).isdigit() else None
    except Exception:
        return None


def post_json(url, payload, headers, timeout=30):
    """以標準函式庫 POST JSON，回傳解析後的 dict；失敗回傳 None。
    可重試錯誤（HTTP 429/500/502/503/504、逾時、連線錯誤）最多共嘗試 4 次，間隔 GEMINI_RETRY_DELAYS，
    並尊重數字型 Retry-After（取較大者，上限 120 秒）；其他錯誤不重試。
    結束時將 LAST_POST["retryable"] 設為「是否因可重試錯誤耗盡而失敗」。
    錯誤訊息只印 HTTP 狀態碼、例外類型，以及 Google 錯誤 JSON 的 error.status／error.message（前 200 字，
    且先把金鑰字串遮成 ***）；不印網址與 header。"""
    secrets = [v for k, v in (headers or {}).items() if "key" in k.lower() and isinstance(v, str) and v]
    data = json.dumps(payload).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    total = len(GEMINI_RETRY_DELAYS) + 1
    LAST_POST["retryable"] = False
    for attempt in range(total):
        retry_after = None
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            LAST_POST["retryable"] = False
            return result if isinstance(result, dict) else None
        except urllib.error.HTTPError as e:
            detail = _http_error_detail(e, secrets)
            print(f"Warning: translation request failed (attempt {attempt + 1}/{total}): HTTP {e.code}{detail}", file=sys.stderr)
            if e.code not in GEMINI_RETRYABLE_HTTP:
                LAST_POST["retryable"] = False
                return None
            retry_after = _retry_after_seconds(e)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # socket.timeout 為 TimeoutError 別名
            print(f"Warning: translation request failed (attempt {attempt + 1}/{total}): {type(e).__name__}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: translation request failed (attempt {attempt + 1}/{total}): {type(e).__name__}", file=sys.stderr)
            LAST_POST["retryable"] = False
            return None
        LAST_POST["retryable"] = True
        if attempt < total - 1:
            delay = GEMINI_RETRY_DELAYS[attempt]
            if retry_after is not None:
                delay = min(max(delay, retry_after), GEMINI_RETRY_AFTER_CAP)
            time.sleep(delay)
    return None


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
        lines.append(f"{nick_en} -> {short_zh}")
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
    LAST_POST["retryable"] = False
    resp = post_json(GEMINI_ENDPOINT, payload, headers)
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
        if out is None and fallback and LAST_POST["retryable"]:
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


def apply_translations(news, views, tx_items, extra_team_names=()):
    """收集整頁需翻譯的文字，只呼叫一次 translate_texts，譯文寫入 *_zh 欄位。回傳是否成功套用。"""
    targets = []  # (物件, 目標鍵, 原文, keep)
    for n in news or []:
        targets.append((n, "title_zh", n["title"], []))
    for view in views:
        box = view.get("news")
        if not box or not box.get("translatable"):
            continue
        names = [p["name"] for p in view.get("batters", []) + view.get("pitchers", [])]
        for src, dst in (("headline", "headline_zh"), ("blurb", "blurb_zh")):
            targets.append((box, dst, box[src], _keep_in(box[src], names)))
    for tx in tx_items:
        targets.append((tx, "description_zh", tx["description"], _keep_in(tx["description"], [tx.get("person_name")])))
    items = [{"text": text, "keep": keep} for _, _, text, keep in targets]
    results = translate_texts(items, extra_team_names)
    if results is None:
        if not os.environ.get("GEMINI_API_KEY"):
            print("Translation: 未啟用（無金鑰）")
        elif not items:
            print("Translation: 無需翻譯的內容")
        else:
            print("Translation: 失敗，使用原文")
        return False
    done = 0
    for (obj, dst, text, _), tr in zip(targets, results):
        if tr != text:
            obj[dst] = tr
            done += 1
    model = os.environ.get("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL
    if LAST_TRANSLATION["fallback_ok"]:
        print(f"Translation: 成功 {done}/{len(items)} 則（模型 {LAST_TRANSLATION['fallback']}，主模型 {model} 失敗後改用）")
    else:
        print(f"Translation: 成功 {done}/{len(items)} 則（模型 {model}）")
    return True


# ---------------------------------------------------------------- 渲染：共用工具
def esc(s):
    """所有來自 API 的字串輸出到 HTML（含屬性值）前一律經過此函式。"""
    return html_lib.escape(str(s), quote=True)


def fmt_date_long(date_str):
    """'2026-07-30' -> '2026 年 07 月 30 日（四）'；格式不符時原樣回傳。"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(date_str)
    return f"{dt.year} 年 {dt.month:02d} 月 {dt.day:02d} 日（{WEEKDAYS_ZH[dt.weekday()]}）"


def report_href(date_str):
    """自網站根目錄起算的戰報路徑：reports/YYYY/MM/YYYY-MM-DD.html。"""
    y, m, _ = date_str.split("-")
    return f"reports/{y}/{m}/{date_str}.html"


def safe_color(color):
    return color if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(color)) else "#6b7280"


def relative_luminance(color):
    """WCAG 相對亮度（0 = 黑、1 = 白）。"""
    color = safe_color(color)
    channels = []
    for i in (1, 3, 5):
        c = int(color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def is_dark_color(color):
    """隊色相對深色背景過暗（色條會消失）時為 True。"""
    return relative_luminance(color) < DARK_LUMINANCE_THRESHOLD


def stripe_class(color):
    """隊色過暗時回傳需追加的 class（含前導空白），否則空字串。"""
    return " team-stripe--dark" if is_dark_color(color) else ""


def _int_id(value):
    """id 必須是整數（或純數字字串），否則回傳 None。"""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def logo_picture(team_id, abbr, size):
    """隊徽；id 不是整數時不輸出圖片（回傳空字串）。"""
    tid = _int_id(team_id)
    if tid is None:
        return ""
    return (
        f'<picture><source media="(prefers-color-scheme: light)" srcset="{LOGO_URL.format("light", tid)}">'
        f'<img src="{LOGO_URL.format("dark", tid)}" width="{size}" height="{size}" alt="{esc(abbr)}" loading="lazy"></picture>'
    )


def player_link(player_id, name):
    """球員 Savant 連結；id 不是整數時退回只輸出 escape 後的名字。"""
    pid = _int_id(player_id)
    if pid is None:
        return esc(name)
    return (
        f'<a class="plink" href="{SAVANT_URL.format(pid)}" '
        f'target="_blank" rel="noopener">{esc(name)}</a>'
    )


def render_tx_description(description, person_id=None, person_name=None):
    """對原文以球員名切分，各段分別 escape，再以 Savant 連結串接（避免名字命中 &amp; 等實體）。"""
    if person_name and _int_id(person_id) is not None and person_name in description:
        link = player_link(person_id, person_name)
        return link.join(esc(part) for part in description.split(person_name))
    return esc(description)


def _tx_display_text(tx):
    """優先用譯文；譯文遺失人名（理論上已被驗證擋下）時退回原文。"""
    zh = tx.get("description_zh")
    name = tx.get("person_name")
    if zh and (not name or name not in tx["description"] or name in zh):
        return zh
    return tx["description"]


def render_tx_items(tx_list):
    return "".join(
        f"<li>{render_tx_description(_tx_display_text(tx), tx.get('person_id'), tx.get('person_name'))}</li>"
        for tx in tx_list
    )


def load_template(name):
    path = os.path.join(TEMPLATE_DIR, name)
    if not os.path.exists(path):
        print(f"Error: Template file {path} not found.", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def fill_slots(template, slots):
    """以 <!--SLOT:NAME--> 為插槽，一次性替換（插槽內容不會被再次替換）；未提供的插槽輸出空字串。"""
    return SLOT_RE.sub(lambda m: str(slots.get(m.group(1), "")), template)


def render_page(slots):
    """戰報與休賽季快報共用的頁面殼（templates/report.html）。slots 的值須為已處理好的 HTML。"""
    return fill_slots(load_template("report.html"), slots)


# ---------------------------------------------------------------- 渲染：總覽與區塊
def render_summary(page_type, total_games=0, postponed=0, news_count=None, transactions_count=0):
    """news_count 為 None（新聞功能尚未接上）時，休賽季總覽省略「新聞」一格。"""
    if page_type == "offseason":
        items = []
        if news_count is not None:
            items.append(f"<div><dt>新聞</dt><dd>{int(news_count)} 則</dd></div>")
        items.append(f"<div><dt>人事異動</dt><dd>{int(transactions_count)} 筆</dd></div>")
    else:
        alert = ' class="is-alert"' if postponed else ""
        items = [
            f"<div><dt>總場數</dt><dd>{int(total_games)}</dd></div>",
            f"<div{alert}><dt>延賽</dt><dd>{int(postponed)}</dd></div>",
        ]
    return f'<div class="wrap">\n  <dl class="overview">\n    {"".join(items)}\n  </dl>\n</div>'


def render_search():
    return """<div class="searchbar">
  <div class="wrap">
    <form id="searchForm" role="search" autocomplete="off">
      <label class="visually-hidden" for="q">搜尋隊名或縮寫</label>
      <input id="q" type="search" placeholder="隊名或縮寫（如 洋基、LAD）" enterkeyhint="search">
      <button class="btn" type="submit">搜尋</button>
    </form>
    <p id="searchHint" class="search-hint" role="status" aria-live="polite">輸入隊名或縮寫，例：洋基、LAD</p>
  </div>
</div>"""


def render_games_section(cards_html):
    return (
        '<section class="section wrap" aria-labelledby="games-h">\n'
        f'  <div class="section-head"><h2 id="games-h">賽事結果</h2><span class="count">{len(cards_html)} 場</span></div>\n'
        '  <div class="games">\n' + "\n".join(cards_html) + "\n  </div>\n</section>"
    )


def render_news_section(news, page_type):
    """每日新聞區塊。news 為 None（抓取失敗）時不輸出；[] 時顯示空狀態。
    休賽季版首則為頭條樣式（有圖片才顯示圖片）。所有文字與網址皆 escape。"""
    if news is None:
        return ""
    head = f'<div class="section-head"><h2 id="news-h">今日新聞</h2><span class="count">{len(news)} 則</span></div>'
    if not news:
        body = '<p class="section-empty">當日無新聞。</p>'
    else:
        rest = list(news)
        lead = ""
        if page_type == "offseason":
            top = rest.pop(0)
            image = ""
            if top.get("image"):
                image = f'<img class="lead-img" src="{esc(top["image"])}" alt="{esc(top.get("title_zh") or top["title"])}" loading="lazy">'
            lead = (
                f'<article class="lead-story">{image}'
                f'<p class="kicker">頭條 · {esc(top["published"].strftime("%m/%d %H:%M"))}</p>'
                f'<h3><a href="{esc(top["link"])}" target="_blank" rel="noopener">{esc(top.get("title_zh") or top["title"])}</a></h3>'
                f'<p class="src">{esc(NEWS_SOURCE)}</p></article>'
            )
        items = "".join(
            f'<li><time datetime="{esc(n["published"].strftime("%Y-%m-%dT%H:%M"))}">{esc(n["published"].strftime("%H:%M"))}</time>'
            f'<div><a class="title" href="{esc(n["link"])}" target="_blank" rel="noopener">{esc(n.get("title_zh") or n["title"])}</a>'
            f'<span class="src">{esc(NEWS_SOURCE)} · {esc(n["published"].strftime("%m/%d %H:%M"))}</span></div></li>'
            for n in rest
        )
        cls = "news is-two-col" if page_type == "offseason" else "news"
        body = lead + (f'<ol class="{cls}">{items}</ol>' if items else "")
    return f'<section class="section wrap" aria-labelledby="news-h">{head}{body}</section>'


def render_transactions_section(tx_list, heading="其他球隊人事異動", empty_text="本日無其他球隊人事異動。"):
    """依球隊分組顯示人事異動（to_team 優先，其次 from_team，皆非 30 隊者歸入「其他異動」）。"""
    grouped = {}
    for tx in tx_list:
        if tx["to_team_id"] in TEAM_META:
            key = tx["to_team_id"]
        elif tx["from_team_id"] in TEAM_META:
            key = tx["from_team_id"]
        else:
            key = "Other"
        grouped.setdefault(key, []).append(tx)

    head = (
        f'<div class="section-head"><h2 id="tx-h">{esc(heading)}</h2>'
        f'<span class="count">{len(tx_list)} 筆</span></div>'
    )
    if not tx_list:
        body = f'<p class="section-empty">{esc(empty_text)}</p>'
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
                    f'<div class="tx-group{stripe_class(meta["color"])}" style="--team:{safe_color(meta["color"])}">'
                    f'<h3>{logo_picture(key, meta["abbr"], 22)}{esc(meta["name"])}</h3>'
                    f'<ul class="tx-list">{items}</ul></div>'
                )
        body = '<div class="tx-grid">' + "".join(groups) + "</div>"
    return f'<section class="section wrap" aria-labelledby="tx-h">{head}{body}</section>'


# ---------------------------------------------------------------- 比賽狀態與分類
def game_type_label(game):
    """依 gameType 回傳卡片標題用的賽事類型文字。"""
    return GAME_TYPE_LABELS.get(game.get("gameType"), "比賽")


def classify_game_status(game, linescore=None):
    """判斷比賽狀態，回傳 (badge_text, badge_class, is_final)。

    badge_class 對應 CSS 徽章：final / extra（延長賽）/ early（提前結束）/ suspended。
    - 完賽：Final（9 局或與預定局數相同）、Final/N（延長賽，extra）、Final/N（提前結束，early）。
    - Suspended：暫停比賽；其餘仍為 Preview/Live（等待達上限）：未完賽。兩者 is_final 皆為 False。
    """
    if linescore is None:
        linescore = game.get("linescore", {})
    status = game.get("status", {})
    abstract = status.get("abstractGameState")
    detailed = status.get("detailedState") or ""

    if detailed.startswith("Suspended"):
        return "暫停比賽", "suspended", False
    if abstract != "Final" or detailed in ("Postponed", "Cancelled"):
        return "未完賽", "suspended", False

    scheduled = linescore.get("scheduledInnings") or 9
    played = len(linescore.get("innings", [])) or linescore.get("currentInning") or 0
    if played == 0 or played == scheduled:
        return "Final", "final", True
    if played > scheduled:
        return f"Final/{played}", "extra", True
    return f"Final/{played}（提前結束）", "early", True


# ---------------------------------------------------------------- 資料抽取（boxscore -> 焦點球員）
def format_cumulative(count, season_total):
    """單場次數與賽季累計：1(9) / 2(10)(11)。賽季累計缺漏或小於單場次數（資料異常）時只輸出次數。"""
    if count <= 0:
        return ""
    if not isinstance(season_total, int) or season_total < count:
        return str(count)
    if count == 1:
        return f"1({season_total})"
    return f"{count}" + "".join(f"({season_total - count + 1 + i})" for i in range(count))


def get_team_info(team_id, english_name):
    if team_id in TEAM_META:
        return TEAM_META[team_id]
    return {"name": english_name, "abbr": english_name[:3].upper(), "color": "#6b7280"}


def collect_candidates(boxscore, away_abbr, home_abbr):
    """從 boxscore 取出打者／投手候選人並評分（分數由高到低）。"""
    teams = boxscore.get("teams", {})
    starters = {
        side: (teams.get(side, {}).get("pitchers") or [None])[0] for side in ("away", "home")
    }
    candidates = []

    for side, team_abbr in (("away", away_abbr), ("home", home_abbr)):
        for _, p_info in (teams.get(side, {}).get("players") or {}).items():
            person = p_info.get("person", {})
            p_id = person.get("id")
            p_name = person.get("fullName", "")
            pos = p_info.get("position", {}).get("abbreviation", "")
            if p_id is None:
                continue

            # 打者：有打數，或雖無打數但有盜壘／得分（代跑者，L4）
            bat = p_info.get("stats", {}).get("batting", {})
            if bat:
                ab = bat.get("atBats", 0)
                r = bat.get("runs", 0)
                h = bat.get("hits", 0)
                d = bat.get("doubles", 0)
                t = bat.get("triples", 0)
                hr = bat.get("homeRuns", 0)
                rbi = bat.get("rbi", 0)
                bb = bat.get("baseOnBalls", 0)
                so = bat.get("strikeOuts", 0)
                sb = bat.get("stolenBases", 0)
                if ab > 0 or sb > 0 or r > 0:
                    # Score = H*1 + 2B*2 + 3B*3 + HR*4 + RBI*1.5 + R*1 + SB*1 + BB*1
                    score = h * 1.0 + d * 2.0 + t * 3.0 + hr * 4.0 + rbi * 1.5 + r * 1.0 + sb * 1.0 + bb * 1.0
                    season = p_info.get("seasonStats", {}).get("batting", {})
                    candidates.append({
                        "type": "batter", "id": p_id, "name": p_name, "team": team_abbr,
                        "pos": pos, "score": score,
                        "stats": {
                            "ab": ab, "r": r, "h": h, "rbi": rbi, "bb": bb, "so": so,
                            "d": d, "t": t, "hr": hr, "sb": sb,
                            "season_d": season.get("doubles", 0),
                            "season_t": season.get("triples", 0),
                            "season_hr": season.get("homeRuns", 0),
                            "season_sb": season.get("stolenBases", 0),
                        },
                    })

            # 投手
            pit = p_info.get("stats", {}).get("pitching", {})
            ip_str = pit.get("inningsPitched") if pit else None
            if ip_str and ip_str != "0.0":
                h = pit.get("hits", 0)
                r = pit.get("runs", 0)
                er = pit.get("earnedRuns", 0)
                bb = pit.get("baseOnBalls", 0)
                so = pit.get("strikeOuts", 0)
                parts = ip_str.split(".")
                ip_outs = int(parts[0]) * 3 + (int(parts[1]) if len(parts) > 1 else 0)
                is_win = pit.get("wins", 0) > 0
                is_save = pit.get("saves", 0) > 0
                is_loss = pit.get("losses", 0) > 0
                # Score = IP_outs + K*1 - ER*2 - H*1 - BB*1 + (Win ? 5 : 0) + (Save ? 4 : 0)
                score = ip_outs + so * 1.0 - er * 2.0 - h * 1.0 - bb * 1.0 + (5.0 if is_win else 0.0) + (4.0 if is_save else 0.0)
                decision = "W" if is_win else ("S" if is_save else ("L" if is_loss else ""))
                candidates.append({
                    "type": "pitcher", "id": p_id, "name": p_name, "team": team_abbr,
                    "pos": pos, "score": score, "is_starter": p_id == starters[side],
                    "decision": decision,
                    "stats": {
                        "ip": ip_str, "h": h, "r": r, "er": er, "bb": bb, "so": so,
                        "pitches": pit.get("numberOfPitches", 0), "strikes": pit.get("strikes", 0),
                    },
                })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def select_performers(candidates):
    """雙門檻：前 3 名保證入選；其餘打者 >= 7、先發 >= 15、後援 >= 8。回傳 (打者, 投手, 全部入選)。"""
    selected = []
    for idx, cand in enumerate(candidates):
        if idx < 3:
            selected.append(cand)
        elif cand["type"] == "batter":
            if cand["score"] >= 7.0:
                selected.append(cand)
        else:
            if cand["score"] >= (15.0 if cand["is_starter"] else 8.0):
                selected.append(cand)
    return (
        [c for c in selected if c["type"] == "batter"],
        [c for c in selected if c["type"] == "pitcher"],
        selected,
    )


def build_decisions(dec_obj, players_away, players_home):
    """投球決定：{'w'|'l'|'s': {'id','name','record'} 或 None}。"""
    def lookup(pid):
        obj = players_away.get(f"ID{pid}") or players_home.get(f"ID{pid}")
        if obj:
            return obj["person"]["fullName"], obj.get("seasonStats", {}).get("pitching", {})
        return None, None

    out = {"w": None, "l": None, "s": None}
    for key, field in (("w", "winner"), ("l", "loser"), ("s", "save")):
        entry = dec_obj.get(field)
        if not entry:
            continue
        pid = entry["id"]
        name, stats = lookup(pid)
        if name:
            if key == "s":
                record = f"({stats.get('saves', 0)})"
            else:
                record = f"({stats.get('wins', 0)}-{stats.get('losses', 0)})"
        else:
            name, record = entry.get("fullName", ""), None
        out[key] = {"id": pid, "name": name, "record": record}
    return out


def split_game_transactions(tx_list, away, home):
    """把與本場兩隊相關的異動分出來。away/home 為 {'id','abbr','name','color'}。
    回傳 (本場球隊分組, 其餘異動)。"""
    groups = []
    matched = set()
    for side in (away, home):
        items = [tx for tx in tx_list if side["id"] in (tx["to_team_id"], tx["from_team_id"])]
        if items:
            groups.append({"team": side, "items": items})
            matched.update(tx["description"] for tx in items)
    remaining = [tx for tx in tx_list if tx["description"] not in matched]
    return groups, remaining


# ---------------------------------------------------------------- 渲染：比賽卡片
def batter_tags(stats):
    """長打與速度標籤：[(css_class, 文字)]，順序 2B、3B、HR、SB。"""
    tags = []
    for label, key, season_key, css in (
        ("2B", "d", "season_d", "tag"),
        ("3B", "t", "season_t", "tag"),
        ("HR", "hr", "season_hr", "tag hr"),
        ("SB", "sb", "season_sb", "tag sb"),
    ):
        if stats[key] > 0:
            tags.append((css, f"{label}: {format_cumulative(stats[key], stats[season_key])}"))
    return tags


def _statline(pairs, extra_class=""):
    cells = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in pairs)
    return f'<dl class="statline{extra_class}">{cells}</dl>'


def render_batter(b):
    s = b["stats"]
    tags = "".join(f'<span class="{css}">{esc(text)}</span>' for css, text in batter_tags(s))
    tags_html = f'<span class="tags">{tags}</span>' if tags else ""
    statline = _statline([("AB", s["ab"]), ("R", s["r"]), ("H", s["h"]), ("RBI", s["rbi"]), ("BB", s["bb"]), ("SO", s["so"])])
    return (
        f'<li><div class="p-head"><span class="p-name">{player_link(b["id"], b["name"])}</span>'
        f'<span class="p-meta">{esc(b["team"])} · {esc(b["pos"])}</span>{tags_html}</div>{statline}</li>'
    )


def render_pitcher(p):
    s = p["stats"]
    tags_html = f'<span class="tags"><span class="tag dec">{esc(p["decision"])}</span></span>' if p.get("decision") else ""
    statline = _statline(
        [("IP", s["ip"]), ("H", s["h"]), ("R", s["r"]), ("ER", s["er"]), ("BB", s["bb"]), ("SO", s["so"]),
         ("P-S", f"{s['pitches']}-{s['strikes']}")],
        " is-pitch",
    )
    return (
        f'<li><div class="p-head"><span class="p-name">{player_link(p["id"], p["name"])}</span>'
        f'<span class="p-meta">{esc(p["team"])} · {esc(p["pos"])}</span>{tags_html}</div>{statline}</li>'
    )


def render_decisions(decisions, is_final, status_text=""):
    """投球決定：只有完賽（is_final）才列出勝／敗／救援，否則顯示說明文字。"""
    if not is_final:
        note = "本場暫停，尚未產生勝敗，無投球決定。" if status_text == "暫停比賽" else "本場尚未完賽，無投球決定。"
        return f'<section><h4 class="sub">投球決定</h4><p class="empty-note">{note}</p></section>'

    def row(label, entry, win=False):
        dt = f'<dt class="is-w">{label}</dt>' if win else f"<dt>{label}</dt>"
        if not entry:
            return f"{dt}<dd>無</dd>"
        rec = f' <span class="rec">{esc(entry["record"])}</span>' if entry.get("record") else ""
        return f'{dt}<dd>{player_link(entry["id"], entry["name"])}{rec}</dd>'

    d = decisions or {}
    return (
        '<section><h4 class="sub">投球決定</h4><dl class="decisions">'
        + row("勝", d.get("w"), True) + row("敗", d.get("l")) + row("救援", d.get("s"))
        + "</dl></section>"
    )


def render_news_box(news):
    if not news:
        return ""
    return (
        '<section><h4 class="sub">賽後新聞</h4><article class="mini-news">'
        f'<h5>{esc(news.get("headline_zh") or news["headline"])}</h5><p>{esc(news.get("blurb_zh") or news["blurb"])}</p></article></section>'
    )


def render_card_transactions(groups):
    if not groups:
        return ""
    parts = []
    for g in groups:
        team = g["team"]
        parts.append(
            f'<div class="tx-group{stripe_class(team["color"])}" style="--team:{safe_color(team["color"])}">'
            f'<h5>{logo_picture(team["id"], team["abbr"], 18)}{esc(team["name"])}</h5>'
            f'<ul class="tx-list">{render_tx_items(g["items"])}</ul></div>'
        )
    return '<section><h4 class="sub">本場相關人事異動</h4>' + "".join(parts) + "</section>"


def _card_open(view):
    away, home = view["away"], view["home"]
    tokens = []
    for side in (away, home):
        tokens += [side["abbr"], side["name"], side.get("name_en", "")]
        if side["abbr"] in LEGACY_ABBR_ALIASES:
            tokens.append(LEGACY_ABBR_ALIASES[side["abbr"]])
    teams = "|".join(str(t).replace("|", " ") for t in tokens)
    label = f'{away["name"]}對{home["name"]}'
    return f'<article class="game" data-teams="{esc(teams)}" aria-label="{esc(label)}">'


def _card_head(view):
    return (
        f'<div class="game-head"><h3 class="game-title">{esc(view["type_label"])} '
        f'<span class="num">#{esc(view["game_pk"])}</span></h3>'
        f'<span class="badge is-{esc(view["status_class"])}">{esc(view["status_text"])}</span></div>'
    )


def _score_table(view):
    rows = []
    for side in (view["away"], view["home"]):
        tr = '<tr class="is-win">' if side.get("winner") else "<tr>"
        rows.append(
            f'{tr}<td class="team{stripe_class(side["color"])}" style="--team:{safe_color(side["color"])}">'
            f'<div class="team-inner">{logo_picture(side["id"], side["abbr"], 24)}'
            f'<span class="tname"><span class="name">{esc(side["name"])}</span>'
            f'<span class="record">{esc(side["record"])}</span></span></div></td>'
            f'<td class="n runs">{esc(side["r"])}</td><td class="n">{esc(side["h"])}</td>'
            f'<td class="n">{esc(side["e"])}</td></tr>'
        )
    caption = f'{view["away"]["name"]}對{view["home"]["name"]}'
    return (
        f'<table class="score"><caption class="visually-hidden">{esc(caption)}</caption>'
        '<thead><tr><th scope="col">隊伍</th><th scope="col" class="col-r">R</th>'
        '<th scope="col" class="col-h">H</th><th scope="col" class="col-e">E</th></tr></thead>'
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_game_card(view):
    """完整比賽卡片（有 boxscore）。view 為 build_game_view() 的輸出加上 news、tx_groups。"""
    batters = "".join(render_batter(b) for b in view["batters"])
    pitchers = "".join(render_pitcher(p) for p in view["pitchers"])
    batters_html = f'<ul class="perf">{batters}</ul>' if batters else '<p class="empty-note">本場無突出打擊表現。</p>'
    pitchers_html = f'<ul class="perf">{pitchers}</ul>' if pitchers else '<p class="empty-note">本場無突出投手表現。</p>'
    decisions = render_decisions(view["decisions"], view["is_final"], view["status_text"])
    return (
        f"{_card_open(view)}\n  {_card_head(view)}\n  {_score_table(view)}\n"
        '  <div class="game-body">\n    <div class="col-left">\n      '
        f'{decisions}{render_news_box(view.get("news"))}{render_card_transactions(view.get("tx_groups"))}\n    </div>\n'
        '    <div class="col-right">\n'
        f'<div class="perf-group"><h4 class="sub">打擊焦點</h4>{batters_html}</div>\n'
        f'<div class="perf-group"><h4 class="sub">投球焦點</h4>{pitchers_html}</div>\n'
        "    </div>\n  </div>\n</article>"
    )


def render_fallback_card(view):
    """boxscore 抓取失敗時，僅用 schedule 資料的簡化卡片（H／E 無資料以 — 表示）。"""
    return (
        f"{_card_open(view)}\n  {_card_head(view)}\n  {_score_table(view)}\n"
        '  <div class="note-strip"><strong>詳細數據暫缺</strong>：本場 boxscore 暫時無法取得，僅顯示比分。</div>\n'
        "</article>"
    )


def render_postponed_card(view):
    """延賽／取消卡。view 需含 note_title、notice、icon。"""
    def side(s):
        return f'<span class="side">{logo_picture(s["id"], s["abbr"], 24)}{esc(s["name"])}</span>'
    return (
        f"{_card_open(view)}\n  {_card_head(view)}\n"
        f'  <div class="game-note">\n    <div class="icon" aria-hidden="true">{view["icon"]}</div>\n'
        f'    <h3>{esc(view["note_title"])}</h3>\n'
        f'    <div class="matchup">{side(view["away"])}<span class="at">@</span>{side(view["home"])}</div>\n'
        f'    <p>{esc(view["notice"])}</p>\n  </div>\n</article>'
    )


# ---------------------------------------------------------------- 組裝：schedule / boxscore -> view
def _side_view(game, side, info):
    team_obj = game["teams"][side]["team"]
    rec = game["teams"][side].get("leagueRecord", {})
    return {
        "id": team_obj["id"],
        "abbr": info["abbr"],
        "name": info["name"],
        "name_en": team_obj.get("name", ""),
        "color": info["color"],
        "record": f"({rec.get('wins', 0)}-{rec.get('losses', 0)})",
        "r": 0, "h": "—", "e": "—", "winner": False,
    }


def build_base_view(game, away_info, home_info):
    """狀態、隊伍與比分（來源為 schedule 的 linescore，缺漏時 R 退回 teams.score；H／E 缺漏為 —）。"""
    linescore = game.get("linescore", {})
    ls_teams = linescore.get("teams", {})
    status_text, status_class, is_final = classify_game_status(game, linescore)
    view = {
        "game_pk": game["gamePk"],
        "type_label": game_type_label(game),
        "status_text": status_text,
        "status_class": status_class,
        "is_final": is_final,
        "away": _side_view(game, "away", away_info),
        "home": _side_view(game, "home", home_info),
    }
    for side in ("away", "home"):
        ls = ls_teams.get(side, {})
        view[side]["r"] = ls.get("runs", game["teams"][side].get("score", 0))
        view[side]["h"] = ls.get("hits", "—")
        view[side]["e"] = ls.get("errors", "—")
    away_r, home_r = view["away"]["r"], view["home"]["r"]
    view["away"]["winner"] = bool(is_final and away_r > home_r)
    view["home"]["winner"] = bool(is_final and home_r > away_r)
    return view


def build_postponed_view(game, away_info, home_info):
    view = build_base_view(game, away_info, home_info)
    status = game.get("status", {})
    type_label = game_type_label(game)
    if status.get("detailedState") == "Cancelled":
        reason_raw = status.get("reason")
        reason_eng = f"Cancelled - {reason_raw}" if reason_raw else "Cancelled"
        if reason_raw in CANCELLED_REASONS:
            reason_chi = CANCELLED_REASONS[reason_raw]
        elif reason_raw:
            reason_chi = f"賽事取消（原因：{reason_raw}）"
        else:
            reason_chi = "賽事取消"
        view.update(
            status_text="Cancelled", status_class="cancelled", icon="🚫",
            note_title="賽事取消",
            notice=f"原定於當日舉行的{type_label}賽事，{reason_chi} ({reason_eng})。",
        )
    else:
        reason_eng = status.get("reason", "Postponed")
        reason_chi = POSTPONED_REASONS.get(reason_eng, "賽事延期")
        view.update(
            status_text="Postponed", status_class="postponed", icon="🌧️",
            note_title=reason_chi,
            notice=f"原定於當日舉行的{type_label}賽事，{reason_chi} (Postponed - {reason_eng})。補賽日期將另行公布。",
        )
    return view


def build_game_view(game, boxscore, away_info, home_info):
    """有 boxscore 的完整卡片資料。回傳 (view, 入選焦點球員清單)。"""
    view = build_base_view(game, away_info, home_info)
    teams = boxscore.get("teams", {})
    players_away = teams.get("away", {}).get("players", {})
    players_home = teams.get("home", {}).get("players", {})
    view["decisions"] = build_decisions(game.get("decisions", {}), players_away, players_home)

    candidates = collect_candidates(boxscore, away_info["abbr"], home_info["abbr"])
    print(f"  Game {game['gamePk']} ({away_info['abbr']} @ {home_info['abbr']}) candidates score log:")
    for cand in candidates[:5]:
        print(f"    - {cand['name']} ({cand['team']} - {cand['type']}): score={cand['score']:.1f}")
    batters, pitchers, selected = select_performers(candidates)
    view["batters"], view["pitchers"] = batters, pitchers
    return view, selected


def top_focus_score(selected):
    """該場焦點球員的最高積分（selected 已依積分由高到低）；無焦點球員為 0。"""
    return max((c["score"] for c in selected), default=0.0)


def pick_summary_sentences(entries, limit=3):
    """entries 為 [(焦點最高積分, 摘要句)]；取積分最高的 limit 場（同分維持原賽程順序）。"""
    ranked = sorted(entries, key=lambda e: -e[0])
    return [sentence for _, sentence in ranked[:limit]]


def build_summary_sentence(view, selected):
    sentence = f"{view['away']['abbr']} @ {view['home']['abbr']}，比分 {view['away']['r']}:{view['home']['r']}"
    if selected:
        top = selected[0]
        if top["type"] == "batter" and top["stats"]["hr"] > 0:
            sentence += f"，{top['name']} 擊出全壘打"
        elif top["type"] == "pitcher" and top["stats"]["so"] >= 5:
            sentence += f"，{top['name']} 投出 {top['stats']['so']} 次三振"
    return sentence


# ---------------------------------------------------------------- 組裝：整頁
def render_report(date_str, page_type, cards_html=(), postponed_count=0, tx_list=(), news=None):
    """賽季戰報與休賽季快報共用：組出所有插槽後交給 render_page。"""
    cards_html = list(cards_html)
    tx_list = list(tx_list)
    if page_type == "offseason":
        subtitle, title_suffix = "休賽季快報", " 休賽季快報"
        summary = render_summary(
            "offseason", news_count=None if news is None else len(news), transactions_count=len(tx_list))
        games = ""
        transactions = render_transactions_section(tx_list, "大聯盟人事異動", "本日無大聯盟人事異動。")
    else:
        subtitle, title_suffix = "賽事結果", ""
        summary = render_summary("season", total_games=len(cards_html), postponed=postponed_count)
        games = render_games_section(cards_html)
        transactions = render_transactions_section(tx_list)
    return render_page({
        "TITLE": esc(f"MLB 每日戰報 - {date_str}{title_suffix}"),
        "DATE_LONG": esc(fmt_date_long(date_str)),
        "SUBTITLE": esc(subtitle),
        "SUMMARY": summary,
        "SEARCH": render_search() if cards_html else "",
        "NEWS": render_news_section(news, page_type),
        "GAMES": games,
        "TRANSACTIONS": transactions,
        "GENERATED_AT": esc(get_taipei_now().strftime("%Y-%m-%d %H:%M")),
    })


def write_report(date_str, html_content):
    year, month, _ = date_str.split("-")
    output_dir = os.path.join("reports", year, month)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{date_str}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Successfully generated report: {path}")
    return path


def generate_report(date_str, games):
    print(f"Generating report for {date_str} with {len(games)} games...")
    total_games = len(games)
    postponed_count = 0
    cards = []  # str（已渲染）或 dict（待翻譯後才渲染的完整卡片 view）
    game_views = []
    key_matchups_summary = []

    tx_list = fetch_daily_transactions(date_str)
    transactions_count = len(tx_list)
    news = fetch_daily_news(date_str)

    for game in games:
        game_pk = game["gamePk"]
        away_team_obj = game["teams"]["away"]["team"]
        home_team_obj = game["teams"]["home"]["team"]
        away_info = get_team_info(away_team_obj["id"], away_team_obj["name"])
        home_info = get_team_info(home_team_obj["id"], home_team_obj["name"])

        # 1. 延賽／取消
        if game.get("status", {}).get("detailedState") in ("Postponed", "Cancelled"):
            postponed_count += 1
            cards.append(render_postponed_card(build_postponed_view(game, away_info, home_info)))
            continue

        # 2. 已進行的比賽：先取 boxscore，失敗則簡化卡
        boxscore = fetch_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore")
        if not boxscore:
            print(f"Warning: Could not fetch boxscore for game {game_pk}. Using fallback card.", file=sys.stderr)
            view = build_base_view(game, away_info, home_info)
            cards.append(render_fallback_card(view))
            key_matchups_summary.append((0.0, build_summary_sentence(view, [])))
            continue

        view, selected = build_game_view(game, boxscore, away_info, home_info)
        key_matchups_summary.append((top_focus_score(selected), build_summary_sentence(view, selected)))
        view["news"] = fetch_game_news(
            game_pk, away_info["abbr"], home_info["abbr"], view["away"]["r"], view["home"]["r"]
        )
        view["tx_groups"], tx_list = split_game_transactions(tx_list, view["away"], view["home"])
        cards.append(view)
        game_views.append(view)

    tx_items = [tx for v in game_views for g in v["tx_groups"] for tx in g["items"]] + list(tx_list)
    team_names = [(v[side]["id"], v[side]["name_en"]) for v in game_views for side in ("away", "home")]
    translated = apply_translations(news, game_views, tx_items, team_names)
    cards_html = [c if isinstance(c, str) else render_game_card(c) for c in cards]

    write_report(date_str, render_report(date_str, "season", cards_html, postponed_count, tx_list, news))

    summary_sentence = f"昨日共進行了 {total_games} 場賽事。"
    if postponed_count > 0:
        summary_sentence += f"其中有 {postponed_count} 場賽事延期。"
    if key_matchups_summary:
        summary_sentence += " " + "；".join(pick_summary_sentences(key_matchups_summary)) + "。"

    meta = {
        "date": date_str,
        "total_games": total_games,
        "postponed": postponed_count,
        "summary": summary_sentence,
        "type": "season",
        "transactions_count": transactions_count,
        "translated": translated,
    }
    if news is not None:  # 抓取失敗時不寫 news_count
        meta["news_count"] = len(news)
    return meta


def build_offseason_summary(news, transactions_count):
    """休賽季 metadata 摘要（純文字，渲染時另行 escape）：優先用前 3 則新聞標題（已翻譯用譯文）。"""
    titles = [(n.get("title_zh") or n["title"]).strip() for n in (news or [])[:3]]
    titles = [t for t in titles if t]
    if titles:
        return "；".join(titles) + "。"
    if transactions_count > 0:
        return f"休賽季：人事異動 {transactions_count} 筆"
    return "休賽季，無重大消息"


def generate_offseason_report(date_str):
    """無賽事日：產出「休賽季快報」頁面（新聞＋大聯盟人事異動），回傳 metadata。"""
    tx_list = fetch_daily_transactions(date_str)
    news = fetch_daily_news(date_str)
    translated = apply_translations(news, [], tx_list)
    write_report(date_str, render_report(date_str, "offseason", tx_list=tx_list, news=news))
    meta = {
        "date": date_str,
        "total_games": 0,
        "postponed": 0,
        "summary": build_offseason_summary(news, len(tx_list)),
        "type": "offseason",
        "transactions_count": len(tx_list),
        "translated": translated,
    }
    if news is not None:  # 抓取失敗時不寫 news_count
        meta["news_count"] = len(news)
    return meta

def load_metadata(metadata_path):
    """讀取 metadata。不存在 -> 空 dict；讀取/解析失敗 -> 結束程式，不覆寫原檔。"""
    if not os.path.exists(metadata_path):
        return {}
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"Error: Could not read metadata.json ({e}). Aborting without overwriting.", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print("Error: metadata.json is not a JSON object. Aborting without overwriting.", file=sys.stderr)
        sys.exit(1)
    return data

def save_metadata(metadata_path, metadata_db):
    """先寫到同目錄暫存檔再 os.replace；失敗則結束程式。"""
    tmp_path = metadata_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(metadata_db, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, metadata_path)
    except Exception as e:
        print(f"Error saving metadata.json: {e}", file=sys.stderr)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        sys.exit(1)


# ---------------------------------------------------------------- 首頁
DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def entry_type(entry):
    """metadata 項目的頁面類型；舊資料缺 type 時以 total_games 推斷。"""
    t = entry.get("type")
    if t in ("season", "offseason"):
        return t
    return "season" if _to_int(entry.get("total_games")) > 0 else "offseason"


def _offseason_counts(entry):
    """休賽季項目的 [(標籤, 文字)]；缺欄位則省略該數字。"""
    out = []
    if "news_count" in entry:
        out.append(("新聞", f"{_to_int(entry['news_count'])} 則"))
    if "transactions_count" in entry:
        out.append(("人事異動", f"{_to_int(entry['transactions_count'])} 筆"))
    return out


def render_latest_card(date_str, entry):
    kind = entry_type(entry)
    date_main, _, weekday = fmt_date_long(date_str).partition("（")
    wk = f"（{weekday}" if weekday else ""
    if kind == "offseason":
        eyebrow = "休賽季快報"
        stats = [("", label, text) for label, text in _offseason_counts(entry)]
    else:
        eyebrow = "戰報"
        postponed = _to_int(entry.get("postponed"))
        stats = [
            ("", "總場數", str(_to_int(entry.get("total_games")))),
            (' class="is-alert"' if postponed else "", "延賽", str(postponed)),
        ]
    stat_html = ""
    if stats:
        stat_html = '\n      <dl class="stat-row">' + "".join(
            f"<div{cls}><dt>{esc(label)}</dt><dd>{esc(text)}</dd></div>" for cls, label, text in stats
        ) + "</dl>"
    return f"""  <article class="latest" aria-labelledby="latest-h">
    <div>
      <p class="eyebrow">最新一期<span class="sep" aria-hidden="true">/</span>{eyebrow}</p>
      <h2 id="latest-h"><time datetime="{esc(date_str)}">{esc(date_main)}</time><span class="wk">{esc(wk)}</span></h2>
      <p class="summary">{esc(entry.get("summary", ""))}</p>{stat_html}
    </div>
    <a class="btn" href="{esc(report_href(date_str))}">閱讀完整戰報 →</a>
  </article>"""


def render_recent_item(date_str, entry):
    kind = entry_type(entry)
    date_main, _, weekday = fmt_date_long(date_str).partition("（")
    wk = f"（{weekday}" if weekday else ""
    summary = str(entry.get("summary", ""))
    if kind == "offseason":
        label, label_cls = "休賽季", "k is-off"
        counts = "、".join(f"{lab} {text}" for lab, text in _offseason_counts(entry))
        if counts:
            summary = f"{summary}（{counts}）" if summary else counts
    else:
        label, label_cls = f"{_to_int(entry.get('total_games'))} 場", "k"
    return (
        f'      <li><a class="issue" href="{esc(report_href(date_str))}">'
        f'<span class="d">{esc(date_str)}<span class="wk">{esc(wk)}</span></span>'
        f'<span class="{label_cls}">{esc(label)}</span><span class="s">{esc(summary)}</span></a></li>'
    )


TEAMS_ENTRY_HTML = """  <a class="teams-entry" href="teams.html">
    <span><strong>球隊名單</strong><span class="sub-txt">30 隊球員名單與本季數據，依守備位置分組</span></span>
    <span class="go" aria-hidden="true">30 隊名單 →</span>
  </a>"""


def render_index(metadata):
    """首頁：最新一期大卡 + 近期 6 期（不含最新一期）。每次由 templates/index.html 全量重新產生。"""
    dates = sorted(
        (d for d, v in metadata.items() if DATE_KEY_RE.match(str(d)) and isinstance(v, dict)),
        reverse=True,
    )
    if not dates:
        return fill_slots(load_template("index.html"), {
            "LATEST": '  <p class="section-empty">尚無戰報。</p>\n\n' + TEAMS_ENTRY_HTML,
            "RECENT": "",
        })
    latest = render_latest_card(dates[0], metadata[dates[0]]) + "\n\n" + TEAMS_ENTRY_HTML
    recent_dates = dates[1:7]
    items = "\n".join(render_recent_item(d, metadata[d]) for d in recent_dates)
    if items:
        ol = f'\n    <ol class="issues">\n{items}\n    </ol>'
        count = f"最新 {len(recent_dates)} 期"
    else:
        ol = '\n    <p class="section-empty">尚無更早的戰報。</p>'
        count = "0 期"
    recent = f"""  <section class="section" aria-labelledby="recent-h">
    <div class="section-head"><h2 id="recent-h">近期</h2><span class="count">{count}</span></div>{ol}
    <p class="more"><a class="btn is-quiet" href="archive.html">查看全部歷史 →</a></p>
  </section>"""
    return fill_slots(load_template("index.html"), {"LATEST": latest, "RECENT": recent})


def update_index_page(new_metadata):
    metadata_path = "reports/metadata.json"
    metadata_db = load_metadata(metadata_path)
    metadata_db[new_metadata["date"]] = new_metadata
    save_metadata(metadata_path, metadata_db)

    # 首頁每次由模板與 metadata 全量重新產生
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(render_index(metadata_db))
    print("Successfully updated index.html index page.")

def should_notify(entry):
    """休賽季快報在新聞與人事異動皆為 0（缺欄位視為 0）時不發通知；其餘照常。"""
    if entry_type(entry) == "offseason":
        return _to_int(entry.get("news_count")) > 0 or _to_int(entry.get("transactions_count")) > 0
    return True


def send_discord_notification(new_meta):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("Warning: DISCORD_WEBHOOK_URL env variable not set. Skipping Discord notification.")
        return
    
    date_str = new_meta["date"]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_formatted = dt.strftime("%Y 年 %m 月 %d 日")
    except:
        date_formatted = date_str
        
    parts = date_str.split('-')
    report_url = f"https://haolun588.github.io/MLB-daily/reports/{parts[0]}/{parts[1]}/{date_str}.html"
    
    if entry_type(new_meta) == "offseason":
        parts_text = []
        if "news_count" in new_meta:
            parts_text.append(f"新聞 {_to_int(new_meta['news_count'])} 則")
        if "transactions_count" in new_meta:
            parts_text.append(f"人事異動 {_to_int(new_meta['transactions_count'])} 筆")
        description = "休賽季快報：" + "、".join(parts_text) if parts_text else "休賽季快報已生成。"
        fields = []
    else:
        description = "昨日焦點戰報與最新賽況已生成，點擊下方連結即可瀏覽完整內容！"
        if "news_count" in new_meta:
            description += f"\n新聞 {_to_int(new_meta['news_count'])} 則"
        fields = [
            {
                "name": "📊 賽事統計",
                "value": f"• 總場數：**{new_meta['total_games']}** 場\n• 延賽場數：**{new_meta['postponed']}** 場",
                "inline": True
            },
            {
                "name": "📝 焦點摘要",
                "value": new_meta["summary"]
            }
        ]
        
    payload = {
        "embeds": [
            {
                "title": f"⚾ MLB 每日焦點戰報 - {date_formatted}",
                "description": description,
                "url": report_url,
                "color": 795456,  # Yankees Blue: #0C2340
                "fields": fields,
                "footer": {
                    "text": "MLB Daily 每日戰報系統"
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            print(f"Discord notification sent successfully. Response code: {response.getcode()}")
    except Exception as e:
        print(f"Error sending Discord notification: {e}", file=sys.stderr)


# ---------------------------------------------------------------- 球隊名單（--rosters）
ROSTER_DIR = os.path.join("data", "teams")
ROSTER_FETCH_INTERVAL = 0.5


def _parse_iso_date(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def resolve_roster_context(today):
    """依台北日期 today（date）判斷名單類型與數據賽季。

    回傳 dict：rosterType（active／40Man）、season（數據賽季 statsSeason）、rosterSeason（名單 season 參數）。
    賽季 API 失敗或缺日期時退回以月份推估並印 warning。
    """
    year = today.year
    start = end = None
    data = fetch_json(f"https://statsapi.mlb.com/api/v1/seasons/{year}?sportId=1")
    try:
        info = data["seasons"][0]
        start = _parse_iso_date(info.get("regularSeasonStartDate"))
        end = _parse_iso_date(info.get("postSeasonEndDate")) or _parse_iso_date(info.get("seasonEndDate"))
    except (TypeError, KeyError, IndexError, AttributeError):
        pass
    if start is None or end is None:
        print(f"Warning: 無法取得 {year} 賽季日期，改以月份推估賽季。", file=sys.stderr)
        if 4 <= today.month <= 10:
            return {"rosterType": "active", "season": year, "rosterSeason": year}
        if today.month >= 11:
            return {"rosterType": "40Man", "season": year, "rosterSeason": year}
        return {"rosterType": "40Man", "season": year - 1, "rosterSeason": year}
    if today < start:
        return {"rosterType": "40Man", "season": year - 1, "rosterSeason": year}
    if today > end:
        return {"rosterType": "40Man", "season": year, "rosterSeason": year}
    return {"rosterType": "active", "season": year, "rosterSeason": year}


def roster_url(team_id, roster_type, roster_season, stats_season):
    hydrate = (f"person(stats(type=%5Bseason,sabermetrics%5D,group=%5Bhitting,pitching%5D,"
               f"season={stats_season}))")
    return (f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster?rosterType={roster_type}"
            f"&season={roster_season}&hydrate={hydrate}")


def _num(value):
    """API 數值（含字串如 ".285"）轉 float；"-.--"、".---"、"*.**"、空值或缺漏回傳 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _round(value, digits):
    n = _num(value)
    return None if n is None else round(n, digits)


def _int_or_none(value):
    n = _num(value)
    return None if n is None else int(round(n))


def _find_stat(person, stat_type, group):
    """取 person.stats 中指定 type／group 的 stat dict；多筆 split（交易球員）取全季合計（無 team 的那筆）。"""
    for st in person.get("stats") or []:
        if (st.get("type") or {}).get("displayName") != stat_type:
            continue
        if (st.get("group") or {}).get("displayName") != group:
            continue
        splits = st.get("splits") or []
        if not splits:
            return None
        chosen = next((sp for sp in splits if "team" not in sp), splits[0])
        return chosen.get("stat") or None
    return None


def build_hitting(person):
    season = _find_stat(person, "season", "hitting")
    if not season:
        return None
    saber = _find_stat(person, "sabermetrics", "hitting") or {}
    pa = _num(season.get("plateAppearances"))
    bb, so = _num(season.get("baseOnBalls")), _num(season.get("strikeOuts"))
    bb_pct = round(bb / pa * 100, 1) if pa and bb is not None else None
    k_pct = round(so / pa * 100, 1) if pa and so is not None else None
    return {
        "g": _int_or_none(season.get("gamesPlayed")),
        "pa": _int_or_none(pa),
        "avg": _num(season.get("avg")),
        "obp": _num(season.get("obp")),
        "slg": _num(season.get("slg")),
        "hr": _int_or_none(season.get("homeRuns")),
        "sb": _int_or_none(season.get("stolenBases")),
        "bbPct": bb_pct,
        "kPct": k_pct,
        "wrcPlus": _int_or_none(saber.get("wRcPlus")),
        "war": _round(saber.get("war"), 1),
    }


def build_pitching(person):
    season = _find_stat(person, "season", "pitching")
    if not season:
        return None
    saber = _find_stat(person, "sabermetrics", "pitching") or {}
    g = season.get("gamesPitched")
    if g is None:
        g = season.get("gamesPlayed")
    ip = season.get("inningsPitched")
    return {
        "g": _int_or_none(g),
        "gs": _int_or_none(season.get("gamesStarted")),
        "ip": None if ip in (None, "") else str(ip),
        "w": _int_or_none(season.get("wins")),
        "l": _int_or_none(season.get("losses")),
        "sv": _int_or_none(season.get("saves")),
        "hld": _int_or_none(season.get("holds")),
        "era": _num(season.get("era")),
        "whip": _num(season.get("whip")),
        "so": _int_or_none(season.get("strikeOuts")),
        "k9": _num(season.get("strikeoutsPer9Inn")),
        "fip": _round(saber.get("fip"), 2),
        "war": _round(saber.get("war"), 1),
    }


def pitcher_group(pitching):
    """GS／G >= 0.5 為 SP，其餘（含無數據）為 RP。"""
    if pitching and pitching.get("g") and (pitching.get("gs") or 0) / pitching["g"] >= 0.5:
        return "SP"
    return "RP"


def build_player(entry):
    """roster 單筆 -> 精簡球員 dict；缺 person.id 回傳 None。"""
    person = entry.get("person") or {}
    pid = person.get("id")
    if pid is None:
        return None
    pos = (entry.get("position") or {}).get("abbreviation") or ""
    player = {
        "id": pid,
        "name": person.get("fullName") or "",
        "number": str(entry.get("jerseyNumber") or ""),
        "pos": pos,
    }
    if pos == "P":
        pitching = build_pitching(person)
        player.update({"group": pitcher_group(pitching), "hitting": None, "pitching": pitching})
    elif pos == "TWP":
        pitching = build_pitching(person)
        player["group"] = "POS"
        if pitching:
            player["pitchingGroup"] = pitcher_group(pitching)
        player["hitting"] = build_hitting(person)
        player["pitching"] = pitching
    else:
        player.update({"group": "POS", "hitting": build_hitting(person), "pitching": None})
    return player


def build_team_players(roster_data):
    """roster API 回應 -> 球員清單；回應結構不符回傳 None。"""
    if not isinstance(roster_data, dict) or not isinstance(roster_data.get("roster"), list):
        return None
    players = [build_player(e) for e in roster_data["roster"] if isinstance(e, dict)]
    return [p for p in players if p]


def write_json_atomic(path, data):
    """先寫暫存檔再 os.replace。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def fetch_rosters(today):
    """抓 30 隊名單與數據，寫 data/teams/{id}.json 與 index.json。回傳成功隊數。"""
    ctx = resolve_roster_context(today)
    roster_type, season, roster_season = ctx["rosterType"], ctx["season"], ctx["rosterSeason"]
    print(f"Roster context: rosterType={roster_type}, season={season}, rosterSeason={roster_season}")
    now_iso = get_taipei_now().isoformat(timespec="seconds")
    teams_index = []
    ok_count = 0
    for i, (tid, meta) in enumerate(sorted(TEAM_META.items(), key=lambda kv: kv[1]["abbr"])):
        if i:
            time.sleep(ROSTER_FETCH_INTERVAL)
        entry = {"id": tid, "abbr": meta["abbr"], "name": meta["name"], "color": meta["color"],
                 "league": meta["league"], "division": meta["division"], "ok": False}
        players = None
        try:
            players = build_team_players(fetch_json(roster_url(tid, roster_type, roster_season, season)))
        except Exception as e:
            print(f"Warning: {meta['abbr']} 名單處理失敗：{e}", file=sys.stderr)
        if players is None:
            print(f"Warning: {meta['abbr']} 名單抓取失敗，保留舊檔（若有）。", file=sys.stderr)
        else:
            try:
                write_json_atomic(os.path.join(ROSTER_DIR, f"{tid}.json"), {
                    "meta": {"id": tid, "abbr": meta["abbr"], "name": meta["name"], "rosterType": roster_type,
                             "season": season, "updatedAt": now_iso},
                    "players": players,
                })
                entry["ok"] = True
                entry["updatedAt"] = now_iso
                ok_count += 1
            except OSError as e:
                print(f"Warning: {meta['abbr']} 寫檔失敗：{e}", file=sys.stderr)
        teams_index.append(entry)
    write_json_atomic(os.path.join(ROSTER_DIR, "index.json"), {
        "updatedAt": now_iso, "rosterType": roster_type, "season": season, "teams": teams_index,
    })
    print(f"Rosters done: {ok_count}/{len(TEAM_META)} teams updated.")
    return ok_count


def main():
    parser = argparse.ArgumentParser(description="MLB Daily Report Generator Script")
    parser.add_argument("--date", type=str, help="Target report date in YYYY-MM-DD format (defaults to yesterday in Taipei Time)")
    parser.add_argument("--check-wait", action="store_true", help="If active, loops and sleeps 30 minutes until yesterday's games are finished")
    parser.add_argument("--max-wait-checks", type=int, default=MAX_WAIT_CHECKS, help=f"Max number of checks in the --check-wait loop (default {MAX_WAIT_CHECKS})")
    
    parser.add_argument("--rosters", action="store_true", help="Fetch all 30 team rosters with season stats and write data/teams/*.json (no report, no notification)")
    parser.add_argument("--no-notify", action="store_true", help="Generate report and metadata but do not send Discord notification")
    parser.add_argument("--notify-only", action="store_true", help="Only send Discord notification from existing metadata (no API calls, no file writes)")

    args = parser.parse_args()
    if args.notify_only and (args.check_wait or args.no_notify):
        print("Error: --notify-only cannot be combined with --check-wait or --no-notify", file=sys.stderr)
        sys.exit(1)
    if args.rosters and (args.notify_only or args.check_wait):
        print("Error: --rosters cannot be combined with --notify-only or --check-wait", file=sys.stderr)
        sys.exit(1)
    if args.max_wait_checks < 1:
        print("Error: --max-wait-checks must be >= 1", file=sys.stderr)
        sys.exit(1)
    
    # --rosters: 只更新 data/teams，不產生戰報、不動 metadata、不發通知
    if args.rosters:
        if args.date:
            try:
                today = datetime.strptime(args.date, "%Y-%m-%d").date()
            except ValueError:
                print("Error: Date format must be YYYY-MM-DD", file=sys.stderr)
                sys.exit(1)
        else:
            today = get_taipei_now().date()
        sys.exit(0 if fetch_rosters(today) > 0 else 1)

    # 1. Resolve date
    if args.date:
        date_str = args.date
        # Validate date format
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            print("Error: Date format must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        date_str = get_yesterday_str()
        
    print(f"Target Date: {date_str}")

    # --notify-only: 僅依既有 metadata 發送通知，不呼叫 API、不寫檔
    if args.notify_only:
        entry = load_metadata("reports/metadata.json").get(date_str)
        if not entry:
            print(f"Error: reports/metadata.json 中找不到 {date_str} 的資料，無法發送通知。", file=sys.stderr)
            sys.exit(1)
        if not should_notify(entry):
            print(f"{date_str} 為休賽季快報且無新聞、無人事異動，不發送 Discord 通知。")
            return
        send_discord_notification(entry)
        return

    # 2. Check wait loop if enabled
    if args.check_wait:
        max_checks = args.max_wait_checks
        print(f"Enabling check wait loop (max {max_checks} checks). Monitoring yesterday's games...")
        games = []
        have_games = False  # 是否曾成功取得賽程
        finalized = False
        for attempt in range(1, max_checks + 1):
            try:
                finalized, fetched_games = check_all_games_finalized(date_str)
                games = fetched_games
                have_games = True
            except ApiError as e:
                print(f"Warning: {e}. Treating as not finalized.", file=sys.stderr)
                finalized = False
            if finalized:
                print("All games are finalized (Final, Postponed, Cancelled or Suspended). Proceeding to build.")
                break
            if attempt < max_checks:
                print(f"Some games are not finalized yet (check {attempt}/{max_checks}). Sleeping for 30 minutes...", flush=True)
                time.sleep(WAIT_SLEEP_SECONDS)
        if not finalized:
            if not have_games:
                print(f"Error: 已達等待上限（{max_checks} 次檢查）仍無法取得 {date_str} 的賽程。Aborting without generating any output.", file=sys.stderr)
                sys.exit(1)
            print(f"Warning: 已達等待上限（{max_checks} 次檢查），仍有未完成場次，將照常產出戰報，未完成場次標示為「未完賽」。", file=sys.stderr)
    else:
        try:
            finalized, games = check_all_games_finalized(date_str)
        except ApiError as e:
            print(f"Error: {e}. Aborting without generating any output.", file=sys.stderr)
            sys.exit(1)
        if not finalized:
            print("Warning: Some games are still active or not finalized yet.", file=sys.stderr)

    # Check if there are games scheduled
    if not games:
        print(f"No games scheduled or found on {date_str}. Generating offseason report.")
        empty_meta = generate_offseason_report(date_str)
        update_index_page(empty_meta)
        if not args.no_notify:
            if should_notify(empty_meta):
                send_discord_notification(empty_meta)
            else:
                print("休賽季快報無新聞、無人事異動，不發送 Discord 通知。")
        sys.exit(0)

    # 3. Generate report files
    new_meta = generate_report(date_str, games)
    
    # 4. Update index homepage listings
    update_index_page(new_meta)
    
    # 5. Send Discord notification
    if not args.no_notify:
        send_discord_notification(new_meta)
    print("All tasks completed successfully.")

if __name__ == "__main__":
    main()
