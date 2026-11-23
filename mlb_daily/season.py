"""賽季日期快取與單日賽季 context 快取。

快取預設關閉（直接呼叫 rosters.resolve_roster_context 的測試不會讀寫 data/）；
只有 cli.main() 開頭呼叫 enable_cache(path) 才啟用。檔案損毀或缺檔一律視為空，不丟例外。
格式：{"years": {"2026": {"regularSeasonStartDate", "postSeasonEndDate", "fetchedAt", "confirmedStarted"}},
       "context": {"date": "2026-11-23", "ctx": {...}}}"""

import json
import sys

from . import net, storage, timeutil
from .config import SEASON_URL, _UNSET

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


def get_season_dates(year, season_data=_UNSET):
    """回傳 (開季日, 季後賽結束日)（date）；API 成功時更新快取，失敗時改用快取（印 warning），都沒有回 None。
    season_data：呼叫端已取得的 /seasons/{year} 回應（None 代表已失敗）；省略時自行請求。"""
    data = net.fetch_json(SEASON_URL.format(year=year)) if season_data is _UNSET else season_data
    dates = None
    try:
        info = data["seasons"][0]
        dates = _parse_dates(info)
        if dates is not None:
            if cache_enabled():
                entry = _load()["years"].setdefault(str(year), {})
                entry["regularSeasonStartDate"] = dates[0].isoformat()
                entry["postSeasonEndDate"] = dates[1].isoformat()
                entry["fetchedAt"] = timeutil.get_taipei_now().isoformat(timespec="seconds")
                _save()
            return dates
    except (TypeError, KeyError, IndexError, AttributeError):
        pass
    cached = _load()["years"].get(str(year))
    if cached:
        dates = _parse_dates({"regularSeasonStartDate": cached.get("regularSeasonStartDate"),
                              "postSeasonEndDate": cached.get("postSeasonEndDate")})
        if dates is not None:
            print(f"Warning: 無法取得 {year} 賽季日期，改用快取的賽季日期（{cached.get('fetchedAt', '?')} 取得）。", file=sys.stderr)
            return dates
    return None
