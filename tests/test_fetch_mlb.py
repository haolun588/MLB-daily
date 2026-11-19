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
from datetime import date, datetime, timedelta, timezone
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


def load_text_fixture(name):
    with open(FIXTURES / name, "r", encoding="utf-8") as f:
        return f.read()


def fixture_exists(name):
    return (FIXTURES / f"{name}.json").exists()


class FakeApi:
    """依 URL 回傳 fixture 的假 fetch_json。schedule 為 None 代表抓取失敗。"""

    def __init__(self):
        self.schedule = None
        self.schedule_sequence = None  # 若設定，依呼叫次數依序回傳（None 代表該次失敗）；用完後重複最後一筆
        self.schedule_calls = 0
        self.transactions = "transactions_empty"
        self.news = None  # RSS fixture 檔名（含副檔名）；None 代表抓取失敗（預設，不影響既有測試）
        self.news_text = None  # 若設定，直接作為 RSS 回應文字（優先於 news）
        self.fail_boxscore = set()
        self.boxscore_overrides = {}  # gamePk -> 自訂 boxscore dict（覆蓋 fixture）
        self.seasons = "seasons_2026"  # 賽季 API fixture 檔名；None 代表抓取失敗
        self.seasons_data = None  # 若設定，直接作為賽季 API 回應（優先於 seasons）
        self.fail_roster = set()  # 球隊 id 集合，或 "all"；該隊 roster 抓取失敗
        self.roster_override = None  # 若設定，所有球隊 roster 回應改用此 dict
        self.standings = None  # standings fixture 檔名；None 代表抓取失敗（預設，不影響既有測試）
        self.people = None  # people（先發投手數據）fixture 檔名；None 代表失敗
        self.seasons_by_year = {}  # {年份: fixture 檔名 或 None（失敗）}；未列出的年份用 seasons
        self.calls = []

    def fetch(self, url, retries=3, delay=5):
        self.calls.append(url)
        if "/standings" in url:
            return load_fixture(self.standings) if self.standings else None
        if "/people?" in url:
            return load_fixture(self.people) if self.people else None
        m = re.search(r"/seasons/(\d{4})", url)
        if m and int(m.group(1)) in self.seasons_by_year:
            name = self.seasons_by_year[int(m.group(1))]
            return load_fixture(name) if name else None
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
        if "/seasons/" in url:
            if self.seasons_data is not None:
                return self.seasons_data
            return load_fixture(self.seasons) if self.seasons else None
        m = re.search(r"/teams/(\d+)/roster\?", url)
        if m:
            if self.fail_roster == "all" or int(m.group(1)) in self.fail_roster:
                return None
            return self.roster_override if self.roster_override is not None else load_fixture("roster_147")
        if "/transactions" in url:
            return load_fixture(self.transactions) if self.transactions else None
        return None


    def fetch_text(self, url, retries=3, delay=5):
        self.calls.append(url)
        if "/feeds/news/rss.xml" in url:
            if self.news_text is not None:
                return self.news_text
            return load_text_fixture(self.news) if self.news else None
        return None


class MlbTestBase(unittest.TestCase):
    def setUp(self):
        # 暫存工作目錄：複製 templates 並放入模擬首頁，reports 為空
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        shutil.copytree(REPO_ROOT / "templates", self.tmp / "templates")
        # index.html 只存在 gh-pages；以模板骨架模擬 gh-pages 上既有的首頁
        shutil.copy(REPO_ROOT / "templates" / "index.html", self.tmp / "index.html")
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
        # 模組層級的翻譯狀態不得在測試之間外洩
        fetch_mlb.LAST_POST["retryable"] = False
        fetch_mlb.TX_TRANSLATION_CACHE.clear()

        # 網路 / 睡眠 / Discord 一律攔截
        self.api = FakeApi()
        for p in (
            mock.patch("mlb_daily.net.fetch_json", side_effect=self.api.fetch),
            mock.patch("mlb_daily.net.fetch_text", side_effect=self.api.fetch_text),
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("測試不得連網")),
            mock.patch.object(fetch_mlb.time, "sleep"),
            # 時鐘固定在休賽季（2026-11-18 12:17 台北），測試結果不得隨真實日期改變；需要賽季中情境的測試自行覆蓋
            mock.patch("mlb_daily.timeutil.get_taipei_now",
                              return_value=datetime(2026, 11, 18, 12, 17, tzinfo=timezone(timedelta(hours=8)))),
        ):
            p.start()
            self.addCleanup(p.stop)
        discord = mock.patch("mlb_daily.notify.send_discord_notification")
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

    def _trade_raw(self):
        desc = "Boston Red Sox traded RHP Angel Bastardo to Kansas City Royals for SS Jack Pineda."
        team = {"id": 111}
        royals = {"id": 118}
        return [
            {"description": desc, "toTeam": royals, "fromTeam": team, "person": {"id": 1, "fullName": "Angel Bastardo"}},
            {"description": desc, "toTeam": team, "fromTeam": royals, "person": {"id": 2, "fullName": "Jack Pineda"}},
            {"description": desc, "toTeam": team, "fromTeam": royals, "person": {"id": 2, "fullName": "Jack Pineda"}},
        ]

    def test_trade_duplicates_merge_persons(self):
        txs = fetch_mlb.filter_major_league_transactions(self._trade_raw())
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]["persons"], [{"id": 1, "name": "Angel Bastardo"}, {"id": 2, "name": "Jack Pineda"}])
        self.assertEqual(txs[0]["person_id"], 1)

    def test_trade_renders_all_links(self):
        tx = fetch_mlb.filter_major_league_transactions(self._trade_raw())[0]
        out = fetch_mlb.render_tx_items([tx])
        self.assertEqual(out.count('class="plink"'), 2)
        self.assertIn('savant-player/1" target="_blank" rel="noopener">Angel Bastardo</a>', out)
        self.assertIn('savant-player/2" target="_blank" rel="noopener">Jack Pineda</a>', out)

    def test_render_substring_names(self):
        persons = [{"id": 1, "name": "Luis Garcia"}, {"id": 2, "name": "Luis Garcia Jr."}]
        out = fetch_mlb.render_tx_description("Traded Luis Garcia Jr. for Luis Garcia.", persons=persons)
        self.assertIn('savant-player/2" target="_blank" rel="noopener">Luis Garcia Jr.</a>', out)
        self.assertIn('savant-player/1" target="_blank" rel="noopener">Luis Garcia</a>', out)
        self.assertEqual(out.count('class="plink"'), 2)

    def test_display_text_falls_back_when_name_missing(self):
        tx = {"description": "A traded Ann Lee for Bob Ray.", "description_zh": "A 交易 Ann Lee。",
              "persons": [{"id": 1, "name": "Ann Lee"}, {"id": 2, "name": "Bob Ray"}]}
        self.assertEqual(fetch_mlb._tx_display_text(tx), tx["description"])
        tx["description_zh"] = "A 交易 Ann Lee 換 Bob Ray。"
        self.assertEqual(fetch_mlb._tx_display_text(tx), tx["description_zh"])

    def test_legacy_tx_without_persons(self):
        tx = {"description": "Signed Bob Ray.", "person_id": 9, "person_name": "Bob Ray"}
        self.assertEqual(fetch_mlb._tx_persons(tx), [{"id": 9, "name": "Bob Ray"}])
        self.assertIn('savant-player/9"', fetch_mlb.render_tx_items([tx]))
        self.assertEqual(fetch_mlb._tx_persons({"description": "x"}), [])


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
        # 其餘 3 筆（Braves、Tigers、Mariners）進入「其他球隊人事異動」
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
        self.assertNotIn("news_count", meta)  # 新聞抓取失敗（FakeApi 預設）時不寫入
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
        self.discord.assert_not_called()  # 無新聞、無人事異動的休賽季快報不發通知

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
        with mock.patch("mlb_daily.net.fetch_json", return_value={"unexpected": True}):
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
        self.assertEqual(fetch_mlb.net.fetch_json.call_count, 0)
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
        self.assertEqual(fetch_mlb.net.fetch_json.call_count, 0)

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

        # setUp 已固定 get_taipei_now；這裡要驗證「真正的」台北時間換算，改成由假的 datetime.now 推算
        with mock.patch("mlb_daily.timeutil.datetime", FakeDatetime),                 mock.patch("mlb_daily.timeutil.get_taipei_now",
                                  side_effect=lambda: FakeDatetime.now(fetch_mlb.timezone.utc).astimezone(fetch_mlb.TAIPEI_TZ)):
            _, html = self.generate("schedule_normal")
        self.assertIn("產生時間：2026-11-17 12:17（台北時間）", html)


