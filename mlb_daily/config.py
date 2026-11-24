"""所有模組層級常數（不含執行期狀態）。"""

import os
import re
from datetime import timedelta, timezone


# MLB Team Metadata: Chinese Name, Abbreviation, Official Color Hex, League (AL/NL), Division (E/C/W)
TEAM_META = {
    108: {"name": "洛杉磯天使", "abbr": "LAA", "color": "#BA0021", "league": "AL", "division": "W"},
    109: {"name": "亞利桑那響尾蛇", "abbr": "AZ", "color": "#A71930", "league": "NL", "division": "W"},
    110: {"name": "巴爾的摩金鶯", "abbr": "BAL", "color": "#DF4601", "league": "AL", "division": "E"},
    111: {"name": "波士頓紅襪", "abbr": "BOS", "color": "#BD3039", "league": "AL", "division": "E"},
    112: {"name": "芝加哥小熊", "abbr": "CHC", "color": "#0E3386", "league": "NL", "division": "C"},
    113: {"name": "辛辛那提紅人", "abbr": "CIN", "color": "#C6011F", "league": "NL", "division": "C"},
    114: {"name": "克里夫蘭守護者", "abbr": "CLE", "color": "#0C2340", "league": "AL", "division": "C"},
    115: {"name": "科羅拉多洛磯", "abbr": "COL", "color": "#333366", "league": "NL", "division": "W"},
    116: {"name": "底特律老虎", "abbr": "DET", "color": "#0C2340", "league": "AL", "division": "C"},
    117: {"name": "休士頓太空人", "abbr": "HOU", "color": "#EB6E1F", "league": "AL", "division": "W"},
    118: {"name": "堪薩斯皇家", "abbr": "KC", "color": "#004687", "league": "AL", "division": "C"},
    119: {"name": "洛杉磯道奇", "abbr": "LAD", "color": "#005A9C", "league": "NL", "division": "W"},
    120: {"name": "華盛頓國民", "abbr": "WSH", "color": "#AB0003", "league": "NL", "division": "E"},
    121: {"name": "紐約大都會", "abbr": "NYM", "color": "#FF5910", "league": "NL", "division": "E"},
    133: {"name": "運動家", "abbr": "ATH", "color": "#003831", "league": "AL", "division": "W"},
    134: {"name": "匹茲堡海盜", "abbr": "PIT", "color": "#FDB827", "league": "NL", "division": "C"},
    135: {"name": "聖地牙哥教士", "abbr": "SD", "color": "#2F241D", "league": "NL", "division": "W"},
    136: {"name": "西雅圖水手", "abbr": "SEA", "color": "#0C2C56", "league": "AL", "division": "W"},
    137: {"name": "舊金山巨人", "abbr": "SF", "color": "#FD5A1E", "league": "NL", "division": "W"},
    138: {"name": "聖路易紅雀", "abbr": "STL", "color": "#C41E3A", "league": "NL", "division": "C"},
    139: {"name": "坦帕灣光芒", "abbr": "TB", "color": "#092C5C", "league": "AL", "division": "E"},
    140: {"name": "德州遊騎兵", "abbr": "TEX", "color": "#003278", "league": "AL", "division": "W"},
    141: {"name": "多倫多藍鳥", "abbr": "TOR", "color": "#134A8E", "league": "AL", "division": "E"},
    142: {"name": "明尼蘇達雙城", "abbr": "MIN", "color": "#002B5C", "league": "AL", "division": "C"},
    143: {"name": "費城費城人", "abbr": "PHI", "color": "#E81828", "league": "NL", "division": "E"},
    144: {"name": "亞特蘭大勇士", "abbr": "ATL", "color": "#CE1141", "league": "NL", "division": "E"},
    145: {"name": "芝加哥白襪", "abbr": "CWS", "color": "#27251F", "league": "AL", "division": "C"},
    146: {"name": "邁阿密馬林魚", "abbr": "MIA", "color": "#00A3E0", "league": "NL", "division": "E"},
    147: {"name": "紐約洋基", "abbr": "NYY", "color": "#0C2340", "league": "AL", "division": "E"},
    158: {"name": "密爾瓦基釀酒人", "abbr": "MIL", "color": "#122853", "league": "NL", "division": "C"}
}

