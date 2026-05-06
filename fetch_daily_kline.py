# -*- coding: utf-8 -*-
"""
台股日 K 線資料擷取腳本（TWSE 上市 + TPEX 上櫃）。

功能：
    * Bootstrap：首次執行時回補每檔近 N 個月的日 K 線（預設 6 個月）
        - TWSE 上市：每檔呼叫 STOCK_DAY N 次（一次一個月）
        - TPEX 上櫃：改走 FinMind TaiwanStockPrice（每檔 1 次 API 呼叫
          即可取得整段日期範圍的 OHLCV）
          ※ TPEX 官方 OpenAPI `tpex_mainboard_daily_close_quotes` 的
            `d=` 參數其實會被忽略，一律只回當日快照；歷史資料因此
            在 2026-04 決定改走 FinMind。
    * Incremental：每日執行，一次 2 個 API 呼叫取得全市場當日 OHLCV，
      再追加到各股對應的 JSON 檔
      （TPEX OpenAPI 當日快照仍正確，因此 incremental 路徑沿用）

資料存放：
    日K線_log_file/{stock_id}.json
    {
      "stock_id": "2330",
      "market": "上市",
      "last_updated": "20260418123045",
      "entries": [
        {"date": "20260101", "open":..., "high":..., "low":..., "close":..., "volume":...},
        ...   # 依日期升冪，去重
      ]
    }

使用方式：
    source venv/bin/activate
    pip install requests
    python fetch_daily_kline.py --bootstrap          # 首次回補近 6 個月
    python fetch_daily_kline.py                      # 每日增量
    python fetch_daily_kline.py --stock 2330         # 只跑單檔（測試）
    python fetch_daily_kline.py --bootstrap --months 12

FinMind token（可選）：
    export FINMIND_TOKEN="你的_FinMind_API_token"
    匿名也可用，但每小時限 300 次；我們 bootstrap 36 檔上櫃只需 36 次，足夠。
    註冊 https://finmindtrade.com 取得免費 token 可拉到 600 次/小時。
"""

import argparse
import csv
import json
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests
    from urllib3.exceptions import InsecureRequestWarning
    warnings.simplefilter("ignore", InsecureRequestWarning)
except ImportError:
    print("[錯誤] 缺少 requests，請先 pip install requests")
    sys.exit(1)

# ============================================================
# 設定區
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
STOCKLIST_GLOB = "stocklist_*.txt"
MARKET_LIST_FILE = SCRIPT_DIR / "tw_stock_list.csv"
KLINE_DIR = SCRIPT_DIR / "日K線_log_file"

BOOTSTRAP_MONTHS = 6
# TWSE 限制 3 次 / 5 秒，留安全邊界
REQUEST_INTERVAL_SEC = 1.8
# FinMind 匿名限制 300/hr；保守留 0.3 秒間隔
FINMIND_INTERVAL_SEC = 0.3
FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "")
VERIFY_SSL = False  # 公司 MITM 自簽環境請設 False
HTTP_TIMEOUT = 30
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",  # 不要 br，Python requests 沒內建解壓
})


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def roc_to_ymd(roc_date):
    """'115/04/17' → '20260417'；失敗回 None"""
    if roc_date is None:
        return None
    s = str(roc_date).strip().replace("＊", "").replace("*", "")
    parts = s.split("/")
    if len(parts) != 3:
        return None
    try:
        y = int(parts[0]) + 1911
        m = int(parts[1])
        d = int(parts[2])
        return f"{y:04d}{m:02d}{d:02d}"
    except ValueError:
        return None


def parse_number(s):
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if s in ("", "--", "-", "X", "null"):
        return None
    if s.startswith("+"):
        s = s[1:]
    try:
        return float(s)
    except ValueError:
        return None


def load_stocklist():
    """掃描所有 stocklist_*.txt 並聯集去重"""
    codes = set()
    found_files = sorted(SCRIPT_DIR.glob(STOCKLIST_GLOB))
    if not found_files:
        log(f"[錯誤] 找不到任何股票清單檔（pattern：{STOCKLIST_GLOB}）")
        sys.exit(1)

    for p in found_files:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                code = line.strip()
                # 排除空行與註解
                if code and not code.startswith("#"):
                    codes.add(code)
    return sorted(list(codes))


