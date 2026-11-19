"""球隊專頁附加資料測試（mlb_daily/team_page.py）：近 30 天動態、新聞關鍵字比對、數據王與門檻、戰績與季後賽摘要、缺資料降級。
只用 unittest 與固定資料／fixtures，不依賴真實日期、不連網。"""

import contextlib
import io
import json
import shutil
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

fetch_mlb = base.fetch_mlb
team_page = fetch_mlb.team_page
weekly = fetch_mlb.weekly
SNAP_DIR = base.FIXTURES / "daily_snapshots"
LAD, NYY, BOS, SF, WSH, CHC, MIN, TB = 119, 147, 111, 137, 120, 112, 142, 139


def tx(code, desc, to_id=None, from_id=None, day="2026-11-10", person="Some Player", zh=None):
    return {"type_code": code, "date": day, "description": desc, "description_zh": zh, "person_id": 1, "person_name": person,
            "persons": [{"id": 1, "name": person}], "to_team_id": to_id, "from_team_id": from_id}


def news(title, link, published="2026-11-10T09:00:00+08:00", zh=None, summary=None):
    return {"title": title, "title_zh": zh, "link": link, "published": published, "summary": summary}


def snap(day, txs=(), items=()):
    return {"date": day, "has_games": False, "transactions": list(txs), "news": list(items)}


class RecentMovesTests(unittest.TestCase):
    def test_trade_splits_join_and_leave(self):
        snaps = {"2026-11-10": snap("2026-11-10", [
            tx("TR", "Giants traded 2B A to Nationals for LHP B.", to_id=WSH, from_id=SF, person="A", zh="巨人將 A 交易到國民")])}
        sf = team_page.build_recent(SF, snaps)
        wsh = team_page.build_recent(WSH, snaps)
        self.assertEqual([(m["dir"], m["type"], m["player"]) for m in sf["moves"]], [("out", "交易", "A")])
        self.assertEqual([(m["dir"], m["type"]) for m in wsh["moves"]], [("in", "交易")])
        self.assertEqual(sf["moves"][0]["note"], "巨人將 A 交易到國民")      # 優先用中文說明
        self.assertEqual(team_page.build_recent(NYY, snaps)["moves"], [])  # 無關球隊

    def test_signing_is_join_and_dfa_is_leave(self):
        snaps = {"2026-11-10": snap("2026-11-10", [
            tx("SFA", "Red Sox signed free agent RHP X to a 2-year contract.", to_id=BOS),
            tx("DES", "Cubs designated RHP Y for assignment.", to_id=CHC),
            tx("SE", "Cubs selected the contract of OF Z.", to_id=CHC, person="Z"),
            tx("SC", "Cubs placed RHP W on the 15-day injured list.", to_id=CHC)])}   # 傷兵名單不列入
        self.assertEqual([(m["dir"], m["type"]) for m in team_page.build_recent(BOS, snaps)["moves"]],
                         [("in", "自由球員簽約")])
        cubs = team_page.build_recent(CHC, snaps)["moves"]
        self.assertEqual(sorted((m["dir"], m["type"]) for m in cubs), [("in", "40 人名單異動"), ("out", "DFA／釋出／讓渡")])

    def test_moves_newest_first_and_deduplicated(self):
        a = tx("SFA", "Red Sox signed free agent RHP A.", to_id=BOS, day="2026-11-05", person="A")
        b = tx("SFA", "Red Sox signed free agent RHP B.", to_id=BOS, day="2026-11-12", person="B")
        snaps = {"2026-11-05": snap("2026-11-05", [a]), "2026-11-12": snap("2026-11-12", [b, a])}  # a 重複
        moves = team_page.build_recent(BOS, snaps)["moves"]
        self.assertEqual([m["player"] for m in moves], ["B", "A"])

    def test_snapshot_days_counts_loaded_files(self):
        self.assertEqual(team_page.build_recent(BOS, {})["snapshotDays"], 0)
        self.assertEqual(team_page.build_recent(BOS, {"2026-11-10": snap("2026-11-10")})["snapshotDays"], 1)