class XssSweepTests(MlbTestBase):
    """以含 <script>、"onmouseover=、& 的 fixture 產生所有頁面，輸出中不得出現未跳脫的標籤。"""

    BAD = ("<script>alert('XSS_MARKER')</script>", "<img", '" onmouseover', "<b>Durham</b>", "<script>alert")

    # 渲染層自己產生的隊徽 <img>（網址只含整數 id）是合法標籤，檢查前先移除
    LOGO_IMG = re.compile(r'<img src="https://www\.mlbstatic\.com/team-logos/[a-z-]+/\d+\.svg" width="\d+" height="\d+" alt="[^"<>]*" loading="lazy">')
    NEWS_IMG = re.compile(r'<img class="lead-img" src="https://img\.mlbstatic\.com/[^"<>\s]*" alt="[^"<>]*" loading="lazy">')

    def assert_clean(self, html, allow_shell_script=True):
        if allow_shell_script:
            # 頁面殼自己的搜尋 <script> 是唯一允許的；先移除它再檢查
            html = re.sub(r"<script>\s*\(function \(\) \{.*?</script>", "", html, flags=re.S)
        html = self.LOGO_IMG.sub("", html)
        html = self.NEWS_IMG.sub("", html)
        for bad in self.BAD:
            self.assertNotIn(bad, html)
        self.assertNotIn("<script", html)

    def test_season_page(self):
        self.api.news = "news_rss.xml"
        _, html = self.generate("schedule_xss", transactions="transactions_xss")
        self.assert_clean(html)
        self.assertIn('aria-labelledby="news-h"', html)
        self.assertIn("Evil &lt;script&gt;", html)
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
        self.api.news = "news_rss.xml"
        self.run_main("schedule_empty")
        html = self.read_report()
        self.assert_clean(html)
        self.assertIn("&lt;script&gt;alert(&#x27;XSS_MARKER&#x27;)&lt;/script&gt;", html)
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
        with mock.patch("mlb_daily.net.fetch_json", side_effect=lambda url, **k:
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


class FetchTextTests(unittest.TestCase):
    """fetch_text 本身：重試行為與回傳型別（urlopen 以 mock 取代，不連網）。"""

    class FakeResponse:
        def __init__(self, data):
            self.data = data

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def test_returns_text(self):
        with mock.patch("urllib.request.urlopen", return_value=self.FakeResponse("héllo".encode("utf-8"))) as m:
            self.assertEqual(fetch_mlb.fetch_text("https://example.test/x"), "héllo")
        self.assertTrue(m.call_args[0][0].get_header("User-agent").startswith("Mozilla/5.0"))

    def test_retries_then_returns_none(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")) as m, \
                mock.patch.object(fetch_mlb.time, "sleep") as sl, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(fetch_mlb.fetch_text("https://example.test/x", retries=3, delay=5))
        self.assertEqual(m.call_count, 3)
        self.assertEqual(sl.call_count, 2)

    def test_retry_then_success(self):
        with mock.patch("urllib.request.urlopen", side_effect=[OSError("boom"), self.FakeResponse(b"ok")]), \
                mock.patch.object(fetch_mlb.time, "sleep"), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fetch_mlb.fetch_text("https://example.test/x"), "ok")


class DailyNewsTests(MlbTestBase):
    def fetch(self, date=DATE, **kw):
        self.api.news = "news_rss.xml"
        with contextlib.redirect_stderr(io.StringIO()):
            return fetch_mlb.fetch_daily_news(date, **kw)

    def test_only_report_day_in_taipei_time_sorted_newest_first(self):
        news = self.fetch()
        titles = [n["title"] for n in news]
        self.assertEqual(len(news), 3)  # 當日 4 則，其中 1 則 link 不安全被捨棄
        self.assertEqual(titles[0], "Hall of Fame announces 2027 ballot")
        self.assertTrue(titles[1].startswith("Evil <script>"))  # 解析後為原文（渲染時才 escape）
        self.assertEqual(titles[2], "Midnight story without image")
        times = [n["published"] for n in news]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(news[0]["published"].strftime("%H:%M"), "23:59")
        self.assertEqual(news[2]["published"].strftime("%H:%M"), "00:00")

    def test_timezone_boundaries(self):
        """台北 11/15 = UTC 11/14 16:00 ~ 11/15 15:59：15:59Z 算當日、16:00Z 算隔日。"""
        titles = [n["title"] for n in self.fetch()]
        self.assertNotIn("Previous day story (out of range)", titles)  # 11/14 15:59Z
        self.assertNotIn("Next day story (out of range)", titles)  # 11/15 16:00Z
        self.assertIn("Midnight story without image", titles)  # 11/14 16:00Z
        self.assertIn("Hall of Fame announces 2027 ballot", titles)  # 11/15 15:59Z
        self.assertEqual([n["title"] for n in self.fetch("2026-11-14")], ["Previous day story (out of range)"])
        self.assertEqual([n["title"] for n in self.fetch("2026-11-16")], ["Next day story (out of range)"])

    def test_limit(self):
        limited = self.fetch(limit=2)
        self.assertEqual(len(limited), 2)
        self.assertEqual(limited[0]["title"], "Hall of Fame announces 2027 ballot")

    def test_unsafe_link_item_is_dropped(self):
        news = self.fetch()
        self.assertFalse(any("javascript" in n["link"] for n in news))
        self.assertNotIn("Unsafe link story", [n["title"] for n in news])

    def test_unsafe_image_becomes_none_and_missing_image_is_none(self):
        by_title = {n["title"][:4]: n for n in self.fetch()}
        self.assertTrue(by_title["Hall"]["image"].startswith("https://img.mlbstatic.com/"))
        self.assertIsNone(by_title["Evil"]["image"])  # http://evil.example.com
        self.assertIsNone(by_title["Midn"]["image"])  # 無 <image>

    def test_safe_url_rules(self):
        ok = fetch_mlb._safe_mlb_url
        self.assertEqual(ok("https://www.mlb.com/news/x"), "https://www.mlb.com/news/x")
        self.assertEqual(ok("https://img.mlbstatic.com/a"), "https://img.mlbstatic.com/a")
        self.assertEqual(ok("https://x.mlbstatic.com/a"), "https://x.mlbstatic.com/a")
        for bad in ("http://www.mlb.com/x", "javascript:alert(1)", "https://evil.com/x", "https://www.mlb.com.evil.com/x",
                    "https://evilmlbstatic.com/x", "https://user@www.mlb.com/x", "https://www.mlb.com:8443/x",
                    "//www.mlb.com/x", "https://www.mlb.com/a b", "", None, 5):
            self.assertIsNone(ok(bad), bad)

    def test_fetch_failure_returns_none(self):
        self.api.news = None
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIsNone(fetch_mlb.fetch_daily_news(DATE))

    def test_malformed_xml_returns_none(self):
        for bad in ("<rss><channel><item>", "not xml at all", "<html><body>blocked</body></html>"):
            self.api.news_text = bad
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertIsNone(fetch_mlb.fetch_daily_news(DATE), bad)

    def test_success_with_zero_items_is_empty_list(self):
        self.api.news_text = '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
        self.assertEqual(fetch_mlb.fetch_daily_news(DATE), [])
        self.assertEqual(self.fetch("2026-12-25"), [])

    def test_bad_item_does_not_break_others(self):
        self.api.news_text = (
            '<rss version="2.0"><channel>'
            "<item><title>No date</title><link>https://www.mlb.com/news/a</link></item>"
            "<item><title>Bad date</title><link>https://www.mlb.com/news/b</link><pubDate>yesterday-ish</pubDate></item>"
            "<item><link>https://www.mlb.com/news/c</link><pubDate>Sun, 15 Nov 2026 01:00:00 GMT</pubDate></item>"
            "<item><title>Good</title><link>https://www.mlb.com/news/d</link><pubDate>Sun, 15 Nov 2026 01:00:00 GMT</pubDate></item>"
            "</channel></rss>")
        self.assertEqual([n["title"] for n in fetch_mlb.fetch_daily_news(DATE)], ["Good"])


class NewsRenderTests(MlbTestBase):
    def news(self):
        self.api.news = "news_rss.xml"
        return fetch_mlb.fetch_daily_news(DATE)

    def test_season_section_structure(self):
        out = fetch_mlb.render_news_section(self.news(), "season")
        self.assertIn('aria-labelledby="news-h"', out)
        self.assertIn('<span class="count">3 則</span>', out)
        self.assertIn('<ol class="news">', out)
        self.assertNotIn("lead-story", out)
        self.assertIn('<time datetime="2026-11-15T23:59">23:59</time>', out)
        self.assertIn('target="_blank" rel="noopener">Hall of Fame announces 2027 ballot</a>', out)
        self.assertIn("MLB.com · 11/15 23:59", out)
        self.assertEqual(out.count("<li>"), 3)

    def test_offseason_first_item_is_lead_story_with_image(self):
        out = fetch_mlb.render_news_section(self.news(), "offseason")
        self.assertIn('<article class="lead-story">', out)
        self.assertIn('<p class="kicker">頭條 · 11/15 23:59</p>', out)
        self.assertIn('<img class="lead-img" src="https://img.mlbstatic.com/mlb-images/image/upload/t_16x9/t_w1024/mlb/hof"'
                      ' alt="Hall of Fame announces 2027 ballot" loading="lazy">', out)
        self.assertIn('<p class="src">MLB.com</p>', out)
        self.assertIn('<ol class="news is-two-col">', out)
        self.assertEqual(out.count("<li>"), 2)  # 頭條不重複出現在清單
        self.assertEqual(out.count("Hall of Fame announces 2027 ballot</a>"), 1)

    def test_lead_without_image_has_no_img_tag(self):
        item = {"title": "T", "link": "https://www.mlb.com/news/t", "image": None,
                "published": fetch_mlb.datetime(2026, 11, 15, 8, 0, tzinfo=fetch_mlb.TAIPEI_TZ)}
        out = fetch_mlb.render_news_section([item], "offseason")
        self.assertNotIn("<img", out)
        self.assertNotIn("<ol", out)
        self.assertIn('<span class="count">1 則</span>', out)

    def test_none_renders_nothing_and_empty_list_shows_empty_state(self):
        self.assertEqual(fetch_mlb.render_news_section(None, "season"), "")
        self.assertEqual(fetch_mlb.render_news_section(None, "offseason"), "")
        out = fetch_mlb.render_news_section([], "season")
        self.assertIn('<p class="section-empty">當日無新聞。</p>', out)
        self.assertIn('<span class="count">0 則</span>', out)

    def test_title_is_escaped(self):
        out = fetch_mlb.render_news_section(self.news(), "season")
        self.assertNotIn("<script>", out)
        self.assertIn("Evil &lt;script&gt;alert(&#x27;XSS_MARKER&#x27;)&lt;/script&gt; &amp; Son signs for &quot;free&quot; agency", out)
        self.assertIn('href="https://www.mlb.com/news/evil-son?a=1&amp;b=2"', out)


class NewsPageTests(MlbTestBase):
    def test_season_page_with_news(self):
        self.api.news = "news_rss.xml"
        meta, html = self.generate("schedule_normal")
        self.assertEqual(meta["news_count"], 3)
        self.assertIn("今日新聞", html)
        self.assertLess(html.index('aria-labelledby="news-h"'), html.index('aria-labelledby="games-h"'))
        # 賽季總覽仍只有總場數與延賽，不加新聞格
        overview = html[html.index('<dl class="overview">'):html.index("</dl>")]
        self.assertNotIn("新聞", overview)
        self.assertIn("總場數", overview)

    def test_season_news_fetch_failure_no_section_no_metadata_key(self):
        self.api.news = None
        meta, html = self.generate("schedule_normal")
        self.assertNotIn("news_count", meta)
        self.assertNotIn('aria-labelledby="news-h"', html)
        self.assertNotIn("今日新聞", html)

    def test_season_zero_news_shows_empty_state(self):
        self.api.news_text = '<rss version="2.0"><channel><title>t</title></channel></rss>'
        meta, html = self.generate("schedule_normal")
        self.assertEqual(meta["news_count"], 0)
        self.assertIn("當日無新聞", html)
        self.assertIn('<span class="count">0 則</span>', html)

    def test_offseason_page_with_news_lead_and_summary(self):
        self.api.news = "news_rss.xml"
        self.api.transactions = "transactions_default"
        self.assertEqual(self.run_main("schedule_empty"), 0)
        meta = self.read_metadata()[DATE]
        self.assertEqual(meta["type"], "offseason")
        self.assertEqual(meta["news_count"], 3)
        self.assertEqual(meta["transactions_count"], 4)
        html = self.read_report()
        self.assertIn('<article class="lead-story">', html)
        self.assertIn("<div><dt>新聞</dt><dd>3 則</dd></div>", html)
        self.assertIn("<div><dt>人事異動</dt><dd>4 筆</dd></div>", html)

    def test_offseason_news_fetch_failure(self):
        self.api.news = None
        self.run_main("schedule_empty")
        meta = self.read_metadata()[DATE]
        self.assertNotIn("news_count", meta)
        html = self.read_report()
        self.assertNotIn('aria-labelledby="news-h"', html)
        self.assertNotIn("<dt>新聞</dt>", html)

    def test_offseason_zero_news(self):
        self.api.news_text = '<rss version="2.0"><channel><title>t</title></channel></rss>'
        self.run_main("schedule_empty")
        self.assertEqual(self.read_metadata()[DATE]["news_count"], 0)
        html = self.read_report()
        self.assertIn("當日無新聞", html)
        self.assertIn("<div><dt>新聞</dt><dd>0 則</dd></div>", html)

    def test_index_shows_news_count_for_offseason(self):
        self.api.news = "news_rss.xml"
        self.run_main("schedule_empty")
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertIn("新聞", index)
        self.assertIn("3 則", index)


class MajorLeagueTransactionTests(MlbTestBase):
    def txs(self, payload):
        self.api.transactions = None
        with mock.patch("mlb_daily.net.fetch_json", side_effect=lambda url, **k:
                               payload if "/transactions" in url else self.api.fetch(url)):
            return fetch_mlb.fetch_daily_transactions(DATE)

    def tx(self, desc, to=None, frm=None):
        out = {"description": desc, "person": {"id": 1, "fullName": "X"}}
        if to is not None:
            out["toTeam"] = {"id": to}
        if frm is not None:
            out["fromTeam"] = {"id": frm}
        return out

    def test_fixture_rules(self):
        self.api.transactions = "transactions_default"
        descs = [t["description"] for t in fetch_mlb.fetch_daily_transactions(DATE)]
        self.assertEqual(len(descs), 4)
        self.assertTrue(any("Jose Test" in d for d in descs))  # 大聯盟 toTeam，自小聯盟 fromTeam 召回
        self.assertTrue(any("Release Guy" in d for d in descs))  # 只有 fromTeam 為大聯盟（REL）
        self.assertFalse(any("Winter Ball" in d for d in descs))  # 冬季聯盟指派
        self.assertFalse(any("Farm Hand" in d for d in descs))  # 小聯盟球隊之間
        self.assertFalse(any("Nowhere Man" in d for d in descs))  # 兩隊皆無

    def test_rules(self):
        out = self.txs({"transactions": [
            self.tx("MLB trade A", to=147, frm=111),
            self.tx("Winter assign B", to=696),
            self.tx("Minor to minor C", to=533, frm=531),
            self.tx("Only from MLB D", frm=119),
            self.tx("Only to MLB E", to=119),
            self.tx("No team F"),
            self.tx("Weird team id G", to="abc"),
        ]})
        self.assertEqual([t["description"] for t in out], ["MLB trade A", "Only from MLB D", "Only to MLB E"])

    def test_assigned_is_not_treated_as_signed(self):
        out = self.txs({"transactions": [
            self.tx("Winter assigned RHP A to Tiburones de La Guaira.", to=696),
            self.tx("Seattle Mariners assigned RHP B outright to Triple-A Tacoma.", to=136, frm=136),
            self.tx("Minor assigned C", to=533, frm=531),
        ]})
        self.assertEqual([t["description"] for t in out],
                         ["Seattle Mariners assigned RHP B outright to Triple-A Tacoma."])

    def test_signed_word_boundary_and_minor_league(self):
        out = self.txs({"transactions": [
            self.tx("Yankees signed RHP Plain.", to=147),  # 仍被 signed 規則排除
            self.tx("Yankees resigned RHP Re to a 2-year deal.", to=147),  # resigned 非單字 signed，不套 signed 規則
            self.tx("Yankees signed RHP M to a minor league contract.", to=147),
            self.tx("Yankees signed RHP Q to a Minor League deal.", to=147),
        ]})
        self.assertEqual([t["description"] for t in out], ["Yankees resigned RHP Re to a 2-year deal."])

    def test_non_mlb_duplicate_does_not_shadow_mlb_entry(self):
        out = self.txs({"transactions": [
            self.tx("Same text", to=696),
            self.tx("Same text", to=147),
        ]})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["to_team_id"], 147)

    def test_filters_still_apply_before_team_rule(self):
        out = self.txs({"transactions": [
            self.tx("Yankees signed RHP Z to a minor league contract.", to=147),
            self.tx("Yankees signed RHP Plain.", to=147),
            self.tx("Yankees signed free agent RHP Ok.", to=147),
        ]})
        self.assertEqual([t["description"] for t in out], ["Yankees signed free agent RHP Ok."])


class NotifyContentTests(MlbTestBase):
    """--notify-only 的休賽季無內容略過，與 send_discord_notification 的文字。"""

    def setUp(self):
        self._real_send = fetch_mlb.send_discord_notification  # 基底 setUp 會以 mock 取代它
        super().setUp()

    def run_notify_only(self, entry):
        (self.tmp / "reports" / "metadata.json").write_text(
            json.dumps({DATE: entry}, ensure_ascii=False), encoding="utf-8")
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", DATE, "--notify-only"]), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code, out.getvalue()
        return None, out.getvalue()

    def entry(self, **kw):
        base = {"date": DATE, "total_games": 0, "postponed": 0, "summary": "s", "type": "offseason"}
        base.update(kw)
        return base

    def test_offseason_empty_not_sent(self):
        for e in (self.entry(news_count=0, transactions_count=0), self.entry(transactions_count=0), self.entry()):
            self.discord.reset_mock()
            code, out = self.run_notify_only(e)
            self.assertIsNone(code)
            self.discord.assert_not_called()
            self.assertIn("不發送", out)

    def test_offseason_with_content_sent(self):
        for e in (self.entry(news_count=2, transactions_count=0), self.entry(news_count=0, transactions_count=1)):
            self.discord.reset_mock()
            self.run_notify_only(e)
            self.discord.assert_called_once_with(e)

    def run_main_empty_day(self, **meta_kw):
        self.api.schedule = "schedule_empty"
        self.api.transactions = meta_kw.get("tx", "transactions_empty")
        self.api.news = meta_kw.get("news")
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", DATE]),                 contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                fetch_mlb.main()
        self.assertEqual(cm.exception.code, 0)
        return out.getvalue()

    def test_main_normal_path_skips_empty_offseason(self):
        out = self.run_main_empty_day()  # 新聞抓取失敗且無異動
        self.discord.assert_not_called()
        self.assertIn("不發送", out)
        self.assertTrue((self.tmp / "reports" / "2026" / "11" / f"{DATE}.html").exists())

    def test_main_normal_path_sends_offseason_with_content(self):
        self.run_main_empty_day(news="news_rss.xml")
        self.discord.assert_called_once()
        self.assertEqual(self.discord.call_args[0][0]["news_count"], 3)
        self.discord.reset_mock()
        self.run_main_empty_day(tx="transactions_default")
        self.discord.assert_called_once()

    def test_season_always_sent(self):
        e = self.entry(type="season", total_games=3, news_count=0, transactions_count=0)
        self.run_notify_only(e)
        self.discord.assert_called_once_with(e)

    def send(self, entry):
        """呼叫真正的 send_discord_notification，攔下 urlopen 的 request，回傳 embed。"""
        os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.test/webhook"
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

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), contextlib.redirect_stdout(io.StringIO()):
            self._real_send(entry)
        self.assertEqual(sent["req"].full_url, "https://discord.test/webhook")
        return json.loads(sent["req"].data.decode("utf-8"))["embeds"][0]

    def test_offseason_text(self):
        embed = self.send(self.entry(news_count=3, transactions_count=4))
        self.assertEqual(embed["description"], "休賽季快報：新聞 3 則、人事異動 4 筆")
        self.assertNotIn("昨日無安排", json.dumps(embed, ensure_ascii=False))
        self.assertEqual(embed["fields"], [])
        self.assertIn("/reports/2026/11/2026-11-15.html", embed["url"])

    def test_offseason_text_omits_missing_fields(self):
        embed = self.send(self.entry(transactions_count=2))
        self.assertEqual(embed["description"], "休賽季快報：人事異動 2 筆")
        embed = self.send(self.entry(news_count=5))
        self.assertEqual(embed["description"], "休賽季快報：新聞 5 則")

    def test_season_text_adds_news_line_only_when_present(self):
        base = {"date": DATE, "total_games": 2, "postponed": 0, "summary": "摘要", "type": "season"}
        embed = self.send(dict(base, news_count=7))
        self.assertIn("新聞 7 則", embed["description"])
        self.assertEqual(len(embed["fields"]), 2)
        embed = self.send(base)
        self.assertNotIn("新聞", embed["description"])