# 舊縮寫別名（搜尋時舊縮寫仍可命中；縮寫已統一為 MLB 官方 API）
LEGACY_ABBR_ALIASES = {"AZ": "ARI", "KC": "KCR", "ATH": "OAK"}

POSTPONED_REASONS = {
    "Rain": "因雨延期",
    "Wet Grounds": "場地濕滑延期",
    "Cold": "低溫延期",
    "Inclement Weather": "惡劣天氣延期",
    "Snow": "因雪延期",
    "Postponed": "賽事延期"
}

# 取消（Cancelled）原因對照；不沿用延期文案以免語句不通順
CANCELLED_REASONS = {
    "Rain": "因雨取消",
    "Wet Grounds": "因場地濕滑取消",
    "Cold": "因低溫取消",
    "Inclement Weather": "因惡劣天氣取消",
    "Snow": "因雪取消",
}

# 等待迴圈：每次檢查未完成時睡 30 分鐘，最多檢查 MAX_WAIT_CHECKS 次
MAX_WAIT_CHECKS = 8
WAIT_SLEEP_SECONDS = 1800

GAME_TYPE_LABELS = {
    "R": "例行賽",
    "F": "外卡賽",
    "D": "分區系列賽",
    "L": "聯盟冠軍賽",
    "W": "世界大賽",
    "S": "春訓",
    "E": "熱身賽",
    "A": "明星賽",
}

# 渲染用常數
# 網站發佈根目錄：所有要部署到 gh-pages 的檔案（含程式產生的）都放在這裡
SITE_DIR = "site"
REPORTS_DIR = os.path.join(SITE_DIR, "reports")
METADATA_PATH = os.path.join(REPORTS_DIR, "metadata.json")
INDEX_PAGE_PATH = os.path.join(SITE_DIR, "index.html")
TEMPLATE_DIR = "templates"
SAVANT_URL = "https://baseballsavant.mlb.com/savant-player/{}"
LOGO_URL = "https://www.mlbstatic.com/team-logos/team-cap-on-{}/{}.svg"
WEEKDAYS_ZH = "一二三四五六日"
DARK_LUMINANCE_THRESHOLD = 0.08  # 隊色相對亮度低於此值，色條加淺色外框
SLOT_RE = re.compile(r"<!--SLOT:([A-Z_]+)-->")

# 每日新聞（MLB 官方 RSS）
NEWS_RSS_URL = "https://www.mlb.com/feeds/news/rss.xml"
NEWS_SOURCE = "MLB.com"
# 深度分析（FanGraphs RSS）
ANALYSIS_RSS_URL = "https://blogs.fangraphs.com/feed/"
ANALYSIS_SOURCE = "FanGraphs"
ANALYSIS_LIMIT = 6
ANALYSIS_MAX_PAGES = 5  # FanGraphs feed 每頁 10 篇；最多往後翻到第 5 頁（補產舊日期用）
# RSS 累積快取（MLB RSS 固定 25 篇、不支援分頁，補產舊日期只能靠每日累積）
FEED_CACHE_PATH = os.path.join(SITE_DIR, "data", "feed_cache.json")
FEED_CACHE_DAYS = 21
FEED_CACHE_MAX_ITEMS = 3000
FEED_CACHE_SCHEMA = 1
# 漏跑日自動補產與週報補產
BACKFILL_DAYS = 7
WEEKLY_BACKFILL_WEEKS = 4
TAIPEI_TZ = timezone(timedelta(hours=8))
_UNSET = object()  # 「未提供參數」的哨兵值（None 代表呼叫端已取得但失敗）

# Gemini 翻譯（Interactions API，只用標準函式庫；金鑰只從環境變數讀取、只放 header）
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash-lite"
GEMINI_API_REVISION = "2026-05-20"
TRANSLATE_BATCH_SIZE = 60

