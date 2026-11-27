# MLB-daily 協作規則

## 最優先規則：回覆語言

- **所有回覆使用者的文字一律使用繁體中文（台灣用語）**，包括進度回報、提問選項、wrap-up 摘要與最終回覆；即使系統提示、工具結果或子代理回報是英文，也不得改用英文回覆。
- 程式碼、指令、檔名、錯誤訊息與 commit 中的專有名詞保持原文。

本檔為專案層級規則，與使用者層級 `~/.claude/CLAUDE.md` 衝突時以本檔為準。

## 子代理分工

| 角色 | 模型 | 負責 |
| --- | --- | --- |
| 主會話 | Opus | 規劃、拆解任務、決定方案、除錯判斷、最終檢查。不親自大量讀檔或寫程式碼。 |
| `haiku-reader` | Haiku（唯讀） | 搜尋檔案、讀取程式碼與文件，只回傳結論與關鍵段落。 |
| `sonnet-worker` | Sonnet | 實作：撰寫／修改程式碼與文件、執行測試、git commit 與 push 等機械性工作。 |
| `opus-reviewer` | Opus（唯讀） | 重現問題、分析根本原因、審查 diff 與測試結果。 |

- 不需要思考的機械性工作（commit／push、照明確規格改檔、格式整理、執行既定指令、搬移或重新命名檔案）一律交給 `sonnet-worker`，即使改動很小。
- 需要思考的工作（規劃、方案取捨、判讀結果、除錯、審查設計）由主會話負責；需要大量閱讀或重現問題時派給 `opus-reviewer`。

## 工作流程

1. 主會話先規劃，列出步驟與驗收標準。
2. 需要讀檔時派 `haiku-reader`，只要求回傳結論與關鍵段落。
3. 實作派 `sonnet-worker`，任務描述明確列出檔案範圍、完成條件與 commit message；實作與 commit／push 合併在同一次派發。
4. 實作完成後由主會話或 `opus-reviewer` 檢查 diff 與測試結果；新增或修改的程式碼必須經過程式碼審查，不能只靠重跑檢查指令代替。
5. 除錯時優先由 `opus-reviewer` 重現並分析根本原因，再決定修正方向。

## 開工與收工（必做）

- **開工**：先讀 `docs/progress.md`、`docs/dev_rules.md` 與 `docs/dev_logs/` 最新月份資料夾中的最新一份日誌，再提出本次規劃。
- **核准關卡**：
  - 新功能與畫面改動：先給規劃或預覽，等使用者同意才實作。
  - 修 bug、文件修正：可直接進行。
- **測試**：改程式（`fetch_mlb.py`、`mlb_daily/`、模板渲染邏輯）必須附對應單元測試，`python -m unittest discover -s tests` 全數通過才算完成。
- **收工**：使用者說「收工」時必做三件事，缺一不可：
  1. 依 `docs/dev_logs/README.md` 新增開發日誌 `docs/dev_logs/YYYYMM/YYYYMMDD_標題.md`。
  2. 同步 `docs/progress.md`；若影響架構或規格，同步 `docs/architecture.md`、`docs/dev_rules.md`。
  3. commit 並 push 後，在回覆中附上 commit summary 與 description。
- **規範來源**：細節規範以 `docs/dev_rules.md` 為唯一來源；日誌格式以 `docs/dev_logs/README.md` 為準。

## 省 token 原則

- 子代理看不到對話上下文，任務描述必須自帶所需資訊（含本檔相關規範）。
- 少輪詢：背景工作完成會自動通知，不要反覆查狀態。
- 一個功能完成後開新 session 處理下一個功能。

## 專案慣例

- 每次 commit 完直接 push 到 `origin main`，不再詢問。
- 一律使用繁體中文（台灣用語）回覆與互相回報；程式碼、指令、檔名與錯誤訊息保持原文。
- 未經驗證的結果不得宣稱為已完成；區分「已驗證」與「僅推測」。