# ---------------------------------------------------------------- Gemini 翻譯
def gemini_response(translations):
    """依文件 Interactions 回應結構包裝：steps[].model_output.content[].text。"""
    return {"id": "i1", "status": "completed", "steps": [
        {"type": "model_output", "content": [{"type": "text", "text": json.dumps({"translations": translations})}]}]}


def request_items(payload):
    return json.loads(payload["input"].split("輸入：\n", 1)[1])


def zh_echo(payload):
    """假模型：每則回「中文N」並附上 keep 字串（保留英文姓名）。"""
    return gemini_response([f"中文{it['id']} " + " ".join(it["keep"]) for it in request_items(payload)])


class PostJsonTests(unittest.TestCase):
    def test_retry_until_exhausted_then_none_and_no_key_in_message(self):
        import urllib.error
        err = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")) as m, \
                mock.patch.object(fetch_mlb.time, "sleep"), contextlib.redirect_stderr(err):
            self.assertIsNone(fetch_mlb.post_json("https://x.test/a", {}, {"x-goog-api-key": "SECRETKEY"}))
        self.assertEqual(m.call_count, 4)
        self.assertIn("attempt 4/4", err.getvalue())
        self.assertNotIn("SECRETKEY", err.getvalue())
        self.assertNotIn("x.test", err.getvalue())

    def test_success_returns_dict(self):
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return b'{"a": 1}'
        with mock.patch("urllib.request.urlopen", return_value=R()):
            self.assertEqual(fetch_mlb.post_json("https://x.test", {}, {}), {"a": 1})


class _OkResp:
    def __init__(self, obj): self.obj = obj
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self.obj).encode()


def http_err(code, retry_after=None):
    import urllib.error
    hdrs = {"Retry-After": retry_after} if retry_after is not None else {}
    body = json.dumps({"error": {"status": "UNAVAILABLE", "message": "high demand"}}).encode()
    return urllib.error.HTTPError("https://x.test", code, "err", hdrs, io.BytesIO(body))


class PostJsonRetryTests(unittest.TestCase):
    def call(self, effects):
        with mock.patch("urllib.request.urlopen", side_effect=effects) as m, \
                mock.patch.object(fetch_mlb.time, "sleep") as sl, contextlib.redirect_stderr(io.StringIO()):
            res = fetch_mlb.post_json("https://x.test", {}, {})
        return res, m, sl

    def test_503_twice_then_success(self):
        res, m, sl = self.call([http_err(503), http_err(503), _OkResp({"a": 1})])
        self.assertEqual(res, {"a": 1})
        self.assertEqual(m.call_count, 3)
        self.assertEqual([c.args[0] for c in sl.call_args_list], [5, 15])
        self.assertFalse(fetch_mlb.LAST_POST["retryable"])

    def test_503_forever_exhausts_four_attempts(self):
        res, m, sl = self.call([http_err(503)] * 4)
        self.assertIsNone(res)
        self.assertEqual(m.call_count, 4)
        self.assertEqual([c.args[0] for c in sl.call_args_list], [5, 15, 45])
        self.assertTrue(fetch_mlb.LAST_POST["retryable"])

    def test_non_retryable_4xx_no_retry(self):
        for code in (400, 401, 403, 404):
            res, m, sl = self.call([http_err(code)])
            self.assertIsNone(res)
            self.assertEqual(m.call_count, 1, code)
            sl.assert_not_called()
            self.assertFalse(fetch_mlb.LAST_POST["retryable"])

    def test_retry_after_used_and_capped(self):
        _, _, sl = self.call([http_err(429, "30"), _OkResp({})])
        self.assertEqual(sl.call_args_list[0].args[0], 30)
        _, _, sl = self.call([http_err(429, "500"), _OkResp({})])
        self.assertEqual(sl.call_args_list[0].args[0], 120)
        _, _, sl = self.call([http_err(429, "1"), _OkResp({})])
        self.assertEqual(sl.call_args_list[0].args[0], 5)
        _, _, sl = self.call([http_err(429, "Wed, 21 Oct 2026 07:28:00 GMT"), _OkResp({})])
        self.assertEqual(sl.call_args_list[0].args[0], 5)

    def test_network_errors_retry(self):
        import urllib.error, socket
        for exc in (urllib.error.URLError("x"), TimeoutError(), socket.timeout(), ConnectionResetError()):
            res, m, sl = self.call([exc, _OkResp({"ok": 1})])
            self.assertEqual(res, {"ok": 1})
            self.assertEqual(m.call_count, 2)


class TranslateFallbackTests(MlbTestBase):
    def setUp(self):
        super().setUp()
        os.environ["GEMINI_API_KEY"] = "SECRETKEY"
        os.environ.pop("GEMINI_FALLBACK_MODEL", None)
        os.environ.pop("GEMINI_MODEL", None)
        self.addCleanup(os.environ.pop, "GEMINI_FALLBACK_MODEL", None)

    def run_apply(self, effects_by_model):
        """effects_by_model: {模型: 依序的 urlopen 效果 list}；成功效果為 'ok'。"""
        used = []

        def fake(req, timeout=30):
            payload = json.loads(req.data.decode())
            used.append(payload["model"])
            eff = effects_by_model[payload["model"]].pop(0)
            if eff == "ok":
                return _OkResp(zh_echo(payload))
            raise eff
        out = io.StringIO()
        news = [{"title": "a"}, {"title": "b"}]
        with mock.patch("urllib.request.urlopen", side_effect=fake), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            ok = fetch_mlb.apply_translations(news, [], [])
        return ok, out.getvalue(), used, news

    def test_fallback_model_used_after_primary_503(self):
        ok, out, used, news = self.run_apply({
            "gemini-3.5-flash-lite": [http_err(503)] * 4, "gemini-3.6-flash": ["ok"]})
        self.assertTrue(ok)
        self.assertEqual(used, ["gemini-3.5-flash-lite"] * 4 + ["gemini-3.6-flash"])
        self.assertIn("title_zh", news[0])
        self.assertIn("Translation: 成功 2/2 則（模型 gemini-3.6-flash，主模型 gemini-3.5-flash-lite 失敗後改用）", out)

    def test_both_fail_falls_back_to_original(self):
        ok, out, used, news = self.run_apply({
            "gemini-3.5-flash-lite": [http_err(503)] * 4, "gemini-3.6-flash": [http_err(503)] * 4})
        self.assertFalse(ok)
        self.assertEqual(len(used), 8)
        self.assertNotIn("title_zh", news[0])
        self.assertIn("Translation: 失敗，使用原文", out)

    def test_non_retryable_does_not_use_fallback(self):
        for code in (400, 401, 403, 404):
            ok, out, used, _ = self.run_apply({"gemini-3.5-flash-lite": [http_err(code)], "gemini-3.6-flash": ["ok"]})
            self.assertFalse(ok)
            self.assertEqual(used, ["gemini-3.5-flash-lite"])

    def test_empty_env_disables_fallback(self):
        os.environ["GEMINI_FALLBACK_MODEL"] = ""
        ok, _, used, _ = self.run_apply({"gemini-3.5-flash-lite": [http_err(503)] * 4})
        self.assertFalse(ok)
        self.assertEqual(len(used), 4)

    def test_same_model_not_repeated_and_override(self):
        os.environ["GEMINI_FALLBACK_MODEL"] = "gemini-3.5-flash-lite"
        ok, _, used, _ = self.run_apply({"gemini-3.5-flash-lite": [http_err(503)] * 4})
        self.assertEqual(len(used), 4)
        os.environ["GEMINI_FALLBACK_MODEL"] = "other-model"
        ok, out, used, _ = self.run_apply({"gemini-3.5-flash-lite": [http_err(503)] * 4, "other-model": ["ok"]})
        self.assertTrue(ok)
        self.assertEqual(used[-1], "other-model")


class TranslateTextsTests(MlbTestBase):
    def setUp(self):
        super().setUp()
        os.environ["GEMINI_API_KEY"] = "SECRETKEY"

    def items(self, n=2):
        return [{"text": f"Text {i} Aaron Judge", "keep": ["Aaron Judge"]} for i in range(n)]

    def test_success(self):
        with mock.patch("mlb_daily.net.post_json", side_effect=lambda u, p, h, timeout=30: zh_echo(p)):
            self.assertEqual(fetch_mlb.translate_texts(self.items()), ["中文0 Aaron Judge", "中文1 Aaron Judge"])

    def test_no_key_does_not_call(self):
        os.environ.pop("GEMINI_API_KEY")
        with mock.patch("mlb_daily.net.post_json") as m:
            self.assertIsNone(fetch_mlb.translate_texts(self.items()))
        m.assert_not_called()

    def test_empty_items_does_not_call(self):
        with mock.patch("mlb_daily.net.post_json") as m:
            self.assertIsNone(fetch_mlb.translate_texts([]))
        m.assert_not_called()

    def test_length_mismatch_returns_none(self):
        with mock.patch("mlb_daily.net.post_json", return_value=gemini_response(["只有一則 Aaron Judge"])):
            self.assertIsNone(fetch_mlb.translate_texts(self.items()))

    def test_non_json_text_returns_none(self):
        resp = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "not json"}]}]}
        with mock.patch("mlb_daily.net.post_json", return_value=resp):
            self.assertIsNone(fetch_mlb.translate_texts(self.items()))

    def test_post_failure_returns_none(self):
        with mock.patch("mlb_daily.net.post_json", return_value=None):
            self.assertIsNone(fetch_mlb.translate_texts(self.items()))

    def test_missing_keep_uses_original_for_that_item_only(self):
        resp = gemini_response(["好 Aaron Judge", "遺失姓名", ""])
        items = self.items(3)
        with mock.patch("mlb_daily.net.post_json", return_value=resp):
            out = fetch_mlb.translate_texts(items)
        self.assertEqual(out, ["好 Aaron Judge", items[1]["text"], items[2]["text"]])

    def test_batching_over_60(self):
        with mock.patch("mlb_daily.net.post_json", side_effect=lambda u, p, h, timeout=30: zh_echo(p)) as m:
            out = fetch_mlb.translate_texts(self.items(130))
        self.assertEqual(m.call_count, 3)
        self.assertEqual([len(request_items(c.args[1])) for c in m.call_args_list], [60, 60, 10])
        self.assertEqual(len(out), 130)

    def test_one_failed_batch_falls_back_for_that_batch_only(self):
        calls = []

        def fake(u, p, h, timeout=30):
            calls.append(1)
            return None if len(calls) == 1 else zh_echo(p)
        items = self.items(70)
        with mock.patch("mlb_daily.net.post_json", side_effect=fake):
            out = fetch_mlb.translate_texts(items)
        self.assertEqual(out[:60], [it["text"] for it in items[:60]])
        self.assertTrue(out[60].startswith("中文0"))

    def test_request_shape_key_in_header_only_no_sampling_params(self):
        with mock.patch("mlb_daily.net.post_json", side_effect=lambda u, p, h, timeout=30: zh_echo(p)) as m:
            fetch_mlb.translate_texts(self.items(), [(147, "New York Yankees")])
        url, payload, headers = m.call_args.args[:3]
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/interactions")
        self.assertEqual(headers["x-goog-api-key"], "SECRETKEY")
        self.assertNotIn("SECRETKEY", url)
        self.assertNotIn("SECRETKEY", json.dumps(payload))
        dumped = json.dumps(payload)
        for bad in ("temperature", "top_p", "top_k"):
            self.assertNotIn(bad, dumped)
        self.assertEqual(payload["model"], "gemini-3.5-flash-lite")
        self.assertEqual(payload["response_format"]["mime_type"], "application/json")
        self.assertIn("New York Yankees -> 紐約洋基", payload["system_instruction"])
        self.assertIn("Yankees -> 洋基", payload["system_instruction"])

    def test_model_env_override(self):
        os.environ["GEMINI_MODEL"] = "gemini-x"
        with mock.patch("mlb_daily.net.post_json", side_effect=lambda u, p, h, timeout=30: zh_echo(p)) as m:
            fetch_mlb.translate_texts(self.items())
        self.assertEqual(m.call_args.args[1]["model"], "gemini-x")


