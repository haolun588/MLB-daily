"""數據排行榜：抓取、整理、寫檔與渲染（排行頁 leaders.html 與首頁小卡）。

層級與 standings 同層：只依賴 config、timeutil、net、htmlutil、storage，不 import rosters／standings／
index_page／cli。賽季與是否賽季中由呼叫端（cli）判斷後傳入；首頁小卡以 HTML 字串交給 index_page。"""

import math
import sys

from . import htmlutil, net, storage, timeutil
from .config import (
    LEADERS_CATEGORIES,
    LEADERS_HITTING_URL,
    LEADERS_LIMIT,
    LEADERS_PAGE_PATH,
    LEADERS_PATH,
    LEADERS_PITCHING_URL,
    LEADERS_SCHEMA,
    LEADERS_TOP_VISIBLE,
    SABERMETRICS_URL,
    TEAM_META,
)

WAR_FIP_NOTE = ("WAR 與 FIP 取自 MLB Stats API，計算方式與 FanGraphs（fWAR）、Baseball-Reference（bWAR）不同，"
                "數值可能有落差。")


# ---------------------------------------------------------------- 解析
def _number(value):
    """有限的數字（int／float，排除 bool）才回傳 float，其餘（None、字串、NaN）回傳 None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _person_team(row):
    """從 API 列取出 (球員 id, 球員名, 球隊 id)；缺球員 id 或姓名回傳 None。"""
    person = row.get("person") if isinstance(row.get("person"), dict) else {}
    name = str(person.get("fullName") or "").strip()
    pid = htmlutil._int_id(person.get("id"))
    if pid is None or not name:
        return None
    team = row.get("team") if isinstance(row.get("team"), dict) else {}
    return pid, name, htmlutil._int_id(team.get("id"))


def parse_leaders(data, group):
    """解析 /stats/leaders 回應，只取 statGroup == group 的區塊。
    回傳 {leaderCategory: [entry]}；entry = {"rank", "id", "name", "team_id", "value"}。
    保留 API 的排序，名次依顯示值（API 的 value 字串）重新計算：與前一筆 value 相同即同名次，否則名次 = 序號；
    名次 > LEADERS_LIMIT 者略過（第 10 名並列者全列）。"""
    out = {}
    if not isinstance(data, dict) or not isinstance(data.get("leagueLeaders"), list):
        return out
    for block in data["leagueLeaders"]:
        if not isinstance(block, dict) or block.get("statGroup") != group:
            continue
        category = block.get("leaderCategory")
        if not isinstance(category, str) or out.get(category):
            continue
        entries = []
        rank = 0
        for row in block.get("leaders") or []:
            if not isinstance(row, dict):
                continue
            who = _person_team(row)
            value = str(row.get("value") if row.get("value") is not None else "").strip()
            if who is None or not value:
                continue
            if not entries or value != entries[-1]["value"]:
                rank = len(entries) + 1
            if rank > LEADERS_LIMIT:
                break
            entries.append({"rank": rank, "id": who[0], "name": who[1], "team_id": who[2], "value": value})
        out[category] = entries
    return out


def _format_stat(value, decimals):
    text = f"{value:.{decimals}f}"
    return "0." + "0" * decimals if float(text) == 0 else text  # 避免 "-0.0"


def parse_sabermetrics(data, field, descending, decimals):
    """解析 /stats?stats=sabermetrics 回應：取 stat[field]（缺值或非數字略過）、同一球員只留最佳一列，
    依數值排序後自行計算名次（標準競賽排名，並列同名次），保留名次 <= LEADERS_LIMIT 者（第 10 名並列者全列）。"""
    best = {}
    for stat_block in (data.get("stats") if isinstance(data, dict) else None) or []:
        if not isinstance(stat_block, dict):
            continue
        for split in stat_block.get("splits") or []:
            if not isinstance(split, dict) or not isinstance(split.get("stat"), dict):
                continue
            value = _number(split["stat"].get(field))
            if value is None:
                continue
            who = _person_team({"person": split.get("player"), "team": split.get("team")})
            if who is None:
                continue
            old = best.get(who[0])
            if old is None or (value > old[0] if descending else value < old[0]):
                best[who[0]] = (value, who)
    # 名次以四捨五入後的顯示值判斷：顯示值相同即並列（標準競賽排名）；次序仍依原值，同顯示值內依英文名
    rows = [(value, _format_stat(value, decimals), who) for value, who in best.values()]
    rows.sort(key=lambda r: (-float(r[1]) if descending else float(r[1]), r[2][1]))
    entries = []
    rank = 0
    for i, (value, shown, (pid, name, team_id)) in enumerate(rows):
        if i == 0 or shown != rows[i - 1][1]:
            rank = i + 1
        if rank > LEADERS_LIMIT:
            break
        entries.append({"rank": rank, "id": pid, "name": name, "team_id": team_id, "value": shown})
    return entries


# ---------------------------------------------------------------- 抓取與寫檔
def _safe_parse(label, fn, *args):
    try:
        return fn(*args) or None
    except Exception as e:  # noqa: BLE001 - 單一項目解析失敗只影響該項
        print(f"Warning: 排行榜項目「{label}」解析失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        return None


def fetch_leaders(season):
    """抓取 12 個排行項目，回傳 {key: [entry] 或 None}（None 代表該項抓取或解析失敗）。共 5 個請求。"""
    season = htmlutil._int_id(season)
    cats = {key: None for key, _group, _label, _note in LEADERS_CATEGORIES}
    if season is None:
        return cats
    for group, url in (("hitting", LEADERS_HITTING_URL), ("pitching", LEADERS_PITCHING_URL)):
        data = net.fetch_json(url.format(season=season))
        parsed = _safe_parse(group, parse_leaders, data, group) or {}
        for key, entries in parsed.items():
            if key in cats and entries:
                cats[key] = entries
    jobs = (
        ("fip", "pitching", "qualified", 500, "fip", False, 2),
        ("war_hitting", "hitting", "all", 2000, "war", True, 1),
        ("war_pitching", "pitching", "all", 2000, "war", True, 1),
    )
    for key, group, pool, limit, field, descending, decimals in jobs:
        data = net.fetch_json(SABERMETRICS_URL.format(group=group, season=season, pool=pool, limit=limit))
        cats[key] = _safe_parse(key, parse_sabermetrics, data, field, descending, decimals)
    return cats


def build_leaders_data(season, in_season, today, categories, now=None):
    now = now or timeutil.get_taipei_now()
    return {
        "schema": LEADERS_SCHEMA,
        "season": htmlutil._int_id(season),
        "in_season": bool(in_season),
        "as_of": today.isoformat(),
        "generated_at": now.isoformat(timespec="seconds"),
        "categories": categories,
    }


def generate_leaders(season, in_season, today, now=None):
    """抓取、寫 data/leaders.json 與 leaders.html。12 項全部失敗時不覆寫任何舊檔並回傳 False；否則 True。"""
    categories = fetch_leaders(season)
    if not any(categories.values()):
        print(f"Error: {season} 賽季的數據排行榜全部抓取失敗，保留既有檔案不覆寫。", file=sys.stderr)
        return False
    failed = [key for key, entries in categories.items() if not entries]
    if failed:
        print(f"Warning: 以下排行項目暫無資料：{', '.join(failed)}。", file=sys.stderr)
    data = build_leaders_data(season, in_season, today, categories, now)
    page = render_leaders_page(data)
    storage.write_json_atomic(LEADERS_PATH, data)
    with open(LEADERS_PAGE_PATH, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Successfully generated {LEADERS_PAGE_PATH} and {LEADERS_PATH}.")
    return True


# ---------------------------------------------------------------- 渲染
def leaders_title(data):
    """「2026 賽季最終」或賽季中的「2026 賽季・截至 M/D」。"""
    season = htmlutil._int_id(data.get("season"))
    if data.get("in_season"):
        as_of = timeutil._parse_iso_date(data.get("as_of"))
        return f"{season} 賽季・截至 {as_of.month}/{as_of.day}" if as_of else f"{season} 賽季"
    return f"{season} 賽季最終"


def _clean_entries(raw):
    """讀回 JSON 時的防禦：只留欄位型別正確的項目，依名次排序。"""
    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        rank = htmlutil._int_id(e.get("rank"))
        name = str(e.get("name") or "").strip()
        value = str(e.get("value") if e.get("value") is not None else "").strip()
        if rank is None or not name or not value:
            continue
        out.append({"rank": rank, "id": e.get("id"), "name": name, "team_id": e.get("team_id"), "value": value})
    return sorted(out, key=lambda e: e["rank"])


def _team_abbr(team_id):
    meta = TEAM_META.get(htmlutil._int_id(team_id))
    return meta["abbr"] if meta else ""


def _render_row(e):
    abbr = _team_abbr(e["team_id"])
    logo = htmlutil.logo_picture(e["team_id"], abbr, 20, alt="") if abbr else ""
    return (f'<li class="ld-row"><span class="ld-rk">{e["rank"]}</span><span class="ld-tm">{logo}</span>'
            f'<span class="ld-nm">{htmlutil.player_link(e["id"], e["name"])}</span>'
            f'<span class="ld-ab">{htmlutil.esc(abbr)}</span><span class="ld-val">{htmlutil.esc(e["value"])}</span></li>')


def _render_full_card(label, note, entries):
    head = (f'<header class="ld-head"><h3>{htmlutil.esc(label)}</h3>'
            f'<span class="ld-note">{htmlutil.esc(note)}</span></header>')
    if not entries:
        return f'      <article class="ld-card">{head}<p class="ld-empty" role="status">暫無資料</p></article>'
    top = [e for e in entries if e["rank"] <= LEADERS_TOP_VISIBLE]
    rest = [e for e in entries if e["rank"] > LEADERS_TOP_VISIBLE]
    body = '<ul class="ld-list">' + "".join(_render_row(e) for e in top) + "</ul>"
    if rest:
        body += ('<details class="ld-more"><summary>展開前 10 名</summary><ul class="ld-list">'
                 + "".join(_render_row(e) for e in rest) + "</ul></details>")
    return f'      <article class="ld-card">{head}{body}</article>'


def _render_group(group, heading, categories):
    cards = "\n".join(
        _render_full_card(label, note, _clean_entries(categories.get(key)))
        for key, grp, label, note in LEADERS_CATEGORIES if grp == group)
    count = sum(1 for _k, grp, _l, _n in LEADERS_CATEGORIES if grp == group)
    return (f'  <section class="section" aria-labelledby="ld-{group}-h">\n'
            f'    <div class="section-head"><h2 id="ld-{group}-h">{heading}</h2><span class="count">{count} 項</span></div>\n'
            f'    <div class="ld-grid">\n{cards}\n    </div>\n  </section>')


def render_leaders_page(data):
    """整頁 HTML（templates/leaders.html）。data 為 build_leaders_data 的結果。"""
    categories = data.get("categories") if isinstance(data.get("categories"), dict) else {}
    title = leaders_title(data)
    generated = str(data.get("generated_at") or "")[:16].replace("T", " ")
    return htmlutil.fill_slots(htmlutil.load_template("leaders.html"), {
        "TITLE": htmlutil.esc(f"MLB 每日戰報 - 數據排行 {title}"),
        "DATELINE": htmlutil.esc(f"{title}　例行賽數據排行"),
        "HITTING": _render_group("hitting", "打者", categories),
        "PITCHING": _render_group("pitching", "投手", categories),
        "NOTE": htmlutil.esc(WAR_FIP_NOTE),
        "GENERATED_AT": htmlutil.esc(generated),
    })


def _mini_who(entries):
    """首頁小卡的「第 1 名」文字；並列時「A 等 N 人」。"""
    firsts = [e for e in entries if e["rank"] == entries[0]["rank"]]
    if len(firsts) > 1:
        return f'{htmlutil.esc(firsts[0]["name"])} 等 {len(firsts)} 人'
    abbr = _team_abbr(firsts[0]["team_id"])
    team = f' <span class="ld-mt">{htmlutil.esc(abbr)}</span>' if abbr else ""
    return f'{htmlutil.esc(firsts[0]["name"])}{team}'


def _render_mini_group(group, heading, categories):
    items = []
    for key, grp, label, _note in LEADERS_CATEGORIES:
        if grp != group:
            continue
        entries = _clean_entries(categories.get(key))
        if entries:
            who, value = _mini_who(entries), htmlutil.esc(entries[0]["value"])
            items.append(f'<li><span class="ld-mk">{htmlutil.esc(label)}</span><span class="ld-mw">{who}</span>'
                         f'<span class="ld-mv">{value}</span></li>')
        else:
            items.append(f'<li class="is-empty"><span class="ld-mk">{htmlutil.esc(label)}</span>'
                         f'<span class="ld-mw">暫無資料</span><span class="ld-mv">–</span></li>')
    return f'    <h4>{heading}</h4>\n    <ul class="ld-mini">\n      ' + "\n      ".join(items) + "\n    </ul>"


def render_leaders_card(data):
    """首頁側欄的排行小卡（12 項各列第 1 名）；沒有任何可用資料回傳空字串。"""
    if not isinstance(data, dict) or not isinstance(data.get("categories"), dict):
        return ""
    categories = data["categories"]
    if not any(_clean_entries(categories.get(key)) for key, _g, _l, _n in LEADERS_CATEGORIES):
        return ""
    return (f'  <section class="ld-card-mini" aria-labelledby="ld-mini-h">\n'
            f'    <div class="ld-mini-head"><h3 id="ld-mini-h">數據排行</h3>'
            f'<span class="ld-mini-sub">{htmlutil.esc(leaders_title(data))}</span></div>\n'
            f'{_render_mini_group("hitting", "打者", categories)}\n'
            f'{_render_mini_group("pitching", "投手", categories)}\n'
            f'    <a class="ld-mini-more" href="{LEADERS_PAGE_PATH}">完整排行 →</a>\n'
            f'  </section>')
