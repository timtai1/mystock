# 法人目標價分析 - 專案重點筆記

> 本檔為整個專案的知識庫，包含架構、資料流、關鍵設計決策，以及開發過程中踩到的坑與解法。
> 新成員或未來自己回來接手時，請先從這份開始看。

---

## 1. 專案目標

抓取台股的法人（券商）目標價報告，結合每日 K 線資料，提供一個**本地端**的輕量網頁介面，快速瀏覽：

- 每檔股票最近 90 天內的券商目標價、評等、敘述摘要。
- 中位數目標價、最高目標價、最新目標價與其潛在報酬率。
- 真正的「最新收盤價」（來自每日 K 線，不是券商報告當日的收盤）。
- 最近半年的日 K 線資料（為未來畫 K 線圖、MACD、布林通道預留）。

整個系統完全跑在 `127.0.0.1`，不對外開放。

---

## 2. 檔案結構

```
cmoney/
├── fetch_target_price.py      # ① 法人目標價每日擷取（CMoney API）
├── fetch_daily_kline.py       # ② 日 K 線擷取（TWSE + TPEX OpenAPI）
├── serve.py                   # ③ 本地 Flask Web Service
├── index.html                 # ④ 前端單檔（原生 JS）
├── stocklist.txt              # 追蹤清單（股票代號，一行一檔，約 133 檔）
├── tw_stock_list.csv          # 台股上市/上櫃對照（code,name,market）
├── readme.txt                 # 操作指引
├── PROJECT.md                 # ← 本檔
├── venv/                      # Python 虛擬環境
├── 法人目標價_log_file/
│   └── {yyyyMMdd}/
│       └── {seq}_{code}_{name}.json   # 每日每檔 CMoney 回應
└── 日K線_log_file/
    └── {code}.json                    # 每檔累積的 OHLCV
```

---

## 3. 兩條資料管線

### 管線 A：法人目標價（`fetch_target_price.py`）

- 來源：CMoney App API `https://dtno.cmoney.tw/app/v2/dtno/MobileCsv`，`DTNO=8459549`。
- 認證：`CMONEY_AUTH_TOKEN` 環境變數（Bearer JWT）。**會過期**，過期時需要重新抓 CMoney App 封包後 `export CMONEY_AUTH_TOKEN="new_jwt"`。
- 執行：
  - 全量：`python fetch_target_price.py stocklist.txt`（每日排程）
  - 單檔重抓：`python fetch_target_price.py --stock 2330`（單檔模式會自動跳過舊資料夾清理）
- 輸出：`法人目標價_log_file/{yyyyMMdd}/{seq}_{code}_{name}.json`，
  其中 `titles` + `data` 是 CMoney 原生格式（日期、券商名稱、評等、目標價、收盤價、敘述摘要…等欄位）。
- 保留策略：`RETENTION_DAYS=90`，超過天數的日資料夾會被自動清掉。
- 備援對照：`tw_stock_list.csv` 補 `code → name`，找不到就退回 `unknown`。
- 公司 MITM：`VERIFY_SSL=False`（Trend Micro 內網自簽憑證會壞 SSL 驗證）。

### 管線 B：日 K 線（`fetch_daily_kline.py`，近期新加）

**為什麼要做這條管線？**
一開始主畫面的「收盤價」是從券商報告的 JSON 裡面讀出來的 `收盤價` 欄位 —— 那是**報告當日**的收盤，不是今天的最新收盤。所以當券商很久沒出報告，那個價格會嚴重過時，算出來的「潛在報酬」也不對。這條管線就是為了拿到每檔的真正最新收盤，順便把近 6 個月 OHLCV 存下來以後畫 K 線用。

**為什麼要分 TWSE / TPEX 兩條路？**

| 市場 | Bootstrap API | Incremental API | 策略 |
|------|---------------|-----------------|------|
| 上市 TWSE | `STOCK_DAY`（單股單月 OHLCV） | `STOCK_DAY_ALL`（單日全市場） | 按「每檔 × 每月」呼叫 |
| 上櫃 TPEX | **FinMind `TaiwanStockPrice`**（單股整段日期範圍） | `tpex_mainboard_daily_close_quotes`（當日全市場） | 按「每檔」一次 API 取整段 |

