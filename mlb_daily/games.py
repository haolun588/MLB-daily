"""比賽狀態、焦點球員、比賽卡片與摘要。"""

from . import htmlutil, net, news as news_mod, transactions as transactions_mod
from .config import (
    CANCELLED_REASONS,
    GAME_TYPE_LABELS,
    LEGACY_ABBR_ALIASES,
    POSTPONED_REASONS,
    TEAM_META,
)


def check_all_games_finalized(date_str):
    """回傳 (finalized, games)。

    - API 失敗（fetch 回 None、回應結構不符）：拋出 ApiError。
    - 回應成功且確實無賽事（totalGames == 0 或 dates 為空）：回傳 (True, [])。
    - 有賽事：回傳 (是否全數完賽, games)。
    """
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date_str}&hydrate=linescore,decisions"
    data = net.fetch_json(url)
    if data is None:
        raise net.ApiError(f"無法取得 {date_str} 的賽程（API 無回應）")
    if not isinstance(data, dict) or not isinstance(data.get("dates"), list):
        raise net.ApiError(f"{date_str} 的賽程回應缺少預期結構（dates）")

    dates = data["dates"]
    if not dates:
        return True, []
    if not isinstance(dates[0], dict):
        raise net.ApiError(f"{date_str} 的賽程回應結構異常（dates[0]）")
    games = dates[0].get("games")
    if games is None:
        if data.get("totalGames") == 0:
            return True, []
        raise net.ApiError(f"{date_str} 的賽程回應缺少預期結構（games）")
    if not isinstance(games, list):
        raise net.ApiError(f"{date_str} 的賽程回應結構異常（games）")
    if not games:
        return True, []

    for game in games:
        state = game.get("status", {}).get("abstractGameState")
        detailed = game.get("status", {}).get("detailedState")
        # Games are NOT finalized if they are Preview or Live (excluding Postponed/Cancelled,
        # and Suspended games which will not resume on the same day)
        if state in ["Preview", "Live"] and detailed not in ["Postponed", "Cancelled"] and not (detailed or "").startswith("Suspended"):
            return False, games

    return True, games


# ---------------------------------------------------------------- 比賽狀態與分類
def game_type_label(game):
    """依 gameType 回傳卡片標題用的賽事類型文字。"""
    return GAME_TYPE_LABELS.get(game.get("gameType"), "比賽")


def classify_game_status(game, linescore=None):
    """判斷比賽狀態，回傳 (badge_text, badge_class, is_final)。

    badge_class 對應 CSS 徽章：final / extra（延長賽）/ early（提前結束）/ suspended。
    - 完賽：Final（9 局或與預定局數相同）、Final/N（延長賽，extra）、Final/N（提前結束，early）。
    - Suspended：暫停比賽；其餘仍為 Preview/Live（等待達上限）：未完賽。兩者 is_final 皆為 False。
    """
    if linescore is None:
        linescore = game.get("linescore", {})
    status = game.get("status", {})
    abstract = status.get("abstractGameState")
    detailed = status.get("detailedState") or ""

    if detailed.startswith("Suspended"):
        return "暫停比賽", "suspended", False
    if abstract != "Final" or detailed in ("Postponed", "Cancelled"):
        return "未完賽", "suspended", False

    scheduled = linescore.get("scheduledInnings") or 9
    played = len(linescore.get("innings", [])) or linescore.get("currentInning") or 0
    if played == 0 or played == scheduled:
        return "Final", "final", True
    if played > scheduled:
        return f"Final/{played}", "extra", True
    return f"Final/{played}（提前結束）", "early", True


# ---------------------------------------------------------------- 資料抽取（boxscore -> 焦點球員）
def format_cumulative(count, season_total):
    """單場次數與賽季累計：1(9) / 2(10)(11)。賽季累計缺漏或小於單場次數（資料異常）時只輸出次數。"""
    if count <= 0:
        return ""
    if not isinstance(season_total, int) or season_total < count:
        return str(count)
    if count == 1:
        return f"1({season_total})"
    return f"{count}" + "".join(f"({season_total - count + 1 + i})" for i in range(count))


def get_team_info(team_id, english_name):
    if team_id in TEAM_META:
        return TEAM_META[team_id]
    return {"name": english_name, "abbr": english_name[:3].upper(), "color": "#6b7280"}


