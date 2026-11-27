# 開發守則 (Developer Guidelines)

本專案在開發過程中必須嚴格遵守以下守則。**每次開工前，AI Agent 必須閱讀並確認本守則內的所有規則。**

---

## 🚫 核心限制與開發順序

1. **先規劃、後實作（核准關卡）**：
   - **新功能與畫面改動**：實作前必須先給規劃或預覽，等使用者同意才能動手，並確認 `docs/` 下的專案架構與進度表已更新。
   - **修 bug 與文件修正**：可直接進行，不需事先核准。
2. **模組化開發 (One Feature at a Time)**：
   - 每次只開發一個模組或功能。禁止同時修改多個無關的模組。
   - 每個模組完成後，必須進行手動或自動驗證，確認無誤後才能進入下一個模組。
3. **UI 優先 (UI-First Approach)**（適用於新功能與畫面改動）：
   - **必須先做好 HTML/CSS 的靜態頁面架構與視覺設計**（預覽）。
   - 只有在使用者確認視覺效果（深色模式、響應式佈局、美感）滿意並同意後，才能開始撰寫 Python 抓取腳本與自動化功能。

---

## ⚙️ 系統設計守則

4. **排程與開工檢查邏輯**：
   - 主要由外部 cron-job.org 於台北時間 13:00 以 `workflow_dispatch` 觸發；GitHub schedule（`cron: '30 6 * * *'`，即 06:30 UTC、台北 14:30）僅作備援，排在主排程之後。當日戰報已存在於 gh-pages 時，自動觸發由 `check` job 跳過（以 `daily_report.yml` 註解為準）。另外 `schedule` 事件於台北時間 13 點前執行時也跳過（防止備援排程被 GitHub 延遲跨過台北午夜而提前產報）；`workflow_dispatch` 不受影響。
   - 手動觸發（`workflow_dispatch`）輸入：`date`（報表日期）、`notify`（是否發 Discord 通知）、`week`（補產指定週的休賽季每週總整理，ISO 週 `YYYY-Www`）。
   - **開工前置檢查**：腳本以 `--check-wait` 啟動後，先透過 API 檢查目標日（預設為台北時間昨日）所有比賽狀態。API 失敗不得當成「無賽事」：等待中視為未完成並重試，從未取得賽程則 `exit 1`；單次檢查失敗直接 `exit 1`，不寫任何檔案。
   - **延遲等待機制**：
     - 有比賽尚未結束（Preview／Live）時，休眠 **30 分鐘**後重新檢查，**最多檢查 8 次**（`--max-wait-checks`，預設 8）。
     - 全數 Final、Postponed、Cancelled 即開始產生。**Suspended（暫停比賽）不等待**，不視為未完成。
     - 達上限仍有未完成場次時，照常產出戰報，並把這些場次標示為「未完賽」。
   - 無賽事日產出「休賽季快報」頁面（新聞＋大聯盟人事異動），不再跳過。
5. **程式設計原則**：
   - 儘量使用 Python 原生標準函式庫 (如 `urllib.request` 與 `json`)，避免在 GitHub Actions 環境中安裝不必要的第三方依賴套件，確保程式輕量化與執行穩定度。需要 Python 3.12 以上。
   - 金鑰（`DISCORD_WEBHOOK_URL`、`GEMINI_API_KEY`）只能放 GitHub Secrets 或本機環境變數，不得寫進程式、文件或測試。
   - 所有來自外部的字串輸出到 HTML 前一律經過 `esc()`。