TPEX 的這個架構在 2026-04 改過一次（見 §6.10）：原本 bootstrap 用 TPEX OpenAPI 的「單日全市場」，結果發現 `d=` 參數被官方完全忽略，不管傳哪一天都只回今日快照，導致歷史資料整批壞掉。現在改用 **FinMind** 的 `TaiwanStockPrice` dataset，一次 API 呼叫就能拿到單檔整段日期範圍的逐日 OHLCV。

**FinMind 重點**

- Endpoint: `https://api.finmindtrade.com/api/v4/data`
- Params: `dataset=TaiwanStockPrice`, `data_id=XXXX`, `start_date=YYYY-MM-DD`, `end_date=YYYY-MM-DD`
- Auth: `Authorization: Bearer <token>`（可選）；匿名 300 req/hr、註冊後 600 req/hr。
- 環境變數：`export FINMIND_TOKEN="..."` 會自動帶 header。
- 回傳欄位映射：`date → date`、`open → open`、`max → high`、`min → low`、`close → close`、`Trading_Volume → volume`。

**關鍵常數**

```python
BOOTSTRAP_MONTHS = 6
REQUEST_INTERVAL_SEC = 1.8     # TWSE 官方限制 3 req / 5s，留安全邊界
FINMIND_INTERVAL_SEC = 0.3     # 匿名限 300/hr，保守留 0.3 秒
FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
VERIFY_SSL = False             # 公司 MITM
HTTP_TIMEOUT = 30
```

**執行**

```bash
python fetch_daily_kline.py --bootstrap             # 首次回補近 6 個月
python fetch_daily_kline.py --bootstrap --months 12 # 回補 12 個月
python fetch_daily_kline.py                         # 每日增量
python fetch_daily_kline.py --stock 2330            # 測單檔
```

**輸出格式**

```json
{
  "stock_id": "2330",
  "market": "上市",
  "last_updated": "20260418123045",
  "entries": [
    {"date": "20260101", "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}
  ]
}
```
`entries` 已依日期升冪排序並去重，`serve.py` 直接取最後一筆就是最新收盤。

---

## 4. Web Service：`serve.py`

- Flask，綁 `127.0.0.1:8765`，啟動 1 秒後自動開瀏覽器。
- `before_request` 檢查 `remote_addr ∈ {127.0.0.1, ::1}`，其餘 403。
- 三個主要 API：
  - `GET /` → 回 `index.html`。
  - `GET /api/stocks?date=YYYYMMDD` → 回該日資料夾的聚合結果；沒帶 date 就取最新。
  - `GET /api/dates` → 列出所有可選日期。
  - `GET /api/kline?code=XXXX` → 單檔 K 線資料（`code.isalnum()` 防 path traversal）。
- `POST /api/refetch?code=XXXX&scope=...` → 重抓單檔。`scope` 可以是：
  - `target_price`（預設）：只跑 `fetch_target_price.py --stock XXXX`
  - `kline`：先用 `_compute_existing_kline_months(code)` 掃現有 kline JSON 最早一筆的日期，算出從那天到今天的月數，再跑 `fetch_daily_kline.py --bootstrap --months M --stock XXXX`。意思是「本來有多久的資料、就重抓那麼久」。
  - 回傳 `{ok, scope, months?, details:{target_price | kline}}`。`_refetch_lock` 單一鎖避免並發重抓（回 HTTP 429）。子程序 timeout 預設 180 秒。
- `parse_stock_file` 會算：近 90 天內的最高目標價、最新目標價、中位數目標價、潛在報酬。
- **重點：主畫面的 `close` 會被 K 線資料覆寫。** 流程：
  1. 先從券商報告算出 `parsed["close"]`、`median_target`。
  2. 查 `load_latest_closes()`（掃 `日K線_log_file/*.json`）。
  3. 如果有對應的 K 線資料，覆寫 `close` 為 K 線最後一筆的收盤，記錄 `close_date`，並**重新計算** `potential_return`。
  4. 同時標記 `close_source = "kline"` 或 `"broker_report"`。

