# -*- coding: utf-8 -*-
"""fetch_mlb.py 的單元測試（只用標準函式庫，完全不連網）。

- 所有 HTTP 一律由 FakeApi 以 tests/fixtures/*.json 回應。
- 所有檔案輸出都導到暫存目錄（chdir 進去，並複製 templates/ 與 index.html）。
- Discord 通知與 time.sleep 皆被 patch；DISCORD_WEBHOOK_URL / GEMINI_API_KEY 會被清掉。

標示 @unittest.expectedFailure 的測試是「已知 bug」：docstring 的編號（H1、M1...）
對應 docs/plans/20261117_審查修正計畫.md。對應模組修好後，移除裝飾器即可。
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import fetch_mlb  # noqa: E402

DATE = "2026-11-15"
WINNER_ROW = '<tr class="winner-row">'
CARD_OPEN = '<div class="game-card"'


def load_fixture(name):
    with open(FIXTURES / f"{name}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def fixture_exists(name):
    return (FIXTURES / f"{name}.json").exists()


class FakeApi:
    """依 URL 回傳 fixture 的假 fetch_json。schedule 為 None 代表抓取失敗。"""

    def __init__(self):
        self.schedule = None
        self.schedule_sequence = None  # 若設定，依呼叫次數依序回傳（None 代表該次失敗）；用完後重複最後一筆
        self.schedule_calls = 0
        self.transactions = "transactions_empty"
        self.fail_boxscore = set()
        self.calls = []

    def fetch(self, url, retries=3, delay=5):
        self.calls.append(url)
        if "/schedule" in url:
            name = self.schedule
            if self.schedule_sequence:
                idx = min(self.schedule_calls, len(self.schedule_sequence) - 1)
                name = self.schedule_sequence[idx]
            self.schedule_calls += 1
            return load_fixture(name) if name else None
        m = re.search(r"/game/(\d+)/(boxscore|content)", url)
        if m:
            pk, kind = int(m.group(1)), m.group(2)
            if kind == "boxscore" and pk in self.fail_boxscore:
                return None
            name = f"{kind}_{pk}"
            return load_fixture(name) if fixture_exists(name) else None
        if "/transactions" in url:
            return load_fixture(self.transactions) if self.transactions else None
        return None


class MlbTestBase(unittest.TestCase):
    def setUp(self):
        # 暫存工作目錄：複製 templates 與 index.html，reports 為空
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        shutil.copytree(REPO_ROOT / "templates", self.tmp / "templates")
        shutil.copy(REPO_ROOT / "index.html", self.tmp / "index.html")
        (self.tmp / "reports").mkdir()
        old_cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, old_cwd)

        # 環境變數：確保不會取到真實金鑰
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in ("DISCORD_WEBHOOK_URL", "GEMINI_API_KEY"):
            os.environ.pop(key, None)

        # 網路 / 睡眠 / Discord 一律攔截
        self.api = FakeApi()
        for p in (
            mock.patch.object(fetch_mlb, "fetch_json", side_effect=self.api.fetch),
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("測試不得連網")),
            mock.patch.object(fetch_mlb.time, "sleep"),
        ):
            p.start()
            self.addCleanup(p.stop)
        discord = mock.patch.object(fetch_mlb, "send_discord_notification")
        self.discord = discord.start()
        self.addCleanup(discord.stop)

    # ---- helpers ----
    def games_of(self, schedule_name):
        dates = load_fixture(schedule_name).get("dates", [])
        return dates[0]["games"] if dates else []

    def generate(self, schedule_name, transactions="transactions_empty", fail_boxscore=()):
        self.api.schedule = schedule_name
        self.api.transactions = transactions
        self.api.fail_boxscore = set(fail_boxscore)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            meta = fetch_mlb.generate_report(DATE, self.games_of(schedule_name))
        return meta, self.read_report()

    def read_report(self, date=DATE):
        y, m, _ = date.split("-")
        return (self.tmp / "reports" / y / m / f"{date}.html").read_text(encoding="utf-8")

    def run_main(self, schedule_name):
        self.api.schedule = schedule_name
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", DATE]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code
        return None

    def read_metadata(self):
        return json.loads((self.tmp / "reports" / "metadata.json").read_text(encoding="utf-8"))

    def quiet_update_index(self, meta):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            fetch_mlb.update_index_page(meta)


# ---------------------------------------------------------------- 純函式
class FormatCumulativeTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(fetch_mlb.format_cumulative(1, 9), "1(9)")

    def test_multiple(self):
        self.assertEqual(fetch_mlb.format_cumulative(2, 11), "2(10)(11)")

    def test_triple(self):
        self.assertEqual(fetch_mlb.format_cumulative(3, 5), "3(3)(4)(5)")

    @unittest.expectedFailure
    def test_L2_season_total_smaller_than_count(self):
        """L2: 賽季累計 < 單場次數時，不應輸出 (0) 或負數，如 2(0)(1)。"""
        out = fetch_mlb.format_cumulative(2, 1)
        self.assertNotIn("(0)", out)
        self.assertNotIn("(-", out)


class TeamInfoTests(unittest.TestCase):
    def test_known_team(self):
        self.assertEqual(fetch_mlb.get_team_info(147, "New York Yankees")["abbr"], "NYY")

    def test_unknown_team_fallback(self):
        info = fetch_mlb.get_team_info(9999, "Foo Bar")
        self.assertEqual(info["abbr"], "FOO")
        self.assertEqual(info["name"], "Foo Bar")


# ---------------------------------------------------------------- 賽程狀態
class CheckFinalizedTests(MlbTestBase):
    def check(self, schedule):
        self.api.schedule = schedule
        return fetch_mlb.check_all_games_finalized(DATE)

    def test_all_final(self):
        finalized, games = self.check("schedule_normal")
        self.assertTrue(finalized)
        self.assertEqual(len(games), 2)

    def test_live_game_not_finalized(self):
        finalized, games = self.check("schedule_live")
        self.assertFalse(finalized)
        self.assertEqual(len(games), 1)

    def test_postponed_counts_as_finalized(self):
        finalized, games = self.check("schedule_postponed")
        self.assertTrue(finalized)
        self.assertEqual(len(games), 1)

    def test_empty_day(self):
        self.assertEqual(self.check("schedule_empty"), (True, []))


# ---------------------------------------------------------------- 新聞 / 人事異動
class NewsTests(MlbTestBase):
    def test_news_from_content(self):
        news = fetch_mlb.fetch_game_news(1001, "NYY", "BOS", 5, 3)
        self.assertEqual(news["headline"], "Yankees top Red Sox 5-3")

    def test_news_fallback_when_no_content(self):
        news = fetch_mlb.fetch_game_news(1002, "LAD", "SF", 2, 4)
        self.assertEqual(news["headline"], "LAD @ SF 賽事總結")
        self.assertIn("LAD 2 - SF 4", news["blurb"])


class TransactionTests(MlbTestBase):
    def setUp(self):
        super().setUp()
        self.api.transactions = "transactions_default"
        self.txs = fetch_mlb.fetch_daily_transactions(DATE)
        self.descs = [t["description"] for t in self.txs]

    def test_minor_league_excluded(self):
        self.assertFalse(any("Minor Guy" in d for d in self.descs))

    def test_plain_signed_excluded(self):
        self.assertFalse(any("Plain Signing" in d for d in self.descs))

    def test_signed_with_major_indicator_kept(self):
        self.assertTrue(any("Big Name" in d for d in self.descs))  # free agent
        self.assertTrue(any("Long Term" in d for d in self.descs))  # extension / -year

    def test_duplicates_removed(self):
        self.assertEqual(sum("Big Name" in d for d in self.descs), 1)
        self.assertEqual(len(self.txs), 4)

    def test_player_savant_link(self):
        jose = next(d for d in self.descs if "Jose Test" in d)
        self.assertIn('href="https://baseballsavant.mlb.com/savant-player/777001"', jose)
        self.assertIn('class="player-link">Jose Test</a>', jose)


# ---------------------------------------------------------------- 戰報產生
class GenerateReportTests(MlbTestBase):
    def test_normal_day_meta_and_cards(self):
        meta, html = self.generate("schedule_normal")
        self.assertEqual(meta["date"], DATE)
        self.assertEqual(meta["total_games"], 2)
        self.assertEqual(meta["postponed"], 0)
        self.assertEqual(html.count(CARD_OPEN), 2)
        self.assertIn("昨日共進行了 2 場賽事", meta["summary"])

    def test_normal_day_scores_and_records(self):
        _, html = self.generate("schedule_normal")
        self.assertIn('<td class="num-cell runs-cell">5</td>', html)
        self.assertIn('<td class="num-cell runs-cell">3</td>', html)
        self.assertIn("紐約洋基 (90-72)", html)
        self.assertEqual(html.count('<span class="status-badge final">Final</span>'), 2)
        self.assertEqual(html.count(WINNER_ROW), 2)

    def test_report_file_location_and_date_replaced(self):
        _, html = self.generate("schedule_normal")
        self.assertTrue((self.tmp / "reports" / "2026" / "11" / f"{DATE}.html").exists())
        self.assertIn("2026 年 11 月 15 日", html)
        self.assertNotIn("2026 年 07 月 19 日", html)

    def test_summary_panel_counts(self):
        _, html = self.generate("schedule_normal")
        self.assertIn('<span class="summary-value highlight-blue">2</span>', html)
        self.assertIn('<span class="summary-value highlight-red">0</span>', html)

    def test_decisions_and_highlights(self):
        _, html = self.generate("schedule_normal")
        # 勝投（away starter, id 10012）有 Savant 連結與賽季戰績 10-5
        self.assertIn('savant-player/10012" target="_blank" class="player-link">Away Starter 1001</a> (10-5)', html)
        # 打者焦點：HR 1(9)、SB 1(15) 與 Savant 連結
        self.assertIn("HR: 1(9)", html)
        self.assertIn("SB: 1(15)", html)
        self.assertIn('savant-player/10011" target="_blank" class="player-link">Away Batter 1001</a>', html)

    def test_news_and_fallback_in_cards(self):
        _, html = self.generate("schedule_normal")
        self.assertIn("Yankees top Red Sox 5-3", html)
        self.assertIn("LAD @ SF 賽事總結", html)  # 1002 沒有 content

    def test_extra_innings_marked(self):
        _, html = self.generate("schedule_extra")
        self.assertIn('<span class="status-badge extra">Final/11</span>', html)

    def test_postponed_card(self):
        meta, html = self.generate("schedule_postponed")
        self.assertEqual(meta["total_games"], 1)
        self.assertEqual(meta["postponed"], 1)
        self.assertIn("因雨延期", html)
        self.assertIn('<span class="status-badge postponed">Postponed</span>', html)
        self.assertFalse(any("/boxscore" in u for u in self.api.calls))
        self.assertIn("其中有 1 場賽事延期", meta["summary"])

    def test_transactions_routed_to_game_card_and_other_section(self):
        _, html = self.generate("schedule_normal", transactions="transactions_default")
        card_start = html.index("<!-- Game Card: NYY @ BOS -->")
        card_end = html.index("<!-- Game Card: LAD @ SF -->")
        self.assertIn("Jose Test", html[card_start:card_end])
        # 其餘 3 筆（Braves、Nowhere Man、Mariners）進入「其他球隊人事異動」
        self.assertIn('<span class="tx-badge">3</span>', html)
        other = html[html.index('<section class="transactions-section">'):]
        self.assertNotIn("Jose Test", other)
        self.assertIn("Big Name", other)

    def test_no_transactions_message(self):
        _, html = self.generate("schedule_normal", transactions="transactions_empty")
        self.assertIn("本日無其他球隊人事異動", html)


# ---------------------------------------------------------------- metadata / 首頁
class IndexAndMetadataTests(MlbTestBase):
    def make_meta(self, date, total=3):
        return {"date": date, "total_games": total, "postponed": 1, "summary": f"摘要 {date}"}

    def test_metadata_written_format(self):
        self.quiet_update_index(self.make_meta(DATE))
        text = (self.tmp / "reports" / "metadata.json").read_text(encoding="utf-8")
        self.assertTrue(text.startswith('{\n  "2026-11-15": {\n    "date": "2026-11-15",'))
        self.assertIn("摘要 2026-11-15", text)  # ensure_ascii=False
        self.assertEqual(json.loads(text)[DATE]["total_games"], 3)

    def test_metadata_merges_existing(self):
        self.quiet_update_index(self.make_meta("2026-11-14"))
        self.quiet_update_index(self.make_meta(DATE))
        self.assertEqual(sorted(self.read_metadata()), ["2026-11-14", DATE])

    def test_index_keeps_latest_six_only(self):
        for day in range(1, 9):  # 8 筆
            self.quiet_update_index(self.make_meta(f"2026-11-{day:02d}"))
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        start = index.index("<!-- REPORTS_LIST_START -->")
        end = index.index("<!-- REPORTS_LIST_END -->")
        archive = index[start:end]
        self.assertEqual(archive.count('class="history-card"'), 6)
        self.assertIn("2026-11-08", archive)
        self.assertIn("2026-11-03", archive)
        self.assertNotIn("2026-11-02", archive)
        self.assertNotIn("2026-11-01", archive)
        # metadata 仍保留全部 8 筆
        self.assertEqual(len(self.read_metadata()), 8)

    def test_latest_card_shows_newest(self):
        self.quiet_update_index(self.make_meta("2026-11-10"))
        self.quiet_update_index(self.make_meta("2026-11-12", total=7))
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        latest = index[index.index("<!-- LATEST_REPORT_START -->"):index.index("<!-- /LATEST_REPORT_END -->")]
        self.assertIn("reports/2026/11/2026-11-12.html", latest)
        self.assertIn("2026 年 11 月 12 日", latest)
        self.assertIn('<span class="stat-value blue">7</span>', latest)


# ---------------------------------------------------------------- main()
class MainFlowTests(MlbTestBase):
    def test_normal_day_end_to_end(self):
        self.api.transactions = "transactions_default"
        code = self.run_main("schedule_normal")
        self.assertIsNone(code)  # 正常結束，未呼叫 sys.exit
        self.assertTrue((self.tmp / "reports" / "2026" / "11" / f"{DATE}.html").exists())
        self.assertEqual(self.read_metadata()[DATE]["total_games"], 2)
        self.discord.assert_called_once()
        self.assertEqual(self.discord.call_args[0][0]["total_games"], 2)

    def test_empty_day_characterization(self):
        code = self.run_main("schedule_empty")
        self.assertEqual(code, 0)
        meta = self.read_metadata()[DATE]
        self.assertEqual(meta["total_games"], 0)
        self.assertEqual(meta["postponed"], 0)
        self.assertIn("今日無賽事", self.read_report())
        self.discord.assert_called_once()

    def test_invalid_date_exits_nonzero(self):
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", "2026/11/15"]), \
                contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                fetch_mlb.main()
        self.assertEqual(cm.exception.code, 1)


# ---------------------------------------------------------------- 抓取失敗處理（模組 A：H1 / M3 / M5）
class FailureHandlingTests(MlbTestBase):
    def run_main_args(self, *extra):
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", DATE, *extra]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code
        return None

    def html_files(self):
        return list((self.tmp / "reports").rglob("*.html"))

    def test_schedule_failure_without_check_wait_writes_nothing(self):
        meta_path = self.tmp / "reports" / "metadata.json"
        original_meta = json.dumps({"2026-11-14": {"date": "2026-11-14", "total_games": 3,
                                                   "postponed": 0, "summary": "舊"}}, ensure_ascii=False)
        meta_path.write_text(original_meta, encoding="utf-8")
        index_before = (self.tmp / "index.html").read_bytes()
        self.api.schedule = None
        code = self.run_main_args()
        self.assertEqual(code, 1)
        self.assertEqual(meta_path.read_text(encoding="utf-8"), original_meta)
        self.assertEqual((self.tmp / "index.html").read_bytes(), index_before)
        self.assertEqual(self.html_files(), [])
        self.discord.assert_not_called()

    def test_malformed_schedule_response_is_api_failure(self):
        with mock.patch.object(fetch_mlb, "fetch_json", return_value={"unexpected": True}):
            with self.assertRaises(fetch_mlb.ApiError):
                fetch_mlb.check_all_games_finalized(DATE)

    def test_schedule_failure_with_check_wait_retries_after_sleep(self):
        self.api.schedule_sequence = [None, "schedule_normal"]
        code = self.run_main_args("--check-wait")
        self.assertIsNone(code)
        fetch_mlb.time.sleep.assert_called_once_with(1800)
        self.assertTrue((self.tmp / "reports" / "2026" / "11" / f"{DATE}.html").exists())
        self.assertEqual(self.read_metadata()[DATE]["total_games"], 2)
        self.discord.assert_called_once()

    def test_zero_games_success_is_distinct_from_failure(self):
        self.api.schedule = "schedule_empty"
        self.assertEqual(fetch_mlb.check_all_games_finalized(DATE), (True, []))
        code = self.run_main_args()
        self.assertEqual(code, 0)
        self.assertEqual(self.read_metadata()[DATE]["total_games"], 0)

    def test_boxscore_failure_renders_fallback_card(self):
        meta, html = self.generate("schedule_normal", fail_boxscore={1002})
        self.assertEqual(meta["total_games"], 2)
        self.assertEqual(html.count(CARD_OPEN), 2)
        self.assertEqual(html.count("詳細數據暫缺"), 1)
        cards = html.split(CARD_OPEN)[1:]
        failed = [c for c in cards if "詳細數據暫缺" in c][0]
        normal = [c for c in cards if "詳細數據暫缺" not in c][0]
        self.assertIn("data-teams=", failed)
        self.assertIn("1002", failed)
        scores = {g["gamePk"]: g for g in self.games_of("schedule_normal")}[1002]["teams"]
        for side in ("away", "home"):
            runs = scores[side].get("score", None)
            if runs is None:
                runs = {g["gamePk"]: g for g in self.games_of("schedule_normal")}[1002]["linescore"]["teams"][side]["runs"]
            self.assertIn(f'<td class="num-cell runs-cell">{runs}</td>', failed)
        self.assertNotIn("performer-stats", failed)
        self.assertNotIn("決定", failed.replace("詳細數據暫缺", ""))
        # 其他場維持完整卡片
        self.assertIn("1001", normal)
        self.assertIn("單場焦點球員", normal)

    def test_corrupt_metadata_aborts_without_overwrite(self):
        meta_path = self.tmp / "reports" / "metadata.json"
        meta_path.write_text("{bad json", encoding="utf-8")
        index_before = (self.tmp / "index.html").read_bytes()
        code = self.run_main("schedule_normal")
        self.assertEqual(code, 1)
        self.assertEqual(meta_path.read_text(encoding="utf-8"), "{bad json")
        self.assertEqual((self.tmp / "index.html").read_bytes(), index_before)
        self.discord.assert_not_called()

    def test_missing_metadata_is_created(self):
        meta_path = self.tmp / "reports" / "metadata.json"
        self.assertFalse(meta_path.exists())
        code = self.run_main("schedule_normal")
        self.assertIsNone(code)
        self.assertEqual(self.read_metadata()[DATE]["total_games"], 2)
        self.assertFalse((self.tmp / "reports" / "metadata.json.tmp").exists())

    def test_metadata_write_failure_exits_nonzero(self):
        with mock.patch.object(fetch_mlb.os, "replace", side_effect=OSError("disk full")):
            code = self.run_main("schedule_normal")
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp / "reports" / "metadata.json").exists())
        self.assertFalse((self.tmp / "reports" / "metadata.json.tmp").exists())
        self.discord.assert_not_called()


# ---------------------------------------------------------------- 已知 bug（修好後移除 expectedFailure）
class KnownBugTests(MlbTestBase):
    def test_H1_api_failure_must_not_overwrite_metadata(self):
        """H1: schedule 抓取失敗（fetch_json 回 None）時，應以非零碼結束，且不得改寫 metadata、不得通知。"""
        original = {DATE: {"date": DATE, "total_games": 5, "postponed": 0, "summary": "原本的摘要"}}
        (self.tmp / "reports" / "metadata.json").write_text(
            json.dumps(original, ensure_ascii=False), encoding="utf-8")
        code = self.run_main(None)  # schedule=None -> fetch_json 回 None
        self.assertNotIn(code, (None, 0))
        self.assertEqual(self.read_metadata(), original)
        self.discord.assert_not_called()

    @unittest.expectedFailure
    def test_M1_news_must_be_escaped(self):
        """M1: 新聞 headline/blurb 內的 HTML 必須 escape。"""
        _, html = self.generate("schedule_xss", transactions="transactions_xss")
        self.assertNotIn("<script>alert('XSS_MARKER')</script>", html)
        self.assertNotIn("<img src=x onerror", html)
        self.assertIn("&amp; more", html)

    @unittest.expectedFailure
    def test_M1_player_name_must_be_escaped(self):
        """M1: 球員名（打者焦點）內的 HTML 必須 escape。"""
        _, html = self.generate("schedule_xss", transactions="transactions_empty")
        self.assertNotIn("Evil <script>", html)
        self.assertIn("Evil &lt;script&gt;", html)

    @unittest.expectedFailure
    def test_M1_transaction_description_must_be_escaped(self):
        """M1: 交易描述與其中的球員名必須 escape。"""
        _, html = self.generate("schedule_normal", transactions="transactions_xss")
        self.assertNotIn("<b>Durham</b>", html)
        self.assertNotIn("<script>alert('XSS_MARKER')</script>", html)

    @unittest.expectedFailure
    def test_M2_empty_day_placeholders_replaced(self):
        """M2: 無賽事日的副標題日期（「月」字樣）與 OVERALL_SUMMARY 應被替換。"""
        self.run_main("schedule_empty")
        html = self.read_report()
        self.assertIn("2026 年 11 月 15 日", html)
        self.assertNotIn("2026 年 07 月 19 日", html)
        self.assertIn('<span class="summary-value highlight-blue">0</span>', html)

    def test_M3_boxscore_failure_keeps_card_count_equal_total(self):
        """M3: boxscore 失敗的場次仍應有卡片，卡片數等於 total_games。"""
        meta, html = self.generate("schedule_normal", fail_boxscore={1002})
        self.assertEqual(meta["total_games"], 2)
        self.assertEqual(html.count(CARD_OPEN), meta["total_games"])

    @unittest.expectedFailure
    def test_M4_seven_inning_doubleheader_not_marked_extra(self):
        """M4: 7 局雙重賽不應標成 Final/7 與 extra 樣式。"""
        _, html = self.generate("schedule_doubleheader7")
        self.assertNotIn("Final/7", html)
        self.assertNotIn('<span class="status-badge extra">', html)

    @unittest.expectedFailure
    def test_M4_suspended_not_marked_final(self):
        """M4: Suspended 的場次不應被標成完賽（Final/5）。"""
        _, html = self.generate("schedule_suspended")
        self.assertNotIn("Final/5", html)
        self.assertNotIn('<span class="status-badge final">', html)

    @unittest.expectedFailure
    def test_M4_tie_has_no_winner(self):
        """M4: 比分相同（2:2）時，兩隊都不應標示勝方。"""
        _, html = self.generate("schedule_suspended")
        self.assertEqual(html.count(WINNER_ROW), 0)


if __name__ == "__main__":
    unittest.main()
