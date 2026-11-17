# -*- coding: utf-8 -*-
"""fetch_mlb.py 的單元測試（只用標準函式庫，完全不連網）。

- 所有 HTTP 一律由 FakeApi 以 tests/fixtures/*.json 回應。
- 所有檔案輸出都導到暫存目錄（chdir 進去，並複製 templates/ 與 index.html）。
- Discord 通知與 time.sleep 皆被 patch；DISCORD_WEBHOOK_URL / GEMINI_API_KEY 會被清掉。

docstring 的編號（H1、M1...）對應 docs/plans/20261117_審查修正計畫.md。
目前沒有任何 @unittest.expectedFailure（已知 bug 皆已修正）。
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
WINNER_ROW = '<tr class="is-win">'
CARD_OPEN = '<article class="game"'


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
        self.boxscore_overrides = {}  # gamePk -> 自訂 boxscore dict（覆蓋 fixture）
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
            if kind == "boxscore" and pk in self.boxscore_overrides:
                return self.boxscore_overrides[pk]
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

    def test_L2_season_total_smaller_than_count(self):
        """L2: 賽季累計 < 單場次數時，不應輸出 (0) 或負數，如 2(0)(1)。"""
        out = fetch_mlb.format_cumulative(2, 1)
        self.assertNotIn("(0)", out)
        self.assertNotIn("(-", out)
        self.assertEqual(out, "2")
        self.assertEqual(fetch_mlb.format_cumulative(1, 0), "1")
        self.assertEqual(fetch_mlb.format_cumulative(1, None), "1")


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
        tx = next(t for t in self.txs if "Jose Test" in t["description"])
        jose = fetch_mlb.render_tx_description(tx["description"], tx["person_id"], tx["person_name"])
        self.assertIn('href="https://baseballsavant.mlb.com/savant-player/777001"', jose)
        self.assertIn('class="plink"', jose)
        self.assertIn('rel="noopener">Jose Test</a>', jose)

    def test_description_is_raw_text_until_rendered(self):
        """fetch 階段保留 API 原文，escape 與連結一律在渲染階段處理。"""
        self.assertFalse(any("<a " in d for d in self.descs))

    def test_render_tx_description_escapes_then_links(self):
        out = fetch_mlb.render_tx_description('Signed <b>Bob</b> & "Al" Smith', 55, '"Al" Smith')
        self.assertNotIn("<b>", out)
        self.assertIn("&lt;b&gt;Bob&lt;/b&gt; &amp; ", out)
        self.assertIn('savant-player/55"', out)
        self.assertIn("&quot;Al&quot; Smith</a>", out)

    def test_render_tx_description_without_person(self):
        self.assertEqual(fetch_mlb.render_tx_description("A & B"), "A &amp; B")


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
        self.assertIn('<td class="n runs">5</td>', html)
        self.assertIn('<td class="n runs">3</td>', html)
        self.assertIn('<span class="name">紐約洋基</span><span class="record">(90-72)</span>', html)
        self.assertEqual(html.count('<span class="badge is-final">Final</span>'), 2)
        self.assertEqual(html.count(WINNER_ROW), 2)

    def test_report_file_location_and_date_replaced(self):
        _, html = self.generate("schedule_normal")
        self.assertTrue((self.tmp / "reports" / "2026" / "11" / f"{DATE}.html").exists())
        self.assertIn("2026 年 11 月 15 日（日）賽事結果", html)
        self.assertNotIn("2026 年 07 月 19 日", html)
        self.assertNotIn("<!--SLOT:", html)

    def test_summary_panel_counts(self):
        _, html = self.generate("schedule_normal")
        self.assertIn("<div><dt>總場數</dt><dd>2</dd></div>", html)
        self.assertIn("<div><dt>延賽</dt><dd>0</dd></div>", html)

    def test_decisions_and_highlights(self):
        _, html = self.generate("schedule_normal")
        # 勝投（away starter, id 10012）有 Savant 連結與賽季戰績 10-5
        self.assertIn('savant-player/10012" target="_blank" rel="noopener">Away Starter 1001</a> <span class="rec">(10-5)</span>', html)
        # 打者焦點：HR 1(9)、SB 1(15) 與 Savant 連結
        self.assertIn("HR: 1(9)", html)
        self.assertIn("SB: 1(15)", html)
        self.assertIn('savant-player/10011" target="_blank" rel="noopener">Away Batter 1001</a>', html)

    def test_news_and_fallback_in_cards(self):
        _, html = self.generate("schedule_normal")
        self.assertIn("Yankees top Red Sox 5-3", html)
        self.assertIn("LAD @ SF 賽事總結", html)  # 1002 沒有 content

    def test_extra_innings_marked(self):
        _, html = self.generate("schedule_extra")
        self.assertIn('<span class="badge is-extra">Final/11</span>', html)

    def test_postponed_card(self):
        meta, html = self.generate("schedule_postponed")
        self.assertEqual(meta["total_games"], 1)
        self.assertEqual(meta["postponed"], 1)
        self.assertIn("因雨延期", html)
        self.assertIn('<span class="badge is-postponed">Postponed</span>', html)
        self.assertFalse(any("/boxscore" in u for u in self.api.calls))
        self.assertIn("其中有 1 場賽事延期", meta["summary"])

    def test_transactions_routed_to_game_card_and_other_section(self):
        _, html = self.generate("schedule_normal", transactions="transactions_default")
        cards = html.split(CARD_OPEN)[1:]
        nyy_card = next(c for c in cards if 'data-teams="NYY|' in c)
        lad_card = next(c for c in cards if 'data-teams="LAD|' in c)
        self.assertIn("Jose Test", nyy_card)
        self.assertIn("本場相關人事異動", nyy_card)
        self.assertNotIn("Jose Test", lad_card)
        # 其餘 3 筆（Braves、Nowhere Man、Mariners）進入「其他球隊人事異動」
        other = html[html.index('aria-labelledby="tx-h"'):]
        self.assertIn('<span class="count">3 筆</span>', other)
        self.assertNotIn("Jose Test", other)
        self.assertIn("Big Name", other)

    def test_no_transactions_message(self):
        _, html = self.generate("schedule_normal", transactions="transactions_empty")
        self.assertIn("本日無其他球隊人事異動", html)

    def test_meta_new_fields_season(self):
        meta, _ = self.generate("schedule_normal", transactions="transactions_default")
        self.assertEqual(meta["type"], "season")
        self.assertNotIn("news_count", meta)  # 新聞功能（階段 2）接上前不寫入
        # 4 筆（卡片內 1 + 其他 3）：transactions_count 為該頁顯示的全部異動筆數
        self.assertEqual(meta["transactions_count"], 4)

    def test_meta_new_fields_postponed_only_day_is_still_season(self):
        meta, _ = self.generate("schedule_postponed")
        self.assertEqual(meta["type"], "season")
        self.assertEqual(meta["transactions_count"], 0)


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

    def read_index(self):
        return (self.tmp / "index.html").read_text(encoding="utf-8")

    def test_index_is_regenerated_from_template_not_patched(self):
        """首頁每次全量重新產生：原檔內容（即使被破壞）不影響輸出。"""
        (self.tmp / "index.html").write_text("garbage without markers", encoding="utf-8")
        self.quiet_update_index(self.make_meta(DATE))
        index = self.read_index()
        self.assertNotIn("garbage", index)
        self.assertIn('class="latest"', index)
        self.assertNotIn("<!--SLOT:", index)

    def test_index_keeps_latest_six_only(self):
        for day in range(1, 9):  # 8 筆
            self.quiet_update_index(self.make_meta(f"2026-11-{day:02d}"))
        index = self.read_index()
        recent = index[index.index('id="recent-h"'):]
        # 最新一期（11-08）為大卡；近期 6 期為 11-07 ~ 11-02
        self.assertEqual(recent.count('class="issue"'), 6)
        for day in range(2, 8):
            self.assertIn(f"2026-11-{day:02d}", recent)
        self.assertNotIn("2026-11-01", index)
        # metadata 仍保留全部 8 筆
        self.assertEqual(len(self.read_metadata()), 8)

    def test_latest_card_shows_newest(self):
        self.quiet_update_index(self.make_meta("2026-11-10"))
        self.quiet_update_index(self.make_meta("2026-11-12", total=7))
        index = self.read_index()
        latest = index[index.index('class="latest"'):index.index('id="recent-h"')]
        self.assertIn("reports/2026/11/2026-11-12.html", latest)
        self.assertIn("2026 年 11 月 12 日", latest)
        self.assertIn("<div><dt>總場數</dt><dd>7</dd></div>", latest)

    def test_latest_not_repeated_in_recent(self):
        self.quiet_update_index(self.make_meta("2026-11-10"))
        self.quiet_update_index(self.make_meta("2026-11-12"))
        index = self.read_index()
        recent = index[index.index('id="recent-h"'):]
        self.assertNotIn("2026-11-12", recent)
        self.assertIn("2026-11-10", recent)

    def test_index_links_and_wording(self):
        self.quiet_update_index(self.make_meta(DATE))
        index = self.read_index()
        self.assertIn("每日中午自動整理前一日大聯盟賽況", index)
        self.assertNotIn("12:00", index)
        self.assertIn('href="assets/style.css"', index)
        self.assertIn('href="archive.html"', index)


class RenderIndexTests(MlbTestBase):
    def test_offseason_entries_show_label_and_counts(self):
        db = {
            "2026-11-16": {"date": "2026-11-16", "total_games": 0, "postponed": 0, "summary": "LATEST_SUMMARY",
                           "type": "offseason", "news_count": 8, "transactions_count": 6},
            "2026-11-15": {"date": "2026-11-15", "total_games": 0, "postponed": 0, "summary": "休賽 A",
                           "type": "offseason", "news_count": 2, "transactions_count": 3},
            "2026-11-10": {"date": "2026-11-10", "total_games": 1, "postponed": 0, "summary": "有賽事",
                           "type": "season", "news_count": 0, "transactions_count": 0},
        }
        out = fetch_mlb.render_index(db)
        latest = out[out.index('class="latest"'):out.index('id="recent-h"')]
        self.assertIn("休賽季快報", latest)
        self.assertIn("<div><dt>新聞</dt><dd>8 則</dd></div>", latest)
        self.assertIn("<div><dt>人事異動</dt><dd>6 筆</dd></div>", latest)
        recent = out[out.index('id="recent-h"'):]
        self.assertIn('<span class="k is-off">休賽季</span>', recent)
        self.assertIn("新聞 2 則", recent)
        self.assertIn("人事異動 3 筆", recent)
        self.assertIn('<span class="k">1 場</span>', recent)
        self.assertNotIn("LATEST_SUMMARY", recent)
        self.assertIn("LATEST_SUMMARY", latest)

    def test_legacy_entries_without_new_fields(self):
        """舊資料缺 type / news_count / transactions_count：以 total_games 推斷，缺欄位省略數字。"""
        db = {
            "2026-11-16": {"date": "2026-11-16", "total_games": 0, "postponed": 0, "summary": "舊休賽"},
            "2026-11-15": {"date": "2026-11-15", "total_games": 12, "postponed": 1, "summary": "舊賽季"},
            "2026-11-14": {"date": "2026-11-14", "total_games": 0, "postponed": 0, "summary": "舊休賽 B"},
        }
        out = fetch_mlb.render_index(db)
        latest = out[out.index('class="latest"'):out.index('id="recent-h"')]
        self.assertIn("休賽季快報", latest)
        self.assertNotIn("<dl", latest)  # 缺欄位時不顯示任何數字
        recent = out[out.index('id="recent-h"'):]
        self.assertIn('<span class="k">12 場</span>', recent)
        self.assertIn('<span class="k is-off">休賽季</span>', recent)
        self.assertNotIn("新聞", recent)
        self.assertNotIn("人事異動", recent)
        self.assertEqual(fetch_mlb.entry_type(db["2026-11-15"]), "season")
        self.assertEqual(fetch_mlb.entry_type(db["2026-11-14"]), "offseason")

    def test_season_latest_shows_games_and_postponed(self):
        db = {"2026-07-30": {"date": "2026-07-30", "total_games": 15, "postponed": 1, "summary": "s"}}
        out = fetch_mlb.render_index(db)
        self.assertIn("最新一期", out)
        self.assertIn("<div><dt>總場數</dt><dd>15</dd></div>", out)
        self.assertIn('<div class="is-alert"><dt>延賽</dt><dd>1</dd></div>', out)
        self.assertIn("2026 年 07 月 30 日", out)
        self.assertIn("（四）", out)

    def test_empty_metadata(self):
        out = fetch_mlb.render_index({})
        self.assertIn("尚無戰報", out)
        self.assertNotIn("<!--SLOT:", out)

    def test_non_date_keys_ignored(self):
        out = fetch_mlb.render_index({"junk": {"summary": "x"}, "2026-11-16": {"date": "2026-11-16", "summary": "ok"}})
        self.assertNotIn("junk", out)
        self.assertIn("2026-11-16", out)

    def test_summary_is_escaped(self):
        db = {"2026-11-16": {"date": "2026-11-16", "total_games": 1, "postponed": 0,
                             "summary": "<script>alert(1)</script> & more"}}
        out = fetch_mlb.render_index(db)
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; more", out)


class MetadataFieldsTests(MlbTestBase):
    def test_offseason_metadata_written_to_file(self):
        self.api.schedule = "schedule_empty"
        self.api.transactions = "transactions_default"
        self.assertEqual(self.run_main("schedule_empty"), 0)
        meta = self.read_metadata()[DATE]
        self.assertEqual(meta["type"], "offseason")
        self.assertNotIn("news_count", meta)
        self.assertEqual(meta["transactions_count"], 4)
        html = self.read_report()
        self.assertNotIn("新聞", html)
        self.assertNotIn("0 則", html)
        self.assertIn("<div><dt>人事異動</dt><dd>4 筆</dd></div>", html)

    def test_season_metadata_written_to_file(self):
        self.api.transactions = "transactions_default"
        self.assertIsNone(self.run_main("schedule_normal"))
        meta = self.read_metadata()[DATE]
        self.assertEqual(meta["type"], "season")
        self.assertEqual(meta["transactions_count"], 4)
        self.assertNotIn("news_count", meta)

    def test_existing_legacy_entries_are_preserved(self):
        legacy = {"2026-11-14": {"date": "2026-11-14", "total_games": 3, "postponed": 0, "summary": "舊"}}
        (self.tmp / "reports" / "metadata.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(self.run_main("schedule_normal"))
        db = self.read_metadata()
        self.assertEqual(db["2026-11-14"], legacy["2026-11-14"])  # 舊項目不被改寫
        self.assertIn("2026-11-14", (self.tmp / "index.html").read_text(encoding="utf-8"))


class ArchivePageTests(unittest.TestCase):
    """archive.html / assets 為靜態檔，直接檢查 repo 內的檔案。"""

    def test_archive_page_references(self):
        html = (REPO_ROOT / "archive.html").read_text(encoding="utf-8")
        self.assertIn('href="assets/style.css"', html)
        self.assertIn('src="assets/archive.js"', html)
        self.assertIn('href="index.html"', html)

    def test_archive_js_reads_metadata_and_has_no_simulation_params(self):
        js = (REPO_ROOT / "assets" / "archive.js").read_text(encoding="utf-8")
        self.assertIn("reports/metadata.json", js)
        self.assertNotIn("metadata.sample.json", js)
        self.assertNotIn("URLSearchParams", js)
        self.assertNotIn("fail", js.replace("failure", ""))
        for state in ("讀取中", "無法讀取存檔索引", "目前沒有存檔"):
            self.assertIn(state, js)

    def test_style_sheet_has_dark_stripe_rule(self):
        css = (REPO_ROOT / "assets" / "style.css").read_text(encoding="utf-8")
        self.assertIn(".team-stripe--dark", css)


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
        self.assertEqual(meta["type"], "offseason")
        self.assertNotIn("news_count", meta)
        self.assertIn("休賽季快報", self.read_report())
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
            self.assertIn(f'<td class="n runs">{runs}</td>', failed)
        self.assertNotIn("statline", failed)
        self.assertNotIn("決定", failed.replace("詳細數據暫缺", ""))
        # 其他場維持完整卡片
        self.assertIn("1001", normal)
        self.assertIn("打擊焦點", normal)

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


# ---------------------------------------------------------------- 通知旗標（模組 C：--no-notify / --notify-only）
class NotifyFlagTests(MlbTestBase):
    def run_flags(self, *flags):
        argv = ["fetch_mlb.py", "--date", DATE, *flags]
        with mock.patch.object(sys, "argv", argv),                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code
        return None

    def snapshot(self):
        files = {}
        for p in sorted(self.tmp.rglob("*")):
            if p.is_file() and "templates" not in p.parts:
                files[str(p.relative_to(self.tmp))] = p.read_bytes()
        return files

    def test_no_notify_with_games(self):
        self.api.schedule = "schedule_normal"
        code = self.run_flags("--no-notify")
        self.assertIsNone(code)
        self.assertTrue((self.tmp / "reports" / "2026" / "11" / f"{DATE}.html").exists())
        self.assertEqual(self.read_metadata()[DATE]["total_games"], 2)
        self.discord.assert_not_called()

    def test_no_notify_empty_day(self):
        self.api.schedule = "schedule_empty"
        code = self.run_flags("--no-notify")
        self.assertEqual(code, 0)
        self.assertIn("休賽季快報", self.read_report())
        self.assertEqual(self.read_metadata()[DATE]["total_games"], 0)
        self.discord.assert_not_called()

    def test_notify_only_sends_existing_entry(self):
        entry = {"date": DATE, "total_games": 4, "postponed": 1, "summary": "既有摘要"}
        (self.tmp / "reports" / "metadata.json").write_text(
            json.dumps({DATE: entry}, ensure_ascii=False), encoding="utf-8")
        before = self.snapshot()
        code = self.run_flags("--notify-only")
        self.assertIsNone(code)
        self.discord.assert_called_once_with(entry)
        self.assertEqual(fetch_mlb.fetch_json.call_count, 0)
        self.assertEqual(self.snapshot(), before)

    def test_notify_only_missing_entry_exits_1(self):
        (self.tmp / "reports" / "metadata.json").write_text("{}", encoding="utf-8")
        code = self.run_flags("--notify-only")
        self.assertEqual(code, 1)
        self.discord.assert_not_called()

    def test_notify_only_conflicts_with_check_wait(self):
        code = self.run_flags("--notify-only", "--check-wait")
        self.assertEqual(code, 1)
        self.discord.assert_not_called()
        self.assertEqual(fetch_mlb.fetch_json.call_count, 0)

    def test_notify_only_conflicts_with_no_notify(self):
        code = self.run_flags("--notify-only", "--no-notify")
        self.assertEqual(code, 1)
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

    def test_M1_news_must_be_escaped(self):
        """M1: 新聞 headline/blurb 內的 HTML 必須 escape。"""
        _, html = self.generate("schedule_xss", transactions="transactions_xss")
        self.assertNotIn("<script>alert('XSS_MARKER')</script>", html)
        self.assertNotIn("<img src=x onerror", html)
        self.assertIn("&amp; more", html)

    def test_M1_player_name_must_be_escaped(self):
        """M1: 球員名（打者焦點）內的 HTML 必須 escape。"""
        _, html = self.generate("schedule_xss", transactions="transactions_empty")
        self.assertNotIn("Evil <script>", html)
        self.assertIn("Evil &lt;script&gt;", html)
        self.assertIn("&amp; Son", html)

    def test_M1_transaction_description_must_be_escaped(self):
        """M1: 交易描述與其中的球員名必須 escape。"""
        _, html = self.generate("schedule_normal", transactions="transactions_xss")
        self.assertNotIn("<b>Durham</b>", html)
        self.assertNotIn("<script>alert('XSS_MARKER')</script>", html)
        self.assertIn("&lt;b&gt;Durham&lt;/b&gt;", html)

    def test_M2_empty_day_placeholders_replaced(self):
        """M2: 無賽事日的副標題日期（「月」字樣）與總覽列應被正確產生。"""
        self.run_main("schedule_empty")
        html = self.read_report()
        self.assertIn("2026 年 11 月 15 日（日）休賽季快報", html)
        self.assertNotIn("2026 年 07 月 19 日", html)
        self.assertNotIn("07 年", html)
        self.assertIn("<div><dt>人事異動</dt><dd>0 筆</dd></div>", html)

    def test_M3_boxscore_failure_keeps_card_count_equal_total(self):
        """M3: boxscore 失敗的場次仍應有卡片，卡片數等於 total_games。"""
        meta, html = self.generate("schedule_normal", fail_boxscore={1002})
        self.assertEqual(meta["total_games"], 2)
        self.assertEqual(html.count(CARD_OPEN), meta["total_games"])

    def test_M4_scheduled_seven_innings_completed_shows_final(self):
        """M4: 預定 7 局的比賽（如春訓）打滿應顯示 Final、不套延長賽樣式；MLB 雙重賽自 2022 年起已恢復 9 局。"""
        _, html = self.generate("schedule_scheduled7")
        self.assertNotIn("Final/7", html)
        self.assertNotIn('<span class="badge is-extra">', html)
        self.assertIn('<span class="badge is-final">Final</span>', html)

    def test_M4_suspended_not_marked_final(self):
        """M4: Suspended 的場次不應被標成完賽（Final/5）。"""
        _, html = self.generate("schedule_suspended")
        self.assertNotIn("Final/5", html)
        self.assertNotIn('<span class="badge is-final">', html)

    def test_M4_tie_has_no_winner(self):
        """M4: 比分相同（2:2）時，兩隊都不應標示勝方。"""
        _, html = self.generate("schedule_suspended")
        self.assertEqual(html.count(WINNER_ROW), 0)


# ---------------------------------------------------------------- 狀態判斷與等待迴圈（模組 B：H4 / M4 / L1）
def make_game(state="Final", detailed="Final", innings=9, scheduled=9, game_type="R", **status_extra):
    status = {"abstractGameState": state, "detailedState": detailed}
    status.update(status_extra)
    linescore = {"innings": [{"num": i} for i in range(1, innings + 1)], "teams": {}}
    if scheduled is not None:
        linescore["scheduledInnings"] = scheduled
    return {"gamePk": 1, "gameType": game_type, "status": status, "linescore": linescore}


def games_block(html):
    """賽事結果區塊（不含人事異動區塊）。"""
    block = html.split('aria-labelledby="games-h"')[1]
    return block.split('aria-labelledby="tx-h"')[0]


class StatusTests(MlbTestBase):
    def classify(self, **kw):
        game = make_game(**kw)
        return fetch_mlb.classify_game_status(game, game["linescore"])

    def test_nine_inning_final(self):
        self.assertEqual(self.classify(), ("Final", "final", True))

    def test_extra_innings(self):
        self.assertEqual(self.classify(innings=11), ("Final/11", "extra", True))

    def test_seven_inning_doubleheader(self):
        self.assertEqual(self.classify(innings=7, scheduled=7), ("Final", "final", True))

    def test_completed_early(self):
        result = self.classify(detailed="Completed Early: Rain", innings=5, scheduled=9)
        self.assertEqual(result, ("Final/5（提前結束）", "early", True))

    def test_suspended(self):
        text, cls, is_final = self.classify(state="Live", detailed="Suspended: Rain", innings=5)
        self.assertEqual(text, "暫停比賽")
        self.assertNotEqual(cls, "final")
        self.assertFalse(is_final)

    def test_live_is_unfinished(self):
        text, cls, is_final = self.classify(state="Live", detailed="In Progress", innings=3)
        self.assertEqual(text, "未完賽")
        self.assertNotEqual(cls, "final")
        self.assertFalse(is_final)

    def test_missing_scheduled_innings_defaults_to_nine(self):
        self.assertEqual(self.classify(innings=9, scheduled=None), ("Final", "final", True))

    def test_suspended_card_has_no_winner_and_no_decisions(self):
        _, html = self.generate("schedule_suspended")
        self.assertEqual(html.count(WINNER_ROW), 0)
        self.assertIn("暫停比賽", html)
        self.assertIn('<span class="badge is-suspended">暫停比賽</span>', html)
        # 不列出勝／敗／救援（設計稿以「無投球決定」說明文字取代）
        self.assertNotIn('class="decisions"', html)
        self.assertNotIn('class="is-w"', html)
        self.assertIn("無投球決定", html)

    def test_completed_early_card(self):
        _, html = self.generate("schedule_completed_early")
        self.assertIn('<span class="badge is-early">Final/5（提前結束）</span>', html)
        self.assertEqual(html.count(WINNER_ROW), 1)

    def test_extra_card_keeps_decisions(self):
        _, html = self.generate("schedule_extra")
        self.assertIn('<span class="badge is-extra">Final/11</span>', html)
        self.assertIn('class="decisions"', html)

    def test_fallback_card_uses_classified_status(self):
        _, html = self.generate("schedule_suspended", fail_boxscore={4001})
        self.assertIn("詳細數據暫缺", html)
        self.assertIn("暫停比賽", html)
        self.assertNotIn('<span class="badge is-final">', html)


class WordingTests(MlbTestBase):
    def test_world_series_title(self):
        _, html = self.generate("schedule_worldseries")
        self.assertIn('世界大賽 <span class="num">#8003</span>', html)
        self.assertNotIn("例行賽", games_block(html))

    def test_regular_season_title(self):
        _, html = self.generate("schedule_normal")
        self.assertIn('例行賽 <span class="num">#1001</span>', html)

    def test_game_type_labels(self):
        expected = {"R": "例行賽", "F": "外卡賽", "D": "分區系列賽", "L": "聯盟冠軍賽",
                    "W": "世界大賽", "S": "春訓", "E": "熱身賽", "A": "明星賽", "X": "比賽"}
        for code, label in expected.items():
            self.assertEqual(fetch_mlb.game_type_label({"gameType": code}), label)
        self.assertEqual(fetch_mlb.game_type_label({}), "比賽")

    def test_cancelled_card(self):
        meta, html = self.generate("schedule_cancelled")
        games = games_block(html)
        self.assertIn("取消", games)
        self.assertNotIn("Postponed", games)
        self.assertNotIn("延期", games)
        self.assertEqual(meta["postponed"], 1)

    def test_postponed_wording(self):
        _, html = self.generate("schedule_postponed")
        games = games_block(html)
        self.assertNotIn("今日", games)
        self.assertIn("原定於當日舉行", games)
        self.assertIn("Postponed", games)

    def test_world_series_postponed_title(self):
        _, html = self.generate("schedule_worldseries_postponed")
        games = games_block(html)
        self.assertIn("世界大賽", games)
        self.assertNotIn("例行賽", games)


class WaitLoopTests(MlbTestBase):
    def run_wait(self, *extra):
        argv = ["fetch_mlb.py", "--date", DATE, "--check-wait", *extra]
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code
        return None

    def html_files(self):
        return list((self.tmp / "reports").rglob("*.html"))

    def test_default_cap_sleeps_seven_times_then_reports(self):
        self.api.schedule = "schedule_live_timeout"
        code = self.run_wait()
        self.assertIsNone(code)
        self.assertEqual(fetch_mlb.MAX_WAIT_CHECKS, 8)
        self.assertEqual(fetch_mlb.time.sleep.call_count, 7)
        self.assertEqual(self.api.schedule_calls, 8)
        html = self.read_report()
        self.assertIn("未完賽", html)
        self.assertNotIn('<span class="badge is-final">', html)
        self.assertEqual(html.count(WINNER_ROW), 0)
        self.assertNotIn('class="decisions"', html)
        self.assertEqual(self.read_metadata()[DATE]["total_games"], 1)
        self.discord.assert_called_once()

    def test_max_wait_checks_option(self):
        self.api.schedule = "schedule_live_timeout"
        code = self.run_wait("--max-wait-checks", "3")
        self.assertIsNone(code)
        self.assertEqual(fetch_mlb.time.sleep.call_count, 2)
        self.assertEqual(self.api.schedule_calls, 3)
        self.assertIn("未完賽", self.read_report())

    def test_api_error_all_the_way_exits_without_output(self):
        self.api.schedule = None
        code = self.run_wait()
        self.assertEqual(code, 1)
        self.assertEqual(fetch_mlb.time.sleep.call_count, 7)
        self.assertEqual(self.html_files(), [])
        self.assertFalse((self.tmp / "reports" / "metadata.json").exists())
        self.discord.assert_not_called()

    def test_suspended_does_not_wait(self):
        self.api.schedule = "schedule_suspended"
        finalized, _ = fetch_mlb.check_all_games_finalized(DATE)
        self.assertTrue(finalized)
        code = self.run_wait()
        self.assertIsNone(code)
        fetch_mlb.time.sleep.assert_not_called()
        self.assertIn("暫停比賽", self.read_report())

    def test_invalid_max_wait_checks(self):
        self.api.schedule = "schedule_normal"
        self.assertEqual(self.run_wait("--max-wait-checks", "0"), 1)
        self.assertEqual(self.html_files(), [])



# ---------------------------------------------------------------- 渲染層（模組 D + G 階段 1）
def make_player(pid, name, pos="RF", batting=None, pitching=None, season_batting=None, season_pitching=None):
    info = {"person": {"id": pid, "fullName": name}, "position": {"abbreviation": pos}, "stats": {}, "seasonStats": {}}
    if batting is not None:
        info["stats"]["batting"] = batting
    if pitching is not None:
        info["stats"]["pitching"] = pitching
    if season_batting is not None:
        info["seasonStats"]["batting"] = season_batting
    if season_pitching is not None:
        info["seasonStats"]["pitching"] = season_pitching
    return info


class DateFormatTests(unittest.TestCase):
    def test_fmt_date_long(self):
        self.assertEqual(fetch_mlb.fmt_date_long("2026-07-30"), "2026 年 07 月 30 日（四）")
        self.assertEqual(fetch_mlb.fmt_date_long("2026-11-16"), "2026 年 11 月 16 日（一）")
        self.assertEqual(fetch_mlb.fmt_date_long("2026-11-15"), "2026 年 11 月 15 日（日）")

    def test_fmt_date_long_invalid(self):
        self.assertEqual(fetch_mlb.fmt_date_long("not-a-date"), "not-a-date")


class EscapeTests(unittest.TestCase):
    def test_esc_quotes_and_amp(self):
        self.assertEqual(fetch_mlb.esc('a&b"c\'d<e>'), "a&amp;b&quot;c&#x27;d&lt;e&gt;")

    def test_esc_non_string(self):
        self.assertEqual(fetch_mlb.esc(5), "5")


class TeamColorTests(unittest.TestCase):
    def test_dark_team_colors(self):
        for tid in (147, 114, 116):  # NYY、CLE、DET 為深藍
            self.assertTrue(fetch_mlb.is_dark_color(fetch_mlb.TEAM_META[tid]["color"]), tid)

    def test_bright_team_colors(self):
        for tid in (119, 117, 134, 111):  # LAD、HOU、PIT、BOS
            self.assertFalse(fetch_mlb.is_dark_color(fetch_mlb.TEAM_META[tid]["color"]), tid)

    def test_luminance_extremes(self):
        self.assertAlmostEqual(fetch_mlb.relative_luminance("#000000"), 0.0)
        self.assertAlmostEqual(fetch_mlb.relative_luminance("#FFFFFF"), 1.0, places=3)

    def test_invalid_color_falls_back_safely(self):
        self.assertEqual(fetch_mlb.safe_color("red; background:url(x)"), "#6b7280")


class CardRenderingTests(MlbTestBase):
    def test_dark_team_stripe_class_only_on_dark_teams(self):
        _, html = self.generate("schedule_normal")
        cards = html.split(CARD_OPEN)[1:]
        nyy = next(c for c in cards if 'data-teams="NYY|' in c)
        # NYY 為深色：色條有 team-stripe--dark；BOS 不是
        self.assertIn('<td class="team team-stripe--dark" style="--team:#0C2340">', nyy)
        self.assertIn('<td class="team" style="--team:#BD3039">', nyy)

    def test_data_teams_pipe_separated(self):
        _, html = self.generate("schedule_normal")
        self.assertIn('data-teams="NYY|紐約洋基|New York Yankees|BOS|波士頓紅襪|Boston Red Sox"', html)
        self.assertNotIn('data-teams="NYY 紐約洋基', html)

    def test_search_section_present_with_games(self):
        _, html = self.generate("schedule_normal")
        self.assertIn('id="searchForm"', html)
        self.assertIn("再按 Enter 跳下一場", html)  # 搜尋 JS（設計稿）
        self.assertIn("找不到「", html)

    def test_game_card_structure(self):
        _, html = self.generate("schedule_normal")
        for needle in ('class="game-head"', 'class="score"', 'class="game-body"', 'class="col-left"',
                       'class="col-right"', "投球決定", "賽後新聞", "打擊焦點", "投球焦點", 'class="statline'):
            self.assertIn(needle, html)

    def test_news_text_rendered_escaped(self):
        _, html = self.generate("schedule_normal")
        self.assertIn('<h5>Yankees top Red Sox 5-3</h5>', html)


class ExtraBaseTagTests(MlbTestBase):
    def boxscore_with(self, batting, season_batting):
        box = load_fixture("boxscore_1001")
        away = box["teams"]["away"]["players"]
        away["ID10011"] = make_player(10011, "Away Batter 1001", "RF", batting=batting, season_batting=season_batting)
        return box

    def test_M9_labels_and_cumulative(self):
        batting = {"atBats": 5, "runs": 2, "hits": 4, "doubles": 2, "triples": 1, "homeRuns": 1,
                   "rbi": 3, "baseOnBalls": 1, "strikeOuts": 2, "stolenBases": 1}
        season = {"doubles": 21, "triples": 4, "homeRuns": 9, "stolenBases": 15}
        self.api.boxscore_overrides[1001] = self.boxscore_with(batting, season)
        _, html = self.generate("schedule_normal")
        self.assertIn("2B: 2(20)(21)", html)
        self.assertIn("3B: 1(4)", html)
        self.assertIn("HR: 1(9)", html)
        self.assertIn("SB: 1(15)", html)
        # 打者欄位：AB R H RBI BB SO
        card = next(c for c in html.split(CARD_OPEN)[1:] if "Away Batter 1001" in c)
        statline = card[card.index("Away Batter 1001"):]
        for label in ("AB", "R", "H", "RBI", "BB", "SO"):
            self.assertIn(f"<dt>{label}</dt>", statline)
        self.assertIn("<dt>SO</dt><dd>2</dd>", statline)

    def test_M9_pitcher_columns(self):
        _, html = self.generate("schedule_normal")
        for label in ("IP", "H", "R", "ER", "BB", "SO", "P-S"):
            self.assertIn(f"<dt>{label}</dt>", html)
        self.assertIn('class="statline is-pitch"', html)

    def test_tag_omitted_when_no_extra_base_hits(self):
        batting = {"atBats": 5, "runs": 2, "hits": 4, "doubles": 0, "triples": 0, "homeRuns": 0,
                   "rbi": 4, "baseOnBalls": 0, "strikeOuts": 1, "stolenBases": 0}
        self.api.boxscore_overrides[1001] = self.boxscore_with(batting, {})
        _, html = self.generate("schedule_normal")
        card = next(c for c in html.split(CARD_OPEN)[1:] if "Away Batter 1001" in c)
        row = card[card.index("Away Batter 1001"):card.index("<dl", card.index("Away Batter 1001"))]
        self.assertNotIn("2B:", row)
        self.assertNotIn("3B:", row)

    def test_missing_season_stats_does_not_emit_zero(self):
        batting = {"atBats": 4, "runs": 1, "hits": 2, "doubles": 1, "triples": 0, "homeRuns": 0,
                   "rbi": 0, "baseOnBalls": 0, "strikeOuts": 0, "stolenBases": 0}
        self.api.boxscore_overrides[1001] = self.boxscore_with(batting, {})
        _, html = self.generate("schedule_normal")
        self.assertIn("2B: 1<", html)
        self.assertNotIn("2B: 1(0)", html)


class CandidateSelectionTests(unittest.TestCase):
    def box(self, players):
        return {"teams": {"away": {"players": players, "pitchers": []}, "home": {"players": {}, "pitchers": []}}}

    def test_L4_pinch_runner_with_sb_and_no_at_bats_is_candidate(self):
        runner = make_player(1, "Pinch Runner", "PR",
                             batting={"atBats": 0, "runs": 1, "hits": 0, "stolenBases": 1},
                             season_batting={"stolenBases": 20})
        cands = fetch_mlb.collect_candidates(self.box({"ID1": runner}), "AAA", "BBB")
        self.assertEqual([c["name"] for c in cands], ["Pinch Runner"])
        self.assertEqual(cands[0]["stats"]["sb"], 1)

    def test_L4_pinch_runner_scored_only_is_candidate(self):
        runner = make_player(2, "Scoring Runner", "PR", batting={"atBats": 0, "runs": 1})
        cands = fetch_mlb.collect_candidates(self.box({"ID2": runner}), "AAA", "BBB")
        self.assertEqual(len(cands), 1)

    def test_bench_player_without_activity_excluded(self):
        bench = make_player(3, "Bench Guy", "PH", batting={"atBats": 0, "runs": 0, "stolenBases": 0})
        self.assertEqual(fetch_mlb.collect_candidates(self.box({"ID3": bench}), "AAA", "BBB"), [])

    def test_top_three_guaranteed_and_threshold(self):
        players = {}
        for i in range(2):  # 弱打者：各 1 分
            players[f"ID{i}"] = make_player(i + 1, f"Weak{i}", batting={"atBats": 4, "hits": 1, "runs": 0, "rbi": 0})
        for i in range(4):  # 強打者：各 20.5 分
            players[f"ID9{i}"] = make_player(90 + i, f"Slugger{i}",
                                             batting={"atBats": 4, "hits": 3, "homeRuns": 2, "runs": 2, "rbi": 5})
        cands = fetch_mlb.collect_candidates(self.box(players), "AAA", "BBB")
        batters, pitchers, selected = fetch_mlb.select_performers(cands)
        # 前 3 名保證入選，第 4 名（Slugger，>= 7 分）因達門檻入選；弱打者（1 分）不入選
        self.assertEqual(len(selected), 4)
        self.assertTrue(all(b["name"].startswith("Slugger") for b in batters))
        self.assertEqual(pitchers, [])

    def test_L4_sb_pinch_runner_rendered_in_card(self):
        # 整合：代跑者出現在卡片的打擊焦點（至少 3 名保證入選）
        box = load_fixture("boxscore_1001")
        box["teams"]["away"]["players"] = {
            "ID70": make_player(70, "Pinch Runner Seven", "PR",
                                batting={"atBats": 0, "runs": 1, "stolenBases": 1},
                                season_batting={"stolenBases": 12}),
        }
        box["teams"]["away"]["pitchers"] = []
        box["teams"]["home"]["players"] = {}
        box["teams"]["home"]["pitchers"] = []
        batters, _, _ = fetch_mlb.select_performers(fetch_mlb.collect_candidates(box, "NYY", "BOS"))
        html = fetch_mlb.render_batter(batters[0])
        self.assertIn("Pinch Runner Seven", html)
        self.assertIn("SB: 1(12)", html)


class PageLinkTests(MlbTestBase):
    def hrefs(self, html):
        return re.findall(r'(?:href|src)="([^"]+)"', html)

    def test_season_page_links(self):
        _, html = self.generate("schedule_normal")
        refs = self.hrefs(html)
        self.assertIn("../../../assets/style.css", refs)
        self.assertIn("../../../index.html", refs)
        self.assertIn("../../../archive.html", refs)
        self.assertNotIn("assets/style.css", [r for r in refs if not r.startswith("../../../")])

    def test_offseason_page_links(self):
        self.run_main("schedule_empty")
        refs = self.hrefs(self.read_report())
        self.assertIn("../../../assets/style.css", refs)
        self.assertIn("../../../index.html", refs)
        self.assertIn("../../../archive.html", refs)

    def test_index_links(self):
        self.run_main("schedule_normal")
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        refs = self.hrefs(index)
        self.assertIn("assets/style.css", refs)
        self.assertIn("archive.html", refs)
        self.assertIn("reports/2026/11/2026-11-15.html", refs)

    def test_relative_links_resolve_from_report_location(self):
        """戰報位於 reports/YYYY/MM/，'../../../' 應解析到網站根目錄（此處為暫存目錄）。"""
        (self.tmp / "assets").mkdir()
        (self.tmp / "assets" / "style.css").write_text("/* css */", encoding="utf-8")
        (self.tmp / "index.html").write_text("x", encoding="utf-8")
        (self.tmp / "archive.html").write_text("x", encoding="utf-8")
        self.generate("schedule_normal")
        report_dir = self.tmp / "reports" / "2026" / "11"
        for ref in ("../../../assets/style.css", "../../../index.html", "../../../archive.html"):
            self.assertTrue((report_dir / ref).resolve().exists(), ref)


class OffseasonPageTests(MlbTestBase):
    def test_offseason_page_content(self):
        self.api.transactions = "transactions_default"
        self.run_main("schedule_empty")
        html = self.read_report()
        self.assertIn("休賽季快報", html)
        self.assertIn("2026 年 11 月 15 日（日）休賽季快報", html)
        self.assertIn("<title>MLB 每日戰報 - 2026-11-15 休賽季快報</title>", html)
        self.assertIn("大聯盟人事異動", html)
        self.assertIn("Big Name", html)
        # 無比賽卡片：不輸出搜尋區，也不輸出賽事區塊
        self.assertNotIn('id="searchForm"', html)
        self.assertNotIn('aria-labelledby="games-h"', html)
        self.assertNotIn(CARD_OPEN, html)

    def test_offseason_without_transactions(self):
        self.run_main("schedule_empty")
        html = self.read_report()
        self.assertIn("本日無大聯盟人事異動", html)
        self.assertIn("<div><dt>人事異動</dt><dd>0 筆</dd></div>", html)


class FooterTests(MlbTestBase):
    def test_footer_uses_taipei_time(self):
        fixed = fetch_mlb.datetime(2026, 11, 17, 4, 17, tzinfo=fetch_mlb.timezone.utc)  # UTC 04:17 = 台北 12:17

        class FakeDatetime(fetch_mlb.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz else fixed

        with mock.patch.object(fetch_mlb, "datetime", FakeDatetime):
            _, html = self.generate("schedule_normal")
        self.assertIn("產生時間：2026-11-17 12:17（台北時間）", html)


class XssSweepTests(MlbTestBase):
    """以含 <script>、"onmouseover=、& 的 fixture 產生所有頁面，輸出中不得出現未跳脫的標籤。"""

    BAD = ("<script>alert('XSS_MARKER')</script>", "<img", '" onmouseover', "<b>Durham</b>", "<script>alert")

    # 渲染層自己產生的隊徽 <img>（網址只含整數 id）是合法標籤，檢查前先移除
    LOGO_IMG = re.compile(r'<img src="https://www\.mlbstatic\.com/team-logos/[a-z-]+/\d+\.svg" width="\d+" height="\d+" alt="[^"<>]*" loading="lazy">')

    def assert_clean(self, html, allow_shell_script=True):
        if allow_shell_script:
            # 頁面殼自己的搜尋 <script> 是唯一允許的；先移除它再檢查
            html = re.sub(r"<script>\s*\(function \(\) \{.*?</script>", "", html, flags=re.S)
        html = self.LOGO_IMG.sub("", html)
        for bad in self.BAD:
            self.assertNotIn(bad, html)
        self.assertNotIn("<script", html)

    def test_season_page(self):
        _, html = self.generate("schedule_xss", transactions="transactions_xss")
        self.assert_clean(html)
        self.assertIn("&amp; more", html)
        self.assertIn("Evil &lt;script&gt;", html)

    def test_season_page_with_attribute_injection_in_team_name(self):
        games = self.games_of("schedule_normal")
        games[0]["teams"]["away"]["team"]["name"] = 'Bad" onmouseover="alert(1)'
        games[0]["teams"]["away"]["team"]["id"] = 99999  # 非 30 隊：使用英文名當隊名
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            fetch_mlb.generate_report(DATE, games)
        html = self.read_report()
        self.assert_clean(html)
        self.assertNotIn('" onmouseover', html)
        self.assertIn("&quot; onmouseover=&quot;alert(1)", html)

    def test_offseason_page(self):
        self.api.transactions = "transactions_xss"
        self.run_main("schedule_empty")
        html = self.read_report()
        self.assert_clean(html)
        self.assertIn("&lt;b&gt;Durham&lt;/b&gt;", html)

    def test_index_page(self):
        db = {
            "2026-11-16": {"date": "2026-11-16", "total_games": 1, "postponed": 0,
                           "summary": '<script>alert(1)</script> "onmouseover=x <img src=x> &'},
            "2026-11-15": {"date": "2026-11-15", "total_games": 0, "postponed": 0,
                           "summary": '<img src=x onerror=1>" onmouseover="y'},
        }
        out = fetch_mlb.render_index(db)
        self.assertNotIn("<script>alert", out)
        self.assertNotIn("<img", out)  # 首頁沒有任何圖片標籤
        self.assertNotIn('" onmouseover', out)
        self.assertIn("&amp;", out)


