# -*- coding: utf-8 -*-
"""
法人目標價分析 - 輕量本地 Web Service
只允許 127.0.0.1 連線，啟動後自動開啟瀏覽器。

使用方式：
    source venv/bin/activate
    pip install flask
    python serve.py
"""

import csv
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

try:
    from flask import Flask, jsonify, send_from_directory, abort
except ImportError:
    print("[錯誤] 沒有安裝 flask，請執行：pip install flask")
    sys.exit(1)

# ============================================================
# 設定區
# ============================================================
LOG_ROOT_DIR = "法人目標價_log_file"
KLINE_DIR = "日K線_log_file"
MARKET_LIST_FILE = "tw_stock_list.csv"
PORT = 8765
RECENT_DAYS = 90  # 用於計算最高目標價與中位數的時間窗
REFETCH_TIMEOUT_SEC = 180  # 重抓單檔的子程序逾時
# ============================================================

# 同時間只允許一個重抓在跑（避免重複觸發 / race）
_refetch_lock = threading.Lock()

SCRIPT_DIR = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)


def find_latest_date_folder() -> Path | None:
    """找到 LOG_ROOT_DIR 底下最新的 yyyyMMdd 資料夾"""
    root = SCRIPT_DIR / LOG_ROOT_DIR
    if not root.exists():
        return None
    candidates = []
    for d in root.iterdir():
        if d.is_dir() and len(d.name) == 8 and d.name.isdigit():
            try:
                datetime.strptime(d.name, "%Y%m%d")
                candidates.append(d)
            except ValueError:
                pass
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.name)


def _find_idx(titles, keyword, default):
    for i, t in enumerate(titles):
        if t and keyword in t:
            return i
    return default


def load_market_map() -> dict:
    """讀 tw_stock_list.csv → {code: '上市'|'上櫃'}。

    備援：若主 CSV 缺某檔，會用 KLINE_DIR 下已寫入的 market 欄位補齊。
    """
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
    # Fallback：從 K 線 JSON 補齊（bootstrap 時會寫入）
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
    """讀 日K線_log_file/ 下每一檔的最後一筆 entry

    回 {code: {"date": "YYYYMMDD", "close": float}}
    """
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
        # fetch_daily_kline.py 寫入時已按日期升冪排序 + 去重
        last = entries[-1]
        close_val = last.get("close")
        if close_val is None:
            continue
        code = (data.get("stock_id") or f.stem).strip()
        out[code] = {"date": last.get("date"), "close": close_val}
    return out


