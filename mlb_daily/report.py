"""每日戰報、休賽季快報與休兵日快報的產生。"""

import sys

from . import analysis as analysis_mod, games as games_mod, htmlutil, net, news as news_mod, storage, timeutil, transactions as transactions_mod, translate, watchlist
from .config import DAILY_SNAPSHOT_SCHEMA


# ---------------------------------------------------------------- 渲染：總覽與區塊
def render_summary(page_type, total_games=0, postponed=0, news_count=None, transactions_count=0, rest_reason=""):
    """news_count 為 None（新聞功能尚未接上）時，休賽季／休兵日總覽省略「新聞」一格；休兵日第一格為賽程（rest_reason）。"""
    if page_type in ("offseason", "rest_day"):
        items = []
        if page_type == "rest_day":
            items.append(f"<div><dt>賽程</dt><dd>{htmlutil.esc(rest_reason)}</dd></div>")
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
def render_report(date_str, page_type, cards_html=(), postponed_count=0, tx_list=(), news=None, spotlight_html="",
                  analysis=None, rest_reason=""):
    """賽季戰報、休賽季快報與休兵日快報共用：組出所有插槽後交給 render_page。"""
    cards_html = list(cards_html)
    tx_list = list(tx_list)
    if page_type in ("offseason", "rest_day"):
        subtitle, title_suffix = ("休兵日", " 休兵日") if page_type == "rest_day" else ("休賽季快報", " 休賽季快報")
        summary = render_summary(
            page_type, news_count=None if news is None else len(news), transactions_count=len(tx_list),
            rest_reason=rest_reason)
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
        "SPOTLIGHT": spotlight_html,
        "NEWS": news_mod.render_news_section(news, page_type),
        "ANALYSIS": analysis_mod.render_analysis_section(analysis),
        "GAMES": games,
        "TRANSACTIONS": transactions,
        "GENERATED_AT": htmlutil.esc(timeutil.get_taipei_now().strftime("%Y-%m-%d %H:%M")),
    })


