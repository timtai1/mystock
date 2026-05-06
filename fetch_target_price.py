# -*- coding: utf-8 -*-
"""
法人目標價撈取腳本
每天排程執行，掃描目錄下所有 stocklist_*.txt（自選股清單）中的股票代號，
以 set 去重後逐一撈取法人目標價資料。
Response 會儲存為 JSON 檔案，放在 法人目標價_log_file/{stock_id}_{name}.json。
每次執行會覆蓋舊檔；CMoney 每次回傳即為近 90 天完整快照，無需保留歷史日資料夾。
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
import urllib3

# ============================================================
# 設定區（可依需求修改）
# ============================================================

# Authorization Bearer Token（優先從 credential.txt 登入取得，若無則回退到環境變數）
# credential.txt 格式：
#   account=+886...
#   hashed_password=...
CMONEY_AUTH_TOKEN = os.environ.get("CMONEY_AUTH_TOKEN", "").strip()

# 每檔股票之間的間隔時間（毫秒）
INTERVAL_MS = 1000

# 自選股清單檔案的 glob pattern（一個群組一個檔，檔名為 stocklist_{群組名}.txt）
STOCKLIST_GLOB = "stocklist_*.txt"

# 輸出的 Log 根目錄
LOG_ROOT_DIR = "法人目標價_log_file"

# API 設定
API_URL = "https://dtno.cmoney.tw/app/v2/dtno/MobileCsv"
DTNO = "8459549"

# Request 逾時時間（秒）
REQUEST_TIMEOUT = 30

# SSL 憑證驗證設定
#   True  : 正常驗證（預設推薦）
#   False : 關閉驗證（公司網路 MITM 憑證替換時可暫時用，會有安全警告）
#   "/path/to/ca-bundle.pem" : 指定自訂 CA bundle（例如公司 Root CA 的 .pem 檔）
VERIFY_SSL = False

# Response 中「股票名稱」欄位的索引（依 titles 中的順序，從 0 開始）
STOCK_NAME_INDEX = 19

# 台股上市/上櫃完整清單，用來對照股票代號→名稱（避免 unknown）
TW_STOCK_LIST_FILE = "tw_stock_list.csv"
TW_STOCK_LIST_MAX_AGE_DAYS = 7
TWSE_LIST_URLS = {
    "上市": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2",
    "上櫃": "https://isin.twse.com.tw/isin/C_public.jsp?strMode=4",
}

# ============================================================
# 以下是程式邏輯，一般不需要修改
# ============================================================

SCRIPT_DIR = Path(__file__).resolve().parent

# 登入 API 設定
LOGIN_URL = "https://api.cmoney.tw/identity/token"
CREDENTIAL_FILE = "credential.txt"

CMONEY_TRACE_CONTEXT = json.dumps({
    "appId": 2,
    "osVersion": "26.3.1",
    "appVersion": "10.124.0",
    "manufacturer": "Apple",
    "model": "iPhone16,1",
    "osName": "iOS",
    "platform": 1,
}, separators=(",", ":"))


def get_cmoney_token() -> str:
    """從環境變數或 credential.txt 讀取帳密並登入 CMoney 取得 access_token"""
    account = os.environ.get("CMONEY_ACCOUNT", "").strip()
    hashed_password = os.environ.get("CMONEY_PASSWORD", "").strip()

    if not account or not hashed_password:
        cred_path = SCRIPT_DIR / CREDENTIAL_FILE
        if cred_path.exists():
            try:
                with cred_path.open("r", encoding="utf-8") as f:
                    for line in f:
                        if "=" in line:
                            key, val = line.strip().split("=", 1)
                            if key == "account":
                                account = val
                            elif key == "hashed_password":
                                hashed_password = val
            except Exception as e:
                print(f"[警告] 讀取 {CREDENTIAL_FILE} 失敗：{e}")
        
    if not account or not hashed_password:
        return ""

    print(f"[資訊] 正在登入 CMoney (帳號: {account}) ...")
    
    headers = {
        "Cmoneyapi-Trace-Context": CMONEY_TRACE_CONTEXT,
        "X-Cmapi-Trace-Context": json.dumps({"Platform": 1, "AppId": 2, "Mode": 3}, separators=(",", ":")),
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "User-Agent": "ChipK/10.124.0.260416.0 CFNetwork/3860.400.51 Darwin/25.3.0",
        "Accept": "*/*",
    }
    
    payload = {
        "grant_type": "password",
        "account": account,
        "hashed_password": hashed_password,
        "client_id": "cmchipkmobile",
        "login_method": "cellphone",
    }
    
    try:
        resp = requests.post(
            LOGIN_URL,
            headers=headers,
            data=payload,
            timeout=REQUEST_TIMEOUT,
            verify=VERIFY_SSL
        )
        if resp.status_code == 200:
            data = resp.json()
            token = data.get("access_token")
            if token:
                print("[成功] 登入成功，已取得新 Token。")
                return token
            else:
                print("[失敗] 登入回應中未包含 access_token")
        else:
            print(f"[失敗] 登入失敗 HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[錯誤] 登入過程發生異常：{e}")
        
    return ""


def build_headers():
    return {
        "Host": "dtno.cmoney.tw",
        "Cookie": "cm_kl=1",
        "Content-Type": "application/json",
        "Cmoneyapi-Trace-Context": CMONEY_TRACE_CONTEXT,
        "Accept": "*/*",
        "Authorization": f"Bearer {CMONEY_AUTH_TOKEN}",
        "Accept-Language": "zh-TW,zh-Hant;q=0.9",
        # 故意不接受 br (Brotli)，因為 Python requests 沒內建 Brotli 解壓。
        # 只留 gzip, deflate 讓 requests 自動解壓。
        "Accept-Encoding": "gzip, deflate",
        "User-Agent": "ChipK/10.124.0.260416.0 CFNetwork/3860.400.51 Darwin/25.3.0",
    }


def build_payload(stock_id: str) -> dict:
    """組合 POST payload，把 AssignID 換成指定股票代號"""
    params = f"AssignID={stock_id};DTMode=0;DTRange=350;DTOrder=1;MajorTable=M605;MTPeriod=0;"
    return {
        "AssignSpid": "",
        "Ftno": "0",
        "KeyMap": "",
        "Params": params,
        "Dtno": DTNO,
    }


def extract_stock_name(response_json: dict) -> str:
    """從 response 中取出股票名稱。若抓不到則回傳 'unknown'"""
    try:
        data = response_json.get("data") or []
        if not data:
            return "unknown"
        # 優先用 titles 找出「股票名稱:期底」的 index，抓不到則用預設 index
        titles = response_json.get("titles") or []
        idx = STOCK_NAME_INDEX
        for i, t in enumerate(titles):
            if t and "股票名稱" in t:
                idx = i
                break
        first_row = data[0]
        if idx < len(first_row):
            name = str(first_row[idx]).strip()
            return name if name else "unknown"
    except Exception:
        pass
    return "unknown"


def sanitize_filename(name: str) -> str:
    """清掉檔名不允許的字元"""
    bad = '<>:"/\\|?*'
    for ch in bad:
        name = name.replace(ch, "_")
    return name.strip()


def _parse_twse_html(html_text: str, market: str):
    """從 TWSE ISIN 頁面 HTML 解析出 (code, name, market) list。
    每個 <tr> 的第一個 <td> 內容類似 '1101　台泥'，以全形空白 (U+3000) 分隔。
    部分 <tr> 只有 1 個 <td>（區塊標題），要過濾掉。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("    [錯誤] 未安裝 beautifulsoup4，請執行：pip install beautifulsoup4")
        return []

    soup = BeautifulSoup(html_text, "html.parser")
    results = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 2:
            continue  # 區塊標題或表頭
        first = tds[0].get_text(strip=True)
        if not first:
            continue
        # 將全形空白視為分隔符
        normalized = first.replace("\u3000", " ")
        parts = re.split(r"\s+", normalized, maxsplit=1)
        if len(parts) != 2:
            continue
        code, name = parts[0].strip(), parts[1].strip()
        if not code or not name:
            continue
        results.append({"code": code, "name": name, "market": market})
    return results


