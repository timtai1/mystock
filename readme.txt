在 Mac 終端機進到腳本所在的資料夾後，依序執行：

第一次執行
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install requests
pip install beautifulsoup4
pip install flask        # 第一次才需要

設定 CMoney Bearer Token（每次開新 shell 都要；寫到 ~/.zshrc 可永久生效）
export CMONEY_AUTH_TOKEN="你的_bearer_jwt"

每日執行
python fetch_target_price.py                       # 自動掃描 stocklist_*.txt（所有自選股清單）
# python fetch_daily_kline.py                      # (註：目前已改為隨選補抓，非必要執行)

隨選自動補抓 (On-Demand Fetching)
1. 點開 K 線圖時，系統會自動檢查並補齊近 2 年資料。
2. 若資料超過 4 小時未更新，背景會自動重抓，保持最新。

首次回補 K 線（或上櫃資料壞掉重抓時用）
python fetch_daily_kline.py --bootstrap --months 13

# 可選：FinMind token（上櫃歷史資料來源）
# 匿名 300 req/hr 已夠 36 檔上櫃 bootstrap；註冊後 600 req/hr
# export FINMIND_TOKEN="你的_token"


WEB PORTAL
source venv/bin/activate
python3 serve.py