class RecentWindowTests(unittest.TestCase):
    def test_moves_outside_window_are_dropped(self):
        old = tx("SFA", "Red Sox signed free agent RHP Old.", to_id=BOS, day="2026-10-01", person="Old")
        ok = tx("SFA", "Red Sox signed free agent RHP Ok.", to_id=BOS, day="2026-11-10", person="Ok")
        edge = tx("SFA", "Red Sox signed free agent RHP Edge.", to_id=BOS, day="2026-11-16", person="Edge")
        late = tx("SFA", "Red Sox signed free agent RHP Late.", to_id=BOS, day="2026-11-19", person="Late")
        snaps = {"2026-11-18": snap("2026-11-18", [old, ok, edge, late])}
        moves = team_page.build_recent(BOS, snaps, start=date(2026, 11, 16), end=date(2026, 11, 18))["moves"]
        self.assertEqual([m["player"] for m in moves], ["Edge"])      # 11-10 早於起日 11-16、11-19 晚於終日
        wide = team_page.build_recent(BOS, snaps, start=date(2026, 11, 1), end=date(2026, 11, 18))["moves"]
        self.assertEqual([m["player"] for m in wide], ["Edge", "Ok"])
        self.assertEqual(len(team_page.build_recent(BOS, snaps)["moves"]), 4)     # 不給窗口則不設限

    def test_window_bounds_are_inclusive_and_unparseable_date_is_kept(self):
        self.assertTrue(team_page._in_window("garbage", date(2026, 11, 1), date(2026, 11, 2)))
        self.assertFalse(team_page._in_window("2026-10-31", date(2026, 11, 1), date(2026, 11, 2)))
        self.assertTrue(team_page._in_window("2026-11-01", date(2026, 11, 1), date(2026, 11, 2)))
        self.assertTrue(team_page._in_window("2026-11-02", date(2026, 11, 1), date(2026, 11, 2)))
        self.assertFalse(team_page._in_window("2026-11-03", date(2026, 11, 1), date(2026, 11, 2)))


