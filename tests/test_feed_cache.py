"""RSS 累積快取與 FanGraphs 分頁測試：全部不連網（net.fetch_text 以 mock 取代），檔案只寫暫存目錄。"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mlb_daily import analysis, feed_cache, news  # noqa: E402
from mlb_daily.config import ANALYSIS_MAX_PAGES, ANALYSIS_RSS_URL, NEWS_RSS_URL  # noqa: E402

TPE = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=TPE)


def rfc(dt):
    """datetime（任意時區）轉 RSS pubDate（GMT）。"""
    return dt.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")


def news_rss(*entries):
    """entries: (title, slug, 台北 datetime)"""
    body = "".join(
        f"<item><title>{t}</title><link>https://www.mlb.com/news/{slug}</link><pubDate>{rfc(d)}</pubDate></item>"
        for t, slug, d in entries)
    return f'<rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


def fg_rss(*entries):
    """entries: (title, slug, 台北 datetime[, categories])"""
    body = ""
    for e in entries:
        t, slug, d = e[:3]
        cats = "".join(f"<category>{c}</category>" for c in (e[3] if len(e) > 3 else ()))
        body += (f"<item><title>{t}</title><link>https://blogs.fangraphs.com/{slug}/</link>"
                 f"<pubDate>{rfc(d)}</pubDate>{cats}</item>")
    return f'<rss version="2.0"><channel><title>x</title>{body}</channel></rss>'


def tpe(y, m, d, hh=12, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TPE)


class FeedTestBase(unittest.TestCase):
    now = NOW

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        old = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, old)
        self.path = str(self.tmp / "data" / "feed_cache.json")
        self.pages = {}  # url -> 文字（None 代表抓取失敗）
        self.calls = []  # (url, retries)
        for p in (
            mock.patch("mlb_daily.net.fetch_text", side_effect=self._fetch),
            mock.patch("mlb_daily.timeutil.get_taipei_now", side_effect=lambda: self.now),
            mock.patch("urllib.request.urlopen", side_effect=AssertionError("測試不得連網")),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(feed_cache.disable_cache)

    def _fetch(self, url, retries=3, delay=5):
        self.calls.append((url, retries))
        return self.pages.get(url)

    def quiet(self, fn, *a, **kw):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            result = fn(*a, **kw)
        self.err = err.getvalue()
        return result

    def read_cache(self):
        return json.loads(Path(self.path).read_text(encoding="utf-8"))


class NewsBoundaryTests(FeedTestBase):
    def test_taipei_day_boundary(self):
        # UTC 15:59 = 台北 23:59（當日）；UTC 16:00 = 台北隔日 00:00
        a = datetime(2026, 11, 15, 15, 59, tzinfo=timezone.utc)
        b = datetime(2026, 11, 15, 16, 0, tzinfo=timezone.utc)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", a), ("B", "b", b))
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2026-11-15")], ["A"])
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2026-11-16")], ["B"])
        self.assertEqual(self.quiet(news.fetch_all_daily_news, "2026-11-15")[0]["published"].hour, 23)

    def test_year_boundary(self):
        self.now = tpe(2027, 1, 2)
        a = tpe(2026, 12, 31, 23, 30)
        b = tpe(2027, 1, 1, 0, 30)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", a), ("B", "b", b))
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2026-12-31")], ["A"])
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2027-01-01")], ["B"])

    def test_year_boundary_with_cache(self):
        self.now = tpe(2027, 1, 2)
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 12, 31, 23, 30)), ("B", "b", tpe(2027, 1, 1, 0, 30)))
        self.quiet(news.fetch_all_daily_news, "2027-01-01")
        feed_cache.enable_cache(self.path)  # 模擬下一個 process
        self.pages[NEWS_RSS_URL] = None
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2026-12-31")], ["A"])


class NewsCacheTests(FeedTestBase):
    def test_accumulates_between_runs(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)))
        self.quiet(news.fetch_all_daily_news, "2026-11-16")
        feed_cache.enable_cache(self.path)  # 第二次執行：feed 只剩 B
        self.pages[NEWS_RSS_URL] = news_rss(("B", "b", tpe(2026, 11, 17)))
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2026-11-15")], ["A"])
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2026-11-17")], ["B"])
        data = self.read_cache()
        self.assertEqual(data["schema"], 1)
        self.assertEqual({it["link"] for it in data["feeds"]["news"]},
                         {"https://www.mlb.com/news/a", "https://www.mlb.com/news/b"})
        self.assertIn("+08:00", data["feeds"]["news"][0]["published"])

    def test_new_overrides_old_by_link(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("Old title", "a", tpe(2026, 11, 15)))
        self.quiet(news.fetch_all_daily_news, "2026-11-15")
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("New title", "a", tpe(2026, 11, 15)))
        result = self.quiet(news.fetch_all_daily_news, "2026-11-15")
        self.assertEqual([n["title"] for n in result], ["New title"])

    def test_fetch_failure_with_cache_returns_cached(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)))
        self.quiet(news.fetch_all_daily_news, "2026-11-15")
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = None
        result = self.quiet(news.fetch_all_daily_news, "2026-11-15")
        self.assertEqual([n["title"] for n in result], ["A"])
        self.assertIn("Warning", self.err)
        self.assertIsNotNone(result[0]["published"].tzinfo)

    def test_parse_failure_with_cache_returns_cached(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)))
        self.quiet(news.fetch_all_daily_news, "2026-11-15")
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = "<html>blocked</html>"
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2026-11-15")], ["A"])

    def test_fetch_failure_without_cache_is_none(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = None
        self.assertIsNone(self.quiet(news.fetch_all_daily_news, "2026-11-15"))

    def test_fetch_failure_cache_has_no_item_that_day_is_none(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)))
        self.quiet(news.fetch_all_daily_news, "2026-11-15")
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = None
        self.assertIsNone(self.quiet(news.fetch_all_daily_news, "2026-11-14"))

    def test_success_with_zero_items_is_empty_list(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)))
        self.assertEqual(self.quiet(news.fetch_all_daily_news, "2026-11-10"), [])

    def test_expired_items_pruned_and_cap(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(
            ("Expired", "old", tpe(2026, 10, 20)),  # 超過 21 天（截止 10/28 12:00）
            ("Keep1", "k1", tpe(2026, 11, 10)), ("Keep2", "k2", tpe(2026, 11, 12)), ("Keep3", "k3", tpe(2026, 11, 14)))
        with mock.patch("mlb_daily.feed_cache.FEED_CACHE_MAX_ITEMS", 2):
            self.quiet(news.fetch_all_daily_news, "2026-11-14")
        links = [it["link"].rsplit("/", 1)[1] for it in self.read_cache()["feeds"]["news"]]
        self.assertEqual(links, ["k3", "k2"])  # 過期者被清除；超過上限保留最新

        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("Keep1", "k1", tpe(2026, 11, 10)))
        self.quiet(news.fetch_all_daily_news, "2026-11-10")
        self.assertNotIn("old", [it["link"].rsplit("/", 1)[1] for it in self.read_cache()["feeds"]["news"]])

    def test_disabled_cache_writes_nothing(self):
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)))
        self.assertEqual(len(self.quiet(news.fetch_all_daily_news, "2026-11-15")), 1)
        self.assertEqual(os.listdir(self.tmp), [])
        self.assertFalse(feed_cache.cache_enabled())
        # 未啟用時沒有 memo：每次都重抓
        self.quiet(news.fetch_all_daily_news, "2026-11-15")
        self.assertEqual(len(self.calls), 2)

    def test_corrupt_or_wrong_schema_cache_treated_as_empty(self):
        for content in ("{not json", json.dumps({"schema": 99, "feeds": {"news": []}}), json.dumps([1, 2])):
            Path(self.path).parent.mkdir(exist_ok=True)
            Path(self.path).write_text(content, encoding="utf-8")
            feed_cache.enable_cache(self.path)
            self.pages[NEWS_RSS_URL] = None
            self.assertIsNone(self.quiet(news.fetch_all_daily_news, "2026-11-15"), content)
            self.assertIn("Warning", self.err)
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)))
        self.quiet(news.fetch_all_daily_news, "2026-11-15")
        self.assertEqual(self.read_cache()["schema"], 1)  # 壞檔被有效內容覆蓋

    def test_bad_items_in_cache_skipped(self):
        Path(self.path).parent.mkdir(exist_ok=True)
        good = {"title": "G", "link": "https://www.mlb.com/news/g", "published": "2026-11-15T10:00:00+08:00",
                "image": None, "summary": ""}
        Path(self.path).write_text(json.dumps({"schema": 1, "feeds": {"news": [
            good, {"title": "no link"}, "str", {**good, "link": "https://www.mlb.com/news/h", "published": "bad"}]}}),
            encoding="utf-8")
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = None
        self.assertEqual([n["title"] for n in self.quiet(news.fetch_all_daily_news, "2026-11-15")], ["G"])

    def test_write_failure_only_warns(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)))
        with mock.patch("mlb_daily.storage.write_json_atomic", side_effect=OSError("disk full")):
            result = self.quiet(news.fetch_all_daily_news, "2026-11-15")
        self.assertEqual(len(result), 1)
        self.assertIn("無法寫入 RSS 快取", self.err)

    def test_memo_fetches_once_per_process(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = news_rss(("A", "a", tpe(2026, 11, 15)), ("B", "b", tpe(2026, 11, 16)))
        self.quiet(news.fetch_all_daily_news, "2026-11-15")
        self.quiet(news.fetch_all_daily_news, "2026-11-16")
        self.quiet(news.fetch_daily_news, "2026-11-15")
        self.assertEqual([u for u, _ in self.calls], [NEWS_RSS_URL])

    def test_memo_remembers_failure(self):
        feed_cache.enable_cache(self.path)
        self.pages[NEWS_RSS_URL] = None
        self.quiet(news.fetch_all_daily_news, "2026-11-15")
        self.quiet(news.fetch_all_daily_news, "2026-11-16")
        self.assertEqual(len(self.calls), 1)


class AnalysisPagingTests(FeedTestBase):
    P1 = ANALYSIS_RSS_URL
    P2 = ANALYSIS_RSS_URL + "?paged=2"

    def page(self, n):
        return ANALYSIS_RSS_URL if n == 1 else f"{ANALYSIS_RSS_URL}?paged={n}"

    def test_stops_when_oldest_before_day_start(self):
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = fg_rss(("P1a", "p1a", tpe(2026, 11, 16)), ("P1b", "p1b", tpe(2026, 11, 15, 20)))
        self.pages[self.P2] = fg_rss(("P2a", "p2a", tpe(2026, 11, 15, 9)), ("P2b", "p2b", tpe(2026, 11, 14, 23)))
        self.pages[self.page(3)] = fg_rss(("P3a", "p3a", tpe(2026, 11, 14)))
        result = self.quiet(analysis.fetch_daily_analysis, "2026-11-15")
        self.assertEqual([a["title"] for a in result], ["P1b", "P2a"])
        self.assertEqual(self.calls, [(self.P1, 3), (self.P2, 1)])  # 第 2 頁起只試一次；第 2 頁最舊已早於當日 → 不抓第 3 頁

    def test_single_page_when_first_page_already_older(self):
        self.pages[self.P1] = fg_rss(("A", "a", tpe(2026, 11, 15)), ("B", "b", tpe(2026, 11, 14)))
        self.quiet(analysis.fetch_daily_analysis, "2026-11-15")
        self.assertEqual(len(self.calls), 1)  # 未啟用快取也適用分頁規則，但這裡不需第 2 頁

    def test_page_cap(self):
        feed_cache.enable_cache(self.path)
        for n in range(1, 9):
            self.pages[self.page(n)] = fg_rss((f"T{n}", f"t{n}", tpe(2026, 11, 10) + timedelta(minutes=n)))
        result = self.quiet(analysis.fetch_daily_analysis, "2026-11-01")
        self.assertEqual(result, [])
        self.assertEqual(len(self.calls), ANALYSIS_MAX_PAGES)
        self.assertEqual(self.calls[-1][0], self.page(ANALYSIS_MAX_PAGES))

    def test_page2_fetch_failure_keeps_page1(self):
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = fg_rss(("A", "a", tpe(2026, 11, 15, 8)), ("B", "b", tpe(2026, 11, 15, 7)))
        self.pages[self.P2] = None
        result = self.quiet(analysis.fetch_daily_analysis, "2026-11-15")
        self.assertEqual([a["title"] for a in result], ["A", "B"])
        self.assertEqual(len(self.calls), 2)

    def test_page2_parse_failure_and_empty_keep_page1(self):
        for bad in ("<html>blocked</html>", "<rss><channel></channel></rss>"):
            self.calls.clear()
            feed_cache.enable_cache(self.path)
            self.pages[self.P1] = fg_rss(("A", "a", tpe(2026, 11, 15, 8)))
            self.pages[self.P2] = bad
            result = self.quiet(analysis.fetch_daily_analysis, "2026-11-15")
            self.assertEqual([a["title"] for a in result], ["A"], bad)
            self.assertEqual(len(self.calls), 2, bad)  # 不再嘗試第 3 頁

    def test_podcast_counts_for_oldest(self):
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = fg_rss(("A", "a", tpe(2026, 11, 15, 8)),
                                     ("Effectively Wild 1", "ew", tpe(2026, 11, 14, 8)),
                                     ("Talk", "talk", tpe(2026, 11, 14, 7), ["Podcasts"]))
        result = self.quiet(analysis.fetch_daily_analysis, "2026-11-15")
        self.assertEqual([a["title"] for a in result], ["A"])
        self.assertEqual(len(self.calls), 1)  # podcast 雖被捨棄，其時間仍使最舊早於當日 → 停

    def test_first_page_failure_is_none_and_no_paging(self):
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = None
        self.assertIsNone(self.quiet(analysis.fetch_daily_analysis, "2026-11-15"))
        self.assertEqual(len(self.calls), 1)

    def test_first_page_failure_uses_cache(self):
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = fg_rss(("A", "a", tpe(2026, 11, 15, 8)), ("Z", "z", tpe(2026, 11, 14)))
        self.quiet(analysis.fetch_daily_analysis, "2026-11-15")
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = None
        result = self.quiet(analysis.fetch_daily_analysis, "2026-11-15")
        self.assertEqual([a["title"] for a in result], ["A"])
        self.assertEqual(set(result[0]), {"title", "link", "published", "author"})
        self.assertIn("Warning", self.err)
        self.assertIsNone(self.quiet(analysis.fetch_daily_analysis, "2026-11-12"))

    def test_accumulates_between_runs(self):
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = fg_rss(("A", "a", tpe(2026, 11, 15, 8)), ("Z", "z", tpe(2026, 11, 14)))
        self.quiet(analysis.fetch_daily_analysis, "2026-11-15")
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = fg_rss(("B", "b", tpe(2026, 11, 17, 8)), ("Y", "y", tpe(2026, 11, 16)))
        self.assertEqual([a["title"] for a in self.quiet(analysis.fetch_daily_analysis, "2026-11-15")], ["A"])
        self.assertEqual([a["title"] for a in self.quiet(analysis.fetch_daily_analysis, "2026-11-17")], ["B"])

    def test_memo_fetches_once_and_continues_paging_for_older_dates(self):
        feed_cache.enable_cache(self.path)
        self.pages[self.P1] = fg_rss(("A", "a", tpe(2026, 11, 16, 8)), ("B", "b", tpe(2026, 11, 15, 8)))
        self.pages[self.P2] = fg_rss(("C", "c", tpe(2026, 11, 14, 8)), ("D", "d", tpe(2026, 11, 13, 8)))
        self.quiet(analysis.fetch_daily_analysis, "2026-11-16")  # 最舊 11/15 08:00 >= 11/16? 否 → 不翻頁
        self.assertEqual(len(self.calls), 1)
        self.quiet(analysis.fetch_daily_analysis, "2026-11-16")
        self.assertEqual(len(self.calls), 1)  # 同 process 不重複抓
        result = self.quiet(analysis.fetch_daily_analysis, "2026-11-14")  # 更早日期：從第 2 頁接續，不重抓第 1 頁
        self.assertEqual([u for u, _ in self.calls], [self.P1, self.P2])
        self.assertEqual([a["title"] for a in result], ["C"])

    def test_limit_and_order(self):
        self.pages[self.P1] = fg_rss(*[(f"T{i}", f"t{i}", tpe(2026, 11, 15, i)) for i in range(1, 9)]
                                     + [("Old", "old", tpe(2026, 11, 14))])
        result = self.quiet(analysis.fetch_daily_analysis, "2026-11-15", limit=3)
        self.assertEqual([a["title"] for a in result], ["T8", "T7", "T6"])

    def test_dedup_across_pages_when_cache_disabled(self):
        self.pages[self.P1] = fg_rss(("A", "a", tpe(2026, 11, 15, 8)))
        self.pages[self.P2] = fg_rss(("A", "a", tpe(2026, 11, 15, 8)), ("Z", "z", tpe(2026, 11, 14)))
        self.assertEqual(len(self.quiet(analysis.fetch_daily_analysis, "2026-11-15")), 1)
        self.assertEqual(os.listdir(self.tmp), [])


if __name__ == "__main__":
    unittest.main()
