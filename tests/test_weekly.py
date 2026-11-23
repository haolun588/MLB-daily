"""休賽季每週總整理測試：只用 unittest 與 tests/fixtures/，不連網、不呼叫真實 Gemini／Discord。
所有寫檔都在暫存目錄（MlbTestBase 會 chdir 進去），不污染 repo 的 reports/、data/。"""

import ast
import contextlib
import io
import json
import shutil
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

fetch_mlb = base.fetch_mlb
weekly = fetch_mlb.weekly
from mlb_daily import season as season_mod  # noqa: E402
DATE = base.DATE
SNAP_DIR = base.FIXTURES / "daily_snapshots"
CAL_PATH = str(base.FIXTURES / "offseason_calendar_test.json")
WL_PATH = str(base.FIXTURES / "watchlist_test.json")
WEEK = "2026-W47"
OHTANI, JHLEE = 660271, 808982
SF, WSH, TEX, CHC, MIN, NYY, LAD, BOS, SEA, TB = 137, 120, 140, 112, 142, 147, 119, 111, 136, 139


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


def load_wl():
    with contextlib.redirect_stderr(io.StringIO()):  # 名單 fixture 刻意含壞項目，會印警告
        return fetch_mlb.watchlist.load_watchlist(WL_PATH)


def item(code, desc, to=None, frm=None, persons=(), day="2026-11-18", **extra):
    """組出 weekly 內部用的 {"tx", "category", "date"}；category 依 classify_weekly_transaction。"""
    tx = {"type_code": code, "date": day, "description": desc, "description_zh": None,
          "person_id": persons[0]["id"] if persons else None, "person_name": persons[0]["name"] if persons else None,
          "persons": list(persons), "to_team_id": to, "from_team_id": frm, **extra}
    return {"tx": tx, "category": weekly.classify_weekly_transaction(tx), "date": day}


def fixture_snapshots():
    with contextlib.redirect_stderr(io.StringIO()):  # fixture 刻意含一個損壞檔
        return weekly.load_week_snapshots(weekly.week_days(WEEK), str(SNAP_DIR))


def news_entry(title, link, published="2026-11-16T10:00:00+08:00", summary="", title_zh=None):
    return {"title": title, "title_zh": title_zh, "link": link, "published": published, "image": None, "summary": summary}


def snap_of(day, news=(), txs=()):
    return {"date": day, "has_games": False, "transactions": list(txs), "news": list(news)}


# ---------------------------------------------------------------- 週次計算
class WeekHelperTests(unittest.TestCase):
    def test_parse_week_and_days(self):
        self.assertEqual(weekly.parse_week(WEEK), date(2026, 11, 16))
        days = weekly.week_days(WEEK)
        self.assertEqual((days[0], days[-1]), (date(2026, 11, 16), date(2026, 11, 22)))
        self.assertEqual(days[0].isoweekday(), 1)
        self.assertEqual(len(days), 7)

    def test_week_label_across_year_boundary(self):
        # 2026 年有 53 個 ISO 週：2027-01-03（週日）仍屬 2026-W53，2027-01-04 才是 2027-W01
        self.assertEqual(weekly.week_label(date(2027, 1, 3)), "2026-W53")
        self.assertEqual(weekly.week_label(date(2027, 1, 4)), "2027-W01")
        self.assertEqual(weekly.parse_week("2026-W53"), date(2026, 12, 28))
        self.assertEqual(weekly.week_days("2026-W53")[-1], date(2027, 1, 3))
        self.assertEqual(weekly.parse_week("2027-W01"), date(2027, 1, 4))

    def test_previous_week_label_on_monday(self):
        self.assertEqual(weekly.previous_week_label(date(2026, 11, 23)), WEEK)   # 週一整理上一週
        self.assertEqual(weekly.previous_week_label(date(2027, 1, 4)), "2026-W53")
        self.assertEqual(weekly.previous_week_label(date(2026, 11, 24)), WEEK)   # 週二（--force）也是上一個完整週

    def test_parse_week_rejects_bad_input(self):
        for bad in ("2026-47", "2026-W5", "26-W47", "2026-W00", "2026-W54", "2027-W53", "", None, "2026-w47"):
            with self.assertRaises(ValueError, msg=str(bad)):
                weekly.parse_week(bad)


