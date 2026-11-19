"""FanGraphs 深度分析（RSS）的抓取與渲染。"""

import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from . import htmlutil, net
from .config import ANALYSIS_LIMIT, ANALYSIS_RSS_URL, ANALYSIS_SOURCE, TAIPEI_TZ

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


def fetch_daily_analysis(date_str, limit=ANALYSIS_LIMIT):
    """抓取 FanGraphs RSS，回傳 date_str 當天（台北時間）發布的文章，新到舊，最多 limit 則。

    每則為 {"title", "link", "published"（台北時間 datetime）, "author"（可為空字串）}。
    podcast 與 link 不安全的文章整則捨棄。抓取或解析失敗回傳 None（與成功但 0 則的 [] 區分）。"""
    text = net.fetch_text(ANALYSIS_RSS_URL)
    if not text:
        return None
    try:
        day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TAIPEI_TZ)
        day_end = day_start + timedelta(days=1)
        root = ET.fromstring(text)
        if root.tag != "rss":
            raise ValueError(f"unexpected root element: {root.tag}")
        found = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = _safe_fangraphs_url(item.findtext("link"))
            pub = (item.findtext("pubDate") or "").strip()
            if not title or not link or not pub:
                continue
            if _is_podcast(title, [c.text for c in item.findall("category")]):
                continue
            try:
                published = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                continue
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            published = published.astimezone(TAIPEI_TZ)
            if not (day_start <= published < day_end):
                continue
            author = (item.findtext(_DC_CREATOR) or "").strip()
            found.append({"title": title, "link": link, "published": published, "author": author})
    except Exception as e:
        print(f"Warning: Could not parse analysis RSS ({e}). Skipping analysis section.", file=sys.stderr)
        return None
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