class TranslatedPageTests(MlbTestBase):
    BAD, LOGO_IMG, NEWS_IMG = XssSweepTests.BAD, XssSweepTests.LOGO_IMG, XssSweepTests.NEWS_IMG
    assert_clean = XssSweepTests.assert_clean

    def setUp(self):
        super().setUp()
        os.environ["GEMINI_API_KEY"] = "SECRETKEY"
        self.api.news = "news_rss.xml"
        self.api.transactions = "transactions_default"
        self.seen = []

    def fake_post(self, mapping=None):
        def fake(u, p, h, timeout=30):
            items = request_items(p)
            self.seen.extend(it["text"] for it in items)
            outs = []
            for it in items:
                tr = (mapping or {}).get(it["text"])
                outs.append(tr if tr is not None else f"中文譯文{it['id']} " + " ".join(it["keep"]))
            return gemini_response(outs)
        return mock.patch("mlb_daily.net.post_json", side_effect=fake)

    def test_season_page_translated(self):
        with self.fake_post() as m:
            meta, html = self.generate("schedule_normal", transactions="transactions_default")
        self.assertEqual(m.call_count, 1)  # 整頁只呼叫一次
        self.assertTrue(meta["translated"])
        self.assertIn("中文譯文", html)
        for en in ("Yankees top Red Sox 5-3", "Hall of Fame announces 2027 ballot", "recalled RHP"):
            self.assertNotIn(en, html)
        # 人事異動：保留英文姓名且仍有 Savant 連結
        self.assertIn('href="https://baseballsavant.mlb.com/savant-player/777004"', html)
        self.assertIn(">Big Name</a>", html)

    def test_offseason_page_translated(self):
        with self.fake_post() as m:
            self.assertEqual(self.run_main("schedule_empty"), 0)
        # 首頁「本週人事異動精選」與戰報有相同 description，直接沿用戰報譯文，不再呼叫 Gemini
        self.assertEqual(m.call_count, 1)
        self.assertTrue(self.read_metadata()[DATE]["translated"])
        html = self.read_report()
        self.assertIn("中文譯文", html)
        self.assertNotIn("Hall of Fame announces 2027 ballot", html)
        self.assertNotIn("signed free agent", html)
        self.assertIn('savant-player/777004"', html)
        self.assertIn(">Big Name</a>", html)

    def test_translation_with_script_is_escaped(self):
        evil = "<script>alert('XSS_MARKER')</script> <img src=x onerror=1>"
        with self.fake_post({"Hall of Fame announces 2027 ballot": evil}):
            _, html = self.generate("schedule_normal", transactions="transactions_default")
        self.assertIn("&lt;script&gt;alert(&#x27;XSS_MARKER&#x27;)&lt;/script&gt;", html)
        self.assert_clean(html)

    def test_game_recap_translated(self):
        with self.fake_post():
            _, html = self.generate("schedule_normal")
        self.assertNotIn("Yankees top Red Sox", html)

    def test_translation_failure_shows_english_and_flag_false(self):
        with mock.patch("mlb_daily.net.post_json", return_value=None):
            meta, html = self.generate("schedule_normal", transactions="transactions_default")
        self.assertFalse(meta["translated"])
        self.assertIn("Hall of Fame announces 2027 ballot", html)
        self.assertIn("Yankees top Red Sox 5-3", html)
        self.assertIn("signed free agent RHP", html)

    def test_no_key_metadata_false_and_no_call(self):
        os.environ.pop("GEMINI_API_KEY")
        with mock.patch("mlb_daily.net.post_json") as m:
            self.run_main("schedule_empty")
        m.assert_not_called()
        self.assertFalse(self.read_metadata()[DATE]["translated"])

    def test_fallback_headline_not_sent_for_translation(self):
        with self.fake_post():
            self.generate("schedule_normal")
        self.assertFalse(any("賽事總結" in t for t in self.seen))


class TranslationDiagnosticsTests(MlbTestBase):
    def setUp(self):
        super().setUp()
        os.environ["GEMINI_API_KEY"] = "SECRETKEY"

    def test_http_error_body_message_printed_and_key_redacted(self):
        import urllib.error
        body = json.dumps({"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                     "message": "API key SECRETKEY not valid. " + "x" * 300}}).encode()
        err = urllib.error.HTTPError("https://x.test", 400, "Bad", {}, io.BytesIO(body))
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=err), \
                mock.patch.object(fetch_mlb.time, "sleep"), contextlib.redirect_stderr(out):
            self.assertIsNone(fetch_mlb.post_json("https://x.test", {}, {"x-goog-api-key": "SECRETKEY"}))
        text = out.getvalue()
        self.assertIn("HTTP 400", text)
        self.assertIn("status=INVALID_ARGUMENT", text)
        self.assertIn("API key *** not valid.", text)
        self.assertNotIn("SECRETKEY", text)
        self.assertNotIn("x" * 201, text)  # message 截斷為 200 字

    def test_http_error_non_json_body_only_code(self):
        import urllib.error
        err = urllib.error.HTTPError("https://x.test", 503, "Unavail", {}, io.BytesIO(b"<html>oops</html>"))
        out = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=err), \
                mock.patch.object(fetch_mlb.time, "sleep"), contextlib.redirect_stderr(out):
            fetch_mlb.post_json("https://x.test", {}, {})
        self.assertIn("HTTP 503", out.getvalue())
        self.assertNotIn("oops", out.getvalue())

    def run_translate(self, resp):
        err = io.StringIO()
        with mock.patch("mlb_daily.net.post_json", return_value=resp), contextlib.redirect_stderr(err):
            out = fetch_mlb.translate_texts([{"text": "A", "keep": []}])
        return out, err.getvalue()

    def test_missing_text_prints_keys_and_step_types_not_content(self):
        resp = {"id": "i1", "status": "failed", "steps": [{"type": "thought", "content": "SECRET THOUGHT"}, {"type": "other"}]}
        out, err = self.run_translate(resp)
        self.assertIsNone(out)
        self.assertIn("['id', 'status', 'steps']", err)
        self.assertIn("['thought', 'other']", err)
        self.assertNotIn("SECRET THOUGHT", err)

    def test_invalid_json_and_length_mismatch_warnings(self):
        bad = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "not json"}]}]}
        _, err = self.run_translate(bad)
        self.assertIn("not valid JSON", err)
        _, err = self.run_translate(gemini_response(["a", "b"]))
        self.assertIn("expected 1, got 2", err)

    def run_apply(self, news=(), env_key=True, post=None):
        if not env_key:
            os.environ.pop("GEMINI_API_KEY")
        out = io.StringIO()
        news = [{"title": t} for t in news]
        with mock.patch("mlb_daily.net.post_json", side_effect=post or (lambda u, p, h, timeout=30: zh_echo(p))), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            fetch_mlb.apply_translations(news, [], [])
        return out.getvalue()

    def test_info_line_success(self):
        self.assertIn("Translation: 成功 2/2 則（模型 gemini-3.5-flash-lite）", self.run_apply(["a", "b"]))

    def test_info_line_partial_success_counts_only_translated(self):
        post = lambda u, p, h, timeout=30: gemini_response(["中文", ""])
        self.assertIn("Translation: 成功 1/2 則", self.run_apply(["a", "b"], post=post))

    def test_info_line_failure_and_no_key(self):
        self.assertIn("Translation: 失敗，使用原文", self.run_apply(["a"], post=lambda *a, **k: None))
        self.assertIn("Translation: 未啟用（無金鑰）", self.run_apply(["a"], env_key=False))


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


class SummarySentenceTests(MlbTestBase):
    """模組 F：首頁摘要選場（L7）、休賽季摘要、取消原因文案。"""

    def test_pick_summary_sentences_by_score_stable(self):
        entries = [(1.0, "a"), (9.0, "b"), (5.0, "c"), (9.0, "d"), (0.0, "e")]
        self.assertEqual(fetch_mlb.pick_summary_sentences(entries), ["b", "d", "c"])
        self.assertEqual(fetch_mlb.pick_summary_sentences(entries[:2]), ["b", "a"])
        self.assertEqual(fetch_mlb.pick_summary_sentences([]), [])

    def test_top_focus_score(self):
        self.assertEqual(fetch_mlb.top_focus_score([]), 0.0)
        self.assertEqual(fetch_mlb.top_focus_score([{"score": 3.5}, {"score": 12.0}]), 12.0)

    def test_L7_summary_uses_highest_scoring_games_not_first_three(self):
        g1, g2 = self.games_of("schedule_normal")
        games = [g1, g2, g1, g2]  # 順序：NYY@BOS, LAD@SF, NYY@BOS, LAD@SF
        self.api.schedule = "schedule_normal"
        with mock.patch("mlb_daily.games.top_focus_score", side_effect=[1.0, 2.0, 3.0, 4.0]),                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            meta = fetch_mlb.generate_report(DATE, games)
        matchups = re.findall(r"(\w+) @ (\w+)，比分", meta["summary"])
        # 前 3 場為 NYY, LAD, NYY；積分最高的 3 場（4、3、2）則為 LAD, NYY, LAD
        self.assertEqual(matchups, [("LAD", "SF"), ("NYY", "BOS"), ("LAD", "SF")])

    def test_offseason_summary_uses_first_three_news_titles(self):
        news = [{"title": "T1"}, {"title": "T2", "title_zh": "譯文2"}, {"title": "T3"}, {"title": "T4"}]
        self.assertEqual(fetch_mlb.build_offseason_summary(news, 5), "T1；譯文2；T3。")

    def test_offseason_summary_fallbacks(self):
        self.assertEqual(fetch_mlb.build_offseason_summary([], 4), "休賽季：人事異動 4 筆")
        self.assertEqual(fetch_mlb.build_offseason_summary(None, 4), "休賽季：人事異動 4 筆")
        self.assertEqual(fetch_mlb.build_offseason_summary([], 0), "休賽季，無重大消息")
        self.assertEqual(fetch_mlb.build_offseason_summary(None, 0), "休賽季，無重大消息")

    def test_offseason_metadata_summary_from_news_and_not_old_wording(self):
        self.api.news = "news_rss.xml"
        self.assertEqual(self.run_main("schedule_empty"), 0)
        summary = self.read_metadata()[DATE]["summary"]
        self.assertNotIn("無安排", summary)
        self.assertIn("Hall of Fame announces 2027 ballot", summary)
        self.assertTrue(summary.endswith("。"))

    def test_offseason_empty_day_summary(self):
        self.assertEqual(self.run_main("schedule_empty"), 0)
        self.assertEqual(self.read_metadata()[DATE]["summary"], "休賽季，無重大消息")

    def test_offseason_summary_is_escaped_on_index(self):
        self.api.news = "news_rss.xml"
        self.assertEqual(self.run_main("schedule_empty"), 0)
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert('XSS_MARKER')", index)
        self.assertIn("&lt;script&gt;alert(&#x27;XSS_MARKER&#x27;)&lt;/script&gt;", index)

    def test_cancelled_reason_wording(self):
        _, html = self.generate("schedule_cancelled")
        games = games_block(html)
        self.assertIn("因雨取消 (Cancelled - Rain)", games)
        self.assertNotIn("延期", games)

    def test_cancelled_reason_unknown_and_missing(self):
        game = self.games_of("schedule_cancelled")[0]
        info = fetch_mlb.get_team_info(game["teams"]["away"]["team"]["id"], game["teams"]["away"]["team"]["name"])
        for reason, expected in (("Mystery <b>", "賽事取消（原因：Mystery <b>）"), (None, "賽事取消")):
            g = json.loads(json.dumps(game))
            if reason is None:
                g["status"].pop("reason", None)
            else:
                g["status"]["reason"] = reason
            view = fetch_mlb.build_postponed_view(g, info, info)
            self.assertIn(expected + " (", view["notice"])
            self.assertNotIn("延期", view["notice"])
            # 渲染時外部字串仍會 escape
            self.assertNotIn("<b>", fetch_mlb.render_postponed_card(view))


# ---------------------------------------------------------------- 球隊名單（模組 R2）
def _split(stat, team=None):
    sp = {"stat": stat}
    if team:
        sp["team"] = {"id": team}
    return sp


def _entry(pos, stats=None, pid=1, name="Test Player", number="9"):
    """組出 roster API 單筆；stats 為 [(type, group, [splits])]。"""
    return {
        "person": {"id": pid, "fullName": name,
                   "stats": [{"type": {"displayName": t}, "group": {"displayName": g}, "splits": sp}
                             for t, g, sp in (stats or [])]},
        "jerseyNumber": number,
        "position": {"abbreviation": pos},
    }


def _pitch_stats(g, gs, **extra):
    stat = {"gamesPitched": g, "gamesStarted": gs, "inningsPitched": "10.1", "era": "3.50"}
    stat.update(extra)
    return [("season", "pitching", [_split(stat)])]


class RosterContextTests(MlbTestBase):
    def ctx(self, today, seasons="seasons_2026"):
        self.api.seasons = seasons
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = fetch_mlb.resolve_roster_context(today)
        return result, err.getvalue()

    def test_in_season_uses_active_and_current_season(self):
        ctx, _ = self.ctx(date(2026, 7, 1))
        self.assertEqual(ctx, {"rosterType": "active", "season": 2026, "rosterSeason": 2026})

    def test_season_boundaries_inclusive(self):
        self.assertEqual(self.ctx(date(2026, 3, 25))[0]["rosterType"], "active")
        self.assertEqual(self.ctx(date(2026, 10, 28))[0]["rosterType"], "active")

    def test_before_opening_day_uses_40man_and_previous_stats(self):
        ctx, _ = self.ctx(date(2026, 3, 24))
        self.assertEqual(ctx, {"rosterType": "40Man", "season": 2025, "rosterSeason": 2026})

    def test_after_postseason_uses_40man_and_current_stats(self):
        ctx, _ = self.ctx(date(2026, 10, 29))
        self.assertEqual(ctx, {"rosterType": "40Man", "season": 2026, "rosterSeason": 2026})

    def test_api_failure_falls_back_to_month_with_warning(self):
        cases = [(date(2026, 4, 1), "active", 2026), (date(2026, 10, 31), "active", 2026),
                 (date(2026, 11, 18), "40Man", 2026), (date(2026, 12, 31), "40Man", 2026),
                 (date(2026, 1, 15), "40Man", 2025), (date(2026, 3, 31), "40Man", 2025)]
        for today, roster_type, season in cases:
            ctx, err = self.ctx(today, seasons=None)
            self.assertEqual((ctx["rosterType"], ctx["season"]), (roster_type, season), today)
            self.assertIn("Warning", err)

    def test_missing_dates_falls_back(self):
        self.api.seasons_data = {"seasons": [{}]}
        ctx, err = self.ctx(date(2026, 7, 1), seasons="__inline__")
        self.assertEqual(ctx["rosterType"], "active")
        self.assertIn("Warning", err)


