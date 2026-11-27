# 開發日誌：2026-11-20 FanGraphs 深度分析區塊

## 📝 修改摘要

每日戰報新增 FanGraphs 深度分析區塊。對應 commit：`0386441`。

> 事後補寫，依 commit 內容整理。

## 🛠️ 詳細改動內容

- 新增 `mlb_daily/analysis.py`（讀取 FanGraphs RSS）；`mlb_daily/config.py` 新增 `ANALYSIS_RSS_URL`（`https://blogs.fangraphs.com/feed/`）、`ANALYSIS_SOURCE`、`ANALYSIS_LIMIT = 6`。
- `mlb_daily/report.py`、`templates/report.html`、`assets/style.css` 加入區塊渲染；`mlb_daily/translate.py` 與 `fetch_mlb.py` 小幅配合。
- `docs/dev_rules.md` 記載規則：取台北時間目標日當天發布者（最多 6 則）、濾掉 podcast；連結只允許 https 的 fangraphs.com 網域，不通過者整則捨棄；附在「今日新聞」下方，無資料或抓取失敗時整個區塊不顯示，不得讓戰報產生失敗；目前不進每日快照、週報與球隊專頁。
- 新增 `tests/fixtures/fangraphs_rss.xml`，`tests/test_fetch_mlb.py` 新增測試；`README.md`、`docs/architecture.md` 同步。

## ✅ 驗證結果

- commit 訊息未記載測試數或實測紀錄；測試結果未留紀錄，無法確認。

## 📋 待辦／評估事項

- 不進每日快照、週報與球隊專頁為 `dev_rules.md` 載明的現況限制。
- 後續 `c3c47e4` 加入 RSS 累積快取與分頁（見 `20261123_RSS累積快取與自動補產.md`）。
