# 專案進度 (Project Progress)

本專案採用**模組化循序漸進開發**，在每個模組開發完成並經過您的實際測試確認後，才會推進到下一個功能。

> 註：舊階段提到的 `templates/report_template.html` 已於階段十五改名為 `templates/report.html`；各舊階段中已被取代的描述以「※」註記，歷史內容不刪除。

---

## 任務清單

### 📋 階段一：專案文件與開發守則
- [x] 新增 `docs/` 資料夾與 `architecture.md` (專案架構文件)
- [x] 新增 `docs/progress.md` (專案進度表)
- [x] 新增 `docs/dev_rules.md` 與 `.agents/AGENTS.md` (開發守則與約束)
- [x] 新增 `docs/dev_logs/` (開發日誌資料夾與規範)


### 🎨 階段二：HTML 靜態網頁架構與視覺設計
- [x] 建立日戰報 HTML 模板 `templates/report_template.html` (響應式深色模式卡片設計)　※ 已於階段十五改為 `templates/report.html`（插槽式骨架）
- [x] 建立專案首頁 `index.html` (歷程報告索引與今日賽況展示)
- [x] 本地瀏覽器手動測試視覺效果，確認排版與美觀度滿意
 
### 🐍 階段三：Python 數據抓取與 HTML 生成腳本
- [x] 撰寫 `fetch_mlb.py` 核心邏輯 (串接 MLB Stats API 取得賽事 JSON)
- [x] 實作賽事資料解析 (特別處理雙重賽、延賽、延長賽等情境)　※ 並未特別處理雙重賽；階段十五改以 `linescore.scheduledInnings` 判斷延長賽，7 局雙重賽不再誤標
- [x] 修改 `fetch_mlb.py` 數據解析：實作不分勝負隊伍的傑出球員評分與動態篩選（至少 3 名，達門檻加列）
- [x] 實作 HTML 模板填寫與檔案輸出 (輸出至 `reports/YYYY-MM-DD.html`)　※ 階段六起改為 `reports/YYYY/MM/YYYY-MM-DD.html`
- [x] 實作 `index.html` 的歷史報告清單自動更新
- [x] 本地手動測試 `python fetch_mlb.py --date 2026-07-18` 驗證生成結果
 
### ⚙️ 階段四：GitHub Actions 自動化排程 [已完成]
- [x] 設定 `workflows/daily_report.yml` 於每日台北時間 12:00 PM 觸發　※ 現為 `.github/workflows/daily_report.yml`，cron `17 4 * * *`（約台北 12:17），見階段十五
- [x] 實作「開工前檢查」邏輯 (檢查 API 中昨日比賽是否全數結束)
- [x] 實作「延遲等待」機制 (若未全數結束，休眠 30 分鐘後重試)　※ 階段十五加上最多 8 次檢查的上限
- [x] 實作自動 Git Commit & Push 邏輯　※ 現僅推回 `index.html` 與 `reports/metadata.json`，戰報由 gh-pages 部署（階段十三、十五）
- [x] 設定 GitHub Pages 託管並確認能夠線上瀏覽

### 💬 階段五：Discord 通知整合 [已完成]
- [x] 在 `fetch_mlb.py` 中加入 Discord Webhook 串接發送程式
- [x] 設計精美的 Discord Embed 戰報卡片 (包含昨日日期、戰況統計、Pages 連結)　※ 階段十五起改在部署且 Pages 上線後，以 `--notify-only` 發送
- [x] 完整系統跑通測試

