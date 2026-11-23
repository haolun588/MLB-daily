"""休兵日（rest_day）類型測試：賽季中無比賽日的分類、頁面、metadata、首頁、通知與 cli 分流。全部不連網。"""

import contextlib
import io
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402
import test_season as ts  # noqa: E402

fetch_mlb = base.fetch_mlb
from mlb_daily import index_page, report, season, storage  # noqa: E402

FINAL = ("Final", "Final")


def full_season(start="2026-03-25", end="2026-10-28"):
    return {"seasons": [{
        "regularSeasonStartDate": start, "lastDate1stHalf": "2026-07-12", "allStarDate": "2026-07-14",
        "firstDate2ndHalf": "2026-07-16", "regularSeasonEndDate": "2026-09-27",
        "postSeasonStartDate": "2026-09-28", "postSeasonEndDate": end}]}


class RangeNet(ts.FakeNet):
    """schedule 依 startDate 回應：prev 回應給 endDate < 報表日者；以 (start, end) 區分前後段由 by_range 決定。"""

    def __init__(self):
        super().__init__()
        self.seasons = {2026: full_season()}
        self.prev = {"dates": []}
        self.next = {"dates": []}
        self.day = None

    def fetch(self, url, retries=3, delay=5):
        if "/schedule" in url:
            self.calls.append(url)
            end = re.search(r"endDate=([\d-]+)", url).group(1)
            return self.prev if end < self.day else self.next
        return super().fetch(url, retries, delay)


class ClassifyTests(ts.SeasonTestBase):
    def setUp(self):
        super().setUp()
        self.net = RangeNet()
        p = mock.patch("mlb_daily.net.fetch_json", side_effect=self.net.fetch)
        p.start()
        self.addCleanup(p.stop)

    def classify(self, day, prev=None, nxt=None):
        self.net.day = day
        if prev is not None:
            self.net.prev = prev
        if nxt is not None:
            self.net.next = nxt
        with contextlib.redirect_stderr(io.StringIO()) as err:
            out = season.classify_no_game_day(day)
        self.errs = err.getvalue()
        return out

    def played(self, day="2026-09-27"):
        return ts.sched((day, [FINAL]))

    def upcoming(self, day="2026-09-29"):
        return ts.sched((day, ["Scheduled"]))

    def test_postseason_break(self):
        self.assertEqual(self.classify("2026-09-28", self.played(), self.upcoming()), ("rest_day", "季後賽休兵"))

    def test_all_star_break(self):
        self.assertEqual(self.classify("2026-07-14", self.played("2026-07-12"), self.upcoming("2026-07-16")),
                         ("rest_day", "明星賽假期"))

    def test_regular_season_rest_day(self):
        self.assertEqual(self.classify("2026-05-05", self.played("2026-05-04"), self.upcoming("2026-05-06")),
                         ("rest_day", "例行賽休兵日"))

    def test_between_postseason_rounds(self):
        self.assertEqual(self.classify("2026-10-22", self.played("2026-10-21"), self.upcoming("2026-10-24")),
                         ("rest_day", "季後賽休兵"))

    def test_query_urls_cover_all_game_types_and_windows(self):
        self.classify("2026-10-22", self.played("2026-10-21"), self.upcoming("2026-10-24"))
        urls = [c for c in self.net.calls if "/schedule" in c]
        self.assertEqual(len(urls), 2)
        self.assertIn("gameType=R,F,D,L,W", urls[0])
        self.assertIn("startDate=2026-10-08&endDate=2026-10-21", urls[0])
        self.assertIn("startDate=2026-10-23&endDate=2026-10-28", urls[1])  # 後段不超過 postSeasonEndDate

    def test_world_series_ended_early_before_post_season_end(self):
        # 前段有完賽，但後段到 postSeasonEndDate 都沒有比賽
        self.assertEqual(self.classify("2026-10-26", self.played("2026-10-25"), {"dates": []}), ("offseason", None))

    def test_only_postponed_or_cancelled_after_is_offseason(self):
        nxt = ts.sched(("2026-10-27", ["Postponed", "Cancelled"]))
        self.assertEqual(self.classify("2026-10-26", self.played("2026-10-25"), nxt), ("offseason", None))

    def test_on_post_season_end_date_skips_query(self):
        self.assertEqual(self.classify("2026-10-28", self.played("2026-10-27")), ("offseason", None))
        self.assertEqual(self.net.count("/schedule"), 0)

    def test_outside_season_range(self):
        for day in ("2026-11-23", "2026-03-24", "2026-10-29"):
            self.assertEqual(self.classify(day), ("offseason", None))
        self.assertEqual(self.net.count("/schedule"), 0)

    def test_delayed_opening_without_final_games_before(self):
        self.assertEqual(self.classify("2026-04-10", ts.sched(("2026-04-05", ["Postponed"])), self.upcoming("2026-04-12")),
                         ("offseason", None))
        self.assertEqual(self.classify("2026-04-10", {"dates": []}, self.upcoming("2026-04-12")), ("offseason", None))

    def test_schedule_failure_falls_back_to_rest_day_with_warning(self):
        for prev, nxt in ((None, self.upcoming()), (self.played(), None), (None, None), ({"oops": 1}, {"dates": "x"})):
            self.assertEqual(self._classify_failing("2026-09-28", prev, nxt), ("rest_day", "季後賽休兵"))
            self.assertIn("Warning", self.errs)

    def _classify_failing(self, day, prev, nxt):
        self.net.day = day
        self.net.prev, self.net.next = prev, nxt
        with contextlib.redirect_stderr(io.StringIO()) as err:
            out = season.classify_no_game_day(day)
        self.errs = err.getvalue()
        return out

    def test_failure_of_one_segment_but_other_empty_is_offseason(self):
        self.assertEqual(self._classify_failing("2026-10-26", None, {"dates": []}), ("offseason", None))

    def test_seasons_api_failure_uses_cached_info(self):
        self.enable()
        self.classify("2026-09-28", self.played(), self.upcoming())  # 成功一次，寫入快取
        cached = self.read_cache()["years"]["2026"]
        self.assertEqual((cached["lastDate1stHalf"], cached["firstDate2ndHalf"], cached["postSeasonStartDate"]),
                         ("2026-07-12", "2026-07-16", "2026-09-28"))
        season.enable_cache(self.cache_path)  # 清掉 process 內記憶
        self.net.seasons = {}
        self.assertEqual(self.classify("2026-07-14", self.played("2026-07-12"), self.upcoming("2026-07-16")),
                         ("rest_day", "明星賽假期"))
        self.assertIn("改用快取", self.errs)

    def test_seasons_api_failure_without_cache_is_offseason(self):
        self.net.seasons = {}
        self.assertEqual(self.classify("2026-09-28", self.played(), self.upcoming()), ("offseason", None))

    def test_missing_info_fields_give_generic_reason(self):
        self.net.seasons = {2026: ts.seasons_resp("2026-03-25", "2026-10-28")}
        self.assertEqual(self.classify("2026-09-28", self.played(), self.upcoming()), ("rest_day", "休兵日"))

    def test_get_season_dates_interface_unchanged_and_info(self):
        self.net.seasons = {2026: full_season()}
        dates = season.get_season_dates(2026)
        self.assertEqual([x.isoformat() for x in dates], ["2026-03-25", "2026-10-28"])
        self.assertEqual(season.get_season_info(2026)["lastDate1stHalf"], "2026-07-12")