def load_market_map():
    """讀 tw_stock_list.csv → {code: '上市'|'上櫃'}"""
    m = {}
    if not MARKET_LIST_FILE.exists():
        return m
    with open(MARKET_LIST_FILE, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = (row.get("code") or "").strip()
            market = (row.get("market") or "").strip()
            if code:
                m[code] = market
    return m


def today_ymd():
    return datetime.now().strftime("%Y%m%d")


# ------------------------------------------------------------
# 單檔單月（Bootstrap 用）
# ------------------------------------------------------------
def fetch_twse_stock_month(stock_id, year, month):
    """TWSE 上市：回一個月的日 OHLCV 列表"""
    date = f"{year:04d}{month:02d}01"
    url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
    params = {"response": "json", "date": date, "stockNo": stock_id}
    try:
        r = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT, verify=VERIFY_SSL)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log(f"  [TWSE][{stock_id}] {year}-{month:02d} 請求失敗：{e}")
        return []
    if data.get("stat") != "OK":
        # 該月無資料或回無效（例：未上市期間）
        return []
    rows = data.get("data") or []
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            continue
        date_str = roc_to_ymd(row[0])
        if not date_str:
            continue
        out.append({
            "date":   date_str,
            "open":   parse_number(row[3]),
            "high":   parse_number(row[4]),
            "low":    parse_number(row[5]),
            "close":  parse_number(row[6]),
            "volume": parse_number(row[1]),
        })
    return out


# 註：TPEX 2024/10 改版後舊的 st43_result.php 已停用，因此不再提供「單檔單月」API；
# 上櫃一律改走 OpenAPI 的「每日全市場」端點（可帶 d= 參數取歷史某日）。


# ------------------------------------------------------------
# 全市場（當日或指定日）
# ------------------------------------------------------------
def fetch_twse_all_today():
    """TWSE OpenAPI：當日全市場 → {code: {date, open, high, low, close, volume}}

    STOCK_DAY_ALL 只有「最新一個交易日」的資料，無法指定日期。
    """
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        r = SESSION.get(url, timeout=HTTP_TIMEOUT, verify=VERIFY_SSL)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log(f"[TWSE] STOCK_DAY_ALL 失敗：{e}")
        return {}
    day = today_ymd()
    out = {}
    for item in data or []:
        if not isinstance(item, dict):
            continue
        code = (item.get("Code") or "").strip()
        close = parse_number(item.get("ClosingPrice"))
        if not code or close is None:
            continue
        out[code] = {
            "date":   day,
            "open":   parse_number(item.get("OpeningPrice")),
            "high":   parse_number(item.get("HighestPrice")),
            "low":    parse_number(item.get("LowestPrice")),
            "close":  close,
            "volume": parse_number(item.get("TradeVolume")),
        }
    return out


def fetch_finmind_stock_history(stock_id, start_date, end_date=None):
    """FinMind TaiwanStockPrice：一次取一檔股票整段日期範圍的 OHLCV。

    Args:
        stock_id:   台股代號（字串，例如 "8299"）
        start_date: datetime.date / datetime.datetime
        end_date:   datetime.date / datetime.datetime，預設今天

    Returns:
        list[dict]：[{"date":"YYYYMMDD","open":,"high":,"low":,"close":,"volume":}, ...]
        查無資料或失敗回 []
    """
    if end_date is None:
        end_date = datetime.today()

    params = {
        "dataset":    "TaiwanStockPrice",
        "data_id":    str(stock_id),
        "start_date": start_date.strftime("%Y-%m-%d"),
        "end_date":   end_date.strftime("%Y-%m-%d"),
    }
    headers = {}
    if FINMIND_TOKEN:
        headers["Authorization"] = f"Bearer {FINMIND_TOKEN}"

    try:
        r = SESSION.get(
            FINMIND_API_URL,
            params=params,
            headers=headers,
            timeout=HTTP_TIMEOUT,
            verify=VERIFY_SSL,
        )
        if r.status_code == 402:
            log(f"  [FinMind][{stock_id}] 配額用完（402），停一會再試")
            return []
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log(f"  [FinMind][{stock_id}] 請求失敗：{e}")
        return []

    if not isinstance(data, dict):
        return []
    if data.get("status") not in (200, "200"):
        msg = data.get("msg") or data.get("message") or "unknown"
        log(f"  [FinMind][{stock_id}] API 回覆非 200：{msg}")
        return []

    rows = data.get("data") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # FinMind 欄位：date, Trading_Volume, open, max, min, close
        d_raw = row.get("date")
        if not d_raw:
            continue
        # date 形如 "2024-06-03" → "20240603"
        d_str = str(d_raw).replace("-", "").strip()
        if len(d_str) != 8 or not d_str.isdigit():
            continue
        close = row.get("close")
        if close is None:
            continue
        out.append({
            "date":   d_str,
            "open":   row.get("open"),
            "high":   row.get("max"),
            "low":    row.get("min"),
            "close":  close,
            "volume": row.get("Trading_Volume"),
        })
    return out


