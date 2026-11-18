"""每日戰報與休賽季快報的產生。"""

import sys

from . import games as games_mod, htmlutil, net, news as news_mod, storage, timeutil, transactions as transactions_mod, translate, watchlist


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
def render_report(date_str, page_type, cards_html=(), postponed_count=0, tx_list=(), news=None, spotlight_html=""):
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
        "SPOTLIGHT": spotlight_html,
        "NEWS": news_mod.render_news_section(news, page_type),
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


def _spotlight_build(date_str, wl, tx_matches, news_matches, boxscores=()):
    """翻譯完成後組成 metadata 用的 spotlight 清單與戰報區塊 HTML；失敗回傳 ([], "")。"""
    try:
        lines = watchlist.extract_game_lines(boxscores, wl)
        entries = watchlist.build_spotlight(date_str, wl, tx_matches, news_matches, lines)
        return entries, watchlist.render_spotlight_section(entries, wl)
    except Exception as e:  # noqa: BLE001
        print(f"Warning: 台日韓球員專區產生失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        return [], ""


def generate_report(date_str, games):
    print(f"Generating report for {date_str} with {len(games)} games...")
    total_games = len(games)
    postponed_count = 0
    cards = []  # str（已渲染）或 dict（待翻譯後才渲染的完整卡片 view）
    game_views = []
    key_matchups_summary = []

    raw_tx = transactions_mod.fetch_raw_transactions(date_str)
    tx_list = transactions_mod.filter_major_league_transactions(raw_tx)
    transactions_count = len(tx_list)
    news_all = news_mod.fetch_all_daily_news(date_str)
    news = None if news_all is None else news_all[:12]
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
    translated = translate.apply_translations(news, game_views, tx_items + tx_matches, team_names, extra_news)
    cards_html = [c if isinstance(c, str) else games_mod.render_game_card(c) for c in cards]
    spotlight, spotlight_html = _spotlight_build(date_str, wl, tx_matches, news_matches, boxscores)

    storage.write_report(date_str, render_report(date_str, "season", cards_html, postponed_count, tx_list, news,
                                                 spotlight_html))

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
    raw_tx = transactions_mod.fetch_raw_transactions(date_str)
    tx_list = transactions_mod.filter_major_league_transactions(raw_tx)
    news_all = news_mod.fetch_all_daily_news(date_str)
    news = None if news_all is None else news_all[:12]
    wl = watchlist.load_watchlist()
    tx_matches, news_matches, extra_news = _spotlight_matches(wl, raw_tx, news_all, news)
    translated = translate.apply_translations(news, [], tx_list + tx_matches, extra_news=extra_news)
    spotlight, spotlight_html = _spotlight_build(date_str, wl, tx_matches, news_matches)
    storage.write_report(date_str, render_report(date_str, "offseason", tx_list=tx_list, news=news,
                                                 spotlight_html=spotlight_html))
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
    if spotlight:  # 有內容才寫入
        meta["spotlight"] = spotlight
    return meta
