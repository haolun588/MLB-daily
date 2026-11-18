#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# Reconfigure standard I/O encoding to UTF-8 to avoid encoding errors on Windows (CP950)
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 拆分後的 mlb_daily 套件；以下重新匯出原本 fetch_mlb 的所有名稱以維持相容
from mlb_daily import config, games as games_mod, htmlutil, net, news as news_mod, previews, rosters, standings as standings_mod, storage, timeutil, transactions as transactions_mod, translate  # noqa: F401
from mlb_daily.config import (  # noqa: F401
    CANCELLED_REASONS,
    CLINCH_LABELS,
    CLINCH_LEGEND,
    DARK_LUMINANCE_THRESHOLD,
    DATE_KEY_RE,
    DIVISION_LAYOUT,
    EARLY_MORNING_HOUR,
    GAME_TYPE_LABELS,
    GEMINI_API_REVISION,
    GEMINI_DEFAULT_MODEL,
    GEMINI_ENDPOINT,
    GEMINI_FALLBACK_MODEL,
    GEMINI_RETRYABLE_HTTP,
    GEMINI_RETRY_AFTER_CAP,
    GEMINI_RETRY_DELAYS,
    LEGACY_ABBR_ALIASES,
    LOGO_URL,
    MAX_WAIT_CHECKS,
    NEWS_RSS_URL,
    NEWS_SOURCE,
    NL_DIVISION_IDS,
    PEOPLE_STATS_URL,
    POSTPONED_REASONS,
    ROSTER_DIR,
    ROSTER_FETCH_INTERVAL,
    SAVANT_URL,
    SCHEDULE_PREVIEW_URL,
    SEASON_URL,
    SLOT_RE,
    STANDINGS_URL,
    TAIPEI_TZ,
    TEAM_META,
    TEAM_NAMES_EN,
    TEAM_SLANG_ALIASES,
    TEMPLATE_DIR,
    TRANSACTIONS_RANGE_URL,
    TRANSLATE_BATCH_SIZE,
    WAIT_SLEEP_SECONDS,
    WEEKDAYS_ZH,
    WEEKLY_TX_DAYS,
    WEEKLY_TX_EXTENSION_RE,
    WEEKLY_TX_LIMIT,
    WEEKLY_TX_TYPE_CODES,
    _UNSET,
)
from mlb_daily.games import (  # noqa: F401
    _card_head,
    _card_open,
    _score_table,
    _side_view,
    _statline,
    batter_tags,
    build_base_view,
    build_decisions,
    build_game_view,
    build_postponed_view,
    build_summary_sentence,
    check_all_games_finalized,
    classify_game_status,
    collect_candidates,
    format_cumulative,
    game_type_label,
    get_team_info,
    pick_summary_sentences,
    render_batter,
    render_decisions,
    render_fallback_card,
    render_game_card,
    render_pitcher,
    render_postponed_card,
    select_performers,
    top_focus_score,
)
from mlb_daily.htmlutil import (  # noqa: F401
    _int_id,
    _section_message,
    esc,
    fill_slots,
    is_dark_color,
    load_template,
    logo_picture,
    player_link,
    relative_luminance,
    render_page,
    safe_color,
    stripe_class,
)
from mlb_daily.net import (  # noqa: F401
    ApiError,
    LAST_POST,
    _http_error_detail,
    _redact,
    _retry_after_seconds,
    fetch_json,
    fetch_text,
    post_json,
)
from mlb_daily.news import (  # noqa: F401
    _safe_mlb_url,
    fetch_daily_news,
    fetch_game_news,
    render_news_box,
    render_news_section,
)
from mlb_daily.previews import (  # noqa: F401
    _is_called_off,
    _render_preview_side,
    _season_pitching_line,
    fetch_countdown,
    fetch_game_previews,
    fetch_pitcher_stats,
    preview_range_text,
    render_countdown,
    render_game_previews,
)
from mlb_daily.rosters import (  # noqa: F401
    _find_stat,
    _int_or_none,
    _num,
    _round,
    build_hitting,
    build_pitching,
    build_player,
    build_team_players,
    fetch_rosters,
    pitcher_group,
    resolve_roster_context,
    roster_url,
)
from mlb_daily.standings import (  # noqa: F401
    STANDINGS_ICON,
    fetch_standings,
    render_standings_aside,
    render_standings_division,
    render_standings_fab,
    render_standings_row,
)
from mlb_daily.storage import (  # noqa: F401
    _to_int,
    entry_type,
    load_metadata,
    save_metadata,
    write_json_atomic,
    write_report,
)
from mlb_daily.timeutil import (  # noqa: F401
    _parse_iso_date,
    fmt_date_long,
    get_taipei_now,
    get_yesterday_str,
    report_href,
)
from mlb_daily.transactions import (  # noqa: F401
    _is_mlb_team,
    _tx_display_text,
    _tx_persons,
    fetch_daily_transactions,
    fetch_weekly_transactions,
    filter_major_league_transactions,
    render_card_transactions,
    render_transactions_section,
    render_tx_description,
    render_tx_items,
    render_weekly_transactions,
    select_weekly_highlights,
    split_game_transactions,
)
from mlb_daily.translate import (  # noqa: F401
    LAST_TRANSLATION,
    TRANSLATE_SYSTEM,
    TX_TRANSLATION_CACHE,
    _fallback_model,
    _interaction_text,
    _keep_in,
    _translate_chunk,
    apply_translations,
    build_team_glossary,
    translate_texts,
    translate_weekly_transactions,
)



