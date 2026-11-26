"""台日韓球員專區：追蹤名單、比對（異動／新聞／boxscore）、組成資料與渲染。

層級與 transactions／news 相同，只依賴 config、htmlutil、net、storage、timeutil；異動、新聞以 dict 形狀傳入，不 import 其他功能模組。"""

import json
import re
import sys
from datetime import date, timedelta

from . import htmlutil, net, storage, timeutil
from .config import DATE_KEY_RE, DYNAMIC_WATCHLIST_PATH, TEAM_META, TEAM_NAMES_EN, WATCHLIST_PATH

REGION_NAMES = {"TW": "台灣", "JP": "日本", "KR": "韓國"}
REGION_ORDER = {"TW": 0, "JP": 1, "KR": 2}
HEADING = "台日韓球員動態"
HOME_WINDOW_DAYS = 7


# ---------------------------------------------------------------- 動態名單抓取（MLB API）
# sportId → 層級；大聯盟收台日韓，小聯盟只收台灣
LEVELS = {1: "MLB", 11: "AAA", 12: "AA", 13: "A+", 14: "A", 16: "ROK"}
LEVEL_ORDER = {"MLB": 0, "AAA": 1, "AA": 2, "A+": 3, "A": 4, "ROK": 5}
MINOR_SPORT_IDS = (11, 12, 13, 14, 16)
BIRTH_COUNTRY_REGION = {
    "Taiwan": "TW", "Japan": "JP",
    "Republic of Korea": "KR", "South Korea": "KR", "Korea": "KR",
}
PLAYERS_URL = "https://statsapi.mlb.com/api/v1/sports/{sport_id}/players?season={season}"


TEAMS_URL = "https://statsapi.mlb.com/api/v1/teams?sportIds=11,12,13,14,16&season={season}"


def _parse_sport_players(data, sport_id):
    """解析單一 sportId 的 /players 回應 → [(id, name_en, region, level, current_team_id)]；小聯盟只收台灣。"""
    out = []
    people = data.get("people") if isinstance(data, dict) else None
    if not isinstance(people, list):
        return out
    for p in people:
        if not isinstance(p, dict):
            continue
        pid = htmlutil._int_id(p.get("id"))
        name = str(p.get("fullName") or "").strip()
        region = BIRTH_COUNTRY_REGION.get(str(p.get("birthCountry") or "").strip())
        if pid is None or not name or region is None:
            continue
        if sport_id != 1 and region != "TW":
            continue
        team = p.get("currentTeam")
        team_id = htmlutil._int_id(team.get("id")) if isinstance(team, dict) else None
        out.append((pid, name, region, LEVELS[sport_id], team_id))
    return out


def _parse_affiliates(data):
    """解析 /teams?sportIds=11,... 回應 → {附屬隊 id: (母隊 MLB id, 層級)}；格式不符回傳 {}。"""
    teams = data.get("teams") if isinstance(data, dict) else None
    out = {}
    if not isinstance(teams, list):
        return out
    for t in teams:
        if not isinstance(t, dict):
            continue
        tid = htmlutil._int_id(t.get("id"))
        org = htmlutil._int_id(t.get("parentOrgId"))
        sport = t.get("sport")
        level = LEVELS.get(htmlutil._int_id(sport.get("id")) if isinstance(sport, dict) else None)
        if tid is not None and org is not None and level is not None:
            out[tid] = (org, level)
    return out


def _org_and_level(team_id, affiliates):
    """currentTeam id → (母隊 MLB id, 當前層級)；本身是 30 隊之一為 (自己, "MLB")；對不到為 (None, None)。"""
    if team_id is None:
        return None, None
    if team_id in TEAM_META:
        return team_id, "MLB"
    return affiliates.get(team_id, (None, None))


