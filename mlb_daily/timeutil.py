"""台北時間與日期工具。"""

from datetime import datetime, timedelta, timezone

from .config import WEEKDAYS_ZH


def get_taipei_now():
    tz_taipei = timezone(timedelta(hours=8))
    return datetime.now(timezone.utc).astimezone(tz_taipei)

def get_yesterday_str():
    now = get_taipei_now()
    yesterday = now - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def fmt_date_long(date_str):
    """'2026-07-30' -> '2026 年 07 月 30 日（四）'；格式不符時原樣回傳。"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(date_str)
    return f"{dt.year} 年 {dt.month:02d} 月 {dt.day:02d} 日（{WEEKDAYS_ZH[dt.weekday()]}）"


def report_href(date_str):
    """自網站根目錄起算的戰報路徑：reports/YYYY/MM/YYYY-MM-DD.html。"""
    y, m, _ = date_str.split("-")
    return f"reports/{y}/{m}/{date_str}.html"


def _parse_iso_date(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
