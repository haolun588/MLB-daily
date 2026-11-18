"""MLB.com 新聞的抓取與渲染。"""

import html as html_lib
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone

from . import htmlutil, net
from .config import NEWS_RSS_URL, NEWS_SOURCE, TAIPEI_TZ


def fetch_game_news(game_pk, away_abbr, home_abbr, away_r, home_r):
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/content"
    backup_headline = f"{away_abbr} @ {home_abbr} 賽事總結"
    backup_blurb = f"終場比分 {away_abbr} {away_r} - {home_abbr} {home_r}。本場比賽目前無官方文字摘要。"
    
    data = net.fetch_json(url)
    if not data:
        return {"headline": backup_headline, "blurb": backup_blurb}
        
    try:
        recap = data.get("editorial", {}).get("recap", {}).get("mlb", {})
        headline = recap.get("headline")
        blurb = recap.get("blurb")
        if headline and blurb:
            return {"headline": headline, "blurb": blurb, "translatable": True}
    except Exception as e:
        print(f"Warning: Error parsing news for game {game_pk} ({e})", file=sys.stderr)
        
    return {"headline": backup_headline, "blurb": backup_blurb}


def _safe_mlb_url(url):
    """只接受 https 且主機為 www.mlb.com 或 *.mlbstatic.com 的網址；否則回傳 None。"""
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
    if host == "www.mlb.com" or host == "mlbstatic.com" or host.endswith(".mlbstatic.com"):
        return url
    return None


def _plain_text(raw):
    """RSS description 去 HTML 標籤與實體後的純文字（只供名單比對，不顯示）。"""
    text = re.sub(r"<[^>]*>", " ", raw or "")
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def fetch_all_daily_news(date_str):
    """抓取 MLB 官方 RSS，回傳 date_str 當天（台北時間 00:00–24:00）發布的「全部」新聞，新到舊（不設上限）。

    每則為 {"title", "link", "published"（台北時間 datetime）, "image"（網址或 None）, "summary"（純文字，只供比對）}。
    link 不安全的新聞整則捨棄；image 不安全則設為 None。
    抓取或解析失敗回傳 None（與「成功但 0 則」的 [] 區分）。"""
    text = net.fetch_text(NEWS_RSS_URL)
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
            link = _safe_mlb_url(item.findtext("link"))
            pub = (item.findtext("pubDate") or "").strip()
            if not title or not link or not pub:
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
            image_el = item.find("image")
            image = _safe_mlb_url(image_el.get("href")) if image_el is not None else None
            summary = _plain_text(item.findtext("description"))
            found.append({"title": title, "link": link, "published": published, "image": image, "summary": summary})
    except Exception as e:
        print(f"Warning: Could not parse news RSS ({e}). Skipping news section.", file=sys.stderr)
        return None
    found.sort(key=lambda n: n["published"], reverse=True)
    return found


def fetch_daily_news(date_str, limit=12):
    """同 fetch_all_daily_news，但最多 limit 則（顯示用）；失敗回傳 None。"""
    found = fetch_all_daily_news(date_str)
    return None if found is None else found[:limit]


def render_news_section(news, page_type):
    """每日新聞區塊。news 為 None（抓取失敗）時不輸出；[] 時顯示空狀態。
    休賽季版首則為頭條樣式（有圖片才顯示圖片）。所有文字與網址皆 escape。"""
    if news is None:
        return ""
    head = f'<div class="section-head"><h2 id="news-h">今日新聞</h2><span class="count">{len(news)} 則</span></div>'
    if not news:
        body = '<p class="section-empty">當日無新聞。</p>'
    else:
        rest = list(news)
        lead = ""
        if page_type == "offseason":
            top = rest.pop(0)
            image = ""
            if top.get("image"):
                image = f'<img class="lead-img" src="{htmlutil.esc(top["image"])}" alt="{htmlutil.esc(top.get("title_zh") or top["title"])}" loading="lazy">'
            lead = (
                f'<article class="lead-story">{image}'
                f'<p class="kicker">頭條 · {htmlutil.esc(top["published"].strftime("%m/%d %H:%M"))}</p>'
                f'<h3><a href="{htmlutil.esc(top["link"])}" target="_blank" rel="noopener">{htmlutil.esc(top.get("title_zh") or top["title"])}</a></h3>'
                f'<p class="src">{htmlutil.esc(NEWS_SOURCE)}</p></article>'
            )
        items = "".join(
            f'<li><time datetime="{htmlutil.esc(n["published"].strftime("%Y-%m-%dT%H:%M"))}">{htmlutil.esc(n["published"].strftime("%H:%M"))}</time>'
            f'<div><a class="title" href="{htmlutil.esc(n["link"])}" target="_blank" rel="noopener">{htmlutil.esc(n.get("title_zh") or n["title"])}</a>'
            f'<span class="src">{htmlutil.esc(NEWS_SOURCE)} · {htmlutil.esc(n["published"].strftime("%m/%d %H:%M"))}</span></div></li>'
            for n in rest
        )
        cls = "news is-two-col" if page_type == "offseason" else "news"
        body = lead + (f'<ol class="{cls}">{items}</ol>' if items else "")
    return f'<section class="section wrap" aria-labelledby="news-h">{head}{body}</section>'


def render_news_box(news):
    if not news:
        return ""
    return (
        '<section><h4 class="sub">賽後新聞</h4><article class="mini-news">'
        f'<h5>{htmlutil.esc(news.get("headline_zh") or news["headline"])}</h5><p>{htmlutil.esc(news.get("blurb_zh") or news["blurb"])}</p></article></section>'
    )
