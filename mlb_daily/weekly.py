"""休賽季每週總整理：讀取 data/daily 每日快照，分類統計並產出 reports/weekly/YYYY-Www.html。

層級與 report 同層：只依賴 config、timeutil、htmlutil、storage、transactions、news、watchlist、rosters，
不 import report／cli／index_page／notify。資料全部來自每日快照，不重抓 API、不重新翻譯。"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta

from . import htmlutil, news as news_mod, rosters, storage, timeutil, transactions as transactions_mod, watchlist
from .config import (
    DAILY_SNAPSHOT_DIR,
    DAILY_SNAPSHOT_SCHEMA,
    DATE_KEY_RE,
    NEWS_SOURCE,
    OFFSEASON_CALENDAR_PATH,
    TEAM_META,
    WEEK_KEY_RE,
    WEEKLY_CATEGORIES,
    WEEKLY_HIGHLIGHT_CATEGORIES,
    WEEKLY_MINOR_CONTRACT_RE,
    WEEKLY_NEWS_HIGHLIGHT_BONUS,
    WEEKLY_NEWS_LIMIT,
    WEEKLY_NEWS_WATCHLIST_BONUS,
    WEEKLY_TX_EXTENSION_RE,
    WEEKLY_TYPE_CATEGORY,
)

CATEGORY_LABELS = dict(WEEKLY_CATEGORIES)
# 「join」類（加入）與「leave」類（離開）：只有單一球隊欄位時依分類決定方向
_LEAVE_CATEGORIES = ("dfa", "free_agent")


# ---------------------------------------------------------------- 週次
def parse_week(label):
    """'2026-W47' -> 該 ISO 週的週一（date）。格式或週次不存在（如非 53 週年的 W53）拋 ValueError。"""
    m = WEEK_KEY_RE.match(str(label or "").strip())
    if not m:
        raise ValueError(f"週次格式必須為 YYYY-Www（例：2026-W47），收到：{label!r}")
    try:
        return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    except ValueError:
        raise ValueError(f"不存在的 ISO 週次：{label}") from None


def week_label(day):
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def week_days(label):
    """該 ISO 週的 7 個日期（週一至週日）。"""
    monday = parse_week(label)
    return [monday + timedelta(days=i) for i in range(7)]


def previous_week_label(today):
    """today 之前最近一個完整的 ISO 週（週一跑時即「上一週」）。"""
    return week_label(today - timedelta(days=today.isoweekday()))


# ---------------------------------------------------------------- 讀取每日快照
def _clean_tx(tx):
    """persons 內非 dict 的項目濾掉（其餘欄位原樣保留）。"""
    persons = tx.get("persons")
    if persons is None or (isinstance(persons, list) and all(isinstance(p, dict) for p in persons)):
        return tx
    return {**tx, "persons": [p for p in persons if isinstance(p, dict)] if isinstance(persons, list) else []}


def load_week_snapshots(days, directory=None):
    """讀取 days（date 清單）對應的 data/daily/YYYY-MM-DD.json，回傳 {日期字串: 快照}。
    沒有檔案的日子略過；JSON 損壞、schema 或日期不符時印警告並略過。快照內非 dict 的項目濾掉。"""
    directory = directory or DAILY_SNAPSHOT_DIR
    snapshots = {}
    for d in days:
        key = d.isoformat()
        path = os.path.join(directory, f"{key}.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f"Warning: 每日快照 {path} 讀取失敗（{type(e).__name__}: {e}），略過。", file=sys.stderr)
            continue
        if not isinstance(data, dict) or data.get("schema") != DAILY_SNAPSHOT_SCHEMA or data.get("date") != key:
            print(f"Warning: 每日快照 {path} 格式不符（schema 或日期），略過。", file=sys.stderr)
            continue
        txs = data.get("transactions")
        news = data.get("news")
        snapshots[key] = {
            "date": key,
            "has_games": bool(data.get("has_games")),
            "transactions": [_clean_tx(t) for t in txs if isinstance(t, dict) and isinstance(t.get("description"), str)
                             and t["description"]] if isinstance(txs, list) else [],
            "news": [n for n in news if isinstance(n, dict) and isinstance(n.get("title"), str)
                     and isinstance(n.get("link"), str)] if isinstance(news, list) else [],
        }
    return snapshots


# ---------------------------------------------------------------- 分類
def classify_weekly_transaction(tx):
    """回傳分類 key（見 config.WEEKLY_CATEGORIES）；不列入週報者回傳 None。
    判斷順序：延長合約（描述含 extension）優先於 SFA；SFA 含 minor league contract 不列入（防禦判斷，
    實務上快照已排除小聯盟合約）。"""
    desc = str(tx.get("description") or "").lower()
    if WEEKLY_TX_EXTENSION_RE.search(desc):
        return "extension"
    key = WEEKLY_TYPE_CATEGORY.get(tx.get("type_code"))
    if key == "signing" and WEEKLY_MINOR_CONTRACT_RE.search(desc):
        return None
    return key


def _tx_date(tx, fallback):
    d = str(tx.get("date") or "")
    return d if DATE_KEY_RE.match(d) else fallback


def _team_id(value):
    tid = htmlutil._int_id(value)
    return tid if tid in TEAM_META else None


def team_moves(item):
    """該筆異動對各隊的影響 [(team_id, 'join'|'leave')]（只含 30 隊；交易雙方各記一次）。"""
    tx = item["tx"]
    to_id, from_id = _team_id(tx.get("to_team_id")), _team_id(tx.get("from_team_id"))
    if item["category"] in _LEAVE_CATEGORIES:
        # 真實 API：DES／DFA／REL 只有 toTeam（指球員離開的那隊）；OUT 為 toTeam=小聯盟、fromTeam=大聯盟。
        # 依 from、to 順序取第一個 30 隊之一，記為離開。
        for tid in (from_id, to_id):
            if tid is not None:
                return [(tid, "leave")]
        return []
    moves = []
    if to_id is not None and to_id == from_id:  # 兩欄同一隊：只記一次
        return [(to_id, "join")]
    if to_id is not None:
        moves.append((to_id, "join"))
    if from_id is not None:
        moves.append((from_id, "leave"))
    return moves


# ---------------------------------------------------------------- 彙整一週資料
def _collect_items(snapshots):
    """所有快照的異動，依 (日期, 描述) 去重並分類；回傳列入週報（非「不列入」）的 items，日期新到舊（同日維持快照內順序）。"""
    items, seen = [], set()
    for key in sorted(snapshots, reverse=True):
        for tx in snapshots[key]["transactions"]:
            day = _tx_date(tx, key)
            if (day, tx["description"]) in seen:
                continue
            seen.add((day, tx["description"]))
            category = classify_weekly_transaction(tx)
            if category is not None:
                items.append({"tx": tx, "category": category, "date": day})
    items.sort(key=lambda it: it["date"], reverse=True)  # sort 穩定
    return items


def build_team_activity(items):
    """各隊動態：每隊 {team_id, count, join, leave, busiest}。
    依異動筆數（同一筆對同一隊只算一次）多到少，同數依球隊 id；筆數最多者（可並列）busiest 為 True。"""
    teams = {}
    for it in items:
        for tid, direction in team_moves(it):
            t = teams.setdefault(tid, {"team_id": tid, "join": [], "leave": [], "_seen": set()})
            t[direction].append(it)
            t["_seen"].add(id(it))
    out = []
    for t in teams.values():
        t["count"] = len(t.pop("_seen"))
        out.append(t)
    out.sort(key=lambda t: (-t["count"], t["team_id"]))
    top = out[0]["count"] if out else 0
    for t in out:
        t["busiest"] = top >= 2 and t["count"] == top  # 最高筆數至少 2 才標（並列都標）
    return out


def _name_pattern(names):
    names = sorted({n for n in names if isinstance(n, str) and n.strip()}, key=len, reverse=True)
    if not names:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in names) + r")(?!\w)", re.IGNORECASE)


def _published_ts(value):
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def select_news(snapshots, wl, highlight_items, limit=WEEKLY_NEWS_LIMIT):
    """新聞精選：依 link 去重；提到台日韓追蹤球員 +2、提到本週重點異動球員 +1；同分新到舊；最多 limit 則。
    回傳 [{"news": 原新聞, "score", "watch_ids"}]。"""
    highlight_names = [p.get("name") for it in highlight_items for p in transactions_mod._tx_persons(it["tx"])]
    highlight_re = _name_pattern(highlight_names)
    seen, cands = set(), []
    for key in sorted(snapshots, reverse=True):
        for n in snapshots[key]["news"]:
            link = n["link"].strip()
            if not link or link in seen:
                continue
            seen.add(link)
            text = f"{n.get('title', '')} {n.get('summary') or ''}"
            watch_ids = watchlist.players_in_text(text, wl)
            score = (WEEKLY_NEWS_WATCHLIST_BONUS if watch_ids else 0) \
                + (WEEKLY_NEWS_HIGHLIGHT_BONUS if highlight_re and highlight_re.search(text) else 0)
            cands.append({"news": n, "score": score, "watch_ids": watch_ids, "ts": _published_ts(n.get("published"))})
    cands.sort(key=lambda c: (-c["score"], -c["ts"]))
    return cands[:limit]


def build_week(label, snapshots, wl=None):
    """彙整一週資料（純資料，不渲染）。"""
    wl = wl or {}
    days = week_days(label)
    items = _collect_items(snapshots)
    by_cat = {key: [it for it in items if it["category"] == key] for key, _ in WEEKLY_CATEGORIES}
    highlights = [it for it in items if it["category"] in WEEKLY_HIGHLIGHT_CATEGORIES]
    return {
        "label": label,
        "start": days[0],
        "end": days[-1],
        "covered": sorted(snapshots),
        "days": len(snapshots),
        "items": items,
        "by_cat": by_cat,
        "highlights": highlights,
        "teams": build_team_activity(items),
        "news": select_news(snapshots, wl, highlights),
        "stats": {
            "tx_count": len(items),
            "trade_count": len(by_cat["trade"]),
            "signing_count": len(by_cat["signing"]) + len(by_cat["extension"]),  # 大聯盟合約簽約＋延長合約
        },
    }


# ---------------------------------------------------------------- 下週日程設定檔
def load_calendar(path=None):
    """讀取 settings/offseason_calendar.json，回傳 [{"date": date, "title_zh", "note"}]。
    讀檔失敗、格式錯誤只印警告並回傳 []；個別格式錯誤的事件略過。"""
    path = path or OFFSEASON_CALENDAR_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data["events"]
        if not isinstance(raw, list):
            raise ValueError("events 不是陣列")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"Warning: 無法讀取休賽季日程 {path}（{type(e).__name__}: {e}），下週日程視為空。", file=sys.stderr)
        return []
    events = []
    for ev in raw:
        d = timeutil._parse_iso_date(ev.get("date")) if isinstance(ev, dict) and DATE_KEY_RE.match(str(ev.get("date"))) else None
        title = ev.get("title_zh") if isinstance(ev, dict) else None
        if d is None or not isinstance(title, str) or not title.strip():
            print(f"Warning: 休賽季日程中略過格式錯誤的項目：{str(ev)[:80]}", file=sys.stderr)
            continue
        note = ev.get("note")
        events.append({"date": d, "title_zh": title.strip(), "note": note.strip() if isinstance(note, str) else ""})
    return events


def upcoming_events(events, week_end):
    """「下週」（週報最後一天的隔天起 7 天，即下一個週一到週日）內的事件，依日期排序。"""
    start = week_end + timedelta(days=1)
    end = week_end + timedelta(days=7)
    return sorted((e for e in events if start <= e["date"] <= end), key=lambda e: e["date"])


# ---------------------------------------------------------------- 渲染
def _md(d):
    return f"{d.month}/{d.day}"


def fmt_runs(dates):
    """日期清單壓成連續區間：[11/18, 11/19, 11/22] -> '11/18～11/19、11/22'。"""
    ds = sorted(dates)
    runs, cur = [], []
    for d in ds:
        if cur and d - cur[-1] == timedelta(days=1):
            cur.append(d)
        else:
            if cur:
                runs.append(cur)
            cur = [d]
    if cur:
        runs.append(cur)
    return "、".join(_md(r[0]) if len(r) == 1 else f"{_md(r[0])}～{_md(r[-1])}" for r in runs)


def fmt_range_long(start, end):
    left = f"{start.year} 年 {start.month} 月 {start.day} 日"
    right = f"{end.month} 月 {end.day} 日" if start.year == end.year else f"{end.year} 年 {end.month} 月 {end.day} 日"
    return f"{left}～{right}"


def _region_badges(ids, wl):
    return "".join(
        f'<span class="badge is-region" title="{htmlutil.esc(wl[pid]["name_zh"])}">'
        f'{htmlutil.esc(watchlist.REGION_NAMES[wl[pid]["region"]])} {htmlutil.esc(wl[pid]["name_zh"])}</span>'
        for pid in ids)


def _tx_text(tx):
    return transactions_mod.render_tx_description(
        transactions_mod._tx_display_text(tx), persons=transactions_mod._tx_persons(tx))


def _render_tx_row(item, wl):
    """本週重點用的一列：日期、球隊（隊色條）、描述、台日韓徽章。"""
    tx = item["tx"]
    team_id = next((t for t in (_team_id(tx.get("to_team_id")), _team_id(tx.get("from_team_id"))) if t is not None), None)
    style = cls = team_html = ""
    if team_id is not None:
        meta = TEAM_META[team_id]
        cls = htmlutil.stripe_class(meta["color"])
        style = f' style="--team:{htmlutil.safe_color(meta["color"])}"'
        team_html = (f'{htmlutil.logo_picture(team_id, meta["abbr"], 18, alt="")}'
                     f'<span class="wk-team">{htmlutil.esc(meta["name"])}</span>')
    d = timeutil._parse_iso_date(item["date"])
    date_html = f'<time class="num" datetime="{d.isoformat()}">{_md(d)}</time>' if d else ""
    badges = _region_badges(watchlist.players_in_transaction(tx, wl), wl)
    label = f'<span class="badge">{htmlutil.esc(CATEGORY_LABELS[item["category"]])}</span> '
    return (f'      <li class="wk-tx{cls}"{style}><span class="wk-meta">{date_html}{team_html}</span>'
            f'<span class="wk-txt">{label}{_tx_text(tx)}{(" " + badges) if badges else ""}</span></li>')


def _simple_li(item, wl, show_cat=False):
    tx = item["tx"]
    d = timeutil._parse_iso_date(item["date"])
    date_html = f'<time class="spot-date num" datetime="{d.isoformat()}">{_md(d)}</time>' if d else ""
    badges = _region_badges(watchlist.players_in_transaction(tx, wl), wl)
    cat = f'<span class="badge">{htmlutil.esc(CATEGORY_LABELS[item["category"]])}</span> ' if show_cat else ""
    return f'<li>{date_html}{cat}{_tx_text(tx)}{(" " + badges) if badges else ""}</li>'


def _section(heading_id, heading, count_text, body):
    count = f'<span class="count">{htmlutil.esc(count_text)}</span>' if count_text else ""
    head = f'<div class="section-head"><h2 id="{heading_id}">{htmlutil.esc(heading)}</h2>{count}</div>'
    return f'<section class="section wrap" aria-labelledby="{heading_id}">{head}{body}</section>'


def render_summary(week):
    s = week["stats"]
    cells = [("異動", f'{s["tx_count"]} 筆'), ("交易", f'{s["trade_count"]} 筆'), ("簽約", f'{s["signing_count"]} 筆'),
             ("資料涵蓋", f'{week["days"]}／7 天')]
    dl = "".join(f"<div><dt>{htmlutil.esc(k)}</dt><dd>{htmlutil.esc(v)}</dd></div>" for k, v in cells)
    html = f'<div class="wrap">\n  <dl class="overview">\n    {dl}\n  </dl>\n</div>'
    if week["days"] < 7:
        missing = [d for d in week_days(week["label"]) if d.isoformat() not in week["covered"]]
        html += (f'\n<div class="wrap"><p class="note-strip" role="note"><strong>本週資料涵蓋 {week["days"]}／7 天</strong>'
                 f'（{htmlutil.esc(fmt_runs([timeutil._parse_iso_date(k) for k in week["covered"]]))}；'
                 f'缺少 {htmlutil.esc(fmt_runs(missing))}），以下統計僅含有資料的日子。</p></div>')
    return html


def render_highlights(week, wl):
    items = week["highlights"]
    if not items:
        body = '<p class="section-empty">本週無交易、大聯盟簽約或延長合約。</p>'
    else:
        body = '<ul class="wk-list">\n' + "\n".join(_render_tx_row(it, wl) for it in items) + "\n    </ul>"
    return _section("wkh-h", "本週重點", f"{len(items)} 筆", body)


def render_categories(week, wl):
    groups = []
    for key, label in WEEKLY_CATEGORIES:
        items = week["by_cat"][key]
        if not items:
            continue
        lis = "".join(_simple_li(it, wl) for it in items)
        groups.append(f'<div class="tx-group"><h3>{htmlutil.esc(label)}<span class="spot-meta">　{len(items)} 筆</span></h3>'
                      f'<ul class="tx-list">{lis}</ul></div>')
    body = '<div class="tx-grid">' + "".join(groups) + "</div>" if groups else '<p class="section-empty">本週無可分類的人事異動。</p>'
    return _section("wkc-h", "分類整理", f"{week['stats']['tx_count']} 筆", body)


def render_teams(week, wl):
    teams = week["teams"]
    if not teams:
        return _section("wkt-h", "各隊動態", "", '<p class="section-empty">本週無球隊異動。</p>')
    groups = []
    for t in teams:
        meta = TEAM_META[t["team_id"]]
        busiest = '　<span class="badge is-busy">本週最忙</span>' if t["busiest"] else ""
        parts = []
        for direction, label in (("join", "加入"), ("leave", "離開")):
            if t[direction]:
                lis = "".join(_simple_li(it, wl, show_cat=True) for it in t[direction])
                parts.append(f'<h4 class="wk-sub">{label}（{len(t[direction])}）</h4><ul class="tx-list">{lis}</ul>')
        groups.append(
            f'<div class="tx-group{htmlutil.stripe_class(meta["color"])}" style="--team:{htmlutil.safe_color(meta["color"])}">'
            f'<h3>{htmlutil.logo_picture(t["team_id"], meta["abbr"], 22)}{htmlutil.esc(meta["name"])}'
            f'<span class="spot-meta">　{t["count"]} 筆</span>{busiest}</h3>{"".join(parts)}</div>')
    return _section("wkt-h", "各隊動態", f"{len(teams)} 隊", '<div class="tx-grid">' + "".join(groups) + "</div>")


def render_news(week, wl):
    picks = week["news"]
    if not picks:
        return _section("wkn-h", "本週新聞精選", "", '<p class="section-empty">本週無新聞資料。</p>')
    lis = []
    for p in picks:
        n = p["news"]
        link = news_mod._safe_mlb_url(n.get("link"))
        title = htmlutil.esc(n.get("title_zh") or n.get("title"))
        title_html = (f'<a class="title" href="{htmlutil.esc(link)}" target="_blank" rel="noopener">{title}</a>'
                      if link else f'<span class="title">{title}</span>')
        pub = _published_dt(n.get("published"))
        date_html = (f'<time datetime="{htmlutil.esc(pub.isoformat())}">{_md(pub)}</time>' if pub else "<time></time>")
        src = f'{htmlutil.esc(NEWS_SOURCE)}' + (f" · {pub.strftime('%m/%d %H:%M')}" if pub else "")
        badges = _region_badges(p["watch_ids"], wl)
        image = news_mod._safe_mlb_url(n.get("image"))
        img_html = (f'<img class="wk-thumb" src="{htmlutil.esc(image)}" alt="" loading="lazy">' if image else "")
        lis.append(f'<li>{date_html}<div>{title_html}'
                   f'<span class="src">{src}{(" " + badges) if badges else ""}</span>{img_html}</div></li>')
    return _section("wkn-h", "本週新聞精選", f"{len(picks)} 則", '<ol class="news">' + "".join(lis) + "</ol>")


def _published_dt(value):
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def render_watch(week, wl):
    """台日韓動態小區塊：本週列入的異動與精選新聞中涉及名單球員者；沒有就回傳空字串（整區隱藏）。"""
    tx_rows = [(it, watchlist.players_in_transaction(it["tx"], wl)) for it in week["items"]]
    tx_rows = [(it, ids) for it, ids in tx_rows if ids]
    news_rows = [p for p in week["news"] if p["watch_ids"]]
    if not wl or not (tx_rows or news_rows):
        return ""
    lis = []
    for it, ids in tx_rows:
        lis.append(f'<li><span class="badge">人事異動</span> {_tx_text(it["tx"])} {_region_badges(ids, wl)}</li>')
    for p in news_rows:
        n = p["news"]
        link = news_mod._safe_mlb_url(n.get("link"))
        title = htmlutil.esc(n.get("title_zh") or n.get("title"))
        t = f'<a href="{htmlutil.esc(link)}" target="_blank" rel="noopener">{title}</a>' if link else title
        lis.append(f'<li><span class="badge">新聞</span> {t} {_region_badges(p["watch_ids"], wl)}</li>')
    body = f'<ul class="tx-list wk-watch">{"".join(lis)}</ul>'
    return _section("wkw-h", "台日韓動態", f"{len(lis)} 則", body)


def render_calendar(week, events):
    start = week["end"] + timedelta(days=1)
    end = week["end"] + timedelta(days=7)
    upcoming = upcoming_events(events, week["end"])
    if upcoming:
        lis = ""
        for e in upcoming:
            note = f'<span class="spot-meta">　{htmlutil.esc(e["note"])}</span>' if e["note"] else ""
            lis += (f'<li><time class="spot-date num" datetime="{e["date"].isoformat()}">{_md(e["date"])}</time>'
                    f'{htmlutil.esc(e["title_zh"])}{note}</li>')
        body = f'<ul class="tx-list">{lis}</ul>'
    else:
        body = '<p class="section-empty">下週無排定重要日程。</p>'
    return _section("wkd-h", "下週日程", f"{_md(start)}～{_md(end)}", body)


def render_weekly(week, wl, events, generated_at=None):
    """組出整頁 HTML（templates/weekly.html）。"""
    label = week["label"]
    generated_at = generated_at or timeutil.get_taipei_now()
    return htmlutil.fill_slots(htmlutil.load_template("weekly.html"), {
        "TITLE": htmlutil.esc(f"MLB 每日戰報 - 休賽季每週總整理 {label}"),
        "DATELINE": htmlutil.esc(f"{label}　{fmt_range_long(week['start'], week['end'])}　休賽季每週總整理"),
        "SUMMARY": render_summary(week),
        "HIGHLIGHTS": render_highlights(week, wl),
        "WATCH": render_watch(week, wl),
        "CATEGORIES": render_categories(week, wl),
        "TEAMS": render_teams(week, wl),
        "NEWS": render_news(week, wl),
        "CALENDAR": render_calendar(week, events),
        "GENERATED_AT": htmlutil.esc(generated_at.strftime("%Y-%m-%d %H:%M")),
    })


# ---------------------------------------------------------------- 產生
def make_index_entry(week, generated_at):
    return {
        "week": week["label"],
        "start": week["start"].isoformat(),
        "end": week["end"].isoformat(),
        "days": week["days"],
        "tx_count": week["stats"]["tx_count"],
        "trade_count": week["stats"]["trade_count"],
        "signing_count": week["stats"]["signing_count"],
        "path": timeutil.weekly_href(week["label"]),
        "generated_at": generated_at.isoformat(timespec="seconds"),
    }


def generate_weekly_report(today, week=None, force=False, snapshot_dir=None, calendar_path=None, wl=None):
    """產生週報並更新 reports/weekly/index.json；回傳索引項目 dict，沒有產生（條件不符或無快照）回傳 None。

    today：台北今天（date）。week：'YYYY-Www'（格式錯誤拋 ValueError）；省略時整理「上一個」ISO 週，且只在週一執行。
    條件：指定 week 時略過週一判斷；休賽季判斷（rosterType != "active"）則一律套用。force=True 兩項都略過。"""
    if week:
        label = week
        parse_week(label)  # 先驗證格式
    else:
        if today.isoweekday() != 1 and not force:
            print(f"今天（{today.isoformat()}）不是週一，不產生每週總整理。")
            return None
        label = previous_week_label(today)
    if not force:
        ctx = rosters.resolve_roster_context(today)
        if ctx.get("rosterType") == "active":
            print("目前為賽季期間（rosterType=active），不產生休賽季每週總整理。")
            return None
    snapshots = load_week_snapshots(week_days(label), snapshot_dir)
    if not snapshots:
        print(f"{label} 沒有任何每日快照（data/daily），不產生每週總整理。")
        return None
    wl = watchlist.load_watchlist() if wl is None else wl
    week_data = build_week(label, snapshots, wl)
    events = load_calendar(calendar_path)
    now = timeutil.get_taipei_now()
    storage.write_weekly_report(label, render_weekly(week_data, wl, events, now))
    entry = make_index_entry(week_data, now)
    storage.save_weekly_index(entry)
    return entry
