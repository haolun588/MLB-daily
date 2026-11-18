"""Discord 通知。"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from . import storage


def should_notify(entry):
    """休賽季快報在新聞與人事異動皆為 0（缺欄位視為 0）時不發通知；其餘照常。"""
    if storage.entry_type(entry) == "offseason":
        return storage._to_int(entry.get("news_count")) > 0 or storage._to_int(entry.get("transactions_count")) > 0
    return True


def send_discord_notification(new_meta):
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("Warning: DISCORD_WEBHOOK_URL env variable not set. Skipping Discord notification.")
        return
    
    date_str = new_meta["date"]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_formatted = dt.strftime("%Y 年 %m 月 %d 日")
    except:
        date_formatted = date_str
        
    parts = date_str.split('-')
    report_url = f"https://haolun588.github.io/MLB-daily/reports/{parts[0]}/{parts[1]}/{date_str}.html"
    
    if storage.entry_type(new_meta) == "offseason":
        parts_text = []
        if "news_count" in new_meta:
            parts_text.append(f"新聞 {storage._to_int(new_meta['news_count'])} 則")
        if "transactions_count" in new_meta:
            parts_text.append(f"人事異動 {storage._to_int(new_meta['transactions_count'])} 筆")
        description = "休賽季快報：" + "、".join(parts_text) if parts_text else "休賽季快報已生成。"
        fields = []
    else:
        description = "昨日焦點戰報與最新賽況已生成，點擊下方連結即可瀏覽完整內容！"
        if "news_count" in new_meta:
            description += f"\n新聞 {storage._to_int(new_meta['news_count'])} 則"
        fields = [
            {
                "name": "📊 賽事統計",
                "value": f"• 總場數：**{new_meta['total_games']}** 場\n• 延賽場數：**{new_meta['postponed']}** 場",
                "inline": True
            },
            {
                "name": "📝 焦點摘要",
                "value": new_meta["summary"]
            }
        ]
        
    payload = {
        "embeds": [
            {
                "title": f"⚾ MLB 每日焦點戰報 - {date_formatted}",
                "description": description,
                "url": report_url,
                "color": 795456,  # Yankees Blue: #0C2340
                "fields": fields,
                "footer": {
                    "text": "MLB Daily 每日戰報系統"
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            print(f"Discord notification sent successfully. Response code: {response.getcode()}")
    except Exception as e:
        print(f"Error sending Discord notification: {e}", file=sys.stderr)
