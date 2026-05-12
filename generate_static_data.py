# -*- coding: utf-8 -*-
"""
產生靜態 JSON 資料庫，供 GitHub Pages 使用。
將原本由 serve.py (Flask) 動態產生的 API 回應轉存為實體 .json 檔案。
"""

import json
import os
import csv
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# 設定區（應與 serve.py 保持一致）
# ============================================================
LOG_ROOT_DIR = "法人目標價_log_file"
KLINE_DIR = "日K線_log_file"
MARKET_LIST_FILE = "tw_stock_list.csv"
RECENT_DAYS = 90
STOCKLIST_PREFIX = "stocklist_"
STOCKLIST_SUFFIX = ".txt"
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "data"

def _find_idx(titles, keyword, default):
    for i, t in enumerate(titles):
        if t and keyword in t:
            return i
    return default

def load_stock_name_map() -> dict:
    m = {}
    path = SCRIPT_DIR / MARKET_LIST_FILE
    if not path.exists():
        return m
    try:
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get("code") or "").strip()
                name = (row.get("name") or "").strip()
                if code and name:
                    m[code] = name
    except Exception:
        pass
    return m

def load_market_map() -> dict:
    m = {}
    path = SCRIPT_DIR / MARKET_LIST_FILE
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    code = (row.get("code") or "").strip()
                    market = (row.get("market") or "").strip()
                    if code and market:
                        m[code] = market
        except Exception:
            pass
    kroot = SCRIPT_DIR / KLINE_DIR
    if kroot.exists():
        for f in kroot.glob("*.json"):
            code = f.stem.strip()
            if code in m:
                continue
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                mk = (data.get("market") or "").strip()
                if mk:
                    m[code] = mk
            except Exception:
                continue
    return m

def load_latest_closes() -> dict:
    root = SCRIPT_DIR / KLINE_DIR
    if not root.exists():
        return {}
    out = {}
    for f in root.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        entries = data.get("entries") or []
        if not entries:
            continue
        last = entries[-1]
        close_val = last.get("close")
        if close_val is None:
            continue
        code = (data.get("stock_id") or f.stem).strip()
        out[code] = {"date": last.get("date"), "close": close_val}
    return out