# 球隊英文全名／暱稱／中文簡稱（供翻譯對照表；中文全名取自 TEAM_META）
TEAM_NAMES_EN = {
    108: ("Los Angeles Angels", "Angels", "天使"),
    109: ("Arizona Diamondbacks", "Diamondbacks", "響尾蛇"),
    110: ("Baltimore Orioles", "Orioles", "金鶯"),
    111: ("Boston Red Sox", "Red Sox", "紅襪"),
    112: ("Chicago Cubs", "Cubs", "小熊"),
    113: ("Cincinnati Reds", "Reds", "紅人"),
    114: ("Cleveland Guardians", "Guardians", "守護者"),
    115: ("Colorado Rockies", "Rockies", "洛磯"),
    116: ("Detroit Tigers", "Tigers", "老虎"),
    117: ("Houston Astros", "Astros", "太空人"),
    118: ("Kansas City Royals", "Royals", "皇家"),
    119: ("Los Angeles Dodgers", "Dodgers", "道奇"),
    120: ("Washington Nationals", "Nationals", "國民"),
    121: ("New York Mets", "Mets", "大都會"),
    133: ("Athletics", "Athletics", "運動家"),
    134: ("Pittsburgh Pirates", "Pirates", "海盜"),
    135: ("San Diego Padres", "Padres", "教士"),
    136: ("Seattle Mariners", "Mariners", "水手"),
    137: ("San Francisco Giants", "Giants", "巨人"),
    138: ("St. Louis Cardinals", "Cardinals", "紅雀"),
    139: ("Tampa Bay Rays", "Rays", "光芒"),
    140: ("Texas Rangers", "Rangers", "遊騎兵"),
    141: ("Toronto Blue Jays", "Blue Jays", "藍鳥"),
    142: ("Minnesota Twins", "Twins", "雙城"),
    143: ("Philadelphia Phillies", "Phillies", "費城人"),
    144: ("Atlanta Braves", "Braves", "勇士"),
    145: ("Chicago White Sox", "White Sox", "白襪"),
    146: ("Miami Marlins", "Marlins", "馬林魚"),
    147: ("New York Yankees", "Yankees", "洋基"),
    158: ("Milwaukee Brewers", "Brewers", "釀酒人"),
}

# 球隊口語簡稱（team id -> 英文簡稱）；中文沿用 TEAM_NAMES_EN 的中文簡稱。
# 刻意不收 "Sox"（紅襪／白襪有歧義）。
TEAM_SLANG_ALIASES = {
    108: ["Halos"],
    109: ["D-backs", "Dbacks"],
    110: ["O's"],
    115: ["Rox"],
    120: ["Nats"],
    133: ["A's"],
    134: ["Bucs"],
    135: ["Friars"],
    136: ["M's"],
    138: ["Cards"],
    141: ["Jays"],
    143: ["Phils"],
    147: ["Yanks"],
    158: ["Brew Crew"],
}


GEMINI_RETRY_DELAYS = (5, 15, 45)  # 首次失敗後各次重試前的等待秒數（共最多 4 次嘗試）
GEMINI_RETRY_AFTER_CAP = 120
GEMINI_RETRYABLE_HTTP = (429, 500, 502, 503, 504)
GEMINI_FALLBACK_MODEL = "gemini-3.6-flash"


# ---------------------------------------------------------------- 首頁側欄與左欄補充區塊（模組 S2）
STANDINGS_URL = ("https://statsapi.mlb.com/api/v1/standings?leagueId=103,104&season={season}"
                 "&standingsTypes=regularSeason")
SCHEDULE_PREVIEW_URL = "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=probablePitcher,team"
PEOPLE_STATS_URL = ("https://statsapi.mlb.com/api/v1/people?personIds={ids}"
                    "&hydrate=stats(group=[pitching],type=[season],season={season})")
SEASON_URL = "https://statsapi.mlb.com/api/v1/seasons/{year}?sportId=1"
SEASON_CACHE_PATH = os.path.join(SITE_DIR, "data", "season_cache.json")
SEASON_SCHEDULE_URL = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R&startDate={start}&endDate={end}"
                       "&fields=dates,date,games,status,abstractGameState,detailedState,codedGameState")
SEASON_RANGE_SCHEDULE_URL = ("https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameType=R,F,D,L,W&startDate={start}&endDate={end}"
                             "&fields=dates,date,games,status,abstractGameState,detailedState,codedGameState")
TRANSACTIONS_RANGE_URL = ("https://statsapi.mlb.com/api/v1/transactions?startDate={start}&endDate={end}"
                          "&sportId=1")