def _previous_players(dynamic_path):
    """讀既有動態名單的球員清單；缺檔或損毀回傳 []。"""
    try:
        players = _read_json(dynamic_path)["players"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return [p for p in players if isinstance(p, dict)] if isinstance(players, list) else []


def watchlist_seasons(today, ctx, season_end=None):
    """決定動態名單要查的賽季。回傳 {"mlb": {"required": [...], "optional": [...]}, "minor": {...}}（皆為遞增整數 list）。
    必查賽季失敗會讓該層級走舊檔 fallback；選查賽季失敗只印訊息。
    大聯盟：必查 {ctx.season, ctx.rosterSeason}；當年季後賽結束後（today > season_end，取不到時以 11 月起推估）
      且 today.year == ctx.season（尚未跨年）時，選查 year+1（休賽季新簽球員要到開幕才出現在 season=year-1）。
    小聯盟：必查 ctx.season；ctx.rosterSeason 不同時選查（下一季的小聯盟資料休賽季不完整）。"""
    season, roster_season = ctx["season"], ctx["rosterSeason"]
    mlb_required = sorted({season, roster_season})
    after_season = (today > season_end) if season_end is not None else today.month >= 11
    mlb_optional = [season + 1] if after_season and today.year == season and season + 1 not in mlb_required else []
    minor_optional = [roster_season] if roster_season != season else []
    return {"mlb": {"required": mlb_required, "optional": mlb_optional},
            "minor": {"required": [season], "optional": minor_optional}}


def _normalize_seasons(seasons):
    """整數賽季（舊呼叫方式）視為大聯盟、小聯盟都只必查該賽季。"""
    if isinstance(seasons, int):
        return {"mlb": {"required": [seasons], "optional": []}, "minor": {"required": [seasons], "optional": []}}
    return seasons


def planned_seasons(seasons):
    """計畫查詢的所有賽季（遞增、去重），寫入 data/watchlist.json 的 season 欄位。"""
    seasons = _normalize_seasons(seasons)
    return sorted({y for grp in seasons.values() for key in ("required", "optional") for y in grp[key]})


def _rank(level, season):
    """去重排序鍵（越小越優先）：大聯盟名單中有的人一律最優先；其次較新賽季；同賽季才比層級高低。"""
    return (0 if level == "MLB" else 1, -season, LEVEL_ORDER[level])


def fetch_dynamic_players(seasons, dynamic_path=None):
    """抓取各層級球員並過濾台日韓，依 id 去重。
    seasons：watchlist_seasons 的結果，或單一整數賽季（兩者層級都只查該賽季）。
    每個 sportId 依序查各賽季（遞增）：必查賽季失敗（含 people 為空）→ 該層級列入 failed，沿用既有 data/watchlist.json
    中該層級的球員（新資料有同 id 者以新資料為準），已成功賽季的資料照樣併入；選查賽季失敗或為空只印訊息。
    大聯盟各賽季依 id 聯集；小聯盟同一人出現在多個賽季時以較新賽季的層級為準、同賽季才比層級高低；跨層級 MLB 優先。
    回傳 [{id,name_en,region,level,org_id,current_level}]（org_id 為母隊 MLB id、current_level 為 currentTeam 所在層級，
    對不到或 teams 端點失敗為 None）；全部 sportId 都失敗回傳 None。"""
    dynamic_path = dynamic_path or DYNAMIC_WATCHLIST_PATH
    seasons = _normalize_seasons(seasons)
    minor = seasons["minor"]
    minor_all = sorted(set(minor["required"]) | set(minor["optional"]))
    affiliates = {}
    for y in dict.fromkeys([minor_all[-1]] + list(minor["required"])):  # 最新賽季優先，空了退回必查賽季
        affiliates = _parse_affiliates(net.fetch_json(TEAMS_URL.format(season=y)))
        if affiliates:
            break
    if not affiliates:
        print("Warning: 附屬球隊對照抓取失敗，org_id／current_level 將為空。", file=sys.stderr)
    best = {}  # id → (rank, 球員 dict)
    failed = []
    ok = 0
    for sport_id in (1,) + MINOR_SPORT_IDS:
        grp = seasons["mlb"] if sport_id == 1 else minor
        required = set(grp["required"])
        sport_ok = False
        for season in sorted(required | set(grp["optional"])):
            data = net.fetch_json(PLAYERS_URL.format(sport_id=sport_id, season=season))
            if not isinstance(data, dict) or not isinstance(data.get("people"), list) or not data["people"]:
                if season in required:
                    print(f"Warning: sportId={sport_id}（{LEVELS[sport_id]}）{season} 賽季球員清單抓取失敗或為空，略過。", file=sys.stderr)
                    if LEVELS[sport_id] not in failed:
                        failed.append(LEVELS[sport_id])
                else:
                    print(f"Note: sportId={sport_id}（{LEVELS[sport_id]}）{season} 賽季（選查）無資料，略過。")
                continue
            sport_ok = True
            for pid, name, region, level, team_id in _parse_sport_players(data, sport_id):
                rank = _rank(level, season)
                old = best.get(pid)
                if old is None or rank < old[0]:
                    org_id, current_level = _org_and_level(team_id, affiliates)
                    best[pid] = (rank, {"id": pid, "name_en": name, "region": region, "level": level,
                                        "org_id": org_id, "current_level": current_level})
        if sport_ok:
            ok += 1
    if ok == 0:
        return None
    best = {pid: v[1] for pid, v in best.items()}
    if failed:
        carried = 0
        for p in _previous_players(dynamic_path):
            pid = htmlutil._int_id(p.get("id"))
            if pid is None or pid in best or p.get("level") not in failed \
                    or p.get("region") not in REGION_NAMES or not str(p.get("name_en") or "").strip():
                continue
            cl = p.get("current_level")
            best[pid] = {"id": pid, "name_en": str(p["name_en"]).strip(), "region": p["region"], "level": p["level"],
                         "org_id": htmlutil._int_id(p.get("org_id")), "current_level": cl if cl in LEVEL_ORDER else None}
            carried += 1
        print(f"{'、'.join(failed)} 層級必查賽季抓取失敗或為空，沿用上次名單中的 {carried} 位球員。")
    return list(best.values())


def _read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _settings_exclude_ids(data):
    raw = data.get("exclude_ids") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return set()
    return {i for i in (htmlutil._int_id(x) for x in raw) if i is not None}


def generate_watchlist(seasons, now=None, path=None, dynamic_path=None):
    """抓取並寫 data/watchlist.json。seasons 同 fetch_dynamic_players；檔內 season 欄位為查過的賽季清單（遞增整數 list）。
    全部抓取失敗回傳 False（不覆寫舊檔）。
    missing_zh：動態名單中 settings 沒有中文名對照（且未被排除）的球員。"""
    path = path or WATCHLIST_PATH
    dynamic_path = dynamic_path or DYNAMIC_WATCHLIST_PATH
    players = fetch_dynamic_players(seasons, dynamic_path)
    if players is None:
        print(f"Error: {planned_seasons(seasons)} 賽季的台日韓球員名單全部抓取失敗，保留既有檔案不覆寫。", file=sys.stderr)
        return False
    try:
        settings = _read_json(path)
    except (OSError, ValueError) as e:
        print(f"Warning: 無法讀取 {path}（{type(e).__name__}: {e}），缺中文名清單以全部球員計。", file=sys.stderr)
        settings = {}
    raw_players = settings.get("players") if isinstance(settings, dict) else None
    known = {htmlutil._int_id(p.get("id")) for p in raw_players if isinstance(p, dict)} \
        if isinstance(raw_players, list) else set()
    excluded = _settings_exclude_ids(settings)
    players.sort(key=lambda p: (REGION_ORDER[p["region"]], LEVEL_ORDER[p["level"]], p["name_en"]))
    missing = [p for p in players if p["id"] not in known and p["id"] not in excluded]
    now = now or timeutil.get_taipei_now()
    data = {"updatedAt": now.isoformat(timespec="seconds"), "season": planned_seasons(seasons), "players": players, "missing_zh": missing}
    storage.write_json_atomic(dynamic_path, data)
    counts = {r: sum(1 for p in players if p["region"] == r) for r in REGION_NAMES}
    print(f"Successfully wrote {dynamic_path}：台灣 {counts['TW']}、日本 {counts['JP']}、韓國 {counts['KR']} 位。")
    if missing:
        print(f"缺中文名（{len(missing)} 位，請補進 {path}）：")
        for p in missing:
            print(f"  {p['id']}  {p['name_en']}  {p['level']}  {p['region']}")
    return True


# ---------------------------------------------------------------- 名單讀取
def _clean_player(p):
    """驗證並正規化一筆 settings 球員；格式錯誤回傳 None。"""
    if not isinstance(p, dict):
        return None
    pid = htmlutil._int_id(p.get("id"))
    name_zh, name_en, region = p.get("name_zh"), p.get("name_en"), p.get("region")
    if pid is None or not isinstance(name_zh, str) or not isinstance(name_en, str) \
            or not name_zh.strip() or not name_en.strip() or region not in REGION_NAMES:
        print(f"Warning: 追蹤名單中略過格式錯誤的項目：{str(p)[:80]}", file=sys.stderr)
        return None
    aliases = p.get("aliases") or []
    aliases = [a.strip() for a in aliases if isinstance(a, str) and a.strip()] if isinstance(aliases, list) else []
    return {"id": pid, "name_zh": name_zh.strip(), "name_en": name_en.strip(), "region": region, "aliases": aliases}


def _load_dynamic(dynamic_path):
    """讀動態快取 → [{id,name_en,region,level}]；缺檔或損毀印警告並回傳 []。"""
    try:
        data = _read_json(dynamic_path)
        players = data["players"]
        if not isinstance(players, list):
            raise ValueError("players 不是陣列")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"Warning: 無法讀取動態追蹤名單 {dynamic_path}（{type(e).__name__}: {e}），僅使用 settings 名單。", file=sys.stderr)
        return []
    out = []
    for p in players:
        if not isinstance(p, dict):
            continue
        pid = htmlutil._int_id(p.get("id"))
        name_en, region = p.get("name_en"), p.get("region")
        if pid is None or not isinstance(name_en, str) or not name_en.strip() or region not in REGION_NAMES:
            continue
        level = p.get("level") if p.get("level") in LEVEL_ORDER else None
        org_id = htmlutil._int_id(p.get("org_id"))
        current_level = p.get("current_level") if p.get("current_level") in LEVEL_ORDER else None
        out.append({"id": pid, "name_en": name_en.strip(), "region": region, "level": level,
                    "org_id": org_id, "current_level": current_level})
    return out


