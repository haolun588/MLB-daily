"""球隊專頁 JSON 的抓取與整理。"""

import os
import sys
import time

from . import net, season as season_mod, storage, timeutil
from .config import (
    SEASON_SCHEDULE_URL,
    ROSTER_DIR,
    ROSTER_FETCH_INTERVAL,
    TEAM_META,
    _UNSET,
)


def _season_started(year, start, today):
    """賽季日期區間內，確認開季真的發生（停擺防護）。回傳 True／False；查詢失敗回傳 None（無法判斷）。"""
    if season_mod.confirmed_started(year):
        return True
    data = net.fetch_json(SEASON_SCHEDULE_URL.format(start=start.isoformat(), end=today.isoformat()))
    dates = data.get("dates") if isinstance(data, dict) else None
    if not isinstance(dates, list):
        return None
    today_iso = today.isoformat()
    for d in dates:
        if not isinstance(d, dict):
            continue
        for g in d.get("games") or []:
            status = (g.get("status") or {}) if isinstance(g, dict) else {}
            if status.get("abstractGameState") == "Final" or (
                    d.get("date") == today_iso and status.get("detailedState") not in ("Postponed", "Cancelled")):
                season_mod.set_confirmed_started(year)
                return True
    return False


def resolve_roster_context(today, season_data=_UNSET):
    """依台北日期 today（date）判斷名單類型與數據賽季。

    回傳 dict：rosterType（active／40Man）、season（數據賽季 statsSeason）、rosterSeason（名單 season 參數）。
    快取啟用（cli.main）時，同一天只算一次，結果存進 data/season_cache.json 供各步驟共用。
    賽季 API 失敗時沿用快取的賽季日期；快取也沒有則退回以月份推估並印 warning（此結果不寫入快取）。
    日期上落在賽季中時，另查例行賽賽程確認開季真的發生（停擺防護）；查詢失敗則依日期判為賽季中。
    season_data：呼叫端已取得的 /seasons/{today.year} 回應（可為 None 代表已失敗）；省略時自行請求。
    """
    cached = season_mod.get_context(today)
    if cached is not None:
        return cached
    year = today.year
    dates = season_mod.get_season_dates(year, season_data)
    if dates is None:
        print(f"Warning: 無法取得 {year} 賽季日期，改以月份推估賽季。", file=sys.stderr)
        if 4 <= today.month <= 10:
            return {"rosterType": "active", "season": year, "rosterSeason": year}
        if today.month >= 11:
            return {"rosterType": "40Man", "season": year, "rosterSeason": year}
        return {"rosterType": "40Man", "season": year - 1, "rosterSeason": year}
    start, end = dates
    if today < start:
        ctx = {"rosterType": "40Man", "season": year - 1, "rosterSeason": year}
    elif today > end:
        ctx = {"rosterType": "40Man", "season": year, "rosterSeason": year}
    else:
        started = _season_started(year, start, today)
        if started is False:
            print(f"Warning: {year} 賽季日期已到（{start.isoformat()} 起），但查無已完賽或今日賽程，視為延後開季。", file=sys.stderr)
            ctx = {"rosterType": "40Man", "season": year - 1, "rosterSeason": year}
        else:
            if started is None:
                print(f"Warning: 無法確認 {year} 賽季是否已開打，依日期判為賽季中。", file=sys.stderr)
            ctx = {"rosterType": "active", "season": year, "rosterSeason": year}
    season_mod.set_context(today, ctx)
    return ctx


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


def _hand_code(person, key):
    """person[key].code（batSide／pitchHand）；非字串或空值視為 None。"""
    hand = person.get(key)
    code = hand.get("code") if isinstance(hand, dict) else None
    return code if isinstance(code, str) and code else None


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
        "bats": _hand_code(person, "batSide"),
        "throws": _hand_code(person, "pitchHand"),
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


def fetch_rosters(today, extras_factory=None):
    """抓 30 隊名單與數據，寫 data/teams/{id}.json 與 index.json。回傳成功隊數。
    extras_factory(today, ctx) 回傳 extras(team_id, players) -> dict，其內容併入各隊 JSON（球隊專頁的 summary／recent／leaders）；
    工廠或 extras 失敗只印警告，名單照寫。"""
    ctx = resolve_roster_context(today)
    roster_type, season, roster_season = ctx["rosterType"], ctx["season"], ctx["rosterSeason"]
    print(f"Roster context: rosterType={roster_type}, season={season}, rosterSeason={roster_season}")
    now_iso = timeutil.get_taipei_now().isoformat(timespec="seconds")
    extras = None
    if extras_factory is not None:
        try:
            extras = extras_factory(today, ctx)
        except Exception as e:  # noqa: BLE001 - 附加資料失敗不得影響名單
            print(f"Warning: 球隊專頁附加資料初始化失敗（{type(e).__name__}: {e}），只寫名單。", file=sys.stderr)
    teams_index = []
    ok_count = 0
    for i, (tid, meta) in enumerate(sorted(TEAM_META.items(), key=lambda kv: kv[1]["abbr"])):
        if i:
            time.sleep(ROSTER_FETCH_INTERVAL)
        entry = {"id": tid, "abbr": meta["abbr"], "name": meta["name"], "color": meta["color"],
                 "league": meta["league"], "division": meta["division"], "ok": False}
        players = None
        try:
            players = build_team_players(net.fetch_json(roster_url(tid, roster_type, roster_season, season)))
        except Exception as e:
            print(f"Warning: {meta['abbr']} 名單處理失敗：{e}", file=sys.stderr)
        if not players:  # None（抓取失敗／格式不符）或 []（0 人，視為異常）都保留舊檔
            reason = "抓取失敗" if players is None else "回傳 0 人"
            print(f"Warning: {meta['abbr']} 名單{reason}，保留舊檔（若有）。", file=sys.stderr)
        else:
            extra = {}
            if extras is not None:
                try:
                    extra = extras(tid, players) or {}
                except Exception as e:  # noqa: BLE001
                    print(f"Warning: {meta['abbr']} 球隊專頁附加資料失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
            try:
                storage.write_json_atomic(os.path.join(ROSTER_DIR, f"{tid}.json"), {
                    "meta": {"id": tid, "abbr": meta["abbr"], "name": meta["name"], "rosterType": roster_type,
                             "season": season, "updatedAt": now_iso},
                    "players": players,
                    **extra,
                })
                entry["ok"] = True
                entry["updatedAt"] = now_iso
                ok_count += 1
            except OSError as e:
                print(f"Warning: {meta['abbr']} 寫檔失敗：{e}", file=sys.stderr)
        teams_index.append(entry)
    index_path = os.path.join(ROSTER_DIR, "index.json")
    if ok_count == 0 and os.path.exists(index_path):  # 全部失敗且已有舊索引：保留，避免把上次成功的索引覆寫成全 ok=False
        print("Warning: 30 隊名單全部失敗，保留既有 index.json 不覆寫。", file=sys.stderr)
    else:
        storage.write_json_atomic(index_path, {
            "updatedAt": now_iso, "rosterType": roster_type, "season": season, "teams": teams_index,
        })
    print(f"Rosters done: {ok_count}/{len(TEAM_META)} teams updated.")
    return ok_count