class RosterGroupingTests(unittest.TestCase):
    def test_starter_is_sp(self):
        p = fetch_mlb.build_player(_entry("P", _pitch_stats(30, 30)))
        self.assertEqual((p["group"], p["hitting"]), ("SP", None))
        self.assertNotIn("pitchingGroup", p)

    def test_reliever_is_rp(self):
        self.assertEqual(fetch_mlb.build_player(_entry("P", _pitch_stats(50, 10)))["group"], "RP")

    def test_half_starts_is_sp(self):
        self.assertEqual(fetch_mlb.build_player(_entry("P", _pitch_stats(10, 5)))["group"], "SP")

    def test_pitcher_without_stats_is_rp(self):
        p = fetch_mlb.build_player(_entry("P"))
        self.assertEqual((p["group"], p["pitching"], p["hitting"]), ("RP", None, None))

    def test_two_way_player_has_hitting_and_pitching_group(self):
        stats = _pitch_stats(14, 14) + [("season", "hitting", [_split({"gamesPlayed": 100, "plateAppearances": 400})])]
        p = fetch_mlb.build_player(_entry("TWP", stats))
        self.assertEqual((p["group"], p["pitchingGroup"]), ("POS", "SP"))
        self.assertIsNotNone(p["hitting"])
        self.assertIsNotNone(p["pitching"])

    def test_two_way_player_without_pitching_has_no_pitching_group(self):
        stats = [("season", "hitting", [_split({"gamesPlayed": 100, "plateAppearances": 400})])]
        p = fetch_mlb.build_player(_entry("TWP", stats))
        self.assertNotIn("pitchingGroup", p)
        self.assertIsNone(p["pitching"])

    def test_if_ut_of_are_pos(self):
        for pos in ("IF", "UT", "OF", "C", "DH"):
            p = fetch_mlb.build_player(_entry(pos))
            self.assertEqual(p["group"], "POS", pos)
            self.assertEqual(p["pos"], pos)

    def test_position_player_pitching_stats_ignored(self):
        p = fetch_mlb.build_player(_entry("LF", _pitch_stats(1, 0)))
        self.assertIsNone(p["pitching"])


class RosterStatConversionTests(unittest.TestCase):
    HIT = {"gamesPlayed": 100, "plateAppearances": 400, "avg": ".285", "obp": ".350", "slg": ".500",
           "homeRuns": 20, "stolenBases": 5, "baseOnBalls": 40, "strikeOuts": 100}

    def hitting(self, season=None, saber=None):
        stats = [("season", "hitting", [_split(dict(self.HIT, **(season or {})))])]
        if saber is not None:
            stats.append(("sabermetrics", "hitting", [_split(saber)]))
        return fetch_mlb.build_player(_entry("1B", stats))["hitting"]

    def test_strings_to_floats_and_ints(self):
        h = self.hitting()
        self.assertEqual((h["avg"], h["obp"], h["slg"]), (0.285, 0.35, 0.5))
        self.assertEqual((h["g"], h["pa"], h["hr"], h["sb"]), (100, 400, 20, 5))

    def test_placeholder_strings_become_null(self):
        h = self.hitting({"avg": "-.--", "obp": ".---", "slg": "*.**"})
        self.assertEqual((h["avg"], h["obp"], h["slg"]), (None, None, None))
        p = fetch_mlb.build_player(_entry("P", _pitch_stats(10, 0, era="-.--", whip="*.**")))
        self.assertEqual((p["pitching"]["era"], p["pitching"]["whip"]), (None, None))

    def test_missing_field_is_null(self):
        stat = dict(self.HIT)
        del stat["avg"]
        h = fetch_mlb.build_player(_entry("1B", [("season", "hitting", [_split(stat)])]))["hitting"]
        self.assertIsNone(h["avg"])

    def test_bb_k_percentage(self):
        h = self.hitting()
        self.assertEqual((h["bbPct"], h["kPct"]), (10.0, 25.0))
        h = self.hitting({"plateAppearances": 285, "baseOnBalls": 43, "strikeOuts": 85})
        self.assertEqual((h["bbPct"], h["kPct"]), (15.1, 29.8))

    def test_zero_or_missing_pa_gives_null_percentages(self):
        h = self.hitting({"plateAppearances": 0})
        self.assertEqual((h["bbPct"], h["kPct"]), (None, None))
        stat = dict(self.HIT)
        del stat["plateAppearances"]
        h = fetch_mlb.build_player(_entry("1B", [("season", "hitting", [_split(stat)])]))["hitting"]
        self.assertEqual((h["pa"], h["bbPct"], h["kPct"]), (None, None, None))

    def test_wrc_plus_integer_and_war_one_decimal(self):
        h = self.hitting(saber={"wRcPlus": 140.1, "war": 2.00024})
        self.assertEqual((h["wrcPlus"], h["war"]), (140, 2.0))
        self.assertIsInstance(h["wrcPlus"], int)
        h = self.hitting(saber={"wRcPlus": 98.55098, "war": -0.0449})
        self.assertEqual((h["wrcPlus"], h["war"]), (99, -0.0))

    def test_no_sabermetrics_gives_null_wrc_and_war(self):
        h = self.hitting()
        self.assertEqual((h["wrcPlus"], h["war"]), (None, None))

    def test_pitching_fields(self):
        stats = [("season", "pitching", [_split({
            "gamesPitched": 22, "gamesStarted": 22, "inningsPitched": "127.0", "wins": 10, "losses": 9,
            "saves": 0, "holds": 0, "era": "3.61", "whip": "1.10", "strikeOuts": 131,
            "strikeoutsPer9Inn": "9.28"})]),
            ("sabermetrics", "pitching", [_split({"fip": 3.92645, "war": 2.14725})])]
        p = fetch_mlb.build_player(_entry("P", stats))["pitching"]
        self.assertEqual(p, {"g": 22, "gs": 22, "ip": "127.0", "w": 10, "l": 9, "sv": 0, "hld": 0,
                             "era": 3.61, "whip": 1.1, "so": 131, "k9": 9.28, "fip": 3.93, "war": 2.1})
        self.assertIsInstance(p["ip"], str)

    def test_pitching_games_falls_back_to_games_played(self):
        stats = [("season", "pitching", [_split({"gamesPlayed": 8, "gamesStarted": 8})])]
        p = fetch_mlb.build_player(_entry("P", stats))
        self.assertEqual((p["pitching"]["g"], p["group"]), (8, "SP"))

    def test_no_season_data_gives_null_groups(self):
        p = fetch_mlb.build_player(_entry("2B"))
        self.assertEqual((p["hitting"], p["pitching"]), (None, None))
        p = fetch_mlb.build_player(_entry("2B", [("season", "hitting", [])]))
        self.assertIsNone(p["hitting"])

    def test_traded_player_uses_total_split(self):
        stats = [("season", "hitting", [_split(dict(self.HIT, gamesPlayed=112)),
                                         _split(dict(self.HIT, gamesPlayed=74), team=137),
                                         _split(dict(self.HIT, gamesPlayed=38), team=147)])]
        self.assertEqual(fetch_mlb.build_player(_entry("LF", stats))["hitting"]["g"], 112)

    def test_player_without_id_dropped(self):
        self.assertIsNone(fetch_mlb.build_player({"person": {"fullName": "x"}, "position": {}}))

    def test_fixture_roster_end_to_end(self):
        players = fetch_mlb.build_team_players(load_fixture("roster_147"))
        by_name = {p["name"]: p for p in players}
        self.assertEqual(by_name["Gerrit Cole"]["group"], "SP")
        self.assertEqual(by_name["Brent Headrick"]["group"], "RP")
        self.assertEqual(by_name["Luis Gil"]["group"], "RP")  # 4 GS / 9 G
        self.assertEqual(by_name["Heliot Ramos"]["hitting"]["g"], 112)
        self.assertEqual(by_name["Test Twoway"]["pitchingGroup"], "SP")
        self.assertIsNone(by_name["No Data Fielder"]["hitting"])


class RosterUrlTests(unittest.TestCase):
    def test_brackets_are_encoded(self):
        url = fetch_mlb.roster_url(147, "40Man", 2026, 2025)
        self.assertIn("/teams/147/roster?rosterType=40Man&season=2026&hydrate=", url)
        self.assertIn("type=%5Bseason,sabermetrics%5D,group=%5Bhitting,pitching%5D,season=2025", url)
        self.assertNotIn("[", url)


class RosterFlowTests(MlbTestBase):
    def run_rosters(self, *extra, date_arg="2026-11-18"):
        argv = ["fetch_mlb.py", "--rosters"] + ([] if date_arg is None else ["--date", date_arg]) + list(extra)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code, out.getvalue(), err.getvalue()
        return None, out.getvalue(), err.getvalue()

    def read_team(self, tid):
        return json.loads((self.tmp / "data" / "teams" / f"{tid}.json").read_text(encoding="utf-8"))

    def read_index(self):
        return json.loads((self.tmp / "data" / "teams" / "index.json").read_text(encoding="utf-8"))

    def snapshot(self):
        files = {}
        for sub in ("reports", "templates"):
            for f in (self.tmp / sub).rglob("*"):
                if f.is_file():
                    files[str(f.relative_to(self.tmp))] = f.read_bytes()
        files["index.html"] = (self.tmp / "index.html").read_bytes()
        return files

    def test_writes_all_teams_and_index(self):
        code, out, _ = self.run_rosters()
        self.assertEqual(code, 0)
        files = sorted(p.name for p in (self.tmp / "data" / "teams").iterdir())
        self.assertEqual(len(files), 31)
        self.assertNotIn("index.json.tmp", files)
        self.assertFalse(any(n.endswith(".tmp") for n in files))
        team = self.read_team(147)
        self.assertEqual(set(team), {"meta", "players"})
        self.assertEqual(team["meta"]["id"], 147)
        self.assertEqual(team["meta"]["abbr"], "NYY")
        self.assertEqual(team["meta"]["rosterType"], "40Man")
        self.assertEqual(team["meta"]["season"], 2026)
        self.assertRegex(team["meta"]["updatedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")
        self.assertEqual(team["players"][0]["name"], "Gerrit Cole")

    def test_index_structure_matches_sample(self):
        self.run_rosters()
        index = self.read_index()
        self.assertEqual(set(index), {"updatedAt", "rosterType", "season", "teams"})
        self.assertEqual((index["rosterType"], index["season"]), ("40Man", 2026))
        self.assertEqual(len(index["teams"]), 30)
        for t in index["teams"]:
            self.assertEqual(set(t), {"id", "abbr", "name", "color", "league", "division", "ok", "updatedAt"})
            self.assertTrue(t["ok"])
            self.assertIn(t["league"], ("AL", "NL"))
        sample = json.loads((REPO_ROOT / "docs" / "mockups" / "assets" / "teams.sample.json").read_text(encoding="utf-8"))
        sample_keys = set(sample["index"]["teams"][0])
        self.assertTrue(sample_keys <= set(index["teams"][0]))

    def test_player_structure_matches_sample(self):
        self.run_rosters()
        sample = json.loads((REPO_ROOT / "docs" / "mockups" / "assets" / "teams.sample.json").read_text(encoding="utf-8"))
        sample_players = sample["teams"]["NYY"]["players"] + sample["teams"]["LAD"]["players"]
        sample_hit = next(p["hitting"] for p in sample_players if p["hitting"])
        sample_pit = next(p["pitching"] for p in sample_players if p["pitching"])
        players = {p["name"]: p for p in self.read_team(147)["players"]}
        self.assertEqual(set(players["Aaron Judge"]["hitting"]), set(sample_hit))
        self.assertEqual(set(players["Gerrit Cole"]["pitching"]), set(sample_pit))
        base = {"id", "name", "number", "pos", "group", "hitting", "pitching"}
        self.assertEqual(set(players["Aaron Judge"]), base)
        self.assertEqual(set(players["Test Twoway"]), base | {"pitchingGroup"})

    def test_requests_use_roster_context(self):
        self.run_rosters()
        roster_calls = [u for u in self.api.calls if "/roster?" in u]
        self.assertEqual(len(roster_calls), 30)
        self.assertTrue(all("rosterType=40Man&season=2026" in u and "season=2026))" in u for u in roster_calls))

    def test_before_season_requests_previous_stats(self):
        self.run_rosters(date_arg="2026-02-10")
        url = next(u for u in self.api.calls if "/roster?" in u)
        self.assertIn("rosterType=40Man&season=2026", url)
        self.assertIn("season=2025))", url)
        self.assertEqual(self.read_index()["season"], 2025)

    def test_in_season_uses_active(self):
        self.run_rosters(date_arg="2026-07-01")
        self.assertEqual(self.read_index()["rosterType"], "active")
        self.assertTrue(all("rosterType=active" in u for u in self.api.calls if "/roster?" in u))

    def test_single_team_failure_marks_not_ok_and_writes_no_file(self):
        self.api.fail_roster = {147}
        code, _, err = self.run_rosters()
        self.assertEqual(code, 0)
        self.assertFalse((self.tmp / "data" / "teams" / "147.json").exists())
        self.assertTrue((self.tmp / "data" / "teams" / "119.json").exists())
        index = self.read_index()
        nyy = next(t for t in index["teams"] if t["id"] == 147)
        self.assertFalse(nyy["ok"])
        self.assertNotIn("updatedAt", nyy)
        self.assertEqual(sum(t["ok"] for t in index["teams"]), 29)
        self.assertIn("NYY", err)

    def test_failure_keeps_previous_file(self):
        self.run_rosters()
        before = (self.tmp / "data" / "teams" / "147.json").read_bytes()
        self.api.fail_roster = {147}
        self.run_rosters()
        self.assertEqual((self.tmp / "data" / "teams" / "147.json").read_bytes(), before)

    def test_all_failures_exit_1(self):
        self.api.fail_roster = "all"
        code, _, _ = self.run_rosters()
        self.assertEqual(code, 1)
        index = self.read_index()
        self.assertFalse(any(t["ok"] for t in index["teams"]))

    def test_malformed_roster_response_counts_as_failure(self):
        self.api.roster_override = {"unexpected": True}
        code, _, _ = self.run_rosters()
        self.assertEqual(code, 1)

    def test_does_not_touch_reports_pages_or_notify(self):
        before = self.snapshot()
        self.run_rosters()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.tmp / "reports" / "metadata.json").exists())
        self.discord.assert_not_called()
        self.assertFalse(any("/schedule" in u for u in self.api.calls))

    def test_sleeps_between_teams(self):
        self.run_rosters()
        self.assertEqual(fetch_mlb.time.sleep.call_count, 29)
        fetch_mlb.time.sleep.assert_called_with(0.5)

    def test_mutually_exclusive_flags(self):
        for flag in ("--notify-only", "--check-wait"):
            code, _, err = self.run_rosters(flag)
            self.assertEqual(code, 1, flag)
            self.assertIn("--rosters", err)
        self.assertFalse((self.tmp / "data").exists())

    def test_invalid_date_exits_1(self):
        code, _, _ = self.run_rosters(date_arg="2026/11/18")
        self.assertEqual(code, 1)

    def test_default_today_uses_taipei_date(self):
        with mock.patch("mlb_daily.timeutil.get_taipei_now",
                               return_value=datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=8)))):
            code, _, _ = self.run_rosters(date_arg=None)
        self.assertEqual(code, 0)
        self.assertEqual(self.read_index()["rosterType"], "active")


