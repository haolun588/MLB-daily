"""賽事預告與賽季倒數。"""

from datetime import datetime, timezone

from . import htmlutil, net, storage, timeutil
from .config import (
    EARLY_MORNING_HOUR,
    PEOPLE_STATS_URL,
    SCHEDULE_PREVIEW_URL,
    SEASON_URL,
    TAIPEI_TZ,
    TEAM_META,
    WEEKDAYS_ZH,
    _UNSET,
)


# ---------------------------------------------------------------- 今日賽事預告
def _is_called_off(game):
    detailed = str((game.get("status") or {}).get("detailedState") or "")
    return detailed.startswith(("Postponed", "Cancelled"))


def _season_pitching_line(person):
    """people API 的球員 -> (勝, 敗, ERA 字串)；找不到本季投手數據回傳 None。"""
    for block in person.get("stats") or []:
        if not isinstance(block, dict):
            continue
        if (block.get("group") or {}).get("displayName") != "pitching":
            continue
        if (block.get("type") or {}).get("displayName") != "season":
            continue
        splits = block.get("splits") or []
        if splits and isinstance(splits[0], dict) and isinstance(splits[0].get("stat"), dict):
            stat = splits[0]["stat"]
            return storage._to_int(stat.get("wins")), storage._to_int(stat.get("losses")), str(stat.get("era") or "-.--")
    return None


def fetch_pitcher_stats(person_ids, season):
    """批次查先發投手本季 W-L、ERA：{person_id: (W, L, ERA)}。失敗回傳空 dict（預告改為只顯示姓名）。"""
    ids = sorted({i for i in (htmlutil._int_id(x) for x in person_ids) if i is not None})
    if not ids:
        return {}
    data = net.fetch_json(PEOPLE_STATS_URL.format(ids=",".join(str(i) for i in ids), season=int(season)))
    result = {}
    if not isinstance(data, dict):
        return result
    for person in data.get("people") or []:
        if not isinstance(person, dict):
            continue
        pid = htmlutil._int_id(person.get("id"))
        line = _season_pitching_line(person)
        if pid is not None and line:
            result[pid] = line
    return result


