"""部署前替 HTML 引用的本站 assets/*.js、assets/*.css 加上 ?v=<版本>，避免瀏覽器快取舊版。

用法：python -m mlb_daily.stamp_assets <目錄> <版本>
只在部署用的工作目錄執行，不要對 repo 原始檔提交結果。"""

import re
import sys
from pathlib import Path

# 這些目錄不會被部署（或不是頁面來源），不走訪
SKIP_DIRS = {
    ".git", ".github", ".agents", ".claude", "node_modules", "__pycache__",
    "docs", "templates", "tests", "mlb_daily", "settings",
}

# src="..." / href='...'；網址本體不含引號
_ATTR_RE = re.compile(r"""(?P<head>\b(?:src|href)\s*=\s*)(?P<q>["'])(?P<url>[^"']*)(?P=q)""", re.IGNORECASE)
# 本站相對網址：可有 ./ 或多層 ../，之後是 assets/…（.js 或 .css），可帶 query 與 fragment
_LOCAL_RE = re.compile(
    r"^(?P<path>(?:\./|(?:\.\./)+)?assets/[^?#]+\.(?:js|css))(?:\?(?P<query>[^#]*))?(?P<frag>#.*)?$",
    re.IGNORECASE,
)


def stamp_url(url: str, version: str) -> str:
    """回傳加上／替換 v 參數後的網址；非本站 assets 的 js/css 原樣回傳。"""
    m = _LOCAL_RE.match(url)
    if not m:
        return url
    parts = [p for p in (m.group("query") or "").split("&") if p and p.split("=", 1)[0] != "v"]
    parts.append(f"v={version}")
    return f"{m.group('path')}?{'&'.join(parts)}{m.group('frag') or ''}"


def stamp_html(text: str, version: str) -> str:
    def repl(m: re.Match) -> str:
        return f"{m.group('head')}{m.group('q')}{stamp_url(m.group('url'), version)}{m.group('q')}"

    return _ATTR_RE.sub(repl, text)


def stamp_tree(root: Path, version: str) -> int:
    """走訪 root 下所有 .html，只改有變動的檔案；回傳改動檔數。"""
    changed = 0
    for path in sorted(root.rglob("*.html")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        raw = path.read_bytes()  # bytes 讀寫，保留原編碼與換行
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            print(f"略過非 UTF-8 檔案：{path}", file=sys.stderr)
            continue
        new = stamp_html(text, version)
        if new != text:
            path.write_bytes(new.encode("utf-8"))
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not re.fullmatch(r"[0-9A-Za-z._-]+", args[1]):
        print("用法：python -m mlb_daily.stamp_assets <目錄> <版本>", file=sys.stderr)
        return 2
    root = Path(args[0])
    if not root.is_dir():
        print(f"目錄不存在：{root}", file=sys.stderr)
        return 2
    print(f"已替 {stamp_tree(root, args[1])} 個 HTML 檔案加上資源版本號 v={args[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