def fetch_tpex_all_by_date(dt=None):
    """TPEX OpenAPI：當日全市場快照（僅當日正確；`d=` 參數會被官方忽略）。

    歷史資料請改用 fetch_finmind_stock_history()。

    Returns: {code: {date, open, high, low, close, volume}}；查無資料回 {}
    """
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    params = {"l": "zh-tw"}
    if dt is not None:
        # 注意：此 endpoint 實際忽略 d=，永遠回今日快照。保留是為了 incremental。
        roc_y = dt.year - 1911
        params["d"] = f"{roc_y}/{dt.month:02d}/{dt.day:02d}"
        day = dt.strftime("%Y%m%d")
    else:
        day = today_ymd()

    try:
        r = SESSION.get(url, params=params, timeout=HTTP_TIMEOUT, verify=VERIFY_SSL)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        if dt:
            log(f"  [TPEX] {dt.strftime('%Y-%m-%d')} 失敗：{e}")
        else:
            log(f"[TPEX] daily_close_quotes 失敗：{e}")
        return {}

    if not data:
        return {}

    out = {}
    for item in data or []:
        if not isinstance(item, dict):
            continue
        # TPEX 欄位命名跨端點不一致，多試幾個 key
        code = (item.get("SecuritiesCompanyCode")
                or item.get("Code")
                or item.get("code")
                or "").strip()
        close = parse_number(
            item.get("Close")
            or item.get("ClosingPrice")
            or item.get("LastPrice")
        )
        if not code or close is None:
            continue
        out[code] = {
            "date":   day,
            "open":   parse_number(item.get("Open") or item.get("OpeningPrice")),
            "high":   parse_number(item.get("High") or item.get("HighestPrice")),
            "low":    parse_number(item.get("Low") or item.get("LowestPrice")),
            "close":  close,
            "volume": parse_number(
                item.get("TradingShares")
                or item.get("TradeVolume")
                or item.get("Volume")
            ),
        }
    return out


def fetch_tpex_all_today():
    """Incremental 用：取當日全市場 OHLCV（TPEX OpenAPI 當日快照）"""
    return fetch_tpex_all_by_date(None)


# ------------------------------------------------------------
# 檔案讀寫
# ------------------------------------------------------------
def load_kline(stock_id):
    path = KLINE_DIR / f"{stock_id}.json"
    if not path.exists():
        return {"stock_id": stock_id, "entries": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"stock_id": stock_id, "entries": []}
        data.setdefault("stock_id", stock_id)
        data.setdefault("entries", [])
        return data
    except Exception:
        return {"stock_id": stock_id, "entries": []}