def raw_sched(day, *statuses):
    return {"dates": [{"date": day, "games": [{"status": st} for st in statuses]}]}


POSTPONED = {"abstractGameState": "Final", "detailedState": "Postponed", "codedGameState": "D"}
CANCELLED = {"abstractGameState": "Final", "detailedState": "Cancelled", "codedGameState": "C"}
POSTPONED_NO_CODE = {"abstractGameState": "Final", "detailedState": "Postponed: Rain"}


class StatusHelperTests(unittest.TestCase):
    def test_helpers(self):
        for st in (POSTPONED, CANCELLED, POSTPONED_NO_CODE, {"codedGameState": "D"}, {"detailedState": "Cancelled"}):
            self.assertTrue(season.is_cancelled_or_postponed(st), st)
            self.assertFalse(season.is_completed(st), st)
        done = {"abstractGameState": "Final", "detailedState": "Final", "codedGameState": "F"}
        self.assertTrue(season.is_completed(done))
        self.assertFalse(season.is_cancelled_or_postponed(done))
        self.assertFalse(season.is_completed({"abstractGameState": "Live", "detailedState": "In Progress"}))
        self.assertFalse(season.is_cancelled_or_postponed({"abstractGameState": "Preview", "detailedState": "Scheduled"}))
        self.assertFalse(season.is_completed(None))


class PostponedNotCompletedTests(ClassifyTests):
    def test_postponed_or_cancelled_before_is_not_completed(self):
        for st in (POSTPONED, CANCELLED, POSTPONED_NO_CODE):
            self.assertEqual(self.classify("2026-04-10", raw_sched("2026-04-09", st), self.upcoming("2026-04-12")),
                             ("offseason", None))

    def test_postponed_or_cancelled_after_is_not_a_game(self):
        for st in (POSTPONED, CANCELLED, POSTPONED_NO_CODE):
            self.assertEqual(self.classify("2026-10-26", self.played("2026-10-25"), raw_sched("2026-10-27", st)),
                             ("offseason", None))

    def test_range_url_requests_coded_game_state(self):
        self.classify("2026-09-28", self.played(), self.upcoming())
        self.assertIn("codedGameState", [c for c in self.net.calls if "/schedule" in c][0])


