"""stamp_assets 測試：只在暫存目錄操作，不動 repo 檔案。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mlb_daily import stamp_assets as sa  # noqa: E402


class StampUrlTests(unittest.TestCase):
    def test_relative_paths(self):
        for prefix in ("", "./", "../", "../../", "../../../"):
            self.assertEqual(sa.stamp_url(f"{prefix}assets/x.js", "abc"), f"{prefix}assets/x.js?v=abc")
        self.assertEqual(sa.stamp_url("assets/style.css", "abc"), "assets/style.css?v=abc")

    def test_replace_existing_v(self):
        self.assertEqual(sa.stamp_url("assets/x.js?v=old", "new"), "assets/x.js?v=new")

    def test_keep_other_query_and_fragment(self):
        self.assertEqual(sa.stamp_url("assets/x.js?a=1", "n"), "assets/x.js?a=1&v=n")
        self.assertEqual(sa.stamp_url("assets/x.js?a=1&v=old&b=2", "n"), "assets/x.js?a=1&b=2&v=n")
        self.assertEqual(sa.stamp_url("assets/x.css#top", "n"), "assets/x.css?v=n#top")

    def test_external_untouched(self):
        for u in ("https://cdn.example.com/assets/x.js", "http://a.com/assets/x.css", "//cdn.x.com/assets/x.js"):
            self.assertEqual(sa.stamp_url(u, "n"), u)

    def test_non_assets_untouched(self):
        for u in ("other/x.js", "/assets/x.js", "assets/data.json", "assets/logo.png", "teams.html", "myassets/x.js"):
            self.assertEqual(sa.stamp_url(u, "n"), u)


class StampHtmlTests(unittest.TestCase):
    def test_quotes_and_tags(self):
        html = """<link rel="stylesheet" href="assets/a.css"><script src='../assets/b.js' defer></script>"""
        self.assertEqual(
            sa.stamp_html(html, "v1"),
            """<link rel="stylesheet" href="assets/a.css?v=v1"><script src='../assets/b.js?v=v1' defer></script>""",
        )

    def test_anchor_href_to_page_untouched(self):
        html = '<a href="teams.html">x</a><a href="https://x.com/assets/a.js">y</a>'
        self.assertEqual(sa.stamp_html(html, "v1"), html)


class StampTreeTests(unittest.TestCase):
    def test_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "reports" / "2026" / "11").mkdir(parents=True)
            (root / "templates").mkdir()
            (root / "docs").mkdir()
            a = root / "a.html"
            sub = root / "reports" / "2026" / "11" / "r.html"
            same = root / "same.html"
            tpl = root / "templates" / "t.html"
            doc = root / "docs" / "d.html"
            a.write_bytes('<script src="assets/a.js"></script>\r\n中文\r\n'.encode("utf-8"))
            sub.write_text('<link href="../../../assets/style.css?v=old">', encoding="utf-8")
            same.write_text('<p>無資源</p>', encoding="utf-8")
            tpl.write_text('<script src="assets/a.js"></script>', encoding="utf-8")
            doc.write_text('<script src="assets/a.js"></script>', encoding="utf-8")
            mtime = same.stat().st_mtime_ns

            self.assertEqual(sa.stamp_tree(root, "9"), 2)
            self.assertEqual(a.read_bytes(), '<script src="assets/a.js?v=9"></script>\r\n中文\r\n'.encode("utf-8"))
            self.assertIn("style.css?v=9", sub.read_text(encoding="utf-8"))
            self.assertEqual(same.stat().st_mtime_ns, mtime)  # 無變動不寫檔
            self.assertNotIn("v=9", tpl.read_text(encoding="utf-8"))
            self.assertNotIn("v=9", doc.read_text(encoding="utf-8"))
            self.assertEqual(sa.stamp_tree(root, "9"), 0)  # 冪等

    def test_main_usage(self):
        self.assertEqual(sa.main(["only-one"]), 2)


if __name__ == "__main__":
    unittest.main()