class RecentNewsTests(unittest.TestCase):
    def rec(self, items, team=NYY, limit=5):
        return team_page.build_recent(team, {"2026-11-10": snap("2026-11-10", items=items)}, news_limit=limit)["news"]

    def test_matches_english_and_chinese_team_terms(self):
        out = self.rec([
            news("Yankees re-sign ace", "https://x/1"),
            news("Offseason notebook", "https://x/2", zh="紐約洋基補強牛棚"),
            news("Yanks eye outfield help", "https://x/3"),
            news("Dodgers win", "https://x/4"),
            news("Notes", "https://x/5", summary="The New York Yankees are in talks.")])
        self.assertEqual(sorted(n["url"] for n in out), ["https://x/1", "https://x/2", "https://x/3", "https://x/5"])

    def test_whole_word_only_for_english(self):
        # "Redsox" 不是球隊關鍵字；"Reds" 為整詞才命中 CIN，"Reddish" 不算
        self.assertEqual(self.rec([news("Reddish skies in Cincinnati", "https://x/1")], team=113), [])
        self.assertEqual(len(self.rec([news("Reds trade for arm", "https://x/2")], team=113)), 1)

    def test_sorted_newest_first_limited_and_deduplicated(self):
        items = [news(f"Yankees story {i}", f"https://x/{i}", published=f"2026-11-0{i + 1}T09:00:00+08:00") for i in range(7)]
        items.append(news("Yankees story 6 again", "https://x/6", published="2026-11-09T09:00:00+08:00"))  # 同 link 去重
        out = self.rec(items, limit=5)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0]["url"], "https://x/6")
        self.assertEqual(out[0]["date"], "2026-11-07")
        self.assertEqual(len({n["url"] for n in out}), 5)

    def test_entry_fields_prefer_translated_title_and_have_source(self):
        out = self.rec([news("Yankees sign", "https://x/1", zh="洋基簽約")])
        self.assertEqual(out[0]["title"], "洋基簽約")
        self.assertEqual(out[0]["source"], "MLB.com")

    def test_false_positives_are_excluded(self):
        cases = [
            (TB, "X-rays came back negative for the shortstop"), (TB, "Prospect's X-Rays look clean"),
            (138, "Collectors are trading cards again"),
            (WSH, "Japanese nationals dominate the WBC"),
            (SF, "Yomiuri Giants ace posted"), (SF, "Lotte Giants outfielder to MLB"),
            (116, "Hanshin Tigers star eyes MLB"), (116, "Kia Tigers pitcher signs"), (116, "KIA Tigers lefty"),
            (MIN, "LG Twins slugger posted"), (MIN, "NC Dinos and LG Twins meet"),
            (WSH, "讀賣巨人與韓國國民打者"), (SF, "讀賣巨人外野手赴美"), (WSH, "韓國國民打者轉戰大聯盟"),
            (TB, "新秀綻放光芒"), (108, "天使之城迎來新球季"), (116, "阪神老虎投手簽約"), (MIN, "LG雙城隊打者"),
        ]
        for team, title in cases:
            with self.subTest(title=title):
                self.assertEqual(self.rec([news(title, "https://x/1")], team=team), [])

    def test_true_positives_still_match(self):
        cases = [
            (SF, "Giants sign veteran reliever"), (SF, "San Francisco Giants add depth"), (SF, "san francisco giants add depth"),
            (SF, "舊金山巨人簽下新投手"), (TB, "Rays trade for outfielder"), (TB, "坦帕灣光芒交易外野手"),
            (WSH, "Nationals promote prospect"), (WSH, "華盛頓國民簽約"), (138, "Cards extend closer"),
            (MIN, "Twins hire new pitching coach"), (116, "Tigers claim lefty"), (116, "Detroit Tigers re-sign"),
            (110, "O's acquire catcher"), (136, "M's add bat"), (133, "A's stadium update"), (133, "Athletics sign OF"),
            (SF, "Giants beat Yomiuri Giants in exhibition"),       # 同句同時有真正的隊名
        ]
        for team, title in cases:
            with self.subTest(title=title):
                self.assertEqual(len(self.rec([news(title, "https://x/1")], team=team)), 1)

    def test_chinese_short_name_alone_does_not_match(self):
        # 中文簡稱歧義太多；譯文標題一律帶英文原文，真實新聞仍會由英文命中
        self.assertEqual(self.rec([news("Offseason notebook", "https://x/1", zh="巨人簽下新投手")], team=SF), [])
        self.assertEqual(len(self.rec([news("Giants sign arm", "https://x/2", zh="巨人簽下新投手")], team=SF)), 1)

    def test_non_http_links_are_dropped(self):
        self.assertEqual(self.rec([news("Yankees sign", "javascript:alert(1)")]), [])


def hitter(pid, name, pa, avg, obp, slg, hr, sb, war):
    return {"id": pid, "name": name, "group": "POS", "pitching": None,
            "hitting": {"pa": pa, "avg": avg, "obp": obp, "slg": slg, "hr": hr, "sb": sb, "war": war}}


def pitcher(pid, name, ip, era, whip, so, sv, war):
    return {"id": pid, "name": name, "group": "RP", "hitting": None,
            "pitching": {"ip": ip, "era": era, "whip": whip, "so": so, "sv": sv, "war": war}}


def by_key(entries):
    return {e["key"]: e for e in entries}