def collect_candidates(boxscore, away_abbr, home_abbr):
    """從 boxscore 取出打者／投手候選人並評分（分數由高到低）。"""
    teams = boxscore.get("teams", {})
    starters = {
        side: (teams.get(side, {}).get("pitchers") or [None])[0] for side in ("away", "home")
    }
    candidates = []

    for side, team_abbr in (("away", away_abbr), ("home", home_abbr)):
        for _, p_info in (teams.get(side, {}).get("players") or {}).items():
            person = p_info.get("person", {})
            p_id = person.get("id")
            p_name = person.get("fullName", "")
            pos = p_info.get("position", {}).get("abbreviation", "")
            if p_id is None:
                continue

            # 打者：有打數，或雖無打數但有盜壘／得分（代跑者，L4）
            bat = p_info.get("stats", {}).get("batting", {})
            if bat:
                ab = bat.get("atBats", 0)
                r = bat.get("runs", 0)
                h = bat.get("hits", 0)
                d = bat.get("doubles", 0)
                t = bat.get("triples", 0)
                hr = bat.get("homeRuns", 0)
                rbi = bat.get("rbi", 0)
                bb = bat.get("baseOnBalls", 0)
                so = bat.get("strikeOuts", 0)
                sb = bat.get("stolenBases", 0)
                if ab > 0 or sb > 0 or r > 0:
                    # Score = H*1 + 2B*2 + 3B*3 + HR*4 + RBI*1.5 + R*1 + SB*1 + BB*1
                    score = h * 1.0 + d * 2.0 + t * 3.0 + hr * 4.0 + rbi * 1.5 + r * 1.0 + sb * 1.0 + bb * 1.0
                    season = p_info.get("seasonStats", {}).get("batting", {})
                    candidates.append({
                        "type": "batter", "id": p_id, "name": p_name, "team": team_abbr,
                        "pos": pos, "score": score,
                        "stats": {
                            "ab": ab, "r": r, "h": h, "rbi": rbi, "bb": bb, "so": so,
                            "d": d, "t": t, "hr": hr, "sb": sb,
                            "season_d": season.get("doubles", 0),
                            "season_t": season.get("triples", 0),
                            "season_hr": season.get("homeRuns", 0),
                            "season_sb": season.get("stolenBases", 0),
                        },
                    })

            # 投手
            pit = p_info.get("stats", {}).get("pitching", {})
            ip_str = pit.get("inningsPitched") if pit else None
            if ip_str and ip_str != "0.0":
                h = pit.get("hits", 0)
                r = pit.get("runs", 0)
                er = pit.get("earnedRuns", 0)
                bb = pit.get("baseOnBalls", 0)
                so = pit.get("strikeOuts", 0)
                parts = ip_str.split(".")
                ip_outs = int(parts[0]) * 3 + (int(parts[1]) if len(parts) > 1 else 0)
                is_win = pit.get("wins", 0) > 0
                is_save = pit.get("saves", 0) > 0
                is_loss = pit.get("losses", 0) > 0
                # Score = IP_outs + K*1 - ER*2 - H*1 - BB*1 + (Win ? 5 : 0) + (Save ? 4 : 0)
                score = ip_outs + so * 1.0 - er * 2.0 - h * 1.0 - bb * 1.0 + (5.0 if is_win else 0.0) + (4.0 if is_save else 0.0)
                decision = "W" if is_win else ("S" if is_save else ("L" if is_loss else ""))
                candidates.append({
                    "type": "pitcher", "id": p_id, "name": p_name, "team": team_abbr,
                    "pos": pos, "score": score, "is_starter": p_id == starters[side],
                    "decision": decision,
                    "stats": {
                        "ip": ip_str, "h": h, "r": r, "er": er, "bb": bb, "so": so,
                        "pitches": pit.get("numberOfPitches", 0), "strikes": pit.get("strikes", 0),
                    },
                })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def select_performers(candidates):
    """雙門檻：前 3 名保證入選；其餘打者 >= 7、先發 >= 15、後援 >= 8。回傳 (打者, 投手, 全部入選)。"""
    selected = []
    for idx, cand in enumerate(candidates):
        if idx < 3:
            selected.append(cand)
        elif cand["type"] == "batter":
            if cand["score"] >= 7.0:
                selected.append(cand)
        else:
            if cand["score"] >= (15.0 if cand["is_starter"] else 8.0):
                selected.append(cand)
    return (
        [c for c in selected if c["type"] == "batter"],
        [c for c in selected if c["type"] == "pitcher"],
        selected,
    )