6. **通知與傳送及 GitHub Pages 發布架構**：
   - 網站檔案集中在 `site/`，部署時以 `site/` 為發佈根目錄（gh-pages 布局與網址不變）。`main` 主分支保留程式碼、模板、文件與靜態網站檔（`site/archive.html`、`site/teams.html`、`site/assets/`）；`site/reports/metadata.json` 索引檔、首頁 `site/index.html`、`site/leaders.html`、`site/playoffs.html`、`site/data/` 與每日戰報 `.html` 皆為程式產生、只存在 gh-pages，由 `.gitignore` 忽略。workflow 產生前先從 gh-pages 取回 `metadata.json`，產生後隨 gh-pages 部署，不推回 `main`。
   - workflow 步驟順序（完整步驟見 `docs/architecture.md`）：單元測試 → 取回歷史 → 產生（`--check-wait --no-notify`）→ 以 `peaceiris/actions-gh-pages` 部署到 `gh-pages` → 等待 GitHub Pages 上線 → 以 `--notify-only` 發送 Discord 通知。
   - **通知時機**：必須在部署完成且 Pages 回應 200 之後才發送，連結才能直接開啟。休賽季快報若新聞與人事異動皆為 0，不發通知。
   - 部署只發佈 `site/`（`publish_dir: ./site`），程式、文件、`settings` 不在其中，故不需 `exclude_assets`；`site/` 內不得放 `.gitignore`，否則會讓新戰報被忽略。網站路徑一律由 `mlb_daily/config.py` 的 `SITE_DIR` 等常數組出，不要寫死。
   - 所有頁面（`site/archive.html`、`site/teams.html`、`templates/` 下各頁面模板）的 `<head>` 需同時含 `favicon.svg` 與 `apple-touch-icon.png` 兩個 icon link（iOS Safari 加入主畫面不支援 SVG，須有 PNG）；由 `tests/test_favicon.py` 檢查。
   - 網站只在 workflow 執行時部署到 gh-pages；修改 `site/assets/` 或模板等靜態資源後，需等下次排程或手動 `workflow_dispatch`（`notify=false`）補部署才會上線。iOS 主畫面圖示會快取，需刪除舊捷徑重新加入。
7. **測試守則**：
   - 修改程式（`fetch_mlb.py` 或 `mlb_daily/`、模板渲染邏輯）必須附對應的單元測試，並確認 `python -m unittest discover -s tests` 全部通過後才算完成。
   - 測試只用標準庫 `unittest`，以 `tests/fixtures/*.json` 假資料執行，不得連網、不得呼叫真實 Gemini 或 Discord。
   - workflow 會在產生戰報前先跑測試；測試失敗就不產生、不部署、不通知。

---

## 🚀 開工與收工工作流 (Session Commands Workflow)

摘要見根目錄 `CLAUDE.md`；本段為細節。

8. **開工流程**：
   - 先讀 `docs/progress.md`、本檔與 `docs/dev_logs/` 最新月份資料夾中的最新一份日誌，再向使用者提出本次規劃。
   - 核准關卡依守則 1：新功能與畫面改動須先給規劃或預覽並等使用者同意；修 bug、文件修正可直接進行。
9. **「收工」指令流程**：
   - 當使用者說 **「收工」** 時，AI Agent 必須完成以下三件事，缺一不可：
     1. 依 `docs/dev_logs/README.md` 新增開發日誌（命名與內容格式以該檔為準，此處不重複）。
     2. 同步 `docs/progress.md`；若影響架構或規格，同步 `docs/architecture.md` 與本檔。
     3. 由 AI 自行 commit 並 push 到 `origin main`，並在回覆中附上 commit 的 `summary`（標題）與 `description`（詳細描述）。

---

## 📊 戰報數據與呈現規格 (Report Data & Presentation Specs)

### 1. 簡化版面與戰況焦點
- **移除逐局比分 (Linescore)**：不顯示 1 至 9+ 局的得分方格，僅顯示雙方隊伍的最終比分、安打數與失誤數 (R, H, E)。
- **移除主/客勝統計**：頁首及首頁不統計「主隊勝/客隊勝」，僅保留「總場數」與「延賽場數」。
- **精簡球員名單 (Notable Performers)**：不列出雙方全隊完整打擊與投手名單，僅列出「有優異或關鍵表現」的球員。
- **顯示球隊當前戰績**：在戰報中，球隊名稱旁必須附帶當季累計勝敗戰績（格式為 `(勝-敗)`，例如 `紐約洋基 (84-66)`）。

