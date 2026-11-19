"""球隊專頁的附加資料：球隊摘要、近 30 天動態（報表日往前 30 天）、隊內數據王（寫進 data/teams/{id}.json）。

層級在 rosters／weekly／standings／playoffs 之上、cli 之下。純函式（compute_leaders、build_recent、
postseason_result、tracked_names）不連網；讀不到或缺資料一律降級為空狀態，不丟例外（呼叫端另有保護）。"""

import re
import sys
from datetime import date, timedelta

from . import standings as standings_mod, storage, watchlist, weekly
from .config import (
    NEWS_SOURCE,
    NON_MLB_TEAM_PREFIXES,
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


def _tiebreak(p, stats, gate):
    """同值時的次要排序鍵（越小越優先）：打擊 PA 多者、投球 IP 多者優先，再依球員 id 小者。"""
    if gate == "pa":
        volume = _number(stats.get("pa")) or 0
    else:
        volume = (_ip_outs(stats.get("ip")) or 0) if gate == "ip" else 0
    pid = p.get("id")
    return (-volume, pid if isinstance(pid, int) and not isinstance(pid, bool) else float("inf"))


def _pick_leader(players, group, field, direction, qualify, count_only):
    """回傳 (player, value) 或 None。同值依 _tiebreak（PA／IP 多者、再 id 小者）；count_only 項目需 > 0。
    field 為 None 時為 OPS：優先用 API 原始 ops 欄位，沒有才用 OBP + SLG。"""
    best = None
    for p in players:
        stats = p.get(group)
        if not isinstance(stats, dict) or not qualify(stats):
            continue
        if field is None:  # OPS：原始 ops 欄位優先，否則 OBP + SLG
            value = _number(stats.get("ops"))
            if value is None:
                obp, slg = _number(stats.get("obp")), _number(stats.get("slg"))
                value = None if obp is None or slg is None else round(obp + slg, 3)
        else:
            value = _number(stats.get(field))
        if value is None or (count_only and value <= 0):
            continue
        gate = "pa" if group == "hitting" else "ip"
        key = _tiebreak(p, stats, gate)
        if best is None:
            best = (p, value, key)
            continue
        better = value < best[1] if direction == "asc" else value > best[1]
        if better or (value == best[1] and key < best[2]):
            best = (p, value, key)
    return None if best is None else (best[0], best[1])


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
def tracked_names(players, wl, team_id=None):
    """球隊專頁「台日韓球員」標籤：先列該隊名單上的追蹤球員 {"name"}（依追蹤名單順序），
    再列母隊為該隊、不在名單上、目前在小聯盟的台灣球員 {"name","level"}（依層級 AAA→ROK，再依英文名）。"""
    ids = {p.get("id") for p in (players or []) if isinstance(p, dict)}
    out = [{"name": info["name_zh"]} for pid, info in (wl or {}).items() if pid in ids]
    if team_id is not None:
        minors = [info for pid, info in (wl or {}).items()
                  if pid not in ids and info.get("region") == "TW" and info.get("org_id") == team_id
                  and info.get("current_level") not in (None, "MLB") and info.get("current_level") in watchlist.LEVEL_ORDER]
        minors.sort(key=lambda i: (watchlist.LEVEL_ORDER[i["current_level"]], i.get("name_en") or ""))
        out.extend({"name": i["name_zh"], "level": i["current_level"]} for i in minors)
    return out


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
    mine = next(t for t in s["teams"] if isinstance(t, dict) and t.get("id") == team_id)
    other = next((t for t in s["teams"] if t is not mine and isinstance(t, dict)), {})
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
    """戰績摘要 {"w","l","pct","divRank","rd","pctRank","rdRank"}；standings 為 fetch_standings 的回傳（可為 None）。
    pctRank／rdRank 為全大聯盟排名（勝率高到低、得失分差大到小；並列採標準競賽排名 1,2,2,4；該隊無值則 None）。"""
    if not isinstance(standings, dict):
        return None

    def num(v):
        if isinstance(v, bool):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f == f and f not in (float("inf"), float("-inf")) else None

    def rank_of(value, values):
        if value is None:
            return None
        return 1 + sum(1 for v in values if v > value)

    teams = [t for div in standings.get("divisions") or [] for t in div.get("teams") or []]
    pcts = [p for p in (num(t.get("pct")) for t in teams) if p is not None]
    rds = [r for r in (num(t.get("run_diff")) for t in teams) if r is not None]
    for t in teams:
        if t.get("id") == team_id:
            pct = num(t.get("pct"))
            rd = num(t.get("run_diff"))
            return {"w": t["wins"], "l": t["losses"], "pct": pct, "divRank": t.get("rank"), "rd": t.get("run_diff"),
                    "pctRank": rank_of(pct, pcts), "rdRank": rank_of(rd, rds)}
    return None


# ---------------------------------------------------------------- 近期動態
_BOUNDARY_BEFORE = r"(?<![A-Za-z0-9])"
_BOUNDARY_AFTER = r"(?![A-Za-z0-9])"
_PREFIX_RE = re.compile(
    _BOUNDARY_BEFORE + "(?:" + "|".join(re.escape(p) for p in sorted(NON_MLB_TEAM_PREFIXES, key=len, reverse=True)) + r")[\s-]*$",
    re.IGNORECASE)


def _team_terms(team_id):
    """該隊新聞比對用的關鍵字，分三類 {"full", "nick", "zh"}：
    full＝英文城市＋暱稱全名（不分大小寫）；nick＝單獨暱稱與口語簡稱（分大小寫、整詞，且前面不得是日韓等球團名）；
    zh＝中文全名（子字串比對）。中文簡稱（巨人、老虎、光芒、國民…）歧義太多，不納入；
    譯文標題一定同時帶英文原文（比對文字含 title），所以只靠英文與中文全名不會漏掉真正的球隊新聞。"""
    full, nick, zh = [], [], []
    en = TEAM_NAMES_EN.get(team_id)
    if en:
        full_en, nick_en, short_zh = en
        (full if full_en != nick_en else nick).append(full_en)   # Athletics 沒有城市，全名等於暱稱
        nick.append(nick_en)
    nick += TEAM_SLANG_ALIASES.get(team_id, [])
    if team_id in TEAM_META:
        name_zh = TEAM_META[team_id]["name"]
        if not en or name_zh != en[2]:          # 中文全名即簡稱者（運動家）歧義大，只靠英文
            zh.append(name_zh)
    dedupe = lambda items: [t for t in dict.fromkeys(items) if t]  # noqa: E731
    return {"full": dedupe(full), "nick": dedupe(nick), "zh": dedupe(zh)}


def _word_regex(terms, flags=0):
    alts = [_BOUNDARY_BEFORE + re.escape(t) + _BOUNDARY_AFTER for t in sorted(terms, key=len, reverse=True)]
    return re.compile("|".join(alts), flags) if alts else None


def _term_matcher(terms):
    """回傳 matches(text) -> bool。規則見 _team_terms；暱稱命中時另檢查前面是否緊接非大聯盟球團名（Yomiuri Giants、LG Twins）。"""
    full_re = _word_regex(terms["full"], re.IGNORECASE)
    nick_re = _word_regex(terms["nick"])
    zh_re = re.compile("|".join(re.escape(t) for t in sorted(terms["zh"], key=len, reverse=True))) if terms["zh"] else None

    def matches(text):
        if full_re and full_re.search(text):
            return True
        if zh_re and zh_re.search(text):
            return True
        if nick_re:
            for m in nick_re.finditer(text):
                if not _PREFIX_RE.search(text[:m.start()]):
                    return True
        return False

    return matches


def _tx_player(tx):
    names = [p.get("name") for p in (tx.get("persons") or []) if isinstance(p, dict) and p.get("name")]
    return str(tx.get("person_name") or (names[0] if names else "") or "")


def _tx_player_id(tx):
    """球員 id（整數或 None）：優先 person_id，其次 persons[0].id。"""
    persons = [p for p in (tx.get("persons") or []) if isinstance(p, dict)]
    for cand in (tx.get("person_id"), persons[0].get("id") if persons else None):
        if isinstance(cand, int) and not isinstance(cand, bool):
            return cand
    return None


def _news_date(news, fallback):
    published = str(news.get("published") or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}", published):
        return published[:10]
    return fallback


def _is_http(url):
    return isinstance(url, str) and re.match(r"^https?://", url, re.IGNORECASE) is not None


def _in_window(day, start, end):
    """日期字串是否落在 [start, end]（date 物件，None 代表不設限）；無法解析的日期保留（它來自窗口內的快照）。"""
    try:
        d = date.fromisoformat(str(day))
    except ValueError:
        return True
    return (start is None or d >= start) and (end is None or d <= end)


def build_recent(team_id, snapshots, news_limit=TEAM_PAGE_NEWS_LIMIT, start=None, end=None):
    """某隊近期動態 {"moves", "news", "snapshotDays"}。snapshots 為 weekly.load_week_snapshots 的回傳。
    start／end（date）限定異動日期窗口（含兩端），異動本身的日期可能早於快照日，窗口外者不列。
    moves：依 weekly 的分類與方向判斷（加入 in／離開 out），新到舊；news：依隊名關鍵字比對標題與摘要，新到舊。"""
    items = weekly._collect_items(snapshots)
    moves = []
    for it in items:
        if not _in_window(it["date"], start, end):
            continue
        for tid, direction in weekly.team_moves(it):
            if tid != team_id:
                continue
            tx = it["tx"]
            moves.append({
                "date": it["date"],
                "dir": "in" if direction == "join" else "out",
                "player": _tx_player(tx),
                "playerId": _tx_player_id(tx),
                "type": weekly.CATEGORY_LABELS.get(it["category"], ""),
                "note": str(tx.get("description_zh") or tx.get("description") or ""),
            })

    matches = _term_matcher(_team_terms(team_id))
    seen, found = set(), []
    for key in sorted(snapshots, reverse=True):
        for n in snapshots[key]["news"]:
            link = n["link"].strip()
            if not _is_http(link) or link in seen:
                continue
            text = " ".join(str(n.get(k) or "") for k in ("title", "title_zh", "summary"))
            if not matches(text):
                continue
            seen.add(link)
            found.append({"ts": weekly._published_ts(n.get("published")), "date": _news_date(n, key),
                          "title": str(n.get("title_zh") or n.get("title") or ""), "url": link, "source": NEWS_SOURCE})
    found.sort(key=lambda x: -x["ts"])
    news = [{k: v for k, v in x.items() if k != "ts"} for x in found[:news_limit]]
    return {"moves": moves, "news": news, "snapshotDays": len(snapshots)}


# ---------------------------------------------------------------- 整合（供 rosters.fetch_rosters 呼叫）
def recent_window(today):
    """近期動態窗口 (起日, 終日)：終日為報表日（today 的前一天），往前共 TEAM_PAGE_RECENT_DAYS 天。"""
    end = today - timedelta(days=1)
    return end - timedelta(days=TEAM_PAGE_RECENT_DAYS - 1), end


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
    start = end = None
    try:
        start, end = recent_window(today)
        days = [end - timedelta(days=i) for i in range(TEAM_PAGE_RECENT_DAYS)]
        snapshots = weekly.load_week_snapshots(days, snapshot_dir)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: 球隊專頁每日快照讀取失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        snapshots = {}
    if wl is None:
        wl = watchlist.load_watchlist()

    def summary(team_id, players):
        out = {"record": None, "postseason": None, "tracked": []}   # 各欄位獨立降級，一個出錯不影響其他
        for key, fn in (
            ("record", lambda: build_record(standings, team_id)),
            ("postseason", lambda: postseason_result(playoffs_data, team_id) if playoffs_data else None),
            ("tracked", lambda: tracked_names(players, wl, team_id)),
        ):
            try:
                out[key] = fn()
            except Exception as e:  # noqa: BLE001
                print(f"Warning: 球隊 {team_id} 摘要的 {key} 欄位產生失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        return out

    def extras(team_id, players):
        out = {}
        for key, fn in (
            ("summary", lambda: summary(team_id, players)),
            ("recent", lambda: build_recent(team_id, snapshots, start=start, end=end)),
            ("leaders", lambda: compute_leaders(players)),
        ):
            try:
                out[key] = fn()
            except Exception as e:  # noqa: BLE001 - 單一區塊失敗只缺該區塊
                print(f"Warning: 球隊 {team_id} 的 {key} 區塊產生失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        return out

    return extras