### 🛠️ 階段六：優化與進階長期維護 [已完成]
- [x] 重構 `templates/report_template.html` 卡片版面為「方案 B」（上半部滿版對戰與比分表，下半部雙欄展示投手決定與傑出球員）
- [x] 實作歷史戰報目錄分層（`reports/YYYY/MM/`）與首頁動態 JSON 存檔索引　※ 實際並未產生 `archive_index.json`；階段十五改為 `archive.html` 讀取 `reports/metadata.json`
- [x] 實作昨日無賽事自動跳過（藉由「昨日賽事為零」動態適應休賽季與季中休兵日）　※ 已被取代：階段十五起無賽事日改產「休賽季快報」頁面（新聞＋大聯盟人事異動）
- [x] 在網頁上引入 MLB 官方 `team-cap-on-dark` SVG 隊徽，並在 CSS 加入 `filter: drop-shadow` 外發光描邊效果以解決黑底對比度問題

### 📰 階段七：進階內容擴充與優化 [已完成]
- [x] 於每場比賽左下角（投球決定下方）串接展示 MLB 官方英文新聞摘要 (Headline & Blurb)（含未上傳時以即時比分作為備份的防禦機制）
- [x] 串接 MLB Transactions API，實作當日球員人事異動展示。其中有比賽球隊之異動直接與賽事結果合併在比賽卡片內，其餘球隊異動分組展示於網頁最下方。
- [x] 支援休賽季/無賽事日的當日人事異動生成與底部展示。

### 🛠️ 階段八：功能微調與優化 [已完成]
- [x] 優化 `fetch_mlb.py` 數據過濾：在人事異動中過濾包含 `"minor league"` 關鍵字的小聯盟深度合約，避免版面被無大聯盟出賽機會的簽約資訊干擾
- [x] 進階簽約篩選：對於包含 `"signed"` 的異動，必須同時具備大聯盟特徵詞（如 `"free agent"`、`"extension"` 等）才予以顯示，以徹底過濾大量的小聯盟深度簽約與選秀簽約

### 🛠️ 階段九：版面微調與 Bug 修復 [已完成]
- [x] 修復戰報返回連結 404 問題（將 `templates/report_template.html` 中的 `../index.html` 改為 `../../../index.html`）
- [x] 調整返回按鈕為絕對定位（置於左上角，不佔用獨立行空間）

### 📊 階段十：球員進階數據連結 [已完成]
- [x] 於 `templates/report_template.html` 與 `fetch_mlb.py` 中，將球員名稱加上超連結，連結至其 Baseball Savant Profile (`baseballsavant.mlb.com/savant-player/{playerId}`)（包含投球決定、傑出球員、以及人事異動中的球員姓名）

### 🔍 階段十一：搜尋與自動定位跳轉 [已完成]
- [x] 於 `templates/report_template.html` 中新增深色系搜尋輸入框，支援輸入中/英文隊名與縮寫
- [x] 修改 `fetch_mlb.py` 卡片渲染，為每個比賽卡片加上包含雙方隊伍名稱與縮寫的 `data-teams` 屬性
- [x] 撰寫輕量 JavaScript 實作搜尋監聽與匹配，按下 Enter 後使用 `scrollIntoView` 平滑跳轉定位
- [x] 於 CSS 新增跳轉成功的呼吸發光與微縮放動畫（`.highlight-card`）

### 🏷️ 階段十二：焦點球員標籤排版優化 [已完成]
- [x] 新增 `.performer-tags` Flexbox 容器，將 HR 與 SB 標籤統一包覆並靠右併排顯示

### 🚀 階段十三：GitHub Pages 部署與 main 分頁解耦 [已完成]
- [x] 在 `.gitignore` 中忽略每日戰報 `.html` 檔案 (`reports/*/*/*.html`)
- [x] 從 `main` 分頁移除歷史 `.html` 檔案追蹤，保留 `reports/metadata.json` 作為輕量歷史索引數據
- [x] 設定 `.github/workflows/daily_report.yml` 透過 `peaceiris/actions-gh-pages` 將完整網頁發布至 `gh-pages` 分頁

### 🗂️ 階段十四：首頁歷史戰報數量限制優化 [已完成]
- [x] 調整 `index.html` 靜態頁面，僅保留最新 6 場歷史戰報卡片
- [x] 修改 `fetch_mlb.py` 中 `update_index_html()` 邏輯，設限僅顯示最新 6 場歷史戰報（`sorted_dates[:6]`）　※ 階段十五起改為「最新一期大卡＋近期 6 期」，並新增 `archive.html`