def parse_stock_file(file_path: Path):
    """讀取單一股票 JSON，回傳聚合後的資料（含 entries list）"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    if "data" not in data or "titles" not in data:
        return None

    titles = data.get("titles") or []
    rows = data.get("data") or []
    if not rows:
        return None

    # 從檔名取股票代號與名稱作為備援
    stem_parts = file_path.stem.split("_", 2)
    stock_id = stem_parts[1] if len(stem_parts) >= 2 else ""
    stock_name_from_file = stem_parts[2] if len(stem_parts) >= 3 else ""

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

    # 股票名稱：優先從 data 取，fallback 到檔名
    stock_name = stock_name_from_file
    if idx_stock_name < len(rows[0]):
        tmp = str(rows[0][idx_stock_name]).strip()
        if tmp:
            stock_name = tmp

    # 按日期遞減排序（最新在前）
    entries.sort(key=lambda e: e["date"], reverse=True)

    # 篩選近 N 天
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
    # recent_entries 已按日期遞減排序，第一筆就是「近 90 天內最新一筆」
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


# ----- Security: 只讓 127.0.0.1 能用 -----
@app.before_request
def _limit_to_localhost():
    from flask import request
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)


# ----- Routes -----
@app.route("/")
def index():
    return send_from_directory(str(SCRIPT_DIR), "index.html")


@app.route("/api/stocks")
def api_stocks():
    from flask import request
    date_param = (request.args.get("date") or "").strip()

    target_folder: Path | None = None
    if date_param:
        # 驗證格式，避免 path traversal
        if len(date_param) != 8 or not date_param.isdigit():
            abort(400)
        try:
            datetime.strptime(date_param, "%Y%m%d")
        except ValueError:
            abort(400)
        candidate = SCRIPT_DIR / LOG_ROOT_DIR / date_param
        if candidate.exists() and candidate.is_dir():
            target_folder = candidate

    if target_folder is None:
        target_folder = find_latest_date_folder()

    if target_folder is None:
        return jsonify({"date": None, "recent_days": RECENT_DAYS, "stocks": []})

    latest_closes = load_latest_closes()
    market_map = load_market_map()

    stocks = []
    for f in sorted(target_folder.glob("*.json")):
        parsed = parse_stock_file(f)
        if not parsed:
            continue

        # 標註上市 / 上櫃
        parsed["market"] = market_map.get(parsed["stock_id"]) or None

        # 以日 K 線資料覆寫主畫面的「最新收盤價」，並重算潛在報酬
        lc = latest_closes.get(parsed["stock_id"])
        if lc and lc.get("close") is not None:
            parsed["close"] = lc["close"]
            parsed["close_date"] = lc.get("date")
            parsed["close_source"] = "kline"
            if parsed.get("median_target") is not None and parsed["close"]:
                parsed["potential_return"] = round(
                    100.0 * (parsed["median_target"] - parsed["close"]) / parsed["close"], 2
                )
        else:
            parsed["close_date"] = None
            parsed["close_source"] = "broker_report"

        stocks.append(parsed)

    return jsonify({
        "date": target_folder.name,
        "recent_days": RECENT_DAYS,
        "stocks": stocks,
    })


@app.route("/api/dates")
def api_dates():
    """列出有哪些日期資料夾（之後擴充用）"""
    root = SCRIPT_DIR / LOG_ROOT_DIR
    if not root.exists():
        return jsonify({"dates": []})
    dates = []
    for d in root.iterdir():
        if d.is_dir() and len(d.name) == 8 and d.name.isdigit():
            dates.append(d.name)
    dates.sort(reverse=True)
    return jsonify({"dates": dates})


@app.route("/api/kline")
def api_kline():
    """單檔日 K 線資料。

    Query: /api/kline?code=2330
    Response:
        {
          "stock_id": "2330",
          "market": "上市",
          "last_updated": "...",
          "entries": [{"date":"20260101","open":...,"high":...,"low":...,"close":...,"volume":...}, ...]
        }
    """
    from flask import request
    code = (request.args.get("code") or "").strip()
    # 僅允許英數字，避免 path traversal
    if not code or len(code) > 10 or not code.isalnum():
        abort(400)
    path = SCRIPT_DIR / KLINE_DIR / f"{code}.json"
    if not path.exists() or not path.is_file():
        abort(404)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        abort(500)
    return jsonify(data)


def _run_fetch_subprocess(script_name: str, extra_args: list) -> dict:
    """呼叫子程序重抓單檔資料，回傳結果摘要。"""
    cmd = [sys.executable, str(SCRIPT_DIR / script_name), *extra_args]
    try:
        r = subprocess.run(
            cmd,
            cwd=str(SCRIPT_DIR),
            capture_output=True,
            text=True,
            timeout=REFETCH_TIMEOUT_SEC,
            check=False,
        )
        stdout = (r.stdout or "").strip()
        stderr = (r.stderr or "").strip()
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "cmd": " ".join(cmd[1:]),  # 去掉 python 可執行檔路徑
            "stdout_tail": stdout[-800:],
            "stderr_tail": stderr[-800:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout (>{REFETCH_TIMEOUT_SEC}s)"}
    except FileNotFoundError as e:
        return {"ok": False, "error": f"script not found: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _compute_existing_kline_months(code: str, default_months: int = 12) -> int:
    """讀現有 kline JSON，回傳從最早一筆到今天的月數（至少 12、最多 60）。

    下限 12：重抓時至少回補 1 年，避免圖表過短難看趨勢。
    """
    MIN_MONTHS = 12
    MAX_MONTHS = 60
    path = SCRIPT_DIR / KLINE_DIR / f"{code}.json"
    if not path.exists():
        return default_months
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return default_months
    entries = data.get("entries") or []
    if not entries:
        return default_months
    # entries 已升冪排序
    first_date = str(entries[0].get("date") or "")
    if len(first_date) != 8 or not first_date.isdigit():
        return default_months
    try:
        first = datetime.strptime(first_date, "%Y%m%d")
    except ValueError:
        return default_months
    today = datetime.now()
    months = (today.year - first.year) * 12 + (today.month - first.month)
    if today.day >= first.day:
        months += 1  # 同月內算 1 個月，當天日期已過就再 +1
    return max(MIN_MONTHS, min(MAX_MONTHS, months))


@app.route("/api/refetch", methods=["POST"])
def api_refetch():
    """重抓單檔資料。

    Query:
        POST /api/refetch?code=2330&scope=target_price
        POST /api/refetch?code=2330&scope=kline

    scope:
        - target_price（預設）：只重抓法人目標價
        - kline：只重抓日 K 線，bootstrap 月數依現有歷史長度自動推算
    """
    from flask import request
    code = (request.args.get("code") or "").strip()
    scope = (request.args.get("scope") or "target_price").strip()
    if not code or len(code) > 10 or not code.isalnum():
        abort(400)
    if scope not in ("target_price", "kline"):
        abort(400)

    # 避免同時觸發多個重抓
    if not _refetch_lock.acquire(blocking=False):
        return jsonify({
            "ok": False,
            "code": code,
            "scope": scope,
            "error": "另一檔正在重抓中，請稍後再試。",
        }), 429

    try:
        if scope == "target_price":
            result = _run_fetch_subprocess(
                "fetch_target_price.py",
                ["--stock", code],
            )
            return jsonify({
                "ok": bool(result.get("ok")),
                "code": code,
                "scope": scope,
                "details": {"target_price": result},
            })
        else:  # kline
            months = _compute_existing_kline_months(code)
            result = _run_fetch_subprocess(
                "fetch_daily_kline.py",
                ["--bootstrap", "--months", str(months), "--stock", code],
            )
            return jsonify({
                "ok": bool(result.get("ok")),
                "code": code,
                "scope": scope,
                "months": months,
                "details": {"kline": result},
            })
    finally:
        _refetch_lock.release()


def _open_browser():
    time.sleep(1.0)
    webbrowser.open(f"http://127.0.0.1:{PORT}/")


if __name__ == "__main__":
    print(f"[資訊] 啟動 Web Service: http://127.0.0.1:{PORT}/")
    print(f"[資訊] 1 秒後自動開啟瀏覽器。按 Ctrl+C 停止。")
    threading.Thread(target=_open_browser, daemon=True).start()
    # 只綁 127.0.0.1，不對外開放
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