def build_decisions(dec_obj, players_away, players_home):
    """投球決定：{'w'|'l'|'s': {'id','name','record'} 或 None}。"""
    def lookup(pid):
        obj = players_away.get(f"ID{pid}") or players_home.get(f"ID{pid}")
        if obj:
            return obj["person"]["fullName"], obj.get("seasonStats", {}).get("pitching", {})
        return None, None

    out = {"w": None, "l": None, "s": None}
    for key, field in (("w", "winner"), ("l", "loser"), ("s", "save")):
        entry = dec_obj.get(field)
        if not entry:
            continue
        pid = entry["id"]
        name, stats = lookup(pid)
        if name:
            if key == "s":
                record = f"({stats.get('saves', 0)})"
            else:
                record = f"({stats.get('wins', 0)}-{stats.get('losses', 0)})"
        else:
            name, record = entry.get("fullName", ""), None
        out[key] = {"id": pid, "name": name, "record": record}
    return out


# ---------------------------------------------------------------- 渲染：比賽卡片
def batter_tags(stats):
    """長打與速度標籤：[(css_class, 文字)]，順序 2B、3B、HR、SB。"""
    tags = []
    for label, key, season_key, css in (
        ("2B", "d", "season_d", "tag"),
        ("3B", "t", "season_t", "tag"),
        ("HR", "hr", "season_hr", "tag hr"),
        ("SB", "sb", "season_sb", "tag sb"),
    ):
        if stats[key] > 0:
            tags.append((css, f"{label}: {format_cumulative(stats[key], stats[season_key])}"))
    return tags


def _statline(pairs, extra_class=""):
    cells = "".join(f"<div><dt>{htmlutil.esc(k)}</dt><dd>{htmlutil.esc(v)}</dd></div>" for k, v in pairs)
    return f'<dl class="statline{extra_class}">{cells}</dl>'


def render_batter(b):
    s = b["stats"]
    tags = "".join(f'<span class="{css}">{htmlutil.esc(text)}</span>' for css, text in batter_tags(s))
    tags_html = f'<span class="tags">{tags}</span>' if tags else ""
    statline = _statline([("AB", s["ab"]), ("R", s["r"]), ("H", s["h"]), ("RBI", s["rbi"]), ("BB", s["bb"]), ("SO", s["so"])])
    return (
        f'<li><div class="p-head"><span class="p-name">{htmlutil.player_link(b["id"], b["name"])}</span>'
        f'<span class="p-meta">{htmlutil.esc(b["team"])} · {htmlutil.esc(b["pos"])}</span>{tags_html}</div>{statline}</li>'
    )


def render_pitcher(p):
    s = p["stats"]
    tags_html = f'<span class="tags"><span class="tag dec">{htmlutil.esc(p["decision"])}</span></span>' if p.get("decision") else ""
    statline = _statline(
        [("IP", s["ip"]), ("H", s["h"]), ("R", s["r"]), ("ER", s["er"]), ("BB", s["bb"]), ("SO", s["so"]),
         ("P-S", f"{s['pitches']}-{s['strikes']}")],
        " is-pitch",
    )
    return (
        f'<li><div class="p-head"><span class="p-name">{htmlutil.player_link(p["id"], p["name"])}</span>'
        f'<span class="p-meta">{htmlutil.esc(p["team"])} · {htmlutil.esc(p["pos"])}</span>{tags_html}</div>{statline}</li>'
    )


def render_decisions(decisions, is_final, status_text=""):
    """投球決定：只有完賽（is_final）才列出勝／敗／救援，否則顯示說明文字。"""
    if not is_final:
        note = "本場暫停，尚未產生勝敗，無投球決定。" if status_text == "暫停比賽" else "本場尚未完賽，無投球決定。"
        return f'<section><h4 class="sub">投球決定</h4><p class="empty-note">{note}</p></section>'

    def row(label, entry, win=False):
        dt = f'<dt class="is-w">{label}</dt>' if win else f"<dt>{label}</dt>"
        if not entry:
            return f"{dt}<dd>無</dd>"
        rec = f' <span class="rec">{htmlutil.esc(entry["record"])}</span>' if entry.get("record") else ""
        return f'{dt}<dd>{htmlutil.player_link(entry["id"], entry["name"])}{rec}</dd>'

    d = decisions or {}
    return (
        '<section><h4 class="sub">投球決定</h4><dl class="decisions">'
        + row("勝", d.get("w"), True) + row("敗", d.get("l")) + row("救援", d.get("s"))
        + "</dl></section>"
    )


def _card_open(view):
    away, home = view["away"], view["home"]
    tokens = []
    for side in (away, home):
        tokens += [side["abbr"], side["name"], side.get("name_en", "")]
        if side["abbr"] in LEGACY_ABBR_ALIASES:
            tokens.append(LEGACY_ABBR_ALIASES[side["abbr"]])
    teams = "|".join(str(t).replace("|", " ") for t in tokens)
    label = f'{away["name"]}對{home["name"]}'
    return f'<article class="game" data-teams="{htmlutil.esc(teams)}" aria-label="{htmlutil.esc(label)}">'


