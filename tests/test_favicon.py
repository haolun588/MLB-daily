"""網站 favicon：靜態頁與模板的 <head> 含 icon link，且 href 相對於輸出位置能指到 site/assets/favicon.svg。"""

import os
import re
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fetch_mlb as base  # noqa: E402

from mlb_daily import config  # noqa: E402

REPO_ROOT = base.REPO_ROOT
FAVICON = REPO_ROOT / config.SITE_DIR / "assets" / "favicon.svg"
APPLE_ICON = REPO_ROOT / config.SITE_DIR / "assets" / "apple-touch-icon.png"
APPLE_RE = re.compile(r'<link rel="apple-touch-icon" href="([^"]+)">')
LINK_RE = re.compile(r'<link rel="icon" type="image/svg\+xml" href="([^"]+)">')


def favicon_href(html):
    m = LINK_RE.search(html)
    return m.group(1) if m else None


def assert_favicon(tc, html, out_dir):
    """html 含 favicon link，且從 out_dir（相對於網站根 site/ 的輸出目錄，如 "reports/2026/11"）解析後指到實際檔案。"""
    href = favicon_href(html)
    tc.assertIsNotNone(href, "缺少 favicon link")
    tc.assertTrue(html.index("<title>") < html.index(href) < html.index("</head>"))
    target = os.path.normpath(os.path.join(REPO_ROOT / config.SITE_DIR, out_dir, href))
    tc.assertEqual(Path(target), FAVICON)
    tc.assertTrue(Path(target).is_file())
    # apple-touch-icon（iOS 不支援 SVG）
    m = APPLE_RE.search(html)
    tc.assertIsNotNone(m, "缺少 apple-touch-icon link")
    tc.assertTrue(html.index("<title>") < html.index(m.group(1)) < html.index("</head>"))
    apple = os.path.normpath(os.path.join(REPO_ROOT / config.SITE_DIR, out_dir, m.group(1)))
    tc.assertEqual(Path(apple), APPLE_ICON)
    tc.assertTrue(Path(apple).is_file())


def rel_to_site(path):
    """以 config 輸出路徑常數取得相對於 SITE_DIR 的目錄。"""
    return os.path.relpath(os.path.dirname(path), config.SITE_DIR)


class FaviconStaticTests(unittest.TestCase):
    def test_svg_exists_and_tracked_location(self):
        self.assertTrue(FAVICON.is_file())
        svg = FAVICON.read_text(encoding="utf-8")
        self.assertIn('viewBox="0 0 64 64"', svg)
        self.assertIn("#e5484d", svg)

    def test_apple_touch_icon_is_180_png(self):
        data = APPLE_ICON.read_bytes()
        self.assertEqual(data[:8], bytes([137, 80, 78, 71, 13, 10, 26, 10]))
        self.assertEqual(data[12:16], b"IHDR")
        self.assertEqual(struct.unpack(">II", data[16:24]), (180, 180))

    def test_static_pages(self):
        site = REPO_ROOT / config.SITE_DIR
        for name in ("archive.html", "teams.html"):
            assert_favicon(self, (site / name).read_text(encoding="utf-8"), ".")

    def test_templates(self):
        # 模板輸出位置：首頁／排行／季後賽在網站根；每日戰報在 reports/YYYY/MM；週報在 reports/weekly
        outputs = {
            "index.html": rel_to_site(config.INDEX_PAGE_PATH),
            "leaders.html": rel_to_site(config.LEADERS_PAGE_PATH),
            "playoffs.html": rel_to_site(config.PLAYOFFS_PAGE_PATH),
            "report.html": os.path.join(rel_to_site(config.METADATA_PATH), "2026", "11"),
            "weekly.html": rel_to_site(config.WEEKLY_INDEX_PATH),
        }
        tpl_dir = REPO_ROOT / "templates"
        for tpl in tpl_dir.glob("*.html"):
            self.assertIn(tpl.name, outputs, f"新模板 {tpl.name} 需登記輸出位置")
        for name, out_dir in outputs.items():
            assert_favicon(self, (tpl_dir / name).read_text(encoding="utf-8"), out_dir)


class FaviconRenderedTests(base.MlbTestBase):
    def test_daily_report_and_index(self):
        self.run_main("schedule_normal")
        report = self.read_report()
        assert_favicon(self, report, os.path.join(rel_to_site(config.METADATA_PATH), "2026", "11"))
        index = (self.site / "index.html").read_text(encoding="utf-8")
        assert_favicon(self, index, rel_to_site(config.INDEX_PAGE_PATH))

    def test_offseason_report(self):
        self.run_main("schedule_empty")
        assert_favicon(self, self.read_report(), os.path.join(rel_to_site(config.METADATA_PATH), "2026", "11"))


if __name__ == "__main__":
    unittest.main()