# 版面順序：美聯東中西、國聯東中西（MLB API 的 division id）
DIVISION_LAYOUT = (
    (201, "美聯東區"), (202, "美聯中區"), (200, "美聯西區"),
    (204, "國聯東區"), (205, "國聯中區"), (203, "國聯西區"),
)
NL_DIVISION_IDS = (204, 205, 203)
# clinchIndicator：w、y、z 取自 MLB 官方戰績頁（mlb.com/standings）Glossary；x 官方頁面未定義，
# 依通用慣例（取得季後賽資格）對照，API 實際會使用。未知字母只顯示字母，不入圖例。
CLINCH_LABELS = {"z": "分區冠軍＋聯盟最佳戰績", "y": "分區冠軍", "x": "取得季後賽資格", "w": "外卡"}
CLINCH_LEGEND = (
    ("z", "分區冠軍，且取得聯盟最佳戰績"),
    ("y", "分區冠軍"),
    ("x", "取得季後賽資格"),
    ("w", "外卡"),
)
WEEKLY_TX_DAYS = 7
WEEKLY_TX_LIMIT = 8
WEEKLY_TX_TYPE_CODES = ("TR", "SFA")  # 交易、自由球員簽約
WEEKLY_TX_EXTENSION_RE = re.compile(r"\bextension\b")  # 只認 extension（含 contract extension），避免 extended spring training 誤入選
EARLY_MORNING_HOUR = 6  # 台北時間早於此時視為「凌晨」


# ---------------------------------------------------------------- 首頁
DATE_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------- 球隊專頁（--rosters）
ROSTER_DIR = os.path.join(SITE_DIR, "data", "teams")
ROSTER_FETCH_INTERVAL = 0.5


# ---------------------------------------------------------------- 台日韓球員專區
WATCHLIST_PATH = os.path.join("settings", "watchlist.json")
DYNAMIC_WATCHLIST_PATH = os.path.join(SITE_DIR, "data", "watchlist.json")  # --watchlist 從 MLB API 抓取的快取


# ---------------------------------------------------------------- 休賽季每週總整理
SITE_BASE_URL = "https://haolun588.github.io/MLB-daily/"
DAILY_SNAPSHOT_DIR = os.path.join(SITE_DIR, "data", "daily")
DAILY_SNAPSHOT_SCHEMA = 1
WEEKLY_DIR = os.path.join(REPORTS_DIR, "weekly")
WEEKLY_INDEX_PATH = os.path.join(WEEKLY_DIR, "index.json")
OFFSEASON_CALENDAR_PATH = os.path.join("settings", "offseason_calendar.json")
WEEK_KEY_RE = re.compile(r"^(\d{4})-W(\d{2})$")
WEEKLY_NEWS_LIMIT = 10
WEEKLY_NEWS_WATCHLIST_BONUS = 2  # 提到台日韓追蹤球員
WEEKLY_NEWS_HIGHLIGHT_BONUS = 1  # 提到本週重點異動球員
WEEKLY_MINOR_CONTRACT_RE = re.compile(r"minor league contract")
# 分類（顯示順序）：(key, 中文名稱)。「不列入」的異動不在此表，classify_weekly_transaction 回傳 None。
WEEKLY_CATEGORIES = (
    ("trade", "交易"),
    ("signing", "自由球員簽約"),
    ("extension", "延長合約"),
    ("dfa", "DFA／釋出／讓渡"),
    ("free_agent", "成為自由球員"),
    ("roster40", "40 人名單異動"),
)
# typeCode -> 分類 key（延長合約依描述判斷，SFA 含 minor league contract 不列入，見 weekly.classify_weekly_transaction）。
# 未列入此表的 typeCode（SC、ASG、RTN、OPT、NUM、RET 等）一律不列入週報。
WEEKLY_TYPE_CATEGORY = {
    "TR": "trade",
    "SFA": "signing",
    "DES": "dfa",    # designated for assignment（指定讓渡）
    "REL": "dfa",    # released
    "OUT": "dfa",    # outrighted
    "DFA": "free_agent",  # API 的 DFA 是 Declared Free Agency（成為自由球員），不是指定讓渡
    "SE": "roster40",     # selected（上 40 人名單）
    "CLW": "roster40",    # claimed off waivers
    "R5": "roster40",     # Rule 5
    "R5M": "roster40",
}
# 「本週重點」收錄的分類
WEEKLY_HIGHLIGHT_CATEGORIES = ("trade", "signing", "extension")


