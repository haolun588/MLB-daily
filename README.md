# MLB Daily Scores Report (MLB 每日戰報)

[![GitHub Pages](https://img.shields.io/badge/GitHub%20Pages-Active-brightgreen)](https://haolun588.github.io/MLB-daily/)

輕量化、無伺服器 (Serverless) 的 MLB 每日戰報系統。GitHub Actions 每日定時執行 Python 腳本，抓取 MLB 官方資料，產生深色為主（跟隨系統切換淺色）的響應式靜態網頁，部署到 GitHub Pages，最後以 Discord Webhook 推送通知與連結。

---

## 功能

- **賽季戰報**：前一日（台北時間）所有賽事的比分、R/H/E、投球決定（勝／敗／救援）、打擊與投球焦點球員（球員名連到 Baseball Savant）、賽後新聞、當日人事異動，加上當日 MLB.com 新聞。頁面內有隊伍搜尋（中英文隊名或縮寫，可再按 Enter 跳到下一場）。
- **休賽季快報**：昨日無賽事時，產出「新聞＋大聯盟層級人事異動」頁面，不會留白。
- **歷史存檔**：[archive.html](archive.html) 以 JavaScript 讀取 `reports/metadata.json`，依月份分組；首頁顯示最新一期與近期清單。
- **Gemini 翻譯**：新聞標題、單場賽後新聞（Headline／Blurb）與人事異動描述以 Gemini 翻成繁體中文；球員姓名維持英文原名，球隊名稱沿用程式內建譯名。沒有金鑰、額度用盡或回應格式錯誤時，自動改用英文原文，不會讓整次執行失敗。
- **球隊名單頁**：[teams.html](teams.html) 點選隊徽顯示該隊名單（賽季中 26 人現役、休賽季 40 人），分 SP、RP、野手三張表並附本季數據，可排序，網址可帶球隊縮寫（如 `teams.html#NYY`）。名單資料由每日 workflow 更新。
- **Discord 通知**：頁面部署且 Pages 可開啟後才發送；休賽季快報若無新聞與人事異動則不發。

---

## 架構與資料流

分支分工：

- `main`：只存程式碼、模板、共用資源、文件。歷史索引 `reports/metadata.json`、首頁 `index.html` 與每日戰報 HTML 都不進 `main`（`.gitignore` 忽略）。
- `gh-pages`：存放完整網頁（戰報、首頁、`archive.html`、`assets/`），由 workflow 部署，`keep_files: true` 保留歷史戰報。

Workflow（`.github/workflows/daily_report.yml`）步驟順序：

1. **Run Unit Tests**：`python -m unittest discover -s tests -v`，失敗即中止，不產生、不部署、不通知。
2. **Resolve report date**：手動輸入的日期，或預設台北時間昨日。
3. **Restore history from gh-pages**（位於產生報表之前）：從 `gh-pages` 取回 `reports/metadata.json`；取不到或不是合法 JSON 物件即失敗，避免以單筆 metadata 覆蓋歷史。
4. **Generate Daily Report**：`python fetch_mlb.py --check-wait --no-notify --date ...`。尚有未完賽場次時每 30 分鐘重新檢查，最多 8 次；達上限後照常產出並把未完成場次標示為「未完賽」。
5. **Update Team Rosters**：`python fetch_mlb.py --rosters`，更新 `data/teams/`。`continue-on-error`，失敗不影響戰報與通知。
6. **Deploy Site to gh-pages**：`peaceiris/actions-gh-pages`，並以 `exclude_assets` 排除程式與文件；`data/teams/` 隨之部署。
7. **Wait for GitHub Pages**：輪詢當日戰報網址直到回應 200（最多約 5 分鐘）。
8. **Send Discord Notification**：`python fetch_mlb.py --notify-only --date ...`，只讀既有 metadata，不呼叫 API。僅在排程觸發，或手動觸發且 `notify` 勾選時執行。

排程：每日 04:17 UTC（約台北時間 12:17）。`concurrency` 群組 `daily-report` 避免重疊執行，job 逾時上限 330 分鐘。

細節見 [docs/architecture.md](docs/architecture.md)。

---

## 需要的 Secrets

在 GitHub Repository 的 Settings → Secrets and variables → Actions 設定：

| 名稱 | 必要 | 說明 |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | 要通知時必填 | Discord Webhook 網址 |
| `GEMINI_API_KEY` | 要翻譯時必填 | 未設定則不翻譯，顯示英文原文 |

可選的環境變數 `GEMINI_MODEL`：覆寫預設的 Gemini 模型名稱。可選的環境變數 `GEMINI_FALLBACK_MODEL`：主模型遇到暫時性錯誤（429／5xx／逾時）重試仍失敗時改用的備用模型，預設 `gemini-3.6-flash`，設為空字串則停用。金鑰一律放在 Secrets 或本機環境變數，不得寫進程式或文件。

---

## 本機執行

需求：**Python 3.12 以上**，只用標準函式庫，不需安裝任何套件。

```bash
# 產生指定日期的戰報（不發 Discord 通知）
python fetch_mlb.py --date 2026-07-20 --no-notify

# 預設日期為台北時間昨日；--check-wait 會等待完賽（排程使用）
python fetch_mlb.py --check-wait --no-notify

# 只依既有 reports/metadata.json 發送某日通知（需設定 DISCORD_WEBHOOK_URL）
python fetch_mlb.py --notify-only --date 2026-07-20

# 抓取 30 隊名單與本季數據，寫入 data/teams/（不產生戰報、不發通知）
python fetch_mlb.py --rosters

# 執行單元測試（完全不連網）
python -m unittest discover -s tests
```

其他參數：`--max-wait-checks N`（等待迴圈最多檢查次數，預設 8）。

環境變數設定範例：

```bash
# Windows (PowerShell)
$env:DISCORD_WEBHOOK_URL="你的Discord_Webhook_網址"
$env:GEMINI_API_KEY="你的Gemini金鑰"

# Linux / macOS
export DISCORD_WEBHOOK_URL="你的Discord_Webhook_網址"
export GEMINI_API_KEY="你的Gemini金鑰"
```

### 手動觸發 workflow

到 GitHub 的 Actions 分頁選擇「Daily MLB Report Generator」→ Run workflow：

- `date`：報表日期 `YYYY-MM-DD`，留空為台北時間昨日。
- `notify`：是否發送 Discord 通知，預設不發送（可先驗證流程，確認無誤再勾選）。

---

## 目錄結構

```
fetch_mlb.py              核心腳本：抓取 → 資料抽取 → view → 渲染，另含翻譯、通知
templates/
  report.html             戰報與休賽季快報的頁面骨架（含 <!--SLOT:...--> 插槽）
  index.html              首頁骨架
assets/
  style.css               共用樣式表（部署到 gh-pages）
  archive.js              歷史存檔頁的動態腳本
  teams.js                球隊名單頁的動態腳本
index.html                首頁（由腳本依 metadata 全量重新產生）
archive.html              歷史存檔頁（讀取 reports/metadata.json）
teams.html                球隊名單頁（讀取 data/teams/*.json）
data/teams/               名單 JSON（index.json、{teamId}.json；僅部署於 gh-pages，main 忽略）
reports/
  metadata.json           每日索引（僅存在 gh-pages；workflow 每次先取回）
tests/
  test_fetch_mlb.py       單元測試（unittest）
  fixtures/               測試用 API 假資料
.github/workflows/
  daily_report.yml        排程與部署
docs/
  architecture.md         專案架構
  dev_rules.md            開發守則與規格
  progress.md             進度表
  ai_collaboration_guide.md  AI 協作流程
  plans/                  修改計畫
  dev_logs/               開發日誌
  mockups/                UI 設計稿（歷史）
.agents/AGENTS.md         AI Agent 工作區規則
```

---

## 開發守則

本專案採「先規劃、後實作」、「UI 優先」、「模組化循序漸進」，修改程式必須附單元測試。詳見 [docs/dev_rules.md](docs/dev_rules.md)，AI Agent 規則見 [.agents/AGENTS.md](.agents/AGENTS.md)。
