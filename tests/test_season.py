"""賽季日期快取、單日 context 快取與 resolve_roster_context 的測試：不連網，快取檔都放暫存目錄。"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

fetch_mlb = base.fetch_mlb
from mlb_daily import index_page, rosters, season  # noqa: E402

START, END = "2026-03-25", "2026-11-04"


def seasons_resp(start=START, end=END):
    return {"seasons": [{"regularSeasonStartDate": start, "postSeasonEndDate": end}]}


def sched(*days):
    """days: (日期字串, [detailedState 或 (abstract, detailed)])"""
    out = []
    for d, states in days:
        games = []
        for st in states:
            abstract, detailed = st if isinstance(st, tuple) else ("Preview", st)
            games.append({"status": {"abstractGameState": abstract, "detailedState": detailed}})
        out.append({"date": d, "games": games})
    return {"dates": out}


class FakeNet:
    """依 URL 回應：/seasons/{year}（seasons，缺年份代表失敗）、/schedule（schedule，None 代表失敗）。"""

    def __init__(self):
        self.seasons = {2026: seasons_resp()}
        self.schedule = {"dates": []}
        self.calls = []

    def fetch(self, url, retries=3, delay=5):
        self.calls.append(url)
        if "/seasons/" in url:
            return self.seasons.get(int(url.split("/seasons/")[1][:4]))
        if "/schedule" in url:
            return self.schedule
        return None

    def count(self, part):
        return sum(1 for c in self.calls if part in c)


class SeasonTestBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.cache_path = str(self.tmp / "data" / "season_cache.json")
        self.net = FakeNet()
        p = mock.patch("mlb_daily.net.fetch_json", side_effect=self.net.fetch)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(season.disable_cache)

    def enable(self):
        season.enable_cache(self.cache_path)

    def write_cache(self, data):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        with open(self.cache_path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def read_cache(self):
        with open(self.cache_path, encoding="utf-8") as f:
            return json.load(f)

    def resolve(self, today, *args):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            ctx = rosters.resolve_roster_context(today, *args)
        self.errs = err.getvalue()
        return ctx


class ResolveContextTests(SeasonTestBase):
    def test_before_open_and_after_end(self):
        self.assertEqual(self.resolve(date(2026, 3, 24)), {"rosterType": "40Man", "season": 2025, "rosterSeason": 2026})
        self.assertEqual(self.resolve(date(2026, 12, 31)), {"rosterType": "40Man", "season": 2026, "rosterSeason": 2026})

    def test_new_year_before_next_season(self):
        self.net.seasons[2027] = seasons_resp("2027-03-25", "2027-11-04")
        self.assertEqual(self.resolve(date(2027, 1, 1)), {"rosterType": "40Man", "season": 2026, "rosterSeason": 2027})

    def test_opening_day_with_games_today_is_active_and_confirmed(self):
        self.enable()
        self.net.schedule = sched(("2026-03-25", ["Scheduled", ("Live", "In Progress")]))
        self.assertEqual(self.resolve(date(2026, 3, 25)), {"rosterType": "active", "season": 2026, "rosterSeason": 2026})
        self.assertTrue(self.read_cache()["years"]["2026"]["confirmedStarted"])

    def test_final_game_earlier_confirms_start(self):
        self.net.schedule = sched(("2026-03-25", [("Final", "Final")]))
        self.assertEqual(self.resolve(date(2026, 7, 1))["rosterType"], "active")
        self.assertIn("startDate=2026-03-25", self.net.calls[-1])
        self.assertIn("gameType=R", self.net.calls[-1])

    def test_stoppage_in_range_but_no_games_is_40man_previous_season(self):
        for sc in ({"dates": []}, sched(("2026-04-10", ["Postponed", "Cancelled"]))):
            self.net.schedule = sc
            ctx = self.resolve(date(2026, 4, 10))
            self.assertEqual(ctx, {"rosterType": "40Man", "season": 2025, "rosterSeason": 2026})
            self.assertIn("Warning", self.errs)

    def test_confirmed_started_cache_skips_schedule(self):
        self.write_cache({"years": {"2026": {"regularSeasonStartDate": START, "postSeasonEndDate": END,
                                              "confirmedStarted": True}}})
        self.enable()
        self.net.schedule = {"dates": []}
        self.assertEqual(self.resolve(date(2026, 7, 1))["rosterType"], "active")
        self.assertEqual(self.net.count("/schedule"), 0)

    def test_schedule_failure_or_bad_format_assumes_active_without_confirming(self):
        for bad in (None, {"oops": 1}, {"dates": "x"}):
            self.enable()
            self.net.schedule = bad
            self.assertEqual(self.resolve(date(2026, 7, 1))["rosterType"], "active")
            self.assertIn("Warning", self.errs)
            self.assertFalse(season.confirmed_started(2026))
            if os.path.exists(self.cache_path):
                os.remove(self.cache_path)

    def test_seasons_api_failure_uses_cached_dates(self):
        self.write_cache({"years": {"2026": {"regularSeasonStartDate": START, "postSeasonEndDate": END}}})
        self.net.seasons = {}
        self.net.schedule = None
        cases = [(date(2026, 3, 24), "40Man", 2025), (date(2026, 3, 25), "active", 2026),
                 (date(2026, 11, 2), "active", 2026), (date(2026, 11, 5), "40Man", 2026)]
        for today, roster_type, yr in cases:
            self.enable()  # 每次重新啟用 = 清 process 記憶，模擬新的一天／新的 process
            ctx = self.resolve(today)
            self.assertEqual((ctx["rosterType"], ctx["season"]), (roster_type, yr), today)
            self.assertIn("改用快取", self.errs)

    def test_seasons_api_failure_without_cache_uses_month_estimate_and_skips_context_cache(self):
        self.enable()
        self.net.seasons = {}
        self.assertEqual(self.resolve(date(2026, 11, 18)), {"rosterType": "40Man", "season": 2026, "rosterSeason": 2026})
        self.assertEqual(self.resolve(date(2026, 1, 15)), {"rosterType": "40Man", "season": 2025, "rosterSeason": 2026})
        self.assertIn("月份推估", self.errs)
        self.assertFalse(os.path.exists(self.cache_path))

    def test_empty_seasons_list_and_missing_dates_fall_back(self):
        for data in ({"seasons": []}, {"seasons": [{}]}, {"seasons": [{"regularSeasonStartDate": START}]}, {}):
            self.net.seasons = {2026: data}
            self.assertEqual(self.resolve(date(2026, 7, 1))["rosterType"], "active", data)
            self.assertIn("月份推估", self.errs)

    def test_season_end_falls_back_to_season_end_date(self):
        self.net.seasons = {2026: {"seasons": [{"regularSeasonStartDate": START, "seasonEndDate": "2026-09-27"}]}}
        self.assertEqual(self.resolve(date(2026, 10, 1))["rosterType"], "40Man")

    def test_same_day_context_cache_hit_and_new_process(self):
        self.enable()
        self.net.schedule = sched(("2026-07-01", ["Scheduled"]))
        first = self.resolve(date(2026, 7, 1))
        n = len(self.net.calls)
        self.assertEqual(self.resolve(date(2026, 7, 1)), first)
        self.assertEqual(self.resolve(date(2026, 7, 1), None), first)  # 傳入 season_data 也不影響
        self.assertEqual(len(self.net.calls), n)
        self.assertEqual(self.read_cache()["context"], {"date": "2026-07-01", "ctx": first})
        season.enable_cache(self.cache_path)  # 模擬下一個 process：從檔案讀回
        self.assertEqual(self.resolve(date(2026, 7, 1)), first)
        self.assertEqual(len(self.net.calls), n)
        self.resolve(date(2026, 7, 2))  # 隔天重算
        self.assertGreater(len(self.net.calls), n)

    def test_cache_disabled_reads_and_writes_nothing(self):
        old = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, old)
        self.write_cache({"years": {"2026": {"regularSeasonStartDate": "2026-01-01", "postSeasonEndDate": "2026-01-02"}},
                          "context": {"date": "2026-07-01",
                                      "ctx": {"rosterType": "40Man", "season": 1, "rosterSeason": 1}}})
        before = Path(self.cache_path).read_bytes()
        self.net.schedule = sched(("2026-07-01", ["Scheduled"]))
        self.assertEqual(self.resolve(date(2026, 7, 1)), {"rosterType": "active", "season": 2026, "rosterSeason": 2026})
        self.assertEqual(Path(self.cache_path).read_bytes(), before)
        self.assertEqual(os.listdir(self.tmp / "data"), ["season_cache.json"])

    def test_cache_disabled_creates_no_file(self):
        self.net.schedule = sched(("2026-07-01", ["Scheduled"]))
        self.resolve(date(2026, 7, 1))
        self.assertFalse((self.tmp / "data").exists())

    def test_corrupt_cache_file_is_treated_as_empty(self):
        for content in ("{broken", "[]", '{"years": 5, "context": "x"}', '{"years": {"2026": 3}}', ""):
            self.write_cache(content)
            self.enable()
            self.net.schedule = sched(("2026-07-01", ["Scheduled"]))
            self.assertEqual(self.resolve(date(2026, 7, 1))["rosterType"], "active", content)
            self.assertEqual(self.read_cache()["context"]["date"], "2026-07-01")  # 已被正常內容覆寫

    def test_write_failure_does_not_raise(self):
        self.enable()
        self.net.schedule = sched(("2026-07-01", ["Scheduled"]))
        with mock.patch("mlb_daily.storage.write_json_atomic", side_effect=OSError("disk")):
            self.assertEqual(self.resolve(date(2026, 7, 1))["rosterType"], "active")
        self.assertIn("無法寫入賽季快取", self.errs)

    def test_get_season_dates_preserves_confirmed_started_and_uses_given_data(self):
        self.write_cache({"years": {"2026": {"confirmedStarted": True}}})
        self.enable()
        dates = season.get_season_dates(2026, seasons_resp("2026-04-01", "2026-10-01"))
        self.assertEqual(dates, (date(2026, 4, 1), date(2026, 10, 1)))
        self.assertEqual(self.net.calls, [])
        entry = self.read_cache()["years"]["2026"]
        self.assertTrue(entry["confirmedStarted"])
        self.assertEqual(entry["regularSeasonStartDate"], "2026-04-01")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(season.get_season_dates(2027, None))


class FallbackContextTests(SeasonTestBase):
    def fallback(self, today):
        with contextlib.redirect_stderr(io.StringIO()):
            return index_page._fallback_roster_context(today)

    def test_uses_cached_dates_instead_of_month_guess(self):
        # 3 月底開幕：月份推估會判成 40Man／2025，快取日期則判為賽季中
        self.write_cache({"years": {"2026": {"regularSeasonStartDate": "2026-03-26", "postSeasonEndDate": END}}})
        self.enable()
        self.net.seasons = {}
        self.net.schedule = None
        self.assertEqual(self.fallback(date(2026, 3, 28)), {"rosterType": "active", "season": 2026})

    def test_uses_same_day_context_cache(self):
        self.write_cache({"context": {"date": "2026-11-23",
                                      "ctx": {"rosterType": "40Man", "season": 2026, "rosterSeason": 2026}}})
        self.enable()
        self.net.seasons = {}
        self.assertEqual(self.fallback(date(2026, 11, 23)), {"rosterType": "40Man", "season": 2026})
        self.assertEqual(self.net.calls, [])

    def test_exception_falls_back_to_month(self):
        with mock.patch("mlb_daily.rosters.resolve_roster_context", side_effect=RuntimeError("x")):
            self.assertEqual(self.fallback(date(2027, 2, 10)), {"rosterType": "40Man", "season": 2026})
            self.assertEqual(self.fallback(date(2027, 4, 1)), {"rosterType": "active", "season": 2027})

    def test_no_cache_no_api_falls_back_to_month(self):
        self.net.seasons = {}
        self.assertEqual(self.fallback(date(2026, 11, 18)), {"rosterType": "40Man", "season": 2026})


class CliCacheTests(unittest.TestCase):
    def test_main_enables_cache_with_config_path(self):
        with mock.patch("mlb_daily.season.enable_cache") as enable, \
                mock.patch.object(sys, "argv", ["fetch_mlb.py", "--leaders", "--date", "2026-11-23"]), \
                mock.patch("mlb_daily.cli._run_leaders") as run, \
                contextlib.redirect_stdout(io.StringIO()):
            fetch_mlb.main()
        enable.assert_called_once_with(os.path.join("data", "season_cache.json"))
        run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