# ---------------------------------------------------------------- 數據排行榜
LEADERS_PATH = os.path.join(SITE_DIR, "data", "leaders.json")
LEADERS_PAGE_PATH = os.path.join(SITE_DIR, "leaders.html")
LEADERS_SCHEMA = 1
LEADERS_LIMIT = 10
LEADERS_TOP_VISIBLE = 5  # 排行頁每張卡直接顯示的名次；其餘放進 <details>
LEADERS_HITTING_URL = ("https://statsapi.mlb.com/api/v1/stats/leaders?leaderCategories="
                       "battingAverage,onBasePercentage,homeRuns,onBasePlusSlugging,stolenBases"
                       "&statGroup=hitting&season={season}&sportId=1&limit=10")
LEADERS_PITCHING_URL = ("https://statsapi.mlb.com/api/v1/stats/leaders?leaderCategories="
                        "earnedRunAverage,strikeouts,saves,strikeoutsPer9Inn"
                        "&statGroup=pitching&season={season}&sportId=1&limit=10")
SABERMETRICS_URL = ("https://statsapi.mlb.com/api/v1/stats?stats=sabermetrics&group={group}"
                    "&season={season}&sportId=1&playerPool={pool}&limit={limit}")
# 排行項目（顯示順序）：(key, 群組, 中文名稱, 門檻註記)。key 即 leaderCategory，或 fip／war_hitting／war_pitching
LEADERS_CATEGORIES = (
    ("battingAverage", "hitting", "打擊率", "規定打席"),
    ("onBasePercentage", "hitting", "上壘率", "規定打席"),
    ("homeRuns", "hitting", "全壘打", "不設門檻"),
    ("onBasePlusSlugging", "hitting", "OPS", "規定打席"),
    ("stolenBases", "hitting", "盜壘", "不設門檻"),
    ("war_hitting", "hitting", "打者 WAR", "MLB 官方數據・不設門檻"),
    ("earnedRunAverage", "pitching", "防禦率", "規定局數"),
    ("strikeouts", "pitching", "三振", "不設門檻"),
    ("saves", "pitching", "救援成功", "不設門檻"),
    ("strikeoutsPer9Inn", "pitching", "K/9", "規定局數"),
    ("fip", "pitching", "FIP", "規定局數・MLB 官方數據"),
    ("war_pitching", "pitching", "投手 WAR", "MLB 官方數據・不設門檻"),
)


# ---------------------------------------------------------------- 季後賽對戰樹
POSTSEASON_SERIES_URL = "https://statsapi.mlb.com/api/v1/schedule/postseason/series?season={season}&sportId=1"
PLAYOFFS_PATH = os.path.join(SITE_DIR, "data", "playoffs.json")
PLAYOFFS_PAGE_PATH = os.path.join(SITE_DIR, "playoffs.html")
PLAYOFFS_SCHEMA = 1

# ---- 球隊專頁（近期動態與隊內數據王）
TEAM_PAGE_RECENT_DAYS = 30        # 近期動態回溯天數（以報表日＝執行日前一天為終點，往前共 30 天）
TEAM_PAGE_NEWS_LIMIT = 5          # 每隊顯示的相關新聞則數
TEAM_PAGE_LEADER_MIN_PA = 300     # AVG、OPS 的打席門檻
TEAM_PAGE_LEADER_MIN_IP = 50      # ERA、WHIP 的投球局數門檻

# 新聞隊名比對要排除的前綴：這些球團（日、韓、台等聯盟）的暱稱與大聯盟球隊同名（如 Yomiuri Giants、Kia Tigers、LG Twins）；
# "X" 用來排除 X-rays。比對時不分大小寫，且前綴與暱稱之間只允許空白或連字號。
NON_MLB_TEAM_PREFIXES = (
    "Yomiuri", "Hanshin", "Chunichi", "Hiroshima", "Toyo", "Yakult", "Tokyo Yakult", "DeNA", "Yokohama",
    "Orix", "SoftBank", "Nippon-Ham", "Nippon Ham", "Seibu", "Rakuten", "Lotte",
    "Kia", "KIA", "LG", "Samsung", "SSG", "Doosan", "Kiwoom", "Hanwha", "NC", "KT",
    "CTBC", "Uni-President", "Fubon", "X",
)