class RestDayPageTests(base.MlbTestBase):
    DAY = "2026-09-28"

    def test_render_report_title_and_summary(self):
        html = fetch_mlb.render_report(self.DAY, "rest_day", news=[], rest_reason="季後賽休兵")
        self.assertIn("MLB 每日戰報 - 2026-09-28 休兵日", html)
        self.assertIn("<div><dt>賽程</dt><dd>季後賽休兵</dd></div>", html)
        self.assertIn("<div><dt>新聞</dt><dd>0 則</dd></div>", html)
        self.assertIn("<div><dt>人事異動</dt><dd>0 筆</dd></div>", html)
        self.assertLess(html.index("<dt>賽程</dt>"), html.index("<dt>新聞</dt>"))
        self.assertIn("本日無大聯盟人事異動。", html)
        self.assertNotIn("休賽季快報", html)

    def test_render_summary_omits_news_when_none_and_escapes_reason(self):
        out = report.render_summary("rest_day", transactions_count=2, rest_reason="<b>x</b>")
        self.assertNotIn("新聞", out)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", out)

    def test_news_lead_story_like_offseason(self):
        self.api.news = "news_rss.xml"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            meta = report.generate_rest_day_report(base.DATE, "季後賽休兵")
        html = self.read_report(base.DATE)
        self.assertIn('class="lead-story"', html)
        self.assertIn('class="news is-two-col"', html)
        self.assertEqual(meta["type"], "rest_day")
        self.assertEqual(meta["rest_reason"], "季後賽休兵")
        self.assertEqual((meta["total_games"], meta["postponed"]), (0, 0))
        self.assertEqual(meta["news_count"], 3)
        self.assertIn("transactions_count", meta)
        self.assertIn("translated", meta)
        self.assertTrue(meta["summary"].endswith("。"))

    def test_metadata_without_news_and_summaries(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            meta = report.generate_rest_day_report(self.DAY, "例行賽休兵日")
        self.assertNotIn("news_count", meta)
        self.assertEqual(meta["summary"], "休兵日，無重大消息")
        self.assertEqual(report.build_rest_day_summary([], 4), "休兵日：人事異動 4 筆")
        self.assertEqual(report.build_rest_day_summary(None, 0), "休兵日，無重大消息")
        self.assertEqual(report.build_rest_day_summary([{"title": "T1"}, {"title": "T2", "title_zh": "譯2"}], 1), "T1；譯2。")
        self.assertEqual(report.build_offseason_summary([], 0), "休賽季，無重大消息")

    def test_snapshot_has_games_false(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            report.generate_rest_day_report(self.DAY, "季後賽休兵")
        snap = json.loads((self.tmp / "data" / "daily" / f"{self.DAY}.json").read_text(encoding="utf-8"))
        self.assertIs(snap["has_games"], False)

    def test_offseason_report_unchanged(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            meta = report.generate_offseason_report("2026-11-15")
        self.assertEqual(meta["type"], "offseason")
        self.assertNotIn("rest_reason", meta)
        self.assertIn("休賽季快報", self.read_report("2026-11-15"))


class EntryTypeAndIndexTests(base.MlbTestBase):
    ENTRY = {"date": "2026-09-28", "total_games": 0, "postponed": 0, "summary": "休兵日，無重大消息",
             "type": "rest_day", "rest_reason": "季後賽休兵", "news_count": 3, "transactions_count": 2}

    def test_entry_type(self):
        self.assertEqual(storage.entry_type(self.ENTRY), "rest_day")
        self.assertEqual(storage.entry_type({"total_games": 0}), "offseason")
        self.assertEqual(storage.entry_type({"total_games": 2}), "season")

    def test_latest_card(self):
        out = index_page.render_latest_card("2026-09-28", self.ENTRY)
        self.assertIn("最新一期", out)
        self.assertIn("/</span>休兵日</p>", out)
        self.assertIn("<dt>新聞</dt><dd>3 則</dd>", out)
        self.assertIn("<dt>人事異動</dt><dd>2 筆</dd>", out)

    def test_recent_item(self):
        out = index_page.render_recent_item("2026-09-28", self.ENTRY)
        self.assertIn('<span class="k is-off">休兵日</span>', out)
        self.assertIn("（新聞 3 則、人事異動 2 筆）", out)
        off = index_page.render_recent_item("2026-11-15", dict(self.ENTRY, type="offseason"))
        self.assertIn('<span class="k is-off">休賽季</span>', off)


class NotifyTests(base.MlbTestBase):
    DAY = "2026-09-28"

    def setUp(self):
        self._real_send = fetch_mlb.send_discord_notification
        self._real_should = fetch_mlb.notify.should_notify
        super().setUp()

    def entry(self, **kw):
        e = {"date": self.DAY, "total_games": 0, "postponed": 0, "summary": "s", "type": "rest_day"}
        e.update(kw)
        return e

    def test_should_notify(self):
        should = self._real_should
        self.assertFalse(should(self.entry(news_count=0, transactions_count=0)))
        self.assertFalse(should(self.entry()))
        self.assertTrue(should(self.entry(news_count=1)))
        self.assertTrue(should(self.entry(transactions_count=1)))

    def send(self, entry):
        import os
        os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.test/webhook"
        sent = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def getcode(self):
                return 204

        def fake(req, timeout=None):
            sent["req"] = req
            return Resp()

        with mock.patch("urllib.request.urlopen", side_effect=fake), contextlib.redirect_stdout(io.StringIO()):
            self._real_send(entry)
        return json.loads(sent["req"].data.decode("utf-8"))["embeds"][0]

    def test_description(self):
        embed = self.send(self.entry(news_count=3, transactions_count=4))
        self.assertEqual(embed["description"], "休兵日快報：新聞 3 則、人事異動 4 筆")
        self.assertEqual(embed["fields"], [])
        self.assertEqual(self.send(self.entry(transactions_count=2))["description"], "休兵日快報：人事異動 2 筆")
        self.assertEqual(self.send(self.entry(news_count=5))["description"], "休兵日快報：新聞 5 則")
        self.assertEqual(self.send(self.entry())["description"], "休兵日快報已生成。")

    def test_notify_only_skips_empty_rest_day(self):
        (self.tmp / "reports" / "metadata.json").write_text(
            json.dumps({self.DAY: self.entry(news_count=0, transactions_count=0)}), encoding="utf-8")
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", self.DAY, "--notify-only"]), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            fetch_mlb.main()
        self.discord.assert_not_called()
        self.assertIn("休兵日快報", out.getvalue())
        self.assertIn("不發送", out.getvalue())


class CliRoutingTests(base.MlbTestBase):
    DAY = base.DATE

    def run_day(self, classified, news=None):
        self.api.schedule = "schedule_empty"
        self.api.transactions = "transactions_empty"
        self.api.news = news
        with mock.patch("mlb_daily.season.classify_no_game_day", return_value=classified) as c, \
                mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", self.DAY]), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                fetch_mlb.main()
        self.assertEqual(cm.exception.code, 0)
        c.assert_called_once_with(self.DAY)
        return out.getvalue()

    def test_rest_day_branch(self):
        out = self.run_day(("rest_day", "季後賽休兵"), news="news_rss.xml")
        meta = self.read_metadata()[self.DAY]
        self.assertEqual((meta["type"], meta["rest_reason"]), ("rest_day", "季後賽休兵"))
        self.assertIn("休兵日", self.read_report(self.DAY))
        self.assertIn("休兵日", (self.tmp / "index.html").read_text(encoding="utf-8"))
        self.discord.assert_called_once()
        self.assertIn("rest day", out)

    def test_rest_day_empty_not_sent(self):
        out = self.run_day(("rest_day", "休兵日"))
        self.discord.assert_not_called()
        self.assertIn("休兵日快報無新聞、無人事異動，不發送", out)

    def test_offseason_branch(self):
        self.run_day(("offseason", None), news="news_rss.xml")
        self.assertEqual(self.read_metadata()[self.DAY]["type"], "offseason")
        self.assertIn("休賽季快報", self.read_report(self.DAY))

    def test_real_classify_end_to_end_with_mocked_ranges(self):
        self.api.seasons_data = full_season()
        self.api.schedule = "schedule_empty"
        self.api.transactions = "transactions_empty"
        played = ts.sched(("2026-09-27", [FINAL]))
        upcoming = ts.sched(("2026-09-29", ["Scheduled"]))
        with mock.patch("mlb_daily.season._range_games",
                        side_effect=[[g["status"] for d in played["dates"] for g in d["games"]],
                                     [g["status"] for d in upcoming["dates"] for g in d["games"]]]), \
                mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", "2026-09-28", "--no-notify"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fetch_mlb.main()
        meta = self.read_metadata()["2026-09-28"]
        self.assertEqual((meta["type"], meta["rest_reason"]), ("rest_day", "季後賽休兵"))


if __name__ == "__main__":
    unittest.main()
