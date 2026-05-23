# MyStock - 台股法人目標價與 K 線分析工具

這是一個輕量級的台股分析工具，專為追蹤法人目標價、潛在報酬率以及日 K 線趨勢而設計。

## 🌟 核心功能

- **法人目標價追蹤**：自動抓取並整合各大券商對個股的評等與目標價。
- **潛在報酬率計算**：結合最新收盤價，自動計算與中位數目標價的價差。
- **動態 K 線圖表**：內建 TradingView 風格的 K 線圖，支援布林通道 (BOLL) 與成交量指標。
- **隨選補抓 (On-Demand Fetching)**：
  - K 線資料不再需要每日手動補抓。
  - 當您點開任何個股時，系統會自動透過 FinMind API 補齊近 2 年的歷史資料。
  - 資料若超過 4 小時未更新，背景會自動觸發更新，確保圖表資訊始終維持最新狀態。
- **多組自選股管理**：支援多個自選清單，方便分類管理追蹤標的。

## 🚀 快速啟動

1. **安裝依賴**：
   ```bash
   pip install -r requirements.txt
   ```

2. **啟動 Web 介面**：
   ```bash
   python serve.py
   ```
   啟動後，瀏覽器將自動開啟並導向 `http://127.0.0.1:8765`。

3. **資料更新（可選）**：
   - 法人目標價：`python fetch_target_price.py`
   - 每日 K 線增量：`python fetch_daily_kline.py` (註：目前已改為隨選自動補抓，非必要手動執行)

## 🛠 技術棧

- **Backend**: Python (Flask)
- **Frontend**: 原生 JavaScript + Vanilla CSS
- **Chart**: KLineChart
- **Data Source**: CMoney App API, FinMind, TWSE/TPEX OpenAPI

---
*本工具僅供參考，投資請自行評估風險。*
