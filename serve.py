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
    from flask import Flask, jsonify, send_from_directory, abort, request
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

# 自選股清單檔名規則：stocklist_{群組名}.txt；群組名禁用字元與長度限制
STOCKLIST_PREFIX = "stocklist_"
STOCKLIST_SUFFIX = ".txt"
WATCHLIST_NAME_MAX_LEN = 32
WATCHLIST_FORBIDDEN_CHARS = set('/\\:*?"<>|')  # 跨平台安全的檔名禁用集
# ============================================================

# 同時間只允許一個重抓在跑（避免重複觸發 / race）
_refetch_lock = threading.Lock()

SCRIPT_DIR = Path(__file__).resolve().parent
app = Flask(__name__, static_folder=None)


def _find_idx(titles, keyword, default):
    for i, t in enumerate(titles):
        if t and keyword in t:
            return i
    return default


def load_stock_name_map() -> dict:
    """讀 tw_stock_list.csv → {code: name}，給自選股中沒有 log 檔的股票補名稱用。"""
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


def _empty_stock_row(stock_id: str, stock_name: str, market: str | None, latest_close: dict | None) -> dict:
    """在 watchlist 模式下，清單有但還沒抓到法人目標價的股票，回一個空殼資料列。"""
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

    # 從檔名取股票代號與名稱作為備援：檔名格式為 `{code}_{name}.json`
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


# ----- 自選股（watchlist）輔助 -----
def validate_watchlist_name(name: str) -> bool:
    """檢查群組名是否合法（排除空字串、超長、含危險字元、以 . 開頭）。"""
    if not isinstance(name, str):
        return False
    name = name.strip()
    if not name or len(name) > WATCHLIST_NAME_MAX_LEN:
        return False
    if name.startswith(".") or name.startswith("-"):
        return False
    if any(c in WATCHLIST_FORBIDDEN_CHARS for c in name):
        return False
    # 禁止 ASCII 控制字元
    if any(ord(c) < 32 for c in name):
        return False
    return True


def watchlist_path(name: str) -> Path:
    """群組名 → stocklist_{name}.txt 的完整路徑。"""
    return SCRIPT_DIR / f"{STOCKLIST_PREFIX}{name}{STOCKLIST_SUFFIX}"


def list_watchlists() -> list:
    """掃描 stocklist_*.txt，回 [{name, count}]（依群組名字串排序）。"""
    result = []
    for p in sorted(SCRIPT_DIR.glob(f"{STOCKLIST_PREFIX}*{STOCKLIST_SUFFIX}")):
        name = p.name[len(STOCKLIST_PREFIX): -len(STOCKLIST_SUFFIX)]
        if not name:
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                count = sum(
                    1 for line in f
                    if line.strip() and not line.strip().startswith("#")
                )
        except OSError:
            count = 0
        result.append({"name": name, "count": count})
    return result


def read_watchlist_stocks(name: str) -> list:
    """讀單一群組檔內的股票代號（忽略註解/空行）。找不到回空陣列。"""
    p = watchlist_path(name)
    if not p.exists():
        return []
    ids = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith("#"):
                ids.append(s)
    return ids


def next_default_watchlist_name() -> str:
    """找出「自選股N」最小未使用的 N。"""
    existing = {w["name"] for w in list_watchlists()}
    n = 1
    while f"自選股{n}" in existing:
        n += 1
    return f"自選股{n}"


# ----- Security: 只讓 127.0.0.1 能用 -----
@app.before_request
def _limit_to_localhost():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        abort(403)


# ----- Routes -----
@app.route("/")
def index():
    return send_from_directory(str(SCRIPT_DIR), "index.html")


@app.route("/api/stocks")
def api_stocks():
    # 可選：?watchlist=群組名 → 只回該清單內的股票
    watchlist_name = (request.args.get("watchlist") or "").strip()
    watchlist_ids: set | None = None
    watchlist_total = None
    if watchlist_name:
        if not validate_watchlist_name(watchlist_name):
            abort(400)
        if not watchlist_path(watchlist_name).exists():
            abort(404)
        watchlist_ids = set(read_watchlist_stocks(watchlist_name))
        watchlist_total = len(watchlist_ids)

    root = SCRIPT_DIR / LOG_ROOT_DIR
    if not root.exists():
        return jsonify({
            "last_updated": None,
            "recent_days": RECENT_DAYS,
            "stocks": [],
            "watchlist": watchlist_name or None,
            "watchlist_total": watchlist_total,
        })

    latest_closes = load_latest_closes()
    market_map = load_market_map()

    # 預先準備 stock_id → stock_name 對照（tw_stock_list.csv）
    name_map = load_stock_name_map()

    stocks = []
    seen_ids: set = set()
    latest_mtime = 0.0
    for f in sorted(root.glob("*.json")):
        parsed = parse_stock_file(f)
        if not parsed:
            continue

        # 有 watchlist 過濾時，非清單內的股票跳過
        if watchlist_ids is not None and parsed["stock_id"] not in watchlist_ids:
            continue

        # 標註上市 / 上櫃
        parsed["market"] = market_map.get(parsed["stock_id"]) or None
        parsed["has_target_price"] = True

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
        seen_ids.add(parsed["stock_id"])

        try:
            mt = f.stat().st_mtime
            if mt > latest_mtime:
                latest_mtime = mt
        except OSError:
            pass

    # Watchlist 模式：清單中但還沒抓過資料的股票 → 補上空白 placeholder
    if watchlist_ids is not None:
        for sid in sorted(watchlist_ids):
            if sid in seen_ids:
                continue
            stocks.append(_empty_stock_row(
                sid,
                name_map.get(sid, ""),
                market_map.get(sid),
                latest_closes.get(sid),
            ))

    last_updated = (
        datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M")
        if latest_mtime else None
    )

    return jsonify({
        "last_updated": last_updated,
        "recent_days": RECENT_DAYS,
        "stocks": stocks,
        "watchlist": watchlist_name or None,
        "watchlist_total": watchlist_total,
    })


