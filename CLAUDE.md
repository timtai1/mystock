# 專案備註:mystock

此檔為本 repo 給 Claude(及其他 AI 助理)的專案層規範。個人偏好(例如語言)請見 `~/.claude/CLAUDE.md`;此檔只放跟本專案相關的事項。

## 專案背景

- 功能:追蹤台股法人目標價 + 日 K 線資料,Flask 本機儀表板。
- 兩條資料管線:
  - `fetch_target_price.py`:CMoney App API → 每日法人目標價快照。會自動掃描所有 `stocklist_*.txt`,跨清單做集合聯集去重後一次抓完。
  - `fetch_daily_kline.py`:TWSE STOCK_DAY(上市) + FinMind TaiwanStockPrice(上櫃)→ 日 K 線。
- 前端:`index.html` 單檔 vanilla JS + KLineChart CDN。左側選單支援「自選股」群組(多份 `stocklist_{群組名}.txt`)+「法人目標價(全部)」兩種 view。
- 後端:`serve.py` Flask,綁 `127.0.0.1:8765`,`before_request` 擋非本機來源。多組自選股 CRUD API 見 `/api/watchlists*`。
- 詳細設計、事故紀錄、欄位規格請讀 `PROJECT.md`。

## 常見任務快速指南

- **改表格欄位 / 過濾邏輯**:動 `index.html` 的 `columns` 陣列與 `getFilteredSorted()`;視情況在 `serve.py` 的 `/api/stocks` 加欄位。
- **改 K 線圖樣式 / 指標**:動 `index.html` 的 `openKlineModal()`;KLineChart 文件為 <https://klinecharts.com/>。
- **改自選股 UI / API**:前端 sidebar 與 dialogs 在 `index.html` 的 `Watchlist sidebar` / `Context menu` / `Name dialog` / `Delete dialog` 幾個段落;後端 CRUD 在 `serve.py` 的 `/api/watchlists*`,檔案落地由 `validate_watchlist_name()` + `watchlist_path()` 把關。
- **改資料管線**:先看 `PROJECT.md` §3。上市走 TWSE OpenAPI;上櫃走 FinMind(TPEX OpenAPI 的 `d=` 參數壞掉,不要再用它取歷史)。
- **改敏感資訊存放方式**:絕不 hardcode。一律讀環境變數(目前有 `CMONEY_AUTH_TOKEN`、`FINMIND_TOKEN`)。
- **Commit 之前**:
  - 檢查 token 字串沒有被塞進任何檔案。
  - `.gitignore` 要繼續排除 `venv/`、`法人目標價_log_file/`、`日K線_log_file/`、`__pycache__/`、`.DS_Store`。

## 程式風格

- Python:標準庫優先;外部套件只有 `requests`、`beautifulsoup4`、`flask`,新增前先問過。
- 新加入的函式請加繁中 docstring。
- JS:維持 vanilla、單檔、沒有 build step。不要引入 React、bundler 等。
- 錯誤訊息面向人類讀者,用中文敘述加上英文關鍵字(檔名、變數名)。

## 工具與流程

- 使用 TodoList 追蹤非 trivial 任務進度。
- 修改超過一個檔案前先給我規劃;我確認後再動手。
- 檔案大幅改動後做語法檢查(`python3 -c "import ast; ast.parse(...)"` 或 Node 的 `new Function()`)再收工。
- 不要主動 commit;除非明確要求「commit」或「push」。

## 需要時主動提醒的事

- CMoney Bearer Token 有時效性,看到 401 / 403 時先猜 token 過期。
- FinMind 匿名額度 300 req/hr,上櫃檔數超過這個量時建議設 `FINMIND_TOKEN`。
- 公司網路有 MITM,`VERIFY_SSL=False` 是刻意的;不要自作主張改回 True。