class LeadersTests(unittest.TestCase):
    def test_hitting_leaders_and_pa_threshold(self):
        players = [
            hitter(1, "Regular", 600, .300, .380, .500, 30, 10, 5.0),
            hitter(2, "Hot Part-timer", 299, .400, .500, .800, 12, 30, 2.0),     # 打席不足：AVG／OPS 不計，HR／SB 仍計
            hitter(3, "Exactly 300", 300, .310, .360, .450, 5, 3, 1.0),
        ]
        h = by_key(team_page.compute_leaders(players)["hitting"])
        self.assertEqual((h["avg"]["name"], h["avg"]["value"]), ("Exactly 300", .310))   # 300 打席算達標
        self.assertEqual((h["ops"]["name"], h["ops"]["value"]), ("Regular", .880))
        self.assertEqual((h["hr"]["name"], h["hr"]["value"]), ("Regular", 30))
        self.assertEqual((h["sb"]["name"], h["sb"]["value"]), ("Hot Part-timer", 30))
        self.assertEqual((h["war"]["name"], h["war"]["value"]), ("Regular", 5.0))
        self.assertEqual(h["avg"]["id"], 3)

    def test_pitching_leaders_ip_threshold_and_direction(self):
        players = [
            pitcher(10, "Ace", "182.1", 2.80, 1.00, 220, 0, 6.0),
            pitcher(11, "Closer", "60.0", 2.50, 0.95, 70, 35, 2.0),
            pitcher(12, "Short Sample", "49.2", 0.90, 0.60, 80, 1, 1.0),         # 49.2 局：不足 50 局，ERA／WHIP 不計
        ]
        p = by_key(team_page.compute_leaders(players)["pitching"])
        self.assertEqual((p["era"]["name"], p["era"]["value"]), ("Closer", 2.50))   # ERA 越低越好
        self.assertEqual((p["whip"]["name"], p["whip"]["value"]), ("Closer", 0.95))
        self.assertEqual((p["so"]["name"], p["so"]["value"]), ("Ace", 220))
        self.assertEqual((p["sv"]["name"], p["sv"]["value"]), ("Closer", 35))
        self.assertEqual((p["war"]["name"], p["war"]["value"]), ("Ace", 6.0))

    def test_ties_prefer_more_volume_then_lower_id(self):
        players = [hitter(30, "Few PA", 400, .300, .380, .500, 20, 10, 3.0), hitter(20, "Many PA", 600, .300, .380, .500, 20, 10, 3.0),
                   hitter(10, "Many PA Low Id", 600, .300, .380, .500, 20, 10, 3.0)]
        h = by_key(team_page.compute_leaders(players)["hitting"])
        for key in ("avg", "ops", "hr", "sb", "war"):
            self.assertEqual(h[key]["id"], 10, key)
        self.assertEqual(by_key(team_page.compute_leaders(list(reversed(players)))["hitting"])["hr"]["id"], 10)   # 與名單順序無關
        pitchers = [pitcher(5, "Short", "60.0", 3.0, 1.1, 50, 0, 1.0), pitcher(4, "Long", "100.1", 3.0, 1.1, 50, 0, 1.0),
                    pitcher(3, "Long Low Id", "100.1", 3.0, 1.1, 50, 0, 1.0)]
        p = by_key(team_page.compute_leaders(pitchers)["pitching"])
        for key in ("era", "whip", "so", "war"):
            self.assertEqual(p[key]["id"], 3, key)

    def test_ops_prefers_raw_field(self):
        raw = hitter(1, "Raw", 500, .300, .300, .300, 1, 1, 1.0)
        raw["hitting"]["ops"] = .901
        self.assertEqual(by_key(team_page.compute_leaders([raw])["hitting"])["ops"]["value"], .901)

    def test_ip_exactly_fifty_qualifies(self):
        p = by_key(team_page.compute_leaders([pitcher(10, "Fifty", "50.0", 3.0, 1.1, 40, 0, 0.5)])["pitching"])
        self.assertEqual(p["era"]["name"], "Fifty")

    def test_no_qualifier_gives_empty_entries(self):
        out = team_page.compute_leaders([hitter(1, "Rookie", 50, .300, .350, .400, 0, 0, 0.1),
                                         pitcher(10, "No Saves", "10.0", 3.0, 1.0, 10, 0, 0.0)])
        h, p = by_key(out["hitting"]), by_key(out["pitching"])
        for entry in (h["avg"], h["ops"], h["hr"], h["sb"], p["era"], p["whip"], p["sv"]):
            self.assertEqual((entry["id"], entry["name"], entry["value"]), (None, None, None), entry["key"])
        self.assertEqual(h["war"]["name"], "Rookie")      # WAR 無門檻
        self.assertEqual(p["so"]["name"], "No Saves")
        self.assertEqual(out["thresholds"], {"minPa": 300, "minIp": 50})

    def test_missing_values_and_empty_roster(self):
        broken = {"id": 1, "name": "No Stats", "hitting": None, "pitching": None}
        partial = hitter(2, "Partial", 400, None, None, None, None, None, None)
        out = team_page.compute_leaders([broken, partial, "bad"])
        self.assertTrue(all(e["value"] is None for e in out["hitting"] + out["pitching"]))
        self.assertEqual(len(team_page.compute_leaders([])["hitting"]), 5)
        self.assertEqual(len(team_page.compute_leaders(None)["pitching"]), 5)

    def test_two_way_player_counts_in_both_groups(self):
        twp = {"id": 660271, "name": "Two Way", "group": "POS",
               "hitting": {"pa": 600, "avg": .290, "obp": .390, "slg": .600, "hr": 50, "sb": 20, "war": 8.0},
               "pitching": {"ip": "100.0", "era": 2.50, "whip": 1.00, "so": 120, "sv": 0, "war": 3.0}}
        out = team_page.compute_leaders([twp])
        self.assertEqual(by_key(out["hitting"])["hr"]["name"], "Two Way")
        self.assertEqual(by_key(out["pitching"])["era"]["name"], "Two Way")