---

## 5. 前端：`index.html`（原生 JS 單檔）

### 版面

- `.container`：`max-width: none; padding: 16px 50px;`（滿寬 + 50px 左右留白）。
- 表格：主畫面列出所有股票、各欄可排序、支援關鍵字搜尋。
- 側邊欄 `<aside class="panel">`：點主畫面一列後，側拉出詳細資料。
- 色彩規範：**紅漲綠跌**（台股慣例，`--pos: #d1242f`、`--neg: #1a7f37`）。

### 表格欄位（主畫面）

依序：股票代號、股票名稱、最新收盤價、中位數目標價、潛在報酬、最高目標價（日期、券商）、**最新目標價（日期、券商）**、最高目標價敘述、近 90 天筆數。

### 「已達標」邏輯（側邊欄裡每一筆券商紀錄）

- 當 `close > entry.target`（目前收盤已超過該筆券商預估），把**潛在報酬那個百分比**本身換成橘色 `已達標` 文字（不是外加標籤）。
- CSS：`.reached { color: #bc4c00; font-weight: 600; }`。

### 「收盤價」欄位的 tooltip

主畫面 `close` cell 加 `title="收盤日期：YYYY/MM/DD"`（來自 K 線的 `close_date`），避免誤以為是今天。

---

## 6. 對話過程中的關鍵決策與踩坑紀錄

依時間排序的重要里程碑：

1. **版面改滿寬 + 新增 3 欄**
   `.container` 改 `max-width: none`；在「最高目標價券商」後面加 `latest_target`、`latest_target_date`、`latest_target_broker`。

2. **「已達標」位置搞錯過一次**
   一開始做成券商名旁邊的 pill 標籤，後來使用者澄清：是要把**潛在報酬的百分比數字本身**替換成橘色「已達標」。
   最終用一個簡單的 `<span class="reached">已達標</span>` 替換百分比字串。

3. **發現收盤價是錯的**
   主畫面的 `close` 原本來自 `entries[0]["close"]` —— 券商報告 JSON 裡的「當日收盤」，實際上是**報告日**而非**今日**的收盤。
   → 催生了整條 K 線管線 + `serve.py` 的覆寫邏輯。

4. **TWSE API 選型**
   - `STOCK_DAY_ALL`：全市場當日 OHLCV，單次呼叫搞定上市每日增量。
   - `STOCK_DAY`：單股單月，用於 bootstrap。
   - 3 req / 5s 限制 → 請求間隔設 1.8 秒。

5. **TPEX 站改版大地震（2024/10）**
   - 舊介面 `st43_result.php` 沒了，`wwwov.tpex.org.tw` 2025/5/31 退役。
   - 一開始用舊 URL 卡在「404 + JSON 解析失敗」，一度以為是 rate limit，實際上是端點整個下線。
   - 最終解法：改用 `https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes`，日期格式 `d=YYY/MM/DD`（**民國年！**年份要減 1911）。
   - 策略翻過來：以「交易日」為外層迴圈，一次取全市場，再在 Python 端依 `stocklist.txt` 分配進各檔的 JSON。

6. **民國 ↔ 西元**
   TPEX 的 date param 是民國年格式（例如 `115/04/18`），轉換用 `dt.year - 1911`；回來 parse 時是 `+ 1911`。

7. **SSL 驗證**
   Trend Micro 內網有 MITM，所有 `requests.get` 都帶 `verify=False`，並 `warnings.simplefilter("ignore", InsecureRequestWarning)` 壓警告。

8. **收盤價要標日期**（最新需求）
   側邊欄的「收盤價」卡片下方要加 `card-sub` 顯示 `close_date`，讓人知道是哪一天的價。

9. **K 線浮窗**（TradingView 風 + KLineChart 9.8.10 + BOLL + VOL + 畫線工具）已完成（見 §8.已完成）。