### 🔍 階段十五：全面審查與穩定性修正（2026-11-17）
依 `docs/plans/20261117_審查修正計畫.md`，一次一個模組，每個模組附單元測試。

- [x] **模組 E**（`9b9956b`）：新增 `tests/` 與 JSON fixture，只用標準庫 `unittest`，完全不連網；workflow 產生前先跑測試
- [x] **模組 A**（`3a96985`）：API 失敗與「無賽事」分開處理；boxscore 失敗改用簡化卡片；metadata 讀寫失敗不覆寫並以非零碼結束
- [x] **模組 B**（`24d4281`）：等待迴圈上限（8 次 × 30 分鐘）；以 `codedGameState`／`detailedState` 區分 Final、Suspended、Postponed、Cancelled；延長賽改用 `scheduledInnings`；Suspended 不標勝方；季後賽不再顯示「例行賽」
- [x] **模組 C**（`17c17df`、`709cb95`）：workflow 加 `concurrency` 與 `timeout-minutes`；push 前 `pull --rebase` 並重試；順序改為測試→產生→push main→部署→等待 Pages→`--notify-only`；cron 改 `17 4 * * *`；`exclude_assets` 排除程式、文件與 `.gitignore`（修正 8/1 起新戰報未被部署的 bug）
- [x] **模組 H**（`55673ac`）：UI 重新設計靜態設計稿（`docs/mockups/`），使用者核准
- [x] **模組 D+G**（`c25ad77`、`4420e94`、`aee93b6`）：
  - 階段 1：依設計稿改寫渲染，抽出共用 `render_report`，所有外部字串一律 `esc()`，新增 `assets/style.css`、`archive.html`、`templates/report.html`／`index.html`
  - 階段 2：加入 MLB.com 每日新聞；人事異動限縮為大聯盟層級；休賽季快報頁面；metadata 新增 `type`、`news_count`、`transactions_count`
  - 階段 3：以 Gemini 翻譯新聞、賽後新聞與人事異動，失敗退回原文；metadata 新增 `translated`
- [x] **模組 F**：首頁摘要改取焦點積分最高的 3 場；休賽季摘要改用當日新聞標題；取消原因改用獨立對照表；README、架構、開發守則、AGENTS、進度與計畫文件同步
### ⚾ 階段十六：球隊名單頁（2026-11-18）
依 `docs/plans/20261118_球隊名單頁計畫.md`，一次一個模組。

- [x] **模組 R1**（`b8de886`、`843b383`）：球隊名單頁靜態設計稿與全站導覽，使用者核准；野手合併為單一表格並顯示守位
- [x] **模組 R2**（`96192e8`）：`python fetch_mlb.py --rosters`，賽季判斷、分組與欄位計算、JSON 輸出與單元測試
- [x] **模組 R3**（`ae0e1fd`）：正式 `teams.html`、`assets/teams.js` 與全站導覽
- [x] **模組 R4**（本 commit）：workflow 加入 Update Team Rosters 步驟（`continue-on-error`），文件同步
- [ ] 待辦：GitHub 實測（手動觸發 workflow，確認 `data/teams/` 部署與 `teams.html` 可用）

- [x] 已完成：補產 2026-07-19、2026-08-01 至 2026-11-15 的戰報（gh-pages commit 0b627c5，2026-11-18；已核對 08-01～11-17 無缺漏）
- [ ] 待辦：上述補產戰報未經翻譯（內容為英文），是否補翻待決定
- [ ] 待辦：賽季中 RSS 只保留約 25 則，可能不足以涵蓋整日新聞
- [ ] 待辦：賽季卡片待明年開季以真實資料驗證（目前休賽季只能用 fixture）
- [ ] 待辦：首次 Discord 實際通知待排程驗證（模組 C 實測時 `notify=false`）