# ---------------------------------------------------------------- 渲染：總覽與區塊
def render_summary(page_type, total_games=0, postponed=0, news_count=None, transactions_count=0):
    """news_count 為 None（新聞功能尚未接上）時，休賽季總覽省略「新聞」一格。"""
    if page_type == "offseason":
        items = []
        if news_count is not None:
            items.append(f"<div><dt>新聞</dt><dd>{int(news_count)} 則</dd></div>")
        items.append(f"<div><dt>人事異動</dt><dd>{int(transactions_count)} 筆</dd></div>")
    else:
        alert = ' class="is-alert"' if postponed else ""
        items = [
            f"<div><dt>總場數</dt><dd>{int(total_games)}</dd></div>",
            f"<div{alert}><dt>延賽</dt><dd>{int(postponed)}</dd></div>",
        ]
    return f'<div class="wrap">\n  <dl class="overview">\n    {"".join(items)}\n  </dl>\n</div>'


def render_search():
    return """<div class="searchbar">
  <div class="wrap">
    <form id="searchForm" role="search" autocomplete="off">
      <label class="visually-hidden" for="q">搜尋隊名或縮寫</label>
      <input id="q" type="search" placeholder="隊名或縮寫（如 洋基、LAD）" enterkeyhint="search">
      <button class="btn" type="submit">搜尋</button>
    </form>
    <p id="searchHint" class="search-hint" role="status" aria-live="polite">輸入隊名或縮寫，例：洋基、LAD</p>
  </div>
</div>"""


def render_games_section(cards_html):
    return (
        '<section class="section wrap" aria-labelledby="games-h">\n'
        f'  <div class="section-head"><h2 id="games-h">賽事結果</h2><span class="count">{len(cards_html)} 場</span></div>\n'
        '  <div class="games">\n' + "\n".join(cards_html) + "\n  </div>\n</section>"
    )


# ---------------------------------------------------------------- 組裝：整頁
def render_report(date_str, page_type, cards_html=(), postponed_count=0, tx_list=(), news=None):
    """賽季戰報與休賽季快報共用：組出所有插槽後交給 render_page。"""
    cards_html = list(cards_html)
    tx_list = list(tx_list)
    if page_type == "offseason":
        subtitle, title_suffix = "休賽季快報", " 休賽季快報"
        summary = render_summary(
            "offseason", news_count=None if news is None else len(news), transactions_count=len(tx_list))
        games = ""
        transactions = transactions_mod.render_transactions_section(tx_list, "大聯盟人事異動", "本日無大聯盟人事異動。")
    else:
        subtitle, title_suffix = "賽事結果", ""
        summary = render_summary("season", total_games=len(cards_html), postponed=postponed_count)
        games = render_games_section(cards_html)
        transactions = transactions_mod.render_transactions_section(tx_list)
    return htmlutil.render_page({
        "TITLE": htmlutil.esc(f"MLB 每日戰報 - {date_str}{title_suffix}"),
        "DATE_LONG": htmlutil.esc(timeutil.fmt_date_long(date_str)),
        "SUBTITLE": htmlutil.esc(subtitle),
        "SUMMARY": summary,
        "SEARCH": render_search() if cards_html else "",
        "NEWS": news_mod.render_news_section(news, page_type),
        "GAMES": games,
        "TRANSACTIONS": transactions,
        "GENERATED_AT": htmlutil.esc(timeutil.get_taipei_now().strftime("%Y-%m-%d %H:%M")),
    })


