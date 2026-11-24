"""漏跑日自動補產、前一天較晚登錄的人事異動回填、週報補產。

主排程每天只產生「昨日」（D）的戰報；若某天 workflow 沒跑成功，之後該日會永久缺漏。--backfill 會往前檢查
最近 N 天（today-2 … today-(N+1)，D 由主步驟負責）並補產缺的日子。補產一律不發 Discord 通知。

refresh_snapshot_transactions 只回填「每日快照」的人事異動，刻意不重產 D-1 的頁面：重產頁面得重抓 boxscore
並重翻全部內容，成本與風險都高，也可能讓已發布頁面的譯文改變；而快照是休賽季週報的來源，補進快照即可讓
週報看到較晚登錄的異動。"""

import json
import os
import sys
from datetime import timedelta

from . import games as games_mod, index_page, net, report, rosters, season, storage, transactions as transactions_mod, translate, weekly
from .config import BACKFILL_DAYS, DAILY_SNAPSHOT_DIR, DAILY_SNAPSHOT_SCHEMA, DATE_KEY_RE, METADATA_PATH, WEEKLY_BACKFILL_WEEKS



def generate_no_game_report(date_str):
    """無賽事日：依 season.classify_no_game_day 產生休兵日或休賽季快報，回傳 (metadata, 標籤)。
    cli.main 與補產共用同一套判斷；不更新首頁、不發通知（由呼叫端決定）。"""
    kind, reason = season.classify_no_game_day(date_str)
    if kind == "rest_day":
        print(f"No games scheduled or found on {date_str}. Generating rest day report ({reason}).")
        return report.generate_rest_day_report(date_str, reason), "休兵日快報"
    print(f"No games scheduled or found on {date_str}. Generating offseason report.")
    return report.generate_offseason_report(date_str), "休賽季快報"


# ---------------------------------------------------------------- D-1 人事異動回填
def _tx_key(tx):
    return (tx.get("type_code"), tx.get("date"), tx.get("description"))


def refresh_snapshot_transactions(date_str):
    """重抓 date_str 的人事異動，把快照（data/daily/{date}.json）裡沒有的項目新增進去，回傳新增筆數。

    既有項目（含譯文）完全不改、不刪；快照不存在或格式不符、重抓結果為空（API 失敗也回空，無法區分）都不動檔案。
    新項目有 GEMINI_API_KEY 才翻譯，否則 description_zh 為 None。has_games、news 保持原值。"""
    path = os.path.join(DAILY_SNAPSHOT_DIR, f"{date_str}.json")
    if not os.path.exists(path):
        print(f"{date_str} 沒有每日快照，略過人事異動回填。")
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError) as e:
        print(f"Warning: 每日快照 {path} 讀取失敗（{type(e).__name__}: {e}），略過人事異動回填。", file=sys.stderr)
        return 0
    if (not isinstance(snap, dict) or snap.get("schema") != DAILY_SNAPSHOT_SCHEMA or snap.get("date") != date_str
            or not isinstance(snap.get("transactions"), list)):
        print(f"Warning: 每日快照 {path} 格式不符，略過人事異動回填。", file=sys.stderr)
        return 0
    fetched = transactions_mod.filter_major_league_transactions(transactions_mod.fetch_raw_transactions(date_str))
    if not fetched:
        print(f"{date_str} 重抓人事異動無結果，快照不變。")
        return 0
    seen = {_tx_key(t) for t in snap["transactions"] if isinstance(t, dict)}
    new_items = []
    for tx in fetched:
        key = _tx_key(tx)
        if key not in seen:
            seen.add(key)
            new_items.append(tx)
    if not new_items:
        print(f"{date_str} 快照已包含全部人事異動，無新增。")
        return 0
    try:
        translate.apply_translations(None, [], new_items)
    except Exception as e:  # noqa: BLE001 - 翻譯失敗不應阻擋回填，description_zh 維持 None
        print(f"Warning: 新增人事異動翻譯失敗（{type(e).__name__}: {e}），以原文寫入。", file=sys.stderr)
    snap["transactions"].extend(report.snapshot_transaction(tx) for tx in new_items)
    if storage.write_daily_snapshot(date_str, snap) is None:
        return 0
    print(f"{date_str} 快照回填 {len(new_items)} 筆較晚登錄的人事異動。")
    return len(new_items)


