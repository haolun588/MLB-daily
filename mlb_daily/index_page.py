"""首頁的產生與更新。"""

import sys

from . import htmlutil, net, previews, rosters, standings as standings_mod, storage, timeutil, transactions as transactions_mod, translate, watchlist
from .config import DATE_KEY_RE, SEASON_URL, WEEK_KEY_RE


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
            _safe_call("最新週報", render_latest_weekly_link, None, today) or "",
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


WEEKLY_LINK_MAX_AGE_DAYS = 14


def render_latest_weekly_link(entry=None, today=None):
    """首頁休賽季區塊的「最新週報」連結卡（讀 reports/weekly/index.json 最新一週）；沒有週報回傳空字串。
    連結一律由週次組出（不信任索引內的 path），週次格式不符視為沒有；該週 end 距 today 超過 14 天不顯示（過舊）。"""
    entry = entry if entry is not None else storage.latest_weekly_entry()
    if not entry or not WEEK_KEY_RE.match(str(entry.get("week"))):
        return ""
    week = entry["week"]
    start, end = timeutil._parse_iso_date(entry.get("start")), timeutil._parse_iso_date(entry.get("end"))
    today = today or timeutil.get_taipei_now().date()
    if end is not None and (today - end).days > WEEKLY_LINK_MAX_AGE_DAYS:
        return ""
    rng = f"（{start.month}/{start.day}～{end.month}/{end.day}）" if start and end else ""
    sub = []
    for key, label, unit in (("tx_count", "異動", "筆"), ("trade_count", "交易", "筆"), ("signing_count", "簽約", "筆")):
        if key in entry:
            sub.append(f"{label} {storage._to_int(entry[key])} {unit}")
    if "days" in entry:
        sub.append(f"涵蓋 {storage._to_int(entry['days'])}／7 天")
    sub_txt = "、".join(sub) or "休賽季每週人事異動與新聞總整理"
    return (
        '  <section class="section" aria-labelledby="wkly-h">\n'
        '    <div class="section-head"><h2 id="wkly-h">每週總整理</h2><span class="count">休賽季</span></div>\n'
        f'    <a class="teams-entry" href="{htmlutil.esc(timeutil.weekly_href(week))}">\n'
        f'      <span><strong>最新週報：{htmlutil.esc(week)}{htmlutil.esc(rng)}</strong>'
        f'<span class="sub-txt">{htmlutil.esc(sub_txt)}</span></span>\n'
        '      <span class="go" aria-hidden="true">閱讀週報 →</span>\n'
        '    </a>\n  </section>')


TEAMS_ENTRY_HTML = """  <a class="teams-entry" href="teams.html">
    <span><strong>球隊名單</strong><span class="sub-txt">30 隊球員名單與本季數據，依守備位置分組</span></span>
    <span class="go" aria-hidden="true">30 隊名單 →</span>
  </a>"""


def render_spotlight_slot(metadata):
    """首頁「台日韓球員動態」：最新戰報日期往回 7 天（含最新日）的 metadata spotlight 彙整。
    名單讀取失敗、無任何戰報或任何例外都回傳空字串（區塊不顯示）。"""
    wl = watchlist.load_watchlist()
    if not wl:
        return ""
    entries = _safe_call("台日韓球員專區", watchlist.home_window_entries, metadata)
    return _safe_call("台日韓球員專區渲染", watchlist.render_spotlight_home, entries, wl) or ""


def render_index(metadata, extras=None):
    """首頁：最新一期大卡 + 左欄補充區塊（extras）+ 球隊名單入口 + 近期 6 期（不含最新一期）+ 戰績側邊欄。
    每次由 templates/index.html 全量重新產生。extras 為 build_index_extras 的結果（None 時不輸出側欄與補充區塊）。"""
    extras = extras or {}
    side_slots = {
        "UPCOMING": extras.get("UPCOMING", ""),
        "STANDINGS": extras.get("STANDINGS", ""),
        "STANDINGS_UI": extras.get("STANDINGS_UI", ""),
        "TEAMS": TEAMS_ENTRY_HTML,
        "SPOTLIGHT": render_spotlight_slot(metadata),
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
    _write_index(metadata_db)


def refresh_index_page():
    """不新增 metadata，僅依既有 reports/metadata.json 重新產生首頁（週報產生後讓首頁立即出現週報連結）。"""
    _write_index(storage.load_metadata("reports/metadata.json"))


def _write_index(metadata_db):
    # 戰績側欄、今日預告／休賽季區塊：任何失敗都不得影響戰報與 metadata（各區塊已各自隔離，這裡再兜底）
    extras = _safe_call("首頁補充區塊", build_index_extras) or _safe_call("首頁側欄錯誤版", fallback_index_extras)

    # 首頁每次由模板與 metadata 全量重新產生
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(render_index(metadata_db, extras))
    print("Successfully updated index.html index page.")
