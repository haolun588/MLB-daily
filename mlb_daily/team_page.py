"""球隊專頁的附加資料：球隊摘要、近 30 天動態、隊內數據王（寫進 data/teams/{id}.json）。

層級在 rosters／weekly／standings／playoffs 之上、cli 之下。純函式（compute_leaders、build_recent、
postseason_result、tracked_names）不連網；讀不到或缺資料一律降級為空狀態，不丟例外（呼叫端另有保護）。"""

import re
import sys
from datetime import timedelta

from . import standings as standings_mod, storage, watchlist, weekly
from .config import (
    NEWS_SOURCE,
    TEAM_META,
    TEAM_NAMES_EN,
    TEAM_SLANG_ALIASES,
    TEAM_PAGE_LEADER_MIN_IP,
    TEAM_PAGE_LEADER_MIN_PA,
    TEAM_PAGE_NEWS_LIMIT,
    TEAM_PAGE_RECENT_DAYS,
)

# 數據王項目：(key, 欄位, 方向 desc 越大越好／asc 越小越好, 門檻種類 pa／ip／None, 計數類（需 > 0）)
HITTING_LEADERS = (
    ("avg", "avg", "desc", "pa", False),
    ("hr", "hr", "desc", None, True),
    ("ops", None, "desc", "pa", False),
    ("sb", "sb", "desc", None, True),
    ("war", "war", "desc", None, False),
)
PITCHING_LEADERS = (
    ("era", "era", "asc", "ip", False),
    ("so", "so", "desc", None, True),
    ("sv", "sv", "desc", None, True),
    ("whip", "whip", "asc", "ip", False),
    ("war", "war", "desc", None, False),
)

ROUND_NAMES = {"W": "世界大賽", "L": "聯盟冠軍賽", "D": "分區系列賽", "F": "外卡系列賽"}
ROUND_RANK = {"F": 0, "D": 1, "L": 2, "W": 3}


# ---------------------------------------------------------------- 隊內數據王
def _ip_outs(ip):
    """"182.1" -> 547 出局數；無法解析回傳 None。"""
    if ip in (None, ""):
        return None
    parts = str(ip).strip().split(".")
    try:
        outs = int(parts[0]) * 3
        if len(parts) > 1 and parts[1]:
            outs += int(parts[1])
    except ValueError:
        return None
    return outs


def _number(value):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _pick_leader(players, group, field, direction, qualify, count_only):
    """回傳 (player, value) 或 None。同值取名單中較前者；count_only 項目需 > 0。"""
    best = None
    for p in players:
        stats = p.get(group)
        if not isinstance(stats, dict) or not qualify(stats):
            continue
        if field is None:  # OPS = OBP + SLG
            obp, slg = _number(stats.get("obp")), _number(stats.get("slg"))
            value = None if obp is None or slg is None else round(obp + slg, 3)
        else:
            value = _number(stats.get(field))
        if value is None or (count_only and value <= 0):
            continue
        if best is None or (value < best[1] if direction == "asc" else value > best[1]):
            best = (p, value)
    return best


def compute_leaders(players, min_pa=TEAM_PAGE_LEADER_MIN_PA, min_ip=TEAM_PAGE_LEADER_MIN_IP):
    """由名單本季數據算出打擊與投球各 5 項隊內第一名。
    AVG、OPS 需 PA >= min_pa；ERA、WHIP 需 IP >= min_ip；HR／SB／SO／SV 需 > 0；無人達標時 id、name、value 為 None。"""
    players = [p for p in (players or []) if isinstance(p, dict)]

    def build(group, spec):
        out = []
        for key, field, direction, gate, count_only in spec:
            if gate == "pa":
                def qualify(s):
                    return (_number(s.get("pa")) or 0) >= min_pa
            elif gate == "ip":
                def qualify(s):
                    return (_ip_outs(s.get("ip")) or 0) >= min_ip * 3
            else:
                def qualify(s):
                    return True
            hit = _pick_leader(players, group, field, direction, qualify, count_only)
            if hit is None:
                out.append({"key": key, "id": None, "name": None, "value": None})
            else:
                p, value = hit
                out.append({"key": key, "id": p.get("id"), "name": str(p.get("name") or ""), "value": value})
        return out

    return {
        "thresholds": {"minPa": min_pa, "minIp": min_ip},
        "hitting": build("hitting", HITTING_LEADERS),
        "pitching": build("pitching", PITCHING_LEADERS),
    }


