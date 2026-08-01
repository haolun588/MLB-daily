# 開發日誌：2026-08-01 GitHub Pages 部署解耦與焦點球員標籤優化

## 📝 修改摘要

本工作階段完成了兩項核心優化：
1. **單場焦點球員標籤排列優化**：修復全壘打 (HR) 與盜壘 (SB) 標籤分開過遠的問題，新增 Flexbox 容器使標籤靠右對齊並保持緊鄰併排。
2. **GitHub Pages 部署與 `main` 分頁解耦**：將每日生成的戰報 HTML 檔案與 `main` 主分頁解耦，並自動發布至 `gh-pages` 分頁。解決了每次在本地 `git pull` 時會拉下所有歷史 HTML 戰報檔案的問題。

---

## 🛠️ 詳細改動內容

### 一、 焦點球員標籤視覺優化
- **[templates/report_template.html](file:///c:/GitHub/MLB-daily/templates/report_template.html)**：
  - 新增 `.performer-tags` 樣式：
    ```css
    .performer-tags {
      display: flex;
      gap: 0.35rem;
      align-items: center;
    }
    ```
- **[fetch_mlb.py](file:///c:/GitHub/MLB-daily/fetch_mlb.py)**：
  - 更新打者與投手卡片 HTML 生成邏輯，將標籤包覆於 `<div class="performer-tags">` 容器中，實現靠右併排展示。

### 二、 GitHub Pages 發布解耦與本地清理
- **[.gitignore](file:///c:/GitHub/MLB-daily/.gitignore)**：
  - 加入 `reports/*/*/*.html` 與 `reports/*.html`，確保 `.html` 檔案不再推送到 `main` 分頁。
  - 明確保留 `reports/metadata.json` 追蹤。
- **[.github/workflows/daily_report.yml](file:///c:/GitHub/MLB-daily/.github/workflows/daily_report.yml)**：
  - 調整 Actions 工作流程：執行前先從 `gh-pages` 取得歷史戰報 HTML，產生新戰報後僅將 `index.html` 與 `reports/metadata.json` 寫回 `main` 分頁。
  - 使用 `peaceiris/actions-gh-pages@v4` 並設定 `keep_files: true`，將包含完整歷史戰報的網頁發布至 `gh-pages` 分頁。
- **本地檔案清理**：
  - 執行 `git rm --cached` 取消 `main` 分頁對 14 個歷史 `.html` 檔的追蹤。
  - 清理本地 `reports/` 資料夾下的 HTML 檔案，釋放本地硬碟空間並保持倉庫乾淨。

---

## 📦 Git Commit Message

```text
feat: 解耦 GitHub Pages 部署與優化焦點球員標籤排版

- 在 templates/report_template.html 新增 .performer-tags 容器樣式
- 修改 fetch_mlb.py 卡片渲染，將 HR/SB 標籤包覆於 .performer-tags 容器靠右併排
- 在 .gitignore 加入 reports/**/*.html，取消 main 分頁對歷史戰報 HTML 的追蹤
- 更新 daily_report.yml 使用 peaceiris/actions-gh-pages 自動發布至 gh-pages 分頁
- 清理本地與 main 追蹤的戰報 HTML 檔案，僅保留 reports/metadata.json 輕量索引
```
