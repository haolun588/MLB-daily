"""賽季日期快取與單日賽季 context 快取。

快取預設關閉（直接呼叫 rosters.resolve_roster_context 的測試不會讀寫 data/）；
只有 cli.main() 開頭呼叫 enable_cache(path) 才啟用。檔案損毀或缺檔一律視為空，不丟例外。
格式：{"years": {"2026": {"regularSeasonStartDate", "postSeasonEndDate", "fetchedAt", "confirmedStarted"}},
       "context": {"date": "2026-11-23", "ctx": {...}}}"""

import json
import sys
from datetime import timedelta

from . import net, storage, timeutil
from .config import SEASON_URL, SEASON_RANGE_SCHEDULE_URL, _UNSET

_state = {"path": None, "data": None}


def enable_cache(path):
    """啟用快取並清掉 process 內記憶。"""
    _state["path"] = path
    _state["data"] = None


def disable_cache():
    _state["path"] = None
    _state["data"] = None


def cache_enabled():
    return _state["path"] is not None


def _empty():
    return {"years": {}, "context": None}


def _load():
    """讀快取（process 內只讀檔一次）；未啟用回傳空結構（不寫回），損毀視為空。"""
    if not cache_enabled():
        return _empty()
    if _state["data"] is None:
        data = _empty()
        try:
            with open(_state["path"], "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                if isinstance(raw.get("years"), dict):
                    data["years"] = {k: v for k, v in raw["years"].items() if isinstance(v, dict)}
                if isinstance(raw.get("context"), dict):
                    data["context"] = raw["context"]
        except (OSError, ValueError):
            pass
        _state["data"] = data
    return _state["data"]


def _save():
    if not cache_enabled() or _state["data"] is None:
        return
    try:
        storage.write_json_atomic(_state["path"], _state["data"])
    except OSError as e:
        print(f"Warning: 無法寫入賽季快取 {_state['path']}（{type(e).__name__}: {e}）。", file=sys.stderr)


def get_context(today):
    """快取啟用且為同一天 → 回傳快取的 ctx（dict 複本），否則 None。"""
    ctx = _load().get("context")
    if isinstance(ctx, dict) and ctx.get("date") == today.isoformat() and isinstance(ctx.get("ctx"), dict):
        c = ctx["ctx"]
        if all(k in c for k in ("rosterType", "season", "rosterSeason")):
            return dict(c)
    return None


def set_context(today, ctx):
    if not cache_enabled():
        return
    _load()["context"] = {"date": today.isoformat(), "ctx": dict(ctx)}
    _save()


def confirmed_started(year):
    return _load()["years"].get(str(year), {}).get("confirmedStarted") is True


def set_confirmed_started(year):
    if not cache_enabled():
        return
    entry = _load()["years"].setdefault(str(year), {})
    entry["confirmedStarted"] = True
    _save()


def _parse_dates(info):
    if not isinstance(info, dict):
        return None
    start = timeutil._parse_iso_date(info.get("regularSeasonStartDate"))
    end = timeutil._parse_iso_date(info.get("postSeasonEndDate")) or timeutil._parse_iso_date(info.get("seasonEndDate"))
    if start is None or end is None:
        return None
    return start, end


# /seasons 的額外日期欄位（存進快取 years[year]，API 失敗時從快取讀）：休兵日判斷、例行賽是否結束、春訓開打日
_INFO_KEYS = ("lastDate1stHalf", "firstDate2ndHalf", "postSeasonStartDate", "regularSeasonEndDate", "springStartDate")
REST_WINDOW_DAYS = 14


def _parse_info(info):
    """只保留可解析的日期欄位（isoformat 字串）。"""
    out = {}
    if isinstance(info, dict):
        for k in _INFO_KEYS:
            d = timeutil._parse_iso_date(info.get(k))
            if d is not None:
                out[k] = d.isoformat()
    return out


def _season_lookup(year, season_data=_UNSET):
    """回傳 ((開季日, 季後賽結束日) 或 None, 額外日期欄位 dict)；API 成功時更新快取，失敗時改用快取（印 warning）。"""
    data = net.fetch_json(SEASON_URL.format(year=year)) if season_data is _UNSET else season_data
    try:
        info = data["seasons"][0]
        dates = _parse_dates(info)
        if dates is not None:
            extra = _parse_info(info)
            if cache_enabled():
                entry = _load()["years"].setdefault(str(year), {})
                entry["regularSeasonStartDate"] = dates[0].isoformat()
                entry["postSeasonEndDate"] = dates[1].isoformat()
                for k in _INFO_KEYS:
                    if k in extra:
                        entry[k] = extra[k]
                    else:
                        entry.pop(k, None)
                entry["fetchedAt"] = timeutil.get_taipei_now().isoformat(timespec="seconds")
                _save()
            return dates, extra
    except (TypeError, KeyError, IndexError, AttributeError):
        pass
    cached = _load()["years"].get(str(year))
    if cached:
        dates = _parse_dates({"regularSeasonStartDate": cached.get("regularSeasonStartDate"),
                              "postSeasonEndDate": cached.get("postSeasonEndDate")})
        if dates is not None:
            print(f"Warning: 無法取得 {year} 賽季日期，改用快取的賽季日期（{cached.get('fetchedAt', '?')} 取得）。", file=sys.stderr)
            return dates, _parse_info(cached)
    return None, {}


def get_season_dates(year, season_data=_UNSET):
    """回傳 (開季日, 季後賽結束日)（date）；API 成功時更新快取，失敗時改用快取（印 warning），都沒有回 None。
    season_data：呼叫端已取得的 /seasons/{year} 回應（None 代表已失敗）；省略時自行請求。"""
    return _season_lookup(year, season_data)[0]


def get_season_info(year, season_data=_UNSET):
    """回傳額外日期欄位 dict（_INFO_KEYS，isoformat 字串，取不到的欄位省略）。"""
    return _season_lookup(year, season_data)[1]


def regular_season_ended(year, today, season_data=_UNSET):
    """today 是否已超過該年例行賽最後一天（regularSeasonEndDate；當天本身不算超過）。
    取不到日期（API 失敗且無快取）或任何例外一律回 False，維持原本顯示。"""
    try:
        end = timeutil._parse_iso_date(_season_lookup(year, season_data)[1].get("regularSeasonEndDate"))
        return end is not None and today > end
    except Exception as e:  # noqa: BLE001 - 顯示用判斷不得讓呼叫端崩潰
        print(f"Warning: 無法判斷 {year} 例行賽是否結束（{type(e).__name__}: {e}）。", file=sys.stderr)
        return False


def season_calendar_events(years):
    """各年份的 MLB 官方賽季日程事件（春訓熱身賽開打、例行賽開幕日），供週報「下週日程」自動帶入。
    回傳 [{"id", "date", "title_zh", "note"}]；年份尚未公布（seasons: []）、網路或任何例外都吞掉，取不到的年份不產生事件。"""
    events = []
    for year in sorted(set(years)):
        try:
            dates, extra = _season_lookup(year)
            spring = timeutil._parse_iso_date(extra.get("springStartDate"))
            if spring is not None:
                events.append({"id": "spring_start", "date": spring, "title_zh": "春訓熱身賽開打", "note": "MLB 官方賽季日程"})
            if dates is not None:
                events.append({"id": "opening_day", "date": dates[0], "title_zh": "例行賽開幕日", "note": "MLB 官方賽季日程"})
        except Exception as e:  # noqa: BLE001 - 日程自動帶入失敗不得影響週報
            print(f"Warning: 無法取得 {year} 賽季日程（{type(e).__name__}: {e}）。", file=sys.stderr)
    return events


# ---------------------------------------------------------------- 無比賽日分類
def is_cancelled_or_postponed(status):
    """延賽／取消（這類比賽的 abstractGameState 也是 Final，不能只看它）。"""
    status = status if isinstance(status, dict) else {}
    detailed = str(status.get("detailedState") or "")
    return status.get("codedGameState") in ("D", "C") or detailed.startswith(("Postponed", "Cancelled"))


def is_completed(status):
    """真正完賽：abstractGameState 為 Final，且不是延賽／取消。"""
    status = status if isinstance(status, dict) else {}
    return status.get("abstractGameState") == "Final" and not is_cancelled_or_postponed(status)


def _range_games(start, end):
    """區間內所有賽事（status dict 的 list）；查詢失敗或格式不符回傳 None。"""
    data = net.fetch_json(SEASON_RANGE_SCHEDULE_URL.format(start=start.isoformat(), end=end.isoformat()))
    if not isinstance(data, dict) or not isinstance(data.get("dates"), list):
        return None
    out = []
    for day in data["dates"]:
        if not isinstance(day, dict) or not isinstance(day.get("games", []), list):
            return None
        for g in day.get("games", []):
            if not isinstance(g, dict):
                return None
            st = g.get("status")
            out.append(st if isinstance(st, dict) else {})
    return out


def _rest_reason(d, info):
    last1 = timeutil._parse_iso_date(info.get("lastDate1stHalf"))
    first2 = timeutil._parse_iso_date(info.get("firstDate2ndHalf"))
    post = timeutil._parse_iso_date(info.get("postSeasonStartDate"))
    if last1 and first2 and last1 < d < first2:
        return "明星賽假期"
    if post and d >= post:
        return "季後賽休兵"
    if last1 or first2 or post:
        return "例行賽休兵日"
    return "休兵日"


def classify_no_game_day(date_str):
    """無比賽日分類：回傳 ("rest_day", reason) 或 ("offseason", None)。
    在 [開季日, 季後賽結束日] 內，且前 14 天有完賽、後 14 天（不超過季後賽結束日）有賽程 → 休兵日；
    任一段查詢成功但無符合比賽 → 休賽季；查詢失敗則依日期區間判休兵日（印 warning）。"""
    d = timeutil._parse_iso_date(date_str)
    if d is None:
        return "offseason", None
    dates, info = _season_lookup(d.year)
    if dates is None or not (dates[0] <= d <= dates[1]):
        return "offseason", None
    reason = _rest_reason(d, info)
    delta = timedelta(days=1)
    if d >= dates[1]:  # 後段區間為空
        return "offseason", None
    prev = _range_games(d - timedelta(days=REST_WINDOW_DAYS), d - delta)
    nxt = _range_games(d + delta, min(d + timedelta(days=REST_WINDOW_DAYS), dates[1]))
    prev_ok = None if prev is None else any(is_completed(s) for s in prev)
    next_ok = None if nxt is None else any(not is_cancelled_or_postponed(s) for s in nxt)
    if prev_ok is False or next_ok is False:
        return "offseason", None
    if prev_ok is None or next_ok is None:
        print(f"Warning: 無法查詢 {date_str} 前後賽程，依日期區間判定為休兵日。", file=sys.stderr)
    return "rest_day", reason
