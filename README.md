# mystock — 台股法人目標價 + 日 K 線儀表板

<div align="center">

![mystock](https://img.shields.io/badge/mystock-Taiwan%20Stocks-blue)
![Python](https://img.shields.io/badge/Python-3.9+-green)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Platform](https://img.shields.io/badge/Platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)

**本機執行的台股儀表板,整合法人目標價與日 K 線,支援上市(TWSE)與上櫃(TPEX)股票。**

</div>

---

## 專案目的

將兩條獨立的台股資料管線整合進單一本機儀表板:

1. **法人目標價**:從 CMoney App API 每日拉取券商目標價、投資評等與敘述摘要(最近 90 天)。
2. **日 K 線 OHLCV**:上市走 TWSE `STOCK_DAY` OpenAPI;上櫃走 FinMind `TaiwanStockPrice` 資料集。

Web Portal 只讀不寫,綁定 `127.0.0.1`,資料快取到本地之後即使斷網也能瀏覽。

---

## 安全說明

**重要:本 repo 刻意不提交任何憑證。**

本專案支援自動登入 CMoney 以取得 Bearer JWT。憑證儲存於本機 `credential.txt`(已被加入 `.gitignore`),腳本執行時會自動登入並更新 Token。

此 Token 與您的帳號資料掛鉤,請**不要**:

- 把 `credential.txt` 提交到 Git。
- 把 Token 或密碼貼進 shell 歷史紀錄。
- 把憑證分享給其他人。

若不慎外流,請立即修改 CMoney 密碼並更新 `credential.txt`。

Flask 伺服器 (`serve.py`) 會拒絕 `remote_addr` 非 `127.0.0.1` / `::1` 的連線,因此儀表板無法從同網段其他機器讀取。

---

## 功能

- 每日排程拉取法人目標價,扁平存放於 `法人目標價_log_file/{stock_id}_{name}.json`(每次執行覆蓋;CMoney 每次回傳即為近 90 天完整快照)。
- 上市增量 K 線(TWSE)+ 上櫃整段歷史(FinMind)的一鍵 bootstrap。
- **多組自選股**:左側選單支援任意多份自選股清單,每份對應一個 `stocklist_{群組名}.txt` 檔,可在頁面上直接新增 / 改名 / 刪除。
  - 「法人目標價(全部)」固定顯示 `法人目標價_log_file/` 內所有股票。
  - 自選股模式會列出該份清單裡的所有股票;還沒抓到資料的檔也會顯示空白列,並在 header 提示「清單 N 檔,其中 M 檔有法人目標價資料」。
  - `fetch_target_price.py` 會自動掃描所有 `stocklist_*.txt`,跨清單去重後只跑一次,避免重複呼叫 CMoney。
- Web 儀表板提供可搜尋、可排序的個股列表,包含:
  - **市 / 櫃** 標籤(上市 vs 上櫃)
  - 最新收盤價(若有 K 線資料會以 K 線為準)
  - 最新目標價 + 券商 + 投資評等
  - 90 天中位數目標價與潛在報酬
  - 側邊面板展開各券商的敘述摘要
- KLineChart 9.x 驅動的 K 線 modal,含 BOLL、VOL,台股紅漲綠跌配色,支援線段 / 水平線 / 垂直線 / 射線 / 價格線 / Fibonacci / 矩形 / 箭頭 / 文字註記等繪圖工具。
- 每檔獨立的「重抓 K 線」按鈕,可針對單一檔觸發 bootstrap。
- K 線 modal 支援 **左右箭頭快速切上下檔**(鍵盤 `←` / `→` 或畫面上的箭頭按鈕)。
- Flask 綁 `127.0.0.1:8765`,無遠端存取、無帳號資料落地。
- 法人目標價檔案每次執行覆蓋,無需額外清理機制(股票改名時會自動刪掉舊檔名)。

---

## 系統需求

- Python 3.9 以上
- pip(Python 套件管理)
- 對外連線權限,可達:
  - `dtno.cmoney.tw`(法人目標價)
  - `www.twse.com.tw`(TWSE 上市 K 線)
  - `api.finmindtrade.com`(TPEX 上櫃 K 線)
  - `isin.twse.com.tw`(每週刷新 `tw_stock_list.csv`)
- 有效的 CMoney Bearer JWT(見下文「設定」)

---

## 快速上手

### 1. Clone 並安裝依賴

```bash
git clone https://github.com/timtai1/mystock.git
cd mystock
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows(PowerShell):

```powershell
git clone https://github.com/timtai1/mystock.git
cd mystock
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 設定 CMoney 登入資訊 (推薦)

在專案根目錄建立 `credential.txt`,內容如下:

```text
account=+886912345678
hashed_password=你的密碼MD5雜湊值
```

- **account**: 您的手機號碼(含國碼,如 `+886...`)。
- **hashed_password**: 密碼的 MD5 雜湊值(可從 App 封包擷取)。

若不使用自動登入,則需手動設定環境變數作為備援:

```bash
export CMONEY_AUTH_TOKEN="eyJhbGciOi..."      # 貼上你真實的 token
```

### 3. 每日執行

```bash
python fetch_target_price.py                       # 自動掃描所有 stocklist_*.txt(跨清單去重)
python fetch_daily_kline.py                        # 日 K 線增量
```

### 4. 首次回補(或清掉資料重抓)

```bash
python fetch_daily_kline.py --bootstrap --months 13
```

### 5. 啟動 Web Portal

```bash
python serve.py
```

瀏覽器會自動開啟 `http://127.0.0.1:8765/`。

---

## 設定

所有設定分兩層:敏感資訊走憑證檔或環境變數,其餘走各腳本頂端的常數。

### 憑證與環境變數

| 名稱                 | 必填 | 用途                                                                                           |
| -------------------- | ---- | ---------------------------------------------------------------------------------------------- |
| `credential.txt`     | 推薦 | 存放 `account` 與 `hashed_password` 以進行自動登入。                                            |
| `CMONEY_AUTH_TOKEN`  | 備援 | CMoney `dtno/MobileCsv` 端點的 Bearer JWT。若無 `credential.txt` 則以此環境變數為準。            |
| `FINMIND_TOKEN`      | 否   | FinMind API token,用於上櫃歷史。匿名 300 req/hr 已足夠 ~40 檔 bootstrap;註冊後升到 600 req/hr。 |

### 程式內可調常數

| 檔案                     | 常數名              | 意義                                                         |
| ------------------------ | ------------------- | ------------------------------------------------------------ |
| `fetch_target_price.py`  | `VERIFY_SSL`        | 公司網路有 MITM 根憑證時設 `False`。                         |
| `fetch_target_price.py`  | `INTERVAL_MS`       | 對 CMoney 每次請求的間隔(毫秒,預設 1000)。                |
| `fetch_daily_kline.py`   | `BOOTSTRAP_MONTHS`  | Bootstrap 預設抓取歷史的月數。                               |
| `fetch_daily_kline.py`   | `TWSE_INTERVAL_SEC` | TWSE 每月請求之間的間隔(秒,預設 1)。                      |
| `serve.py`               | `PORT`              | Flask 綁定的本機埠(預設 8765)。                            |
| `serve.py`               | `RECENT_DAYS`       | 中位數 / 最新目標價的時間窗(預設 90 天)。                  |

### 股票清單(自選股)

每一份自選股對應一個 `stocklist_{群組名}.txt` 檔,一行一個股票代號,`#` 開頭視為註解。

- 預設內建 `stocklist_自選股1.txt`,首次使用即套用。
- 在 Web 儀表板左側選單可直接新增 / 改名 / 刪除清單(改名會連檔名一起改,刪除會刪掉對應檔案),對應的 API 見下表。
- `fetch_target_price.py` 每次執行會掃描所有 `stocklist_*.txt`,做**跨清單的聯集去重**後一次抓完,避免重複呼叫 CMoney。

`tw_stock_list.csv` 是從 TWSE 自動抓回來的上市 + 上櫃對照表(code → name → market),由 `fetch_target_price.py` 每週自動刷新。

### 自選股 API(serve.py)

| Method | Path                         | 說明                                                              |
| ------ | ---------------------------- | ----------------------------------------------------------------- |
| GET    | `/api/watchlists`            | 列出所有自選股清單與各自股數                                      |
| POST   | `/api/watchlists`            | 新增清單;body `{"name": "..."}`,省略 name 會自動編號「自選股N」 |
| PATCH  | `/api/watchlists/<name>`     | 改名;body `{"new_name": "..."}`,同時改檔名                      |
| DELETE | `/api/watchlists/<name>`     | 刪除清單(刪掉對應的 `stocklist_{name}.txt`)                     |
| GET    | `/api/stocks?watchlist=<name>` | 只列出該清單裡的股票;清單內缺資料的檔也會回空白列               |

---

## 每日流程

完成首次 bootstrap 之後,平日通常是這樣:

```bash
source venv/bin/activate
python fetch_target_price.py                       # 1 ~ 2 分鐘,跨所有 stocklist_*.txt
python fetch_daily_kline.py                        # 約 30 秒
python serve.py                                    # 開瀏覽器看結果
```

也可以用 `cron` 或 macOS `launchd` 排程,完全自動化。

---

## 專案結構

```
mystock/
├── fetch_target_price.py        # CMoney 法人目標價管線(每日)
├── fetch_daily_kline.py         # TWSE / FinMind 日 K 線管線(每日 + bootstrap)
├── serve.py                     # 本機 Flask Web Service(127.0.0.1:8765)
├── index.html                   # 單檔 vanilla-JS 儀表板 UI
├── stocklist_自選股1.txt         # 預設自選股清單;要幾份就放幾份 stocklist_*.txt
├── tw_stock_list.csv            # TWSE/TPEX 完整對照表
├── requirements.txt             # Python 依賴
├── readme.txt                   # 中文快速指令備忘
├── PROJECT.md                   # 內部設計筆記與事故紀錄
├── CLAUDE.md                    # 給 AI 助理的永久規範
└── LICENSE                      # MIT 授權
```

資料目錄在首次執行時自動建立,已加入 `.gitignore`:

```
法人目標價_log_file/             # 法人目標價快照(每次執行覆蓋,每檔一檔)
  └── 2330_台積電.json
日K線_log_file/                   # 每檔股票的 OHLCV 歷史
  └── 2330.json
```

---

## 資料來源

| 來源                                              | 用途                         | 備註                                                                                             |
| ------------------------------------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------ |
| CMoney `dtno/MobileCsv`(App API)                | 法人目標價                   | 需要會過期的 Bearer JWT,從 iOS App 封包擷取。                                                  |
| TWSE OpenAPI `STOCK_DAY`                          | 上市日 K 線(歷史 + 當日)   | Bootstrap 每檔每月一次請求;增量每檔每日一次請求。                                              |
| TPEX OpenAPI `tpex_mainboard_daily_close_quotes`  | 上櫃當日快照                 | `d=` 參數會被官方忽略,因此**不可**用此端點抓歷史資料。                                          |
| FinMind `TaiwanStockPrice`                        | 上櫃歷史 K 線                | 一次 API 呼叫就能取得某檔整段歷史。匿名 300 req/hr;註冊後 600 req/hr。                         |
| TWSE ISIN CSV(`strMode=2` / `strMode=4`)         | code → name → market 對照表 | 每週刷新到 `tw_stock_list.csv`。                                                                |

---

## 疑難排解

### `[錯誤] 無法取得登入憑證且環境變數 CMONEY_AUTH_TOKEN 未設定`

代表您既沒有設定 `credential.txt`,也沒有 `export CMONEY_AUTH_TOKEN`。請參考「快速上手」進行設定。

### 所有法人目標價請求都回 HTTP 401 / 403

代表 Token 已過期且自動登入失敗。請檢查 `credential.txt` 內的帳號密碼是否正確,或手動重新從 App 擷取 Token。

### 公司網路 SSL 驗證錯誤

公司 Proxy 會用內部 Root CA 換掉 TLS 憑證。打開 `fetch_target_price.py` 頂端把 `VERIFY_SSL = False`(或指向公司 CA bundle 的 `.pem` 路徑)。`fetch_daily_kline.py` 也有一樣的旗標。

### 上櫃股票的 K 線沒有歷史

若 bootstrap JSON 看起來壞掉(全部日期欄位相同、或 NaN),代表是舊版 TPEX OpenAPI 的產物。刪掉後重抓:

```bash
python fetch_daily_kline.py --bootstrap --stock 8299 --months 13
```

### Port 8765 被占用

改 `serve.py` 頂端的 `PORT` 常數,或關掉占用該埠的程式。

---

## 開發筆記

詳細的設計決策、資料欄位規格、歷史事故整理都在 `PROJECT.md`,包含:

- 為什麼上櫃歷史資料從 TPEX OpenAPI 搬到 FinMind(`d=` 參數 bug)。
- 法人目標價的 schema 與各欄位索引。
- 過往事故(上櫃資料大地震、K 線壞資料、TPEX 改版)與處理方式。

UI 刻意保持成一個 `index.html` + vanilla JS,沒有 build step。後端狀態全走 JSON 檔案,不用資料庫。

---

## 貢獻

本專案原則上為個人工具,但歡迎 PR:

1. Fork 本 repo。
2. 建立 feature branch(`git checkout -b feature/your-feature`)。
3. Commit 你的改動(`git commit -m 'Add your feature'`)。
4. Push(`git push origin feature/your-feature`)。
5. 開 Pull Request。

---

## 授權

本專案採用 MIT License,詳見 `LICENSE`。

---

## 免責聲明

本工具僅供個人研究與學習使用。它透過第三方 API(CMoney、TWSE、TPEX、FinMind)取得資料,這些 API 的服務條款隨時可能異動。使用者自行負責:

- 遵守各資料來源的使用條款。
- 把市場資料當參考而非投資建議。
- 妥善保管憑證(尤其 Bearer Token)。

作者對基於本工具所呈現資料所做的投資決策,或因違反第三方服務條款導致 API 權限被撤銷,不負任何責任。

---

<div align="center">

[回報 Bug](https://github.com/timtai1/mystock/issues) · [功能許願](https://github.com/timtai1/mystock/issues)

</div>