def save_kline(stock_id, kline, market=None):
    KLINE_DIR.mkdir(exist_ok=True)
    path = KLINE_DIR / f"{stock_id}.json"
    kline["stock_id"] = stock_id
    if market:
        kline["market"] = market
    # 日期升冪排序 + 去重（以 date 為 key）
    by_date = {}
    for e in kline.get("entries", []):
        d = e.get("date")
        if d:
            by_date[d] = e
    kline["entries"] = [by_date[d] for d in sorted(by_date.keys())]
    kline["last_updated"] = datetime.now().strftime("%Y%m%d%H%M%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kline, f, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------
# Bootstrap: TWSE（上市，逐檔逐月）
# ------------------------------------------------------------
def bootstrap_twse_stock(stock_id, months=BOOTSTRAP_MONTHS):
    """TWSE 上市：一檔股票呼叫 STOCK_DAY N 次取得近 N 個月 OHLCV"""
    kline = load_kline(stock_id)
    existing = {e["date"] for e in kline.get("entries", []) if e.get("date")}

    # 從本月往前回推 months 個月
    today = datetime.today()
    month_list = []
    y, m = today.year, today.month
    for _ in range(months):
        month_list.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    added = 0
    for y, m in month_list:
        rows = fetch_twse_stock_month(stock_id, y, m)
        for r in rows:
            if r["date"] not in existing:
                kline["entries"].append(r)
                existing.add(r["date"])
                added += 1
        time.sleep(REQUEST_INTERVAL_SEC)

    save_kline(stock_id, kline, market="上市")
    return len(kline["entries"]), added


# ------------------------------------------------------------
# Bootstrap: TPEX（上櫃，改走 FinMind，每檔 1 次 API 呼叫）
# ------------------------------------------------------------
def bootstrap_tpex_stock(stock_id, months=BOOTSTRAP_MONTHS):
    """TPEX 上櫃：FinMind 一次 API 呼叫就能取得一檔整段日期範圍的 OHLCV。

    比舊版「逐日批次」省下極大量 API 呼叫：
      舊：N 個交易日 × 1 call/day（但 d= 被忽略，資料全錯）
      新：1 call/stock，範圍整段涵蓋
    """
    kline = load_kline(stock_id)
    existing = {e["date"] for e in kline.get("entries", []) if e.get("date")}

    today = datetime.today()
    start = today - timedelta(days=months * 31 + 5)
    rows = fetch_finmind_stock_history(stock_id, start, today)

    added = 0
    for r in rows:
        if r["date"] not in existing:
            kline["entries"].append(r)
            existing.add(r["date"])
            added += 1

    save_kline(stock_id, kline, market="上櫃")
    return len(kline["entries"]), added


def bootstrap_tpex_batch(tpex_codes, months=BOOTSTRAP_MONTHS):
    """Bootstrap 所有上櫃檔：逐檔呼叫 FinMind。"""
    if not tpex_codes:
        return 0

    est_min = len(tpex_codes) * FINMIND_INTERVAL_SEC / 60
    log(f"  TPEX（FinMind）：{len(tpex_codes)} 檔，預估 {est_min:.1f} 分鐘")
    if not FINMIND_TOKEN:
        log(f"  （未設 FINMIND_TOKEN，使用匿名模式；匿名限 300 次/小時）")

    added_total = 0
    for i, code in enumerate(tpex_codes, 1):
        try:
            total, added = bootstrap_tpex_stock(code, months=months)
            added_total += added
            log(f"    [TPEX {i}/{len(tpex_codes)}] {code} → 累計 {total} 筆（本次新增 {added}）")
        except Exception as e:
            log(f"    [TPEX {i}/{len(tpex_codes)}] {code} 失敗：{e}")
        time.sleep(FINMIND_INTERVAL_SEC)

    log(f"  TPEX 完成：共新增 {added_total} 筆")
    return added_total


# ------------------------------------------------------------
# Incremental
# ------------------------------------------------------------
def incremental_update(stocks, market_map):
    log("抓取 TWSE 當日全市場 …")
    twse_today = fetch_twse_all_today()
    log(f"  TWSE 取得 {len(twse_today)} 檔")
    time.sleep(1)
    log("抓取 TPEX 當日全市場 …")
    tpex_today = fetch_tpex_all_today()
    log(f"  TPEX 取得 {len(tpex_today)} 檔")

    if not twse_today and not tpex_today:
        log("[錯誤] 兩個 API 都沒資料，可能是非交易日或連線問題")
        return

    day = today_ymd()
    updated = 0
    skipped_same_day = 0
    skipped_duplicate_data = 0
    backfilled_stocks = []
    not_found = []

    # 假日檢查：若是週六 (5) 或週日 (6)，且非強制執行，則跳過更新
    # 台灣股市週六日不開盤
    now = datetime.now()
    is_weekend = now.weekday() >= 5
    
    for code in stocks:
        market = market_map.get(code, "上市")
        kline = load_kline(code)
        entries = kline.get("entries", [])

        # ... (backfill logic remains same)

        # 如果最後一筆已是今天，跳過
        if entries and entries[-1].get("date") == day:
            skipped_same_day += 1
            continue
            
        # 主來源依 market 決定，若沒有再去另一個找
        if market == "上櫃":
            rec = tpex_today.get(code) or twse_today.get(code)
        else:
            rec = twse_today.get(code) or tpex_today.get(code)

        if not rec or rec.get("close") is None:
            if code not in backfilled_stocks:
                not_found.append(code)
            continue

        # 關鍵修正：檢查 OHLCV 是否與最後一筆完全相同（代表可能是拿舊快照充數）
        if entries:
            last = entries[-1]
            if (last.get("close") == rec.get("close") and
                last.get("open") == rec.get("open") and
                last.get("high") == rec.get("high") and
                last.get("low") == rec.get("low") and
                last.get("volume") == rec.get("volume")):
                skipped_duplicate_data += 1
                continue

        # 如果是週末且資料沒變，或是 API 尚未更新成今天的資料，通常會發生在此
        # 但如果資料變了（例如週六補盤），則允許寫入
        
        entries.append({
            "date":   rec.get("date") or day,
            "open":   rec.get("open"),
            "high":   rec.get("high"),
            "low":    rec.get("low"),
            "close":  rec.get("close"),
            "volume": rec.get("volume"),
        })
        kline["entries"] = entries
        save_kline(code, kline, market=market)
        updated += 1

    log(f"更新 {updated} 檔；已是最新 {skipped_same_day} 檔；重複資料跳過 {skipped_duplicate_data} 檔"
        + (f"；補回 {len(backfilled_stocks)} 檔" if backfilled_stocks else "")
        + (f"；找不到 {len(not_found)} 檔" if not_found else "")
        + (f"：{not_found[:20]}{'…' if len(not_found) > 20 else ''}" if not_found else ""))


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="TWSE/TPEX 日 K 線擷取")
    ap.add_argument("--bootstrap", action="store_true",
                    help="回補近 N 個月歷史 K 線（每檔多次 API 呼叫）")
    ap.add_argument("--months", type=int, default=BOOTSTRAP_MONTHS,
                    help=f"回補月數，預設 {BOOTSTRAP_MONTHS}")
    ap.add_argument("--stock", type=str, default=None,
                    help="只處理特定股票代號（測試用）")
    args = ap.parse_args()

    stocks = [args.stock] if args.stock else load_stocklist()
    market_map = load_market_map()
    if not market_map:
        log("[警告] 找不到 tw_stock_list.csv，所有股票將預設走 TWSE 介面")

    KLINE_DIR.mkdir(exist_ok=True)

    if args.bootstrap:
        # 分離上市 / 上櫃（找不到市場別的預設走 TWSE）
        twse_codes = [c for c in stocks if market_map.get(c, "上市") != "上櫃"]
        tpex_codes = [c for c in stocks if market_map.get(c) == "上櫃"]

        log(f"Bootstrap 模式：TWSE {len(twse_codes)} 檔（逐檔逐月）"
            f" + TPEX {len(tpex_codes)} 檔（逐日批次）")

        # --- TWSE 段 ---
        if twse_codes:
            est_min = len(twse_codes) * args.months * REQUEST_INTERVAL_SEC / 60
            log(f"--- TWSE 開始，預估 {est_min:.1f} 分鐘 ---")
            for i, code in enumerate(twse_codes, 1):
                log(f"[TWSE {i}/{len(twse_codes)}] {code}")
                try:
                    total, added = bootstrap_twse_stock(code, months=args.months)
                    log(f"  → 累計 {total} 筆（本次新增 {added}）")
                except Exception as e:
                    log(f"  失敗：{e}")

        # --- TPEX 段 ---
        if tpex_codes:
            log("--- TPEX 開始 ---")
            try:
                bootstrap_tpex_batch(tpex_codes, months=args.months)
            except Exception as e:
                log(f"TPEX batch 失敗：{e}")
    else:
        log(f"Incremental：更新 {len(stocks)} 檔")
        incremental_update(stocks, market_map)

    log("完成")


if __name__ == "__main__":
    main()