10. **TPEX 歷史資料大地震（2026-04，再次）**
    - **症狀**：8299（群聯）從網頁「重抓 K 線」超時。檢視現有 JSON，發現每檔上櫃的 269 筆 entry OHLCV 完全相同，只有 date 欄位不同。36 檔上櫃全部中招。
    - **根因**：`https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes?d=YYY/MM/DD` 這個端點**實際上完全忽略 `d=` 參數**，不管傳哪一天一律只回今日快照。原本的 `bootstrap_tpex_batch(tpex_codes, months)` 以交易日為外層迴圈，每次呼叫都拿到同一筆今日資料，於是所有歷史 entry 都被覆蓋成同一份。
    - **排查過程**：
      - 跑腳本 dump 8299 所有 entries → 確認 OHLCV 全部一樣（1600/1640/1565/1570, vol=7629221）。
      - 查 TPEX OpenAPI 清單、讀 TWSEMCPServer 等第三方專案 → 確認 TPEX OpenAPI 只有當日快照類端點，沒有等價於 TWSE `STOCK_DAY` 的「單股單月」歷史 API。
      - 舊版 `st43.php` 301 redirect 去 SPA，SPA 底層 XHR 是未公開內部 API。
    - **決策**：採用 **FinMind** `TaiwanStockPrice` dataset。原因：（1）REST JSON 乾淨、有官方文件；（2）同一個 API 可同時支援上市上櫃，之後要統一也方便；（3）不用額外寫 scraper；（4）有免費額度（匿名 300 req/hr；我們只需 36 次）。
    - **實作**：新增 `fetch_finmind_stock_history(stock_id, start_date, end_date)`；新增 `bootstrap_tpex_stock(code, months)` 一檔一呼叫；改寫 `bootstrap_tpex_batch` 為逐檔呼叫 FinMind（每檔間距 0.3 秒）。TWSE 路徑保留不動。
    - **清理**：原有 36 檔上櫃 JSON（全部是壞資料）**已搬到** `日K線_log_file/_backup_corrupted_tpex_20260418/`，等 FinMind bootstrap 成功後可以刪除。
    - **API 端點保留**：`fetch_tpex_all_by_date(dt=None)` 保留，因為「當日快照」仍正確，`fetch_tpex_all_today()` 用於每日 incremental。只有「歷史日期」這條路不再使用。
    - **後續動作（需使用者在 Mac 上執行）**：
      ```bash
      source venv/bin/activate
      python3 fetch_daily_kline.py --bootstrap --months 13
      # 跑完驗證：8299 的 entries OHLC 不再全部相同
      python3 -c "import json; e=json.load(open('日K線_log_file/8299.json'))['entries']; print(len(e), len(set((x['open'],x['high'],x['low'],x['close']) for x in e)))"
      ```
   - 互動改成：點「股票代號」→ 彈出置中浮窗 K 線圖、主畫面變暗遮罩。
   - 關閉：右上 `✕` 按鈕 或 `ESC`。
   - 「股票名稱」維持原本行為（點了開側邊欄詳細資料）。
   - 元件選型：**KLineChart 9.x**（比 lightweight-charts 更合適，因為內建 BOLL 指標 + 畫線工具）。
     CDN：`https://cdn.jsdelivr.net/npm/klinecharts@9.8.10/dist/umd/klinecharts.min.js`。
   - 必備功能：K 棒 + 成交量 + 布林通道、畫線工具列（線段／水平線／垂直線／射線／斐波那契／矩形／文字…）、TradingView 風格 UI。
   - 色彩仍是紅漲綠跌（`upColor: #d1242f`、`downColor: #1a7f37`）。
   - 資料源：`/api/kline?code=XXXX`。

---

## 7. 操作 Runbook

### 首次安裝

```bash
cd /path/to/cmoney
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install requests beautifulsoup4 flask
```

### FinMind Token（可選）

```bash
# 匿名（300 req/hr）足夠我們 36 檔上櫃 bootstrap，但建議註冊取得 token
export FINMIND_TOKEN="你的_token"
# 或寫進 ~/.zshrc 讓 launchd / cron 也能看到
```

### 每日排程

```bash
source venv/bin/activate
python fetch_target_price.py stocklist.txt
python fetch_daily_kline.py
```

### 首次 bootstrap K 線