# ---------------------------------------------------------------- 讀取每日快照
class LoadSnapshotsTests(unittest.TestCase):
    def test_fixture_week_skips_missing_and_corrupt_days(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            snaps = weekly.load_week_snapshots(weekly.week_days(WEEK), str(SNAP_DIR))
        self.assertEqual(sorted(snaps), ["2026-11-16", "2026-11-18", "2026-11-20", "2026-11-22"])  # 11-17、21 缺檔，11-19 損壞
        self.assertIn("Warning", err.getvalue())
        self.assertIn("2026-11-19", err.getvalue())

    def test_non_dict_persons_are_filtered(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            tx = {"description": "x", "type_code": "TR", "persons": [{"id": 1, "name": "A"}, "oops", None, 5]}
            tx2 = {"description": "y", "type_code": "TR", "persons": "bad"}
            (Path(d) / "2026-11-16.json").write_text(json.dumps({"schema": 1, "date": "2026-11-16",
                                                                  "transactions": [tx, tx2], "news": []}), encoding="utf-8")
            snaps = weekly.load_week_snapshots(weekly.week_days(WEEK), d)
        txs = snaps["2026-11-16"]["transactions"]
        self.assertEqual(txs[0]["persons"], [{"id": 1, "name": "A"}])
        self.assertEqual(txs[1]["persons"], [])
        weekly.build_week(WEEK, snaps)  # 不得拋例外

    def test_bad_schema_date_mismatch_and_non_dict_are_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            files = {
                "2026-11-16": {"schema": 2, "date": "2026-11-16", "transactions": [], "news": []},
                "2026-11-17": {"schema": 1, "date": "2026-11-18", "transactions": [], "news": []},
                "2026-11-18": ["not", "a", "dict"],
                "2026-11-20": {"schema": 1, "date": "2026-11-20", "transactions": "oops", "news": None},
                "2026-11-22": {"schema": 1, "date": "2026-11-22", "has_games": True,
                               "transactions": [{"description": "ok", "type_code": "TR"}, {"no_desc": 1}, "str", 5],
                               "news": [{"title": "t", "link": "https://www.mlb.com/a"}, {"title": "no link"}, None]},
            }
            for name, data in files.items():
                (Path(d) / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                snaps = weekly.load_week_snapshots(weekly.week_days(WEEK), d)
        self.assertEqual(sorted(snaps), ["2026-11-20", "2026-11-22"])
        self.assertEqual(snaps["2026-11-20"]["transactions"], [])  # 欄位型別錯誤視為空
        self.assertEqual(snaps["2026-11-20"]["news"], [])
        self.assertEqual(len(snaps["2026-11-22"]["transactions"]), 1)  # 非 dict、缺描述的項目濾掉
        self.assertEqual(len(snaps["2026-11-22"]["news"]), 1)
        self.assertTrue(snaps["2026-11-22"]["has_games"])


# ---------------------------------------------------------------- 分類
class ClassifyTests(unittest.TestCase):
    def cls(self, code, desc="x"):
        return weekly.classify_weekly_transaction({"type_code": code, "description": desc})

    def test_type_codes(self):
        table = {
            "TR": "trade", "SFA": "signing", "DES": "dfa", "REL": "dfa", "OUT": "dfa",
            "DFA": "free_agent", "SE": "roster40", "CLW": "roster40", "R5": "roster40", "R5M": "roster40",
        }
        for code, expected in table.items():
            self.assertEqual(self.cls(code), expected, code)

    def test_dfa_is_declared_free_agency_not_designation(self):
        self.assertEqual(self.cls("DFA", "RHP Ryne Stanek elected free agency."), "free_agent")
        self.assertEqual(self.cls("DES", "Washington Nationals designated LHP Richard Lovelady for assignment."), "dfa")
        self.assertNotEqual(self.cls("DFA"), self.cls("DES"))

    def test_ignored_codes(self):
        for code in ("SC", "ASG", "RTN", "OPT", "NUM", "RET", "XXX", None, ""):
            self.assertIsNone(self.cls(code, "whatever"), code)

    def test_minor_league_contract_is_not_listed(self):
        # 小聯盟合約分類已移除（快照為大聯盟過濾後資料）；防禦判斷：SFA 含 minor league contract 不列入
        self.assertIsNone(self.cls("SFA", "Texas Rangers signed free agent RHP Enrique Pantoja to a minor league contract."))
        self.assertEqual(self.cls("SFA", "Texas Rangers signed free agent RHP Enrique Pantoja."), "signing")

    def test_extension_regex_takes_priority_over_sfa(self):
        self.assertEqual(self.cls("SFA", "Seattle Mariners signed 1B Test Slugger to a contract extension."), "extension")
        # extended spring training 不是延長合約
        self.assertEqual(self.cls("SFA", "Team signed free agent RHP X; he will remain in extended spring training."), "signing")

    def test_categories_table_is_complete(self):
        keys = [k for k, _ in fetch_mlb.WEEKLY_CATEGORIES]
        for expected in ("trade", "signing", "extension", "dfa", "free_agent", "roster40"):
            self.assertIn(expected, keys)
        self.assertNotIn("minor", keys)
        self.assertNotIn("option", keys)  # 選擇權：API 沒有資料，已決定不做
        self.assertTrue(set(fetch_mlb.WEEKLY_TYPE_CATEGORY.values()) <= set(keys))


# ---------------------------------------------------------------- 各隊動態
class TeamActivityTests(unittest.TestCase):
    def test_trade_records_both_sides(self):
        trade = item("TR", "San Francisco Giants traded 2B Nate Furman to Washington Nationals for LHP Tony Neubeck.",
                     to=WSH, frm=SF)
        teams = {t["team_id"]: t for t in weekly.build_team_activity([trade])}
        self.assertEqual(len(teams[WSH]["join"]), 1)
        self.assertEqual(len(teams[SF]["leave"]), 1)
        self.assertEqual((teams[WSH]["leave"], teams[SF]["join"]), ([], []))

    def test_single_busiest_team_and_ordering(self):
        items = [
            item("SFA", "Boston Red Sox signed free agent RHP A.", to=BOS),
            item("SFA", "Boston Red Sox signed free agent RHP B.", to=BOS),
            item("SFA", "Boston Red Sox signed free agent RHP C.", to=BOS),
            item("REL", "New York Yankees released RHP D.", frm=NYY),
            item("REL", "New York Yankees released RHP E.", frm=NYY),
            item("SFA", "Seattle Mariners signed free agent RHP F.", to=SEA),
        ]
        teams = weekly.build_team_activity(items)
        self.assertEqual([t["team_id"] for t in teams], [BOS, NYY, SEA])
        self.assertEqual([t["busiest"] for t in teams], [True, False, False])

    def test_tied_teams_are_all_busiest(self):
        items = [
            item("SFA", "Boston Red Sox signed free agent RHP A.", to=BOS),
            item("SFA", "Boston Red Sox signed free agent RHP B.", to=BOS),
            item("REL", "New York Yankees released RHP D.", frm=NYY),
            item("REL", "New York Yankees released RHP E.", frm=NYY),
            item("SFA", "Seattle Mariners signed free agent RHP F.", to=SEA),
        ]
        teams = {t["team_id"]: t for t in weekly.build_team_activity(items)}
        self.assertTrue(teams[BOS]["busiest"] and teams[NYY]["busiest"])
        self.assertFalse(teams[SEA]["busiest"])

    def test_minor_contracts_and_ignored_are_excluded(self):
        minor = item("SFA", "Texas Rangers signed free agent RHP X to a minor league contract.", to=TEX)
        self.assertIsNone(minor["category"])
        week = weekly.build_week(WEEK, fixture_snapshots(), load_wl())
        self.assertNotIn("Boston Red Sox placed RHP Inj Ured", json.dumps([i["tx"]["description"] for i in week["items"]]))
        self.assertNotIn("Texas Rangers assigned RHP Min Or", json.dumps([i["tx"]["description"] for i in week["items"]]))
        self.assertNotIn("Enrique Pantoja", json.dumps([i["tx"]["description"] for i in week["items"]]))

    def test_leave_categories_use_real_api_shapes(self):
        # 真實 API：DES／DFA／REL 只有 toTeam（指球員離開的那隊）
        for code, desc in (("REL", "Minnesota Twins released RHP Some Guy."),
                           ("DES", "Washington Nationals designated LHP Richard Lovelady for assignment."),
                           ("DFA", "RHP Ryne Stanek elected free agency.")):
            it = item(code, desc, to=MIN)
            self.assertEqual(weekly.team_moves(it), [(MIN, "leave")], code)
            (t,) = weekly.build_team_activity([it])
            self.assertEqual((len(t["leave"]), len(t["join"])), (1, 0), code)

    def test_outright_uses_from_team_not_minor_league_to_team(self):
        it = item("OUT", "Texas Rangers sent RHP Mover outright to Round Rock Express.", to=2310, frm=TEX)
        self.assertEqual(weekly.team_moves(it), [(TEX, "leave")])

    def test_leave_category_without_mlb_team_records_nothing(self):
        it = item("REL", "Somebody released RHP X.", to=2310)
        self.assertEqual(weekly.team_moves(it), [])
        self.assertEqual(weekly.build_team_activity([it]), [])

    def test_multi_team_trade_with_legs(self):
        raw = json.loads((base.FIXTURES / "transactions_three_team_trade.json").read_text(encoding="utf-8"))["transactions"]
        tx = fetch_mlb.filter_major_league_transactions(raw)[0]
        it = {"tx": tx, "category": weekly.classify_weekly_transaction(tx), "date": "2024-07-29"}
        moves = weekly.team_moves(it)
        self.assertEqual(sorted(moves), sorted([(119, "join"), (145, "join"), (138, "join")]))
        self.assertEqual({t["team_id"] for t in weekly.build_team_activity([it])}, {119, 145, 138})

    def test_cash_only_side_records_leave(self):
        # 白襪送出球員、只收現金（person=None 的 leg 的 to）：記 leave；道奇收球員：join
        tx = {"type_code": "TR", "date": "2026-11-18", "description": "LAD traded X to CWS... cash", "persons": [],
              "to_team_id": LAD, "from_team_id": 145,
              "team_ids": [LAD, 145],
              "legs": [{"to_team_id": LAD, "from_team_id": 145, "person_id": 1},
                       {"to_team_id": 145, "from_team_id": LAD, "person_id": None}]}
        it = {"tx": tx, "category": "trade", "date": "2026-11-18"}
        self.assertEqual(weekly.team_moves(it), [(LAD, "join"), (145, "leave")])

    def test_non_trade_with_legs_ignores_legs(self):
        tx = {"type_code": "SFA", "date": "2026-11-18", "description": "Los Angeles Dodgers signed free agent X.",
              "persons": [], "to_team_id": LAD, "from_team_id": None,
              "legs": [{"to_team_id": LAD, "from_team_id": None, "person_id": None}]}
        it = {"tx": tx, "category": "signing", "date": "2026-11-18"}
        self.assertEqual(weekly.team_moves(it), [(LAD, "join")])

    def test_legacy_snapshot_without_legs_unchanged(self):
        it = item("TR", "A traded B.", to=WSH, frm=SF)
        self.assertNotIn("legs", it["tx"])
        self.assertEqual(weekly.team_moves(it), [(WSH, "join"), (SF, "leave")])

    def test_busiest_badge_requires_at_least_two(self):
        one = [item("SFA", "Boston Red Sox signed free agent RHP A.", to=BOS),
               item("SFA", "Seattle Mariners signed free agent RHP F.", to=SEA)]
        self.assertFalse(any(t["busiest"] for t in weekly.build_team_activity(one)))

    def test_same_team_on_both_fields_counts_once(self):
        des = item("DES", "Washington Nationals designated LHP Richard Lovelady for assignment.", to=WSH, frm=WSH)
        (t,) = weekly.build_team_activity([des])
        self.assertEqual((t["count"], len(t["leave"]), len(t["join"])), (1, 1, 0))  # 離開類只記一次
        se = item("SE", "Washington Nationals selected the contract of X.", to=WSH, frm=WSH)
        (t,) = weekly.build_team_activity([se])
        self.assertEqual((t["count"], len(t["join"]), len(t["leave"])), (1, 1, 0))

    def test_non_mlb_team_ids_ignored(self):
        x = item("SFA", "Somebody signed free agent RHP X.", to=99999, frm="abc")
        self.assertEqual(weekly.build_team_activity([x]), [])

    def test_fixture_week_ties_all_get_badge(self):
        week = weekly.build_week(WEEK, fixture_snapshots(), load_wl())
        busiest = {t["team_id"] for t in week["teams"] if t["busiest"]}
        self.assertEqual(busiest, {BOS, CHC, WSH, SF, MIN})  # 各 2 筆並列最多
        self.assertEqual(len(week["teams"]), 10)


# ---------------------------------------------------------------- 彙整統計
class BuildWeekTests(unittest.TestCase):
    def setUp(self):
        self.week = weekly.build_week(WEEK, fixture_snapshots(), load_wl())

    def test_stats_and_coverage(self):
        self.assertEqual(self.week["days"], 4)
        self.assertEqual((self.week["start"], self.week["end"]), (date(2026, 11, 16), date(2026, 11, 22)))
        self.assertEqual(self.week["stats"], {"tx_count": 12, "trade_count": 2, "signing_count": 4})

    def test_duplicate_description_same_day_counted_once(self):
        descs = [i["tx"]["description"] for i in self.week["items"]]
        self.assertEqual(sum("Dup Lic" in d for d in descs), 1)

    def test_highlights_are_trade_signing_extension_newest_first(self):
        hl = self.week["highlights"]
        self.assertEqual({i["category"] for i in hl}, {"trade", "signing", "extension"})
        self.assertEqual(len(hl), 6)
        dates = [i["date"] for i in hl]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_minor_contract_not_listed_anywhere(self):
        self.assertNotIn("minor", self.week["by_cat"])
        self.assertNotIn("Enrique Pantoja", json.dumps([i["tx"]["description"] for i in self.week["items"]]))


# ---------------------------------------------------------------- 新聞精選
class NewsSelectionTests(unittest.TestCase):
    def setUp(self):
        self.wl = load_wl()
        self.furman = [{"tx": {"persons": [{"id": 1, "name": "Nate Furman"}]}, "category": "trade", "date": "2026-11-16"}]

    def pick(self, news_by_day, highlights=None, limit=10):
        snaps = {d: snap_of(d, news) for d, news in news_by_day.items()}
        return weekly.select_news(snaps, self.wl, self.furman if highlights is None else highlights, limit)

    def test_score_order_watch_then_highlight_then_recency(self):
        picks = self.pick({
            "2026-11-16": [
                news_entry("Plain old", "https://www.mlb.com/news/old", "2026-11-16T08:00:00+08:00"),
                news_entry("Watch player", "https://www.mlb.com/news/w", "2026-11-16T07:00:00+08:00", "Shohei Ohtani homers"),
                news_entry("Furman news", "https://www.mlb.com/news/f", "2026-11-16T06:00:00+08:00", "Nate Furman arrives"),
            ],
            "2026-11-20": [news_entry("Plain new", "https://www.mlb.com/news/new", "2026-11-20T08:00:00+08:00")],
        })
        self.assertEqual([p["news"]["link"].rsplit("/", 1)[-1] for p in picks], ["w", "f", "new", "old"])
        self.assertEqual([p["score"] for p in picks], [2, 1, 0, 0])
        self.assertEqual(picks[0]["watch_ids"], [OHTANI])

    def test_player_in_both_watchlist_and_highlight_scores_three(self):
        hl = [{"tx": {"persons": [{"id": OHTANI, "name": "Shohei Ohtani"}]}, "category": "signing", "date": "2026-11-18"}]
        (p,) = self.pick({"2026-11-18": [news_entry("Ohtani wins", "https://www.mlb.com/news/o", summary="Shohei Ohtani")]}, hl)
        self.assertEqual(p["score"], 3)

    def test_dedup_by_link(self):
        picks = self.pick({
            "2026-11-16": [news_entry("Same", "https://www.mlb.com/news/dup")],
            "2026-11-18": [news_entry("Same again", "https://www.mlb.com/news/dup"), news_entry("Other", "https://www.mlb.com/news/o")],
        })
        self.assertEqual(sorted(p["news"]["link"] for p in picks), ["https://www.mlb.com/news/dup", "https://www.mlb.com/news/o"])

    def test_limit_is_ten(self):
        many = [news_entry(f"n{i}", f"https://www.mlb.com/news/n{i}", f"2026-11-16T{i:02d}:00:00+08:00") for i in range(15)]
        picks = self.pick({"2026-11-16": many})
        self.assertEqual(len(picks), 10)
        self.assertEqual(picks[0]["news"]["title"], "n14")  # 同分新到舊
        self.assertEqual(fetch_mlb.WEEKLY_NEWS_LIMIT, 10)

    def test_bad_published_sorts_last_among_equal_scores(self):
        picks = self.pick({"2026-11-16": [
            news_entry("bad date", "https://www.mlb.com/news/b", "not a date"),
            news_entry("good", "https://www.mlb.com/news/g", "2026-11-16T10:00:00+08:00")]})
        self.assertEqual([p["news"]["title"] for p in picks], ["good", "bad date"])

    def test_fixture_week_top_and_dedup(self):
        picks = weekly.select_news(fixture_snapshots(), self.wl, weekly.build_week(WEEK, fixture_snapshots(), self.wl)["highlights"])
        links = [p["news"]["link"] for p in picks]
        self.assertEqual(len(picks), 10)
        self.assertEqual(len(links), len(set(links)))
        self.assertEqual(sum("furman-trade" in l for l in links), 1)  # 兩天都有的新聞只留一則
        self.assertTrue(all(p["score"] >= 2 for p in picks[:3]))      # 台日韓球員新聞排最前面
        self.assertEqual({JHLEE, OHTANI}, {i for p in picks for i in p["watch_ids"]})


# ---------------------------------------------------------------- 台日韓標註
class WatchlistBadgeTests(unittest.TestCase):
    def render(self, wl):
        week = weekly.build_week(WEEK, fixture_snapshots(), wl)
        return weekly.render_weekly(week, wl, [], datetime(2026, 11, 23, 12, 17, tzinfo=timezone(timedelta(hours=8))))

    def test_badges_on_transactions_news_and_summary_block(self):
        html = self.render(load_wl())
        self.assertIn('class="badge is-region"', html)
        self.assertIn("日本 大谷翔平", html)
        self.assertIn("韓國 李政厚", html)
        self.assertIn('id="wkw-h"', html)
        block = html[html.index('id="wkw-h"'):html.index('id="wkc-h"')]
        self.assertIn("Shohei Ohtani", block)  # 異動
        self.assertIn("大谷翔平再度獲選 MVP", block)  # 新聞
        self.assertIn("5 則", block)

    def test_no_watchlist_means_no_badges_and_no_block(self):
        html = self.render({})
        self.assertNotIn("is-region", html)
        self.assertNotIn('id="wkw-h"', html)

    def test_no_watch_hits_hides_block(self):
        snaps = {"2026-11-16": snap_of("2026-11-16", [news_entry("Plain", "https://www.mlb.com/news/p")], [
            {"type_code": "TR", "date": "2026-11-16", "description": "A traded B.", "persons": [], "to_team_id": BOS, "from_team_id": NYY}])}
        week = weekly.build_week(WEEK, snaps, load_wl())
        html = weekly.render_weekly(week, load_wl(), [], datetime(2026, 11, 23, tzinfo=timezone.utc))
        self.assertNotIn('id="wkw-h"', html)


# ---------------------------------------------------------------- 下週日程
class CalendarTests(unittest.TestCase):
    def test_load_calendar_fixture_skips_bad_entries(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            events = weekly.load_calendar(CAL_PATH)
        self.assertEqual(len(events), 6)
        self.assertEqual(err.getvalue().count("略過格式錯誤"), 3)  # 壞日期、缺標題、非物件

    def test_upcoming_is_next_monday_to_sunday_sorted(self):
        with contextlib.redirect_stderr(io.StringIO()):
            events = weekly.load_calendar(CAL_PATH)
        up = weekly.upcoming_events(events, date(2026, 11, 22))
        self.assertEqual([e["date"] for e in up], [date(2026, 11, 23), date(2026, 11, 25), date(2026, 11, 29)])
        self.assertEqual(weekly.upcoming_events(events, date(2026, 11, 29))[0]["date"], date(2026, 11, 30))

    def test_missing_or_broken_file_warns_and_returns_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("bad.json", "{oops"), ("shape.json", '{"events": 5}'), ("list.json", "[]")):
                (Path(d) / name).write_text(text, encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(weekly.load_calendar(str(Path(d) / name)), [], name)
                self.assertIn("Warning", err.getvalue())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(weekly.load_calendar(str(Path(d) / "nope.json")), [])
            self.assertIn("Warning", err.getvalue())

    def test_real_settings_file_has_valid_format(self):
        # 只驗格式（使用者會手動補日程，不可假設為空）
        path = str(base.REPO_ROOT / "settings" / "offseason_calendar.json")
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertIsInstance(data["events"], list)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            events = weekly.load_calendar(path)
        self.assertNotIn("Warning", err.getvalue())
        self.assertEqual(len(events), len(data["events"]))  # 每筆日期與標題皆有效，沒有被略過
        for e in events:
            self.assertIsInstance(e["date"], date)
            self.assertTrue(e["title_zh"])

    def test_render_calendar_with_and_without_events(self):
        week = weekly.build_week(WEEK, fixture_snapshots(), {})
        with contextlib.redirect_stderr(io.StringIO()):
            events = weekly.load_calendar(CAL_PATH)
        html = weekly.render_calendar(week, events)
        self.assertIn("11/23～11/29", html)
        self.assertIn("測試日程一", html)
        self.assertNotIn("冬季會議", html)
        self.assertNotIn("本週最後一天", html)
        self.assertIn("&lt;b&gt;備註&lt;/b&gt; &amp; 說明", html)  # note 經 esc
        self.assertNotIn("<b>備註", html)
        self.assertIn("下週無排定重要日程", weekly.render_calendar(week, []))


# ---------------------------------------------------------------- 渲染
class RenderTests(unittest.TestCase):
    NOW = datetime(2026, 11, 23, 12, 17, tzinfo=timezone(timedelta(hours=8)))

    def render(self, snaps=None, wl=None, events=()):
        wl = load_wl() if wl is None else wl
        week = weekly.build_week(WEEK, fixture_snapshots() if snaps is None else snaps, wl)
        return weekly.render_weekly(week, wl, list(events), self.NOW)

    def test_coverage_notice_for_partial_week(self):
        html = self.render()
        self.assertIn("本週資料涵蓋 4／7 天", html)
        self.assertIn("11/16、11/18、11/20、11/22", html)
        self.assertIn("缺少 11/17、11/19、11/21", html)
        self.assertIn("<dd>4／7 天</dd>", html)

    def test_full_week_has_no_notice(self):
        snaps = {d.isoformat(): snap_of(d.isoformat()) for d in weekly.week_days(WEEK)}
        html = self.render(snaps)
        self.assertIn("<dd>7／7 天</dd>", html)
        self.assertNotIn("note-strip", html)

    def test_fmt_runs(self):
        d = lambda *a: date(2026, *a)  # noqa: E731
        self.assertEqual(weekly.fmt_runs([d(11, 18), d(11, 19), d(11, 20), d(11, 22)]), "11/18～11/20、11/22")
        self.assertEqual(weekly.fmt_runs([d(11, 17)]), "11/17")

    def test_sections_and_headers(self):
        html = self.render()
        for heading in ("本週重點", "分類整理", "各隊動態", "本週新聞精選", "下週日程"):
            self.assertIn(f">{heading}</h2>", html)
        self.assertIn("MLB 每日戰報 - 休賽季每週總整理 2026-W47", html)
        self.assertIn("2026-W47　2026 年 11 月 16 日～11 月 22 日", html)
        self.assertIn("../../assets/style.css", html)
        self.assertIn("../../index.html", html)
        self.assertIn("2026-11-23 12:17", html)
        self.assertEqual(html.count("本週最忙"), 5)  # 5 隊並列

    def test_no_minor_contract_section(self):
        html = self.render()
        self.assertNotIn("<details", html)
        self.assertNotIn("小聯盟合約", html)
        self.assertNotIn("Enrique Pantoja", html)

    def test_empty_week_renders_empty_states(self):
        html = self.render({"2026-11-16": snap_of("2026-11-16")})
        self.assertIn("本週無交易、大聯盟簽約或延長合約", html)
        self.assertIn("本週無可分類的人事異動", html)
        self.assertIn("本週無球隊異動", html)
        self.assertIn("本週無新聞資料", html)

    def test_all_external_strings_are_escaped(self):
        html = self.render()
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; Evil", html)   # 異動（含球員名）
        self.assertIn("&lt;script&gt;alert(4)&lt;/script&gt; 標題", html)    # 新聞標題
        self.assertNotIn('"onmouseover="', html)                           # 新聞連結屬性不得被截斷
        self.assertIn("&quot;onmouseover=&quot;", html)

    def test_unsafe_links_and_images_are_not_rendered(self):
        bad = news_entry("Bad link", "javascript:alert(1)", "2026-11-16T10:00:00+08:00")
        bad2 = news_entry("Evil image", "https://www.mlb.com/news/ok", "2026-11-16T09:00:00+08:00")
        bad2["image"] = "https://evil.example.com/x.jpg"
        html = self.render({"2026-11-16": snap_of("2026-11-16", [bad, bad2])})
        self.assertNotIn("javascript:", html)
        self.assertNotIn("evil.example.com", html)
        self.assertIn("Bad link", html)

    def test_news_item_shows_date_source_and_image(self):
        html = self.render()
        sec = html[html.index('id="wkn-h"'):html.index('id="wkd-h"')]
        self.assertIn("MLB.com · 11/20 06:00", sec)
        self.assertIn("https://img.mlbstatic.com/mlb-images/x.jpg", sec)
        self.assertIn("巨人從國民取得 Furman", sec)


# ---------------------------------------------------------------- 每日快照寫入
class DailySnapshotTests(base.MlbTestBase):
    def snapshot(self, date_str=DATE):
        return json.loads((self.tmp / "data" / "daily" / f"{date_str}.json").read_text(encoding="utf-8"))

    def test_season_report_writes_snapshot(self):
        self.api.news = "news_rss.xml"
        meta, _ = self.generate("schedule_normal", transactions="transactions_default")
        snap = self.snapshot()
        self.assertEqual((snap["schema"], snap["date"], snap["has_games"]), (1, DATE, True))
        self.assertEqual(len(snap["transactions"]), meta["transactions_count"])  # 與戰報顯示同一批（含各場卡片內的）
        self.assertGreater(len(snap["transactions"]), 0)
        for tx in snap["transactions"]:
            for key in ("type_code", "date", "description", "description_zh", "person_id", "person_name",
                        "persons", "to_team_id", "from_team_id", "team_ids", "legs"):
                self.assertIn(key, tx)
        self.assertEqual(len(snap["news"]), meta["news_count"])
        for n in snap["news"]:
            self.assertIsInstance(n["published"], str)
            datetime.fromisoformat(n["published"])
            self.assertTrue(n["link"].startswith("https://www.mlb.com/"))

    def test_offseason_report_writes_snapshot(self):
        self.api.transactions = "transactions_default"
        self.api.news = "news_rss.xml"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            meta = fetch_mlb.generate_offseason_report(DATE)
        snap = self.snapshot()
        self.assertEqual((snap["schema"], snap["date"], snap["has_games"]), (1, DATE, False))
        self.assertEqual(len(snap["transactions"]), meta["transactions_count"])
        self.assertEqual(len(snap["news"]), meta["news_count"])

    def test_snapshot_stores_translations(self):
        self.api.news = "news_rss.xml"
        self.api.transactions = "transactions_default"

        def fake_translate(items, extra=()):
            return ["ZH:" + it["text"] for it in items]

        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test-key-not-real"}), \
                mock.patch("mlb_daily.translate.translate_texts", side_effect=fake_translate):
            quiet(fetch_mlb.generate_offseason_report, DATE)
        snap = self.snapshot()
        self.assertTrue(all(n["title_zh"].startswith("ZH:") for n in snap["news"]))
        self.assertTrue(all(t["description_zh"].startswith("ZH:") for t in snap["transactions"]))

    def test_snapshot_without_translation_keeps_null_zh(self):
        self.api.transactions = "transactions_default"
        quiet(fetch_mlb.generate_offseason_report, DATE)
        self.assertTrue(all(t["description_zh"] is None for t in self.snapshot()["transactions"]))

    def test_news_fetch_failure_stores_empty_news(self):
        quiet(fetch_mlb.generate_offseason_report, DATE)  # api.news 預設為抓取失敗
        self.assertEqual(self.snapshot()["news"], [])

    def test_write_failure_only_warns(self):
        err = io.StringIO()
        with mock.patch("mlb_daily.storage.write_json_atomic", side_effect=OSError("disk full")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            meta = fetch_mlb.generate_offseason_report(DATE)
        self.assertEqual(meta["type"], "offseason")
        self.assertTrue((self.tmp / "reports" / "2026" / "11" / f"{DATE}.html").exists())
        self.assertIn("無法寫入每日資料快照", err.getvalue())
        self.assertFalse((self.tmp / "data" / "daily" / f"{DATE}.json").exists())

    def test_build_failure_only_warns(self):
        err = io.StringIO()
        with mock.patch("mlb_daily.report.build_daily_snapshot", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.api.schedule = "schedule_normal"
            meta = fetch_mlb.generate_report(DATE, self.games_of("schedule_normal"))
        self.assertEqual(meta["type"], "season")
        self.assertIn("每日資料快照產生失敗", err.getvalue())

    def test_build_daily_snapshot_published_is_iso_string(self):
        pub = datetime(2026, 11, 18, 9, 30, tzinfo=timezone(timedelta(hours=8)))
        snap = fetch_mlb.build_daily_snapshot("2026-11-18", False, [], [
            {"title": "t", "link": "https://www.mlb.com/x", "published": pub, "image": None, "summary": "s"}])
        self.assertEqual(snap["news"][0]["published"], "2026-11-18T09:30:00+08:00")
        json.dumps(snap)  # 可序列化


# ---------------------------------------------------------------- 產生條件與索引
class GenerateWeeklyTests(base.MlbTestBase):
    def setUp(self):
        super().setUp()
        shutil.copytree(SNAP_DIR, self.tmp / "data" / "daily")
        self.wl = load_wl()

    def gen(self, today, **kw):
        kw.setdefault("calendar_path", CAL_PATH)
        kw.setdefault("wl", self.wl)
        kw.setdefault("auto_events", [])  # 不打 MLB API；自動帶入另有專屬測試
        return quiet(weekly.generate_weekly_report, today, **kw)

    def html_path(self, label=WEEK):
        return self.tmp / "reports" / "weekly" / f"{label}.html"

    def test_monday_in_offseason_generates_previous_week(self):
        entry = self.gen(date(2026, 11, 23))
        self.assertEqual((entry["week"], entry["days"]), (WEEK, 4))
        self.assertTrue(self.html_path().exists())
        self.assertEqual(entry["path"], "reports/weekly/2026-W47.html")

    def test_not_monday_skips_unless_week_or_force(self):
        self.assertIsNone(self.gen(date(2026, 11, 24)))
        self.assertFalse(self.html_path().exists())
        self.assertEqual(self.gen(date(2026, 11, 24), week=WEEK)["week"], WEEK)  # --week 略過週一判斷

    def test_force_on_non_monday_uses_previous_week(self):
        self.assertEqual(self.gen(date(2026, 11, 24), force=True)["week"], WEEK)

    def test_active_season_skips(self):
        self.api.seasons = "seasons_2026"
        today = date(2026, 7, 20)  # 週一、賽季中
        self.assertEqual(today.isoweekday(), 1)
        self.assertIsNone(self.gen(today))
        self.assertFalse((self.tmp / "reports" / "weekly").exists())
        self.assertIsNone(self.gen(date(2026, 7, 22), week=WEEK))  # --week 也套用休賽季判斷
        self.assertEqual(self.gen(date(2026, 7, 22), week=WEEK, force=True)["week"], WEEK)  # --force 略過

    def test_force_skips_roster_api(self):
        self.api.calls.clear()
        self.gen(date(2026, 11, 23), force=True)
        self.assertFalse(any("/seasons/" in u for u in self.api.calls))

    def test_zero_snapshots_means_no_output(self):
        self.assertIsNone(self.gen(date(2026, 11, 23), week="2026-W41"))  # 沒有任何快照
        self.assertFalse((self.tmp / "reports" / "weekly").exists())
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            weekly.generate_weekly_report(date(2026, 11, 23), week="2026-W41", calendar_path=CAL_PATH, wl=self.wl, auto_events=[])
        self.assertIn("沒有任何每日快照", out.getvalue())

    def test_partial_week_is_generated_and_marked(self):
        self.gen(date(2026, 11, 23))
        html = self.html_path().read_text(encoding="utf-8")
        self.assertIn("本週資料涵蓋 4／7 天", html)
        self.assertIn("本週最忙", html)

    def test_bad_week_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.gen(date(2026, 11, 23), week="2026-47")

    def test_corrupt_snapshot_warns_but_generates(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            weekly.generate_weekly_report(date(2026, 11, 23), calendar_path=CAL_PATH, wl=self.wl, auto_events=[])
        self.assertIn("2026-11-19", err.getvalue())
        self.assertTrue(self.html_path().exists())

    def test_index_entry_and_dedup_newest_first(self):
        entry = self.gen(date(2026, 11, 23))
        index = json.loads((self.tmp / "reports" / "weekly" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["weeks"], [entry])
        for key in ("week", "start", "end", "days", "tx_count", "trade_count", "signing_count", "path", "generated_at"):
            self.assertIn(key, entry)
        self.assertEqual((entry["start"], entry["end"], entry["tx_count"], entry["trade_count"], entry["signing_count"]),
                         ("2026-11-16", "2026-11-22", 12, 2, 4))
        fetch_mlb.save_weekly_index({"week": "2026-W46", "path": "reports/weekly/2026-W46.html"})
        fetch_mlb.save_weekly_index({"week": "2026-W48", "path": "reports/weekly/2026-W48.html"})
        self.gen(date(2026, 11, 23))  # 同週重產：覆蓋而不是新增
        weeks = fetch_mlb.load_weekly_index()["weeks"]
        self.assertEqual([w["week"] for w in weeks], ["2026-W48", WEEK, "2026-W46"])
        self.assertEqual(fetch_mlb.latest_weekly_entry()["week"], "2026-W48")

    def test_load_weekly_index_tolerates_bad_files(self):
        path = self.tmp / "reports" / "weekly" / "index.json"
        path.parent.mkdir(parents=True)
        self.assertEqual(fetch_mlb.load_weekly_index(), {"weeks": []})  # 不存在
        for text in ("{oops", "[]", '{"weeks": 5}'):
            path.write_text(text, encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(fetch_mlb.load_weekly_index(), {"weeks": []})
            self.assertIn("Warning", err.getvalue())
        path.write_text('{"weeks": [{"week": "2026-W47"}, "x", {"no_week": 1}, 5]}', encoding="utf-8")
        self.assertEqual(fetch_mlb.load_weekly_index(), {"weeks": [{"week": "2026-W47"}]})
        path.unlink()
        self.assertIsNone(fetch_mlb.latest_weekly_entry())


# ---------------------------------------------------------------- 首頁與存檔頁連結
class IndexLinkTests(base.MlbTestBase):
    ENTRY = {"week": WEEK, "start": "2026-11-16", "end": "2026-11-22", "days": 4, "tx_count": 12,
             "trade_count": 2, "signing_count": 4, "path": "reports/weekly/2026-W47.html"}

    def write_index(self, *entries):
        for e in entries:
            fetch_mlb.save_weekly_index(e)

    def test_render_latest_weekly_link(self):
        html = fetch_mlb.render_latest_weekly_link(self.ENTRY)
        self.assertIn('href="reports/weekly/2026-W47.html"', html)
        self.assertIn("最新週報：2026-W47（11/16～11/22）", html)
        self.assertIn("涵蓋 4／7 天", html)

    def test_link_hidden_when_week_is_older_than_14_days(self):
        end = date(2026, 11, 22)
        self.assertNotEqual(fetch_mlb.render_latest_weekly_link(self.ENTRY, end + timedelta(days=14)), "")
        self.assertEqual(fetch_mlb.render_latest_weekly_link(self.ENTRY, end + timedelta(days=15)), "")

    def test_link_built_from_week_label_not_path(self):
        evil = {**self.ENTRY, "path": "javascript:alert(1)", "week": WEEK}
        self.assertNotIn("javascript:", fetch_mlb.render_latest_weekly_link(evil))
        self.assertEqual(fetch_mlb.render_latest_weekly_link({"week": 'x"><script>'}), "")
        self.assertEqual(fetch_mlb.render_latest_weekly_link({"week": WEEK, "start": "bad"}).count("（"), 0)
        self.assertEqual(fetch_mlb.render_latest_weekly_link(None), "")  # 沒有索引

    def test_offseason_homepage_shows_latest_weekly(self):
        self.write_index({**self.ENTRY, "week": "2026-W46", "start": "2026-11-09", "end": "2026-11-15"}, self.ENTRY)
        now = base.OFFSEASON_NOW
        with mock.patch("mlb_daily.timeutil.get_taipei_now", return_value=now), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            extras = fetch_mlb.build_index_extras(now)
        self.assertIn("最新週報：2026-W47", extras["UPCOMING"])
        self.assertNotIn("2026-W46", extras["UPCOMING"])

    def test_no_weekly_index_means_no_block(self):
        now = base.OFFSEASON_NOW
        with mock.patch("mlb_daily.timeutil.get_taipei_now", return_value=now), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            extras = fetch_mlb.build_index_extras(now)
        self.assertNotIn("每週總整理", extras["UPCOMING"])

    def test_season_homepage_has_no_weekly_block(self):
        self.write_index(self.ENTRY)
        now = base.SEASON_NOW
        with mock.patch("mlb_daily.timeutil.get_taipei_now", return_value=now), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            extras = fetch_mlb.build_index_extras(now)
        self.assertNotIn("最新週報", extras["UPCOMING"])

    def test_archive_js_reads_weekly_index_safely(self):
        js = (base.REPO_ROOT / "assets" / "archive.js").read_text(encoding="utf-8")
        self.assertIn("reports/weekly/index.json", js)
        self.assertIn("每週總整理", js)
        self.assertNotIn("innerHTML", js.split("*/", 1)[1])  # 外部資料一律 textContent（檔頭註解提到不算）
        self.assertIn(r"/^\d{4}-W\d{2}$/", js)  # 週次格式驗證後才組連結
        self.assertIn(".catch(function () {", js[js.index("function loadWeekly"):])  # 讀不到就不顯示


# ---------------------------------------------------------------- CLI
class WeeklyCliTests(base.MlbTestBase):
    def setUp(self):
        super().setUp()
        shutil.copytree(SNAP_DIR, self.tmp / "data" / "daily")
        shutil.copytree(base.REPO_ROOT / "settings", self.tmp / "settings")
        (self.tmp / "reports" / "metadata.json").write_text(json.dumps({DATE: {
            "date": DATE, "total_games": 3, "postponed": 0, "summary": "s", "type": "season"}}), encoding="utf-8")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = None
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", *args]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_weekly_with_week_and_force_generates_and_refreshes_index_page(self):
        code, out, _ = self.run_cli("--weekly", "--week", WEEK, "--force", "--date", "2026-11-23")
        self.assertIn(code, (None, 0))
        self.assertTrue((self.tmp / "reports" / "weekly" / f"{WEEK}.html").exists())
        self.assertIn(f"weekly_week={WEEK}", out)
        self.assertIn(f"weekly_url=https://haolun588.github.io/MLB-daily/reports/weekly/{WEEK}.html", out)
        index_html = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertIn("最新週報：2026-W47", index_html)
        self.assertIn("休賽季", index_html)

    def test_weekly_monday_without_week_uses_previous_week(self):
        code, out, _ = self.run_cli("--weekly", "--date", "2026-11-23")
        self.assertIn(code, (None, 0))
        self.assertIn(f"weekly_week={WEEK}", out)

    def test_weekly_skip_prints_no_markers_and_writes_nothing(self):
        code, out, _ = self.run_cli("--weekly", "--date", "2026-11-24")  # 週二
        self.assertIn(code, (None, 0))
        self.assertNotIn("weekly_url=", out)
        self.assertFalse((self.tmp / "reports" / "weekly").exists())
        self.assertNotIn("最新週報", (self.tmp / "index.html").read_text(encoding="utf-8"))

    def test_weekly_never_touches_metadata_or_daily_flow(self):
        before = (self.tmp / "reports" / "metadata.json").read_text(encoding="utf-8")
        self.run_cli("--weekly", "--week", WEEK, "--force", "--date", "2026-11-23")
        self.assertEqual((self.tmp / "reports" / "metadata.json").read_text(encoding="utf-8"), before)
        self.discord.assert_not_called()
        self.assertFalse([u for u in self.api.calls if "/schedule" in u or "transactions?date=" in u or "/boxscore" in u])

    def test_bad_week_and_bad_combinations_exit_1(self):
        for args in (("--weekly", "--week", "2026-47"), ("--week", WEEK), ("--force",), ("--weekly", "--notify-only"),
                     ("--weekly", "--check-wait"), ("--weekly", "--no-notify"), ("--weekly", "--rosters"),
                     ("--weekly", "--notify-weekly"), ("--notify-weekly", "--force")):
            code, _, err = self.run_cli(*args, "--date", "2026-11-23")
            self.assertEqual(code, 1, args)
            self.assertIn("Error", err, args)

    def test_notify_weekly_sends_latest_or_given_week(self):
        fetch_mlb.save_weekly_index({"week": "2026-W46", "start": "2026-11-09", "end": "2026-11-15"})
        fetch_mlb.save_weekly_index({"week": WEEK, "start": "2026-11-16", "end": "2026-11-22"})
        with mock.patch("mlb_daily.notify.send_weekly_notification") as send:
            self.assertIn(self.run_cli("--notify-weekly")[0], (None, 0))
            self.assertEqual(send.call_args[0][0]["week"], WEEK)
            self.run_cli("--notify-weekly", "--week", "2026-W46")
            self.assertEqual(send.call_args[0][0]["week"], "2026-W46")
            self.assertEqual(send.call_count, 2)

    def test_notify_weekly_missing_week_exits_1(self):
        with mock.patch("mlb_daily.notify.send_weekly_notification") as send:
            code, _, err = self.run_cli("--notify-weekly")  # 沒有任何索引
            self.assertEqual(code, 1)
            self.assertIn("找不到", err)
            fetch_mlb.save_weekly_index({"week": WEEK})
            code, _, err = self.run_cli("--notify-weekly", "--week", "2026-W01")
            self.assertEqual(code, 1)
            send.assert_not_called()


# ---------------------------------------------------------------- Discord 週報訊息
class WeeklyNotifyTests(base.MlbTestBase):
    ENTRY = {"week": WEEK, "start": "2026-11-16", "end": "2026-11-22", "days": 4, "tx_count": 12,
             "trade_count": 2, "signing_count": 4}

    def send(self, entry):
        os_env = {"DISCORD_WEBHOOK_URL": "https://discord.test/webhook"}
        sent = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def getcode(self):
                return 204

        def fake_urlopen(req, timeout=None):
            sent["req"] = req
            return Resp()

        with mock.patch.dict("os.environ", os_env), mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                contextlib.redirect_stdout(io.StringIO()):
            fetch_mlb.send_weekly_notification(entry)
        self.assertEqual(sent["req"].full_url, "https://discord.test/webhook")
        return json.loads(sent["req"].data.decode("utf-8"))["embeds"][0]

    def test_message_content(self):
        embed = self.send(self.ENTRY)
        self.assertIn("2026-W47", embed["title"])
        self.assertEqual(embed["url"], "https://haolun588.github.io/MLB-daily/reports/weekly/2026-W47.html")
        self.assertIn("11/16～11/22", embed["description"])
        self.assertIn("交易 **2** 筆", embed["description"])
        self.assertIn("簽約 **4** 筆", embed["description"])
        self.assertIn("資料涵蓋 4／7 天", embed["description"])

    def test_url_matches_daily_report_base(self):
        # 與每日戰報通知相同的網址基底
        daily = self.send_daily()
        self.assertTrue(daily["url"].startswith(fetch_mlb.SITE_BASE_URL))

    def send_daily(self):
        sent = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def getcode(self):
                return 204

        def fake_urlopen(req, timeout=None):
            sent["req"] = req
            return Resp()

        # fetch_mlb.send_discord_notification 是重新匯出的原函式（基底 setUp 只 patch 了 mlb_daily.notify 內的名稱）
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.test/webhook"}), \
                mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                contextlib.redirect_stdout(io.StringIO()):
            fetch_mlb.send_discord_notification({"date": DATE, "total_games": 1, "postponed": 0, "summary": "s", "type": "season"})
        return json.loads(sent["req"].data.decode("utf-8"))["embeds"][0]

    def test_without_optional_fields(self):
        embed = self.send({"week": WEEK})
        self.assertIn("本週人事異動與新聞總整理已生成", embed["description"])
        self.assertNotIn("資料涵蓋", embed["description"])

    def test_no_webhook_skips_with_warning(self):
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("不得連網")), contextlib.redirect_stdout(out):
            fetch_mlb.send_weekly_notification(self.ENTRY)
        self.assertIn("DISCORD_WEBHOOK_URL", out.getvalue())

    def test_send_error_is_swallowed(self):
        err = io.StringIO()
        with mock.patch.dict("os.environ", {"DISCORD_WEBHOOK_URL": "https://discord.test/webhook"}), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("boom")), contextlib.redirect_stderr(err):
            fetch_mlb.send_weekly_notification(self.ENTRY)
        self.assertIn("Error sending Discord weekly notification", err.getvalue())


# ---------------------------------------------------------------- workflow 與匯入規則
class WorkflowAndImportTests(unittest.TestCase):
    def setUp(self):
        self.yml = (base.REPO_ROOT / ".github" / "workflows" / "daily_report.yml").read_text(encoding="utf-8")

    def test_restore_step_restores_daily_snapshots_and_weekly_index(self):
        self.assertIn("mkdir -p reports reports/weekly", self.yml)
        self.assertIn("git archive origin/gh-pages data/daily | tar -x", self.yml)
        self.assertIn("::warning::gh-pages 尚無 data/daily", self.yml)
        self.assertIn("git show origin/gh-pages:reports/weekly/index.json > reports/weekly/index.json", self.yml)
        self.assertIn("rm -f reports/weekly/index.json", self.yml)

    def test_weekly_steps_and_order(self):
        y = self.yml
        order = [y.index(s) for s in ("- name: Generate Daily Report", "- name: Generate Weekly Report",
                                      "- name: Deploy Site to gh-pages", "- name: Wait for GitHub Pages",
                                      "- name: Wait for Weekly Report on GitHub Pages", "- name: Send Discord Notification",
                                      "- name: Send Weekly Discord Notification")]
        self.assertEqual(order, sorted(order))
        step = y[y.index("- name: Generate Weekly Report"):y.index("- name: Update Team Rosters")]
        self.assertIn("id: weekly", step)
        self.assertIn("continue-on-error: true", step)
        self.assertIn("ARGS=(--weekly)", step)
        self.assertIn('ARGS+=(--week "$WEEK")', step)
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", step)  # 週報會重產首頁，需要翻譯
        self.assertIn("set +e", step)
        self.assertIn("rc=${PIPESTATUS[0]}", step)  # tee 不得吃掉 python 的失敗
        self.assertIn("exit $rc", step)
        self.assertIn("weekly_(week|url)", step)
        self.assertIn("$GITHUB_OUTPUT", step)
        self.assertIn("$RUNNER_TEMP", step)  # log 放在工作目錄之外，避免被部署
        self.assertIn("week:", y[:y.index("permissions:")])  # workflow_dispatch 輸入
        notify = y[y.index("- name: Send Weekly Discord Notification"):]
        self.assertIn("steps.weekly.outputs.weekly_url != ''", notify)
        self.assertIn("github.event_name == 'schedule' || inputs.notify == true", notify)
        self.assertIn('--notify-weekly --week "$WEEKLY_WEEK"', notify)

    def test_weekly_module_respects_layering(self):
        tree = ast.parse((base.REPO_ROOT / "mlb_daily" / "weekly.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
                imported.update(a.name for a in node.names)
        self.assertTrue(imported)
        self.assertEqual(imported & {"report", "cli", "index_page", "notify"}, set())


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- 下週日程自動帶入
def _seasons_fetch(year_payloads):
    """fetch_json 的替身：/seasons/{year} 依 year_payloads，其餘回 None；記錄呼叫的 URL。"""
    calls = []

    def fetch(url, retries=3, delay=5):
        calls.append(url)
        for y, payload in year_payloads.items():
            if f"/seasons/{y}" in url:
                return payload
        return None
    fetch.calls = calls
    return fetch


SEASON_2027 = {"seasons": [{"springStartDate": "2027-02-19", "regularSeasonStartDate": "2027-03-25",
                            "regularSeasonEndDate": "2027-09-26", "postSeasonStartDate": "2027-09-27",
                            "postSeasonEndDate": "2027-10-31"}]}


def _write_snapshot(directory, day):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{day}.json").write_text(json.dumps(
        {"schema": 1, "date": day, "has_games": False, "transactions": [], "news": []}), encoding="utf-8")


class AutoCalendarTests(base.MlbTestBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(season_mod.disable_cache)
        self.daily = self.tmp / "data" / "daily"
        _write_snapshot(self.daily, "2027-02-09")  # 2027-W06（2/8～2/14）；其下週 W07 為 2/15～2/21，涵蓋 2/19
        self.cal = self.tmp / "cal.json"
        self.cal.write_text(json.dumps({"events": []}), encoding="utf-8")

    def gen(self, payloads, week="2027-W06", today=date(2027, 2, 15)):
        fetch = _seasons_fetch(payloads)
        with mock.patch("mlb_daily.net.fetch_json", side_effect=fetch):
            entry = quiet(weekly.generate_weekly_report, today, week=week, force=True,
                          calendar_path=str(self.cal), wl={})
        self.assertIsNotNone(entry)
        return (self.tmp / "reports" / "weekly" / f"{week}.html").read_text(encoding="utf-8"), fetch

    def test_next_week_covers_spring_start(self):
        self.assertEqual(weekly.week_days("2027-W07")[0], date(2027, 2, 15))
        html, _ = self.gen({2027: SEASON_2027})
        self.assertIn("春訓熱身賽開打", html)
        self.assertIn("2/19", html)
        self.assertNotIn("下週無排定重要日程", html)

    def test_unannounced_season_does_not_fail(self):
        self.cal.write_text(json.dumps({"events": [{"date": "2027-02-16", "title_zh": "手動事件"}]}), encoding="utf-8")
        html, _ = self.gen({2027: {"seasons": []}})
        self.assertIn("手動事件", html)
        self.assertNotIn("春訓熱身賽開打", html)

    def test_network_failure_falls_back_to_manual(self):
        self.cal.write_text(json.dumps({"events": [{"date": "2027-02-16", "title_zh": "手動事件"}]}), encoding="utf-8")
        html, _ = self.gen({})
        self.assertIn("手動事件", html)

    def test_manual_id_overrides_auto(self):
        self.cal.write_text(json.dumps({"events": [
            {"id": "spring_start", "date": "2027-02-18", "title_zh": "春訓提前", "note": "手動"}]}), encoding="utf-8")
        html, _ = self.gen({2027: SEASON_2027})
        self.assertIn("春訓提前", html)
        self.assertNotIn("春訓熱身賽開打", html)

    def test_injected_auto_events_skip_network(self):
        with mock.patch("mlb_daily.net.fetch_json", side_effect=AssertionError("不得連網")):
            quiet(weekly.generate_weekly_report, date(2027, 2, 15), week="2027-W06", force=True,
                  calendar_path=str(self.cal), wl={}, auto_events=[])

    def test_auto_failure_only_warns(self):
        with mock.patch.object(season_mod, "season_calendar_events", side_effect=RuntimeError("boom")):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                entry = weekly.generate_weekly_report(date(2027, 2, 15), week="2027-W06", force=True,
                                                      calendar_path=str(self.cal), wl={})
        self.assertIsNotNone(entry)
        self.assertIn("自動帶入失敗", err.getvalue())

    def test_year_boundary_queries_both_years(self):
        week = "2026-W52"  # 週日 2026-12-27，下週 12/28～1/3 跨年
        self.assertEqual(weekly.week_days(week)[-1], date(2026, 12, 27))
        _write_snapshot(self.daily, "2026-12-21")
        _, fetch = self.gen({}, week=week, today=date(2026, 12, 28))
        queried = {int(u.split("/seasons/")[1][:4]) for u in fetch.calls if "/seasons/" in u}
        self.assertEqual(queried, {2026, 2027})


class MergeCalendarTests(unittest.TestCase):
    def ev(self, d, title, id=None):
        return {"date": d, "title_zh": title, "note": "", "id": id}

    def test_manual_id_removes_auto(self):
        auto = [self.ev(date(2027, 2, 19), "春訓熱身賽開打", "spring_start")]
        manual = [self.ev(date(2027, 2, 18), "春訓提前", "spring_start")]
        self.assertEqual([e["title_zh"] for e in weekly.merge_calendar(auto, manual)], ["春訓提前"])

    def test_same_date_and_title_deduped_and_sorted(self):
        auto = [self.ev(date(2027, 3, 25), "例行賽開幕日", "opening_day"),
                self.ev(date(2027, 2, 19), "春訓熱身賽開打", "spring_start")]
        manual = [self.ev(date(2027, 3, 25), "例行賽開幕日")]
        merged = weekly.merge_calendar(auto, manual)
        self.assertEqual([(e["date"], e["title_zh"]) for e in merged],
                         [(date(2027, 2, 19), "春訓熱身賽開打"), (date(2027, 3, 25), "例行賽開幕日")])

    def test_load_calendar_keeps_optional_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({"events": [{"date": "2027-02-18", "title_zh": "a", "id": "spring_start"},
                                                {"date": "2027-02-19", "title_zh": "b"}]}), encoding="utf-8")
            ev = weekly.load_calendar(str(p))
        self.assertEqual([e["id"] for e in ev], ["spring_start", None])
