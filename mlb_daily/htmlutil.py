"""HTML 跳脫、顏色、隊徽與球員連結、模板載入與填入。"""

import html as html_lib
import os
import re
import sys

from .config import (
    DARK_LUMINANCE_THRESHOLD,
    LOGO_URL,
    SAVANT_URL,
    SLOT_RE,
    TEMPLATE_DIR,
)


# ---------------------------------------------------------------- 渲染：共用工具
def esc(s):
    """所有來自 API 的字串輸出到 HTML（含屬性值）前一律經過此函式。"""
    return html_lib.escape(str(s), quote=True)


def safe_color(color):
    return color if re.fullmatch(r"#[0-9A-Fa-f]{6}", str(color)) else "#6b7280"


def relative_luminance(color):
    """WCAG 相對亮度（0 = 黑、1 = 白）。"""
    color = safe_color(color)
    channels = []
    for i in (1, 3, 5):
        c = int(color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def is_dark_color(color):
    """隊色相對深色背景過暗（色條會消失）時為 True。"""
    return relative_luminance(color) < DARK_LUMINANCE_THRESHOLD


def stripe_class(color):
    """隊色過暗時回傳需追加的 class（含前導空白），否則空字串。"""
    return " team-stripe--dark" if is_dark_color(color) else ""


def _int_id(value):
    """id 必須是整數（或純數字字串），否則回傳 None。"""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def logo_picture(team_id, abbr, size, alt=None):
    """隊徽；id 不是整數時不輸出圖片（回傳空字串）。alt 預設為 abbr；旁邊已有文字名稱時可傳 "" 當裝飾圖。"""
    tid = _int_id(team_id)
    if tid is None:
        return ""
    if alt is None:
        alt = abbr
    return (
        f'<picture><source media="(prefers-color-scheme: light)" srcset="{LOGO_URL.format("light", tid)}">'
        f'<img src="{LOGO_URL.format("dark", tid)}" width="{size}" height="{size}" alt="{esc(alt)}" loading="lazy"></picture>'
    )


def player_link(player_id, name):
    """球員 Savant 連結；id 不是整數時退回只輸出 escape 後的名字。"""
    pid = _int_id(player_id)
    if pid is None:
        return esc(name)
    return (
        f'<a class="plink" href="{SAVANT_URL.format(pid)}" '
        f'target="_blank" rel="noopener">{esc(name)}</a>'
    )


def load_template(name):
    path = os.path.join(TEMPLATE_DIR, name)
    if not os.path.exists(path):
        print(f"Error: Template file {path} not found.", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def fill_slots(template, slots):
    """以 <!--SLOT:NAME--> 為插槽，一次性替換（插槽內容不會被再次替換）；未提供的插槽輸出空字串。"""
    return SLOT_RE.sub(lambda m: str(slots.get(m.group(1), "")), template)


def render_page(slots):
    """戰報與休賽季快報共用的頁面殼（templates/report.html）。slots 的值須為已處理好的 HTML。"""
    return fill_slots(load_template("report.html"), slots)


def _section_message(heading_id, heading, message, status=False):
    role = ' role="status"' if status else ""
    return (f'  <section class="section" aria-labelledby="{heading_id}">\n'
            f'    <div class="section-head"><h2 id="{heading_id}">{heading}</h2></div>\n'
            f'    <p class="section-empty"{role}>{message}</p>\n  </section>')