def generate_report(date_str, games):
    print(f"Generating report for {date_str} with {len(games)} games...")
    total_games = len(games)
    postponed_count = 0
    cards = []  # str（已渲染）或 dict（待翻譯後才渲染的完整卡片 view）
    game_views = []
    key_matchups_summary = []

    tx_list = transactions_mod.fetch_daily_transactions(date_str)
    transactions_count = len(tx_list)
    news = news_mod.fetch_daily_news(date_str)

    for game in games:
        game_pk = game["gamePk"]
        away_team_obj = game["teams"]["away"]["team"]
        home_team_obj = game["teams"]["home"]["team"]
        away_info = games_mod.get_team_info(away_team_obj["id"], away_team_obj["name"])
        home_info = games_mod.get_team_info(home_team_obj["id"], home_team_obj["name"])

        # 1. 延賽／取消
        if game.get("status", {}).get("detailedState") in ("Postponed", "Cancelled"):
            postponed_count += 1
            cards.append(games_mod.render_postponed_card(games_mod.build_postponed_view(game, away_info, home_info)))
            continue

        # 2. 已進行的比賽：先取 boxscore，失敗則簡化卡
        boxscore = net.fetch_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore")
        if not boxscore:
            print(f"Warning: Could not fetch boxscore for game {game_pk}. Using fallback card.", file=sys.stderr)
            view = games_mod.build_base_view(game, away_info, home_info)
            cards.append(games_mod.render_fallback_card(view))
            key_matchups_summary.append((0.0, games_mod.build_summary_sentence(view, [])))
            continue

        view, selected = games_mod.build_game_view(game, boxscore, away_info, home_info)
        key_matchups_summary.append((games_mod.top_focus_score(selected), games_mod.build_summary_sentence(view, selected)))
        view["news"] = news_mod.fetch_game_news(
            game_pk, away_info["abbr"], home_info["abbr"], view["away"]["r"], view["home"]["r"]
        )
        view["tx_groups"], tx_list = transactions_mod.split_game_transactions(tx_list, view["away"], view["home"])
        cards.append(view)
        game_views.append(view)

    tx_items = [tx for v in game_views for g in v["tx_groups"] for tx in g["items"]] + list(tx_list)
    team_names = [(v[side]["id"], v[side]["name_en"]) for v in game_views for side in ("away", "home")]
    translated = translate.apply_translations(news, game_views, tx_items, team_names)
    cards_html = [c if isinstance(c, str) else games_mod.render_game_card(c) for c in cards]

    storage.write_report(date_str, render_report(date_str, "season", cards_html, postponed_count, tx_list, news))

    summary_sentence = f"昨日共進行了 {total_games} 場賽事。"
    if postponed_count > 0:
        summary_sentence += f"其中有 {postponed_count} 場賽事延期。"
    if key_matchups_summary:
        summary_sentence += " " + "；".join(games_mod.pick_summary_sentences(key_matchups_summary)) + "。"

    meta = {
        "date": date_str,
        "total_games": total_games,
        "postponed": postponed_count,
        "summary": summary_sentence,
        "type": "season",
        "transactions_count": transactions_count,
        "translated": translated,
    }
    if news is not None:  # 抓取失敗時不寫 news_count
        meta["news_count"] = len(news)
    return meta


def build_offseason_summary(news, transactions_count):
    """休賽季 metadata 摘要（純文字，渲染時另行 escape）：優先用前 3 則新聞標題（已翻譯用譯文）。"""
    titles = [(n.get("title_zh") or n["title"]).strip() for n in (news or [])[:3]]
    titles = [t for t in titles if t]
    if titles:
        return "；".join(titles) + "。"
    if transactions_count > 0:
        return f"休賽季：人事異動 {transactions_count} 筆"
    return "休賽季，無重大消息"