def parse_stock_file(file_path: Path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if not isinstance(data, dict) or "data" not in data or "titles" not in data:
        return None

    titles = data.get("titles") or []
    rows = data.get("data") or []
    if not rows:
        return None

    stem_parts = file_path.stem.split("_", 1)
    stock_id = stem_parts[0] if len(stem_parts) >= 1 else ""
    stock_name_from_file = stem_parts[1] if len(stem_parts) >= 2 else ""

    idx_date = _find_idx(titles, "日期", 0)
    idx_broker = _find_idx(titles, "券商名稱", 2)
    idx_rating = _find_idx(titles, "投資評等", 3)
    idx_target = _find_idx(titles, "目標價", 4)
    idx_close = _find_idx(titles, "收盤價", 5)
    idx_summary = _find_idx(titles, "敘述式摘要", 7)
    idx_stock_name = 19
    for i, t in enumerate(titles):
        if t and "股票名稱" in t:
            idx_stock_name = i
            break

    entries = []
    max_idx = max(idx_date, idx_broker, idx_rating, idx_target, idx_close, idx_summary)
    for row in rows:
        if not isinstance(row, list) or len(row) <= max_idx:
            continue
        try:
            target_str = str(row[idx_target]).strip()
            close_str = str(row[idx_close]).strip()
            if not target_str or not close_str:
                continue
            target = float(target_str)
            close = float(close_str)
        except (ValueError, TypeError):
            continue

        entries.append({
            "date": str(row[idx_date]),
            "broker": str(row[idx_broker]) if idx_broker < len(row) else "",
            "rating": str(row[idx_rating]) if idx_rating < len(row) else "",
            "target": target,
            "close": close,
            "summary": str(row[idx_summary]) if idx_summary < len(row) else "",
        })

    if not entries:
        return None

    stock_name = stock_name_from_file
    if idx_stock_name < len(rows[0]):
        tmp = str(rows[0][idx_stock_name]).strip()
        if tmp:
            stock_name = tmp

    entries.sort(key=lambda e: e["date"], reverse=True)
    cutoff = (datetime.now() - timedelta(days=RECENT_DAYS)).strftime("%Y%m%d")
    recent_entries = [e for e in entries if e["date"] >= cutoff]
    close_price = entries[0]["close"]

    if not recent_entries:
        return {
            "stock_id": stock_id,
            "stock_name": stock_name,
            "close": close_price,
            "max_target": None,
            "max_target_date": None,
            "max_target_broker": None,
            "max_target_rating": None,
            "max_target_summary": None,
            "latest_target": None,
            "latest_target_date": None,
            "latest_target_broker": None,
            "latest_target_rating": None,
            "latest_target_summary": None,
            "median_target": None,
            "potential_return": None,
            "entries": entries,
            "recent_count": 0,
        }

    max_entry = max(recent_entries, key=lambda e: e["target"])
    latest_entry = recent_entries[0]
    targets_sorted = sorted(e["target"] for e in recent_entries)
    n = len(targets_sorted)
    if n % 2 == 1:
        median = targets_sorted[n // 2]
    else:
        median = (targets_sorted[n // 2 - 1] + targets_sorted[n // 2]) / 2.0

    potential_return = None
    if close_price:
        potential_return = round(100.0 * (median - close_price) / close_price, 2)

    return {
        "stock_id": stock_id,
        "stock_name": stock_name,
        "close": close_price,
        "max_target": max_entry["target"],
        "max_target_date": max_entry["date"],
        "max_target_broker": max_entry["broker"],
        "max_target_rating": max_entry["rating"],
        "max_target_summary": max_entry["summary"],
        "latest_target": latest_entry["target"],
        "latest_target_date": latest_entry["date"],
        "latest_target_broker": latest_entry["broker"],
        "latest_target_rating": latest_entry["rating"],
        "latest_target_summary": latest_entry["summary"],
        "median_target": round(median, 2),
        "potential_return": potential_return,
        "entries": entries,
        "recent_count": len(recent_entries),
    }

def _empty_stock_row(stock_id: str, stock_name: str, market: str | None, latest_close: dict | None) -> dict:
    close = None
    close_date = None
    close_source = None
    if latest_close and latest_close.get("close") is not None:
        close = latest_close["close"]
        close_date = latest_close.get("date")
        close_source = "kline"
    return {
        "stock_id": stock_id,
        "stock_name": stock_name,
        "market": market,
        "has_target_price": False,
        "close": close,
        "close_date": close_date,
        "close_source": close_source,
        "max_target": None,
        "max_target_date": None,
        "max_target_broker": None,
        "max_target_rating": None,
        "max_target_summary": None,
        "latest_target": None,
        "latest_target_date": None,
        "latest_target_broker": None,
        "latest_target_rating": None,
        "latest_target_summary": None,
        "median_target": None,
        "potential_return": None,
        "entries": [],
        "recent_count": 0,
    }

def list_watchlists():
    result = []
    for p in sorted(SCRIPT_DIR.glob(f"{STOCKLIST_PREFIX}*{STOCKLIST_SUFFIX}")):
        name = p.name[len(STOCKLIST_PREFIX): -len(STOCKLIST_SUFFIX)]
        if not name:
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                count = sum(1 for line in f if line.strip() and not line.strip().startswith("#"))
        except OSError:
            count = 0
        result.append({"name": name, "count": count})
    return result

def read_watchlist_stocks(name: str) -> list:
    p = SCRIPT_DIR / f"{STOCKLIST_PREFIX}{name}{STOCKLIST_SUFFIX}"
    if not p.exists():
        return []
    ids = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                ids.append(s)
    return ids

def generate():
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    # 1. 產生 watchlists.json
    watchlists = list_watchlists()
    with open(OUTPUT_DIR / "watchlists.json", "w", encoding="utf-8") as f:
        json.dump({"watchlists": watchlists}, f, ensure_ascii=False, indent=2)
    print(f"Generated data/watchlists.json")

    # 2. 預備共用資料
    latest_closes = load_latest_closes()
    market_map = load_market_map()
    name_map = load_stock_name_map()
    
    root = SCRIPT_DIR / LOG_ROOT_DIR
    kroot = SCRIPT_DIR / KLINE_DIR
    all_parsed_stocks = {}
    latest_mtime = 0.0
    
    # 掃描法人目標價檔案
    if root.exists():
        for f in sorted(root.glob("*.json")):
            parsed = parse_stock_file(f)
            if not parsed:
                continue
            
            sid = parsed["stock_id"]
            all_parsed_stocks[sid] = parsed
            try:
                mt = f.stat().st_mtime
                if mt > latest_mtime:
                    latest_mtime = mt
            except OSError:
                pass

    # 掃描日K線檔案更新時間
    if kroot.exists():
        for f in kroot.glob("*.json"):
            try:
                mt = f.stat().st_mtime
                if mt > latest_mtime:
                    latest_mtime = mt
            except OSError:
                pass

    # 補全 market, close 等資訊
    for sid, parsed in all_parsed_stocks.items():
        parsed["market"] = market_map.get(sid) or None
        parsed["has_target_price"] = True
        
        lc = latest_closes.get(sid)
        if lc and lc.get("close") is not None:
            parsed["close"] = lc["close"]
            parsed["close_date"] = lc.get("date")
            parsed["close_source"] = "kline"
            if parsed.get("median_target") is not None and parsed["close"]:
                parsed["potential_return"] = round(100.0 * (parsed["median_target"] - parsed["close"]) / parsed["close"], 2)
        else:
            parsed["close_date"] = None
            parsed["close_source"] = "broker_report"

    # 取得檔案最後修改時間，並轉換為台灣時間 (UTC+8)
    last_updated = None
    if latest_mtime:
        # GitHub Runner 是 UTC，手動加上 8 小時轉換為台灣時間
        dt_utc = datetime.fromtimestamp(latest_mtime)
        dt_taiwan = dt_utc + timedelta(hours=8)
        last_updated = dt_taiwan.strftime("%Y-%m-%d %H:%M")

    # 3. 產生主表 stocks.json (All)
    all_list = sorted(all_parsed_stocks.values(), key=lambda x: x["stock_id"])
    with open(OUTPUT_DIR / "stocks.json", "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": last_updated,
            "recent_days": RECENT_DAYS,
            "stocks": all_list,
            "watchlist": None,
            "watchlist_total": None
        }, f, ensure_ascii=False, indent=2)
    print(f"Generated data/stocks.json")

    # 4. 產生各個自選股的 stocks_<name>.json
    for w in watchlists:
        w_name = w["name"]
        w_ids = read_watchlist_stocks(w_name)
        w_stocks = []
        seen_ids = set()
        
        for sid in w_ids:
            if sid in all_parsed_stocks:
                w_stocks.append(all_parsed_stocks[sid])
                seen_ids.add(sid)
            else:
                w_stocks.append(_empty_stock_row(sid, name_map.get(sid, ""), market_map.get(sid), latest_closes.get(sid)))
        
        with open(OUTPUT_DIR / f"stocks_{w_name}.json", "w", encoding="utf-8") as f:
            json.dump({
                "last_updated": last_updated,
                "recent_days": RECENT_DAYS,
                "stocks": w_stocks,
                "watchlist": w_name,
                "watchlist_total": len(w_ids)
            }, f, ensure_ascii=False, indent=2)
        print(f"Generated data/stocks_{w_name}.json")

if __name__ == "__main__":
    generate()