def fetch_game_previews(date_str, season, records=None):
    """今日賽事預告資料；schedule 抓取失敗（無回應或結構不符）回傳 None，無比賽回傳 []。

    records：fetch_standings 的 records（team_id -> (W, L)）；取不到的球隊退回 schedule 內的 leagueRecord，仍無則省略。
    開賽時間轉台北時間；排除延賽／取消；兩隊任一不在 TEAM_META（如明星賽）者略過；依開賽時間排序。"""
    data = net.fetch_json(SCHEDULE_PREVIEW_URL.format(date=date_str))
    if not isinstance(data, dict) or not isinstance(data.get("dates"), list):
        return None
    raw_games = []
    for d in data["dates"]:
        if isinstance(d, dict):
            raw_games.extend(g for g in (d.get("games") or []) if isinstance(g, dict))
    records = records or {}
    games = []
    for g in raw_games:
        if _is_called_off(g):
            continue
        teams = g.get("teams") or {}
        sides = {}
        for key in ("away", "home"):
            side = teams.get(key) or {}
            tid = htmlutil._int_id((side.get("team") or {}).get("id"))
            if tid not in TEAM_META:
                break
            pitcher = side.get("probablePitcher") or {}
            rec = records.get(tid)
            if rec is None:
                lr = side.get("leagueRecord") or {}
                if "wins" in lr and "losses" in lr:
                    rec = (storage._to_int(lr.get("wins")), storage._to_int(lr.get("losses")))
            sides[key] = {
                "team_id": tid,
                "record": rec,
                "pitcher_id": htmlutil._int_id(pitcher.get("id")),
                "pitcher_name": pitcher.get("fullName") or None,
            }
        if len(sides) != 2:
            continue
        try:
            start = datetime.fromisoformat(str(g.get("gameDate")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if start.tzinfo is None:  # 無時區資訊視為 UTC
            start = start.replace(tzinfo=timezone.utc)
        start = start.astimezone(TAIPEI_TZ)
        dh = g.get("doubleHeader")
        number = storage._to_int(g.get("gameNumber"))
        if dh in ("Y", "S"):
            note = f"雙重賽 {number}/2" if number in (1, 2) else "雙重賽"
        else:
            note = ""
        games.append({
            "start": start,
            "tbd": bool((g.get("status") or {}).get("startTimeTBD")),
            "pk": storage._to_int(g.get("gamePk")),
            "note": note,
            "away": sides["away"],
            "home": sides["home"],
        })
    games.sort(key=lambda x: (x["tbd"], x["start"], x["pk"]))
    stats = fetch_pitcher_stats(
        [s["pitcher_id"] for g in games for s in (g["away"], g["home"]) if s["pitcher_id"] is not None], season)
    for g in games:
        for s in (g["away"], g["home"]):
            s["stat"] = stats.get(s["pitcher_id"])
    return games


def preview_range_text(games):
    """副標：台北時間首場日期；首場在凌晨（< 06:00）寫「凌晨起」，否則寫首場時刻。"""
    first = games[0]["start"]
    if games[0]["tbd"]:  # 依排序，首場待定代表全部待定，時間不可信，副標省略日期與時刻
        return f"開賽時間待定 · {len(games)} 場"
    day = f"{first.month}/{first.day}（{WEEKDAYS_ZH[first.weekday()]}）"
    if first.hour < EARLY_MORNING_HOUR:
        return f"台北時間 {day}凌晨起 · {len(games)} 場"
    return f"台北時間 {day} {first:%H:%M} 起 · {len(games)} 場"


def _render_preview_side(side, label):
    meta = TEAM_META[side["team_id"]]
    cls = f"gp-side{htmlutil.stripe_class(meta['color'])}"
    rec = f'<span class="gp-rec">{side["record"][0]}-{side["record"][1]}</span>' if side["record"] else ""
    name = side["pitcher_name"]
    if not name:
        pitch = '<span class="gp-tbd">未定</span>'
    else:
        pitch = htmlutil.player_link(side["pitcher_id"], name)
        stat = side.get("stat")
        if stat:
            pitch += f' <span class="gp-stat">({stat[0]}-{stat[1]}, {htmlutil.esc(stat[2])})</span>'
    return (
        f'<span class="visually-hidden">{label}</span><div class="{cls}" style="--team:{htmlutil.safe_color(meta["color"])}">'
        f'<span class="gp-team">{htmlutil.logo_picture(side["team_id"], meta["abbr"], 20, alt="")}'
        f'<span class="gp-name">{htmlutil.esc(meta["name"])}</span>{rec}</span>'
        f'<span class="gp-pitch">{pitch}</span></div>'
    )


def render_game_previews(games):
    """今日賽事預告區塊。games 為 None -> 錯誤文字；[] -> 今日無比賽。"""
    if games is None:
        return htmlutil._section_message("gp-h", "今日賽事預告", "賽程暫時無法取得", True)
    if not games:
        return htmlutil._section_message("gp-h", "今日賽事預告", "今日無比賽")
    items = []
    for g in games:
        if g["tbd"]:
            time_html = '<span class="gp-time">待定</span>'
        else:
            t = f"{g['start']:%H:%M}"
            time_html = f'<time class="gp-time" datetime="{t}">{t}</time>'
        note = f'<span class="gp-note">{htmlutil.esc(g["note"])}</span>' if g["note"] else ""
        items.append(
            f'      <li class="gp-game"><div class="gp-when">{time_html}{note}</div>'
            f'<div class="gp-teams">{_render_preview_side(g["away"], "客隊")}{_render_preview_side(g["home"], "主隊")}</div></li>'
        )
    count = f'<span class="count">{htmlutil.esc(preview_range_text(games))}</span>'
    return ('  <section class="section" aria-labelledby="gp-h">\n'
            f'    <div class="section-head"><h2 id="gp-h">今日賽事預告</h2>{count}</div>\n'
            '    <ol class="gp-list">\n' + "\n".join(items) + '\n    </ol>\n  </section>')


# ---------------------------------------------------------------- 休賽季：開季倒數、本週人事異動
def fetch_countdown(today, season_data=_UNSET):
    """下一個球季的春訓／開幕日倒數；任何失敗回傳 None（區塊隱藏）。

    今天已過當年 postSeasonEndDate 取 year+1，否則取當年。
    season_data：呼叫端已取得的 /seasons/{today.year} 回應（避免重複請求）；省略時自行請求。"""
    try:
        cur = net.fetch_json(SEASON_URL.format(year=today.year)) if season_data is _UNSET else season_data
        info = cur["seasons"][0]
        end = timeutil._parse_iso_date(info.get("postSeasonEndDate")) or timeutil._parse_iso_date(info.get("seasonEndDate"))
        if end is None:
            return None
        year = today.year + 1 if today > end else today.year
        nxt = cur if year == today.year else net.fetch_json(SEASON_URL.format(year=year))
        info = nxt["seasons"][0]
        spring = timeutil._parse_iso_date(info.get("springStartDate"))
        opening = timeutil._parse_iso_date(info.get("regularSeasonStartDate"))
    except (TypeError, KeyError, IndexError, AttributeError):
        return None
    if spring is None or opening is None:
        return None
    return {
        "year": year,
        "spring": spring,
        "opening": opening,
        "spring_days": (spring - today).days,
        "opening_days": (opening - today).days,
        "spring_started": today >= spring,
    }


def render_countdown(cd):
    if not cd:
        return ""

    def cell(label, value_html, date_obj):
        iso = date_obj.isoformat()
        return (f'<div><dt>{label}</dt><dd>{value_html}</dd>'
                f'<dd class="cd-date"><time datetime="{iso}">{iso}</time></dd></div>')

    if cd["spring_started"]:
        spring = cell("春訓", "春訓進行中", cd["spring"])
    else:
        spring = cell("距離春訓", f'<span class="num">{max(cd["spring_days"], 0)}</span> 天', cd["spring"])
    opening = cell("距離開幕戰", f'<span class="num">{max(cd["opening_days"], 0)}</span> 天', cd["opening"])
    return ('  <section class="section" aria-labelledby="cd-h">\n'
            f'    <div class="section-head"><h2 id="cd-h">開季倒數</h2><span class="count">{int(cd["year"])} 球季</span></div>\n'
            f'    <dl class="countdown">\n      {spring}\n      {opening}\n    </dl>\n  </section>')