def load_watchlist(path=None, dynamic_path=None):
    """讀取追蹤名單，回傳 {person id: 球員 dict}。

    合併 data/watchlist.json（MLB API 動態名單）與 settings/watchlist.json（中文名、aliases、region 覆寫，
    以及動態名單沒有的球員），再移除 exclude_ids。球員 dict：id、name_zh（無中文名時為 name_en）、name_en、
    region、aliases、level（可為 None）、org_id、current_level（可為 None）、has_zh。排序：地區 → settings 內球員（依檔案順序）→ 其餘依層級、英文名。
    path 預設為 WATCHLIST_PATH、dynamic_path 預設為 DYNAMIC_WATCHLIST_PATH（皆呼叫時才取值）；
    明確傳 path 而未傳 dynamic_path 時不讀動態快取（只用該 settings 檔）。
    settings 讀檔失敗、JSON 錯誤或格式不符：印警告並回傳空 dict；動態快取缺失或損毀：印警告、只用 settings。"""
    use_dynamic = path is None or dynamic_path is not None
    path = path or WATCHLIST_PATH
    try:
        data = _read_json(path)
        players = data["players"]
        if not isinstance(players, list):
            raise ValueError("players 不是陣列")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"Warning: 無法讀取追蹤名單 {path}（{type(e).__name__}: {e}），台日韓球員專區不顯示。", file=sys.stderr)
        return {}
    excluded = _settings_exclude_ids(data)
    merged = {}  # id → (settings 序, 球員 dict)
    for i, p in enumerate(players):
        cp = _clean_player(p)
        if cp is not None and cp["id"] not in merged:
            cp.update(level=None, org_id=None, current_level=None, has_zh=True)
            merged[cp["id"]] = (i, cp)
    if use_dynamic:
        for d in _load_dynamic(dynamic_path or DYNAMIC_WATCHLIST_PATH):
            if d["id"] in merged:
                merged[d["id"]][1].update(level=d["level"], org_id=d["org_id"], current_level=d["current_level"])
            else:
                merged[d["id"]] = (len(players), {
                    "id": d["id"], "name_zh": d["name_en"], "name_en": d["name_en"], "region": d["region"],
                    "aliases": [], "level": d["level"], "org_id": d["org_id"],
                    "current_level": d["current_level"], "has_zh": False})
    items = [v for pid, v in merged.items() if pid not in excluded]
    items.sort(key=lambda v: (
        REGION_ORDER[v[1]["region"]], v[0],
        LEVEL_ORDER.get(v[1]["level"], len(LEVEL_ORDER)), v[1]["name_en"]))
    return {v[1]["id"]: v[1] for v in items}