# ----- 自選股 CRUD -----

@app.route("/api/watchlists", methods=["GET"])
def api_watchlists_list():
    """列出所有自選股清單。"""
    return jsonify({"watchlists": list_watchlists()})


@app.route("/api/watchlists", methods=["POST"])
def api_watchlists_create():
    """新增清單。body 可帶 {"name": "..."}，沒給就自動找「自選股N」最小未使用編號。"""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()

    if name:
        if not validate_watchlist_name(name):
            return jsonify({"ok": False, "error": "invalid_name"}), 400
    else:
        name = next_default_watchlist_name()

    path = watchlist_path(name)
    if path.exists():
        return jsonify({"ok": False, "error": "name_exists"}), 409

    try:
        path.touch()
    except OSError as e:
        return jsonify({"ok": False, "error": f"write_failed: {e}"}), 500

    return jsonify({"ok": True, "name": name, "count": 0})


@app.route("/api/watchlists/<name>", methods=["PATCH"])
def api_watchlists_rename(name: str):
    """改名。body {"new_name": "..."}。"""
    if not validate_watchlist_name(name):
        abort(400)
    old_path = watchlist_path(name)
    if not old_path.exists():
        abort(404)

    body = request.get_json(silent=True) or {}
    new_name = (body.get("new_name") or "").strip()
    if not validate_watchlist_name(new_name):
        return jsonify({"ok": False, "error": "invalid_new_name"}), 400
    if new_name == name:
        return jsonify({"ok": True, "name": new_name})

    new_path = watchlist_path(new_name)
    if new_path.exists():
        return jsonify({"ok": False, "error": "name_exists"}), 409

    try:
        old_path.rename(new_path)
    except OSError as e:
        return jsonify({"ok": False, "error": f"rename_failed: {e}"}), 500

    return jsonify({"ok": True, "name": new_name})


@app.route("/api/watchlists/<name>", methods=["DELETE"])
def api_watchlists_delete(name: str):
    """刪除清單（會刪掉對應的 stocklist_{name}.txt 檔）。"""
    if not validate_watchlist_name(name):
        abort(400)
    path = watchlist_path(name)
    if not path.exists():
        abort(404)

    try:
        path.unlink()
    except OSError as e:
        return jsonify({"ok": False, "error": f"delete_failed: {e}"}), 500

    return jsonify({"ok": True, "name": name})


@app.route("/api/kline")
def api_kline():
    """單檔日 K 線資料。
    若資料不存在或超過 4 小時未更新，則自動觸發後端重抓。
    """
    from flask import request
    code = (request.args.get("code") or "").strip()
    if not code or len(code) > 10 or not code.isalnum():
        abort(400)

    path = SCRIPT_DIR / KLINE_DIR / f"{code}.json"
    needs_fetch = False

    if not path.exists():
        needs_fetch = True
    else:
        # 檢查更新時間與資料長度
        try:
            mtime = path.stat().st_mtime
            # 若超過 4 小時未更新，則更新
            if (time.time() - mtime) > 4 * 3600:
                needs_fetch = True
            else:
                # 若更新時間尚早，但資料長度不足 22 個月 (約 2 年)，也補抓
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entries = data.get("entries") or []
                if entries:
                    first_date_str = entries[0].get("date")
                    if first_date_str:
                        first_dt = datetime.strptime(first_date_str, "%Y%m%d")
                        # 差距小於 660 天 (約 22 個月)
                        if (datetime.now() - first_dt).days < 660:
                            needs_fetch = True
                else:
                    needs_fetch = True
        except Exception:
            needs_fetch = True

    if needs_fetch:
        print(f"[資訊] {code} K 線資料不全或過舊，啟動背景補抓...")
        # 這裡用一個簡單的鎖防止重複觸發同一個代號
        # 但為了 UX，我們在 api_kline 內部直接跑一次重抓（同步或非同步？）
        # 若是同步，使用者會等 1-2 秒；若是非同步，使用者第一次會看到舊資料或 404。
        # 考慮到 FinMind 很快，同步抓取 2 年資料約 1-2 秒，體驗尚可。
        months = _compute_existing_kline_months(code, default_months=24)
        _run_fetch_subprocess(
            "fetch_daily_kline.py",
            ["--bootstrap", "--months", str(months), "--stock", code],
        )

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


def _compute_existing_kline_months(code: str, default_months: int = 24) -> int:
    """讀現有 kline JSON，回傳從最早一筆到今天的月數（至少 24、最多 60）。

    下限 24：重抓時至少回補 2 年，符合使用者需求。
    """
    MIN_MONTHS = 24
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