# ---------------------------------------------------------------- 台日韓標籤
def tracked_names(players, wl):
    """該隊名單中屬於台日韓追蹤名單的球員中文名（依追蹤名單順序）。"""
    ids = {p.get("id") for p in (players or []) if isinstance(p, dict)}
    return [info["name_zh"] for pid, info in (wl or {}).items() if pid in ids]


# ---------------------------------------------------------------- 球隊摘要
def _opponent_name(team):
    tid = team.get("id")
    if tid in TEAM_META:
        return TEAM_META[tid]["name"]
    return team.get("name") or "待定對手"


def postseason_result(data, team_id):
    """季後賽成績 {"text", "outcome"}（outcome：champion／eliminated／active／missed）；沒有季後賽資料回傳 None。"""
    rounds = data.get("rounds") if isinstance(data, dict) else None
    if not isinstance(rounds, dict):
        return None
    all_series = []
    for league in ("AL", "NL"):
        for rnd in "FDL":
            all_series.extend((rounds.get(league) or {}).get(rnd) or [])
    all_series.extend(rounds.get("WS") or [])
    all_series = [s for s in all_series if isinstance(s, dict) and isinstance(s.get("teams"), list)]
    if not all_series:
        return None

    best = None
    for s in all_series:
        if not any(isinstance(t, dict) and t.get("id") == team_id for t in s["teams"]):
            continue
        rnd = str(s.get("id") or "")[:1]
        if rnd in ROUND_RANK and (best is None or ROUND_RANK[rnd] > ROUND_RANK[best[0]]):
            best = (rnd, s)
    if best is None:
        return {"text": "未晉級季後賽", "outcome": "missed"}

    rnd, s = best
    mine = next(t for t in s["teams"] if t.get("id") == team_id)
    other = next((t for t in s["teams"] if t is not mine), {})
    name, opp = ROUND_NAMES[rnd], _opponent_name(other)
    mw, ow = _number(mine.get("wins")) or 0, _number(other.get("wins")) or 0
    if s.get("winnerId") == team_id:
        if rnd == "W":
            return {"text": f"{name} {mw}–{ow} 擊敗 {opp}，奪得冠軍", "outcome": "champion"}
        return {"text": f"{name} {mw}–{ow} 擊敗 {opp}，晉級下一輪", "outcome": "active"}
    if s.get("winnerId") is not None:
        return {"text": f"{name} {mw}–{ow} 敗給 {opp}", "outcome": "eliminated"}
    if mw or ow:
        return {"text": f"{name} 進行中，{mw}–{ow} 對 {opp}", "outcome": "active"}
    return {"text": f"{name} 對 {opp}（尚未開打）", "outcome": "active"}


def build_record(standings, team_id):
    """戰績摘要 {"w","l","pct","divRank","rd"}；standings 為 fetch_standings 的回傳（可為 None）。"""
    if not isinstance(standings, dict):
        return None
    for div in standings.get("divisions") or []:
        for t in div.get("teams") or []:
            if t.get("id") == team_id:
                try:
                    pct = float(t["pct"])
                except (TypeError, ValueError, KeyError):
                    pct = None
                return {"w": t["wins"], "l": t["losses"], "pct": pct, "divRank": t.get("rank"), "rd": t.get("run_diff")}
    return None


# ---------------------------------------------------------------- 近期動態
def _team_terms(team_id):
    """該隊新聞比對用的關鍵字：英文全名、暱稱、口語簡稱，以及中文全名與簡稱。"""
    en = TEAM_NAMES_EN.get(team_id)
    terms = []
    if en:
        terms += [en[0], en[1], en[2]]
    terms += TEAM_SLANG_ALIASES.get(team_id, [])
    if team_id in TEAM_META:
        terms.append(TEAM_META[team_id]["name"])
    return [t for t in dict.fromkeys(terms) if t]


def _term_regex(terms):
    """英文詞整詞比對（不分大小寫）、中文詞子字串比對，合成一個 regex。"""
    alts = []
    for t in sorted(terms, key=len, reverse=True):
        if re.search(r"[A-Za-z]", t):
            alts.append(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])")
        else:
            alts.append(re.escape(t))
    return re.compile("|".join(alts), re.IGNORECASE)


