"""數據排行榜測試：只用 unittest 與 tests/fixtures/，不連網。
所有寫檔都在暫存目錄（MlbTestBase 會 chdir 進去），不污染 repo 的 data/、leaders.html。"""

import ast
import contextlib
import copy
import io
import json
import re
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

fetch_mlb = base.fetch_mlb
leaders = fetch_mlb.leaders
DATE = base.DATE
TODAY = date(2026, 11, 18)
HITTING_KEYS = ("battingAverage", "onBasePercentage", "homeRuns", "onBasePlusSlugging", "stolenBases", "war_hitting")
PITCHING_KEYS = ("earnedRunAverage", "strikeouts", "saves", "strikeoutsPer9Inn", "fip", "war_pitching")
ALL_KEYS = HITTING_KEYS + PITCHING_KEYS


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


def names(entries):
    return [e["name"] for e in entries]


class LeadersTestBase(base.MlbTestBase):
    """在 MlbTestBase 的假 API 之上，加上排行榜五個端點的 fixture 回應。"""

    FIXTURE_FOR = {
        "hitting_leaders": "leaders_hitting",
        "pitching_leaders": "leaders_pitching",
        "fip": "sabermetrics_pitching_qualified",
        "war_hitting": "sabermetrics_hitting_all",
        "war_pitching": "sabermetrics_pitching_all",
    }

    def setUp(self):
        super().setUp()
        self.lead = dict(self.FIXTURE_FOR)  # 端點 -> fixture 名稱、dict 或 None（失敗）
        self.lead_calls = []
        p = mock.patch("mlb_daily.net.fetch_json", side_effect=self._fetch)
        p.start()
        self.addCleanup(p.stop)

    def _fetch(self, url, retries=3, delay=5):
        if "/stats/leaders" in url:
            self.lead_calls.append(url)
            return self._resolve("hitting_leaders" if "statGroup=hitting" in url else "pitching_leaders")
        if "stats=sabermetrics" in url:
            self.lead_calls.append(url)
            if "group=pitching" in url:
                return self._resolve("fip" if "playerPool=qualified" in url else "war_pitching")
            return self._resolve("war_hitting")
        return self.api.fetch(url, retries, delay)

    def _resolve(self, endpoint):
        value = self.lead[endpoint]
        if isinstance(value, str):
            return base.load_fixture(value)
        return copy.deepcopy(value)

    def categories(self):
        return quiet(leaders.fetch_leaders, 2026)

    def data(self, categories=None, in_season=False):
        return leaders.build_leaders_data(2026, in_season, TODAY, categories if categories is not None else self.categories())

    def run_cli(self, *args):
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", *args]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code
        return 0

    def read_json(self, path="data/leaders.json"):
        return json.loads((self.tmp / path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 解析
class ParseLeadersTests(LeadersTestBase):
    def test_hitting_fixture_parses_all_categories_with_api_ranks(self):
        parsed = leaders.parse_leaders(base.load_fixture("leaders_hitting"), "hitting")
        self.assertEqual(set(parsed), {"battingAverage", "onBasePercentage", "homeRuns", "onBasePlusSlugging", "stolenBases"})
        avg = parsed["battingAverage"]
        self.assertEqual(avg[0], {"rank": 1, "id": avg[0]["id"], "name": "Yordan Alvarez", "team_id": avg[0]["team_id"], "value": ".316"})
        self.assertEqual([e["rank"] for e in avg], list(range(1, 11)))
        self.assertEqual(parsed["onBasePlusSlugging"][0]["value"], "1.033")  # 維持 API 字串

    def test_ties_keep_same_rank_and_may_exceed_limit(self):
        hr = leaders.parse_leaders(base.load_fixture("leaders_hitting"), "hitting")["homeRuns"]
        self.assertEqual([e["rank"] for e in hr][:4], [1, 1, 3, 4])
        self.assertEqual(len(hr), 11)  # 第 10 名並列，兩人都保留
        self.assertEqual([e["rank"] for e in hr][-2:], [10, 10])

    def test_ranks_recomputed_from_display_value_not_api_rank(self):
        def row(rank, value, i):
            return {"rank": rank, "value": value, "person": {"id": i, "fullName": f"P{i}"}, "team": {"id": 147}}
        # API 名次 4、5 但 value 相同 → 都是第 4；之後 API 名次 6 實為第 6（序號），7、8 同值 → 都是第 7
        data = {"leagueLeaders": [{"leaderCategory": "onBasePercentage", "statGroup": "hitting", "leaders": [
            row(1, ".432", 1), row(2, ".390", 2), row(3, ".389", 3), row(4, ".388", 4), row(5, ".388", 5),
            row(6, ".386", 6), row(7, ".382", 7), row(8, ".382", 8), row(9, ".381", 9), row(10, ".380", 10),
            row(11, ".379", 11), row(12, ".379", 12)]}]}
        out = leaders.parse_leaders(data, "hitting")["onBasePercentage"]
        self.assertEqual([e["rank"] for e in out], [1, 2, 3, 4, 4, 6, 7, 7, 9, 10])  # 第 11 名以後略過
        self.assertEqual(names(out)[3:5], ["P4", "P5"])  # 保留 API 順序
        data["leagueLeaders"][0]["leaders"][9]["value"] = ".381"  # 第 10 筆與第 9 筆同值 → 並列第 9
        data["leagueLeaders"][0]["leaders"][10]["value"] = ".381"
        out = leaders.parse_leaders(data, "hitting")["onBasePercentage"]
        self.assertEqual([e["rank"] for e in out][-3:], [9, 9, 9])
        data["leagueLeaders"][0]["leaders"][9]["value"] = ".380"  # 回復：第 10 名與 API 第 11 筆同值 → 並列第 10 全列
        data["leagueLeaders"][0]["leaders"][10]["value"] = ".380"
        out = leaders.parse_leaders(data, "hitting")["onBasePercentage"]
        self.assertEqual([e["rank"] for e in out][-2:], [10, 10])
        self.assertEqual(len(out), 11)

    def test_fixture_on_base_percentage_ties_by_display_value(self):
        obp = leaders.parse_leaders(base.load_fixture("leaders_hitting"), "hitting")["onBasePercentage"]
        self.assertEqual([e["rank"] for e in obp], [1, 2, 3, 4, 4, 6, 7, 7, 9, 10])  # API 原本回 4、5、7、8

    def test_stat_group_filter_ignores_other_groups(self):
        data = base.load_fixture("leaders_hitting")
        poison = copy.deepcopy(data["leagueLeaders"][0])
        poison["statGroup"] = "pitching"
        poison["leaders"][0]["person"]["fullName"] = "Wrong Group"
        data["leagueLeaders"].insert(0, poison)
        catching = copy.deepcopy(poison)
        catching["statGroup"] = "catching"
        data["leagueLeaders"].insert(0, catching)
        parsed = leaders.parse_leaders(data, "hitting")
        self.assertNotIn("Wrong Group", json.dumps(parsed))
        self.assertEqual(parsed["stolenBases"][0]["name"], "Nasim Nuñez")
        self.assertEqual(leaders.parse_leaders(base.load_fixture("leaders_pitching"), "hitting"), {})

    def test_malformed_rows_are_skipped(self):
        data = {"leagueLeaders": [{"leaderCategory": "homeRuns", "statGroup": "hitting", "leaders": [
            "junk", {"rank": 1, "value": "45"}, {"rank": 1, "value": "", "person": {"id": 1, "fullName": "A"}},
            {"rank": 1, "value": "44", "person": {"id": "x", "fullName": "B"}},
            {"rank": 2, "value": "43", "person": {"id": 3, "fullName": "C"}, "team": {"id": "bad"}},
        ]}]}
        out = leaders.parse_leaders(data, "hitting")["homeRuns"]
        self.assertEqual(names(out), ["C"])
        self.assertIsNone(out[0]["team_id"])
        for bad in (None, [], {"leagueLeaders": None}, {"leagueLeaders": ["x"]}):
            self.assertEqual(leaders.parse_leaders(bad, "hitting"), {})

    def test_fip_ascending_with_ties_and_missing_values_skipped(self):
        out = leaders.parse_sabermetrics(base.load_fixture("sabermetrics_pitching_qualified"), "fip", False, 2)
        self.assertEqual(out[0]["name"], "Jacob Misiorowski")
        self.assertEqual(out[0]["value"], "2.17")
        values = [float(e["value"]) for e in out]
        self.assertEqual(values, sorted(values))  # 升冪
        for bad in ("No Fip", "Bad Fip", "Null Fip"):
            self.assertNotIn(bad, names(out))
        self.assertEqual([e["rank"] for e in out], [1, 2, 3, 3, 5, 6, 7, 8, 9, 10, 10])  # 2.5553 與 2.55751 顯示同為 2.56 → 並列；第 10 名並列者全列
        self.assertEqual(len(out), 11)

    def test_war_descending_with_top_and_tenth_ties(self):
        out = leaders.parse_sabermetrics(base.load_fixture("sabermetrics_hitting_all"), "war", True, 1)
        self.assertEqual(out[0]["name"], "Bobby Witt Jr.")  # 並列者依姓名排序
        self.assertEqual([e["rank"] for e in out][:3], [1, 1, 3])
        self.assertEqual(out[0]["value"], "7.0")  # 一位小數（6.95539）
        values = [float(e["value"]) for e in out]
        self.assertEqual(values, sorted(values, reverse=True))
        # 以一位小數的顯示值判斷並列（標準競賽排名）：5.9／5.5／5.4 各自並列
        self.assertEqual([e["rank"] for e in out], [1, 1, 3, 4, 5, 5, 7, 7, 9, 9, 9])
        self.assertEqual(names(out)[-3:], ["Fernando Tatis Jr.", "Gabriel Moreno", "Kevin McGonigle"])  # 同顯示值依英文名
        self.assertEqual(len(out), 11)
        self.assertNotIn("No War", names(out))
        self.assertNotIn("Bad War", names(out))

    def test_ties_use_rounded_display_value_and_tenth_place_ties_all_listed(self):
        def split(i, war):
            return {"stat": {"war": war}, "player": {"id": i, "fullName": f"P{i:02d}"}, "team": {"id": 147}}
        wars = [9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.54, 0.46, 0.451, 0.44]
        data = {"stats": [{"splits": [split(i, w) for i, w in enumerate(wars, 1)]}]}
        out = leaders.parse_sabermetrics(data, "war", True, 1)
        # 0.54→0.5、0.46→0.5、0.451→0.5 顯示相同，全部並列第 10；0.44→0.4 為第 13，超過 10 不列
        self.assertEqual([e["rank"] for e in out], list(range(1, 10)) + [10, 10, 10])
        self.assertEqual([e["value"] for e in out][-3:], ["0.5", "0.5", "0.5"])
        self.assertEqual(names(out)[-3:], ["P10", "P11", "P12"])

    def test_card_tie_count_follows_display_value(self):
        cats = self.categories()
        cats["war_hitting"] = leaders.parse_sabermetrics(
            {"stats": [{"splits": [
                {"stat": {"war": 6.04}, "player": {"id": 1, "fullName": "Alpha"}, "team": {"id": 147}},
                {"stat": {"war": 5.96}, "player": {"id": 2, "fullName": "Beta"}, "team": {"id": 111}},
            ]}]}, "war", True, 1)
        card = leaders.render_leaders_card(self.data(cats))
        row = re.search(r'<li><span class="ld-mk">打者 WAR</span>.*?</li>', card, re.S).group(0)
        self.assertIn("Alpha 等 2 人", row)
        self.assertIn(">6.0<", row)

    def test_sabermetrics_keeps_best_row_per_player_and_survives_garbage(self):
        data = {"stats": [{"splits": [
            {"stat": {"war": 1.0}, "player": {"id": 1, "fullName": "A"}, "team": {"id": 147}},
            {"stat": {"war": 3.0}, "player": {"id": 1, "fullName": "A"}, "team": {"id": 111}},
            {"stat": {"war": True}, "player": {"id": 2, "fullName": "Bool"}},
            {"stat": {"war": float("nan")}, "player": {"id": 3, "fullName": "NaN"}},
            {"stat": {"war": 2.0}, "player": {"fullName": "NoId"}},
            "junk",
        ]}, "junk"]}
        out = leaders.parse_sabermetrics(data, "war", True, 1)
        self.assertEqual(names(out), ["A"])
        self.assertEqual(out[0]["value"], "3.0")
        self.assertEqual(leaders.parse_sabermetrics(None, "war", True, 1), [])
        self.assertEqual(leaders.parse_sabermetrics({"stats": None}, "war", True, 1), [])

    def test_value_formatting(self):
        self.assertEqual(leaders._format_stat(6.64943, 1), "6.6")
        self.assertEqual(leaders._format_stat(2.16647, 2), "2.17")
        self.assertEqual(leaders._format_stat(3, 2), "3.00")
        self.assertEqual(leaders._format_stat(-0.02, 1), "0.0")  # 不輸出 -0.0


# ---------------------------------------------------------------- 抓取
class FetchLeadersTests(LeadersTestBase):
    def test_five_requests_with_expected_parameters(self):
        cats = self.categories()
        self.assertEqual(list(cats), [k for k, *_ in fetch_mlb.config.LEADERS_CATEGORIES])
        self.assertEqual(set(cats), set(ALL_KEYS))
        self.assertTrue(all(cats[k] for k in ALL_KEYS))
        self.assertEqual(len(self.lead_calls), 5)
        joined = "\n".join(self.lead_calls)
        self.assertIn("statGroup=hitting&season=2026&sportId=1&limit=10", joined)
        self.assertIn("leaderCategories=earnedRunAverage,strikeouts,saves,strikeoutsPer9Inn&statGroup=pitching", joined)
        self.assertIn("group=pitching&season=2026&sportId=1&playerPool=qualified", joined)
        self.assertIn("group=hitting&season=2026&sportId=1&playerPool=all&limit=2000", joined)
        self.assertIn("group=pitching&season=2026&sportId=1&playerPool=all&limit=2000", joined)

    def test_first_place_values_match_fixtures(self):
        cats = self.categories()
        self.assertEqual(cats["battingAverage"][0]["name"], "Yordan Alvarez")
        self.assertEqual(cats["strikeouts"][0]["name"], "Jacob Misiorowski")
        self.assertEqual(cats["strikeouts"][0]["value"], "252")
        self.assertEqual(cats["earnedRunAverage"][0]["value"], "1.80")

    def test_partial_failure_marks_only_failed_items(self):
        self.lead["pitching_leaders"] = None
        self.lead["fip"] = {"stats": []}
        cats = self.categories()
        for key in ("earnedRunAverage", "strikeouts", "saves", "strikeoutsPer9Inn", "fip"):
            self.assertIsNone(cats[key], key)
        for key in ("battingAverage", "homeRuns", "stolenBases", "war_hitting", "war_pitching"):
            self.assertTrue(cats[key], key)

    def test_category_missing_from_response_is_none(self):
        data = base.load_fixture("leaders_hitting")
        data["leagueLeaders"] = [b for b in data["leagueLeaders"] if b["leaderCategory"] != "stolenBases"]
        self.lead["hitting_leaders"] = data
        cats = self.categories()
        self.assertIsNone(cats["stolenBases"])
        self.assertTrue(cats["homeRuns"])

    def test_invalid_season_returns_all_none_without_requests(self):
        cats = quiet(leaders.fetch_leaders, "abc")
        self.assertFalse(any(cats.values()))
        self.assertEqual(self.lead_calls, [])


# ---------------------------------------------------------------- 寫檔
class GenerateLeadersTests(LeadersTestBase):
    def test_writes_json_and_page(self):
        self.assertTrue(quiet(leaders.generate_leaders, 2026, False, TODAY))
        data = self.read_json()
        self.assertEqual(data["schema"], 1)
        self.assertEqual((data["season"], data["in_season"], data["as_of"]), (2026, False, "2026-11-18"))
        self.assertEqual(set(data["categories"]), set(ALL_KEYS))
        page = (self.tmp / "leaders.html").read_text(encoding="utf-8")
        self.assertIn("2026 賽季最終", page)

    def test_all_failed_does_not_overwrite_existing_files(self):
        quiet(leaders.generate_leaders, 2026, False, TODAY)
        before_json = (self.tmp / "data" / "leaders.json").read_bytes()
        before_html = (self.tmp / "leaders.html").read_bytes()
        for key in self.lead:
            self.lead[key] = None
        self.assertFalse(quiet(leaders.generate_leaders, 2027, False, TODAY))
        self.assertEqual((self.tmp / "data" / "leaders.json").read_bytes(), before_json)
        self.assertEqual((self.tmp / "leaders.html").read_bytes(), before_html)

    def test_all_failed_creates_nothing(self):
        for key in self.lead:
            self.lead[key] = None
        self.assertFalse(quiet(leaders.generate_leaders, 2026, False, TODAY))
        self.assertFalse((self.tmp / "data" / "leaders.json").exists())
        self.assertFalse((self.tmp / "leaders.html").exists())

    def test_partial_failure_still_writes_with_null_items(self):
        self.lead["war_hitting"] = None
        self.assertTrue(quiet(leaders.generate_leaders, 2026, False, TODAY))
        data = self.read_json()
        self.assertIsNone(data["categories"]["war_hitting"])
        self.assertTrue(data["categories"]["homeRuns"])
        self.assertIn("暫無資料", (self.tmp / "leaders.html").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 標題與排行頁
class RenderPageTests(LeadersTestBase):
    def test_title_final_vs_in_season(self):
        self.assertEqual(leaders.leaders_title(self.data(in_season=False)), "2026 賽季最終")
        self.assertEqual(leaders.leaders_title(self.data(in_season=True)), "2026 賽季・截至 11/18")
        page = leaders.render_leaders_page(self.data(in_season=True))
        self.assertIn("2026 賽季・截至 11/18", page)
        self.assertNotIn("賽季最終", page)

    def test_offseason_title_via_cli_uses_final(self):
        self.assertEqual(self.run_cli("--leaders"), 0)
        self.assertIn("2026 賽季最終", (self.tmp / "leaders.html").read_text(encoding="utf-8"))

    def test_before_opening_uses_last_season_final(self):
        self.api.seasons = "seasons_2027"
        with mock.patch("mlb_daily.timeutil.get_taipei_now", return_value=base.datetime(2027, 1, 10, 12, 0, tzinfo=base.timezone.utc)):
            self.assertEqual(self.run_cli("--leaders", "--date", "2027-01-10"), 0)
        self.assertTrue(any("season=2026" in u for u in self.lead_calls))
        self.assertIn("2026 賽季最終", (self.tmp / "leaders.html").read_text(encoding="utf-8"))

    def test_in_season_title_uses_yesterday_in_taipei(self):
        self.assertEqual(self.run_cli("--leaders", "--date", "2026-08-01"), 0)
        data = self.read_json()
        self.assertEqual(data["as_of"], "2026-07-31")  # 跨月
        self.assertIn("2026 賽季・截至 7/31", (self.tmp / "leaders.html").read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("--leaders", "--date", "2026-08-15"), 0)

    def test_playoffs_title_is_as_of(self):
        # 2026 例行賽 9/27 結束、季後賽到 10/28：10/10 仍視為賽季中，但例行賽已結束 → 標題為「例行賽最終」
        self.assertEqual(self.run_cli("--leaders", "--date", "2026-10-10"), 0)
        self.assertTrue(self.read_json()["in_season"])
        self.assertTrue(self.read_json()["regular_final"])
        self.assertIn("2026 例行賽最終", (self.tmp / "leaders.html").read_text(encoding="utf-8"))

    def test_seasons_api_failure_falls_back_to_month(self):
        self.api.seasons = None
        self.assertEqual(self.run_cli("--leaders", "--date", "2026-03-10"), 0)  # 3 月：開季前 → 去年最終
        self.assertEqual(self.read_json()["season"], 2025)
        self.assertFalse(self.read_json()["in_season"])
        self.assertIn("2025 賽季最終", (self.tmp / "leaders.html").read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("--leaders", "--date", "2026-11-18"), 0)  # 11 月：休賽季 → 今年最終
        self.assertEqual(self.read_json()["season"], 2026)
        self.assertIn("2026 賽季最終", (self.tmp / "leaders.html").read_text(encoding="utf-8"))

    def test_in_season_cli_title_and_data(self):
        self.assertEqual(self.run_cli("--leaders", "--date", "2026-08-15"), 0)
        self.assertTrue(self.read_json()["in_season"])
        self.assertIn("2026 賽季・截至 8/14", (self.tmp / "leaders.html").read_text(encoding="utf-8"))

    def test_page_structure_two_sections_six_cards_each(self):
        page = leaders.render_leaders_page(self.data())
        self.assertEqual(page.count('<article class="ld-card">'), 12)
        hitting, _, pitching = page.partition('id="ld-pitching-h"')
        self.assertEqual(hitting.count('<article class="ld-card">'), 6)
        self.assertEqual(pitching.count('<article class="ld-card">'), 6)
        for label in ("打擊率", "上壘率", "全壘打", "OPS", "盜壘", "打者 WAR", "防禦率", "三振", "救援成功", "K/9", "FIP", "投手 WAR"):
            self.assertIn(f"<h3>{label}</h3>", page)
        self.assertIn("規定打席", page)
        self.assertIn("規定局數", page)
        self.assertIn("MLB 官方數據", page)
        self.assertIn("fWAR", page)
        self.assertIn("bWAR", page)

    def test_top_five_visible_and_rest_in_details(self):
        page = leaders.render_leaders_page(self.data())
        card = re.search(r'<article class="ld-card"><header class="ld-head"><h3>打擊率</h3>.*?</article>', page, re.S).group(0)
        self.assertEqual(card.count('<details class="ld-more"><summary>展開前 10 名</summary>'), 1)
        before, _, after = card.partition("<details")
        self.assertEqual(before.count('class="ld-row"'), 5)
        self.assertEqual(after.count('class="ld-row"'), 5)
        self.assertNotIn("<script", page)

    def test_tied_tenth_place_all_listed_in_details(self):
        page = leaders.render_leaders_page(self.data())
        card = re.search(r"<h3>全壘打</h3>.*?</article>", page, re.S).group(0)
        details = card.partition("<details")[2]
        self.assertEqual(len(re.findall(r'<span class="ld-rk">10</span>', details)), 2)
        self.assertEqual(len(re.findall(r'<span class="ld-rk">1</span>', card)), 2)  # 並列第 1 名同名次

    def test_card_without_extra_rows_has_no_details(self):
        cats = self.categories()
        cats["saves"] = cats["saves"][:3]
        card = re.search(r"<h3>救援成功</h3>.*?</article>", leaders.render_leaders_page(self.data(cats)), re.S).group(0)
        self.assertNotIn("<details", card)

    def test_failed_item_shows_placeholder(self):
        cats = self.categories()
        cats["fip"] = None
        page = leaders.render_leaders_page(self.data(cats))
        card = re.search(r"<h3>FIP</h3>.*?</article>", page, re.S).group(0)
        self.assertIn("暫無資料", card)
        self.assertNotIn("ld-row", card)

    def test_row_contains_logo_abbr_and_value(self):
        page = leaders.render_leaders_page(self.data())
        card = re.search(r"<h3>全壘打</h3>.*?</article>", page, re.S).group(0)
        self.assertIn("mlbstatic.com/team-logos/team-cap-on-", card)
        self.assertRegex(card, r'<span class="ld-ab">[A-Z]{2,3}</span><span class="ld-val">45</span>')
        self.assertIn("baseballsavant.mlb.com/savant-player/", card)

    def test_external_strings_are_escaped(self):
        cats = self.categories()
        cats["homeRuns"][0]["name"] = '<img src=x onerror=alert(1)>"&'
        cats["homeRuns"][0]["value"] = "<b>9</b>"
        cats["homeRuns"][0]["id"] = "1\" onmouseover=\"x"
        page = leaders.render_leaders_page(self.data(cats))
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<b>9</b>", page)
        self.assertNotIn('onmouseover="x', page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
        card = leaders.render_leaders_card(self.data(cats))
        self.assertNotIn("<img src=x", card)
        self.assertNotIn("<b>9</b>", card)

    def test_unknown_team_and_garbage_entries_do_not_break_render(self):
        cats = self.categories()
        cats["strikeouts"][0]["team_id"] = 999999
        cats["saves"] = ["junk", {"rank": "x"}, {"rank": 1, "name": "Z", "value": "9", "id": 5, "team_id": None}]
        cats["stolenBases"] = {"not": "a list"}
        page = leaders.render_leaders_page(self.data(cats))
        self.assertIn("Jacob Misiorowski", page)
        self.assertIn("暫無資料", page)
        self.assertIn("Z", leaders.render_leaders_card(self.data(cats)))

    def test_nav_links_and_relative_paths(self):
        page = leaders.render_leaders_page(self.data())
        self.assertIn('href="assets/style.css"', page)
        self.assertIn('<a href="leaders.html" aria-current="page">數據排行</a>', page)
        self.assertIn('href="teams.html"', page)
        self.assertIn('href="archive.html"', page)
        self.assertIn('href="index.html"', page)
        self.assertNotIn("<!--SLOT:", page)


# ---------------------------------------------------------------- 首頁小卡
class HomeCardTests(LeadersTestBase):
    META = {"date": DATE, "total_games": 3, "postponed": 0, "summary": "測試摘要", "type": "season"}

    def index_html(self):
        return (self.tmp / "index.html").read_text(encoding="utf-8")

    def test_card_has_twelve_rows_title_and_link(self):
        card = leaders.render_leaders_card(self.data())
        self.assertEqual(card.count("<li>"), 12)
        self.assertIn("2026 賽季最終", card)
        self.assertIn('<a class="ld-mini-more" href="leaders.html">完整排行 →</a>', card)
        self.assertIn("Yordan Alvarez", card)
        self.assertIn(".316", card)

    def test_tie_for_first_shows_a_and_count(self):
        card = leaders.render_leaders_card(self.data())
        row = re.search(r'<li><span class="ld-mk">全壘打</span>.*?</li>', card, re.S).group(0)
        self.assertRegex(row, r"[^<>]+ 等 2 人")
        self.assertIn('<span class="ld-mv">45</span>', row)
        saves = re.search(r'<li><span class="ld-mk">救援成功</span>.*?</li>', card, re.S).group(0)
        self.assertIn("等 2 人", saves)
        avg = re.search(r'<li><span class="ld-mk">打擊率</span>.*?</li>', card, re.S).group(0)
        self.assertNotIn("等", avg)

    def test_failed_item_row_shows_placeholder_and_empty_data_gives_no_card(self):
        cats = self.categories()
        cats["fip"] = None
        card = leaders.render_leaders_card(self.data(cats))
        self.assertIn('<li class="is-empty"><span class="ld-mk">FIP</span>', card)
        self.assertEqual(leaders.render_leaders_card(self.data({k: None for k in ALL_KEYS})), "")
        self.assertEqual(leaders.render_leaders_card(None), "")
        self.assertEqual(leaders.render_leaders_card({"categories": "x"}), "")

    def test_index_without_file_has_no_card(self):
        self.quiet_update_index(self.META)
        self.assertNotIn("ld-card-mini", self.index_html())

    def test_index_with_file_shows_card_inside_aside_below_standings(self):
        self.api.standings = "standings_2026"
        quiet(leaders.generate_leaders, 2026, False, TODAY)
        self.quiet_update_index(self.META)
        html = self.index_html()
        aside = re.search(r'<aside class="standings".*?</aside>', html, re.S).group(0)
        self.assertIn('class="ld-card-mini"', aside)
        self.assertGreater(aside.index("ld-card-mini"), aside.rindex("</table>"))  # 戰績表之後
        self.assertEqual(html.count("ld-card-mini"), 1)
        self.assertIn('href="leaders.html">數據排行</a>', html)

    def test_index_card_present_even_when_standings_failed(self):
        quiet(leaders.generate_leaders, 2026, False, TODAY)
        self.quiet_update_index(self.META)  # standings fixture 未設定：抓取失敗版側欄
        aside = re.search(r'<aside class="standings is-error".*?</aside>', self.index_html(), re.S).group(0)
        self.assertIn("ld-card-mini", aside)

    def test_missing_or_non_integer_season_is_rejected(self):
        (self.tmp / "data").mkdir()
        good = json.loads(json.dumps(self.data()))
        for season in (None, "2026", True, 2026.5, "missing"):
            doc = dict(good)
            if season == "missing":
                doc.pop("season")
            else:
                doc["season"] = season
            (self.tmp / "data" / "leaders.json").write_text(json.dumps(doc), encoding="utf-8")
            self.assertIsNone(fetch_mlb.storage.load_leaders(), season)
            self.quiet_update_index(self.META)
            self.assertNotIn("ld-card-mini", self.index_html())
        (self.tmp / "data" / "leaders.json").write_text(json.dumps(good), encoding="utf-8")
        self.assertIsNotNone(fetch_mlb.storage.load_leaders())

    def test_corrupt_or_wrong_schema_file_is_ignored(self):
        (self.tmp / "data").mkdir()
        for content in ("{not json", json.dumps({"schema": 99, "categories": {}}), json.dumps([1, 2])):
            (self.tmp / "data" / "leaders.json").write_text(content, encoding="utf-8")
            self.assertIsNone(fetch_mlb.storage.load_leaders())
            self.quiet_update_index(self.META)
            self.assertNotIn("ld-card-mini", self.index_html())

    def test_index_generation_does_not_hit_network_for_leaders(self):
        quiet(leaders.generate_leaders, 2026, False, TODAY)
        self.lead_calls.clear()
        self.quiet_update_index(self.META)
        self.assertEqual(self.lead_calls, [])

    def test_standings_aside_without_extra_is_unchanged(self):
        now = base.datetime(2026, 11, 18, 12, 17)
        a = fetch_mlb.render_standings_aside(None, 2026, False, now)
        b = fetch_mlb.render_standings_aside(None, 2026, False, now, "")
        self.assertEqual(a, b)
        self.assertNotIn("ld-card-mini", a)


# ---------------------------------------------------------------- CLI
class CliTests(LeadersTestBase):
    def test_cli_leaders_writes_files_without_metadata_and_skips_index(self):
        index_before = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertEqual(self.run_cli("--leaders"), 0)
        self.assertTrue((self.tmp / "leaders.html").exists())
        self.assertTrue((self.tmp / "data" / "leaders.json").exists())
        self.assertEqual((self.tmp / "index.html").read_text(encoding="utf-8"), index_before)
        self.assertFalse((self.tmp / "reports" / "metadata.json").exists())  # 不碰 metadata

    def test_cli_leaders_refreshes_index_when_metadata_exists(self):
        (self.tmp / "reports" / "metadata.json").write_text(json.dumps({DATE: HomeCardTests.META}), encoding="utf-8")
        self.assertEqual(self.run_cli("--leaders"), 0)
        self.assertIn("ld-card-mini", (self.tmp / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(self.discord.call_count, 0)

    def test_cli_index_refresh_failure_does_not_fail_leaders(self):
        (self.tmp / "reports" / "metadata.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_cli("--leaders"), 0)
        self.assertTrue((self.tmp / "leaders.html").exists())

    def test_cli_all_failed_exits_nonzero_and_keeps_old_files(self):
        self.assertEqual(self.run_cli("--leaders"), 0)
        before = (self.tmp / "data" / "leaders.json").read_bytes()
        for key in self.lead:
            self.lead[key] = None
        self.assertEqual(self.run_cli("--leaders"), 1)
        self.assertEqual((self.tmp / "data" / "leaders.json").read_bytes(), before)

    def test_cli_rejects_combination_with_other_modes(self):
        for extra in (["--rosters"], ["--check-wait"], ["--notify-only"], ["--no-notify"], ["--weekly"],
                      ["--notify-weekly"], ["--week", "2026-W47"], ["--force"]):
            self.assertEqual(self.run_cli("--leaders", *extra), 1, extra)
        self.assertEqual(self.lead_calls, [])

    def test_cli_invalid_date_exits_nonzero(self):
        self.assertEqual(self.run_cli("--leaders", "--date", "nope"), 1)


# ---------------------------------------------------------------- workflow、導覽列、匯入層級
class StaticChecksTests(unittest.TestCase):
    def setUp(self):
        self.yml = (base.REPO_ROOT / ".github" / "workflows" / "daily_report.yml").read_text(encoding="utf-8")

    def test_workflow_restores_leaders_json_without_failing(self):
        restore = self.yml[self.yml.index("- name: Restore history from gh-pages"):self.yml.index("- name: Update Stat Leaders")]
        self.assertIn("git show origin/gh-pages:data/leaders.json > data/leaders.json 2>/dev/null", restore)
        self.assertIn("rm -f data/leaders.json", restore)
        self.assertIn("::warning::gh-pages 尚無 data/leaders.json", restore)
        self.assertIn("mkdir -p data", restore)

    def test_workflow_leaders_step_before_daily_report_and_non_blocking(self):
        y = self.yml
        self.assertLess(y.index("- name: Update Stat Leaders"), y.index("- name: Generate Daily Report"))
        self.assertGreater(y.index("- name: Update Stat Leaders"), y.index("- name: Restore history from gh-pages"))
        step = y[y.index("- name: Update Stat Leaders"):y.index("- name: Generate Daily Report")]
        self.assertIn("continue-on-error: true", step)
        self.assertIn("python fetch_mlb.py --leaders", step)

    def test_workflow_does_not_exclude_leaders_from_deploy(self):
        exclude = re.search(r"exclude_assets: '([^']*)'", self.yml).group(1)
        self.assertNotIn("leaders", exclude)

    def test_every_page_nav_links_to_leaders(self):
        expected = {
            "templates/index.html": 'href="leaders.html">數據排行</a>',
            "templates/report.html": 'href="../../../leaders.html">數據排行</a>',
            "templates/weekly.html": 'href="../../leaders.html">數據排行</a>',
            "templates/leaders.html": 'href="leaders.html" aria-current="page">數據排行</a>',
            "teams.html": 'href="leaders.html">數據排行</a>',
            "archive.html": 'href="leaders.html">數據排行</a>',
        }
        for rel, needle in expected.items():
            self.assertIn(needle, (base.REPO_ROOT / rel).read_text(encoding="utf-8"), rel)

    def test_root_generated_pages_are_gitignored_with_anchored_rules(self):
        lines = (base.REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/leaders.html", lines)
        self.assertIn("/index.html", lines)
        # 不帶 / 的規則會連 templates/ 內同名模板一起忽略（CI 找不到模板）
        self.assertNotIn("leaders.html", lines)
        self.assertNotIn("index.html", lines)
        self.assertTrue((base.REPO_ROOT / "templates" / "leaders.html").exists())

    def test_leaders_module_respects_layering(self):
        def imported(name):
            tree = ast.parse((base.REPO_ROOT / "mlb_daily" / name).read_text(encoding="utf-8"))
            out = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
                    out.update(a.name for a in node.names)
            return out
        self.assertEqual(imported("leaders.py") & {"rosters", "standings", "games", "previews", "report", "weekly", "index_page", "notify", "cli", "translate"}, set())
        self.assertNotIn("leaders", imported("standings.py"))
        self.assertNotIn("leaders", imported("rosters.py"))


if __name__ == "__main__":
    unittest.main()
