"""RSS 累積快取：把每次抓到的新聞／深度分析項目累積在 data/feed_cache.json，供補產過去日期時使用。

MLB 官方 RSS 固定只回最新 25 篇、不支援分頁，隔日補產舊日期時早已被擠出 feed；因此每次成功抓取後
把項目併入快取（以 link 去重、新資料覆蓋舊資料），之後查詢任一日期都用「快取 ∪ 本次抓取」過濾。

快取預設關閉（直接呼叫 fetch_all_daily_news 等的測試不會讀寫 data/）；只有 cli.main() 開頭呼叫
enable_cache(path) 才啟用。啟用時，同一 process 內同一 feed 只實際抓取一次（memo）。
格式：{"schema": 1, "feeds": {"news": [...], "analysis": [...]}}，項目的 published 存 ISO 字串（含時區），
讀回時轉為台北時間 datetime。檔案損壞或 schema 不符視為空快取（印 warning）；寫入失敗只印 warning。"""

import json
import sys
from datetime import datetime, timedelta

from . import storage, timeutil
from .config import FEED_CACHE_DAYS, FEED_CACHE_MAX_ITEMS, FEED_CACHE_SCHEMA, TAIPEI_TZ

FEEDS = ("news", "analysis")
_state = {"path": None, "data": None, "memo": {}}


def enable_cache(path):
    """啟用快取並清掉 process 內記憶（含 memo）。"""
    _state["path"] = path
    _state["data"] = None
    _state["memo"] = {}


def disable_cache():
    _state["path"] = None
    _state["data"] = None
    _state["memo"] = {}


def cache_enabled():
    return _state["path"] is not None


def memo(key):
    """同一 process 內某個 feed 的抓取狀態 dict。快取未啟用時每次回傳全新的 dict（等同不記憶）。"""
    if not cache_enabled():
        return {}
    return _state["memo"].setdefault(key, {})


def _parse_published(value):
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI_TZ)
    return dt.astimezone(TAIPEI_TZ)


def _decode_item(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("title"), str) or not isinstance(raw.get("link"), str):
        return None
    published = _parse_published(raw.get("published"))
    if published is None:
        return None
    return {**raw, "published": published}


def _encode_item(item):
    return {**item, "published": item["published"].isoformat()}


def _load():
    """讀快取（process 內只讀檔一次）；未啟用回傳空結構，損壞或 schema 不符視為空。"""
    if not cache_enabled():
        return {feed: [] for feed in FEEDS}
    if _state["data"] is None:
        data = {feed: [] for feed in FEEDS}
        try:
            with open(_state["path"], "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict) or raw.get("schema") != FEED_CACHE_SCHEMA or not isinstance(raw.get("feeds"), dict):
                raise ValueError("schema 不符")
            for feed in FEEDS:
                items = raw["feeds"].get(feed)
                if isinstance(items, list):
                    data[feed] = [it for it in map(_decode_item, items) if it is not None]
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print(f"Warning: RSS 快取 {_state['path']} 讀取失敗（{type(e).__name__}: {e}），視為空快取。", file=sys.stderr)
            data = {feed: [] for feed in FEEDS}
        _state["data"] = data
    return _state["data"]


def _save():
    payload = {"schema": FEED_CACHE_SCHEMA,
               "feeds": {feed: [_encode_item(it) for it in _state["data"][feed]] for feed in FEEDS}}
    try:
        storage.write_json_atomic(_state["path"], payload)
    except (OSError, TypeError, ValueError) as e:
        print(f"Warning: 無法寫入 RSS 快取 {_state['path']}（{type(e).__name__}: {e}）。", file=sys.stderr)


def items(feed):
    """快取中某 feed 的項目（新到舊的複本）；未啟用回傳 []。"""
    return list(_load()[feed])


def merge(feed, fresh):
    """把本次抓到的 fresh 併入快取（link 去重、fresh 覆蓋舊的），清除過期與超量，寫檔後回傳合併結果（新到舊）。
    未啟用時不讀寫檔，只對 fresh 依 link 去重後回傳。fresh 為空時不寫檔。"""
    if not cache_enabled():
        return list({it["link"]: it for it in fresh}.values())
    data = _load()
    if not fresh:
        return list(data[feed])
    by_link = {it["link"]: it for it in data[feed]}
    by_link.update({it["link"]: it for it in fresh})
    cutoff = timeutil.get_taipei_now() - timedelta(days=FEED_CACHE_DAYS)
    kept = [it for it in by_link.values() if it["published"] >= cutoff]
    kept.sort(key=lambda it: it["published"], reverse=True)
    data[feed] = kept[:FEED_CACHE_MAX_ITEMS]
    _save()
    return list(data[feed])
