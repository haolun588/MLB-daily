"""季後賽對戰樹測試：只用 unittest 與 tests/fixtures/，不連網。
所有寫檔都在暫存目錄（MlbTestBase 會 chdir 進去），不污染 repo 的 data/、playoffs.html。"""

import ast
import contextlib
import copy
import io
import json
import re
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

fetch_mlb = base.fetch_mlb
playoffs = fetch_mlb.playoffs
DATE = base.DATE
TODAY = date(2026, 11, 18)

# 隊伍 ID
CWS, HOU, NYY, BOS, TB, CLE = 145, 117, 147, 111, 139, 114
ATL, PHI, SD, CHC, MIL, LAD = 144, 143, 135, 112, 158, 119


def quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*a, **kw)


def real_2026():
    return base.load_fixture("postseason_series_2026")


def in_progress_2026():
    """以程式由 2026 真實資料改出「進行中」：外卡全部打完，分區賽打到一半，冠軍賽與世界大賽尚無資料。
    D_1 光芒 2–0 洋基；D_2 白襪 2–1 守護者；D_3 釀酒人 2–0 教士；D_4 第 3 戰進行中（勇士 1–1 道奇）。"""
    raw = copy.deepcopy(real_2026())
    kept = {"D_1": 2, "D_2": 3, "D_3": 2, "D_4": 3}
    out = []
    for s in raw["series"]:
        sid = s["series"]["id"]
        if sid.startswith(("L_", "W_")):
            continue
        if sid in kept:
            s["games"] = s["games"][:kept[sid]]
        out.append(s)
    raw["series"] = out
    d4 = next(s for s in out if s["series"]["id"] == "D_4")
    live = d4["games"][2]
    live["status"] = {"abstractGameState": "Live", "codedGameState": "I", "detailedState": "In Progress"}
    for side in ("away", "home"):
        live["teams"][side].pop("isWinner", None)
        live["teams"][side].pop("score", None)
    return raw


def by_id(data, league, rnd):
    return {s["id"]: s for s in data["rounds"][league][rnd]}


def team_wins(series):
    return {t["id"]: t["wins"] for t in series["teams"]}


