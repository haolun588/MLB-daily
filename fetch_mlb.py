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
from mlb_daily import cli, config, games as games_mod, htmlutil, index_page, net, news as news_mod, notify, previews, report, rosters, standings as standings_mod, storage, timeutil, transactions as transactions_mod, translate  # noqa: F401
from mlb_daily.cli import (  # noqa: F401
    main,
)
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
from mlb_daily.index_page import (  # noqa: F401
    TEAMS_ENTRY_HTML,
    _fallback_roster_context,
    _offseason_counts,
    _safe_call,
    build_index_extras,
    fallback_index_extras,
    render_index,
    render_latest_card,
    render_recent_item,
    update_index_page,
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
from mlb_daily.notify import (  # noqa: F401
    send_discord_notification,
    should_notify,
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
from mlb_daily.report import (  # noqa: F401
    build_offseason_summary,
    generate_offseason_report,
    generate_report,
    render_games_section,
    render_report,
    render_search,
    render_summary,
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


if __name__ == "__main__":
    main()
