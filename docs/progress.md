# 專案進度 (Project Progress)

本專案採用**模組化循序漸進開發**，在每個模組開發完成並經過您的實際測試確認後，才會推進到下一個功能。

---

## 任務清單

### 📋 階段一：專案文件與開發守則
- [x] 新增 `docs/` 資料夾與 `architecture.md` (專案架構文件)
- [x] 新增 `docs/progress.md` (專案進度表)
- [x] 新增 `docs/dev_rules.md` 與 `.agents/AGENTS.md` (開發守則與約束)
- [x] 新增 `docs/dev_logs/` (開發日誌資料夾與規範)


### 🎨 階段二：HTML 靜態網頁架構與視覺設計
- [x] 建立日戰報 HTML 模板 `templates/report_template.html` (響應式深色模式卡片設計)
- [x] 建立專案首頁 `index.html` (歷程報告索引與今日賽況展示)
- [x] 本地瀏覽器手動測試視覺效果，確認排版與美觀度滿意
 
### 🐍 階段三：Python 數據抓取與 HTML 生成腳本
- [x] 撰寫 `fetch_mlb.py` 核心邏輯 (串接 MLB Stats API 取得賽事 JSON)
- [x] 實作賽事資料解析 (特別處理雙重賽、延賽、延長賽等情境)
- [x] 修改 `fetch_mlb.py` 數據解析：實作不分勝負隊伍的傑出球員評分與動態篩選（至少 3 名，達門檻加列）
- [x] 實作 HTML 模板填寫與檔案輸出 (輸出至 `reports/YYYY-MM-DD.html`)
- [x] 實作 `index.html` 的歷史報告清單自動更新
- [x] 本地手動測試 `python fetch_mlb.py --date 2026-07-18` 驗證生成結果
 
### ⚙️ 階段四：GitHub Actions 自動化排程 [已完成]
- [x] 設定 `workflows/daily_report.yml` 於每日台北時間 12:00 PM 觸發
- [x] 實作「開工前檢查」邏輯 (檢查 API 中昨日比賽是否全數結束)
- [x] 實作「延遲等待」機制 (若未全數結束，休眠 30 分鐘後重試)
- [x] 實作自動 Git Commit & Push 邏輯
- [x] 設定 GitHub Pages 託管並確認能夠線上瀏覽

### 💬 階段五：Discord 通知整合 [已完成]
- [x] 在 `fetch_mlb.py` 中加入 Discord Webhook 串接發送程式
- [x] 設計精美的 Discord Embed 戰報卡片 (包含昨日日期、戰況統計、Pages 連結)
- [x] 完整系統跑通測試

### 🛠️ 階段六：優化與進階長期維護 [已完成]
- [x] 重構 `templates/report_template.html` 卡片版面為「方案 B」（上半部滿版對戰與比分表，下半部雙欄展示投手決定與傑出球員）
- [x] 實作歷史戰報目錄分層（`reports/YYYY/MM/`）與首頁動態 JSON 存檔索引
- [x] 實作昨日無賽事自動跳過（藉由「昨日賽事為零」動態適應休賽季與季中休兵日）
- [x] 在網頁上引入 MLB 官方 `team-cap-on-dark` SVG 隊徽，並在 CSS 加入 `filter: drop-shadow` 外發光描邊效果以解決黑底對比度問題

### 📰 階段七：進階內容擴充與優化 [已完成]
- [x] 於每場比賽左下角（投球決定下方）串接展示 MLB 官方英文新聞摘要 (Headline & Blurb)（含未上傳時以即時比分作為備份的防禦機制）
- [x] 串接 MLB Transactions API，實作當日球員人事異動展示。其中有比賽球隊之異動直接與賽事結果合併在比賽卡片內，其餘球隊異動分組展示於網頁最下方。
- [x] 支援休賽季/無賽事日的當日人事異動生成與底部展示。

### 🛠️ 階段八：功能微調與優化 [已完成]
- [x] 優化 `fetch_mlb.py` 數據過濾：在人事異動中過濾包含 `"minor league"` 關鍵字的小聯盟深度合約，避免版面被無大聯盟出賽機會的簽約資訊干擾
- [x] 進階簽約篩選：對於包含 `"signed"` 的異動，必須同時具備大聯盟特徵詞（如 `"free agent"`、`"extension"` 等）才予以顯示，以徹底過濾大量的小聯盟深度簽約與選秀簽約

