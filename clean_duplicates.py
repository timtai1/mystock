# -*- coding: utf-8 -*-
"""
清理日 K 線資料中的重複項。
若連續兩天的開高低收與成交量完全相同，則視為休市期間的重複快照，並刪除較晚的那一筆。
"""

import json
import os
from pathlib import Path
from datetime import datetime

KLINE_DIR = Path("日K線_log_file")

def clean_file(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"讀取 {file_path.name} 失敗: {e}")
        return 0

    if not isinstance(data, dict) or "entries" not in data:
        return 0

    entries = data.get("entries", [])
    if not entries:
        return 0

    new_entries = []
    removed_count = 0
    
    # 總是保留第一筆
    new_entries.append(entries[0])
    
    for i in range(1, len(entries)):
        curr = entries[i]
        prev = new_entries[-1]
        
        # 檢查 OHLCV 是否完全相同
        is_duplicate = (
            curr.get("open") == prev.get("open") and
            curr.get("high") == prev.get("high") and
            curr.get("low") == prev.get("low") and
            curr.get("close") == prev.get("close") and
            curr.get("volume") == prev.get("volume")
        )
        
        if is_duplicate:
            # 如果資料相同，再檢查日期
            # 即使是補盤日，OHLCV 完全相同的機率也極低
            # 這裡我們採取較嚴格的清理：只要資料一模一樣就視為重複快照
            removed_count += 1
            continue
        else:
            new_entries.append(curr)

    if removed_count > 0:
        data["entries"] = new_entries
        data["last_updated"] = datetime.now().strftime("%Y%m%d%H%M%S")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        
    return removed_count

def main():
    if not KLINE_DIR.exists():
        print(f"目錄 {KLINE_DIR} 不存在")
        return

    total_removed = 0
    files_processed = 0
    files_changed = 0

    for f in KLINE_DIR.glob("*.json"):
        files_processed += 1
        removed = clean_file(f)
        if removed > 0:
            total_removed += removed
            files_changed += 1
            print(f"  {f.name}: 移除了 {removed} 筆重複資料")

    print(f"\n完成！")
    print(f"掃描檔案數: {files_processed}")
    print(f"修正檔案數: {files_changed}")
    print(f"總共移除筆數: {total_removed}")

if __name__ == "__main__":
    main()
