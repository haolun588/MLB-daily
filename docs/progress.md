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