# ---------------------------------------------------------------- 台日韓球員專區
def _spotlight_matches(wl, raw_tx, news_all, news_shown):
    """名單比對（用未過濾的異動與當日全部新聞）；任何例外只印警告，專區視為無內容。
    回傳 (異動 matches, 新聞 matches, 不在顯示清單內的名單新聞)。"""
    try:
        tx_matches = watchlist.match_transactions(raw_tx, wl)
        news_matches = watchlist.match_news(news_all, wl)
        extra_news = [n for n, _ in news_matches if not any(n is s for s in news_shown or [])]
        return tx_matches, news_matches, extra_news
    except Exception as e:  # noqa: BLE001 - 專區失敗不得影響戰報
        print(f"Warning: 台日韓球員專區比對失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        return [], [], []


def _spotlight_build(date_str, wl, tx_matches, news_matches, boxscores=(), minor=False):
    """翻譯完成後組成 metadata 用的 spotlight 清單與戰報區塊 HTML；失敗回傳 ([], "")。
    minor=True（賽季戰報）時另加入台灣球員當日小聯盟比賽成績。"""
    try:
        lines = watchlist.extract_game_lines(boxscores, wl)
        if minor:
            try:
                lines += watchlist.fetch_minor_game_lines(date_str, wl)
            except Exception as e:  # noqa: BLE001 - 小聯盟成績失敗不得影響專區其餘內容
                print(f"Warning: 台灣球員小聯盟成績產生失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        entries = watchlist.build_spotlight(date_str, wl, tx_matches, news_matches, lines)
        return entries, watchlist.render_spotlight_section(entries, wl)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: 台日韓球員專區產生失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        return [], ""


# ---------------------------------------------------------------- 每日資料快照（供休賽季每週總整理）
def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else (None if value is None else str(value))


def snapshot_transaction(tx):
    """單筆人事異動轉成每日快照的欄位（build_daily_snapshot 與 backfill 共用）。"""
    return {
        "type_code": tx.get("type_code"), "date": tx.get("date"),
        "description": tx.get("description"), "description_zh": tx.get("description_zh"),
        "person_id": tx.get("person_id"), "person_name": tx.get("person_name"),
        "persons": [{"id": p.get("id"), "name": p.get("name")} for p in transactions_mod._tx_persons(tx)],
        "to_team_id": tx.get("to_team_id"), "from_team_id": tx.get("from_team_id"),
        "team_ids": tx.get("team_ids"), "legs": tx.get("legs"),
    }


def build_daily_snapshot(date_str, has_games, tx_list, news):
    """data/daily/YYYY-MM-DD.json 的內容（schema 1）：戰報實際顯示、已翻譯的大聯盟異動與新聞。
    news 為 None（抓取失敗）時存空清單。"""
    return {
        "schema": DAILY_SNAPSHOT_SCHEMA,
        "date": date_str,
        "has_games": bool(has_games),
        "transactions": [snapshot_transaction(tx) for tx in tx_list],
        "news": [
            {
                "title": n.get("title"), "title_zh": n.get("title_zh"), "link": n.get("link"),
                "published": _iso(n.get("published")), "image": n.get("image"), "summary": n.get("summary"),
            }
            for n in (news or [])
        ],
    }


def _save_daily_snapshot(date_str, has_games, tx_list, news):
    """寫入每日快照；任何失敗只印警告，不影響戰報。"""
    try:
        storage.write_daily_snapshot(date_str, build_daily_snapshot(date_str, has_games, tx_list, news))
    except Exception as e:  # noqa: BLE001 - 快照失敗不得影響戰報
        print(f"Warning: 每日資料快照產生失敗（{type(e).__name__}: {e}）。", file=sys.stderr)


def generate_report(date_str, games):
    print(f"Generating report for {date_str} with {len(games)} games...")
    total_games = len(games)
    postponed_count = 0
    cards = []  # str（已渲染）或 dict（待翻譯後才渲染的完整卡片 view）
    game_views = []
    key_matchups_summary = []

    raw_tx = transactions_mod.fetch_raw_transactions(date_str)
    tx_list = transactions_mod.filter_major_league_transactions(raw_tx)
    snapshot_tx = list(tx_list)  # 之後 tx_list 會被拆給各場卡片；快照要存完整清單（同一批 dict，翻譯會寫入其中）
    transactions_count = len(tx_list)
    news_all = news_mod.fetch_all_daily_news(date_str)
    news = None if news_all is None else news_all[:12]
    analysis = analysis_mod.fetch_daily_analysis(date_str)
    wl = watchlist.load_watchlist()
    tx_matches, news_matches, extra_news = _spotlight_matches(wl, raw_tx, news_all, news)
    boxscores = []

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

        boxscores.append(boxscore)
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
    translated = translate.apply_translations(news, game_views, tx_items + tx_matches, team_names, extra_news, analysis=analysis)
    cards_html = [c if isinstance(c, str) else games_mod.render_game_card(c) for c in cards]
    spotlight, spotlight_html = _spotlight_build(date_str, wl, tx_matches, news_matches, boxscores, minor=True)

    storage.write_report(date_str, render_report(date_str, "season", cards_html, postponed_count, tx_list, news,
                                                 spotlight_html, analysis))
    _save_daily_snapshot(date_str, True, snapshot_tx, news)

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
    if spotlight:  # 有內容才寫入
        meta["spotlight"] = spotlight
    return meta


def _build_no_game_summary(news, transactions_count, label):
    """無賽事日 metadata 摘要（純文字，渲染時另行 escape）：優先用前 3 則新聞標題（已翻譯用譯文）。"""
    titles = [(n.get("title_zh") or n["title"]).strip() for n in (news or [])[:3]]
    titles = [t for t in titles if t]
    if titles:
        return "；".join(titles) + "。"
    if transactions_count > 0:
        return f"{label}：人事異動 {transactions_count} 筆"
    return f"{label}，無重大消息"


def build_offseason_summary(news, transactions_count):
    return _build_no_game_summary(news, transactions_count, "休賽季")


def build_rest_day_summary(news, transactions_count):
    return _build_no_game_summary(news, transactions_count, "休兵日")


def _generate_no_game_report(date_str, page_type, rest_reason=""):
    """無賽事日：產出快報頁面（新聞＋大聯盟人事異動），回傳 metadata。page_type 為 "offseason" 或 "rest_day"。"""
    raw_tx = transactions_mod.fetch_raw_transactions(date_str)
    tx_list = transactions_mod.filter_major_league_transactions(raw_tx)
    news_all = news_mod.fetch_all_daily_news(date_str)
    news = None if news_all is None else news_all[:12]
    analysis = analysis_mod.fetch_daily_analysis(date_str)
    wl = watchlist.load_watchlist()
    tx_matches, news_matches, extra_news = _spotlight_matches(wl, raw_tx, news_all, news)
    translated = translate.apply_translations(news, [], tx_list + tx_matches, extra_news=extra_news, analysis=analysis)
    spotlight, spotlight_html = _spotlight_build(date_str, wl, tx_matches, news_matches)
    storage.write_report(date_str, render_report(date_str, page_type, tx_list=tx_list, news=news,
                                                 spotlight_html=spotlight_html, analysis=analysis,
                                                 rest_reason=rest_reason))
    _save_daily_snapshot(date_str, False, tx_list, news)  # has_games：當日無比賽（休兵日亦然）
    summary = (build_rest_day_summary if page_type == "rest_day" else build_offseason_summary)(news, len(tx_list))
    meta = {
        "date": date_str,
        "total_games": 0,
        "postponed": 0,
        "summary": summary,
        "type": page_type,
        "transactions_count": len(tx_list),
        "translated": translated,
    }
    if page_type == "rest_day":
        meta["rest_reason"] = rest_reason
    if news is not None:  # 抓取失敗時不寫 news_count
        meta["news_count"] = len(news)
    if spotlight:  # 有內容才寫入
        meta["spotlight"] = spotlight
    return meta


def generate_offseason_report(date_str):
    """無賽事日：產出「休賽季快報」頁面（新聞＋大聯盟人事異動），回傳 metadata。"""
    return _generate_no_game_report(date_str, "offseason")


def generate_rest_day_report(date_str, reason):
    """賽季中無賽事日：產出「休兵日」頁面（內容同休賽季快報，總覽第一格為賽程原因），回傳 metadata。"""
    return _generate_no_game_report(date_str, "rest_day", reason)