def generate_offseason_report(date_str):
    """無賽事日：產出「休賽季快報」頁面（新聞＋大聯盟人事異動），回傳 metadata。"""
    tx_list = transactions_mod.fetch_daily_transactions(date_str)
    news = news_mod.fetch_daily_news(date_str)
    translated = translate.apply_translations(news, [], tx_list)
    storage.write_report(date_str, render_report(date_str, "offseason", tx_list=tx_list, news=news))
    meta = {
        "date": date_str,
        "total_games": 0,
        "postponed": 0,
        "summary": build_offseason_summary(news, len(tx_list)),
        "type": "offseason",
        "transactions_count": len(tx_list),
        "translated": translated,
    }
    if news is not None:  # 抓取失敗時不寫 news_count
        meta["news_count"] = len(news)
    return meta


def _safe_call(label, fn, *args):
    """區塊資料抓取的失敗隔離：任何例外都只印 warning 並回傳 None。"""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001 - 首頁區塊失敗不得影響戰報流程
        print(f"Warning: 首頁區塊「{label}」失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        return None


def _fallback_roster_context(today):
    """賽季判斷完全失敗時以月份推估（4–10 月視為賽季中）。"""
    return {"rosterType": "active" if 4 <= today.month <= 10 else "40Man", "season": today.year}


def fallback_index_extras(now=None):
    """build_index_extras 整體失敗時的最低限度輸出：戰績錯誤版側欄與手機按鈕（避免桌機右側空欄）。"""
    now = now or timeutil.get_taipei_now()
    ctx = _fallback_roster_context(now.date())
    return {
        "UPCOMING": "",
        "STANDINGS": standings_mod.render_standings_aside(None, ctx["season"], ctx["rosterType"] == "active", now),
        "STANDINGS_UI": standings_mod.render_standings_fab(),
    }


def build_index_extras(now=None):
    """抓取並渲染首頁的戰績側邊欄、今日預告／休賽季區塊，回傳 render_index 用的 slots dict。
    各資料來源各自失敗隔離。賽季判斷沿用 resolve_roster_context（rosterType == "active" 視為賽季中）。"""
    now = now or timeutil.get_taipei_now()
    today = now.date()
    # 當年 seasons 回應只請求一次，賽季判斷與開季倒數共用
    season_data = _safe_call("賽季日期", net.fetch_json, SEASON_URL.format(year=today.year))
    ctx = _safe_call("賽季判斷", rosters.resolve_roster_context, today, season_data)
    if not ctx:
        ctx = _fallback_roster_context(today)
    in_season = ctx.get("rosterType") == "active"
    season = ctx.get("season", today.year)

    standings = _safe_call("戰績", standings_mod.fetch_standings, season)
    if in_season:
        games = _safe_call("賽事預告", previews.fetch_game_previews, today.isoformat(), season,
                           (standings or {}).get("records"))
        upcoming = _safe_call("賽事預告渲染", previews.render_game_previews, games) or ""
    else:
        countdown = _safe_call("開季倒數", previews.fetch_countdown, today, season_data)
        weekly = _safe_call("本週人事異動", transactions_mod.fetch_weekly_transactions, today)
        if weekly:
            _safe_call("人事異動翻譯", translate.translate_weekly_transactions, weekly)
        upcoming = "\n\n".join(p for p in (
            _safe_call("開季倒數渲染", previews.render_countdown, countdown) or "",
            _safe_call("人事異動渲染", transactions_mod.render_weekly_transactions, weekly) or "",
        ) if p)
    aside = _safe_call("戰績渲染", standings_mod.render_standings_aside, standings, season, in_season, now)
    if not aside:
        aside = standings_mod.render_standings_aside(None, season, in_season, now)
    return {"UPCOMING": upcoming, "STANDINGS": aside, "STANDINGS_UI": standings_mod.render_standings_fab()}


def _offseason_counts(entry):
    """休賽季項目的 [(標籤, 文字)]；缺欄位則省略該數字。"""
    out = []
    if "news_count" in entry:
        out.append(("新聞", f"{storage._to_int(entry['news_count'])} 則"))
    if "transactions_count" in entry:
        out.append(("人事異動", f"{storage._to_int(entry['transactions_count'])} 筆"))
    return out


def render_latest_card(date_str, entry):
    kind = storage.entry_type(entry)
    date_main, _, weekday = timeutil.fmt_date_long(date_str).partition("（")
    wk = f"（{weekday}" if weekday else ""
    if kind == "offseason":
        eyebrow = "休賽季快報"
        stats = [("", label, text) for label, text in _offseason_counts(entry)]
    else:
        eyebrow = "戰報"
        postponed = storage._to_int(entry.get("postponed"))
        stats = [
            ("", "總場數", str(storage._to_int(entry.get("total_games")))),
            (' class="is-alert"' if postponed else "", "延賽", str(postponed)),
        ]
    stat_html = ""
    if stats:
        stat_html = '\n      <dl class="stat-row">' + "".join(
            f"<div{cls}><dt>{htmlutil.esc(label)}</dt><dd>{htmlutil.esc(text)}</dd></div>" for cls, label, text in stats
        ) + "</dl>"
    return f"""  <article class="latest" aria-labelledby="latest-h">
    <div>
      <p class="eyebrow">最新一期<span class="sep" aria-hidden="true">/</span>{eyebrow}</p>
      <h2 id="latest-h"><time datetime="{htmlutil.esc(date_str)}">{htmlutil.esc(date_main)}</time><span class="wk">{htmlutil.esc(wk)}</span></h2>
      <p class="summary">{htmlutil.esc(entry.get("summary", ""))}</p>{stat_html}
    </div>
    <a class="btn" href="{htmlutil.esc(timeutil.report_href(date_str))}">閱讀完整戰報 →</a>
  </article>"""


def render_recent_item(date_str, entry):
    kind = storage.entry_type(entry)
    date_main, _, weekday = timeutil.fmt_date_long(date_str).partition("（")
    wk = f"（{weekday}" if weekday else ""
    summary = str(entry.get("summary", ""))
    if kind == "offseason":
        label, label_cls = "休賽季", "k is-off"
        counts = "、".join(f"{lab} {text}" for lab, text in _offseason_counts(entry))
        if counts:
            summary = f"{summary}（{counts}）" if summary else counts
    else:
        label, label_cls = f"{storage._to_int(entry.get('total_games'))} 場", "k"
    return (
        f'      <li><a class="issue" href="{htmlutil.esc(timeutil.report_href(date_str))}">'
        f'<span class="d">{htmlutil.esc(date_str)}<span class="wk">{htmlutil.esc(wk)}</span></span>'
        f'<span class="{label_cls}">{htmlutil.esc(label)}</span><span class="s">{htmlutil.esc(summary)}</span></a></li>'
    )


TEAMS_ENTRY_HTML = """  <a class="teams-entry" href="teams.html">
    <span><strong>球隊名單</strong><span class="sub-txt">30 隊球員名單與本季數據，依守備位置分組</span></span>
    <span class="go" aria-hidden="true">30 隊名單 →</span>
  </a>"""


def render_index(metadata, extras=None):
    """首頁：最新一期大卡 + 左欄補充區塊（extras）+ 球隊名單入口 + 近期 6 期（不含最新一期）+ 戰績側邊欄。
    每次由 templates/index.html 全量重新產生。extras 為 build_index_extras 的結果（None 時不輸出側欄與補充區塊）。"""
    extras = extras or {}
    side_slots = {
        "UPCOMING": extras.get("UPCOMING", ""),
        "STANDINGS": extras.get("STANDINGS", ""),
        "STANDINGS_UI": extras.get("STANDINGS_UI", ""),
        "TEAMS": TEAMS_ENTRY_HTML,
    }
    dates = sorted(
        (d for d, v in metadata.items() if DATE_KEY_RE.match(str(d)) and isinstance(v, dict)),
        reverse=True,
    )
    if not dates:
        return htmlutil.fill_slots(htmlutil.load_template("index.html"), {
            **side_slots,
            "LATEST": '  <p class="section-empty">尚無戰報。</p>',
            "RECENT": "",
        })
    latest = render_latest_card(dates[0], metadata[dates[0]])
    recent_dates = dates[1:7]
    items = "\n".join(render_recent_item(d, metadata[d]) for d in recent_dates)
    if items:
        ol = f'\n    <ol class="issues">\n{items}\n    </ol>'
        count = f"最新 {len(recent_dates)} 期"
    else:
        ol = '\n    <p class="section-empty">尚無更早的戰報。</p>'
        count = "0 期"
    recent = f"""  <section class="section" aria-labelledby="recent-h">
    <div class="section-head"><h2 id="recent-h">近期</h2><span class="count">{count}</span></div>{ol}
    <p class="more"><a class="btn is-quiet" href="archive.html">查看全部歷史 →</a></p>
  </section>"""
    return htmlutil.fill_slots(htmlutil.load_template("index.html"), {**side_slots, "LATEST": latest, "RECENT": recent})


def update_index_page(new_metadata):
    metadata_path = "reports/metadata.json"
    metadata_db = storage.load_metadata(metadata_path)
    metadata_db[new_metadata["date"]] = new_metadata
    storage.save_metadata(metadata_path, metadata_db)

    # 戰績側欄、今日預告／休賽季區塊：任何失敗都不得影響戰報與 metadata（各區塊已各自隔離，這裡再兜底）
    extras = _safe_call("首頁補充區塊", build_index_extras) or _safe_call("首頁側欄錯誤版", fallback_index_extras)

    # 首頁每次由模板與 metadata 全量重新產生
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(render_index(metadata_db, extras))
    print("Successfully updated index.html index page.")

def should_notify(entry):
    """休賽季快報在新聞與人事異動皆為 0（缺欄位視為 0）時不發通知；其餘照常。"""
    if storage.entry_type(entry) == "offseason":
        return storage._to_int(entry.get("news_count")) > 0 or storage._to_int(entry.get("transactions_count")) > 0
    return True


def send_discord_notification(new_meta):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("Warning: DISCORD_WEBHOOK_URL env variable not set. Skipping Discord notification.")
        return
    
    date_str = new_meta["date"]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_formatted = dt.strftime("%Y 年 %m 月 %d 日")
    except:
        date_formatted = date_str
        
    parts = date_str.split('-')
    report_url = f"https://haolun588.github.io/MLB-daily/reports/{parts[0]}/{parts[1]}/{date_str}.html"
    
    if storage.entry_type(new_meta) == "offseason":
        parts_text = []
        if "news_count" in new_meta:
            parts_text.append(f"新聞 {storage._to_int(new_meta['news_count'])} 則")
        if "transactions_count" in new_meta:
            parts_text.append(f"人事異動 {storage._to_int(new_meta['transactions_count'])} 筆")
        description = "休賽季快報：" + "、".join(parts_text) if parts_text else "休賽季快報已生成。"
        fields = []
    else:
        description = "昨日焦點戰報與最新賽況已生成，點擊下方連結即可瀏覽完整內容！"
        if "news_count" in new_meta:
            description += f"\n新聞 {storage._to_int(new_meta['news_count'])} 則"
        fields = [
            {
                "name": "📊 賽事統計",
                "value": f"• 總場數：**{new_meta['total_games']}** 場\n• 延賽場數：**{new_meta['postponed']}** 場",
                "inline": True
            },
            {
                "name": "📝 焦點摘要",
                "value": new_meta["summary"]
            }
        ]
        
    payload = {
        "embeds": [
            {
                "title": f"⚾ MLB 每日焦點戰報 - {date_formatted}",
                "description": description,
                "url": report_url,
                "color": 795456,  # Yankees Blue: #0C2340
                "fields": fields,
                "footer": {
                    "text": "MLB Daily 每日戰報系統"
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            print(f"Discord notification sent successfully. Response code: {response.getcode()}")
    except Exception as e:
        print(f"Error sending Discord notification: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="MLB Daily Report Generator Script")
    parser.add_argument("--date", type=str, help="Target report date in YYYY-MM-DD format (defaults to yesterday in Taipei Time)")
    parser.add_argument("--check-wait", action="store_true", help="If active, loops and sleeps 30 minutes until yesterday's games are finished")
    parser.add_argument("--max-wait-checks", type=int, default=MAX_WAIT_CHECKS, help=f"Max number of checks in the --check-wait loop (default {MAX_WAIT_CHECKS})")
    
    parser.add_argument("--rosters", action="store_true", help="Fetch all 30 team rosters with season stats and write data/teams/*.json (no report, no notification)")
    parser.add_argument("--no-notify", action="store_true", help="Generate report and metadata but do not send Discord notification")
    parser.add_argument("--notify-only", action="store_true", help="Only send Discord notification from existing metadata (no API calls, no file writes)")

    args = parser.parse_args()
    if args.notify_only and (args.check_wait or args.no_notify):
        print("Error: --notify-only cannot be combined with --check-wait or --no-notify", file=sys.stderr)
        sys.exit(1)
    if args.rosters and (args.notify_only or args.check_wait):
        print("Error: --rosters cannot be combined with --notify-only or --check-wait", file=sys.stderr)
        sys.exit(1)
    if args.max_wait_checks < 1:
        print("Error: --max-wait-checks must be >= 1", file=sys.stderr)
        sys.exit(1)
    
    # --rosters: 只更新 data/teams，不產生戰報、不動 metadata、不發通知
    if args.rosters:
        if args.date:
            try:
                today = datetime.strptime(args.date, "%Y-%m-%d").date()
            except ValueError:
                print("Error: Date format must be YYYY-MM-DD", file=sys.stderr)
                sys.exit(1)
        else:
            today = timeutil.get_taipei_now().date()
        sys.exit(0 if rosters.fetch_rosters(today) > 0 else 1)

    # 1. Resolve date
    if args.date:
        date_str = args.date
        # Validate date format
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            print("Error: Date format must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        date_str = timeutil.get_yesterday_str()
        
    print(f"Target Date: {date_str}")

    # --notify-only: 僅依既有 metadata 發送通知，不呼叫 API、不寫檔
    if args.notify_only:
        entry = storage.load_metadata("reports/metadata.json").get(date_str)
        if not entry:
            print(f"Error: reports/metadata.json 中找不到 {date_str} 的資料，無法發送通知。", file=sys.stderr)
            sys.exit(1)
        if not should_notify(entry):
            print(f"{date_str} 為休賽季快報且無新聞、無人事異動，不發送 Discord 通知。")
            return
        send_discord_notification(entry)
        return

    # 2. Check wait loop if enabled
    if args.check_wait:
        max_checks = args.max_wait_checks
        print(f"Enabling check wait loop (max {max_checks} checks). Monitoring yesterday's games...")
        games = []
        have_games = False  # 是否曾成功取得賽程
        finalized = False
        for attempt in range(1, max_checks + 1):
            try:
                finalized, fetched_games = games_mod.check_all_games_finalized(date_str)
                games = fetched_games
                have_games = True
            except net.ApiError as e:
                print(f"Warning: {e}. Treating as not finalized.", file=sys.stderr)
                finalized = False
            if finalized:
                print("All games are finalized (Final, Postponed, Cancelled or Suspended). Proceeding to build.")
                break
            if attempt < max_checks:
                print(f"Some games are not finalized yet (check {attempt}/{max_checks}). Sleeping for 30 minutes...", flush=True)
                time.sleep(WAIT_SLEEP_SECONDS)
        if not finalized:
            if not have_games:
                print(f"Error: 已達等待上限（{max_checks} 次檢查）仍無法取得 {date_str} 的賽程。Aborting without generating any output.", file=sys.stderr)
                sys.exit(1)
            print(f"Warning: 已達等待上限（{max_checks} 次檢查），仍有未完成場次，將照常產出戰報，未完成場次標示為「未完賽」。", file=sys.stderr)
    else:
        try:
            finalized, games = games_mod.check_all_games_finalized(date_str)
        except net.ApiError as e:
            print(f"Error: {e}. Aborting without generating any output.", file=sys.stderr)
            sys.exit(1)
        if not finalized:
            print("Warning: Some games are still active or not finalized yet.", file=sys.stderr)

    # Check if there are games scheduled
    if not games:
        print(f"No games scheduled or found on {date_str}. Generating offseason report.")
        empty_meta = generate_offseason_report(date_str)
        update_index_page(empty_meta)
        if not args.no_notify:
            if should_notify(empty_meta):
                send_discord_notification(empty_meta)
            else:
                print("休賽季快報無新聞、無人事異動，不發送 Discord 通知。")
        sys.exit(0)

    # 3. Generate report files
    new_meta = generate_report(date_str, games)
    
    # 4. Update index homepage listings
    update_index_page(new_meta)
    
    # 5. Send Discord notification
    if not args.no_notify:
        send_discord_notification(new_meta)
    print("All tasks completed successfully.")

if __name__ == "__main__":
    main()
