"""FanGraphs 深度分析（RSS）的抓取與渲染。"""

import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import feed_cache, htmlutil, net
from .config import ANALYSIS_LIMIT, ANALYSIS_MAX_PAGES, ANALYSIS_RSS_URL, ANALYSIS_SOURCE, TAIPEI_TZ

_ANALYSIS_HOSTS = ("blogs.fangraphs.com", "www.fangraphs.com", "fangraphs.com")
_DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
_PODCAST_CATEGORIES = ("podcasts", "effectively wild")


def _safe_fangraphs_url(url):
    """只接受 https 且主機為 blogs.fangraphs.com／www.fangraphs.com／fangraphs.com 的網址；否則回傳 None。"""
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url or any(ch.isspace() or ord(ch) < 32 for ch in url):
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        return None
    return url if host in _ANALYSIS_HOSTS else None


def _is_podcast(title, categories):
    if title.lower().startswith("effectively wild"):
        return True
    return any((c or "").strip().lower() in _PODCAST_CATEGORIES for c in categories)


def _parse_analysis_rss(text):
    """解析 FanGraphs RSS，回傳 (全部有效項目, 本頁最舊一篇的時間)；結構不符拋例外。
    每則為 {"title", "link", "published"（台北時間 datetime）, "author"}。podcast 與 link 不安全的文章捨棄；
    最舊時間則涵蓋所有 pubDate 可解析的項目（不論是否被捨棄），沒有則為 None（供分頁判斷）。"""
    root = ET.fromstring(text)
    if root.tag != "rss":
        raise ValueError(f"unexpected root element: {root.tag}")
    found = []
    oldest = None
    for item in root.iter("item"):
        pub = (item.findtext("pubDate") or "").strip()
        try:
            published = parsedate_to_datetime(pub)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        published = published.astimezone(TAIPEI_TZ)
        if oldest is None or published < oldest:
            oldest = published
        title = (item.findtext("title") or "").strip()
        link = _safe_fangraphs_url(item.findtext("link"))
        if not title or not link:
            continue
        if _is_podcast(title, [c.text for c in item.findall("category")]):
            continue
        author = (item.findtext(_DC_CREATOR) or "").strip()
        found.append({"title": title, "link": link, "published": published, "author": author})
    return found, oldest


def _page_url(page):
    return ANALYSIS_RSS_URL if page == 1 else f"{ANALYSIS_RSS_URL}?paged={page}"


def _collect_analysis(day_start):
    """抓取 FanGraphs feed 並併入 RSS 快取，回傳 (第 1 頁是否成功, 項目池)。

    第 1 頁為 ANALYSIS_RSS_URL，第 n 頁為 ?paged=n，最多 ANALYSIS_MAX_PAGES 頁；已抓到的最舊一篇仍不早於
    day_start 就繼續往後翻。第 2 頁起抓取、解析失敗或無項目即停（不影響已抓到的）；第 2 頁起只試一次，
    避免翻過最末頁時浪費重試時間。快取啟用時以 memo 記住已翻頁數與最舊時間，同一 process 不重複抓。"""
    st = feed_cache.memo("analysis")
    fresh = []
    if "ok" not in st:
        text = net.fetch_text(_page_url(1))
        st.update(ok=False, pages=0, oldest=None, done=False)
        if text:
            try:
                items, oldest = _parse_analysis_rss(text)
                fresh += items
                st.update(ok=True, pages=1, oldest=oldest, done=oldest is None)
            except Exception as e:
                print(f"Warning: Could not parse analysis RSS ({e}). Skipping analysis section.", file=sys.stderr)
    while (st["ok"] and not st["done"] and st["pages"] < ANALYSIS_MAX_PAGES
           and st["oldest"] is not None and st["oldest"] >= day_start):
        page = st["pages"] + 1
        text = net.fetch_text(_page_url(page), retries=1)
        items = oldest = None
        if text:
            try:
                items, oldest = _parse_analysis_rss(text)
            except Exception as e:
                print(f"Warning: Could not parse analysis RSS page {page} ({e}). Stop paging.", file=sys.stderr)
        if not items and oldest is None:  # 失敗、解析失敗或本頁沒有任何項目：不再往後翻
            st["done"] = True
            break
        fresh += items
        st.update(pages=page, oldest=oldest if oldest is not None else st["oldest"])
    if not st["ok"]:
        return False, []
    return True, feed_cache.merge("analysis", fresh)


def fetch_daily_analysis(date_str, limit=ANALYSIS_LIMIT):
    """抓取 FanGraphs RSS，回傳 date_str 當天（台北時間）發布的文章，新到舊，最多 limit 則。

    每則為 {"title", "link", "published"（台北時間 datetime）, "author"（可為空字串）}。
    podcast 與 link 不安全的文章整則捨棄。RSS 快取啟用時結果取自「快取 ∪ 本次抓取」，並會依目標日期往後翻頁。
    本次抓取或解析失敗：快取有當日項目則印 warning 並回傳快取項目，否則回傳 None（與成功但 0 則的 [] 區分）。"""
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TAIPEI_TZ)
    day_end = day_start + timedelta(days=1)
    ok, pool = _collect_analysis(day_start)
    if not ok:
        pool = feed_cache.items("analysis")
    found = [a for a in pool if day_start <= a["published"] < day_end]
    if not ok:
        if not found:
            return None
        print(f"Warning: 深度分析 RSS 抓取失敗，改用快取中 {date_str} 的 {len(found)} 則文章。", file=sys.stderr)
    found.sort(key=lambda a: a["published"], reverse=True)
    return found[:limit]


def render_analysis_section(items):
    """深度分析區塊。items 為 None 或空時回傳空字串（整個區塊不顯示）。所有文字與網址皆 escape。"""
    if not items:
        return ""
    rows = []
    for a in items:
        link = _safe_fangraphs_url(a.get("link"))
        if not link:
            continue
        src = ANALYSIS_SOURCE + (f" · {a['author']}" if a.get("author") else "")
        rows.append(
            f'<li><div><a class="title" href="{htmlutil.esc(link)}" target="_blank" rel="noopener">'
            f'{htmlutil.esc(a.get("title_zh") or a["title"])}</a>'
            f'<span class="src">{htmlutil.esc(src)}</span></div></li>'
        )
    if not rows:
        return ""
    head = (
        '<div class="section-head"><h2 id="analysis-h">深度分析</h2>'
        f'<span class="sub">{htmlutil.esc(ANALYSIS_SOURCE)} 進階數據分析</span>'
        f'<span class="count">{len(rows)} 則</span></div>'
    )
    return (
        f'<section class="section wrap" aria-labelledby="analysis-h">{head}'
        f'<ol class="news is-single">{"".join(rows)}</ol></section>'
    )
