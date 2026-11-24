"""網站檔案集中在 site/ 的佈局測試：config 路徑常數、workflow 部署根目錄、首頁寫到 site/index.html。
首頁測試在暫存目錄（MlbTestBase 會 chdir 進去）執行，不得寫入 repo。"""

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

from mlb_daily import config, index_page, storage  # noqa: E402

REPO_ROOT = base.REPO_ROOT


def under(path, root):
    """path 是否位於 root 目錄之下（以路徑片段比較，與作業系統分隔符無關）。"""
    p = Path(path).parts
    r = Path(root).parts
    return p[:len(r)] == r and len(p) > len(r)


class ConfigSitePathsTests(unittest.TestCase):
    SITE_OUTPUTS = (
        "FEED_CACHE_PATH", "SEASON_CACHE_PATH", "ROSTER_DIR", "DYNAMIC_WATCHLIST_PATH", "DAILY_SNAPSHOT_DIR",
        "WEEKLY_DIR", "WEEKLY_INDEX_PATH", "LEADERS_PATH", "LEADERS_PAGE_PATH", "PLAYOFFS_PATH", "PLAYOFFS_PAGE_PATH",
        "REPORTS_DIR", "METADATA_PATH", "INDEX_PAGE_PATH",
    )

    def test_site_dir_value(self):
        self.assertEqual(config.SITE_DIR, "site")

    def test_site_outputs_are_under_site_dir(self):
        for name in self.SITE_OUTPUTS:
            self.assertTrue(under(getattr(config, name), config.SITE_DIR), name)

    def test_reports_related_paths(self):
        self.assertEqual(Path(config.REPORTS_DIR), Path("site") / "reports")
        self.assertEqual(Path(config.METADATA_PATH), Path("site") / "reports" / "metadata.json")
        self.assertEqual(Path(config.INDEX_PAGE_PATH), Path("site") / "index.html")
        self.assertTrue(under(config.WEEKLY_DIR, config.REPORTS_DIR))

    def test_templates_and_settings_are_not_under_site(self):
        for name in ("TEMPLATE_DIR", "WATCHLIST_PATH", "OFFSEASON_CALENDAR_PATH"):
            self.assertFalse(under(getattr(config, name), config.SITE_DIR), name)

    def test_fetch_mlb_reexports_site_constants(self):
        import fetch_mlb
        for name in ("SITE_DIR", "REPORTS_DIR", "METADATA_PATH", "INDEX_PAGE_PATH"):
            self.assertEqual(getattr(fetch_mlb, name), getattr(config, name), name)

    def test_page_links_use_file_name_only(self):
        # 小卡的「完整排行／對戰樹」連結是網站內相對連結，不得帶 site/ 前綴
        self.assertEqual(os.path.basename(config.LEADERS_PAGE_PATH), "leaders.html")
        self.assertEqual(os.path.basename(config.PLAYOFFS_PAGE_PATH), "playoffs.html")


class WorkflowSiteDirTests(unittest.TestCase):
    def setUp(self):
        self.yml = (REPO_ROOT / ".github" / "workflows" / "daily_report.yml").read_text(encoding="utf-8")

    def test_publish_dir_is_site_and_no_exclude(self):
        self.assertIn("publish_dir: ./site", self.yml)
        self.assertNotIn("exclude_assets", self.yml)
        self.assertIn("keep_files: true", self.yml)

    def test_stamp_assets_runs_on_site(self):
        self.assertIn('python -m mlb_daily.stamp_assets site "${GITHUB_SHA::7}"', self.yml)

    def test_restore_writes_into_site_but_reads_gh_pages_root(self):
        self.assertIn("git show origin/gh-pages:reports/metadata.json > site/reports/metadata.json", self.yml)
        self.assertIn("git show origin/gh-pages:data/watchlist.json > site/data/watchlist.json", self.yml)
        self.assertIn("git cat-file -e \"origin/gh-pages:reports/$YEAR/$MONTH/$DATE.html\"", self.yml)

    def test_site_dir_has_no_gitignore_and_tracks_static_files(self):
        self.assertFalse((REPO_ROOT / "site" / ".gitignore").exists())  # 部署 .gitignore 會讓新戰報被忽略
        for rel in ("archive.html", "teams.html", "assets/style.css"):
            self.assertTrue((REPO_ROOT / "site" / rel).exists(), rel)
        for rel in ("archive.html", "teams.html", "assets"):
            self.assertFalse((REPO_ROOT / rel).exists(), f"根目錄不應再有 {rel}")


class IndexWrittenToSiteTests(base.MlbTestBase):
    def test_refresh_index_page_writes_site_index_only(self):
        storage.save_metadata(config.METADATA_PATH, {"2026-11-15": {
            "date": "2026-11-15", "total_games": 3, "postponed": 0, "summary": "s", "type": "season"}})
        (self.site / "index.html").unlink()
        index_page.refresh_index_page()
        self.assertTrue((self.site / "index.html").exists())
        self.assertIn("2026-11-15", (self.site / "index.html").read_text(encoding="utf-8"))
        # 暫存目錄根目錄不得出現產生檔
        self.assertFalse((self.tmp / "index.html").exists())
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["site", "templates"])

    def test_update_index_page_writes_metadata_under_site_reports(self):
        index_page.update_index_page({"date": "2026-11-16", "total_games": 1, "postponed": 0, "summary": "x", "type": "season"})
        meta = json.loads((self.site / "reports" / "metadata.json").read_text(encoding="utf-8"))
        self.assertIn("2026-11-16", meta)
        self.assertFalse((self.tmp / "reports").exists())
        self.assertFalse((self.tmp / "index.html").exists())

    def test_report_is_written_under_site_reports(self):
        path = storage.write_report("2026-11-15", "<html></html>")
        self.assertEqual(Path(path), Path("site") / "reports" / "2026" / "11" / "2026-11-15.html")
        self.assertTrue((self.site / "reports" / "2026" / "11" / "2026-11-15.html").exists())
        self.assertFalse((self.tmp / "reports").exists())


if __name__ == "__main__":
    unittest.main()