class TeamAbbrTests(MlbTestBase):
    def test_unified_abbreviations(self):
        self.assertEqual(fetch_mlb.TEAM_META[109]["abbr"], "AZ")
        self.assertEqual(fetch_mlb.TEAM_META[118]["abbr"], "KC")
        self.assertEqual(fetch_mlb.TEAM_META[133]["abbr"], "ATH")
        self.assertEqual(fetch_mlb.TEAM_META[133]["name"], "運動家")
        self.assertEqual(fetch_mlb.TEAM_NAMES_EN[133][0], "Athletics")

    def test_data_teams_includes_legacy_aliases(self):
        for tid, old in ((109, "ARI"), (118, "KCR"), (133, "OAK")):
            info = fetch_mlb.TEAM_META[tid]
            side = {"abbr": info["abbr"], "name": info["name"], "name_en": "X"}
            other = {"abbr": "NYY", "name": "紐約洋基", "name_en": "New York Yankees"}
            html = fetch_mlb._card_open({"away": side, "home": other})
            tokens = re.search(r'data-teams="([^"]*)"', html).group(1).split("|")
            self.assertIn(old, tokens)
            self.assertIn(info["abbr"], tokens)
        # 無別名的球隊不受影響
        html = fetch_mlb._card_open({"away": {"abbr": "NYY", "name": "紐約洋基", "name_en": "New York Yankees"},
                                     "home": {"abbr": "BOS", "name": "波士頓紅襪", "name_en": "Boston Red Sox"}})
        self.assertIn('data-teams="NYY|紐約洋基|New York Yankees|BOS|波士頓紅襪|Boston Red Sox"', html)

    def test_league_and_division_complete(self):
        self.assertEqual(len(fetch_mlb.TEAM_META), 30)
        counts = {}
        for meta in fetch_mlb.TEAM_META.values():
            counts[(meta["league"], meta["division"])] = counts.get((meta["league"], meta["division"]), 0) + 1
        self.assertEqual(len(counts), 6)
        self.assertTrue(all(n == 5 for n in counts.values()))
        self.assertEqual(sum(1 for m in fetch_mlb.TEAM_META.values() if m["league"] == "AL"), 15)
        self.assertEqual(sum(1 for m in fetch_mlb.TEAM_META.values() if m["league"] == "NL"), 15)
        self.assertEqual({m["division"] for m in fetch_mlb.TEAM_META.values()}, {"E", "C", "W"})

    def test_abbreviations_unique(self):
        abbrs = [m["abbr"] for m in fetch_mlb.TEAM_META.values()]
        self.assertEqual(len(abbrs), len(set(abbrs)))