### 2. 打者數據規格 (Batters Specs)
- **篩選標準**：以單場、兩隊合併評分，打者分數 = TB + BB + HBP + SB − CS + 0.5×(R + RBI) − 0.25×(AB − H)；依分數排序最多 4 人，前 2 名分數 ≥ 3 即入選，第 3 名起須 ≥ 5。
- **基本打擊數據**：打數 (AB)、得分 (R)、安打 (H)、打點 (RBI)、保送 (BB)、被三振 (SO)。
- **長打 (Extra-base hits)**：二壘安打 (2B)、三壘安打 (3B)、全壘打 (HR)。
- **速度指標**：盜壘 (SB)。
- **累計數據標記格式**：
  - 必須標記長打與盜壘在「該場比賽的次數」以及「該次數對應的賽季累計總數」。
  - 格式範例：`HR: 1(9)` 代表該場比賽有 1 支全壘打，且該全壘打為賽季第 9 轟。
  - 複數次數範例：`HR: 2(10)(11)` 代表該場比賽有 2 支全壘打，分別為賽季第 10 轟與第 11 轟。
  - 盜壘範例：`SB: 1(15)` 代表該場比賽有 1 次盜壘成功，且為賽季第 15 次盜壘成功。

### 3. 投手數據規格 (Pitchers Specs)
- **篩選標準**：與打者分開評分，投手分數 = 2×出局數 + K − 2×BB − 2×H − 3×R − 6×HR；後援投手 0 失分且拿到救援或中繼成功再 +2，勝投不加分。依分數排序最多 3 人，第 1 名分數 ≥ 8 即入選，其餘先發須 ≥ 20、後援須 ≥ 10。
- **投球數據**：投球局數 (IP)、被安打 (H)、失分 (R)、自責分 (ER)、保送 (BB)、三振 (SO)、總投球數/好球數 (Pitches/Strikes)。
- **移除項目**：**移除**原本規劃的「賽季防禦率 (ERA)」，專注於當場比賽的投球內容。

### 4. 新聞、翻譯與人事異動規格
- **每日新聞**：MLB.com 官方 RSS，取台北時間目標日當天發布者（最多 12 則）。賽季頁附在戰報上方；休賽季頁以新聞與人事異動為主體。
- **深度分析**：FanGraphs RSS（`https://blogs.fangraphs.com/feed/`），取台北時間目標日當天發布者（最多 6 則），濾掉 podcast；連結只允許 https 的 fangraphs.com 網域，不通過者整則捨棄（不得放寬 MLB 新聞的 `_safe_mlb_url`）。附在「今日新聞」下方，無資料或抓取失敗時整個區塊不顯示，不得讓戰報產生失敗；目前不進每日快照、週報與球隊專頁。
- **人事異動**：僅限大聯盟層級——排除 `minor league`；`signed` 需帶大聯盟特徵詞；同一描述的所有紀錄合併為一筆，整組任一紀錄的 `toTeam`／`fromTeam` 為 30 支大聯盟球隊即可；多隊交易在頁尾每支涉及的球隊分組都列出。
- **翻譯（Gemini）**：翻譯範圍為新聞標題、深度分析標題、單場賽後新聞的 Headline／Blurb、人事異動描述。球員姓名保留英文原名、數據縮寫（HR、RBI、IP 等）保留、球隊名稱用內建中文譯名；只顯示中文，不附英文原文（唯一例外：台日韓球員專區的分組標題用設定檔中文名）。無金鑰、額度用盡或回應格式錯誤時改顯示英文原文，不得讓整次執行失敗。
- **metadata 欄位**：`date,total_games,postponed,summary,type,news_count,transactions_count,translated`，另有選填 `spotlight`（台日韓球員專區條目，只在當日有內容時寫入，格式見 `docs/architecture.md`）。
- **休賽季摘要**：以當日新聞前 3 則標題組成；無新聞則為人事異動筆數；皆無為「休賽季，無重大消息」。
- **賽季摘要**：總場數、延賽數，加上焦點積分最高的 3 場。