# ---------------------------------------------------------------- 漏跑日補產
def missing_dates(today, days, metadata):
    """要補產的日期（由舊到新）：today-2 … today-(days+1)，晚於 metadata 最早日期且 metadata 沒有該日。
    metadata 沒有任何日期 key 時回傳 []。"""
    keys = sorted(k for k in metadata if isinstance(k, str) and DATE_KEY_RE.match(k))
    if not keys:
        return []
    earliest = keys[0]
    out = []
    for back in range(days + 1, 1, -1):
        d = (today - timedelta(days=back)).isoformat()
        if d > earliest and d not in metadata:
            out.append(d)
    return out


def _backfill_one_day(date_str):
    """補產單日戰報並更新首頁（不發通知）。"""
    _, games = games_mod.check_all_games_finalized(date_str)
    if games:
        meta = report.generate_report(date_str, games)
    else:
        meta, _ = generate_no_game_report(date_str)
    index_page.update_index_page(meta)


def run_backfill(today, days=BACKFILL_DAYS):
    """補產最近 days 天內缺漏的戰報，再回填 D-1 的人事異動；回傳結束碼（有任何日子失敗為 1）。"""
    if not os.path.exists(METADATA_PATH):
        print(f"找不到 {METADATA_PATH}，不補產。")
        return 0
    metadata = storage.load_metadata(METADATA_PATH)
    targets = missing_dates(today, days, metadata)
    done, failed = [], []
    for date_str in targets:
        print(f"補產 {date_str} 的戰報…")
        try:
            _backfill_one_day(date_str)
            done.append(date_str)
        except net.ApiError as e:
            print(f"Warning: {date_str} 取得賽程失敗（{e}），略過。", file=sys.stderr)
            failed.append(date_str)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - 單日失敗（含 sys.exit）不得中斷其他日子
            print(f"Warning: {date_str} 補產失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
            failed.append(date_str)
    prev_day = (today - timedelta(days=2)).isoformat()
    added = 0
    if prev_day in done:
        print(f"{prev_day} 本次剛補產，略過人事異動回填。")
    else:
        try:
            added = refresh_snapshot_transactions(prev_day)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - 回填失敗不影響補產結果
            print(f"Warning: {prev_day} 人事異動回填失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
    done_text = "（" + "、".join(done) + "）" if done else ""
    failed_text = "（" + "、".join(failed) + "）" if failed else ""
    print(f"補產摘要：補產 {len(done)} 天{done_text}；失敗 {len(failed)} 天{failed_text}；"
          f"{prev_day} 快照新增人事異動 {added} 筆。")
    return 1 if failed else 0


# ---------------------------------------------------------------- 週報補產
def backfill_weekly_reports(today, weeks=WEEKLY_BACKFILL_WEEKS):
    """檢查最近 weeks 週（自上一個完整 ISO 週起往前）週報索引是否缺漏並補產，回傳補產的週次清單（舊到新）。

    只補晚於索引最早一週的週次（索引為空不補）；該週結束隔天（週一）屬賽季中（rosterType == "active"）不補。
    補產的週次只印 weekly_backfilled=<週次>，不印 weekly_week／weekly_url（那兩行會觸發 Discord 通知）。"""
    labels = []
    for w in storage.load_weekly_index()["weeks"]:
        try:
            weekly.parse_week(w["week"])
        except ValueError:
            continue
        labels.append(w["week"])
    if not labels:
        return []
    earliest, have = min(labels), set(labels)
    last_monday = weekly.parse_week(weekly.previous_week_label(today))
    candidates = [weekly.week_label(last_monday - timedelta(days=7 * i)) for i in range(weeks)]
    generated = []
    for label in reversed(candidates):
        if label in have or label <= earliest:
            continue
        try:
            ctx = rosters.resolve_roster_context(weekly.parse_week(label) + timedelta(days=7))
            if ctx.get("rosterType") == "active":
                print(f"{label} 屬賽季期間，不補產週報。")
                continue
            entry = weekly.generate_weekly_report(today, week=label, force=True)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - 單週失敗不影響其他週與正常流程
            print(f"Warning: 補產週報 {label} 失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
            continue
        if entry is not None:
            generated.append(label)
            print(f"weekly_backfilled={label}")
    return generated
