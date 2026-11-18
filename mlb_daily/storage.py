"""metadata 與戰報檔案的讀寫。"""

import json
import os
import sys


def write_report(date_str, html_content):
    year, month, _ = date_str.split("-")
    output_dir = os.path.join("reports", year, month)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{date_str}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Successfully generated report: {path}")
    return path

def load_metadata(metadata_path):
    """讀取 metadata。不存在 -> 空 dict；讀取/解析失敗 -> 結束程式，不覆寫原檔。"""
    if not os.path.exists(metadata_path):
        return {}
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"Error: Could not read metadata.json ({e}). Aborting without overwriting.", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, dict):
        print("Error: metadata.json is not a JSON object. Aborting without overwriting.", file=sys.stderr)
        sys.exit(1)
    return data

def save_metadata(metadata_path, metadata_db):
    """先寫到同目錄暫存檔再 os.replace；失敗則結束程式。"""
    tmp_path = metadata_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(metadata_db, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, metadata_path)
    except Exception as e:
        print(f"Error saving metadata.json: {e}", file=sys.stderr)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        sys.exit(1)


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def entry_type(entry):
    """metadata 項目的頁面類型；舊資料缺 type 時以 total_games 推斷。"""
    t = entry.get("type")
    if t in ("season", "offseason"):
        return t
    return "season" if _to_int(entry.get("total_games")) > 0 else "offseason"


def write_json_atomic(path, data):
    """先寫暫存檔再 os.replace。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