class SiteNavTests(MlbTestBase):
    """R3：全站導覽列與球隊名單頁（模組 R3）。"""

    def nav_of(self, html):
        m = re.search(r'<nav class="nav-row" aria-label="頁面導覽">(.*?)</nav>', html, re.S)
        self.assertIsNotNone(m, "找不到導覽列")
        return m.group(1)

    def nav_links(self, html):
        """導覽列內 [(href, 文字, 是否 aria-current)]。"""
        return [(m.group(2), m.group(4), 'aria-current="page"' in (m.group(1) + m.group(3)))
                for m in re.finditer(r'<a([^>]*?)href="([^"]+)"([^>]*)>([^<]*)</a>', self.nav_of(html))]

    def make_site_files(self):
        for name in ("archive.html", "teams.html"):
            shutil.copy(REPO_ROOT / name, self.tmp / name)
        for name in ("leaders.html", "playoffs.html"):
            (self.tmp / name).write_text("", encoding="utf-8")  # 產出物（只存在 gh-pages），測試以空檔代替

    def assert_nav_resolves(self, html, page_dir, expected):
        links = self.nav_links(html)
        self.assertEqual([t for _, t, _ in links], expected)
        for href, _, _ in links:
            self.assertTrue((page_dir / href).resolve().exists(), href)
        return links

    def test_season_report_nav(self):
        self.make_site_files()
        _, html = self.generate("schedule_normal")
        report_dir = self.tmp / "reports" / "2026" / "11"
        links = self.assert_nav_resolves(html, report_dir, ["← 首頁", "球隊名單", "數據排行", "季後賽", "歷史存檔"])
        self.assertEqual([h for h, _, _ in links], ["../../../index.html", "../../../teams.html", "../../../leaders.html", "../../../playoffs.html", "../../../archive.html"])
        self.assertFalse(any(cur for _, _, cur in links))

    def test_offseason_report_nav(self):
        self.make_site_files()
        self.run_main("schedule_empty")
        html = self.read_report()
        report_dir = self.tmp / "reports" / "2026" / "11"
        self.assert_nav_resolves(html, report_dir, ["← 首頁", "球隊名單", "數據排行", "季後賽", "歷史存檔"])

    def test_index_nav_and_teams_entry(self):
        self.make_site_files()
        self.run_main("schedule_normal")
        html = (self.tmp / "index.html").read_text(encoding="utf-8")
        links = self.assert_nav_resolves(html, self.tmp, ["球隊名單", "數據排行", "季後賽", "歷史存檔"])
        self.assertEqual([h for h, _, _ in links], ["teams.html", "leaders.html", "playoffs.html", "archive.html"])
        # 首頁入口卡：連到 teams.html，且位於最新一期卡片之後、近期區塊之前
        entry = re.search(r'<a class="teams-entry" href="([^"]+)">.*?30 隊名單', html, re.S)
        self.assertIsNotNone(entry)
        self.assertTrue((self.tmp / entry.group(1)).exists())
        self.assertLess(html.index('class="latest"'), html.index('class="teams-entry"'))
        self.assertLess(html.index('class="teams-entry"'), html.index('aria-labelledby="recent-h"'))

    def test_index_teams_entry_when_no_reports(self):
        out = fetch_mlb.render_index({})
        self.assertIn('<a class="teams-entry" href="teams.html">', out)

    def test_static_pages_nav_and_current(self):
        for name, current, hrefs in (
            ("archive.html", "歷史存檔", ["index.html", "teams.html", "leaders.html", "playoffs.html", "archive.html"]),
            ("teams.html", "球隊名單", ["index.html", "teams.html", "leaders.html", "playoffs.html", "archive.html"]),
        ):
            html = (REPO_ROOT / name).read_text(encoding="utf-8")
            links = self.nav_links(html)
            self.assertEqual([h for h, _, _ in links], hrefs, name)
            self.assertEqual([t for _, t, cur in links if cur], [current], name)
            for href, _, _ in links:
                if href in ("index.html", "leaders.html", "playoffs.html"):
                    continue  # 首頁、排行頁與季後賽頁為產生檔，只存在 gh-pages，main 上不存在
                self.assertTrue((REPO_ROOT / href).exists(), href)

    def test_teams_page_files(self):
        html = (REPO_ROOT / "teams.html").read_text(encoding="utf-8")
        self.assertIn('href="assets/style.css"', html)
        self.assertIn('src="assets/teams.js"', html)
        self.assertTrue((REPO_ROOT / "assets" / "teams.js").exists())
        self.assertNotIn("site.css", html)
        for el_id in ("dateline", "teamSelect", "picker", "content"):
            self.assertIn(f'id="{el_id}"', html)

    def test_teams_js_safety_and_paths(self):
        js = (REPO_ROOT / "assets" / "teams.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("document.write", js)
        self.assertIn("data/teams/", js)
        self.assertNotIn("teams.sample.json", js)
        for old, new in (("ARI", "AZ"), ("KCR", "KC"), ("OAK", "ATH")):
            self.assertRegex(js, rf'{old}:\s*"{new}"')

    def test_style_has_nav_and_teams_rules(self):
        css = (REPO_ROOT / "assets" / "style.css").read_text(encoding="utf-8")
        for selector in (".nav-links", ".teams-entry", ".picker-select", ".roster", ".team-title", ".sort-btn"):
            self.assertIn(selector, css)
        self.assertEqual(css.count('.nav-links a + a'), 1)


class TeamGlossaryTests(unittest.TestCase):
    def test_slang_aliases_in_glossary(self):
        lines = fetch_mlb.build_team_glossary()
        for line in ("Yanks -> 洋基", "O's -> 金鶯", "D-backs -> 響尾蛇", "Mets -> 大都會", "Yankees -> 洋基"):
            self.assertIn(line, lines)

    def test_glossary_has_no_duplicate_lines(self):
        lines = fetch_mlb.build_team_glossary()
        self.assertEqual(len(lines), len(set(lines)))
        keys = [ln.split(" -> ")[0] for ln in lines]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_nickname_in_glossary(self):
        lines = fetch_mlb.build_team_glossary()
        for _tid, (_full, nick, zh) in fetch_mlb.TEAM_NAMES_EN.items():
            self.assertIn(f"{nick} -> {zh}", lines)

    def test_translate_system_has_list_rule(self):
        self.assertIn("以逗號或頓號列舉的球隊名稱與簡稱也必須依對照表翻成中文", fetch_mlb.TRANSLATE_SYSTEM)


# ---------------------------------------------------------------- 首頁戰績側邊欄、今日預告、休賽季區塊（模組 S2）
TAIPEI = timezone(timedelta(hours=8))
SEASON_NOW = datetime(2026, 7, 30, 12, 17, tzinfo=TAIPEI)   # 賽季中
OFFSEASON_NOW = datetime(2026, 11, 18, 12, 17, tzinfo=TAIPEI)  # 休賽季（季後賽已結束）


class SidebarBase(MlbTestBase):
    def build(self, now, **api):
        for k, v in api.items():
            setattr(self.api, k, v)
        with mock.patch("mlb_daily.timeutil.get_taipei_now", return_value=now), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fetch_mlb.build_index_extras(now)

    def page(self, now, **api):
        extras = self.build(now, **api)
        return fetch_mlb.render_index({"2026-07-29": {"date": "2026-07-29", "total_games": 3, "postponed": 0,
                                                       "summary": "s", "type": "season"}}, extras)


class StandingsTests(SidebarBase):
    def test_fetch_standings_order_and_sorting(self):
        self.api.standings = "standings_2026"
        st = fetch_mlb.fetch_standings(2026)
        self.assertEqual([d["name"] for d in st["divisions"]],
                         ["美聯東區", "美聯中區", "美聯西區", "國聯東區", "國聯中區", "國聯西區"])
        self.assertEqual([d["id"] for d in st["divisions"]], [201, 202, 200, 204, 205, 203])
        for d in st["divisions"]:
            self.assertEqual(len(d["teams"]), 5)
            self.assertEqual([t["rank"] for t in d["teams"]], [1, 2, 3, 4, 5])  # API 順序相反，仍依 divisionRank
        first = st["divisions"][0]["teams"][0]
        self.assertEqual(first["clinch"], "z")
        self.assertEqual(first["gb"], "–")
        self.assertEqual(first["last_ten"], "9-1")
        self.assertEqual(first["streak"], "W1")
        self.assertEqual(len(st["records"]), 30)
        self.assertIn("season=2026", self.api.calls[-1])
        self.assertIn("leagueId=103,104", self.api.calls[-1])

    def test_fetch_standings_failure_returns_none(self):
        self.api.standings = None
        self.assertIsNone(fetch_mlb.fetch_standings(2026))
        with mock.patch("mlb_daily.net.fetch_json", return_value={"records": []}):
            self.assertIsNone(fetch_mlb.fetch_standings(2026))
        with mock.patch("mlb_daily.net.fetch_json", return_value={"unexpected": 1}):
            self.assertIsNone(fetch_mlb.fetch_standings(2026))

    def test_render_six_divisions_five_rows_and_markers(self):
        out = self.page(OFFSEASON_NOW, standings="standings_2026")
        aside = out[out.index('<aside class="standings"'):out.index("</aside>")]
        self.assertEqual(aside.count('<section class="st-div'), 6)
        self.assertEqual(aside.count('<tr class="st-row">'), 30)
        titles = re.findall(r'<h3 id="st-d\d+">([^<]+)</h3>', aside)
        self.assertEqual(titles, ["美聯東區", "美聯中區", "美聯西區", "國聯東區", "國聯中區", "國聯西區"])
        self.assertEqual(aside.count("st-div--league-start"), 1)
        self.assertLess(aside.index("st-div--league-start"), aside.index("國聯東區"))
        self.assertGreater(aside.index("st-div--league-start"), aside.index("美聯西區"))
        # clinch 標記：z、w 各一（美東）、y 兩個（中區、西區）
        self.assertEqual(aside.count('class="st-clinch"'), 4)
        self.assertIn('aria-hidden="true">z</sup>', aside)
        self.assertIn(">w</sup>", aside)
        self.assertEqual(aside.count(">y</sup>"), 2)
        self.assertIn("分區冠軍＋聯盟最佳戰績", aside)
        self.assertIn('class="st-legend"', aside)
        self.assertIn("<dt>z</dt>", aside)
        # 欄位、L10、STRK、隊名連結
        for col in ("W", "L", "PCT", "GB", "L10", "STRK"):
            self.assertIn(f'<th scope="col">{col}</th>', aside)
        self.assertIn('<td class="is-sub">9-1</td>', aside)
        self.assertRegex(aside, r'<td class="st-streak is-w">W1</td>')
        self.assertRegex(aside, r'<td class="st-streak is-l">L2</td>')
        self.assertIn('href="teams.html#NYY"', aside)
        self.assertIn('aria-label="紐約洋基', aside)
        self.assertNotIn("戰績暫時無法取得", aside)

    def test_title_in_season_vs_final(self):
        season = self.page(SEASON_NOW, standings="standings_2026", schedule="schedule_preview")
        self.assertIn("2026 例行賽戰績</h2>", season)
        self.assertNotIn("最終戰績", season)
        off = self.page(OFFSEASON_NOW, standings="standings_2026")
        self.assertIn("2026 例行賽最終戰績</h2>", off)
        self.assertRegex(off, r"更新於 <span class=\"num\">11/18 12:17</span>")
        self.assertIn("資料來源 MLB Stats API", off)

    def test_title_uses_previous_year_before_opening_day(self):
        now = datetime(2026, 3, 1, 12, 17, tzinfo=TAIPEI)
        out = self.page(now, standings="standings_2026")
        self.assertIn("2025 例行賽最終戰績</h2>", out)
        self.assertIn("season=2025", [c for c in self.api.calls if "/standings" in c][0])

    def test_failure_shows_error_version(self):
        out = self.page(OFFSEASON_NOW, standings=None)
        aside = out[out.index('<aside class="standings is-error"'):out.index("</aside>")]
        self.assertIn("戰績暫時無法取得", aside)
        self.assertNotIn("st-table", aside)
        self.assertNotIn("st-meta", aside)
        self.assertIn("2026 例行賽最終戰績", aside)

    def test_aside_structure_matches_mockup(self):
        out = self.page(OFFSEASON_NOW, standings="standings_2026")
        for needle in ('id="standings"', 'id="standings-panel"', 'data-st="collapse"', 'data-st="expand"',
                       'class="st-btn st-close"', 'data-st="open"', 'class="st-backdrop"', 'class="st-fab"'):
            self.assertIn(needle, out)
        self.assertLess(out.index("</main>"), out.index('<aside'))
        self.assertIn('assets/standings.js', out)
        self.assertIn("mlbdaily.standings.collapsed", out)

    def test_xss_in_standings_fields_escaped(self):
        data = load_fixture("standings_2026")
        tr = data["records"][0]["teamRecords"][0]
        tr["streak"]["streakCode"] = "<script>alert(1)</script>"
        tr["clinchIndicator"] = "<b>"
        tr["gamesBack"] = "<i>"
        with mock.patch("mlb_daily.net.fetch_json", return_value=data):
            st = fetch_mlb.fetch_standings(2026)
        html = fetch_mlb.render_standings_aside(st, 2026, False, OFFSEASON_NOW)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<b>", html)
        self.assertNotIn("<i>", html)
        self.assertIn("&lt;script&gt;", html)


class GamePreviewTests(SidebarBase):
    def previews(self, **api):
        api.setdefault("schedule", "schedule_preview")
        api.setdefault("people", "people_pitchers")
        api.setdefault("standings", "standings_2026")
        for k, v in api.items():
            setattr(self.api, k, v)
        records = (fetch_mlb.fetch_standings(2026) or {}).get("records")
        return fetch_mlb.fetch_game_previews("2026-07-30", 2026, records)

    def test_url_and_pitcher_batch_request(self):
        self.previews()
        sched = [c for c in self.api.calls if "/schedule" in c][0]
        self.assertIn("date=2026-07-30", sched)
        self.assertIn("hydrate=probablePitcher,team", sched)
        people = [c for c in self.api.calls if "/people?" in c][0]
        self.assertIn("personIds=", people)
        self.assertIn("season=2026", people)
        self.assertIn("543037", people)

    def test_taipei_conversion_sorting_and_exclusions(self):
        games = self.previews()
        # 延賽（9004）與非 30 隊（9006 明星賽）被排除
        self.assertEqual([g["pk"] for g in games], [9001, 9002, 9003, 9005, 9007])
        self.assertEqual(games[0]["start"].strftime("%Y-%m-%d %H:%M"), "2026-07-31 00:10")  # 16:10Z 跨日
        self.assertEqual(games[3]["start"].strftime("%m-%d %H:%M"), "07-31 09:05")
        self.assertEqual([g["start"] for g in games], sorted(g["start"] for g in games))

    def test_doubleheader_notes_and_tbd_pitcher(self):
        games = self.previews()
        self.assertEqual([g["note"] for g in games], ["", "雙重賽 1/2", "雙重賽 2/2", "", ""])
        html = fetch_mlb.render_game_previews(games)
        self.assertIn('<span class="gp-note">雙重賽 1/2</span>', html)
        self.assertIn('<span class="gp-note">雙重賽 2/2</span>', html)
        self.assertEqual(html.count('class="gp-tbd">未定</span>'), 2)  # 9002 之後客隊、9001 主隊皆無先發
        self.assertEqual(html.count('class="gp-note"'), 2)

    def test_pitcher_stats_and_records(self):
        html = fetch_mlb.render_game_previews(self.previews())
        self.assertIn("(11-6, 3.21)", html)
        self.assertIn("(18-4, 2.21)", html)
        self.assertIn('savant-player/543037"', html)
        # 戰績取自 standings（紐約洋基在 fixture 為美東第 N 名），退回 schedule 的情況另測
        yankees = fetch_mlb.fetch_standings(2026)["records"][147]
        self.assertIn(f'<span class="gp-rec">{yankees[0]}-{yankees[1]}</span>', html)
        self.assertNotIn("SHOULD NOT SHOW PARK", html)
        self.assertNotIn("API NAME", html)  # 中文隊名取自 TEAM_META

    def test_record_falls_back_to_schedule_then_omitted(self):
        games = self.previews(standings=None)
        # standings 失敗：改用 schedule leagueRecord（光芒 70-39）
        self.assertIn('<span class="gp-rec">70-39</span>', fetch_mlb.render_game_previews(games))
        data = load_fixture("schedule_preview")
        for g in data["dates"][0]["games"]:
            for side in g["teams"].values():
                side.pop("leagueRecord", None)
        with mock.patch("mlb_daily.net.fetch_json", side_effect=lambda u, *a, **k: data if "/schedule" in u else None):
            html = fetch_mlb.render_game_previews(fetch_mlb.fetch_game_previews("2026-07-30", 2026, {}))
        self.assertNotIn("gp-rec", html)

    def test_people_failure_keeps_names_without_stats(self):
        html = fetch_mlb.render_game_previews(self.previews(people=None))
        self.assertIn("Gerrit Cole", html)
        self.assertNotIn("gp-stat", html)

    def test_range_text_early_morning(self):
        games = self.previews()
        self.assertEqual(fetch_mlb.preview_range_text(games), "台北時間 7/31（五）凌晨起 · 5 場")
        self.assertIn("台北時間 7/31（五）凌晨起 · 5 場", fetch_mlb.render_game_previews(games))

    def test_range_text_non_early_has_no_dawn_wording(self):
        games = self.previews()
        games = [g for g in games if g["start"].hour >= 6]
        text = fetch_mlb.preview_range_text(games)
        self.assertNotIn("凌晨", text)
        self.assertRegex(text, r"^台北時間 7/31（五） \d\d:\d\d 起 · \d 場$")

    def test_zero_games(self):
        self.api.schedule = "schedule_empty"
        games = fetch_mlb.fetch_game_previews("2026-07-14", 2026, {})
        self.assertEqual(games, [])
        self.assertIn("今日無比賽", fetch_mlb.render_game_previews(games))

    def test_schedule_failure(self):
        self.api.schedule = None
        self.assertIsNone(fetch_mlb.fetch_game_previews("2026-07-30", 2026, {}))
        self.assertIn("賽程暫時無法取得", fetch_mlb.render_game_previews(None))

    def test_xss_pitcher_name_and_bad_id(self):
        html = fetch_mlb.render_game_previews(self.previews())
        self.assertNotIn("<script>alert('P')</script>", html)
        self.assertIn("&lt;script&gt;alert(&#x27;P&#x27;)&lt;/script&gt;", html)
        self.assertNotIn("bad-id", html)  # id 非整數：不輸出連結

    def test_in_season_page_has_preview_and_no_offseason_blocks(self):
        out = self.page(SEASON_NOW, schedule="schedule_preview", people="people_pitchers", standings="standings_2026")
        self.assertIn('id="gp-h"', out)
        self.assertNotIn('id="cd-h"', out)
        self.assertNotIn('id="wk-h"', out)
        self.assertIn("2026 例行賽戰績</h2>", out)
        # 原有區塊仍在
        self.assertIn('class="latest"', out)
        self.assertIn('href="teams.html"', out)
        self.assertIn('id="recent-h"', out)
        self.assertLess(out.index('class="latest"'), out.index('id="gp-h"'))
        self.assertLess(out.index('id="gp-h"'), out.index('class="teams-entry"'))
        self.assertLess(out.index('class="teams-entry"'), out.index('id="recent-h"'))

    def test_in_season_schedule_failure_shows_error_only_in_block(self):
        out = self.page(SEASON_NOW, schedule=None, standings="standings_2026")
        self.assertIn("賽程暫時無法取得", out)
        self.assertIn('class="st-row"', out)  # 戰績不受影響


class OffseasonBlocksTests(SidebarBase):
    def test_countdown_days(self):
        self.api.seasons_by_year = {2027: "seasons_2027"}
        cd = fetch_mlb.fetch_countdown(date(2026, 11, 18))
        self.assertEqual((cd["year"], cd["spring_days"], cd["opening_days"]), (2027, 93, 127))
        html = fetch_mlb.render_countdown(cd)
        self.assertIn('<span class="num">93</span> 天', html)
        self.assertIn('<span class="num">127</span> 天', html)
        self.assertIn("距離春訓", html)
        self.assertIn("距離開幕戰", html)
        self.assertIn('datetime="2027-02-19"', html)
        self.assertIn("2027 球季", html)

    def test_countdown_before_this_years_postseason_end_uses_current_year(self):
        # 今天（2026-01-10）尚未超過 2026 postSeasonEndDate -> 取 2026 年
        cd = fetch_mlb.fetch_countdown(date(2026, 1, 10))
        self.assertEqual(cd["year"], 2026)
        self.assertEqual((cd["spring_days"], cd["opening_days"]), (41, 74))

    def test_countdown_spring_in_progress(self):
        cd = fetch_mlb.fetch_countdown(date(2026, 3, 1))
        self.assertTrue(cd["spring_started"])
        html = fetch_mlb.render_countdown(cd)
        self.assertIn("春訓進行中", html)
        self.assertNotIn("距離春訓", html)
        self.assertIn("距離開幕戰", html)
        self.assertIn('<span class="num">24</span> 天', html)

    def test_countdown_failure_hides_block(self):
        self.api.seasons_by_year = {2027: None}
        self.assertIsNone(fetch_mlb.fetch_countdown(date(2026, 11, 18)))
        self.assertEqual(fetch_mlb.render_countdown(None), "")
        out = self.page(OFFSEASON_NOW, standings="standings_2026", transactions="transactions_weekly")
        self.assertNotIn('id="cd-h"', out)
        self.assertIn('id="wk-h"', out)  # 其他區塊不受影響

    def test_weekly_range_and_limit_and_order(self):
        self.api.transactions = "transactions_weekly"
        tx = fetch_mlb.fetch_weekly_transactions(date(2026, 11, 18))
        url = [c for c in self.api.calls if "/transactions" in c][0]
        self.assertIn("startDate=2026-11-11", url)
        self.assertIn("endDate=2026-11-17", url)
        self.assertEqual(len(tx), 8)  # 上限 8
        dates = [t["date"] for t in tx]
        self.assertEqual(dates, sorted(dates, reverse=True))  # 新到舊
        descs = " ".join(t["description"] for t in tx)
        self.assertIn("contract extension", descs)  # 延長合約保留
        self.assertIn("traded", descs)  # 交易保留
        self.assertNotIn("minor league", descs)
        self.assertNotIn("assigned", descs)
        self.assertNotIn("released", descs)
        self.assertNotIn("Korean club", descs)

    def test_weekly_zero_and_failure(self):
        self.api.transactions = "transactions_empty"
        self.assertEqual(fetch_mlb.fetch_weekly_transactions(date(2026, 11, 18)), [])
        self.assertIn("本週無重大人事異動", fetch_mlb.render_weekly_transactions([]))
        self.api.transactions = None
        self.assertIsNone(fetch_mlb.fetch_weekly_transactions(date(2026, 11, 18)))
        self.assertIn("暫時無法取得", fetch_mlb.render_weekly_transactions(None))

    def test_weekly_render_links_and_team(self):
        self.api.transactions = "transactions_weekly"
        html = fetch_mlb.render_weekly_transactions(fetch_mlb.fetch_weekly_transactions(date(2026, 11, 18)))
        self.assertIn("近 7 天 · 8 筆", html)
        self.assertIn('savant-player/624413"', html)
        self.assertIn(">Pete Alonso</a>", html)
        self.assertIn("紐約大都會", html)
        self.assertIn('datetime="2026-11-17">11/17</time>', html)

    def test_weekly_translation_applied_and_fallback(self):
        os.environ["GEMINI_API_KEY"] = "SECRETKEY"
        self.api.transactions = "transactions_weekly"
        seen = []

        def fake(u, p, h, timeout=30):
            items = request_items(p)
            seen.extend(items)
            return gemini_response([f"譯文{it['id']} " + " ".join(it["keep"]) for it in items])

        with mock.patch("mlb_daily.net.post_json", side_effect=fake) as m:
            out = self.page(OFFSEASON_NOW, standings="standings_2026", transactions="transactions_weekly")
        self.assertEqual(m.call_count, 1)  # 首頁需翻譯內容一次批次
        self.assertEqual(len(seen), 8)
        self.assertIn("譯文", out)
        self.assertIn(">Pete Alonso</a>", out)  # 保留英文人名與連結
        fetch_mlb.TX_TRANSLATION_CACHE.clear()  # 前一段已寫入譯文快取，這裡模擬沒有戰報譯文可沿用
        with mock.patch("mlb_daily.net.post_json", return_value=None):
            out = self.page(OFFSEASON_NOW, standings="standings_2026", transactions="transactions_weekly")
        self.assertIn("signed free agent 1B", out)  # 失敗退回英文原文
        self.assertNotIn("譯文", out)

    def test_weekly_xss_escaped(self):
        self.api.transactions = "transactions_weekly"
        html = fetch_mlb.render_weekly_transactions(fetch_mlb.fetch_weekly_transactions(date(2026, 11, 18)))
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x onerror=1&gt;", html)

    def test_offseason_page_blocks(self):
        out = self.page(OFFSEASON_NOW, standings="standings_2026", transactions="transactions_weekly",
                        seasons_by_year={2027: "seasons_2027"})
        self.assertIn('id="cd-h"', out)
        self.assertIn('id="wk-h"', out)
        self.assertNotIn('id="gp-h"', out)
        self.assertNotIn("今日賽事預告", out)
        self.assertIn("2026 例行賽最終戰績", out)
        self.assertIn('class="latest"', out)
        self.assertIn('href="teams.html"', out)
        self.assertIn('id="recent-h"', out)
        self.assertLess(out.index('id="cd-h"'), out.index('id="wk-h"'))
        self.assertLess(out.index('id="wk-h"'), out.index('class="teams-entry"'))


class ReviewFixTests(SidebarBase):
    """S2 審查修正：時鐘固定、翻譯不重複耗時、延長合約比對、缺欄位、動態圖例等。"""

    def test_clock_is_fixed_by_default(self):
        self.assertEqual(fetch_mlb.timeutil.get_taipei_now().date(), date(2026, 11, 18))

    def test_extension_regex_is_narrow(self):
        def tx(desc, code="SGN"):
            return {"description": desc, "type_code": code, "date": "2026-11-17"}
        picked = fetch_mlb.select_weekly_highlights([
            tx("Phillies signed C Foo to a 3-year contract extension."),
            tx("Yankees agreed to terms with RHP Bar on a 1-year contract."),
            tx("Mets placed RHP Baz in extended spring training."),
            tx("Cubs designated RHP Qux for assignment.", "DES"),
        ])
        self.assertEqual([t["description"] for t in picked], ["Phillies signed C Foo to a 3-year contract extension."])

    def test_weekly_missing_transactions_field_is_failure(self):
        for body in ({"copyright": "x"}, {"transactions": None}, {"transactions": "oops"}):
            with mock.patch("mlb_daily.net.fetch_json", return_value=body):
                self.assertIsNone(fetch_mlb.fetch_weekly_transactions(date(2026, 11, 18)), body)
        with mock.patch("mlb_daily.net.fetch_json", return_value={"transactions": []}):
            self.assertEqual(fetch_mlb.fetch_weekly_transactions(date(2026, 11, 18)), [])
        html = fetch_mlb.render_weekly_transactions(None)
        self.assertIn("暫時無法取得", html)
        self.assertNotIn("本週無重大人事異動", html)

    def test_translation_skipped_after_retryable_failure(self):
        os.environ["GEMINI_API_KEY"] = "SECRETKEY"
        fetch_mlb.LAST_POST["retryable"] = True  # 戰報翻譯已因 429/5xx/逾時耗盡重試
        weekly = [{"description": "New York Mets signed free agent 1B Pete Alonso.", "person_name": "Pete Alonso"}]
        with mock.patch("mlb_daily.net.post_json") as m, contextlib.redirect_stdout(io.StringIO()) as out:
            fetch_mlb.translate_weekly_transactions(weekly)
        m.assert_not_called()
        self.assertNotIn("description_zh", weekly[0])  # 使用原文
        self.assertIn("不再重試", out.getvalue())

    def test_report_translation_reused_for_weekly(self):
        os.environ["GEMINI_API_KEY"] = "SECRETKEY"
        desc = "New York Mets signed free agent 1B Pete Alonso."
        fetch_mlb.TX_TRANSLATION_CACHE[desc] = "紐約大都會簽下自由球員 1B Pete Alonso。"
        weekly = [{"description": desc, "person_name": "Pete Alonso", "person_id": 624413}]
        with mock.patch("mlb_daily.net.post_json") as m, contextlib.redirect_stdout(io.StringIO()):
            fetch_mlb.translate_weekly_transactions(weekly)
        m.assert_not_called()  # 全部沿用戰報譯文，不呼叫 Gemini
        self.assertEqual(weekly[0]["description_zh"], "紐約大都會簽下自由球員 1B Pete Alonso。")

    def test_apply_translations_fills_cache(self):
        os.environ["GEMINI_API_KEY"] = "SECRETKEY"
        tx = [{"description": "Atlanta Braves signed free agent RHP Big Name.", "person_name": "Big Name"}]
        with mock.patch("mlb_daily.net.post_json",
                               return_value=gemini_response(["勇士簽下自由球員 RHP Big Name。"])), \
                contextlib.redirect_stdout(io.StringIO()):
            fetch_mlb.apply_translations(None, [], tx)
        self.assertEqual(fetch_mlb.TX_TRANSLATION_CACHE[tx[0]["description"]], "勇士簽下自由球員 RHP Big Name。")

    def test_seasons_current_year_requested_once(self):
        self.build(OFFSEASON_NOW, standings="standings_2026", transactions="transactions_weekly",
                   seasons_by_year={2027: "seasons_2027"})
        year_calls = [c for c in self.api.calls if "/seasons/2026" in c]
        self.assertEqual(len(year_calls), 1)

    def test_resolve_roster_context_still_fetches_by_itself(self):
        fetch_mlb.resolve_roster_context(date(2026, 7, 1))
        self.assertEqual(len([c for c in self.api.calls if "/seasons/2026" in c]), 1)

    def test_dynamic_legend_lists_only_present_marks(self):
        data = load_fixture("standings_2026")
        for rec in data["records"]:
            for tr in rec["teamRecords"]:
                tr.pop("clinchIndicator", None)
        data["records"][0]["teamRecords"][0]["clinchIndicator"] = "x"
        data["records"][0]["teamRecords"][1]["clinchIndicator"] = "q"
        with mock.patch("mlb_daily.net.fetch_json", return_value=data):
            st = fetch_mlb.fetch_standings(2026)
        html = fetch_mlb.render_standings_aside(st, 2026, False, OFFSEASON_NOW)
        legend = html[html.index('class="st-legend"'):]
        self.assertIn("<dt>x</dt><dd>取得季後賽資格</dd>", legend)
        self.assertNotIn("<dt>z</dt>", legend)
        self.assertNotIn("<dt>q</dt>", legend)  # 未知字母不入圖例
        self.assertRegex(html, r'aria-label="[^"]*，取得季後賽資格"')  # sup 為 aria-hidden，說明放在連結 aria-label
        self.assertRegex(html, r'aria-label="[^"]*，標記 q"')
        self.assertIn(">q</sup>", html)

    def test_no_clinch_marks_means_no_legend(self):
        data = load_fixture("standings_2026")
        for rec in data["records"]:
            for tr in rec["teamRecords"]:
                tr.pop("clinchIndicator", None)
        with mock.patch("mlb_daily.net.fetch_json", return_value=data):
            st = fetch_mlb.fetch_standings(2026)
        self.assertNotIn("st-legend", fetch_mlb.render_standings_aside(st, 2026, True, SEASON_NOW))

    def _preview_with(self, games):
        data = {"dates": [{"date": "2026-07-30", "games": games}]}
        with mock.patch("mlb_daily.net.fetch_json", side_effect=lambda u, *a, **k: data if "/schedule" in u else None):
            return fetch_mlb.fetch_game_previews("2026-07-30", 2026, {})

    @staticmethod
    def _g(pk, when, status="Preview", tbd=False, away=147, home=111):
        return {"gamePk": pk, "gameDate": when, "doubleHeader": "N", "gameNumber": 1,
                "status": {"detailedState": status, "startTimeTBD": tbd},
                "teams": {"away": {"team": {"id": away}}, "home": {"team": {"id": home}}}}

    def test_naive_game_date_treated_as_utc(self):
        games = self._preview_with([self._g(1, "2026-07-30T16:10:00")])
        self.assertEqual(games[0]["start"].strftime("%m-%d %H:%M"), "07-31 00:10")

    def test_cancelled_and_postponed_excluded(self):
        games = self._preview_with([
            self._g(1, "2026-07-30T16:10:00Z", status="Cancelled"),
            self._g(2, "2026-07-30T17:10:00Z", status="Postponed"),
            self._g(3, "2026-07-30T18:10:00Z"),
        ])
        self.assertEqual([g["pk"] for g in games], [3])

    def test_tbd_games_sorted_last_and_range_text_omits_time(self):
        games = self._preview_with([
            self._g(1, "2026-07-30T16:10:00Z", tbd=True),
            self._g(2, "2026-07-30T23:10:00Z"),
        ])
        self.assertEqual([g["pk"] for g in games], [2, 1])
        html = fetch_mlb.render_game_previews(games)
        self.assertIn('<span class="gp-time">待定</span>', html)
        self.assertIn("台北時間 7/31（五） 07:10 起 · 2 場", html)
        text = fetch_mlb.preview_range_text([g for g in games if g["tbd"]])
        self.assertEqual(text, "開賽時間待定 · 1 場")
        self.assertNotIn("凌晨", text)

    def test_drawer_js_marks_intro_inert_too(self):
        js = (REPO_ROOT / "assets" / "standings.js").read_text(encoding="utf-8")
        self.assertIn(".intro-wrap", js)
        tpl = (REPO_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="wrap intro-wrap"', tpl)


class SidebarFailureIsolationTests(SidebarBase):
    def run_flags(self, *flags):
        with mock.patch.object(sys, "argv", ["fetch_mlb.py", "--date", DATE, *flags]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                fetch_mlb.main()
            except SystemExit as e:
                return e.code
        return None

    def test_all_sources_failing_main_still_exits_zero(self):
        self.api.schedule = "schedule_normal"
        self.api.standings = None
        self.api.seasons = None
        self.api.transactions = None
        with mock.patch("mlb_daily.timeutil.get_taipei_now", return_value=OFFSEASON_NOW):
            code = self.run_flags()
        self.assertIsNone(code)
        self.assertEqual(self.read_metadata()[DATE]["total_games"], 2)
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertIn("戰績暫時無法取得", index)
        self.assertIn('class="latest"', index)
        self.discord.assert_called_once()

    def test_exceptions_inside_blocks_do_not_break_main(self):
        self.api.schedule = "schedule_normal"
        with mock.patch("mlb_daily.standings.fetch_standings", side_effect=RuntimeError("boom")), \
                mock.patch("mlb_daily.previews.fetch_countdown", side_effect=RuntimeError("boom")), \
                mock.patch("mlb_daily.transactions.fetch_weekly_transactions", side_effect=ValueError("boom")), \
                mock.patch("mlb_daily.timeutil.get_taipei_now", return_value=OFFSEASON_NOW):
            code = self.run_flags()
        self.assertIsNone(code)
        self.assertTrue((self.tmp / "reports" / "metadata.json").exists())
        self.assertIn("戰績暫時無法取得", (self.tmp / "index.html").read_text(encoding="utf-8"))

    def test_build_index_extras_total_failure_still_writes_index(self):
        self.api.schedule = "schedule_normal"
        with mock.patch("mlb_daily.index_page.build_index_extras", side_effect=RuntimeError("boom")):
            code = self.run_flags()
        self.assertIsNone(code)
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="latest"', index)
        self.assertNotIn("<!--SLOT:", index)
        # 仍輸出側欄錯誤版與手機按鈕，桌機右側不會是空欄
        self.assertIn('<aside class="standings is-error"', index)
        self.assertIn("戰績暫時無法取得", index)
        self.assertIn('class="st-fab"', index)

    def test_rosters_and_notify_only_do_not_fetch_sidebar_data(self):
        self.api.schedule = "schedule_normal"
        self.run_flags()
        self.api.calls.clear()
        self.discord.reset_mock()
        self.run_flags("--notify-only")
        self.assertEqual(self.api.calls, [])
        with mock.patch("mlb_daily.rosters.fetch_rosters", return_value=1) as m, \
                mock.patch.object(sys, "argv", ["fetch_mlb.py", "--rosters"]), \
                mock.patch("mlb_daily.index_page.build_index_extras") as b:
            with self.assertRaises(SystemExit):
                fetch_mlb.main()
        m.assert_called_once()
        b.assert_not_called()


class SidebarAssetsTests(unittest.TestCase):
    def test_standings_js_has_no_innerhtml_and_no_design_only_switches(self):
        js = (REPO_ROOT / "assets" / "standings.js").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("outerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("state=error", js)
        self.assertIn("localStorage", js)
        self.assertIn("mlbdaily.standings.collapsed", js)
        self.assertIn("inert", js)
        self.assertIn("Escape", js)

    def test_style_css_has_sidebar_rules(self):
        css = (REPO_ROOT / "assets" / "style.css").read_text(encoding="utf-8")
        for needle in (".standings", ".st-panel", ".st-table", ".st-fab", ".st-rail", ".gp-game", ".countdown",
                       ".wk-tx", "html.standings-collapsed", "html.st-drawer-open", ".layout"):
            self.assertIn(needle, css)
        self.assertNotIn("mode-season", css)
        self.assertNotIn("is-season-only", css)

    def test_template_has_slots_and_head_script(self):
        tpl = (REPO_ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        for slot in ("LATEST", "UPCOMING", "TEAMS", "RECENT", "STANDINGS", "STANDINGS_UI"):
            self.assertIn(f"<!--SLOT:{slot}-->", tpl)
        head = tpl[:tpl.index("</head>")]
        self.assertIn("mlbdaily.standings.collapsed", head)
        self.assertIn('src="assets/standings.js"', head)
        self.assertNotIn("mode=season", tpl)

    def test_render_index_without_extras_has_no_leftover_slots(self):
        out = fetch_mlb.render_index({"2026-11-16": {"date": "2026-11-16", "summary": "ok"}})
        self.assertNotIn("<!--SLOT:", out)
        self.assertIn('class="teams-entry"', out)


class ImportRuleTests(unittest.TestCase):
    """mlb_daily 匯入規則防護：只允許 from .config import ...，且不得 import fetch_mlb。不連網。"""

    def test_mlb_daily_import_rules(self):
        import ast
        pkg = REPO_ROOT / "mlb_daily"
        violations = []
        for path in sorted(pkg.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "fetch_mlb" or alias.name.startswith("fetch_mlb."):
                            violations.append(f"{path.name}:{node.lineno} import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod == "fetch_mlb" or mod.startswith("fetch_mlb."):
                        violations.append(f"{path.name}:{node.lineno} from {mod} import ...")
                    elif node.level == 1 and mod:
                        if mod != "config":
                            violations.append(f"{path.name}:{node.lineno} from .{mod} import ...")
                    elif node.level == 0 and mod.startswith("mlb_daily."):
                        if mod != "mlb_daily.config":
                            violations.append(f"{path.name}:{node.lineno} from {mod} import ...")
                    elif node.level == 0 and mod == "mlb_daily":
                        violations.append(f"{path.name}:{node.lineno} from mlb_daily import ...")
        self.assertEqual(violations, [], "mlb_daily 內只允許 from .config import ...，其他請用模組前綴：" + "; ".join(violations))


if __name__ == "__main__":
    unittest.main()