```bash
python fetch_daily_kline.py --bootstrap             # 近 6 個月
python fetch_daily_kline.py --bootstrap --months 12 # 近 12 個月
```

### 啟動 Web Portal

```bash
source venv/bin/activate
python3 serve.py
# 自動打開 http://127.0.0.1:8765/
```

---

## 8. TODO / 待做

### 已完成（本次）

- [x] **Web UI 雙重抓按鈕（已拆分）**：
  - [x] `fetch_target_price.py` 加 `--stock` 參數（argparse，保留 positional 回溯相容）。單檔模式會跳過舊資料夾清理。
  - [x] `serve.py` 的 `POST /api/refetch` 支援 `scope=target_price` 或 `scope=kline`：
    - `target_price`：只跑 `fetch_target_price.py --stock XXX`
    - `kline`：`_compute_existing_kline_months(code)` 掃現有 JSON 推算月數 → `fetch_daily_kline.py --bootstrap --months M --stock XXX`
  - [x] `threading.Lock` 防止同時間多重觸發（回 HTTP 429）。
  - [x] **側邊欄**加「重抓法人目標價」按鈕（scope=target_price）。完成後 `load({preservePanel: true})` 保留面板重繪。
  - [x] **K 線 modal header** 加「重抓 K 線」按鈕（scope=kline）。完成後重新 init chart 並重載資料；主表也同步 load。
  - [x] Toast 提示（成功綠色、失敗紅色、處理中灰色），錯誤時顯示子程序 stderr 尾端。
- [x] `index.html` 側邊欄「收盤價」卡加 `card-sub` 顯示日期（K 線來源顯示「YYYY-MM-DD」，券商報告則顯示「來自券商報告」）。
- [x] K 線浮窗 modal：
  - [x] 在 `<head>` 引入 KLineChart CDN（`klinecharts@9.8.10`）。
  - [x] Modal HTML（backdrop + modal + header(title + ✕) + toolbar + chart container）。
  - [x] Modal CSS（`position: fixed`、`z-index: 50`、backdrop 半透明黑、modal `92vw × 86vh`、`max-width: 1400px`）。
  - [x] 改列點擊：拆成 `kline-link`（股票代號）和 `link`（股票名稱），用 event delegation。
  - [x] `openKlineModal(code)`：fetch `/api/kline?code=...`，`klinecharts.init()` + `createIndicator('BOLL', false, {id:'candle_pane'})` + `createIndicator('VOL')` + `applyNewData()`。
  - [x] `closeKlineModal()`：`dispose()` + 移除 `.open` class。
  - [x] 綁定 ✕、backdrop click、ESC、畫線工具列按鈕（線段／直線／水平／垂直／射線／價格線／斐波那契／矩形／平行四邊形／箭頭／文字註記）、清除按鈕。
  - [x] 視窗 resize 時 `chart.resize()`。

### 未來

- [ ] `CMONEY_AUTH_TOKEN` 過期時的替換流程（未來可以考慮自動化取得）。
- [ ] K 線資料的歷史保留策略（現在只 append，沒裁切，長期會膨脹）。
- [ ] K 線 modal 加更多指標切換（MACD、KD、RSI）。
- [ ] 點「股票名稱」維持開側邊欄的設計，之後可考慮在側邊欄再放一個「看 K 線」按鈕，讓互動更直覺。

---

## 9. 安全性要點（務必保留）

- Flask 只綁 `127.0.0.1`。
- `before_request` 擋非本機來源。
- `/api/kline` 的 `code` 參數限 `code.isalnum() and len(code) <= 10`。
- `/api/stocks` 的 `date` 參數限 8 位數字 + `strptime` 驗證，擋掉 path traversal。
- `CMONEY_AUTH_TOKEN` 是敏感資訊，請**只放在環境變數**（例如 `~/.zshrc`），絕對不要寫進程式碼或提交到 git。

---

*最後更新：2026-04-18（TPEX 歷史資料改走 FinMind；壞資料已備份至 `_backup_corrupted_tpex_20260418/`，待使用者在 Mac 上重新 bootstrap）*
