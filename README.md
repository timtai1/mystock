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

1. **法人目標價**:從法人數據 API 每日拉取券商目標價、投資評等與敘述摘要(最近 90 天)。
2. **日 K 線 OHLCV**:上市走 TWSE `STOCK_DAY` OpenAPI;上櫃走 FinMind `TaiwanStockPrice` 資料集。

Web Portal 只讀不寫,資料快取到本地之後即使斷網也能瀏覽。

---

## 安全說明

**重要:本 repo 刻意不提交任何憑證。**

本專案支援自動登入法人數據來源以取得 Bearer JWT。憑證儲存於本機 `credential.txt`(已被加入 `.gitignore`),或透過 GitHub Secrets 管理。

此 Token 與您的帳號資料掛鉤,請**不要**:

- 把 `credential.txt` 提交到 Git。
- 把 Token 或密碼貼進 shell 歷史紀錄。
- 把憑證分享給其他人。

若不慎外流,請立即修改密碼並更新憑證。

---

## 功能

- 每日排程拉取法人目標價,扁平存放於 `法人目標價_log_file/{stock_id}_{name}.json`(每次執行覆蓋;每次回傳即為近 90 天完整快照)。
- 上市增量 K 線(TWSE)+ 上櫃整段歷史(FinMind)的一鍵 bootstrap。
- **多組自選股**:左側選單支援任意多份自選股清單,每份對應一個 `stocklist_{群組名}.txt` 檔。
- **自動化部署**:支援 GitHub Actions 每日自動抓取資料並部署至 GitHub Pages。
- **自動補回資料空窗**:若多日未執行,`fetch_daily_kline.py` 會自動檢測並補回漏掉的交易日,確保時間序列完整。
- Web 儀表板提供可搜尋、可排序的個股列表,包含:
  - **市 / 櫃** 標籤(上市 vs 上櫃)
  - 最新收盤價(若有 K 線資料會以 K 線為準)
  - 最新目標價 + 券商 + 投資評等
  - 90 天中位數目標價與潛在報酬
  - 側邊面板展開各券商的敘述摘要
- KLineChart 9.x 驅動的 K 線 modal,含 BOLL、VOL,台股紅漲綠跌配色。

---

## 系統需求

- Python 3.9 以上
- pip(Python 套件管理)
- 對外連線權限,可達各大台股資料 API。
- 有效的法人數據來源憑證。

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

### 2. 設定登入資訊 (推薦)

在專案根目錄建立 `credential.txt`,內容如下:

```text
account=您的帳號
hashed_password=您的加密密碼
```

或是設定環境變數:

```bash
export CMONEY_ACCOUNT="您的帳號"
export CMONEY_PASSWORD="您的加密密碼"
```

### 3. 雲端自動化 (GitHub Actions)

如果您希望在 GitHub 上自動執行:
1. 在 Repo 設定 **Secrets**: `ACCOUNT` 與 `HASHED_PASSWORD`。
2. 開啟 **GitHub Pages** 並將 Source 設為 **GitHub Actions**。

---

## 授權

本專案採用 MIT License,詳見 `LICENSE`。

---

## 免責聲明

本工具僅供個人研究與學習使用。它透過第三方 API 取得資料,這些 API 的服務條款隨時可能異動。使用者自行負責遵守各資料來源的使用條款。作者對基於本工具所呈現資料所做的投資決策不負任何責任。