def ensure_stock_name_list(force: bool = False) -> Path | None:
    """檢查 tw_stock_list.csv，若不存在或超過 N 天未更新就重抓並寫入。
    回傳 CSV 的路徑（失敗時若原檔存在仍回傳原路徑）。"""
    csv_path = SCRIPT_DIR / TW_STOCK_LIST_FILE

    if not force and csv_path.exists():
        mtime = datetime.fromtimestamp(csv_path.stat().st_mtime)
        age_seconds = (datetime.now() - mtime).total_seconds()
        age_days = age_seconds / 86400.0
        if age_seconds <= TW_STOCK_LIST_MAX_AGE_DAYS * 86400:
            print(f"[資訊] 使用現有股票清單：{csv_path.name}"
                  f"（{age_days:.1f} 天前更新，未超過 {TW_STOCK_LIST_MAX_AGE_DAYS} 天）")
            return csv_path
        print(f"[資訊] 股票清單已 {age_days:.1f} 天未更新，重新抓取...")
    else:
        print(f"[資訊] 建立股票清單：{csv_path.name}")

    entries = []
    for market, url in TWSE_LIST_URLS.items():
        try:
            resp = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                verify=VERIFY_SSL,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            # TWSE 回傳 Big5 編碼；讓 requests 依 Content-Type 解，不然強制 Big5
            if resp.encoding is None or resp.encoding.lower() in ("iso-8859-1",):
                resp.encoding = "big5"
            if resp.status_code != 200:
                print(f"    [警告] {market}清單下載失敗: HTTP {resp.status_code}")
                continue
            rows = _parse_twse_html(resp.text, market)
            print(f"    {market}：解析出 {len(rows)} 筆")
            entries.extend(rows)
        except Exception as e:
            print(f"    [警告] {market}清單下載失敗: {e}")

    if not entries:
        print(f"[警告] 未能取得任何股票清單；"
              f"{'沿用舊檔' if csv_path.exists() else '後續查不到名稱的股票會使用 unknown'}")
        return csv_path if csv_path.exists() else None

    # 以 code 為 key 去重（上市/上櫃理論上不會重號，但保險起見）
    seen = {}
    for e in entries:
        if e["code"] not in seen:
            seen[e["code"]] = e

    try:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["code", "name", "market"])
            for code in sorted(seen.keys()):
                e = seen[code]
                writer.writerow([e["code"], e["name"], e["market"]])
        print(f"[資訊] 已寫入 {csv_path.name}：共 {len(seen)} 筆")
    except OSError as e:
        print(f"[警告] 寫入 {csv_path.name} 失敗：{e}")
        return None

    # 清快取，下次查詢重新載入
    global _stock_name_cache
    _stock_name_cache = None

    return csv_path