def _tx_player(tx):
    names = [p.get("name") for p in (tx.get("persons") or []) if isinstance(p, dict) and p.get("name")]
    return str(tx.get("person_name") or (names[0] if names else "") or "")


def _news_date(news, fallback):
    published = str(news.get("published") or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}", published):
        return published[:10]
    return fallback


def _is_http(url):
    return isinstance(url, str) and re.match(r"^https?://", url, re.IGNORECASE) is not None


def build_recent(team_id, snapshots, news_limit=TEAM_PAGE_NEWS_LIMIT):
    """某隊近期動態 {"moves", "news", "snapshotDays"}。snapshots 為 weekly.load_week_snapshots 的回傳。
    moves：依 weekly 的分類與方向判斷（加入 in／離開 out），新到舊；news：依隊名關鍵字比對標題與摘要，新到舊。"""
    items = weekly._collect_items(snapshots)
    moves = []
    for it in items:
        for tid, direction in weekly.team_moves(it):
            if tid != team_id:
                continue
            tx = it["tx"]
            moves.append({
                "date": it["date"],
                "dir": "in" if direction == "join" else "out",
                "player": _tx_player(tx),
                "type": weekly.CATEGORY_LABELS.get(it["category"], ""),
                "note": str(tx.get("description_zh") or tx.get("description") or ""),
            })

    pattern = _term_regex(_team_terms(team_id))
    seen, found = set(), []
    for key in sorted(snapshots, reverse=True):
        for n in snapshots[key]["news"]:
            link = n["link"].strip()
            if not _is_http(link) or link in seen:
                continue
            text = " ".join(str(n.get(k) or "") for k in ("title", "title_zh", "summary"))
            if not pattern.search(text):
                continue
            seen.add(link)
            found.append({"ts": weekly._published_ts(n.get("published")), "date": _news_date(n, key),
                          "title": str(n.get("title_zh") or n.get("title") or ""), "url": link, "source": NEWS_SOURCE})
    found.sort(key=lambda x: -x["ts"])
    news = [{k: v for k, v in x.items() if k != "ts"} for x in found[:news_limit]]
    return {"moves": moves, "news": news, "snapshotDays": len(snapshots)}


# ---------------------------------------------------------------- 整合（供 rosters.fetch_rosters 呼叫）
def make_extras(today, ctx, snapshot_dir=None, wl=None):
    """回傳 extras(team_id, players) -> dict（summary、recent、leaders）。
    今天的戰績、季後賽、每日快照與追蹤名單只在這裡讀一次；任何來源失敗都只讓對應欄位缺席或為空。"""
    season = ctx.get("season")
    try:
        standings = standings_mod.fetch_standings(season)
    except Exception as e:  # noqa: BLE001 - 摘要缺戰績不得影響名單
        print(f"Warning: 球隊專頁戰績取得失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        standings = None
    playoffs_data = None
    try:
        data = storage.load_playoffs()
        if data and data.get("season") == season:
            playoffs_data = data
    except Exception as e:  # noqa: BLE001
        print(f"Warning: 球隊專頁季後賽資料讀取失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
    try:
        days = [today - timedelta(days=i) for i in range(TEAM_PAGE_RECENT_DAYS)]
        snapshots = weekly.load_week_snapshots(days, snapshot_dir)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: 球隊專頁每日快照讀取失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        snapshots = {}
    if wl is None:
        wl = watchlist.load_watchlist()

    def extras(team_id, players):
        out = {}
        for key, fn in (
            ("summary", lambda: {
                "record": build_record(standings, team_id),
                "postseason": postseason_result(playoffs_data, team_id) if playoffs_data else None,
                "tracked": tracked_names(players, wl),
            }),
            ("recent", lambda: build_recent(team_id, snapshots)),
            ("leaders", lambda: compute_leaders(players)),
        ):
            try:
                out[key] = fn()
            except Exception as e:  # noqa: BLE001 - 單一區塊失敗只缺該區塊
                print(f"Warning: 球隊 {team_id} 的 {key} 區塊產生失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        return out

    return extras