class PlayoffsTestBase(base.MlbTestBase):
    """在 MlbTestBase 的假 API 之上，加上季後賽系列賽端點（以賽季為 key）。"""

    def setUp(self):
        super().setUp()
        self.series = {2026: real_2026()}  # 賽季 -> 原始 dict 或 None（抓取失敗）；沒有 key 視為空回應
        self.calls = []
        p = mock.patch("mlb_daily.net.fetch_json", side_effect=self._fetch)
        p.start()
        self.addCleanup(p.stop)

    def _fetch(self, url, retries=3, delay=5):
        m = re.search(r"/schedule/postseason/series\?season=(\d+)&sportId=1", url)
        if m:
            season = int(m.group(1))
            self.calls.append(season)
            value = self.series.get(season, {"series": []})
            return copy.deepcopy(value)
        return self.api.fetch(url, retries, delay)

    def data(self, raw=None, season=2026):
        return playoffs.build_playoffs_data(season, raw if raw is not None else real_2026(), TODAY)

    def run_cli(self, *args):
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", *args]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code
        return 0

    def read_json(self, path="data/playoffs.json"):
        return json.loads((self.tmp / path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 解析
class ParsePostseasonTests(PlayoffsTestBase):
    def test_all_series_results_match_2026_actual(self):
        d = playoffs.parse_postseason(real_2026())
        expected = {
            ("AL", "F", "F_1"): {CWS: 2, HOU: 0}, ("AL", "F", "F_2"): {NYY: 2, BOS: 0},
            ("NL", "F", "F_3"): {ATL: 2, PHI: 1}, ("NL", "F", "F_4"): {SD: 2, CHC: 0},
            ("AL", "D", "D_1"): {TB: 3, NYY: 0}, ("AL", "D", "D_2"): {CLE: 3, CWS: 2},
            ("NL", "D", "D_3"): {MIL: 3, SD: 1}, ("NL", "D", "D_4"): {LAD: 3, ATL: 1},
            ("AL", "L", "L_1"): {TB: 4, CLE: 2}, ("NL", "L", "L_2"): {MIL: 4, LAD: 0},
        }
        for (league, rnd, sid), wins in expected.items():
            self.assertEqual(team_wins(by_id(d, league, rnd)[sid]), wins, sid)
            self.assertEqual(by_id(d, league, rnd)[sid]["winnerId"], max(wins, key=wins.get), sid)
            self.assertEqual(by_id(d, league, rnd)[sid]["state"], "已結束", sid)
        ws = d["rounds"]["WS"][0]
        self.assertEqual(team_wins(ws), {TB: 1, MIL: 4})
        self.assertEqual(ws["winsNeeded"], 4)

    def test_postponed_duplicate_game_is_not_double_counted(self):
        raw = real_2026()
        alcs = next(s for s in raw["series"] if s["series"]["id"] == "L_1")
        pks = [g["gamePk"] for g in alcs["games"]]
        self.assertEqual(pks.count(849817), 2)  # fixture 確實含延賽的重複那一筆
        postponed = [g for g in alcs["games"] if g["gamePk"] == 849817 and g["status"]["codedGameState"] == "D"]
        self.assertEqual(len(postponed), 1)
        self.assertEqual(postponed[0]["status"]["abstractGameState"], "Final")  # 只看 abstractGameState 會誤判
        alcl = by_id(playoffs.parse_postseason(raw), "AL", "L")["L_1"]
        self.assertEqual(team_wins(alcl), {CLE: 2, TB: 4})  # 4–2，不是 4–3

    def test_same_gamepk_final_twice_counts_once(self):
        raw = copy.deepcopy(real_2026())
        alcs = next(s for s in raw["series"] if s["series"]["id"] == "L_1")
        alcs["games"].append(copy.deepcopy(alcs["games"][0]))  # 完全相同的 Final 場次重複出現
        self.assertEqual(team_wins(by_id(playoffs.parse_postseason(raw), "AL", "L")["L_1"]), {CLE: 2, TB: 4})

    def test_game_over_state_counts_only_with_winner_flag(self):
        """codedGameState "O"（Game Over）：isWinner 已有布林值才計入；沒有則不計；"D" 延賽照樣排除。"""
        raw = copy.deepcopy(real_2026())
        alcs = next(s for s in raw["series"] if s["series"]["id"] == "L_1")
        finals = [g for g in alcs["games"] if g["status"]["codedGameState"] == "F"]
        a, b = finals[0], finals[1]
        a["status"]["codedGameState"] = "O"  # 仍有 isWinner：照算
        for side in b["teams"].values():
            side.pop("isWinner", None)
        b["status"]["codedGameState"] = "O"  # 沒有 isWinner：不算
        wins = team_wins(by_id(playoffs.parse_postseason(raw), "AL", "L")["L_1"])
        base_wins = team_wins(by_id(playoffs.parse_postseason(real_2026()), "AL", "L")["L_1"])
        self.assertEqual(sum(wins.values()), sum(base_wins.values()) - 1)
        # 延賽那一筆即使有 isWinner 也不算
        postponed = next(g for g in alcs["games"] if g["status"]["codedGameState"] == "D")
        postponed["teams"]["home"]["isWinner"] = True
        self.assertEqual(team_wins(by_id(playoffs.parse_postseason(raw), "AL", "L")["L_1"]), wins)

    def test_game_over_with_winner_is_counted_like_final(self):
        raw = copy.deepcopy(real_2026())
        for s in raw["series"]:
            for g in s["games"]:
                if g["status"]["codedGameState"] == "F":
                    g["status"]["codedGameState"] = "O"
        d = playoffs.parse_postseason(raw)
        self.assertEqual(team_wins(by_id(d, "AL", "L")["L_1"]), {CLE: 2, TB: 4})
        self.assertEqual(d["champion"]["id"], MIL)

    def test_unknown_team_id_is_not_bye_and_not_linked(self):
        """分區系列賽有一隊是 config 查不到的 id：不得被當成輪空隊，也不參與外卡與分區的對應。"""
        raw = copy.deepcopy(real_2026())
        d2 = next(s for s in raw["series"] if s["series"]["id"] == "D_2")  # 白襪（外卡勝）對守護者（輪空）
        for g in d2["games"]:
            for side in g["teams"].values():
                if side["team"]["id"] == CWS:
                    side["team"]["id"] = 99999
        d = playoffs.parse_postseason(raw)
        self.assertEqual(by_id(d, "AL", "D")["D_2"]["byeIds"], [CLE])  # 99999 不算輪空
        self.assertNotIn(99999, by_id(d, "AL", "D")["D_2"]["byeIds"])
        feeds = {s["id"]: s["feedsInto"] for s in d["rounds"]["AL"]["F"]}
        self.assertIsNone(feeds["F_1"])  # 白襪所在的外卡不再靠 99999 接到 D_2
        self.assertEqual(feeds["F_2"], "D_1")

    def test_unknown_team_in_both_wild_card_and_division_is_not_linked(self):
        raw = copy.deepcopy(real_2026())
        for s in raw["series"]:
            if s["series"]["id"] in ("F_1", "D_2"):
                for g in s["games"]:
                    for side in g["teams"].values():
                        if side["team"]["id"] == CWS:
                            side["team"]["id"] = 99999
        d = playoffs.parse_postseason(raw)
        self.assertIsNone({s["id"]: s["feedsInto"] for s in d["rounds"]["AL"]["F"]}["F_1"])
        self.assertEqual(by_id(d, "AL", "D")["D_2"]["byeIds"], [CLE])

    def test_wins_needed_from_games_in_series(self):
        d = playoffs.parse_postseason(real_2026())
        self.assertEqual({s["id"]: s["winsNeeded"] for lg in ("AL", "NL") for r in "FDL" for s in d["rounds"][lg][r]},
                         {"F_1": 2, "F_2": 2, "F_3": 2, "F_4": 2, "D_1": 3, "D_2": 3, "D_3": 3, "D_4": 3, "L_1": 4, "L_2": 4})

    def test_wild_card_feeds_division_by_team_id_not_letter(self):
        d = playoffs.parse_postseason(real_2026())
        feeds = {s["id"]: s["feedsInto"] for lg in ("AL", "NL") for s in d["rounds"][lg]["F"]}
        # 外卡 'A'（白襪）進分區 'B'（D_2）；外卡 'B'（洋基）進分區 'A'（D_1）；國聯同理
        self.assertEqual(feeds, {"F_1": "D_2", "F_2": "D_1", "F_3": "D_4", "F_4": "D_3"})
        labels = {s["id"]: s["label"] for lg in ("AL", "NL") for s in d["rounds"][lg]["F"] + d["rounds"][lg]["D"]}
        self.assertEqual(labels["F_1"], "美聯外卡 A")
        self.assertEqual(labels["D_2"], "美聯分區 B")
        self.assertEqual(labels["F_3"], "國聯外卡 A")
        self.assertEqual(labels["D_4"], "國聯分區 B")

    def test_bye_teams(self):
        d = playoffs.parse_postseason(real_2026())
        self.assertEqual({sid: s["byeIds"] for lg in ("AL", "NL") for sid, s in by_id(d, lg, "D").items()},
                         {"D_1": [TB], "D_2": [CLE], "D_3": [MIL], "D_4": [LAD]})

    def test_champion_and_status_complete(self):
        d = playoffs.parse_postseason(real_2026())
        self.assertEqual(d["status"], "完成")
        self.assertEqual(d["champion"], {"id": MIL, "name": "釀酒人"})

    def test_series_fields_and_chinese_names(self):
        d = playoffs.parse_postseason(real_2026())
        s = by_id(d, "AL", "L")["L_1"]
        self.assertEqual(set(s), {"id", "label", "teams", "winsNeeded", "state", "winnerId"})
        self.assertEqual(s["label"], "美聯冠軍賽")
        self.assertEqual(sorted(t["name"] for t in s["teams"]), sorted(["光芒", "守護者"]))
        self.assertEqual(d["rounds"]["WS"][0]["label"], "世界大賽")


class InProgressTests(PlayoffsTestBase):
    def test_in_progress_fixture_is_parsed(self):
        raw = in_progress_2026()
        self.assertTrue(playoffs.has_started(raw))
        d = playoffs.parse_postseason(raw)
        self.assertEqual(d["status"], "進行中")
        self.assertIsNone(d["champion"])
        self.assertEqual(d["rounds"]["WS"], [])
        self.assertEqual(d["rounds"]["AL"]["L"], [])
        self.assertEqual(d["rounds"]["NL"]["L"], [])
        div = {**by_id(d, "AL", "D"), **by_id(d, "NL", "D")}
        self.assertEqual(team_wins(div["D_1"]), {TB: 2, NYY: 0})
        self.assertEqual(team_wins(div["D_2"]), {CWS: 2, CLE: 1})
        self.assertEqual(team_wins(div["D_3"]), {MIL: 2, SD: 0})
        self.assertEqual(team_wins(div["D_4"]), {ATL: 1, LAD: 1})  # 第 3 戰進行中，不計勝場
        self.assertEqual([s["state"] for s in (div["D_1"], div["D_2"], div["D_3"], div["D_4"])], ["進行中"] * 4)
        self.assertTrue(all(s["winnerId"] is None for s in div.values()))
        self.assertEqual(div["D_1"]["winsNeeded"], 3)

    def test_live_game_without_wins_marks_series_in_progress(self):
        raw = in_progress_2026()
        d3 = next(s for s in raw["series"] if s["series"]["id"] == "D_3")
        d3["games"] = d3["games"][:0]
        d4 = next(s for s in raw["series"] if s["series"]["id"] == "D_4")
        d4["games"] = d4["games"][2:]  # 只剩進行中的第 3 戰
        d = playoffs.parse_postseason(raw)
        self.assertNotIn("D_3", by_id(d, "NL", "D"))  # 沒有任何場次的系列賽略過
        self.assertEqual(by_id(d, "NL", "D")["D_4"]["state"], "進行中")
        self.assertEqual(team_wins(by_id(d, "NL", "D")["D_4"]), {ATL: 0, LAD: 0})

    def test_not_started_series_state(self):
        raw = in_progress_2026()
        d4 = next(s for s in raw["series"] if s["series"]["id"] == "D_4")
        for g in d4["games"]:
            g["status"] = {"abstractGameState": "Preview", "codedGameState": "S", "detailedState": "Scheduled"}
            for side in g["teams"].values():
                side.pop("isWinner", None)
                side.pop("score", None)
        s = by_id(playoffs.parse_postseason(raw), "NL", "D")["D_4"]
        self.assertEqual(s["state"], "未開打")
        self.assertEqual(team_wins(s), {ATL: 0, LAD: 0})

    def test_page_title_and_card_in_progress(self):
        data = self.data(in_progress_2026())
        self.assertEqual(playoffs.playoffs_title(data), "2026 季後賽・截至 11/18")
        card = playoffs.render_playoffs_card(data)
        self.assertIn("分區系列賽", card)
        for line in ("光芒 2–0 洋基", "白襪 2–1 守護者", "釀酒人 2–0 教士", "勇士 1–1 道奇"):
            self.assertIn(f"<li>{line}</li>", card)
        self.assertNotIn("冠軍", card.replace("完整對戰樹", ""))
        page = playoffs.render_playoffs_page(data)
        self.assertIn("<title>MLB 每日戰報 - 季後賽 2026 季後賽・截至 11/18</title>", page)
        self.assertEqual(page.count("進行中</span>"), 8)  # 4 組分區 × 桌面樹與手機堆疊各一份
        self.assertIn("待定", page)  # 冠軍賽與世界大賽尚無對手
        self.assertNotIn("po-champion", page)

    def test_current_round_advances_with_results(self):
        d = self.data(real_2026())
        # 全部打完時小卡改顯示冠軍，不列各輪比數
        card = playoffs.render_playoffs_card(d)
        self.assertNotIn("po-mini\"", card)
        # 只有外卡開打：列外卡
        raw = in_progress_2026()
        raw["series"] = [s for s in raw["series"] if s["series"]["id"].startswith("F_")]
        card = playoffs.render_playoffs_card(self.data(raw))
        self.assertIn("外卡系列賽", card)
        self.assertIn('<li class="is-done">白襪 2–0 太空人</li>', card)


# ---------------------------------------------------------------- 抓取與選擇賽季
class GeneratePlayoffsTests(PlayoffsTestBase):
    def test_writes_json_and_page(self):
        self.assertTrue(quiet(playoffs.generate_playoffs, TODAY))
        self.assertEqual(self.calls, [2026])
        data = self.read_json()
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["season"], 2026)
        self.assertEqual(data["status"], "完成")
        self.assertEqual(data["champion"], {"id": MIL, "name": "釀酒人"})
        self.assertEqual(set(data["rounds"]), {"AL", "NL", "WS"})
        self.assertEqual(set(data["rounds"]["AL"]), {"F", "D", "L"})
        self.assertIn("updatedAt", data)
        page = (self.tmp / "playoffs.html").read_text(encoding="utf-8")
        self.assertIn("2026 季後賽結果", page)

    def test_this_year_without_games_falls_back_to_last_year(self):
        self.series = {2027: {"series": []}, 2026: real_2026()}
        self.assertTrue(quiet(playoffs.generate_playoffs, date(2027, 6, 1)))
        self.assertEqual(self.calls, [2027, 2026])
        self.assertEqual(self.read_json()["season"], 2026)
        self.assertIn("2026 季後賽結果", (self.tmp / "playoffs.html").read_text(encoding="utf-8"))

    def test_this_year_with_only_unplayed_games_falls_back(self):
        raw = copy.deepcopy(real_2026())
        for s in raw["series"]:
            for g in s["games"]:
                g["status"] = {"abstractGameState": "Preview", "codedGameState": "S", "detailedState": "Scheduled"}
        self.series = {2027: raw, 2026: real_2026()}
        self.assertTrue(quiet(playoffs.generate_playoffs, date(2027, 6, 1)))
        self.assertEqual(self.read_json()["season"], 2026)

    def test_this_year_in_progress_is_used_without_fetching_last_year(self):
        self.series = {2026: in_progress_2026()}
        self.assertTrue(quiet(playoffs.generate_playoffs, TODAY))
        self.assertEqual(self.calls, [2026])
        self.assertEqual(self.read_json()["status"], "進行中")
        self.assertIn("2026 季後賽・截至 11/18", (self.tmp / "playoffs.html").read_text(encoding="utf-8"))

    def test_fetch_failure_does_not_overwrite_existing_files(self):
        self.assertTrue(quiet(playoffs.generate_playoffs, TODAY))
        json_before = (self.tmp / "data" / "playoffs.json").read_bytes()
        page_before = (self.tmp / "playoffs.html").read_bytes()
        self.series = {2026: None}
        self.assertFalse(quiet(playoffs.generate_playoffs, TODAY))
        self.assertEqual((self.tmp / "data" / "playoffs.json").read_bytes(), json_before)
        self.assertEqual((self.tmp / "playoffs.html").read_bytes(), page_before)

    def test_fallback_year_fetch_failure_does_not_overwrite(self):
        self.assertTrue(quiet(playoffs.generate_playoffs, TODAY))
        before = (self.tmp / "data" / "playoffs.json").read_bytes()
        self.series = {2027: {"series": []}, 2026: None}
        self.assertFalse(quiet(playoffs.generate_playoffs, date(2027, 6, 1)))
        self.assertEqual((self.tmp / "data" / "playoffs.json").read_bytes(), before)

    def test_no_data_in_either_year_creates_nothing(self):
        self.series = {2027: {"series": []}, 2026: {"series": []}}
        self.assertFalse(quiet(playoffs.generate_playoffs, date(2027, 6, 1)))
        self.assertFalse((self.tmp / "data" / "playoffs.json").exists())
        self.assertFalse((self.tmp / "playoffs.html").exists())

    def test_malformed_response_is_a_failure(self):
        for bad in ({"oops": 1}, [], {"series": "x"}):
            self.series = {2026: bad}
            self.assertIsNone(playoffs.fetch_postseason(2026), bad)
            self.assertFalse(quiet(playoffs.generate_playoffs, TODAY), bad)
        self.assertIsNone(playoffs.fetch_postseason("abc"))


# ---------------------------------------------------------------- 渲染
class RenderPageTests(PlayoffsTestBase):
    def test_complete_title_champion_and_structure(self):
        page = playoffs.render_playoffs_page(self.data())
        self.assertIn("<title>MLB 每日戰報 - 季後賽 2026 季後賽結果</title>", page)
        self.assertIn("2026 季後賽結果", page)
        self.assertIn("po-champion", page)
        self.assertRegex(page, r'<p class="po-champ-n">釀酒人</p>')
        self.assertEqual(page.count('class="po-tree"'), 1)
        self.assertEqual(page.count('class="po-stack"'), 1)
        self.assertNotIn("<!--SLOT:", page)
        # 桌面樹：7 欄標題依序為美外卡、美分區、美冠軍賽、世界大賽、國冠軍賽、國分區、國外卡
        heads = re.findall(r'<h3 class="po-colhead"[^>]*>([^<]+)</h3>', page)
        self.assertEqual(heads, ["美聯外卡", "美聯分區", "美聯冠軍賽", "世界大賽", "國聯冠軍賽", "國聯分區", "國聯外卡"])
        # 每個系列賽兩份（桌面樹 + 手機堆疊）：4 外卡 + 4 分區 + 2 冠軍賽 + 1 世界大賽
        self.assertEqual(page.count('<article class="po-s'), 11 * 2)

    def test_tree_places_wild_card_in_row_of_the_division_it_feeds(self):
        tree = re.search(r'<div class="po-tree".*?\n    </div>', playoffs.render_playoffs_page(self.data()), re.S).group(0)
        cells = re.findall(r'<div class="po-cell ([^"]*)" style="grid-column:(\d+);grid-row:(\d+)(?: / span (\d+))?">(.*?)</article></div>', tree, re.S)
        place = {}
        for kind, col, row, span, html in cells:
            label = re.search(r'<span class="po-label">([^<]+)</span>', html).group(1)
            place[label] = (int(col), int(row), span)
        # 白襪（外卡 A）與守護者（分區 B）同列；洋基（外卡 B）與光芒（分區 A）同列
        self.assertEqual(place["美聯外卡 A"][1], place["美聯分區 B"][1])
        self.assertEqual(place["美聯外卡 B"][1], place["美聯分區 A"][1])
        self.assertEqual(place["國聯外卡 A"][1], place["國聯分區 B"][1])
        self.assertEqual(place["國聯外卡 B"][1], place["國聯分區 A"][1])
        self.assertNotEqual(place["美聯外卡 A"][1], place["美聯外卡 B"][1])
        self.assertEqual((place["美聯外卡 A"][0], place["美聯分區 A"][0], place["美聯冠軍賽"][0]), (1, 2, 3))
        self.assertEqual((place["世界大賽"][0], place["國聯冠軍賽"][0], place["國聯分區 A"][0], place["國聯外卡 A"][0]), (4, 5, 6, 7))
        self.assertEqual(place["世界大賽"][2], "2")  # 世界大賽與冠軍賽格跨兩列
        self.assertEqual(place["美聯冠軍賽"][2], "2")

    def test_stack_order_rounds_and_leagues(self):
        page = playoffs.render_playoffs_page(self.data())
        stack = page[page.index('class="po-stack"'):]
        titles = re.findall(r'<section class="po-round" aria-label="([^"]+)">', stack)
        self.assertEqual(titles, ["世界大賽", "聯盟冠軍賽", "分區系列賽", "外卡系列賽"])
        labels = re.findall(r'<span class="po-label">([^<]+)</span>', stack)
        self.assertEqual(labels, ["世界大賽", "美聯冠軍賽", "國聯冠軍賽",
                                  "美聯分區 A", "美聯分區 B", "國聯分區 A", "國聯分區 B",
                                  "美聯外卡 B", "美聯外卡 A", "國聯外卡 B", "國聯外卡 A"])  # 同輪美聯在前

    def test_winner_marked_loser_dimmed_and_bye_label(self):
        page = playoffs.render_playoffs_page(self.data())
        tree = page[page.index('class="po-tree"'):page.index('class="po-stack"')]
        self.assertEqual(tree.count('class="po-team is-win"'), 11)
        self.assertEqual(tree.count("po-team is-lose"), 11)
        self.assertEqual(tree.count(">晉級<"), 11)
        self.assertEqual(tree.count(">首輪輪空<"), 4)  # 光芒、守護者、釀酒人、道奇
        ds = re.search(r'<span class="po-label">美聯分區 A</span>.*?</article>', tree, re.S).group(0)
        self.assertRegex(ds, r'<span class="po-nm">光芒</span><span class="po-bye">首輪輪空</span>')

    def test_world_series_cell_uses_po_ws_not_wins_class(self):
        page = playoffs.render_playoffs_page(self.data())
        self.assertIn('class="po-cell po-ws"', page)
        self.assertNotRegex(page, r'class="po-cell[^"]*po-w')

    def test_tree_bye_shown_as_line_under_header(self):
        page = playoffs.render_playoffs_page(self.data())
        tree = page[page.index('class="po-tree"'):page.index('class="po-stack"')]
        self.assertEqual(tree.count('class="po-byeline"'), 4)
        self.assertIn('<p class="po-byeline">首輪輪空：光芒</p>', tree)

    def test_finished_series_have_no_in_progress_marker(self):
        page = playoffs.render_playoffs_page(self.data())
        self.assertNotIn('class="po-live"', page)

    def test_unknown_team_shows_pending_and_external_strings_are_escaped(self):
        data = self.data()
        data["rounds"]["WS"][0]["teams"][0] = {"id": None, "name": None, "wins": 0}
        data["rounds"]["AL"]["L"][0]["label"] = '<img src=x onerror="alert(1)">'
        data["rounds"]["AL"]["L"][0]["teams"][1]["name"] = "<b>X&Y</b>"
        data["champion"]["name"] = "<script>1</script>"
        page = playoffs.render_playoffs_page(data)
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<script>1", page)
        self.assertNotIn("<b>X&Y", page)
        self.assertIn("&lt;b&gt;X&amp;Y&lt;/b&gt;", page)
        self.assertIn("&lt;script&gt;1&lt;/script&gt;", page)
        self.assertIn('<li class="po-team is-tbd">', page)
        self.assertIn("待定", page)

    def test_team_without_chinese_mapping_shows_pending(self):
        raw = copy.deepcopy(real_2026())
        w = next(s for s in raw["series"] if s["series"]["id"] == "W_1")
        for g in w["games"]:
            for side in g["teams"].values():
                if side["team"]["id"] == TB:
                    side["team"]["id"] = 99999  # 對照表沒有的球隊
        d = playoffs.parse_postseason(raw)
        self.assertEqual([t["name"] for t in d["rounds"]["WS"][0]["teams"]], [None, "釀酒人"])
        page = playoffs.render_playoffs_page(playoffs.build_playoffs_data(2026, raw, TODAY))
        self.assertIn('<span class="po-nm">待定</span>', page)

    def test_unknown_team_id_row_is_tbd_without_wins_or_advance(self):
        data = self.data()
        data["rounds"]["WS"][0]["teams"][0] = {"id": 99999, "name": None, "wins": 4}
        row = playoffs._render_team_row(data["rounds"]["WS"][0]["teams"][0], 99999, playoffs.STATE_DONE, False)
        self.assertIn("is-tbd", row)
        self.assertNotIn("is-win", row)
        self.assertNotIn("is-lose", row)
        self.assertNotIn("晉級", row)
        self.assertIn('<span class="po-w">–</span>', row)
        self.assertIn("待定", row)

    def test_garbage_json_does_not_break_render(self):
        data = self.data()
        data["rounds"]["AL"]["F"] = ["x", None, {"teams": "bad", "state": "??", "winsNeeded": "z"}]
        data["rounds"]["NL"] = "oops"
        data["rounds"]["WS"] = None
        data["champion"] = "nope"
        page = playoffs.render_playoffs_page(data)
        self.assertIn('class="po-tree"', page)
        self.assertIn("待定", page)
        self.assertEqual(playoffs.render_playoffs_card({"rounds": {"AL": "x"}, "status": "進行中"}), "")

    def test_empty_rounds_still_render_placeholders(self):
        data = playoffs.build_playoffs_data(2026, {"series": []}, TODAY)
        page = playoffs.render_playoffs_page(data)
        self.assertEqual(page.count('<article class="po-s'), 11 * 2)
        self.assertEqual(playoffs.render_playoffs_card(data), "")

    def test_nav_links_and_relative_paths(self):
        page = playoffs.render_playoffs_page(self.data())
        m = re.search(r'<nav class="nav-row" aria-label="頁面導覽">(.*?)</nav>', page, re.S).group(1)
        links = re.findall(r'<a([^>]*?)href="([^"]+)"([^>]*)>([^<]*)</a>', m)
        self.assertEqual([(h, t) for _a, h, _b, t in links],
                         [("index.html", "← 首頁"), ("teams.html", "球隊專頁"), ("leaders.html", "數據排行"),
                          ("playoffs.html", "季後賽"), ("archive.html", "歷史存檔")])
        self.assertEqual([t for a, _h, b, t in links if "aria-current" in a + b], ["季後賽"])
        self.assertIn('href="assets/style.css"', page)


# ---------------------------------------------------------------- 首頁小卡
class HomeCardTests(PlayoffsTestBase):
    META = {"date": DATE, "total_games": 3, "postponed": 0, "summary": "測試摘要", "type": "season"}

    def index_html(self):
        return (self.tmp / "index.html").read_text(encoding="utf-8")

    def test_complete_card_shows_champion_and_link(self):
        card = playoffs.render_playoffs_card(self.data())
        self.assertIn("2026 世界大賽冠軍：<strong>釀酒人</strong>（4–1 勝光芒）", card)
        self.assertIn('<a class="po-mini-more" href="playoffs.html">完整對戰樹 →</a>', card)
        self.assertIn("2026 季後賽結果", card)
        self.assertNotIn("po-mini\"", card)

    def test_card_escapes_champion_name(self):
        data = self.data()
        data["champion"]["name"] = "<i>x</i>"
        self.assertIn("&lt;i&gt;x&lt;/i&gt;", playoffs.render_playoffs_card(data))

    def test_index_without_file_has_no_card(self):
        self.quiet_update_index(self.META)
        self.assertNotIn("po-card-mini", self.index_html())

    def test_index_shows_card_below_standings_when_season_matches(self):
        self.api.standings = "standings_2026"
        quiet(playoffs.generate_playoffs, TODAY)
        self.quiet_update_index(self.META)  # 時鐘固定 2026-11-18：賽季 2026
        html = self.index_html()
        aside = re.search(r'<aside class="standings".*?</aside>', html, re.S).group(0)
        self.assertIn('class="po-card-mini"', aside)
        self.assertGreater(aside.index("po-card-mini"), aside.rindex("</table>"))
        self.assertEqual(html.count("po-card-mini"), 1)
        self.assertIn('href="playoffs.html">季後賽</a>', html)

    def test_card_below_leaders_card(self):
        (self.tmp / "data").mkdir(exist_ok=True)
        leaders_data = {"schema": fetch_mlb.config.LEADERS_SCHEMA, "season": 2026, "in_season": False,
                        "as_of": "2026-11-17", "generated_at": "2026-11-18T12:00:00+08:00",
                        "categories": {"homeRuns": [{"rank": 1, "id": 1, "name": "A B", "team_id": 147, "value": "50"}]}}
        (self.tmp / "data" / "leaders.json").write_text(json.dumps(leaders_data), encoding="utf-8")
        quiet(playoffs.generate_playoffs, TODAY)
        self.quiet_update_index(self.META)
        aside = re.search(r'<aside class="standings.*?</aside>', self.index_html(), re.S).group(0)
        self.assertLess(aside.index("ld-card-mini"), aside.index("po-card-mini"))

    def test_card_hidden_when_data_is_previous_season(self):
        """例行賽期間檔案內是上一屆結果（賽季不符）：隱藏小卡，導覽列連結仍在。"""
        quiet(playoffs.generate_playoffs, TODAY)
        with mock.patch("mlb_daily.timeutil.get_taipei_now",
                        return_value=base.datetime(2027, 6, 1, 12, 17, tzinfo=base.timezone(base.timedelta(hours=8)))):
            self.quiet_update_index(self.META)
        html = self.index_html()
        self.assertNotIn("po-card-mini", html)
        self.assertIn('href="playoffs.html">季後賽</a>', html)

    def test_card_shown_before_opening_day_in_jan_to_mar(self):
        """1–3 月開季前：賽季判斷為 year-1、資料為 year-1，應顯示小卡。"""
        quiet(playoffs.generate_playoffs, TODAY)  # 資料為 2026 屆
        self.api.seasons_by_year = {2027: "seasons_2027"}
        with mock.patch("mlb_daily.timeutil.get_taipei_now",
                        return_value=base.datetime(2027, 2, 10, 12, 17, tzinfo=base.timezone(base.timedelta(hours=8)))):
            self.quiet_update_index(self.META)
        self.assertEqual(self.index_html().count("po-card-mini"), 1)

    def test_card_shown_in_october_when_postseason_started(self):
        """10 月例行賽期間季後賽已開打：rosterType 為 active、資料為今年，應顯示小卡。"""
        quiet(playoffs.generate_playoffs, TODAY)
        with mock.patch("mlb_daily.timeutil.get_taipei_now",
                        return_value=base.datetime(2026, 10, 10, 12, 17, tzinfo=base.timezone(base.timedelta(hours=8)))):
            self.quiet_update_index(self.META)
        self.assertEqual(self.index_html().count("po-card-mini"), 1)

    def test_fallback_roster_context_uses_previous_year_in_jan_to_mar(self):
        from mlb_daily import index_page
        self.assertEqual(index_page._fallback_roster_context(date(2027, 2, 10)), {"rosterType": "40Man", "season": 2026})
        self.assertEqual(index_page._fallback_roster_context(date(2027, 3, 31)), {"rosterType": "40Man", "season": 2026})
        self.assertEqual(index_page._fallback_roster_context(date(2027, 4, 1)), {"rosterType": "active", "season": 2027})
        self.assertEqual(index_page._fallback_roster_context(date(2026, 11, 18)), {"rosterType": "40Man", "season": 2026})

    def test_card_present_even_when_standings_failed(self):
        quiet(playoffs.generate_playoffs, TODAY)
        self.quiet_update_index(self.META)
        aside = re.search(r'<aside class="standings is-error".*?</aside>', self.index_html(), re.S).group(0)
        self.assertIn("po-card-mini", aside)

    def test_corrupt_or_wrong_schema_file_is_ignored(self):
        (self.tmp / "data").mkdir()
        good = json.loads(json.dumps(self.data()))
        bad_season = dict(good, season="2026")
        for content in ("{not json", json.dumps({"schema": 99, "rounds": {}}), json.dumps([1, 2]),
                        json.dumps(dict(good, rounds="x")), json.dumps(bad_season), json.dumps(dict(good, season=True))):
            (self.tmp / "data" / "playoffs.json").write_text(content, encoding="utf-8")
            self.assertIsNone(fetch_mlb.storage.load_playoffs(), content[:40])
            self.quiet_update_index(self.META)
            self.assertNotIn("po-card-mini", self.index_html())
        (self.tmp / "data" / "playoffs.json").write_text(json.dumps(good), encoding="utf-8")
        self.assertIsNotNone(fetch_mlb.storage.load_playoffs())

    def test_index_generation_does_not_hit_network_for_playoffs(self):
        quiet(playoffs.generate_playoffs, TODAY)
        self.calls.clear()
        self.quiet_update_index(self.META)
        self.assertEqual(self.calls, [])


# ---------------------------------------------------------------- CLI
class CliTests(PlayoffsTestBase):
    def test_cli_playoffs_writes_files_without_metadata_and_skips_index(self):
        index_before = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertEqual(self.run_cli("--playoffs"), 0)
        self.assertTrue((self.tmp / "playoffs.html").exists())
        self.assertEqual(self.read_json()["season"], 2026)
        self.assertEqual((self.tmp / "index.html").read_text(encoding="utf-8"), index_before)
        self.assertFalse((self.tmp / "reports" / "metadata.json").exists())

    def test_cli_as_of_is_yesterday_in_taipei(self):
        self.series = {2026: in_progress_2026()}
        self.assertEqual(self.run_cli("--playoffs"), 0)  # 時鐘固定 2026-11-18 → 截至 11/17
        self.assertEqual(self.read_json()["asOf"], "2026-11-17")
        self.assertIn("截至 11/17", (self.tmp / "playoffs.html").read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("--playoffs", "--date", "2026-10-20"), 0)
        self.assertEqual(self.read_json()["asOf"], "2026-10-19")

    def test_cli_refreshes_index_when_metadata_exists(self):
        (self.tmp / "reports" / "metadata.json").write_text(json.dumps({DATE: HomeCardTests.META}), encoding="utf-8")
        self.assertEqual(self.run_cli("--playoffs"), 0)
        self.assertIn("po-card-mini", (self.tmp / "index.html").read_text(encoding="utf-8"))
        self.assertEqual(self.discord.call_count, 0)

    def test_cli_index_refresh_failure_does_not_fail_playoffs(self):
        (self.tmp / "reports" / "metadata.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_cli("--playoffs"), 0)
        self.assertTrue((self.tmp / "playoffs.html").exists())

    def test_cli_fetch_failure_exits_nonzero_and_keeps_old_files(self):
        self.assertEqual(self.run_cli("--playoffs"), 0)
        before = (self.tmp / "data" / "playoffs.json").read_bytes()
        self.series = {2026: None}
        self.assertEqual(self.run_cli("--playoffs"), 1)
        self.assertEqual((self.tmp / "data" / "playoffs.json").read_bytes(), before)

    def test_cli_rejects_combination_with_other_modes(self):
        for extra in (["--rosters"], ["--check-wait"], ["--notify-only"], ["--no-notify"], ["--weekly"],
                      ["--notify-weekly"], ["--week", "2026-W47"], ["--force"], ["--leaders"]):
            self.assertEqual(self.run_cli("--playoffs", *extra), 1, extra)
        self.assertEqual(self.calls, [])

    def test_cli_leaders_rejects_playoffs_is_symmetric(self):
        self.assertEqual(self.run_cli("--leaders", "--playoffs"), 1)

    def test_cli_invalid_date_exits_nonzero(self):
        self.assertEqual(self.run_cli("--playoffs", "--date", "nope"), 1)


# ---------------------------------------------------------------- 靜態檢查
class StaticChecksTests(unittest.TestCase):
    def setUp(self):
        self.yml = (base.REPO_ROOT / ".github" / "workflows" / "daily_report.yml").read_text(encoding="utf-8")

    def test_workflow_restores_playoffs_json_without_failing(self):
        restore = self.yml[self.yml.index("- name: Restore history from gh-pages"):self.yml.index("- name: Update Stat Leaders")]
        self.assertIn("git show origin/gh-pages:data/playoffs.json > data/playoffs.json 2>/dev/null", restore)
        self.assertIn("rm -f data/playoffs.json", restore)
        self.assertIn("::warning::gh-pages 尚無 data/playoffs.json", restore)
        self.assertIn("mkdir -p data", restore)

    def test_workflow_playoffs_step_after_leaders_before_daily_report_and_non_blocking(self):
        y = self.yml
        self.assertGreater(y.index("- name: Update Postseason Bracket"), y.index("- name: Update Stat Leaders"))
        self.assertLess(y.index("- name: Update Postseason Bracket"), y.index("- name: Generate Daily Report"))
        step = y[y.index("- name: Update Postseason Bracket"):y.index("- name: Generate Daily Report")]
        self.assertIn("continue-on-error: true", step)
        self.assertIn("python fetch_mlb.py --playoffs", step)

    def test_workflow_does_not_exclude_playoffs_from_deploy(self):
        exclude = re.search(r"exclude_assets: '([^']*)'", self.yml).group(1)
        self.assertNotIn("playoffs", exclude)

    def test_every_page_nav_links_to_playoffs(self):
        expected = {
            "templates/index.html": 'href="playoffs.html">季後賽</a>',
            "templates/report.html": 'href="../../../playoffs.html">季後賽</a>',
            "templates/weekly.html": 'href="../../playoffs.html">季後賽</a>',
            "templates/leaders.html": 'href="playoffs.html">季後賽</a>',
            "templates/playoffs.html": 'href="playoffs.html" aria-current="page">季後賽</a>',
            "teams.html": 'href="playoffs.html">季後賽</a>',
            "archive.html": 'href="playoffs.html">季後賽</a>',
        }
        for rel, needle in expected.items():
            self.assertIn(needle, (base.REPO_ROOT / rel).read_text(encoding="utf-8"), rel)

    def test_root_generated_page_is_gitignored_with_anchored_rule(self):
        lines = (base.REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/playoffs.html", lines)
        self.assertNotIn("playoffs.html", lines)  # 不帶 / 會連 templates/ 內的模板一起忽略
        self.assertTrue((base.REPO_ROOT / "templates" / "playoffs.html").exists())

    def test_template_and_fixture_are_not_ignored_and_template_is_tracked_or_untracked_new(self):
        for rel in ("templates/playoffs.html", "tests/fixtures/postseason_series_2026.json", "mlb_daily/playoffs.py"):
            r = subprocess.run(["git", "check-ignore", "-q", rel], cwd=base.REPO_ROOT, capture_output=True)
            self.assertEqual(r.returncode, 1, f"{rel} 被 .gitignore 忽略")  # 1 = 未被忽略
        r = subprocess.run(["git", "check-ignore", "-q", "playoffs.html"], cwd=base.REPO_ROOT, capture_output=True)
        self.assertEqual(r.returncode, 0)  # 根目錄產出物則被忽略

    def test_playoffs_module_respects_layering(self):
        def imported(name):
            tree = ast.parse((base.REPO_ROOT / "mlb_daily" / name).read_text(encoding="utf-8"))
            out = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
                    out.update(a.name for a in node.names)
            return out
        self.assertEqual(imported("playoffs.py") & {"rosters", "standings", "games", "previews", "report", "weekly",
                                                    "index_page", "notify", "cli", "translate", "leaders"}, set())
        for lower in ("standings.py", "rosters.py", "leaders.py", "storage.py", "net.py"):
            self.assertNotIn("playoffs", imported(lower), lower)

    def test_css_has_bracket_rules_without_horizontal_scroll(self):
        css = (base.REPO_ROOT / "assets" / "style.css").read_text(encoding="utf-8")
        self.assertIn("repeat(7, minmax(0, 1fr))", css)
        self.assertIn("@media (min-width: 1024px)", css)
        block = css[css.index("季後賽對戰樹（playoffs.html"):]
        self.assertNotIn("overflow-x: auto", block)
        self.assertNotIn("overflow-x: scroll", block)

    def test_css_tree_hides_advance_mark_and_has_no_class_clash_with_wins(self):
        css = (base.REPO_ROOT / "assets" / "style.css").read_text(encoding="utf-8")
        block = css[css.index("季後賽對戰樹（playoffs.html"):]
        self.assertRegex(block, r"\.po-tree \.po-adv[^{]*\{[^}]*display:\s*none")
        self.assertRegex(block, r"\.po-tree \.po-nm\s*\{[^}]*white-space:\s*nowrap")
        self.assertNotRegex(block, r"\.po-cell\.po-w(?!s)")
        self.assertIn(".po-cell.po-ws", block)


if __name__ == "__main__":
    unittest.main()
