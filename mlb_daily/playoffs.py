"""季後賽對戰樹：抓取、整理、寫檔與渲染（對戰樹頁 playoffs.html 與首頁小卡）。

層級與 leaders 同層：只依賴 config、timeutil、net、htmlutil、storage，不 import rosters／standings／
index_page／cli。首頁小卡以 HTML 字串交給 index_page。"""

import os
import re
import sys

from . import htmlutil, net, storage, timeutil
from .config import (
    PLAYOFFS_PAGE_PATH,
    PLAYOFFS_PATH,
    PLAYOFFS_SCHEMA,
    POSTSEASON_SERIES_URL,
    TEAM_META,
    TEAM_NAMES_EN,
)

STATUS_DONE = "完成"
STATUS_LIVE = "進行中"
STATE_NOT_STARTED = "未開打"
STATE_LIVE = "進行中"
STATE_DONE = "已結束"
PENDING = "待定"
BYE_LABEL = "首輪輪空"

LEAGUE_NAMES = {"AL": "美聯", "NL": "國聯"}
ROUND_LABELS = {"F": "外卡", "D": "分區", "L": "冠軍賽", "W": "世界大賽"}
# 手機版輪次標題（由後往前）
STACK_ROUNDS = (("W", "世界大賽"), ("L", "聯盟冠軍賽"), ("D", "分區系列賽"), ("F", "外卡系列賽"))
DEFAULT_WINS_NEEDED = {"F": 2, "D": 3, "L": 4, "W": 4}
SERIES_ID_RE = re.compile(r"^([FDLW])_(\d+)$")
LETTER_RE = re.compile(r"'([A-Z])'")


# ---------------------------------------------------------------- 解析
def _is_known_team(team_id):
    """config 的球隊對照查得到才算真實隊伍（查不到的 id 視為待定）。"""
    tid = htmlutil._int_id(team_id)
    return tid is not None and (tid in TEAM_NAMES_EN or tid in TEAM_META)


def _team_name(team_id):
    """中文簡稱（沿用 config.TEAM_NAMES_EN 的中文簡稱，其次 TEAM_META 全名）；查不到回傳 None。"""
    tid = htmlutil._int_id(team_id)
    if tid in TEAM_NAMES_EN:
        return TEAM_NAMES_EN[tid][2]
    meta = TEAM_META.get(tid)
    return meta["name"] if meta else None


def _games_of(series):
    return [g for g in (series.get("games") if isinstance(series, dict) else None) or [] if isinstance(g, dict)]


def _status_of(game):
    status = game.get("status")
    return status if isinstance(status, dict) else {}


def _has_winner_flag(game):
    return any(isinstance(side.get("isWinner"), bool) for _tid, side in _game_teams(game))


def _is_final(game):
    """已完賽：codedGameState 為 "F"；"O"（Game Over）只在 isWinner 已有布林值時才算。
    延賽（"D"）的那一筆 abstractGameState 仍是 Final 且沒有比分，一律排除。"""
    code = _status_of(game).get("codedGameState")
    return code == "F" or (code == "O" and _has_winner_flag(game))


def _is_live(game):
    return _status_of(game).get("abstractGameState") == "Live"


def has_started(raw):
    """原始回應中是否至少有一場完賽或進行中的比賽。"""
    if not isinstance(raw, dict):
        return False
    return any(_is_final(g) or _is_live(g) for s in raw.get("series") or [] for g in _games_of(s))


def _game_teams(game):
    """[(team_id, side_dict)]，team id 不是整數者略過。"""
    out = []
    teams = game.get("teams") if isinstance(game.get("teams"), dict) else {}
    for side in ("away", "home"):
        t = teams.get(side) if isinstance(teams.get(side), dict) else {}
        team = t.get("team") if isinstance(t.get("team"), dict) else {}
        tid = htmlutil._int_id(team.get("id"))
        if tid is not None:
            out.append((tid, t))
    return out


def _league_of(games, series_desc):
    """AL／NL；世界大賽回傳 None。先看 seriesDescription 前綴，再退回球隊所屬聯盟。"""
    if series_desc.startswith("AL "):
        return "AL"
    if series_desc.startswith("NL "):
        return "NL"
    for game in games:
        for tid, _side in _game_teams(game):
            meta = TEAM_META.get(tid)
            if meta:
                return meta["league"]
    return None