# ---------------------------------------------------------------- 比對（含週報介面）
def players_in_transaction(tx, wl):
    """該筆異動涉及的名單球員 id 清單（比對 person_id 與 persons，依出現順序、不重複）。"""
    ids = []
    for p in (tx.get("persons") or []):
        ids.append(htmlutil._int_id((p or {}).get("id")))
    ids.append(htmlutil._int_id(tx.get("person_id")))
    return [i for i in dict.fromkeys(ids) if i is not None and i in wl]


def _term_pattern(wl_player):
    terms = [wl_player["name_en"]] + list(wl_player.get("aliases") or [])
    alts = "|".join(re.escape(t) for t in sorted(set(terms), key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + alts + r")(?!\w)", re.IGNORECASE)


def players_in_text(text, wl):
    """文字中提到的名單球員 id（不分大小寫、整詞比對 name_en 與 aliases；順序同名單）。"""
    text = str(text or "")
    if not text:
        return []
    return [pid for pid, p in wl.items() if _term_pattern(p).search(text)]


def match_transactions(raw_tx, wl):
    """從未經大聯盟過濾的 transactions 陣列挑出名單球員的異動（含下放、指派小聯盟等）。

    API 多人交易會各人一筆、描述相同：依描述合併，persons 為該描述所有球員，任一人在名單即入選。
    回傳與 filter_major_league_transactions 相同形狀的 dict list（可沿用翻譯與 render_tx_description）。"""
    if not wl:
        return []
    groups = {}
    for tx in raw_tx or []:
        if isinstance(tx, dict) and tx.get("description"):
            groups.setdefault(tx["description"], []).append(tx)
    out = []
    for desc, txs in groups.items():
        persons = []
        for tx in txs:
            person = tx.get("person") or {}
            pid, name = person.get("id"), person.get("fullName")
            if pid is not None and name and all(p["id"] != pid for p in persons):
                persons.append({"id": pid, "name": name})
        # 同一筆交易的各紀錄 to/from 可能方向相反（如現金那一方、無 person）：
        # 優先取 person 為名單球員的紀錄，其次取有 person 的紀錄，最後才退回任一筆
        def _has_person(t):
            return isinstance(t.get("person"), dict) and t["person"].get("id") is not None

        ordered = (
            [t for t in txs if _has_person(t) and htmlutil._int_id(t["person"]["id"]) in wl]
            + [t for t in txs if _has_person(t)]
            + txs
        )
        entry = {
            "to_team_id": next((t["toTeam"]["id"] for t in ordered if isinstance(t.get("toTeam"), dict) and "id" in t["toTeam"]), None),
            "from_team_id": next((t["fromTeam"]["id"] for t in ordered if isinstance(t.get("fromTeam"), dict) and "id" in t["fromTeam"]), None),
            "person_id": persons[0]["id"] if persons else None,
            "person_name": persons[0]["name"] if persons else None,
            "persons": persons,
            "description": desc,
            "date": txs[0].get("date"),
            "type_code": txs[0].get("typeCode"),
        }
        if players_in_transaction(entry, wl):
            out.append(entry)
    return out


def match_news(news, wl):
    """比對新聞標題與 RSS 摘要（summary），回傳 [(新聞, [名單球員 id])]（保持新聞順序）。"""
    out = []
    if not wl:
        return out
    for n in news or []:
        ids = players_in_text(f"{n.get('title', '')} {n.get('summary', '')}", wl)
        if ids:
            out.append((n, ids))
    return out


# ---------------------------------------------------------------- boxscore 成績列
def _num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _join(parts):
    return "、".join(parts)


def format_batting(b):
    """打擊列文字；沒有打席（plateAppearances 與 atBats 皆 0）回傳 None。0 的項目省略（打數、安打一定顯示）。"""
    if not isinstance(b, dict) or not (_num(b.get("plateAppearances")) > 0 or _num(b.get("atBats")) > 0):
        return None
    parts = [f"{_num(b.get('atBats'))} 打數 {_num(b.get('hits'))} 安打"]
    for key, label in (("doubles", "二壘安打"), ("triples", "三壘安打"), ("homeRuns", "全壘打"),
                       ("rbi", "打點"), ("stolenBases", "盜壘"), ("baseOnBalls", "保送")):
        n = _num(b.get(key))
        if n > 0:
            parts.append(f"{n} {label}")
    return _join(parts)


def _has_innings(ip):
    ip = str(ip or "").strip()
    try:
        return float(ip) > 0
    except ValueError:
        return False


def format_pitching(p):
    """投球列文字；沒有投球局數、面對打者數與投球數時回傳 None（0 局失分仍顯示）。0 的項目省略（局數一定顯示）。"""
    if not isinstance(p, dict) or not (
            _has_innings(p.get("inningsPitched")) or _num(p.get("battersFaced")) > 0 or _num(p.get("numberOfPitches")) > 0):
        return None
    ip = str(p.get("inningsPitched") or "").strip() or "0.0"
    parts = [f"{ip} 局"]
    for key, label in (("hits", "安打"), ("runs", "失分"), ("strikeOuts", "三振")):
        n = _num(p.get(key))
        if n > 0:
            parts.append(f"{n} {label}")
    return _join(parts)


def extract_game_lines(boxscores, wl):
    """依 ID{personId} 在當日各場 boxscore 找名單球員，回傳 [{"player_id", "team_id", "batting", "pitching"}]。
    打擊列需有打席、投球列需有投球局數，兩者皆無（只守備或代跑）的球員不列。"""
    lines = []
    if not wl:
        return lines
    for box in boxscores or []:
        teams = (box or {}).get("teams") if isinstance(box, dict) else None
        if not isinstance(teams, dict):
            continue
        for side in ("away", "home"):
            side_obj = teams.get(side)
            if not isinstance(side_obj, dict):
                continue
            team_id = htmlutil._int_id((side_obj.get("team") or {}).get("id")) if isinstance(side_obj.get("team"), dict) else None
            players = side_obj.get("players")
            if not isinstance(players, dict):
                continue
            for pid in wl:
                info = players.get(f"ID{pid}")
                if not isinstance(info, dict):
                    continue
                stats = info.get("stats") if isinstance(info.get("stats"), dict) else {}
                batting = format_batting(stats.get("batting"))
                pitching = format_pitching(stats.get("pitching"))
                if batting or pitching:
                    lines.append({"player_id": pid, "team_id": team_id, "batting": batting, "pitching": pitching})
    return lines


# ---------------------------------------------------------------- 台灣球員小聯盟比賽成績（gameLog）
MINOR_LEVEL_LABELS = {"AAA": "3A", "AA": "2A", "A+": "高階1A", "A": "1A", "ROK": "新人聯盟"}
MINOR_SPORT_LEVELS = {11: "AAA", 12: "AA", 13: "A+", 14: "A", 16: "ROK"}
MINOR_GAMELOG_URL = ("https://statsapi.mlb.com/api/v1/people?personIds={ids}"
                     "&hydrate=stats(group=[hitting,pitching],type=[gameLog],season={season},sportId={sport_id})")


def _parse_minor_gamelog(data, date_str, level):
    """解析單一 sportId 的 gameLog 回應 → 當日比賽紀錄 list（同球員同 gamePk 的打擊／投球合併）。"""
    games = {}  # (player_id, gamePk 或流水號) → 紀錄（保留首次出現順序）
    people = data.get("people") if isinstance(data, dict) else None
    if not isinstance(people, list):
        return []
    for p in people:
        pid = htmlutil._int_id(p.get("id")) if isinstance(p, dict) else None
        if pid is None or not isinstance(p.get("stats"), list):
            continue
        for st in p["stats"]:
            if not isinstance(st, dict) or not isinstance(st.get("splits"), list):
                continue
            group = str((st.get("group") or {}).get("displayName") or "").lower() if isinstance(st.get("group"), dict) else ""
            if group not in ("hitting", "pitching"):
                continue
            for sp in st["splits"]:
                if not isinstance(sp, dict) or sp.get("date") != date_str or not isinstance(sp.get("stat"), dict):
                    continue
                game = sp.get("game") if isinstance(sp.get("game"), dict) else {}
                pk = htmlutil._int_id(game.get("gamePk"))
                key = (pid, pk if pk is not None else f"n{len(games)}")
                rec = games.setdefault(key, {
                    "player_id": pid, "team_id": None, "batting": None, "pitching": None, "level": level,
                    "team_name": str((sp.get("team") or {}).get("name") or "") if isinstance(sp.get("team"), dict) else "",
                    "opponent": str((sp.get("opponent") or {}).get("name") or "") if isinstance(sp.get("opponent"), dict) else "",
                    "is_home": sp.get("isHome") if isinstance(sp.get("isHome"), bool) else None,
                    "game_pk": pk})
                text = format_batting(sp["stat"]) if group == "hitting" else format_pitching(sp["stat"])
                if text:
                    rec[group == "hitting" and "batting" or "pitching"] = text
    return [r for r in games.values() if r["batting"] or r["pitching"]]


def fetch_minor_game_lines(date_str, wl):
    """名單中台灣球員（不論層級）在 date_str 的小聯盟比賽成績；日韓不查。
    逐一 sportId 查詢（11/12/13/14/16），單一 sportId 失敗只警告略過；沒有台灣球員不打 API。
    回傳 [{player_id, team_id(None), batting, pitching, level, team_name, opponent, is_home, game_pk}]。"""
    ids = [pid for pid, p in (wl or {}).items() if p.get("region") == "TW"]
    if not ids or not DATE_KEY_RE.match(str(date_str)):
        return []
    id_text = ",".join(str(i) for i in ids)
    lines = []
    for sport_id, level in MINOR_SPORT_LEVELS.items():
        data = net.fetch_json(MINOR_GAMELOG_URL.format(ids=id_text, season=str(date_str)[:4], sport_id=sport_id))
        if not isinstance(data, dict) or not isinstance(data.get("people"), list):
            print(f"Warning: 台灣球員 {level} 比賽成績抓取失敗，略過。", file=sys.stderr)
            continue
        lines.extend(_parse_minor_gamelog(data, date_str, level))
    return lines


# ---------------------------------------------------------------- 組成可序列化的資料
def build_spotlight(date_str, wl, tx_matches=(), news_matches=(), game_lines=()):
    """組成 metadata `spotlight` 清單（純文字，渲染時才 escape）。呼叫時機須在翻譯之後（優先取譯文）。

    - transaction：{"player_id", "kind", "text", "date", "persons", "to_team_id", "from_team_id", "also_player_ids"?}，
      每筆異動只存一次，歸第一位名單球員；其餘名單球員在 also_player_ids，渲染時顯示「（同上則異動）」。
    - news：{"player_id", "kind", "text", "link", "date", "also_player_ids"?}，一則新聞只列一次，歸第一位名單球員。
    - game：{"player_id", "kind", "batting", "pitching", "team_id", "date"}；小聯盟另有 level、team_name、opponent、is_home。"""
    entries = []
    for tx in tx_matches:
        ids = players_in_transaction(tx, wl)
        if not ids:
            continue
        entry = {
            "player_id": ids[0], "kind": "transaction",
            "text": tx.get("description_zh") or tx["description"],
            "date": tx.get("date") or date_str,
            "persons": [{"id": p["id"], "name": p["name"]} for p in tx.get("persons") or []],
            "to_team_id": tx.get("to_team_id"), "from_team_id": tx.get("from_team_id"),
        }
        if len(ids) > 1:
            entry["also_player_ids"] = list(ids[1:])
        entries.append(entry)
    for n, ids in news_matches:
        entry = {"player_id": ids[0], "kind": "news", "text": n.get("title_zh") or n["title"],
                 "link": n["link"], "date": date_str}
        if len(ids) > 1:
            entry["also_player_ids"] = list(ids[1:])
        entries.append(entry)
    for g in game_lines:
        entry = {"player_id": g["player_id"], "kind": "game", "batting": g.get("batting"),
                 "pitching": g.get("pitching"), "team_id": g.get("team_id"), "date": date_str}
        if g.get("level"):  # 小聯盟比賽：另存層級、所屬隊與對手
            entry.update(level=g["level"], team_name=g.get("team_name") or "", opponent=g.get("opponent") or "",
                         is_home=g.get("is_home"))
        entries.append(entry)
    return entries


# ---------------------------------------------------------------- 渲染
_KIND_ORDER = {"transaction": 0, "tx_ref": 0, "news": 1, "news_ref": 1, "game": 2}


def _group_by_player(entries, wl):
    """依球員分組並排序（台灣 → 日本 → 韓國，再依名單順序）；回傳 [(球員 dict, [entry])]。名單外或格式錯誤者略過。"""
    grouped = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        pid = htmlutil._int_id(e.get("player_id"))
        if pid in wl:
            grouped.setdefault(pid, []).append(e)
        ref_kind = {"news": "news_ref", "transaction": "tx_ref"}.get(e.get("kind"))
        if ref_kind and isinstance(e.get("also_player_ids"), list):
            for other in e["also_player_ids"]:
                oid = htmlutil._int_id(other)
                if oid in wl and oid != pid:  # 其餘球員只放「同上」參照，不重複標題與連結
                    grouped.setdefault(oid, []).append({
                        "kind": ref_kind, "date": e.get("date"),
                        "to_team_id": e.get("to_team_id"), "from_team_id": e.get("from_team_id")})
    order = {pid: i for i, pid in enumerate(wl)}
    pids = sorted(grouped, key=lambda pid: (REGION_ORDER[wl[pid]["region"]], order[pid]))
    return [(wl[pid], grouped[pid]) for pid in pids]


def _team_name(team_id):
    tid = htmlutil._int_id(team_id)
    if tid in TEAM_NAMES_EN:
        return TEAM_NAMES_EN[tid][2]
    if tid in TEAM_META:
        return TEAM_META[tid]["name"]
    return None


def _player_team_id(entries):
    """分組標題的球隊 id：先取最新一筆 boxscore 比賽紀錄的 team_id；沒有則由最新一筆異動推算
    （優先 to_team_id，其次 from_team_id，只取 30 隊之一）；都沒有回傳 None（只顯示地區）。"""
    best = None
    for e in sorted((e for e in entries if e.get("kind") == "game"), key=lambda e: str(e.get("date") or "")):
        if htmlutil._int_id(e.get("team_id")) in TEAM_META:
            best = e["team_id"]
    if best is not None:
        return best
    for e in sorted((e for e in entries if e.get("kind") in ("transaction", "tx_ref")),
                    key=lambda e: str(e.get("date") or ""), reverse=True):
        for key in ("to_team_id", "from_team_id"):
            if htmlutil._int_id(e.get(key)) in TEAM_META:
                return e[key]
    return None


def _minor_prefix(e):
    """小聯盟比賽項目的前綴（層級標籤與球隊對手）；大聯盟項目回傳空字串。"""
    level = e.get("level")
    if level not in MINOR_LEVEL_LABELS:
        return ""
    label = htmlutil.esc(MINOR_LEVEL_LABELS[level])
    team, opp = str(e.get("team_name") or ""), str(e.get("opponent") or "")
    detail = ""
    if team and opp:
        detail = f"：{htmlutil.esc(team)} 對 {htmlutil.esc(opp)}"
    elif team:
        detail = f"：{htmlutil.esc(team)}"
    return f'<span class="spot-meta">{label}{detail}</span> '


def _render_item(e, show_date=False):
    """單一項目 <li>；所有外部字串經 esc。show_date 時（首頁）前置 M/D。"""
    kind = e.get("kind")
    date_html = ""
    if show_date:
        d = str(e.get("date") or "")
        if DATE_KEY_RE.match(d):
            date_html = f'<time class="spot-date num" datetime="{htmlutil.esc(d)}">{int(d[5:7])}/{int(d[8:10])}</time>'
    if kind == "transaction":
        body = "人事異動：" + _tx_html(e)
    elif kind == "news":
        link = str(e.get("link") or "")
        title = htmlutil.esc(e.get("text", ""))
        if link.startswith("https://www.mlb.com/"):
            body = f'新聞：<a href="{htmlutil.esc(link)}" target="_blank" rel="noopener">{title}</a>'
        else:
            body = f"新聞：{title}"
    elif kind == "tx_ref":
        body = "人事異動：（同上則異動）"
    elif kind == "news_ref":
        body = "新聞：（同上則新聞）"
    elif kind == "game":
        rows = []
        if e.get("batting"):
            rows.append("打擊 " + htmlutil.esc(e["batting"]))
        if e.get("pitching"):
            rows.append("投球 " + htmlutil.esc(e["pitching"]))
        if not rows:
            return ""
        prefix = _minor_prefix(e)
        return "".join(f"<li>{date_html}{prefix}{r}</li>" for r in rows)
    else:
        return ""
    return f"<li>{date_html}{body}</li>"


def _tx_html(e):
    """異動描述：以球員名切分各段 escape，名單內有 id 的名字加 Savant 連結（與 transactions.render_tx_description 相同做法）。"""
    text = str(e.get("text", ""))
    by_name = {}
    for p in e.get("persons") or []:
        if isinstance(p, dict) and p.get("name") and htmlutil._int_id(p.get("id")) is not None and p["name"] in text:
            by_name.setdefault(p["name"], p["id"])
    if not by_name:
        return htmlutil.esc(text)
    pattern = "(" + "|".join(re.escape(n) for n in sorted(by_name, key=len, reverse=True)) + ")"
    out = []
    for i, part in enumerate(re.split(pattern, text)):
        if i % 2 == 1:
            out.append(htmlutil.player_link(by_name[part], part))
        elif part:
            out.append(htmlutil.esc(part))
    return "".join(out)


def _render_groups(entries, wl, show_date):
    groups = []
    for player, items in _group_by_player(entries, wl):
        items = sorted(items, key=lambda e: (-_date_key(e) if show_date else 0, _KIND_ORDER.get(e.get("kind"), 9)))
        lis = "".join(_render_item(e, show_date) for e in items)
        if not lis:
            continue
        team = _team_name(_player_team_id(items))
        meta = REGION_NAMES[player["region"]] + (f"・{team}" if team else "")
        groups.append(
            '<div class="spot-group">'
            f'<h3>{htmlutil.player_link(player["id"], player["name_zh"])}　<span class="spot-meta">{htmlutil.esc(meta)}</span></h3>'
            f'<ul class="tx-list">{lis}</ul></div>'
        )
    return groups


def _date_key(e):
    """日期排序鍵（YYYYMMDD 整數，越大越新）；格式錯誤為 0。"""
    d = str(e.get("date") or "")
    return int(d.replace("-", "")) if DATE_KEY_RE.match(d) else 0


def render_spotlight_section(spotlight, wl):
    """戰報區塊 HTML；名單為空或沒有可顯示內容時回傳空字串（整區隱藏）。"""
    if not wl or not spotlight:
        return ""
    groups = _render_groups(spotlight, wl, show_date=False)
    if not groups:
        return ""
    return (
        '<section class="section wrap" aria-labelledby="spot-h">'
        f'<div class="section-head"><h2 id="spot-h">{HEADING}</h2><span class="count">{len(groups)} 位球員</span></div>'
        '<div class="spot-grid">' + "".join(groups) + "</div></section>"
    )


def home_window_entries(metadata, days=HOME_WINDOW_DAYS):
    """從 metadata 取「最新戰報日期」往回共 days 天（含最新日）的 spotlight 條目，每筆附上該期日期（metadata 鍵）。
    metadata 無有效日期回傳 None。"""
    dates = sorted((d for d, v in (metadata or {}).items() if DATE_KEY_RE.match(str(d)) and isinstance(v, dict)),
                   reverse=True)
    if not dates:
        return None
    latest = _parse(dates[0])
    start = latest - timedelta(days=days - 1)
    entries = []
    for d in dates:
        dt = _parse(d)
        if dt is None or dt < start or dt > latest:
            continue
        spot = metadata[d].get("spotlight")
        if not isinstance(spot, list):
            continue
        entries.extend({**e, "date": d} for e in spot if isinstance(e, dict))
    return entries


def _parse(date_str):
    try:
        return date.fromisoformat(str(date_str))
    except ValueError:
        return None


def render_spotlight_home(entries, wl):
    """首頁區塊 HTML。entries 為 home_window_entries 的結果（None 代表沒有任何戰報，不顯示）。
    名單為空、近 7 天無條目時整區隱藏（回傳空字串）。"""
    if not wl or entries is None:
        return ""
    groups = _render_groups(entries, wl, show_date=True)
    if not groups:
        return ""
    return (
        '  <section class="section" aria-labelledby="spot-h">\n'
        f'    <div class="section-head"><h2 id="spot-h">{HEADING}</h2><span class="count">近 {HOME_WINDOW_DAYS} 天 · {len(groups)} 位球員</span></div>\n'
        '    <div class="spot-grid">' + "".join(groups) + "</div>\n  </section>"
    )