class RobustnessTests(MlbTestBase):
    def test_render_summary_offseason_omits_news_when_none(self):
        out = fetch_mlb.render_summary("offseason", transactions_count=3)
        self.assertNotIn("新聞", out)
        self.assertIn("<div><dt>人事異動</dt><dd>3 筆</dd></div>", out)
        out = fetch_mlb.render_summary("offseason", news_count=5, transactions_count=3)
        self.assertIn("<div><dt>新聞</dt><dd>5 則</dd></div>", out)

    def test_tx_description_name_matching_entity_text(self):
        """名字為 amp、描述含 & 時，不得把 &amp; 內的 amp 換成連結。"""
        out = fetch_mlb.render_tx_description("Fish & Chips signed amp", 7, "amp")
        self.assertIn("Fish &amp; Chips signed ", out)
        self.assertNotIn("&<a", out)
        self.assertEqual(out.count('<a class="plink"'), 1)
        self.assertTrue(out.endswith(">amp</a>"))

    def test_tx_description_name_with_lt_end_to_end(self):
        tx = {"transactions": [{
            "description": "Tampa Bay Rays selected the contract of A<B Smith from Triple-A Durham.",
            "person": {"id": 4242, "fullName": "A<B Smith"},
            "toTeam": {"id": 139}, "fromTeam": {}}]}
        self.api.transactions = None
        with mock.patch.object(fetch_mlb, "fetch_json", side_effect=lambda url, **k:
                               tx if "/transactions" in url else self.api.fetch(url)):
            self.api.schedule = "schedule_normal"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                fetch_mlb.generate_report(DATE, self.games_of("schedule_normal"))
        html = self.read_report()
        self.assertNotIn("A<B", html)
        self.assertIn("A&lt;B Smith</a>", html)

    def test_player_link_non_integer_id(self):
        self.assertEqual(fetch_mlb.player_link("12x", "A<B"), "A&lt;B")
        self.assertEqual(fetch_mlb.player_link(None, "Bob"), "Bob")
        self.assertIn('savant-player/12"', fetch_mlb.player_link("12", "Bob"))

    def test_logo_picture_non_integer_id(self):
        self.assertEqual(fetch_mlb.logo_picture("abc", "X", 24), "")
        self.assertEqual(fetch_mlb.logo_picture(None, "X", 24), "")
        self.assertIn("/147.svg", fetch_mlb.logo_picture(147, "NYY", 24))

    def test_tx_description_bad_id_falls_back_to_plain_text(self):
        self.assertEqual(fetch_mlb.render_tx_description("Bob <x>", "zz", "Bob"), "Bob &lt;x&gt;")

    def test_recent_count_reflects_actual_number(self):
        db = {f"2026-11-{d:02d}": {"date": f"2026-11-{d:02d}", "total_games": 1, "summary": "s"} for d in (1, 2, 3)}
        out = fetch_mlb.render_index(db)
        self.assertIn("最新 2 期", out)
        self.assertNotIn("最新 6 期", out)
        one = fetch_mlb.render_index({"2026-11-01": db["2026-11-01"]})
        self.assertIn("尚無更早的戰報", one)
        self.assertNotIn("最新 6 期", one)


class SlotTests(unittest.TestCase):
    def test_fill_slots_single_pass_and_missing_slot(self):
        out = fetch_mlb.fill_slots("a<!--SLOT:X-->b<!--SLOT:Y-->c", {"X": "<!--SLOT:Y-->"})
        self.assertEqual(out, "a<!--SLOT:Y-->bc")

    def test_shell_templates_have_expected_slots(self):
        report = (REPO_ROOT / "templates" / "report.html").read_text(encoding="utf-8")
        for name in ("TITLE", "DATE_LONG", "SUBTITLE", "SUMMARY", "SEARCH", "NEWS", "GAMES", "TRANSACTIONS", "GENERATED_AT"):
            self.assertIn(f"<!--SLOT:{name}-->", report)
        index = (REPO_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        for name in ("LATEST", "RECENT"):
            self.assertIn(f"<!--SLOT:{name}-->", index)
        self.assertFalse((REPO_ROOT / "templates" / "report_template.html").exists())


if __name__ == "__main__":
    unittest.main()