def series(sid, a, b, winner=None, needed=3):
    return {"id": sid, "label": "x", "teams": [{"id": a[0], "name": a[2] if len(a) > 2 else "?", "wins": a[1]},
                                               {"id": b[0], "name": b[2] if len(b) > 2 else "?", "wins": b[1]}],
            "winsNeeded": needed, "state": "已結束" if winner else "進行中", "winnerId": winner}


class PostseasonResultTests(unittest.TestCase):
    def data(self, **rounds):
        out = {"AL": {"F": [], "D": [], "L": []}, "NL": {"F": [], "D": [], "L": []}, "WS": []}
        for key, value in rounds.items():
            if key == "WS":
                out["WS"] = value
            else:
                out[key[:2]][key[2]] = value
        return {"rounds": out}

    def test_champion(self):
        d = self.data(WS=[series("W_1", (LAD, 4), (TB, 2), winner=LAD, needed=4)])
        r = team_page.postseason_result(d, LAD)
        self.assertEqual(r["outcome"], "champion")
        self.assertEqual(r["text"], "世界大賽 4–2 擊敗 " + fetch_mlb.config.TEAM_META[TB]["name"] + "，奪得冠軍")

    def test_runner_up_uses_highest_round(self):
        d = self.data(ALD=[series("D_1", (TB, 3), (NYY, 1), winner=TB)], ALL=[series("L_1", (TB, 4), (BOS, 2), winner=TB, needed=4)],
                      WS=[series("W_1", (LAD, 4), (TB, 2), winner=LAD, needed=4)])
        r = team_page.postseason_result(d, TB)
        self.assertEqual(r["outcome"], "eliminated")
        self.assertTrue(r["text"].startswith("世界大賽 2–4 敗給 "))

    def test_eliminated_in_division_series(self):
        d = self.data(ALD=[series("D_1", (TB, 3), (NYY, 1), winner=TB)])
        r = team_page.postseason_result(d, NYY)
        self.assertEqual(r, {"text": "分區系列賽 1–3 敗給 " + fetch_mlb.config.TEAM_META[TB]["name"], "outcome": "eliminated"})

    def test_in_progress_and_not_started(self):
        d = self.data(ALD=[series("D_1", (TB, 2), (NYY, 1))])
        self.assertEqual(team_page.postseason_result(d, TB)["outcome"], "active")
        self.assertIn("進行中，2–1 對", team_page.postseason_result(d, TB)["text"])
        d2 = self.data(ALD=[series("D_1", (TB, 0), (NYY, 0))])
        self.assertIn("尚未開打", team_page.postseason_result(d2, NYY)["text"])

    def test_missed_and_no_data(self):
        d = self.data(ALD=[series("D_1", (TB, 3), (NYY, 1), winner=TB)])
        self.assertEqual(team_page.postseason_result(d, CHC), {"text": "未晉級季後賽", "outcome": "missed"})
        self.assertIsNone(team_page.postseason_result(self.data(), CHC))
        self.assertIsNone(team_page.postseason_result(None, CHC))
        self.assertIsNone(team_page.postseason_result({"rounds": "bad"}, CHC))

    def test_with_real_fixture(self):
        raw = base.load_fixture("postseason_series_2026")
        data = fetch_mlb.playoffs.build_playoffs_data(2026, raw, date(2026, 11, 18))
        self.assertEqual(team_page.postseason_result(data, 158)["outcome"], "champion")      # 釀酒人
        self.assertEqual(team_page.postseason_result(data, 139)["outcome"], "eliminated")    # 光芒
        self.assertEqual(team_page.postseason_result(data, 108)["outcome"], "missed")        # 天使