def _card_head(view):
    return (
        f'<div class="game-head"><h3 class="game-title">{htmlutil.esc(view["type_label"])} '
        f'<span class="num">#{htmlutil.esc(view["game_pk"])}</span></h3>'
        f'<span class="badge is-{htmlutil.esc(view["status_class"])}">{htmlutil.esc(view["status_text"])}</span></div>'
    )


def _score_table(view):
    rows = []
    for side in (view["away"], view["home"]):
        tr = '<tr class="is-win">' if side.get("winner") else "<tr>"
        rows.append(
            f'{tr}<td class="team{htmlutil.stripe_class(side["color"])}" style="--team:{htmlutil.safe_color(side["color"])}">'
            f'<div class="team-inner">{htmlutil.logo_picture(side["id"], side["abbr"], 24)}'
            f'<span class="tname"><span class="name">{htmlutil.esc(side["name"])}</span>'
            f'<span class="record">{htmlutil.esc(side["record"])}</span></span></div></td>'
            f'<td class="n runs">{htmlutil.esc(side["r"])}</td><td class="n">{htmlutil.esc(side["h"])}</td>'
            f'<td class="n">{htmlutil.esc(side["e"])}</td></tr>'
        )
    caption = f'{view["away"]["name"]}對{view["home"]["name"]}'
    return (
        f'<table class="score"><caption class="visually-hidden">{htmlutil.esc(caption)}</caption>'
        '<thead><tr><th scope="col">隊伍</th><th scope="col" class="col-r">R</th>'
        '<th scope="col" class="col-h">H</th><th scope="col" class="col-e">E</th></tr></thead>'
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def render_game_card(view):
    """完整比賽卡片（有 boxscore）。view 為 build_game_view() 的輸出加上 news、tx_groups。"""
    batters = "".join(render_batter(b) for b in view["batters"])
    pitchers = "".join(render_pitcher(p) for p in view["pitchers"])
    batters_html = f'<ul class="perf">{batters}</ul>' if batters else '<p class="empty-note">本場無突出打擊表現。</p>'
    pitchers_html = f'<ul class="perf">{pitchers}</ul>' if pitchers else '<p class="empty-note">本場無突出投手表現。</p>'
    decisions = render_decisions(view["decisions"], view["is_final"], view["status_text"])
    return (
        f"{_card_open(view)}\n  {_card_head(view)}\n  {_score_table(view)}\n"
        '  <div class="game-body">\n    <div class="col-left">\n      '
        f'{decisions}{news_mod.render_news_box(view.get("news"))}{transactions_mod.render_card_transactions(view.get("tx_groups"))}\n    </div>\n'
        '    <div class="col-right">\n'
        f'<div class="perf-group"><h4 class="sub">打擊焦點</h4>{batters_html}</div>\n'
        f'<div class="perf-group"><h4 class="sub">投球焦點</h4>{pitchers_html}</div>\n'
        "    </div>\n  </div>\n</article>"
    )


def render_fallback_card(view):
    """boxscore 抓取失敗時，僅用 schedule 資料的簡化卡片（H／E 無資料以 — 表示）。"""
    return (
        f"{_card_open(view)}\n  {_card_head(view)}\n  {_score_table(view)}\n"
        '  <div class="note-strip"><strong>詳細數據暫缺</strong>：本場 boxscore 暫時無法取得，僅顯示比分。</div>\n'
        "</article>"
    )


def render_postponed_card(view):
    """延賽／取消卡。view 需含 note_title、notice、icon。"""
    def side(s):
        return f'<span class="side">{htmlutil.logo_picture(s["id"], s["abbr"], 24)}{htmlutil.esc(s["name"])}</span>'
    return (
        f"{_card_open(view)}\n  {_card_head(view)}\n"
        f'  <div class="game-note">\n    <div class="icon" aria-hidden="true">{view["icon"]}</div>\n'
        f'    <h3>{htmlutil.esc(view["note_title"])}</h3>\n'
        f'    <div class="matchup">{side(view["away"])}<span class="at">@</span>{side(view["home"])}</div>\n'
        f'    <p>{htmlutil.esc(view["notice"])}</p>\n  </div>\n</article>'
    )


# ---------------------------------------------------------------- 組裝：schedule / boxscore -> view
def _side_view(game, side, info):
    team_obj = game["teams"][side]["team"]
    rec = game["teams"][side].get("leagueRecord", {})
    return {
        "id": team_obj["id"],
        "abbr": info["abbr"],
        "name": info["name"],
        "name_en": team_obj.get("name", ""),
        "color": info["color"],
        "record": f"({rec.get('wins', 0)}-{rec.get('losses', 0)})",
        "r": 0, "h": "—", "e": "—", "winner": False,
    }


def build_base_view(game, away_info, home_info):
    """狀態、隊伍與比分（來源為 schedule 的 linescore，缺漏時 R 退回 teams.score；H／E 缺漏為 —）。"""
    linescore = game.get("linescore", {})
    ls_teams = linescore.get("teams", {})
    status_text, status_class, is_final = classify_game_status(game, linescore)
    view = {
        "game_pk": game["gamePk"],
        "type_label": game_type_label(game),
        "status_text": status_text,
        "status_class": status_class,
        "is_final": is_final,
        "away": _side_view(game, "away", away_info),
        "home": _side_view(game, "home", home_info),
    }
    for side in ("away", "home"):
        ls = ls_teams.get(side, {})
        view[side]["r"] = ls.get("runs", game["teams"][side].get("score", 0))
        view[side]["h"] = ls.get("hits", "—")
        view[side]["e"] = ls.get("errors", "—")
    away_r, home_r = view["away"]["r"], view["home"]["r"]
    view["away"]["winner"] = bool(is_final and away_r > home_r)
    view["home"]["winner"] = bool(is_final and home_r > away_r)
    return view


def build_postponed_view(game, away_info, home_info):
    view = build_base_view(game, away_info, home_info)
    status = game.get("status", {})
    type_label = game_type_label(game)
    if status.get("detailedState") == "Cancelled":
        reason_raw = status.get("reason")
        reason_eng = f"Cancelled - {reason_raw}" if reason_raw else "Cancelled"
        if reason_raw in CANCELLED_REASONS:
            reason_chi = CANCELLED_REASONS[reason_raw]
        elif reason_raw:
            reason_chi = f"賽事取消（原因：{reason_raw}）"
        else:
            reason_chi = "賽事取消"
        view.update(
            status_text="Cancelled", status_class="cancelled", icon="🚫",
            note_title="賽事取消",
            notice=f"原定於當日舉行的{type_label}賽事，{reason_chi} ({reason_eng})。",
        )
    else:
        reason_eng = status.get("reason", "Postponed")
        reason_chi = POSTPONED_REASONS.get(reason_eng, "賽事延期")
        view.update(
            status_text="Postponed", status_class="postponed", icon="🌧️",
            note_title=reason_chi,
            notice=f"原定於當日舉行的{type_label}賽事，{reason_chi} (Postponed - {reason_eng})。補賽日期將另行公布。",
        )
    return view


def build_game_view(game, boxscore, away_info, home_info):
    """有 boxscore 的完整卡片資料。回傳 (view, 入選焦點球員清單)。"""
    view = build_base_view(game, away_info, home_info)
    teams = boxscore.get("teams", {})
    players_away = teams.get("away", {}).get("players", {})
    players_home = teams.get("home", {}).get("players", {})
    view["decisions"] = build_decisions(game.get("decisions", {}), players_away, players_home)

    candidates = collect_candidates(boxscore, away_info["abbr"], home_info["abbr"])
    print(f"  Game {game['gamePk']} ({away_info['abbr']} @ {home_info['abbr']}) candidates score log:")
    for cand in candidates[:5]:
        print(f"    - {cand['name']} ({cand['team']} - {cand['type']}): score={cand['score']:.1f}")
    batters, pitchers, selected = select_performers(candidates)
    view["batters"], view["pitchers"] = batters, pitchers
    return view, selected


def top_focus_score(selected):
    """該場焦點球員的最高積分（selected 已依積分由高到低）；無焦點球員為 0。"""
    return max((c["score"] for c in selected), default=0.0)


def pick_summary_sentences(entries, limit=3):
    """entries 為 [(焦點最高積分, 摘要句)]；取積分最高的 limit 場（同分維持原賽程順序）。"""
    ranked = sorted(entries, key=lambda e: -e[0])
    return [sentence for _, sentence in ranked[:limit]]


def build_summary_sentence(view, selected):
    sentence = f"{view['away']['abbr']} @ {view['home']['abbr']}，比分 {view['away']['r']}:{view['home']['r']}"
    if selected:
        top = selected[0]
        if top["type"] == "batter" and top["stats"]["hr"] > 0:
            sentence += f"，{top['name']} 擊出全壘打"
        elif top["type"] == "pitcher" and top["stats"]["so"] >= 5:
            sentence += f"，{top['name']} 投出 {top['stats']['so']} 次三振"
    return sentence
