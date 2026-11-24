"""存檔頁月份按鈕依年份分組：本機無 node，以靜態方式檢查 archive.js／style.css，並驗證部署蓋章會涵蓋它們。"""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from mlb_daily import stamp_assets  # noqa: E402


class ArchiveJsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "site" / "assets" / "archive.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "site" / "assets" / "style.css").read_text(encoding="utf-8")

    def test_year_grouping_logic_present(self):
        self.assertIn("function yearsOf(", self.js)
        self.assertRegex(self.js, r"cls:\s*'chip-year'")
        self.assertIn("yearsOf(months)", self.js)

    def test_chip_year_is_not_a_link_and_uses_text_content(self):
        m = re.search(r"el\('span',\s*\{\s*cls:\s*'chip-year',\s*text:", self.js)
        self.assertIsNotNone(m, "年份標籤必須是以 text 寫入（textContent）的 span，不是連結")
        self.assertNotRegex(self.js, r"\.innerHTML\s*[+=]")

    def test_month_chip_text_unchanged(self):
        self.assertIn("Number(parts[1]) + ' 月'", self.js)  # chip 文字仍為「N 月」

    def test_year_label_only_when_multiple_years(self):
        # 只有跨年份才插入；判斷以 yearsOf(...).length > 1 為準
        self.assertRegex(self.js, r"yearsOf\(months\)\.length\s*>\s*1")

    def test_style_has_chip_year_rules(self):
        self.assertIn(".chip-year {", self.css)
        self.assertIn(".chip-year:first-child", self.css)


class ArchiveStampTests(unittest.TestCase):
    def test_archive_html_asset_refs_get_version(self):
        html = (ROOT / "site" / "archive.html").read_text(encoding="utf-8")
        self.assertIn('src="assets/archive.js"', html)
        self.assertIn('href="assets/style.css"', html)
        stamped = stamp_assets.stamp_html(html, "abc1234")
        self.assertIn('src="assets/archive.js?v=abc1234"', stamped)
        self.assertIn('href="assets/style.css?v=abc1234"', stamped)


if __name__ == "__main__":
    unittest.main()