_stock_name_cache = None


def lookup_stock_name(stock_id: str) -> str:
    """從 tw_stock_list.csv 以股票代號查名稱；查不到回傳 'unknown'。"""
    global _stock_name_cache
    if _stock_name_cache is None:
        csv_path = SCRIPT_DIR / TW_STOCK_LIST_FILE
        m = {}
        if csv_path.exists():
            try:
                with open(csv_path, "r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        code = (row.get("code") or "").strip()
                        name = (row.get("name") or "").strip()
                        if code and name:
                            m[code] = name
            except Exception as e:
                print(f"    [警告] 讀取 {csv_path.name} 失敗：{e}")
        _stock_name_cache = m
    return _stock_name_cache.get(str(stock_id).strip(), "unknown")


def _fetch_one_attempt(stock_id: str, session: requests.Session):
    """單次 POST 請求，回傳 (ok, response_json_or_error_dict, stock_name)"""
    payload = build_payload(stock_id)
    headers = build_headers()
    try:
        resp = session.post(
            API_URL,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=REQUEST_TIMEOUT,
            verify=VERIFY_SSL,
        )
    except requests.RequestException as e:
        return False, {"error": f"RequestException: {e}"}, "unknown"

    if resp.status_code != 200:
        # 印出失敗細節到 console
        print()
        print(f"    [失敗] HTTP {resp.status_code} {resp.reason}")
        print(f"    URL: {resp.url}")
        print(f"    Response Headers:")
        for k, v in resp.headers.items():
            print(f"      {k}: {v}")
        body_preview = resp.text[:500] if resp.text else ""
        if body_preview:
            print(f"    Response Body (前 500 字): {body_preview}")
        return False, {
            "error": f"HTTP {resp.status_code} {resp.reason}",
            "http_status": resp.status_code,
            "http_reason": resp.reason,
            "url": resp.url,
            "response_headers": dict(resp.headers),
            "body": resp.text,
        }, "unknown"

    try:
        data = resp.json()
    except ValueError:
        print()
        print(f"    [失敗] Invalid JSON (HTTP {resp.status_code})")
        print(f"    Response Headers:")
        for k, v in resp.headers.items():
            print(f"      {k}: {v}")
        body_preview = resp.text[:500] if resp.text else ""
        if body_preview:
            print(f"    Response Body (前 500 字): {body_preview}")
        return False, {
            "error": "Invalid JSON",
            "http_status": resp.status_code,
            "http_reason": resp.reason,
            "response_headers": dict(resp.headers),
            "body": resp.text,
        }, "unknown"

    stock_name = extract_stock_name(data)
    return True, data, stock_name


def fetch_one(stock_id: str, session: requests.Session):
    """抓取單一股票資料，回傳 (ok, response_json_or_text, stock_name)。
    若 data 陣列為空，會等 1 秒重試一次；若仍然為空則從 Yahoo Finance 取得股票名稱。"""
    ok, data, stock_name = _fetch_one_attempt(stock_id, session)

    def _rows(d):
        if isinstance(d, dict):
            return d.get("data") or []
        return []

    if ok and len(_rows(data)) == 0:
        print()
        print(f"    [提示] 回傳資料為空，1 秒後重試...")
        time.sleep(1.0)
        ok2, data2, stock_name2 = _fetch_one_attempt(stock_id, session)
        if ok2:
            if len(_rows(data2)) > 0:
                return True, data2, stock_name2
            # 仍然為空 → 改用 TWSE 清單對照
            fallback_name = lookup_stock_name(stock_id)
            if fallback_name != "unknown":
                print(f"    [提示] 重試後仍無資料，從 TWSE 清單取得名稱：{fallback_name}")
            else:
                print(f"    [提示] 重試後仍無資料，TWSE 清單也查不到，使用 unknown。")
            return True, data2, fallback_name
        # 重試本身失敗，回傳重試的失敗內容
        return ok2, data2, stock_name2

    return ok, data, stock_name


def main():
    script_dir = SCRIPT_DIR
    global CMONEY_AUTH_TOKEN

    parser = argparse.ArgumentParser(
        description="法人目標價撈取（每日排程用，或以 --stock 重抓單檔）"
    )
    parser.add_argument(
        "--stock", "-s", dest="single_stock", default=None,
        help="只重抓單檔股票代號（會略過清單掃描）"
    )
    args = parser.parse_args()

    # 嘗試從 credential.txt 登入取得 Token
    token_from_login = get_cmoney_token()
    if token_from_login:
        CMONEY_AUTH_TOKEN = token_from_login

    # 檢查 CMoney Authorization Token 是否設定
    if not CMONEY_AUTH_TOKEN:
        print(
            f"[錯誤] 無法取得登入憑證且環境變數 CMONEY_AUTH_TOKEN 未設定。\n"
            f"       請確保 {CREDENTIAL_FILE} 存在且正確，或設定環境變數。",
            file=sys.stderr,
        )
        sys.exit(2)

    # 若關閉 SSL 驗證，抑制警告訊息以免洗版
    if VERIFY_SSL is False:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        print("[警告] SSL 憑證驗證已關閉（VERIFY_SSL = False）")

    # 確保 TWSE 股票清單存在且未過期（每 N 天重抓一次）
    ensure_stock_name_list()

    if args.single_stock:
        stock_ids = [args.single_stock.strip()]
        print(f"[資訊] 單檔重抓模式：{stock_ids[0]}")
    else:
        stocklist_paths = sorted(script_dir.glob(STOCKLIST_GLOB))
        if not stocklist_paths:
            print(
                f"[錯誤] 找不到任何股票清單檔（pattern：{STOCKLIST_GLOB}）。\n"
                f"       請至少建立一份，例如 stocklist_自選股1.txt。",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"[資訊] 掃描到 {len(stocklist_paths)} 份自選股清單：")
        # 跨清單用 set 去重；同一檔股票只會 fetch 一次
        stock_ids_set: set[str] = set()
        for p in stocklist_paths:
            count_before = len(stock_ids_set)
            with p.open("r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    stock_ids_set.add(s)
            print(f"    {p.name}（去重後新增 {len(stock_ids_set) - count_before} 檔）")

        # 維持字串排序，輸出順序可預測
        stock_ids = sorted(stock_ids_set)
        print(f"[資訊] 去重後總共需撈取 {len(stock_ids)} 檔。")

    if not stock_ids:
        print("[警告] 股票清單是空的，結束執行。")
        return

    out_dir = script_dir / LOG_ROOT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[資訊] 開始撈取，共 {len(stock_ids)} 檔，輸出資料夾：{out_dir}")

    session = requests.Session()
    interval_sec = INTERVAL_MS / 1000.0

    ok_count = 0
    fail_count = 0

    for idx, stock_id in enumerate(stock_ids, start=1):
        print(f"[{idx}/{len(stock_ids)}] 撈取 {stock_id} ...", end=" ", flush=True)
        ok, data, stock_name = fetch_one(stock_id, session)

        # HTTP 401 → 幾乎都是 CMoney Bearer Token 過期；立刻中止整個流程，
        # 避免繼續打 API 把 log 洗滿,也不要寫出空殼錯誤檔覆蓋原本好的資料。
        if not ok and isinstance(data, dict) and data.get("http_status") == 401:
            print(f"失敗 -> HTTP 401 Unauthorized")
            print(
                "\n[中止] CMoney 回傳 HTTP 401 Unauthorized，\n"
                f"       登入 Token 已失效。請檢查 {CREDENTIAL_FILE} 是否正確，\n"
                "       並重新執行本腳本以重新登入取得 Token。",
                file=sys.stderr,
            )
            sys.exit(3)

        safe_name = sanitize_filename(stock_name) if stock_name else "unknown"
        filename = f"{stock_id}_{safe_name}.json"
        out_path = out_dir / filename

        # 寫新檔前，先清掉同 stock_id 開頭的舊檔（處理股票改名的情境）
        for old in out_dir.glob(f"{stock_id}_*.json"):
            if old.name != filename:
                try:
                    old.unlink()
                except OSError:
                    pass

        try:
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print(f"寫檔失敗：{e}")
            fail_count += 1
        else:
            if ok:
                print(f"OK ({stock_name}) -> {filename}")
                ok_count += 1
            else:
                print(f"失敗 -> {filename}")
                fail_count += 1

        # 最後一檔後不用再等待
        if idx < len(stock_ids):
            time.sleep(interval_sec)

    print(f"[完成] 成功 {ok_count} 檔，失敗 {fail_count} 檔。")


if __name__ == "__main__":
    main()