def _label(rnd, league, letter):
    if rnd == "W":
        return ROUND_LABELS["W"]
    return f"{LEAGUE_NAMES.get(league, '')}{ROUND_LABELS[rnd]}" + (f" {letter}" if letter else "")


def _parse_series(sid, rnd, games):
    """單一系列賽 -> dict；沒有任何可辨識的資料時回傳 (None, None)。回傳 (league, series)。"""
    first = games[0] if games else {}
    series_desc = str(first.get("seriesDescription") or "")
    league = None if rnd == "W" else _league_of(games, series_desc)
    letter_m = next((LETTER_RE.search(str(g.get("description") or "")) for g in games
                     if LETTER_RE.search(str(g.get("description") or ""))), None)
    letter = letter_m.group(1) if letter_m else None
    needed = max((n // 2 + 1 for n in (htmlutil._int_id(g.get("gamesInSeries")) for g in games) if n), default=0) \
        or DEFAULT_WINS_NEEDED[rnd]

    order = []  # 隊伍出現順序（先客後主、依場次）
    for game in games:
        for tid, _side in _game_teams(game):
            if tid not in order:
                order.append(tid)
    wins = {tid: 0 for tid in order}
    seen = set()
    live = False
    for game in games:
        if _is_live(game):
            live = True
        pk = game.get("gamePk")
        if not _is_final(game) or pk in seen:
            continue
        seen.add(pk)
        for tid, side in _game_teams(game):
            if side.get("isWinner") is True:
                wins[tid] += 1
    teams = [{"id": tid, "name": _team_name(tid), "wins": wins[tid]} for tid in order[:2]]
    while len(teams) < 2:
        teams.append({"id": None, "name": None, "wins": 0})
    winner = next((t["id"] for t in teams if t["id"] is not None and t["wins"] >= needed), None)
    if winner is not None:
        state = STATE_DONE
    elif live or any(t["wins"] for t in teams):
        state = STATE_LIVE
    else:
        state = STATE_NOT_STARTED
    return league, {
        "id": sid,
        "label": _label(rnd, league, letter),
        "teams": teams,
        "winsNeeded": needed,
        "state": state,
        "winnerId": winner,
    }


def parse_postseason(raw):
    """原始回應 -> 結構化資料（純函式）。
    回傳 {"status", "champion", "rounds": {"AL": {"F","D","L"}, "NL": {...}, "WS": [...]}}。
    外卡系列賽附 feedsInto（接到哪一組分區系列賽，以隊伍 ID 比對，不用字母或編號）；
    分區系列賽附 byeIds（首輪輪空的隊伍：出現在分區、但不在同聯盟任何外卡系列賽）。"""
    rounds = {"AL": {"F": [], "D": [], "L": []}, "NL": {"F": [], "D": [], "L": []}, "WS": []}
    for entry in (raw.get("series") if isinstance(raw, dict) else None) or []:
        if not isinstance(entry, dict):
            continue
        info = entry.get("series") if isinstance(entry.get("series"), dict) else {}
        m = SERIES_ID_RE.match(str(info.get("id") or ""))
        games = _games_of(entry)
        if not m or not games:
            continue
        rnd, num = m.group(1), int(m.group(2))
        league, series = _parse_series(info["id"], rnd, games)
        if rnd == "W":
            rounds["WS"].append((num, series))
        elif league in rounds:
            rounds[league][rnd].append((num, series))
    for league in ("AL", "NL"):
        for rnd in "FDL":
            rounds[league][rnd] = [s for _n, s in sorted(rounds[league][rnd], key=lambda p: p[0])]
        _link_wild_card(rounds[league])
    rounds["WS"] = [s for _n, s in sorted(rounds["WS"], key=lambda p: p[0])]

    champion = None
    for s in rounds["WS"]:
        if s["winnerId"] is not None:
            champion = {"id": s["winnerId"], "name": _team_name(s["winnerId"])}
    return {"status": STATUS_DONE if champion else STATUS_LIVE, "champion": champion, "rounds": rounds}


def _team_ids(series):
    """系列賽中的真實隊伍 id（對照表查不到的待定隊伍不參與外卡／分區對應與輪空判斷）。"""
    return {t["id"] for t in series["teams"] if _is_known_team(t["id"])}


def _link_wild_card(league_rounds):
    """外卡 -> 分區的對應與輪空隊伍（原地寫入 feedsInto／byeIds）。"""
    wild, division = league_rounds["F"], league_rounds["D"]
    wild_ids = set().union(*(_team_ids(f) for f in wild)) if wild else set()
    for f in wild:
        f["feedsInto"] = next((d["id"] for d in division if _team_ids(f) & _team_ids(d)), None)
    for d in division:
        d["byeIds"] = sorted(_team_ids(d) - wild_ids) if wild else []


# ---------------------------------------------------------------- 抓取與寫檔
def fetch_postseason(season):
    """抓取該年度的季後賽系列賽原始資料；失敗或格式不符回傳 None。"""
    season = htmlutil._int_id(season)
    if season is None:
        return None
    data = net.fetch_json(POSTSEASON_SERIES_URL.format(season=season))
    if not isinstance(data, dict) or not isinstance(data.get("series"), list):
        return None
    return data


def build_playoffs_data(season, raw, as_of, now=None):
    now = now or timeutil.get_taipei_now()
    parsed = parse_postseason(raw)
    return {
        "schema": PLAYOFFS_SCHEMA,
        "season": htmlutil._int_id(season),
        "status": parsed["status"],
        "champion": parsed["champion"],
        "rounds": parsed["rounds"],
        "asOf": as_of.isoformat(),
        "updatedAt": now.isoformat(timespec="seconds"),
    }


def generate_playoffs(today, now=None):
    """決定要顯示哪一屆（今年已有完賽或進行中的比賽就用今年，否則改抓去年），寫 data/playoffs.json 與 playoffs.html。
    抓取失敗、或兩年都沒有資料時不覆寫任何舊檔並回傳 False；否則 True。"""
    chosen = None
    for season in (today.year, today.year - 1):
        raw = fetch_postseason(season)
        if raw is None:
            print(f"Error: {season} 季後賽資料抓取失敗，保留既有檔案不覆寫。", file=sys.stderr)
            return False
        if has_started(raw):
            chosen = (season, raw)
            break
    if chosen is None:
        print("Error: 今年與去年都沒有季後賽資料，保留既有檔案不覆寫。", file=sys.stderr)
        return False
    data = build_playoffs_data(chosen[0], chosen[1], today, now)
    page = render_playoffs_page(data)
    storage.write_json_atomic(PLAYOFFS_PATH, data)
    with open(PLAYOFFS_PAGE_PATH, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Successfully generated {PLAYOFFS_PAGE_PATH} and {PLAYOFFS_PATH}.")
    return True


# ---------------------------------------------------------------- 渲染
def playoffs_title(data):
    """「2026 季後賽結果」或進行中的「2026 季後賽・截至 M/D」。"""
    season = htmlutil._int_id(data.get("season"))
    if data.get("status") == STATUS_LIVE:
        as_of = timeutil._parse_iso_date(data.get("asOf"))
        return f"{season} 季後賽・截至 {as_of.month}/{as_of.day}" if as_of else f"{season} 季後賽"
    return f"{season} 季後賽結果"


def _clean_series(raw):
    """讀回 JSON 時的防禦：欄位型別不對的系列賽略過；兩隊不足補「待定」。"""
    if not isinstance(raw, dict):
        return None
    teams = []
    for t in raw.get("teams") if isinstance(raw.get("teams"), list) else []:
        if not isinstance(t, dict):
            continue
        tid = htmlutil._int_id(t.get("id"))
        wins = htmlutil._int_id(t.get("wins"))
        name = str(t.get("name") or "").strip() or None
        teams.append({"id": tid, "name": name or (_team_name(tid) if tid is not None else None), "wins": wins or 0})
    while len(teams) < 2:
        teams.append({"id": None, "name": None, "wins": 0})
    byes = [htmlutil._int_id(b) for b in raw.get("byeIds") if htmlutil._int_id(b) is not None] \
        if isinstance(raw.get("byeIds"), list) else []
    return {
        "id": str(raw.get("id") or ""),
        "label": str(raw.get("label") or ""),
        "teams": teams[:2],
        "winsNeeded": htmlutil._int_id(raw.get("winsNeeded")) or 0,
        "state": raw.get("state") if raw.get("state") in (STATE_NOT_STARTED, STATE_LIVE, STATE_DONE) else STATE_NOT_STARTED,
        "winnerId": htmlutil._int_id(raw.get("winnerId")),
        "feedsInto": raw.get("feedsInto") if isinstance(raw.get("feedsInto"), str) else None,
        "byeIds": byes,
    }


def _series_list(container):
    return [s for s in (_clean_series(x) for x in container) if s] if isinstance(container, list) else []


def _league_rounds(rounds, league):
    block = rounds.get(league) if isinstance(rounds.get(league), dict) else {}
    return {rnd: _series_list(block.get(rnd)) for rnd in "FDL"}


def _placeholder(label):
    return {"id": "", "label": label, "teams": [{"id": None, "name": None, "wins": 0} for _ in range(2)],
            "winsNeeded": 0, "state": STATE_NOT_STARTED, "winnerId": None, "feedsInto": None, "byeIds": []}


def _rows(league_rounds, league):
    """[(外卡系列賽或 None, 分區系列賽或 None)]：恆為 2 列（資料不足時補待定）。
    每組分區系列賽與餵給它的外卡系列賽同列；還沒接到分區的外卡系列賽排在後面。"""
    wild, division = league_rounds["F"], league_rounds["D"]
    used = set()
    rows = []
    for d in division:
        feeder = next((f for f in wild if f["feedsInto"] == d["id"] and f["id"] not in used), None)
        if feeder:
            used.add(feeder["id"])
        rows.append([feeder, d])
    for f in wild:
        if f["id"] not in used:
            slot = next((r for r in rows if r[0] is None), None)
            if slot:
                slot[0] = f
            else:
                rows.append([f, None])
            used.add(f["id"])
    while len(rows) < 2:
        rows.append([None, None])
    prefix = LEAGUE_NAMES[league]
    return [(f or _placeholder(f"{prefix}{ROUND_LABELS['F']}"), d or _placeholder(f"{prefix}{ROUND_LABELS['D']}"))
            for f, d in rows]


def _render_team_row(team, winner_id, state, is_bye):
    name = team["name"] if team["name"] else PENDING
    has_team = team["id"] is not None and _is_known_team(team["id"])
    won = has_team and winner_id is not None and team["id"] == winner_id
    lost = has_team and winner_id is not None and team["id"] != winner_id
    cls = "po-team" + (" is-win" if won else "") + (" is-lose" if lost else "") + ("" if has_team else " is-tbd")
    logo = ""
    meta = TEAM_META.get(team["id"]) if has_team else None
    if meta:
        logo = htmlutil.logo_picture(team["id"], meta["abbr"], 18, alt="")
    mark = '<span class="po-adv" title="晉級">晉級</span>' if won else ""
    bye = f'<span class="po-bye">{BYE_LABEL}</span>' if is_bye else ""
    wins = htmlutil.esc(team["wins"]) if has_team and state != STATE_NOT_STARTED else "–"
    return (f'<li class="{cls}"><span class="po-logo">{logo}</span><span class="po-nm">{htmlutil.esc(name)}</span>'
            f'{bye}{mark}<span class="po-w">{wins}</span></li>')


def _render_series(series):
    state = series["state"]
    live = '<span class="po-live">進行中</span>' if state == STATE_LIVE else ""
    rows = "".join(_render_team_row(t, series["winnerId"], state, t["id"] is not None and t["id"] in series["byeIds"])
                   for t in series["teams"])
    needed = f'<span class="po-need">先勝 {series["winsNeeded"]}</span>' if series["winsNeeded"] else ""
    bye_names = "、".join(t["name"] or PENDING for t in series["teams"] if t["id"] is not None and t["id"] in series["byeIds"])
    byeline = f'<p class="po-byeline">{BYE_LABEL}：{htmlutil.esc(bye_names)}</p>' if bye_names else ""
    return (f'<article class="po-s is-{"done" if state == STATE_DONE else "live" if state == STATE_LIVE else "idle"}">'
            f'<header class="po-sh"><span class="po-label">{htmlutil.esc(series["label"])}</span>{live or needed}</header>'
            f'{byeline}<ul class="po-teams">{rows}</ul></article>')


def _tree_cell(series, col, row, span=1, kind=""):
    style = f"grid-column:{col};grid-row:{row}" + (f" / span {span}" if span > 1 else "")
    return f'<div class="po-cell {kind}" style="{style}">{_render_series(series)}</div>'


def _render_tree(rounds):
    """桌面版 7 欄對稱格線：美聯外卡、美聯分區、美聯冠軍賽、世界大賽、國聯冠軍賽、國聯分區、國聯外卡。"""
    al, nl = _league_rounds(rounds, "AL"), _league_rounds(rounds, "NL")
    rows_al, rows_nl = _rows(al, "AL"), _rows(nl, "NL")
    n = max(len(rows_al), len(rows_nl))
    heads = [("美聯外卡", 1), ("美聯分區", 2), ("美聯冠軍賽", 3), ("世界大賽", 4),
             ("國聯冠軍賽", 5), ("國聯分區", 6), ("國聯外卡", 7)]
    cells = [f'<h3 class="po-colhead" style="grid-column:{c};grid-row:1">{t}</h3>' for t, c in heads]
    for league, rows, cols in (("AL", rows_al, (1, 2, 3)), ("NL", rows_nl, (7, 6, 5))):
        for i, (f, d) in enumerate(rows):
            cells.append(_tree_cell(f, cols[0], i + 2, kind=f"po-f po-{league.lower()}"))
            cells.append(_tree_cell(d, cols[1], i + 2, kind=f"po-d po-{league.lower()}"))
        lcs = (_league_rounds(rounds, league)["L"] or [_placeholder(f"{LEAGUE_NAMES[league]}{ROUND_LABELS['L']}")])[0]
        cells.append(_tree_cell(lcs, cols[2], 2, n, kind=f"po-l po-{league.lower()}"))
    ws = (_series_list(rounds.get("WS")) or [_placeholder(ROUND_LABELS["W"])])[0]
    cells.append(_tree_cell(ws, 4, 2, n, kind="po-ws"))
    return f'    <div class="po-tree" role="group" aria-label="季後賽對戰樹">\n      ' + "\n      ".join(cells) + "\n    </div>"


def _render_stack(rounds):
    """手機／平板：依輪次由上而下堆疊（世界大賽、聯盟冠軍賽、分區、外卡），同輪美聯在前。"""
    al, nl = _league_rounds(rounds, "AL"), _league_rounds(rounds, "NL")
    rows = {"AL": _rows(al, "AL"), "NL": _rows(nl, "NL")}
    ws = (_series_list(rounds.get("WS")) or [_placeholder(ROUND_LABELS["W"])])[0]
    parts = []
    for rnd, title in STACK_ROUNDS:
        if rnd == "W":
            body = f'<div class="po-lg po-lg-ws">{_render_series(ws)}</div>'
        else:
            groups = []
            for league, lr in (("AL", al), ("NL", nl)):
                if rnd == "L":
                    items = lr["L"] or [_placeholder(f"{LEAGUE_NAMES[league]}{ROUND_LABELS['L']}")]
                else:
                    items = [r[0 if rnd == "F" else 1] for r in rows[league]]
                groups.append(f'<div class="po-lg"><h4>{LEAGUE_NAMES[league]}</h4>'
                              + "".join(_render_series(s) for s in items) + "</div>")
            body = "".join(groups)
        parts.append(f'      <section class="po-round" aria-label="{title}"><h3>{title}</h3><div class="po-round-body">{body}</div></section>')
    return '    <div class="po-stack">\n' + "\n".join(parts) + "\n    </div>"


def _champion_html(data):
    champ = data.get("champion") if isinstance(data.get("champion"), dict) else None
    if not champ:
        return ""
    name = str(champ.get("name") or "").strip() or _team_name(champ.get("id")) or PENDING
    meta = TEAM_META.get(htmlutil._int_id(champ.get("id")))
    logo = htmlutil.logo_picture(champ.get("id"), meta["abbr"], 40, alt="") if meta else ""
    season = htmlutil._int_id(data.get("season"))
    return (f'  <section class="po-champion" aria-label="冠軍"><span class="po-champ-logo">{logo}</span>'
            f'<div><p class="po-champ-k">{season} 世界大賽冠軍</p><p class="po-champ-n">{htmlutil.esc(name)}</p></div></section>')


def render_playoffs_page(data):
    """整頁 HTML（templates/playoffs.html）。data 為 build_playoffs_data 的結果。"""
    rounds = data.get("rounds") if isinstance(data.get("rounds"), dict) else {}
    title = playoffs_title(data)
    updated = str(data.get("updatedAt") or "")[:16].replace("T", " ")
    return htmlutil.fill_slots(htmlutil.load_template("playoffs.html"), {
        "TITLE": htmlutil.esc(f"MLB 每日戰報 - 季後賽 {title}"),
        "DATELINE": htmlutil.esc(title),
        "CHAMPION": _champion_html(data),
        "BRACKET": _render_tree(rounds) + "\n" + _render_stack(rounds),
        "GENERATED_AT": htmlutil.esc(updated),
    })


def _score_line(series):
    """「光芒 2–1 守護者」：勝場多的在前；未分出的平手或尚未開打保持原順序。"""
    a, b = sorted(series["teams"], key=lambda t: -t["wins"])
    name = lambda t: htmlutil.esc(t["name"] or PENDING)  # noqa: E731
    return f'{name(a)} {a["wins"]}–{b["wins"]} {name(b)}'


def _current_round(rounds):
    """目前這一輪：已有勝場或進行中比賽的最後一輪（世界大賽 > 冠軍賽 > 分區 > 外卡）；回傳 (標題, [系列賽])。"""
    al, nl = _league_rounds(rounds, "AL"), _league_rounds(rounds, "NL")
    levels = [("世界大賽", _series_list(rounds.get("WS"))),
              ("聯盟冠軍賽", al["L"] + nl["L"]),
              ("分區系列賽", al["D"] + nl["D"]),
              ("外卡系列賽", al["F"] + nl["F"])]
    for title, items in levels:
        if any(s["state"] != STATE_NOT_STARTED for s in items):
            return title, items
    return None, []


def render_playoffs_card(data):
    """首頁側欄的季後賽小卡；沒有可用資料回傳空字串。
    進行中列出目前這一輪各系列賽的比數；完成後顯示世界大賽冠軍。"""
    if not isinstance(data, dict) or not isinstance(data.get("rounds"), dict):
        return ""
    rounds = data["rounds"]
    season = htmlutil._int_id(data.get("season"))
    champ = data.get("champion") if isinstance(data.get("champion"), dict) else None
    body = ""
    if data.get("status") == STATUS_DONE and champ:
        ws = _series_list(rounds.get("WS"))
        name = htmlutil.esc(str(champ.get("name") or "").strip() or _team_name(champ.get("id")) or PENDING)
        detail = ""
        if ws:
            winner = next((t for t in ws[0]["teams"] if t["id"] == ws[0]["winnerId"]), None)
            loser = next((t for t in ws[0]["teams"] if t["id"] != ws[0]["winnerId"]), None)
            if winner and loser:
                detail = f'（{winner["wins"]}–{loser["wins"]} 勝{htmlutil.esc(loser["name"] or PENDING)}）'
        body = f'    <p class="po-mini-champ">{season} 世界大賽冠軍：<strong>{name}</strong>{detail}</p>\n'
    else:
        title, items = _current_round(rounds)
        done_attr = lambda s: ' class="is-done"' if s["state"] == STATE_DONE else ""  # noqa: E731
        if not items:
            return ""
        lines = "".join(
            f'<li{done_attr(s)}>{_score_line(s)}</li>' for s in items)
        body = f'    <h4>{htmlutil.esc(title)}</h4>\n    <ul class="po-mini">{lines}</ul>\n'
    return (f'  <section class="po-card-mini" aria-labelledby="po-mini-h">\n'
            f'    <div class="po-mini-head"><h3 id="po-mini-h">季後賽</h3>'
            f'<span class="po-mini-sub">{htmlutil.esc(playoffs_title(data))}</span></div>\n'
            f'{body}'
            f'    <a class="po-mini-more" href="{os.path.basename(PLAYOFFS_PAGE_PATH)}">完整對戰樹 →</a>\n'
            f'  </section>')
