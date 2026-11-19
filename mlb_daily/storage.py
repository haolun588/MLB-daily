"""metadata 與戰報檔案的讀寫。"""

import json
import os
import sys

from .config import DAILY_SNAPSHOT_DIR, LEADERS_PATH, LEADERS_SCHEMA, PLAYOFFS_PATH, PLAYOFFS_SCHEMA, WEEKLY_DIR, WEEKLY_INDEX_PATH


def write_report(date_str, html_content):
    year, month, _ = date_str.split("-")
    output_dir = os.path.join("reports", year, month)
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{date_str}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Successfully generated report: {path}")
    return path

def write_weekly_report(week_label, html_content):
    """寫出 reports/weekly/YYYY-Www.html。"""
    os.makedirs(WEEKLY_DIR, exist_ok=True)
    path = os.path.join(WEEKLY_DIR, f"{week_label}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Successfully generated weekly report: {path}")
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


# ---------------------------------------------------------------- 每日資料快照與週報索引
def write_daily_snapshot(date_str, payload, directory=None):
    """寫出 data/daily/YYYY-MM-DD.json；失敗只印警告並回傳 None，不影響戰報。directory 預設 DAILY_SNAPSHOT_DIR。"""
    path = os.path.join(directory or DAILY_SNAPSHOT_DIR, f"{date_str}.json")
    try:
        write_json_atomic(path, payload)
    except (OSError, TypeError, ValueError) as e:
        print(f"Warning: 無法寫入每日資料快照 {path}（{type(e).__name__}: {e}）。", file=sys.stderr)
        return None
    return path


def load_weekly_index(path=None):
    """讀取 reports/weekly/index.json，回傳 {"weeks": [...]}。不存在回傳空清單；
    讀取失敗、格式不符只印警告並視為空（不中斷）。weeks 內非 dict 的項目略過。"""
    path = path or WEEKLY_INDEX_PATH
    if not os.path.exists(path):
        return {"weeks": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        weeks = data["weeks"]
        if not isinstance(weeks, list):
            raise ValueError("weeks 不是陣列")
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"Warning: 無法讀取週報索引 {path}（{type(e).__name__}: {e}），視為空索引。", file=sys.stderr)
        return {"weeks": []}
    return {"weeks": [w for w in weeks if isinstance(w, dict) and isinstance(w.get("week"), str)]}


def latest_weekly_entry(path=None):
    """索引中最新一週（週次字串最大者）的項目；沒有回傳 None。"""
    weeks = load_weekly_index(path)["weeks"]
    return max(weeks, key=lambda w: w["week"]) if weeks else None


def save_weekly_index(entry, path=None):
    """把一週的項目寫進索引：依週次去重（同週覆蓋）、新的在前；回傳完整索引。"""
    path = path or WEEKLY_INDEX_PATH
    index = load_weekly_index(path)
    weeks = [w for w in index["weeks"] if w["week"] != entry["week"]] + [entry]
    weeks.sort(key=lambda w: w["week"], reverse=True)
    index = {"weeks": weeks}
    write_json_atomic(path, index)
    return index


# ---------------------------------------------------------------- 數據排行榜
def load_leaders(path=None):
    """讀取 data/leaders.json；不存在、損壞或 schema 不符一律回傳 None（首頁不顯示小卡）。"""
    try:
        with open(path or LEADERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != LEADERS_SCHEMA or not isinstance(data.get("categories"), dict):
        return None
    season = data.get("season")
    if isinstance(season, bool) or not isinstance(season, int):
        return None
    return data


# ---------------------------------------------------------------- 季後賽對戰樹
def load_playoffs(path=None):
    """讀取 data/playoffs.json；不存在、損壞或 schema 不符一律回傳 None（首頁不顯示小卡）。"""
    try:
        with open(path or PLAYOFFS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != PLAYOFFS_SCHEMA or not isinstance(data.get("rounds"), dict):
        return None
    season = data.get("season")
    if isinstance(season, bool) or not isinstance(season, int):
        return None
    return data