class RecordAndTrackedTests(unittest.TestCase):
    def test_build_record(self):
        st = {"divisions": [{"teams": [{"id": NYY, "wins": 90, "losses": 72, "pct": ".556", "rank": 2, "run_diff": 55}]}]}
        self.assertEqual(team_page.build_record(st, NYY), {"w": 90, "l": 72, "pct": 0.556, "divRank": 2, "rd": 55})
        self.assertIsNone(team_page.build_record(st, BOS))
        self.assertIsNone(team_page.build_record(None, NYY))

    def test_tracked_names_match_roster_ids_in_watchlist_order(self):
        wl = {1: {"name_zh": "甲"}, 2: {"name_zh": "乙"}, 3: {"name_zh": "丙"}}
        players = [{"id": 3}, {"id": 1}, {"id": 99}]
        self.assertEqual(team_page.tracked_names(players, wl), ["甲", "丙"])
        self.assertEqual(team_page.tracked_names(players, {}), [])
        self.assertEqual(team_page.tracked_names(None, wl), [])


class StandingsRunDiffTests(unittest.TestCase):
    def test_run_diff_parsed_and_missing_is_none(self):
        import copy
        raw = copy.deepcopy(base.load_fixture("standings_2026"))
        first = raw["records"][0]["teamRecords"][0]
        first["runDifferential"] = -42
        raw["records"][0]["teamRecords"][1].pop("runDifferential", None)
        with mock.patch("mlb_daily.net.fetch_json", return_value=raw):
            st = fetch_mlb.fetch_standings(2026)
        teams = {t["id"]: t for d in st["divisions"] for t in d["teams"]}
        self.assertEqual(teams[first["team"]["id"]]["run_diff"], -42)
        self.assertIsNone(teams[raw["records"][0]["teamRecords"][1]["team"]["id"]]["run_diff"])


