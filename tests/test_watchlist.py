"""台日韓球員專區測試：只用 unittest 與 tests/fixtures/，不連網、不呼叫真實 Gemini／Discord。"""

import contextlib
import io
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

fetch_mlb = base.fetch_mlb
DATE = base.DATE
WL_PATH = str(base.FIXTURES / "watchlist_test.json")
OHTANI, TENG, HAOYU, CHENG, JHLEE, KIM = 660271, 678906, 701678, 691907, 808982, 999001


def load_wl():
    return fetch_mlb.watchlist.load_watchlist(WL_PATH)


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


def season_box():
    """boxscore_1001 加上名單球員（含隊 id），供整合測試。"""
    box = base.load_fixture("boxscore_1001")
    extra = base.load_fixture("boxscore_watchlist")
    for side in ("away", "home"):
        box["teams"][side]["team"] = extra["teams"][side]["team"]
        for key, info in extra["teams"][side]["players"].items():
            info["position"] = {"abbreviation": "DH"}
            box["teams"][side]["players"][key] = info
    return box


# ---------------------------------------------------------------- 名單讀取
class LoadWatchlistTests(unittest.TestCase):
    def test_loads_valid_players_in_file_order_and_skips_bad(self):
        wl = load_wl()
        self.assertEqual(list(wl), [TENG, HAOYU, CHENG, OHTANI, JHLEE, KIM])  # 壞地區、缺 id 的項目略過
        self.assertEqual(wl[OHTANI]["aliases"], ["Ohtani"])
        self.assertEqual(wl[TENG]["aliases"], [])

    def test_missing_file_returns_empty_with_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(fetch_mlb.watchlist.load_watchlist("no/such/file.json"), {})
        self.assertIn("Warning", err.getvalue())

    def test_bad_json_and_bad_shape_return_empty(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("a.json", "{not json"), ("b.json", '{"players": 5}'), ("c.json", "[]"), ("d.json", '{"x": 1}')):
                p = Path(d) / name
                p.write_text(text, encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(fetch_mlb.watchlist.load_watchlist(str(p)), {}, name)

    def test_real_settings_file_is_valid(self):
        wl = fetch_mlb.watchlist.load_watchlist(str(base.REPO_ROOT / "settings" / "watchlist.json"))
        self.assertGreaterEqual(len(wl), 20)
        self.assertEqual({p["region"] for p in wl.values()}, {"TW", "JP", "KR"})
        self.assertEqual(wl[660271]["aliases"], ["Ohtani"])


# ---------------------------------------------------------------- 動態名單（MLB API + settings 覆寫）
def person(pid, name, country, team=1):
    return {"id": pid, "fullName": name, "birthCountry": country, "currentTeam": {"id": team}}


class DynamicWatchlistTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.settings = self.tmp / "settings.json"
        self.dynamic = self.tmp / "dynamic.json"
        self.w = fetch_mlb.watchlist

    def write_settings(self, players=(), exclude=()):
        self.settings.write_text(json.dumps({"exclude_ids": list(exclude), "players": list(players)}), encoding="utf-8")

    def write_dynamic(self, players):
        self.dynamic.write_text(json.dumps({"players": players}), encoding="utf-8")

    def load(self):
        return quiet(self.w.load_watchlist, str(self.settings), str(self.dynamic))

    def fake_fetch(self, by_sport, failing=(), teams=None):
        def fetch(url, retries=3, delay=5):
            if "/teams?" in url:
                return None if teams is None else {"teams": teams}
            sid = int(url.split("/sports/")[1].split("/")[0])
            if sid in failing:
                return None
            return {"people": by_sport.get(sid, [])}
        return fetch

    def generate(self, by_sport, failing=(), teams=None):
        with mock.patch("mlb_daily.net.fetch_json", side_effect=self.fake_fetch(by_sport, failing, teams)):
            return quiet(self.w.generate_watchlist, 2026, None, str(self.settings), str(self.dynamic))

    def written(self):
        return json.loads(self.dynamic.read_text(encoding="utf-8"))

    # ---- 抓取過濾
    def test_mlb_keeps_tw_jp_kr_minors_only_taiwan(self):
        self.write_settings()
        ok = self.generate({
            1: [person(1, "A Tw", "Taiwan"), person(2, "B Jp", "Japan"), person(3, "C Kr", "Republic of Korea"),
                person(4, "D Us", "USA"), person(5, "E Kr", "South Korea")],
            11: [person(6, "F Tw", "Taiwan"), person(7, "G Jp", "Japan"), person(8, "H Kr", "Republic of Korea")],
            16: [person(9, "I Tw", "Taiwan")],
        })
        self.assertTrue(ok)
        got = {p["id"]: (p["region"], p["level"]) for p in self.written()["players"]}
        self.assertEqual(got, {1: ("TW", "MLB"), 2: ("JP", "MLB"), 3: ("KR", "MLB"), 5: ("KR", "MLB"),
                               6: ("TW", "AAA"), 9: ("TW", "ROK")})

    def test_dedup_keeps_highest_level(self):
        self.write_settings()
        self.generate({13: [person(1, "A Tw", "Taiwan")], 12: [person(1, "A Tw", "Taiwan")],
                       14: [person(1, "A Tw", "Taiwan")], 11: [person(2, "B Tw", "Taiwan")],
                       1: [person(2, "B Tw", "Taiwan")]})
        got = {p["id"]: p["level"] for p in self.written()["players"]}
        self.assertEqual(got, {1: "AA", 2: "MLB"})

    def test_single_sport_failure_skipped_all_failure_keeps_old_file(self):
        self.write_settings()
        self.assertTrue(self.generate({1: [person(1, "A Tw", "Taiwan")]}, failing=(11, 12)))
        self.assertEqual([p["id"] for p in self.written()["players"]], [1])
        old = self.dynamic.read_text(encoding="utf-8")
        self.assertFalse(self.generate({1: [person(2, "B Tw", "Taiwan")]}, failing=(1, 11, 12, 13, 14, 16)))
        self.assertEqual(self.dynamic.read_text(encoding="utf-8"), old)

    def test_missing_zh_lists_players_without_settings_entry(self):
        self.write_settings([{"id": 1, "name_zh": "甲", "name_en": "A Tw", "region": "TW"}], exclude=[3])
        self.generate({1: [person(1, "A Tw", "Taiwan"), person(2, "B Jp", "Japan"), person(3, "C Jp", "Japan")]})
        data = self.written()
        self.assertEqual([p["id"] for p in data["missing_zh"]], [2])
        self.assertEqual(data["missing_zh"][0], {"id": 2, "name_en": "B Jp", "region": "JP", "level": "MLB",
                                                  "org_id": None, "current_level": None})
        self.assertEqual(data["season"], [2026])
        self.assertIn("updatedAt", data)

    # ---- 母隊與層級
    def test_org_id_and_current_level(self):
        self.write_settings()
        teams = [{"id": 400, "parentOrgId": 133, "sport": {"id": 11}},
                 {"id": 2310, "parentOrgId": 109, "sport": {"id": 13}}]
        self.generate({
            1: [person(1, "In Mlb", "Taiwan", team=133), person(2, "Optioned", "Taiwan", team=400)],
            13: [person(3, "High A", "Taiwan", team=2310), person(4, "Unknown", "Taiwan", team=99999),
                 person(5, "No Team", "Taiwan", team=None)],
        }, teams=teams)
        got = {p["id"]: (p["org_id"], p["current_level"]) for p in self.written()["players"]}
        self.assertEqual(got, {1: (133, "MLB"), 2: (133, "AAA"), 3: (109, "A+"), 4: (None, None), 5: (None, None)})

    def test_teams_endpoint_failure_gives_none_but_rest_works(self):
        self.write_settings()
        self.assertTrue(self.generate({1: [person(1, "A Tw", "Taiwan", team=400), person(2, "B Tw", "Taiwan", team=133)]}))
        got = {p["id"]: (p["org_id"], p["current_level"]) for p in self.written()["players"]}
        self.assertEqual(got, {1: (None, None), 2: (133, "MLB")})

    def test_load_watchlist_carries_org_id_and_current_level(self):
        self.write_settings([{"id": 1, "name_zh": "甲", "name_en": "A Tw", "region": "TW"},
                             {"id": 50, "name_zh": "海外", "name_en": "Overseas", "region": "JP"}])
        self.write_dynamic([{"id": 1, "name_en": "A Tw", "region": "TW", "level": "AAA", "org_id": 133, "current_level": "AAA"},
                            {"id": 2, "name_en": "B Tw", "region": "TW", "level": "AA", "org_id": "x", "current_level": "bad"},
                            {"id": 3, "name_en": "C Tw", "region": "TW", "level": "A"}])
        wl = self.load()
        self.assertEqual((wl[1]["org_id"], wl[1]["current_level"]), (133, "AAA"))
        self.assertEqual((wl[2]["org_id"], wl[2]["current_level"]), (None, None))
        self.assertEqual((wl[3]["org_id"], wl[3]["current_level"]), (None, None))
        self.assertEqual((wl[50]["org_id"], wl[50]["current_level"]), (None, None))

    # ---- 穩定性：空陣列視為失敗、沿用舊檔
    def test_empty_people_counts_as_failure_and_carries_old_level(self):
        self.write_settings()
        self.write_dynamic([
            {"id": 10, "name_en": "Old Aaa", "region": "TW", "level": "AAA", "org_id": 133, "current_level": "AAA"},
            {"id": 11, "name_en": "Old Aa", "region": "TW", "level": "AA", "org_id": 109, "current_level": "AA"},
            {"id": 12, "name_en": "Old Mlb", "region": "JP", "level": "MLB", "org_id": 133, "current_level": "MLB"}])
        self.generate({1: [person(1, "New Mlb", "Taiwan")], 11: [], 12: [person(11, "Old Aa", "Taiwan")]},
                      teams=[{"id": 400, "parentOrgId": 133, "sport": {"id": 12}}])
        got = {p["id"]: p for p in self.written()["players"]}
        self.assertEqual(sorted(got), [1, 10, 11])       # 12（舊 MLB 層級）不沿用，MLB 層級本次成功
        self.assertEqual(got[10]["level"], "AAA")
        self.assertEqual((got[10]["org_id"], got[10]["current_level"]), (133, "AAA"))

    def test_carried_player_yields_to_new_data(self):
        self.write_settings()
        self.write_dynamic([{"id": 10, "name_en": "Old Aaa", "region": "TW", "level": "AAA"}])
        self.generate({1: [person(10, "Old Aaa", "Taiwan")], 11: []})
        got = {p["id"]: p["level"] for p in self.written()["players"]}
        self.assertEqual(got, {10: "MLB"})

    def test_failure_without_old_file_just_skips(self):
        self.write_settings()
        self.assertTrue(self.generate({1: [person(1, "A Tw", "Taiwan")]}, failing=(11,)))
        self.assertEqual([p["id"] for p in self.written()["players"]], [1])

    def test_carry_count_is_reported(self):
        self.write_settings()
        self.write_dynamic([{"id": 10, "name_en": "Old Aaa", "region": "TW", "level": "AAA"}])
        out = io.StringIO()
        with mock.patch("mlb_daily.net.fetch_json", side_effect=self.fake_fetch({1: [person(1, "A", "Taiwan")]}, failing=(11,))), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.w.generate_watchlist(2026, None, str(self.settings), str(self.dynamic))
        self.assertIn("沿用上次名單中的 1 位", out.getvalue())

    # ---- 合併
    def test_settings_override_and_extra_players(self):
        self.write_settings([
            {"id": 1, "name_zh": "甲", "name_en": "A Tw", "region": "TW", "aliases": ["Aa"]},
            {"id": 50, "name_zh": "海外", "name_en": "Overseas Guy", "region": "JP"},
        ])
        self.write_dynamic([
            {"id": 1, "name_en": "A Tw X", "region": "JP", "level": "AAA"},
            {"id": 2, "name_en": "B Jp", "region": "JP", "level": "MLB"},
        ])
        wl = self.load()
        self.assertEqual(wl[1]["name_zh"], "甲")
        self.assertEqual(wl[1]["region"], "TW")  # settings 覆寫動態值
        self.assertEqual(wl[1]["aliases"], ["Aa"])
        self.assertEqual(wl[1]["level"], "AAA")
        self.assertTrue(wl[1]["has_zh"])
        self.assertEqual(wl[50]["name_zh"], "海外")  # 動態沒有的 settings 球員照樣加入
        self.assertIsNone(wl[50]["level"])
        self.assertTrue(wl[50]["has_zh"])
        self.assertEqual(sorted(wl), [1, 2, 50])

    def test_name_zh_falls_back_to_english_with_has_zh_false(self):
        self.write_settings()
        self.write_dynamic([{"id": 2, "name_en": "B Jp", "region": "JP", "level": "MLB"}])
        p = self.load()[2]
        self.assertEqual(p["name_zh"], "B Jp")
        self.assertFalse(p["has_zh"])
        self.assertEqual(p["aliases"], [])
        self.assertEqual(p["level"], "MLB")

    def test_exclude_ids_removes_from_both_sources(self):
        self.write_settings([{"id": 1, "name_zh": "甲", "name_en": "A Tw", "region": "TW"}], exclude=[1, 2])
        self.write_dynamic([{"id": 2, "name_en": "B Jp", "region": "JP", "level": "MLB"},
                            {"id": 3, "name_en": "C Jp", "region": "JP", "level": "MLB"}])
        self.assertEqual(list(self.load()), [3])

    def test_missing_or_corrupt_cache_falls_back_to_settings_with_warning(self):
        self.write_settings([{"id": 1, "name_zh": "甲", "name_en": "A Tw", "region": "TW"}])
        for content in (None, "{broken", '{"players": 5}'):
            with self.subTest(content=content):
                if content is not None:
                    self.dynamic.write_text(content, encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    wl = self.w.load_watchlist(str(self.settings), str(self.dynamic))
                self.assertEqual(list(wl), [1])
                self.assertIn("Warning", err.getvalue())

    def test_explicit_path_without_dynamic_path_ignores_cache(self):
        self.write_settings([{"id": 1, "name_zh": "甲", "name_en": "A Tw", "region": "TW"}])
        self.write_dynamic([{"id": 2, "name_en": "B Jp", "region": "JP", "level": "MLB"}])
        with mock.patch("mlb_daily.watchlist.DYNAMIC_WATCHLIST_PATH", str(self.dynamic)):
            self.assertEqual(list(quiet(self.w.load_watchlist, str(self.settings))), [1])

    def test_default_paths_read_config_at_call_time(self):
        self.write_settings([{"id": 1, "name_zh": "甲", "name_en": "A Tw", "region": "TW"}])
        self.write_dynamic([{"id": 2, "name_en": "B Jp", "region": "JP", "level": "MLB"}])
        with mock.patch("mlb_daily.watchlist.WATCHLIST_PATH", str(self.settings)), \
                mock.patch("mlb_daily.watchlist.DYNAMIC_WATCHLIST_PATH", str(self.dynamic)):
            self.assertEqual(list(quiet(self.w.load_watchlist)), [1, 2])

    # ---- 排序
    def test_sort_region_then_settings_order_then_level_then_name(self):
        self.write_settings([
            {"id": 10, "name_zh": "乙", "name_en": "Zed Jp", "region": "JP"},
            {"id": 11, "name_zh": "丙", "name_en": "Amy Jp", "region": "JP"},
            {"id": 12, "name_zh": "丁", "name_en": "Tw One", "region": "TW"},
        ])
        self.write_dynamic([
            {"id": 21, "name_en": "Kr Mlb", "region": "KR", "level": "MLB"},
            {"id": 22, "name_en": "Jp Aaa", "region": "JP", "level": "AAA"},
            {"id": 23, "name_en": "Jp Mlb B", "region": "JP", "level": "MLB"},
            {"id": 24, "name_en": "Jp Mlb A", "region": "JP", "level": "MLB"},
            {"id": 25, "name_en": "Tw Rok", "region": "TW", "level": "ROK"},
            {"id": 26, "name_en": "Tw Aa", "region": "TW", "level": "AA"},
            {"id": 11, "name_en": "Amy Jp", "region": "JP", "level": "MLB"},
        ])
        self.assertEqual(list(self.load()), [12, 26, 25, 10, 11, 24, 23, 22, 21])


# ---------------------------------------------------------------- 比對
# ---------------------------------------------------------------- 多賽季聯集
class WatchlistSeasonsTests(unittest.TestCase):
    def seasons(self, today, ctx, end=None):
        return fetch_mlb.watchlist.watchlist_seasons(today, ctx, end)

    def test_after_world_series_queries_next_year_optionally(self):
        ctx = {"rosterType": "40Man", "season": 2026, "rosterSeason": 2026}
        end = date(2026, 11, 4)
        for today in (date(2026, 11, 23), date(2026, 12, 31)):
            self.assertEqual(self.seasons(today, ctx, end), {
                "mlb": {"required": [2026], "optional": [2027]}, "minor": {"required": [2026], "optional": []}})

    def test_season_end_unknown_estimates_by_month(self):
        ctx = {"rosterType": "40Man", "season": 2026, "rosterSeason": 2026}
        self.assertEqual(self.seasons(date(2026, 11, 3), ctx)["mlb"]["optional"], [2027])
        self.assertEqual(self.seasons(date(2026, 10, 31), ctx)["mlb"]["optional"], [])

    def test_before_postseason_ends_has_no_optional_year(self):
        ctx = {"rosterType": "active", "season": 2026, "rosterSeason": 2026}
        self.assertEqual(self.seasons(date(2026, 10, 20), ctx, date(2026, 11, 4))["mlb"],
                         {"required": [2026], "optional": []})

    def test_new_year_before_opening_day(self):
        ctx = {"rosterType": "40Man", "season": 2026, "rosterSeason": 2027}
        self.assertEqual(self.seasons(date(2027, 1, 1), ctx, date(2027, 11, 4)), {
            "mlb": {"required": [2026, 2027], "optional": []}, "minor": {"required": [2026], "optional": [2027]}})

    def test_opening_day(self):
        ctx = {"rosterType": "active", "season": 2027, "rosterSeason": 2027}
        self.assertEqual(self.seasons(date(2027, 3, 25), ctx, date(2027, 11, 4)), {
            "mlb": {"required": [2027], "optional": []}, "minor": {"required": [2027], "optional": []}})

    def test_planned_seasons_and_int_compat(self):
        w = fetch_mlb.watchlist
        self.assertEqual(w.planned_seasons(2026), [2026])
        plan = {"mlb": {"required": [2026], "optional": [2027]}, "minor": {"required": [2026], "optional": []}}
        self.assertEqual(w.planned_seasons(plan), [2026, 2027])


class MultiSeasonFetchTests(unittest.TestCase):
    PLAN = {"mlb": {"required": [2026], "optional": [2027]}, "minor": {"required": [2026], "optional": [2027]}}

    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.settings = self.tmp / "settings.json"
        self.settings.write_text(json.dumps({"players": []}), encoding="utf-8")
        self.dynamic = self.tmp / "dynamic.json"
        self.w = fetch_mlb.watchlist
        self.urls = []

    def fetcher(self, data, teams=None):
        """data: {(sportId, 賽季): people list 或 None（失敗）}；沒列出的組合視為失敗。teams: {賽季: [team dict]}。"""
        def fetch(url, retries=3, delay=5):
            self.urls.append(url)
            if "/teams?" in url:
                return None if teams is None else {"teams": teams.get(int(url.split("season=")[1]), [])}
            sid = int(url.split("/sports/")[1].split("/")[0])
            people = data.get((sid, int(url.split("season=")[1])))
            return None if people is None else {"people": people}
        return fetch

    def gen(self, data, plan=None, teams=None):
        with mock.patch("mlb_daily.net.fetch_json", side_effect=self.fetcher(data, teams)):
            return quiet(self.w.generate_watchlist, plan or self.PLAN, None, str(self.settings), str(self.dynamic))

    def written(self):
        return json.loads(self.dynamic.read_text(encoding="utf-8"))

    def levels(self):
        return {p["id"]: p["level"] for p in self.written()["players"]}

    def test_mlb_union_across_seasons_dedup_and_season_field_is_list(self):
        ok = self.gen({(1, 2026): [person(1, "A Tw", "Taiwan"), person(2, "B Jp", "Japan")],
                       (1, 2027): [person(2, "B Jp", "Japan"), person(3, "C Kr", "Korea")],
                       (11, 2026): [person(9, "Z Tw", "Taiwan")], (12, 2026): [person(9, "Z Tw", "Taiwan")]})
        self.assertTrue(ok)
        got = self.written()
        self.assertEqual(sorted(p["id"] for p in got["players"]), [1, 2, 3, 9])
        self.assertEqual(len([p for p in got["players"] if p["id"] == 2]), 1)
        self.assertEqual(got["season"], [2026, 2027])

    def test_minor_newer_season_level_wins_even_if_lower(self):
        self.gen({(1, 2026): [person(8, "M Tw", "Taiwan")],
                  (11, 2026): [person(1, "A Tw", "Taiwan")], (12, 2027): [person(1, "A Tw", "Taiwan")],
                  (12, 2026): [person(2, "B Tw", "Taiwan")], (13, 2026): [person(2, "B Tw", "Taiwan")],
                  (11, 2027): [person(8, "M Tw", "Taiwan")]})
        got = self.levels()
        self.assertEqual(got[1], "AA")     # 2027 的 AA 優先於 2026 的 AAA
        self.assertEqual(got[2], "AA")     # 同賽季才比層級
        self.assertEqual(got[8], "MLB")    # 大聯盟名單有的人一律 MLB

    def test_required_failure_carries_old_level_but_keeps_successful_seasons(self):
        self.dynamic.write_text(json.dumps({"players": [
            {"id": 10, "name_en": "Old Aaa", "region": "TW", "level": "AAA"},
            {"id": 11, "name_en": "Old Mlb", "region": "JP", "level": "MLB"}]}), encoding="utf-8")
        # AAA 必查 2026 失敗，但選查 2027 成功；MLB 兩季都成功
        self.gen({(1, 2026): [person(1, "A Tw", "Taiwan")], (1, 2027): [person(2, "B Tw", "Taiwan")],
                  (11, 2027): [person(3, "C Tw", "Taiwan")], (12, 2026): [person(4, "D Tw", "Taiwan")]})
        self.assertEqual(self.levels(), {1: "MLB", 2: "MLB", 3: "AAA", 4: "AA", 10: "AAA"})  # 10 沿用；11（MLB 層級）不沿用

    def test_optional_failure_or_empty_is_not_a_failure(self):
        self.dynamic.write_text(json.dumps({"players": [
            {"id": 10, "name_en": "Old Aaa", "region": "TW", "level": "AAA"}]}), encoding="utf-8")
        out = io.StringIO()
        data = {(1, 2026): [person(1, "A Tw", "Taiwan")], (1, 2027): [],
                (11, 2026): [person(3, "C Tw", "Taiwan")], (11, 2027): None,
                (12, 2026): [person(4, "D Tw", "Taiwan")]}
        with mock.patch("mlb_daily.net.fetch_json", side_effect=self.fetcher(data)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(self.w.generate_watchlist(self.PLAN, None, str(self.settings), str(self.dynamic)))
        self.assertEqual(sorted(self.levels()), [1, 3, 4])  # 10 不沿用：AAA 必查賽季成功
        self.assertIn("選查", out.getvalue())
        self.assertNotIn("AAA 層級必查", out.getvalue())

    def test_all_required_fail_returns_none_keeps_old_file(self):
        self.dynamic.write_text('{"players": []}', encoding="utf-8")
        before = self.dynamic.read_text(encoding="utf-8")
        self.assertFalse(self.gen({}))
        self.assertEqual(self.dynamic.read_text(encoding="utf-8"), before)

    def test_affiliates_use_latest_minor_season_then_fall_back(self):
        teams = {2027: [{"id": 400, "parentOrgId": 133, "sport": {"id": 11}}], 2026: []}
        self.gen({(1, 2026): [person(1, "A Tw", "Taiwan", team=400)]}, teams=teams)
        self.assertEqual(self.written()["players"][0]["org_id"], 133)
        self.assertEqual([u.split("season=")[1] for u in self.urls if "/teams?" in u], ["2027"])
        self.urls.clear()
        teams = {2027: [], 2026: [{"id": 400, "parentOrgId": 109, "sport": {"id": 11}}]}
        self.gen({(1, 2026): [person(1, "A Tw", "Taiwan", team=400)]}, teams=teams)
        self.assertEqual(self.written()["players"][0]["org_id"], 109)
        self.assertEqual([u.split("season=")[1] for u in self.urls if "/teams?" in u], ["2027", "2026"])

    def test_cli_run_watchlist_passes_season_plan(self):
        args = mock.Mock(date="2026-11-23")
        ctx = {"rosterType": "40Man", "season": 2026, "rosterSeason": 2026}
        with mock.patch("mlb_daily.rosters.resolve_roster_context", return_value=ctx), \
                mock.patch("mlb_daily.season.get_season_dates", return_value=(date(2026, 3, 25), date(2026, 11, 4))), \
                mock.patch("mlb_daily.watchlist.generate_watchlist", return_value=True) as gen:
            fetch_mlb.cli._run_watchlist(args)
        self.assertEqual(gen.call_args[0][0], {
            "mlb": {"required": [2026], "optional": [2027]}, "minor": {"required": [2026], "optional": []}})
        with mock.patch("mlb_daily.rosters.resolve_roster_context", return_value=ctx), \
                mock.patch("mlb_daily.season.get_season_dates", return_value=None), \
                mock.patch("mlb_daily.watchlist.generate_watchlist", return_value=True) as gen:
            fetch_mlb.cli._run_watchlist(args)
        self.assertEqual(gen.call_args[0][0]["mlb"]["optional"], [2027])  # 取不到賽季日期：以 11 月起推估


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.wl = load_wl()
        self.w = fetch_mlb.watchlist

    def test_players_in_text_whole_word_only(self):
        self.assertEqual(self.w.players_in_text("Leeway given to managers", self.wl), [])
        self.assertEqual(self.w.players_in_text("Kimbrel signs a deal", self.wl), [])
        self.assertEqual(self.w.players_in_text("Test Kim wins award", self.wl), [KIM])  # 別名 Kim 整詞命中
        self.assertEqual(self.w.players_in_text("OHTANI's night", self.wl), [OHTANI])  # 不分大小寫，所有格
        self.assertEqual(self.w.players_in_text("shohei ohtani and Kai-Wei Teng", self.wl), [TENG, OHTANI])
        self.assertEqual(self.w.players_in_text("", self.wl), [])

    def test_players_in_transaction_uses_persons_and_person_id(self):
        tx = {"person_id": 777101, "persons": [{"id": 777101, "name": "Other Guy"}, {"id": HAOYU, "name": "Hao-Yu Lee"}]}
        self.assertEqual(self.w.players_in_transaction(tx, self.wl), [HAOYU])
        self.assertEqual(self.w.players_in_transaction({"person_id": TENG}, self.wl), [TENG])
        self.assertEqual(self.w.players_in_transaction({"person_id": 1}, self.wl), [])

    def test_match_transactions_uses_raw_data_including_minor_league_and_multi_person(self):
        raw = base.load_fixture("transactions_watchlist")["transactions"]
        # 原始資料含「Minor League」描述，大聯盟過濾會濾掉，名單比對仍要看到
        filtered = [t["description"] for t in fetch_mlb.filter_major_league_transactions(raw)]
        self.assertNotIn("Houston Astros assigned LHP Kai-Wei Teng to the Minor League camp.", filtered)
        matched = self.w.match_transactions(raw, self.wl)
        descs = [m["description"] for m in matched]
        self.assertEqual(descs, [
            "Houston Astros optioned RHP Kai-Wei Teng to Sugar Land Space Cowboys.",
            "Detroit Tigers traded 2B Hao-Yu Lee and RHP Other Guy to Boston Red Sox.",
            "Houston Astros assigned LHP Kai-Wei Teng to the Minor League camp.",
            "Boston Red Sox traded 2B Tsung-Che Cheng to Detroit Tigers for cash.",
        ])
        trade = matched[1]
        self.assertEqual({p["id"] for p in trade["persons"]}, {777101, HAOYU})  # 多人交易合併，即使名單球員在第二筆
        self.assertEqual((trade["to_team_id"], trade["from_team_id"]), (111, 116))
        self.assertEqual(trade["type_code"], "TR")
        self.assertEqual(self.w.players_in_transaction(trade, self.wl), [HAOYU])

    def test_reversed_record_without_person_does_not_decide_direction(self):
        # fixture 中無 person 的反向紀錄（from=老虎、to=紅襪）排在前面；應採名單球員那筆（from=紅襪、to=老虎）
        raw = base.load_fixture("transactions_watchlist")["transactions"]
        desc = "Boston Red Sox traded 2B Tsung-Che Cheng to Detroit Tigers for cash."
        self.assertIsNone([t for t in raw if t["description"] == desc][0].get("person"))
        cheng = next(m for m in self.w.match_transactions(raw, self.wl) if m["description"] == desc)
        self.assertEqual((cheng["to_team_id"], cheng["from_team_id"]), (116, 111))
        self.assertEqual(cheng["person_id"], CHENG)
        spot = self.w.build_spotlight(DATE, self.wl, [cheng])
        self.assertIn("台灣・老虎", self.w.render_spotlight_section(spot, self.wl))
        # 一般異動過濾的去重同理：改採有 person 的紀錄
        kept = next(t for t in fetch_mlb.filter_major_league_transactions(raw) if t["description"] == desc)
        self.assertEqual((kept["to_team_id"], kept["from_team_id"], kept["person_id"]), (116, 111, CHENG))

    def test_match_transactions_empty_inputs(self):
        self.assertEqual(self.w.match_transactions([], self.wl), [])
        self.assertEqual(self.w.match_transactions(base.load_fixture("transactions_watchlist")["transactions"], {}), [])


class FetchAndMatchNewsTests(base.MlbTestBase):
    def test_all_news_unlimited_but_daily_news_keeps_limit_and_summary(self):
        self.api.news = "news_rss_watchlist.xml"
        all_news = fetch_mlb.fetch_all_daily_news(DATE)
        shown = fetch_mlb.fetch_daily_news(DATE)
        self.assertEqual(len(all_news), 18)
        self.assertEqual(len(shown), 12)
        self.assertEqual([n["title"] for n in shown], [n["title"] for n in all_news[:12]])
        teng = next(n for n in all_news if n["title"] == "Astros reshuffle pitching staff")
        self.assertEqual(teng["summary"], "Right-hander Kai-Wei Teng was optioned & will report to camp.")  # 去標籤、解實體

    def test_match_news_title_summary_and_false_positives(self):
        self.api.news = "news_rss_watchlist.xml"
        wl = load_wl()
        matches = fetch_mlb.watchlist.match_news(fetch_mlb.fetch_all_daily_news(DATE), wl)
        got = {n["title"]: ids for n, ids in matches}
        self.assertEqual(got["Ohtani homers twice as Dodgers rally"], [OHTANI])  # 標題命中
        self.assertEqual(got["Astros reshuffle pitching staff"], [TENG])  # 摘要命中
        self.assertEqual(got["Ohtani and Kai-Wei Teng headline Asia roster"], [TENG, OHTANI])
        self.assertEqual(got["Test Kim wins award"], [KIM])
        self.assertEqual(got["Late Shohei Ohtani feature"], [OHTANI])
        self.assertNotIn("Leeway given to managers on challenges", got)
        self.assertNotIn("Kimbrel signs minor league deal", got)
        self.assertEqual(len(got), 5)

    def test_daily_transactions_behavior_unchanged(self):
        self.api.transactions = "transactions_default"
        raw = fetch_mlb.fetch_raw_transactions(DATE)
        self.assertEqual(raw, base.load_fixture("transactions_default")["transactions"])
        self.assertEqual(fetch_mlb.fetch_daily_transactions(DATE), fetch_mlb.filter_major_league_transactions(raw))
        self.api.transactions = None
        self.assertEqual(fetch_mlb.fetch_raw_transactions(DATE), [])


# ---------------------------------------------------------------- boxscore
class GameLineTests(unittest.TestCase):
    def setUp(self):
        self.wl = load_wl()
        self.w = fetch_mlb.watchlist

    def test_extract_game_lines(self):
        lines = self.w.extract_game_lines([base.load_fixture("boxscore_watchlist")], self.wl)
        by_id = {g["player_id"]: g for g in lines}
        # 只投球
        self.assertEqual(by_id[TENG], {"player_id": TENG, "team_id": 117, "batting": None, "pitching": "2.1 局、1 安打、3 三振"})
        # 二刀流：兩列都有，0 的項目省略
        self.assertEqual(by_id[OHTANI]["batting"], "4 打數 2 安打、1 全壘打、2 打點、1 保送")
        self.assertEqual(by_id[OHTANI]["pitching"], "6.0 局、3 安打、1 失分、8 三振")
        self.assertEqual(by_id[OHTANI]["team_id"], 119)
        # 只有保送（有打席、無打數）仍顯示，打數安打一定顯示
        self.assertEqual(by_id[JHLEE]["batting"], "0 打數 0 安打、1 保送")
        # 只守備、代跑未打席：不列
        self.assertNotIn(HAOYU, by_id)
        self.assertNotIn(CHENG, by_id)
        self.assertEqual(len(lines), 3)

    def test_formats_and_bad_input(self):
        self.assertIsNone(self.w.format_batting({}))
        self.assertIsNone(self.w.format_batting({"atBats": 0, "plateAppearances": 0, "runs": 1}))
        self.assertEqual(self.w.format_batting({"atBats": 3, "hits": 0, "plateAppearances": 3}), "3 打數 0 安打")
        self.assertEqual(self.w.format_batting({"atBats": 4, "hits": 3, "doubles": 1, "triples": 1, "stolenBases": 2}),
                         "4 打數 3 安打、1 二壘安打、1 三壘安打、2 盜壘")
        self.assertIsNone(self.w.format_pitching({"inningsPitched": "0.0"}))
        self.assertIsNone(self.w.format_pitching({}))
        self.assertIsNone(self.w.format_pitching({"inningsPitched": "0.0", "battersFaced": 0, "numberOfPitches": 0, "runs": 2}))
        # 0 局失分（面對打者或有投球數）仍顯示
        self.assertEqual(self.w.format_pitching({"inningsPitched": "0.0", "battersFaced": 5, "hits": 2, "runs": 3}),
                         "0.0 局、2 安打、3 失分")
        self.assertEqual(self.w.format_pitching({"numberOfPitches": 7, "runs": 1}), "0.0 局、1 失分")
        self.assertEqual(self.w.format_pitching({"inningsPitched": "0.1"}), "0.1 局")
        self.assertEqual(self.w.extract_game_lines([None, {}, {"teams": []}, {"teams": {"away": 1, "home": {"players": []}}}], self.wl), [])
        self.assertEqual(self.w.extract_game_lines([base.load_fixture("boxscore_watchlist")], {}), [])


# ---------------------------------------------------------------- 台灣球員小聯盟比賽成績
def minor_split(date, team, opp, stat, game_pk=1, is_home=True, abbr="AAA"):
    return {"date": date, "sport": {"abbreviation": abbr}, "team": {"name": team}, "opponent": {"name": opp},
            "isHome": is_home, "game": {"gamePk": game_pk}, "stat": stat}


def minor_people(pid, hitting=(), pitching=()):
    stats = []
    if hitting:
        stats.append({"group": {"displayName": "hitting"}, "splits": list(hitting)})
    if pitching:
        stats.append({"group": {"displayName": "pitching"}, "splits": list(pitching)})
    return {"id": pid, "stats": stats}


HIT = {"plateAppearances": 5, "atBats": 4, "hits": 2, "doubles": 1, "baseOnBalls": 1}
PIT = {"inningsPitched": "1.0", "hits": 0, "runs": 0, "strikeOuts": 1}


class MinorGameLineTests(unittest.TestCase):
    def setUp(self):
        self.wl = load_wl()
        self.w = fetch_mlb.watchlist

    def run_fetch(self, responses, wl=None):
        """responses：{sportId: 回應 dict 或 None}；回傳 (lines, 呼叫的 URL 清單)。"""
        urls = []

        def fake(url, retries=3, delay=5):
            urls.append(url)
            sid = int(url.rsplit("sportId=", 1)[1].rstrip(")"))
            return responses.get(sid)

        with mock.patch("mlb_daily.net.fetch_json", side_effect=fake),                 contextlib.redirect_stderr(io.StringIO()):
            lines = self.w.fetch_minor_game_lines(DATE, self.wl if wl is None else wl)
        return lines, urls

    def test_parse_batting_pitching_merge_filter_and_doubleheader(self):
        people = [
            minor_people(HAOYU,
                         hitting=[minor_split(DATE, "Toledo Mud Hens", "St. Paul Saints", HIT, game_pk=1),
                                  minor_split(DATE, "Toledo Mud Hens", "St. Paul Saints", HIT, game_pk=2),  # 雙重賽第二場
                                  minor_split("2020-01-01", "Toledo Mud Hens", "X", HIT, game_pk=3)],  # 非當日
                         pitching=[minor_split(DATE, "Toledo Mud Hens", "St. Paul Saints", PIT, game_pk=1)]),  # 同場合併
            minor_people(TENG, pitching=[minor_split(DATE, "Sugar Land", "Y", {"inningsPitched": "0.0"}, game_pk=9)]),  # 無投球：不列
        ]
        lines, urls = self.run_fetch({11: {"people": people}, 12: {"people": []}, 13: {"people": []},
                                      14: {"people": []}, 16: {"people": []}})
        self.assertEqual(len(urls), 5)  # 逐一 sportId，共 5 次
        self.assertEqual([int(u.rsplit("sportId=", 1)[1].rstrip(")")) for u in urls], [11, 12, 13, 14, 16])
        self.assertEqual(len(lines), 2)
        g1, g2 = lines
        self.assertEqual(g1["player_id"], HAOYU)
        self.assertEqual(g1["level"], "AAA")
        self.assertEqual(g1["team_name"], "Toledo Mud Hens")
        self.assertEqual(g1["opponent"], "St. Paul Saints")
        self.assertEqual(g1["batting"], "4 打數 2 安打、1 二壘安打、1 保送")
        self.assertEqual(g1["pitching"], "1.0 局、1 三振")  # 同 gamePk 的打擊與投球合併成一筆
        self.assertIsNone(g2["pitching"])  # 第二場只有打擊

    def test_only_tw_players_queried(self):
        lines, urls = self.run_fetch({})
        ids = urls[0].split("personIds=")[1].split("&")[0].split(",")
        self.assertEqual(set(ids), {str(TENG), str(HAOYU), str(CHENG)})  # 日、韓不查

    def test_no_tw_players_makes_no_api_call(self):
        wl = {k: v for k, v in self.wl.items() if v["region"] != "TW"}
        lines, urls = self.run_fetch({}, wl=wl)
        self.assertEqual((lines, urls), ([], []))
        self.assertEqual(self.run_fetch({}, wl={})[1], [])

    def test_failed_sport_id_is_skipped_with_warning(self):
        people = [minor_people(HAOYU, hitting=[minor_split(DATE, "A", "B", HIT)])]
        err = io.StringIO()
        with mock.patch("mlb_daily.net.fetch_json",
                        side_effect=lambda url, **kw: {"people": people} if "sportId=12" in url else None),                 contextlib.redirect_stderr(err):
            lines = self.w.fetch_minor_game_lines(DATE, self.wl)
        self.assertEqual([g["level"] for g in lines], ["AA"])
        self.assertEqual(err.getvalue().count("Warning"), 4)

    def test_render_has_level_label_team_and_escapes_html(self):
        line = {"player_id": HAOYU, "team_id": None, "batting": "4 打數 2 安打", "pitching": None, "level": "AAA",
                "team_name": "<b>Mud</b> Hens", "opponent": "A&B <i>x</i>", "is_home": True}
        mlb = {"player_id": HAOYU, "team_id": 117, "batting": "3 打數 1 安打", "pitching": None}
        entries = self.w.build_spotlight(DATE, self.wl, game_lines=[mlb, line])
        self.assertEqual(entries[1]["level"], "AAA")
        self.assertNotIn("level", entries[0])
        json.dumps(entries)
        html = self.w.render_spotlight_section(entries, self.wl)
        self.assertIn("3A：&lt;b&gt;Mud&lt;/b&gt; Hens 對 A&amp;B &lt;i&gt;x&lt;/i&gt;", html)
        self.assertNotIn("<b>", html)
        self.assertLess(html.index("3 打數 1 安打"), html.index("4 打數 2 安打"))  # 大聯盟在前
        for level, label in (("AA", "2A"), ("A+", "高階1A"), ("A", "1A"), ("ROK", "新人聯盟")):
            e = self.w.build_spotlight(DATE, self.wl, game_lines=[{**line, "level": level}])
            self.assertIn(label + "：", self.w.render_spotlight_section(e, self.wl))
        # 首頁（含日期）也能顯示
        home = self.w.render_spotlight_home([{**e, "date": DATE} for e in entries], self.wl)
        self.assertIn("3A：", home)


# ---------------------------------------------------------------- 資料與渲染
class BuildAndRenderTests(unittest.TestCase):
    def setUp(self):
        self.wl = load_wl()
        self.w = fetch_mlb.watchlist

    def test_build_spotlight_prefers_translation_and_lists_news_once(self):
        tx = {"description": "Eng desc", "description_zh": "中文異動", "person_id": TENG, "date": "2026-11-14",
              "persons": [{"id": TENG, "name": "Kai-Wei Teng"}]}
        news = {"title": "T", "title_zh": "中文標題", "link": "https://www.mlb.com/news/x"}
        spot = self.w.build_spotlight(DATE, self.wl, [tx], [(news, [TENG, OHTANI])],
                                      [{"player_id": OHTANI, "team_id": 119, "batting": "b", "pitching": None}])
        self.assertEqual(spot[0], {"player_id": TENG, "kind": "transaction", "text": "中文異動", "date": "2026-11-14",
                                   "persons": [{"id": TENG, "name": "Kai-Wei Teng"}],
                                   "to_team_id": None, "from_team_id": None})
        self.assertEqual(spot[1], {"player_id": TENG, "kind": "news", "text": "中文標題", "link": "https://www.mlb.com/news/x",
                                   "date": DATE, "also_player_ids": [OHTANI]})  # 只列一次，歸第一位球員
        self.assertEqual(spot[2]["kind"], "game")
        self.assertEqual(len(spot), 3)
        json.dumps(spot)  # 可序列化

    def test_build_spotlight_transaction_also_player_ids(self):
        tx = {"description": "Trade of Kai-Wei Teng and Hao-Yu Lee", "person_id": HAOYU, "date": DATE, "to_team_id": 111, "from_team_id": 116,
              "persons": [{"id": HAOYU, "name": "Hao-Yu Lee"}, {"id": TENG, "name": "Kai-Wei Teng"}]}
        spot = self.w.build_spotlight(DATE, self.wl, [tx])
        self.assertEqual(len(spot), 1)
        self.assertEqual(spot[0]["player_id"], HAOYU)  # 異動 persons 順序的第一位名單球員
        self.assertEqual(spot[0]["also_player_ids"], [TENG])
        self.assertEqual((spot[0]["to_team_id"], spot[0]["from_team_id"]), (111, 116))

    def test_same_as_above_refs_for_news_and_transactions(self):
        spot = [
            {"player_id": TENG, "kind": "news", "text": "共同新聞", "link": "https://www.mlb.com/news/a", "date": DATE,
             "also_player_ids": [OHTANI, 1]},
            {"player_id": TENG, "kind": "transaction", "text": "共同異動", "date": DATE, "persons": [], "also_player_ids": [JHLEE]},
        ]
        html = self.w.render_spotlight_section(spot, self.wl)
        self.assertEqual(html.count("共同新聞"), 1)  # 標題與連結只出現一次
        self.assertEqual(html.count("共同異動"), 1)
        self.assertEqual(html.count("https://www.mlb.com/news/a"), 1)
        self.assertEqual(html.count("新聞：（同上則新聞）"), 1)
        self.assertEqual(html.count("人事異動：（同上則異動）"), 1)
        self.assertIn("大谷翔平", html)  # 第二位球員有自己的分組
        self.assertIn("李政厚", html)
        self.assertEqual(html.count('class="spot-group"'), 3)

    def test_team_inferred_from_transaction(self):
        def head(**kw):
            e = {"player_id": TENG, "kind": "transaction", "text": "x", "date": DATE, **kw}
            return self.w.render_spotlight_section([e], self.wl)
        self.assertIn("台灣・老虎", head(to_team_id=116, from_team_id=117))  # 優先 to
        self.assertIn("台灣・太空人", head(to_team_id=5434, from_team_id=117))  # to 非 30 隊 -> from
        self.assertIn("台灣</span>", head(to_team_id=5434, from_team_id=5435))  # 都不是：只顯示地區
        self.assertIn("台灣</span>", head())
        # boxscore 球隊優先於異動推算
        both = [{"player_id": TENG, "kind": "transaction", "text": "x", "date": DATE, "to_team_id": 116},
                {"player_id": TENG, "kind": "game", "batting": "b", "team_id": 117, "date": DATE}]
        self.assertIn("台灣・太空人", self.w.render_spotlight_section(both, self.wl))

    def test_section_hidden_when_empty(self):
        self.assertEqual(self.w.render_spotlight_section([], self.wl), "")
        self.assertEqual(self.w.render_spotlight_section([{"player_id": TENG, "kind": "news", "text": "x"}], {}), "")
        self.assertEqual(self.w.render_spotlight_section([{"player_id": 1, "kind": "game", "batting": "x"}], self.wl), "")

    def test_section_groups_order_team_and_links(self):
        spot = [
            {"player_id": OHTANI, "kind": "game", "batting": "4 打數 2 安打", "pitching": "6.0 局", "team_id": 119, "date": DATE},
            {"player_id": JHLEE, "kind": "news", "text": "韓國新聞", "link": "https://www.mlb.com/news/k", "date": DATE},
            {"player_id": TENG, "kind": "news", "text": "新聞標題", "link": "https://www.mlb.com/news/t", "date": DATE},
            {"player_id": TENG, "kind": "transaction", "text": "Houston Astros optioned RHP Kai-Wei Teng.", "date": DATE,
             "persons": [{"id": TENG, "name": "Kai-Wei Teng"}]},
        ]
        html = self.w.render_spotlight_section(spot, self.wl)
        self.assertIn('id="spot-h">台日韓球員動態', html)
        self.assertLess(html.index("鄧愷威"), html.index("大谷翔平"))  # 台灣 → 日本 → 韓國
        self.assertLess(html.index("大谷翔平"), html.index("李政厚"))
        self.assertIn("台灣</span>", html)  # 沒有 boxscore 球隊：只顯示地區
        self.assertIn("日本・道奇", html)
        self.assertIn("韓國</span>", html)
        self.assertIn("savant-player/678906", html)  # 球員名連結（中文名 + 內文英文名）
        self.assertIn(">鄧愷威</a>", html)
        self.assertIn("人事異動：", html)
        self.assertIn("打擊 4 打數 2 安打", html)
        self.assertIn("投球 6.0 局", html)
        # 同一球員內順序：人事異動 → 新聞
        self.assertLess(html.index("人事異動："), html.index("新聞："))

    def test_section_escapes_external_strings(self):
        wl = dict(self.wl)
        wl[TENG] = {**wl[TENG], "name_zh": "<b>鄧</b>"}
        spot = [
            {"player_id": TENG, "kind": "news", "text": "<script>alert(1)</script>", "link": "https://www.mlb.com/news/a?x=1&y=\"2\"", "date": DATE},
            {"player_id": TENG, "kind": "news", "text": "evil", "link": "javascript:alert(1)", "date": DATE},
            {"player_id": TENG, "kind": "transaction", "text": "<img src=x onerror=1> Kai-Wei Teng", "date": DATE,
             "persons": [{"id": TENG, "name": "Kai-Wei Teng"}]},
            {"player_id": TENG, "kind": "game", "batting": "<i>x</i>", "pitching": None, "date": DATE},
        ]
        html = self.w.render_spotlight_section(spot, wl)
        for raw in ("<script>", "<img src=x", "<b>鄧", "<i>x", "javascript:alert"):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;b&gt;鄧", html)

    def test_home_window_and_render(self):
        meta = {
            "2026-11-15": {"date": "2026-11-15", "spotlight": [
                {"player_id": OHTANI, "kind": "game", "batting": "新", "pitching": None, "team_id": 119, "date": "2026-11-15"}]},
            "2026-11-09": {"date": "2026-11-09", "spotlight": [
                {"player_id": OHTANI, "kind": "news", "text": "邊界", "link": "https://www.mlb.com/news/e", "date": "2026-11-09"}]},
            "2026-11-08": {"date": "2026-11-08", "spotlight": [
                {"player_id": TENG, "kind": "news", "text": "太舊", "link": "https://www.mlb.com/news/o", "date": "2026-11-08"}]},
            "2026-11-14": {"date": "2026-11-14"},  # 無 spotlight 欄位（舊資料）
        }
        entries = self.w.home_window_entries(meta)
        self.assertEqual(sorted(e["date"] for e in entries), ["2026-11-09", "2026-11-15"])  # 最新日往回共 7 天（含）
        html = self.w.render_spotlight_home(entries, self.wl)
        self.assertIn("大谷翔平", html)
        self.assertIn("日本・道奇", html)
        self.assertIn("11/15", html)
        self.assertLess(html.index("11/15"), html.index("11/9"))  # 新到舊
        self.assertNotIn("太舊", html)
        self.assertNotIn("鄧愷威", html)

    def test_home_empty_states(self):
        self.assertIsNone(self.w.home_window_entries({}))
        self.assertEqual(self.w.render_spotlight_home(None, self.wl), "")
        self.assertEqual(self.w.render_spotlight_home([], {}), "")
        self.assertIn("近 7 天無台日韓球員動態。", self.w.render_spotlight_home([], self.wl))
        self.assertIn("近 7 天無台日韓球員動態。", self.w.render_spotlight_home(
            self.w.home_window_entries({"2026-11-15": {"date": "2026-11-15"}}), self.wl))

    def test_home_tolerates_malformed_metadata(self):
        meta = {"2026-11-15": {"spotlight": "oops"}, "2026-11-14": {"spotlight": [1, None, {"player_id": "x"}, {"player_id": OHTANI, "kind": "bad"}]},
                "bad": {"spotlight": []}}
        html = self.w.render_spotlight_home(self.w.home_window_entries(meta), self.wl)
        self.assertIn("近 7 天無台日韓球員動態。", html)


# ---------------------------------------------------------------- 整合：戰報與首頁
class SpotlightIntegrationTests(base.MlbTestBase):
    def setUp(self):
        super().setUp()
        p = mock.patch("mlb_daily.watchlist.WATCHLIST_PATH", WL_PATH)
        p.start()
        self.addCleanup(p.stop)

    def season(self, **kw):
        self.api.news = "news_rss_watchlist.xml"
        self.api.boxscore_overrides[1001] = season_box()
        return self.generate("schedule_normal", transactions="transactions_watchlist", **kw)

    def test_season_report_has_spotlight_before_news_and_metadata(self):
        meta, html = self.season()
        self.assertIn('id="spot-h"', html)
        self.assertLess(html.index('id="spot-h"'), html.index('id="news-h"'))
        self.assertLess(html.index('id="games-h"'), html.index('id="tx-h"'))
        sec = html[html.index('id="spot-h"'):html.index('id="news-h"')]
        self.assertIn("鄧愷威", sec)
        self.assertIn("台灣・太空人", sec)  # 球隊由 boxscore 的 team id 取得
        self.assertIn("日本・道奇", sec)
        self.assertIn("4 打數 2 安打、1 全壘打、2 打點、1 保送", sec)
        self.assertIn("6.0 局、3 安打、1 失分、8 三振", sec)
        self.assertIn("2.1 局、1 安打、3 三振", sec)
        self.assertIn("Houston Astros assigned LHP", sec)  # 小聯盟異動也列（無金鑰：英文原文）
        self.assertIn("Late Shohei Ohtani feature", sec)  # 不在顯示前 12 則的新聞也能比對
        self.assertNotIn("Leeway", sec)
        self.assertNotIn("Kimbrel", sec)
        # 顯示的新聞區塊仍只有 12 則
        self.assertIn('<span class="count">12 則</span>', html)
        self.assertEqual(meta["news_count"], 12)
        kinds = [(e["player_id"], e["kind"]) for e in meta["spotlight"]]
        self.assertIn((TENG, "transaction"), kinds)
        self.assertIn((HAOYU, "transaction"), kinds)
        self.assertIn((OHTANI, "game"), kinds)
        self.assertEqual(sum(1 for e in meta["spotlight"] if e["kind"] == "news" and e["text"] == "Ohtani and Kai-Wei Teng headline Asia roster"), 1)
        game = next(e for e in meta["spotlight"] if e["kind"] == "game" and e["player_id"] == OHTANI)
        self.assertEqual(game["pitching"], "6.0 局、3 安打、1 失分、8 三振")
        json.dumps(meta)

    def test_minor_league_lines_in_season_report(self):
        people = [minor_people(HAOYU, hitting=[minor_split(DATE, "Toledo Mud Hens", "St. Paul Saints", HIT)])]
        orig = self.api.fetch

        def fetch(url, retries=3, delay=5):
            if "stats(group=" in url:
                return {"people": people} if "sportId=11" in url else {"people": []}
            return orig(url)

        with mock.patch("mlb_daily.net.fetch_json", side_effect=fetch):
            meta, html = self.season()
        sec = html[html.index('id="spot-h"'):html.index('id="news-h"')]
        self.assertIn("3A：Toledo Mud Hens 對 St. Paul Saints", sec)
        self.assertIn("4 打數 2 安打、1 二壘安打、1 保送", sec)
        game = next(e for e in meta["spotlight"] if e["kind"] == "game" and e.get("level") == "AAA")
        self.assertEqual(game["player_id"], HAOYU)
        json.dumps(meta)

    def test_minor_failure_does_not_break_report(self):
        meta, html = self.season()  # FakeApi 對 gameLog 網址回傳 None
        self.assertIn('id="spot-h"', html)
        self.assertFalse(any(e.get("level") for e in meta["spotlight"]))

    def test_boxscore_not_refetched_for_spotlight(self):
        self.season()
        self.assertEqual(sum(1 for u in self.api.calls if "/game/1001/boxscore" in u), 1)

    def test_metadata_written_and_home_shows_seven_day_summary(self):
        meta, _ = self.season()
        self.quiet_update_index(meta)
        self.assertEqual(self.read_metadata()[DATE]["spotlight"], meta["spotlight"])
        index = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="spot-h"', index)
        self.assertLess(index.index('id="latest-h"'), index.index('id="spot-h"'))
        self.assertIn("大谷翔平", index)
        self.assertIn("11/15", index)

    def test_no_spotlight_when_nothing_matches(self):
        # 有名單但當日無名單球員的異動／新聞／出賽：整區隱藏、metadata 不寫 spotlight
        self.api.news = "news_rss.xml"
        meta, html = self.generate("schedule_normal", transactions="transactions_default")
        self.assertNotIn("spot-h", html)
        self.assertNotIn("台日韓", html)
        self.assertNotIn("spotlight", meta)

    def test_no_slot_marker_left_in_output(self):
        _, html = self.season()
        self.assertNotIn("<!--SLOT:", html)

    def test_offseason_report(self):
        self.api.news = "news_rss_watchlist.xml"
        self.api.transactions = "transactions_watchlist"
        meta = quiet(fetch_mlb.generate_offseason_report, DATE)
        html = self.read_report()
        self.assertEqual(meta["type"], "offseason")
        self.assertIn('id="spot-h"', html)
        self.assertLess(html.index('id="spot-h"'), html.index('id="news-h"'))
        self.assertIn("鄧愷威", html)
        self.assertIn("Houston Astros assigned LHP", html)
        self.assertNotIn("打擊", html)
        # 休賽季無 boxscore：球隊由異動推算（鄧愷威 to 小聯盟球隊 -> 取 from 太空人；李灝宇 to 紅襪）
        self.assertIn("台灣・太空人", html)
        self.assertIn("台灣・紅襪", html)
        self.assertTrue(all(e["kind"] != "game" for e in meta["spotlight"]))
        self.assertEqual(meta["news_count"], 12)

    def test_offseason_without_matches_has_no_section(self):
        self.api.news = "news_rss.xml"
        meta = quiet(fetch_mlb.generate_offseason_report, DATE)
        self.assertNotIn("spot-h", self.read_report())
        self.assertNotIn("spotlight", meta)

    def test_missing_or_corrupt_watchlist_does_not_break_run(self):
        for content in (None, "{broken"):
            with self.subTest(content=content):
                bad = self.tmp / "bad.json"
                if content is None:
                    path = str(self.tmp / "nope.json")
                else:
                    bad.write_text(content, encoding="utf-8")
                    path = str(bad)
                with mock.patch("mlb_daily.watchlist.WATCHLIST_PATH", path):
                    meta, html = self.season()
                    self.quiet_update_index(meta)
                self.assertNotIn("spot-h", html)
                self.assertNotIn("spotlight", meta)
                self.assertEqual(meta["total_games"], 2)
                self.assertNotIn("spot-h", (self.site / "index.html").read_text(encoding="utf-8"))

    def test_home_empty_state_and_hidden_cases(self):
        plain = {"date": DATE, "total_games": 2, "postponed": 0, "summary": "s", "type": "season"}
        self.quiet_update_index(plain)
        index = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertIn("近 7 天無台日韓球員動態。", index)
        self.assertLess(index.index('id="latest-h"'), index.index('id="spot-h"'))
        # 名單失敗：不顯示
        with mock.patch("mlb_daily.watchlist.WATCHLIST_PATH", str(self.tmp / "nope.json")):
            self.quiet_update_index(plain)
        self.assertNotIn("spot-h", (self.site / "index.html").read_text(encoding="utf-8"))
        # 尚無任何戰報：不顯示
        self.assertNotIn("spot-h", quiet(fetch_mlb.render_index, {}))

    def test_home_xss_from_metadata_is_escaped(self):
        meta = {"date": DATE, "total_games": 1, "postponed": 0, "summary": "s", "type": "season", "spotlight": [
            {"player_id": OHTANI, "kind": "news", "text": "<script>alert('X')</script>", "link": "https://www.mlb.com/news/\"><x", "date": DATE}]}
        self.quiet_update_index(meta)
        index = (self.site / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert('X')", index)
        self.assertIn("&lt;script&gt;alert(&#x27;X&#x27;)", index)
        self.assertNotIn('"><x', index)

    def test_translation_includes_spotlight_texts_once(self):
        os_env = {"GEMINI_API_KEY": "test-key-not-real"}
        sent = []

        def fake_translate(items, extra=()):
            sent.extend(it["text"] for it in items)
            return ["ZH:" + it["text"] for it in items]

        self.api.news = "news_rss_watchlist.xml"
        self.api.boxscore_overrides[1001] = season_box()
        self.api.schedule = "schedule_normal"
        self.api.transactions = "transactions_watchlist"
        with mock.patch.dict("os.environ", os_env), mock.patch("mlb_daily.translate.translate_texts", side_effect=fake_translate):
            meta = quiet(fetch_mlb.generate_report, DATE, self.games_of("schedule_normal"))
        html = self.read_report()
        self.assertEqual(len(sent), len(set(sent)), "同一字串不得重送")
        self.assertIn("Astros reshuffle pitching staff", sent)  # 不在前 12 則、但名單命中的新聞標題
        self.assertIn("Houston Astros assigned LHP Kai-Wei Teng to the Minor League camp.", sent)  # 名單異動描述
        sec = html[html.index('id="spot-h"'):html.index('id="news-h"')]
        self.assertIn("ZH:Houston Astros assigned LHP", sec)
        self.assertIn("ZH:Late Shohei Ohtani feature", sec)
        self.assertTrue(any(e["text"].startswith("ZH:") for e in meta["spotlight"] if e["kind"] == "news"))
        # 同描述若同時在一般異動區塊：兩處都套用譯文
        self.assertIn("ZH:Houston Astros optioned RHP", html)


class TemplateSlotTests(unittest.TestCase):
    def test_slot_positions(self):
        report = (base.REPO_ROOT / "templates" / "report.html").read_text(encoding="utf-8")
        s = lambda n: report.index(f"<!--SLOT:{n}-->")  # noqa: E731
        self.assertLess(s("SUMMARY"), s("SPOTLIGHT"))
        self.assertLess(s("SPOTLIGHT"), s("NEWS"))
        index = (base.REPO_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertLess(index.index("<!--SLOT:LATEST-->"), index.index("<!--SLOT:SPOTLIGHT-->"))

    def test_css_has_spotlight_styles_without_horizontal_overflow_rules(self):
        css = (base.REPO_ROOT / "site" / "assets" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".spot-group", css)
        self.assertRegex(css, r"\.spot-group h3[^}]*overflow-wrap: anywhere")


if __name__ == "__main__":
    unittest.main()