class MakeExtrasTests(base.MlbTestBase):
    """整合：make_extras 讀固定的每日快照目錄（以固定的「今天」計 30 天窗口），來源缺失時降級。"""

    def setUp(self):
        super().setUp()
        self.ctx = {"rosterType": "40Man", "season": 2026, "rosterSeason": 2026}

    def snap_dir(self):
        d = self.tmp / "data" / "daily"
        shutil.copytree(SNAP_DIR, d)
        return str(d)

    def snapshot_days(self, today):
        extras = team_page.make_extras(today, self.ctx, snapshot_dir=self.snap_dir_again(), wl={})
        return extras(WSH, [])["recent"]["snapshotDays"]

    def test_window_ends_at_report_day_and_ignores_later_files(self):
        shutil.copytree(SNAP_DIR, self.tmp / "data" / "daily")
        # 今天 11-19 -> 報表日 11-18 為終點；可用快照有 11-16、18、20、22（19 為壞檔），只有 11-16、11-18 在窗口內
        self.assertEqual(self.snapshot_days(date(2026, 11, 19)), 2)
        self.assertEqual(team_page.recent_window(date(2026, 11, 19))[1], date(2026, 11, 18))

    def test_window_covers_30_days_ending_yesterday_boundaries(self):
        shutil.copytree(SNAP_DIR, self.tmp / "data" / "daily")
        # 今天 12-16 -> 窗口 11-16..12-15（共 30 天）：最早一天 11-16 計入
        self.assertEqual(team_page.recent_window(date(2026, 12, 16)), (date(2026, 11, 16), date(2026, 12, 15)))
        self.assertEqual(self.snapshot_days(date(2026, 12, 16)), 4)
        # 今天 12-17 -> 窗口 11-17..12-16：再早一天的 11-16 排除
        self.assertEqual(self.snapshot_days(date(2026, 12, 17)), 3)

    def snap_dir_again(self):
        return str(self.tmp / "data" / "daily")

    def test_summary_fields_degrade_independently(self):
        extras = team_page.make_extras(date(2026, 11, 18), self.ctx, snapshot_dir=str(self.tmp / "nope"), wl={})
        with mock.patch("mlb_daily.team_page.build_record", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(io.StringIO()):
            out = extras(NYY, [{"id": 1}])
        self.assertIsNone(out["summary"]["record"])
        self.assertEqual(out["summary"]["tracked"], [])
        self.assertIn("postseason", out["summary"])

    def test_missing_snapshot_dir_gives_empty_recent(self):
        extras = team_page.make_extras(date(2026, 11, 18), self.ctx, snapshot_dir=str(self.tmp / "nope"), wl={})
        out = extras(NYY, [])
        self.assertEqual(out["recent"], {"moves": [], "news": [], "snapshotDays": 0})
        self.assertIn("leaders", out)

    def test_standings_failure_degrades_summary(self):
        with mock.patch("mlb_daily.standings.fetch_standings", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(io.StringIO()):
            extras = team_page.make_extras(date(2026, 11, 18), self.ctx, snapshot_dir=str(self.tmp / "nope"), wl={})
        out = extras(NYY, [])
        self.assertIsNone(out["summary"]["record"])
        self.assertIsNone(out["summary"]["postseason"])

    def test_postseason_only_when_season_matches(self):
        data = fetch_mlb.playoffs.build_playoffs_data(2026, base.load_fixture("postseason_series_2026"), date(2026, 11, 18))
        with mock.patch("mlb_daily.storage.load_playoffs", return_value=data):
            same = team_page.make_extras(date(2026, 11, 18), self.ctx, snapshot_dir=str(self.tmp / "nope"), wl={})(158, [])
            other = team_page.make_extras(date(2027, 5, 1), {**self.ctx, "season": 2027}, snapshot_dir=str(self.tmp / "nope"), wl={})(158, [])
        self.assertEqual(same["summary"]["postseason"]["outcome"], "champion")
        self.assertIsNone(other["summary"]["postseason"])

    def test_one_block_failure_does_not_drop_others(self):
        extras = team_page.make_extras(date(2026, 11, 18), self.ctx, snapshot_dir=str(self.tmp / "nope"), wl={})
        with mock.patch("mlb_daily.team_page.compute_leaders", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(io.StringIO()):
            out = extras(NYY, [])
        self.assertNotIn("leaders", out)
        self.assertIn("summary", out)
        self.assertIn("recent", out)


class RosterIntegrationTests(base.MlbTestBase):
    """--rosters 端到端：每隊 JSON 附 summary／recent／leaders；附加資料全失敗時名單照寫。"""

    def run_rosters(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--rosters", "--date", "2026-11-19"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code
        return None

    def read_team(self, tid):
        return json.loads((self.tmp / "data" / "teams" / f"{tid}.json").read_text(encoding="utf-8"))

    def test_team_json_has_extras(self):
        self.api.standings = "standings_2026"
        shutil.copytree(SNAP_DIR, self.tmp / "data" / "daily")
        self.assertEqual(self.run_rosters(), 0)
        team = self.read_team(WSH)
        self.assertEqual(set(team), {"meta", "players", "summary", "recent", "leaders"})
        self.assertEqual([(m["dir"], m["type"]) for m in team["recent"]["moves"] if m["type"] == "交易"], [("in", "交易")])
        yankees = self.read_team(NYY)
        self.assertEqual(yankees["summary"]["record"]["w"], 65)
        self.assertEqual(yankees["summary"]["record"]["divRank"], 5)
        self.assertEqual(len(yankees["leaders"]["hitting"]), 5)

    def test_extras_failure_still_writes_roster(self):
        with mock.patch("mlb_daily.team_page.make_extras", side_effect=RuntimeError("boom")):
            self.assertEqual(self.run_rosters(), 0)
        self.assertEqual(set(self.read_team(NYY)), {"meta", "players"})


if __name__ == "__main__":
    unittest.main()
