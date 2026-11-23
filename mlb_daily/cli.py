"""命令列入口與流程控制。"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta

from . import backfill, feed_cache, games as games_mod, index_page, leaders, net, notify, playoffs, report, rosters, season, storage, team_page, timeutil, watchlist, weekly
from .config import BACKFILL_DAYS, FEED_CACHE_PATH, MAX_WAIT_CHECKS, SEASON_CACHE_PATH, SITE_BASE_URL, WAIT_SLEEP_SECONDS


def _taipei_today(date_arg):
    """--date 當作「今天」覆寫（供手動與測試），否則為台北今天；格式錯誤結束程式。"""
    if date_arg:
        try:
            return datetime.strptime(date_arg, "%Y-%m-%d").date()
        except ValueError:
            print("Error: Date format must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    return timeutil.get_taipei_now().date()


def _run_weekly(args):
    """--weekly：產生週報、更新週報索引與首頁；成功時印出 weekly_week= 與 weekly_url= 供 workflow 讀取。
    --notify-weekly：依週報索引發 Discord 訊息（--week 指定週次，預設最新一週）。"""
    if args.notify_weekly:
        index = storage.load_weekly_index()["weeks"]
        if args.week:
            entry = next((w for w in index if w["week"] == args.week), None)
        else:
            entry = max(index, key=lambda w: w["week"]) if index else None
        if entry is None:
            print(f"Error: reports/weekly/index.json 中找不到{' ' + args.week if args.week else '任何週報'}，無法發送通知。", file=sys.stderr)
            sys.exit(1)
        notify.send_weekly_notification(entry)
        return
    today = _taipei_today(args.date)
    try:
        entry = weekly.generate_weekly_report(today, week=args.week, force=args.force)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if entry is not None:
        print(f"weekly_week={entry['week']}")
        print(f"weekly_url={SITE_BASE_URL}{entry['path']}")
    backfilled = []
    if not args.week:  # 補產最近幾週缺漏的週報（不印 weekly_week／weekly_url，不觸發通知）
        try:
            backfilled = backfill.backfill_weekly_reports(today)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - 補產失敗不影響正常週報流程
            print(f"Warning: 週報補產失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
    if (entry is not None or backfilled) and os.path.exists("reports/metadata.json"):  # 讓首頁立即出現最新週報連結；失敗不影響週報
        try:
            index_page.refresh_index_page()
        except (Exception, SystemExit) as e:  # noqa: BLE001 - metadata 損壞會 sys.exit；週報已產生，不應以非 0 結束
            print(f"Warning: 週報產生後重新產生首頁失敗（{type(e).__name__}: {e}）。", file=sys.stderr)


def _run_leaders(args):
    """--leaders：抓取數據排行榜，寫 data/leaders.json 與 leaders.html，再重新產生首頁；全部抓取失敗回傳非 0。"""
    today = _taipei_today(args.date)
    ctx = rosters.resolve_roster_context(today)
    as_of = today - timedelta(days=1)  # 資料截至「昨日」，與每日戰報預設日期一致
    try:
        regular_final = bool(season.regular_season_ended(ctx["season"], as_of))
    except Exception:  # noqa: BLE001 - 判斷失敗維持原本標題
        regular_final = False
    try:
        ok = leaders.generate_leaders(ctx["season"], ctx["rosterType"] == "active", as_of, regular_final=regular_final)
    except OSError as e:
        print(f"Error: 寫入數據排行榜失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        sys.exit(1)
    if not ok:
        sys.exit(1)
    if os.path.exists("reports/metadata.json"):  # 讓首頁小卡立即更新；失敗不影響排行榜
        try:
            index_page.refresh_index_page()
        except (Exception, SystemExit) as e:  # noqa: BLE001 - metadata 損壞會 sys.exit；排行榜已產生，不應以非 0 結束
            print(f"Warning: 排行榜產生後重新產生首頁失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
    else:
        print("Note: 找不到 reports/metadata.json，未重新產生首頁（首頁將於下次每日產生時帶入小卡）。")


def _run_watchlist(args):
    """--watchlist：從 MLB API 抓台日韓球員名單（可跨多個賽季），寫 data/watchlist.json；全部抓取失敗回傳非 0。"""
    today = _taipei_today(args.date)
    ctx = rosters.resolve_roster_context(today)
    dates = season.get_season_dates(today.year)
    season_end = dates[1] if dates else None
    seasons = watchlist.watchlist_seasons(today, ctx, season_end)
    try:
        ok = watchlist.generate_watchlist(seasons)
    except OSError as e:
        print(f"Error: 寫入追蹤名單失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        sys.exit(1)
    if not ok:
        sys.exit(1)


def _run_playoffs(args):
    """--playoffs：抓取季後賽系列賽，寫 data/playoffs.json 與 playoffs.html，再重新產生首頁；抓取失敗回傳非 0。"""
    today = _taipei_today(args.date)
    as_of = today - timedelta(days=1)  # 資料截至「昨日」，與每日戰報預設日期一致
    try:
        ok = playoffs.generate_playoffs(as_of)
    except OSError as e:
        print(f"Error: 寫入季後賽對戰樹失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
        sys.exit(1)
    if not ok:
        sys.exit(1)
    if os.path.exists("reports/metadata.json"):  # 讓首頁小卡立即更新；失敗不影響對戰樹
        try:
            index_page.refresh_index_page()
        except (Exception, SystemExit) as e:  # noqa: BLE001 - metadata 損壞會 sys.exit；對戰樹已產生，不應以非 0 結束
            print(f"Warning: 對戰樹產生後重新產生首頁失敗（{type(e).__name__}: {e}）。", file=sys.stderr)
    else:
        print("Note: 找不到 reports/metadata.json，未重新產生首頁（首頁將於下次每日產生時帶入小卡）。")


def main():
    parser = argparse.ArgumentParser(description="MLB Daily Report Generator Script")
    parser.add_argument("--date", type=str, help="Target report date in YYYY-MM-DD format (defaults to yesterday in Taipei Time)")
    parser.add_argument("--check-wait", action="store_true", help="If active, loops and sleeps 30 minutes until yesterday's games are finished")
    parser.add_argument("--max-wait-checks", type=int, default=MAX_WAIT_CHECKS, help=f"Max number of checks in the --check-wait loop (default {MAX_WAIT_CHECKS})")
    
    parser.add_argument("--rosters", action="store_true", help="Fetch all 30 team rosters with season stats and write data/teams/*.json (no report, no notification)")
    parser.add_argument("--no-notify", action="store_true", help="Generate report and metadata but do not send Discord notification")
    parser.add_argument("--notify-only", action="store_true", help="Only send Discord notification from existing metadata (no API calls, no file writes)")

    parser.add_argument("--weekly", action="store_true", help="Generate the offseason weekly summary (reports/weekly/YYYY-Www.html) from data/daily snapshots; only on Taipei Monday during the offseason unless --week/--force")
    parser.add_argument("--week", type=str, help="ISO week YYYY-Www for --weekly (manual backfill) or --notify-weekly")
    parser.add_argument("--force", action="store_true", help="With --weekly: skip the Monday and offseason checks")
    parser.add_argument("--notify-weekly", action="store_true", help="Send the weekly summary Discord message from reports/weekly/index.json (latest week, or --week)")

    parser.add_argument("--leaders", action="store_true", help="Fetch the stat leaderboards and write data/leaders.json and leaders.html, then refresh the homepage (no report, no notification)")

    parser.add_argument("--playoffs", action="store_true", help="Fetch the postseason bracket and write data/playoffs.json and playoffs.html, then refresh the homepage (no report, no notification)")

    parser.add_argument("--watchlist", action="store_true", help="Fetch Taiwan/Japan/Korea players from the MLB API (MLB: TW/JP/KR, minors: TW only) and write data/watchlist.json (no report, no notification)")

    parser.add_argument("--backfill", action="store_true", help="Generate missing reports for the last --backfill-days days (excluding yesterday) and refresh the day-before-yesterday snapshot transactions; never sends notifications")
    parser.add_argument("--backfill-days", type=int, default=None, help=f"With --backfill: how many days back to check (default {BACKFILL_DAYS}, must be >= 1)")

    args = parser.parse_args()
    if args.backfill_days is not None and not args.backfill:
        print("Error: --backfill-days requires --backfill", file=sys.stderr)
        sys.exit(1)
    if args.backfill and (args.rosters or args.notify_only or args.check_wait or args.no_notify or args.weekly or args.notify_weekly
                          or args.week or args.force or args.leaders or args.playoffs or args.watchlist):
        print("Error: --backfill cannot be combined with other modes or options (only --date and --backfill-days are allowed)", file=sys.stderr)
        sys.exit(1)
    if args.backfill and args.backfill_days is not None and args.backfill_days < 1:
        print("Error: --backfill-days must be >= 1", file=sys.stderr)
        sys.exit(1)
    if args.notify_only and (args.check_wait or args.no_notify):
        print("Error: --notify-only cannot be combined with --check-wait or --no-notify", file=sys.stderr)
        sys.exit(1)
    if args.rosters and (args.notify_only or args.check_wait):
        print("Error: --rosters cannot be combined with --notify-only or --check-wait", file=sys.stderr)
        sys.exit(1)
    weekly_mode = args.weekly or args.notify_weekly
    if args.weekly and args.notify_weekly:
        print("Error: --weekly and --notify-weekly cannot be combined", file=sys.stderr)
        sys.exit(1)
    if weekly_mode and (args.rosters or args.notify_only or args.check_wait or args.no_notify):
        print("Error: --weekly/--notify-weekly cannot be combined with --rosters, --notify-only, --check-wait or --no-notify", file=sys.stderr)
        sys.exit(1)
    if (args.week or args.force) and not weekly_mode:
        print("Error: --week and --force require --weekly (or --notify-weekly for --week)", file=sys.stderr)
        sys.exit(1)
    if args.force and not args.weekly:
        print("Error: --force requires --weekly", file=sys.stderr)
        sys.exit(1)
    if args.leaders and (args.rosters or args.notify_only or args.check_wait or args.no_notify or weekly_mode or args.week or args.force):
        print("Error: --leaders cannot be combined with other modes or options (only --date is allowed)", file=sys.stderr)
        sys.exit(1)
    if args.playoffs and (args.leaders or args.rosters or args.notify_only or args.check_wait or args.no_notify or weekly_mode or args.week or args.force):
        print("Error: --playoffs cannot be combined with other modes or options (only --date is allowed)", file=sys.stderr)
        sys.exit(1)
    if args.watchlist and (args.leaders or args.playoffs or args.rosters or args.notify_only or args.check_wait or args.no_notify or weekly_mode or args.week or args.force):
        print("Error: --watchlist cannot be combined with other modes or options (only --date is allowed)", file=sys.stderr)
        sys.exit(1)
    if args.max_wait_checks < 1:
        print("Error: --max-wait-checks must be >= 1", file=sys.stderr)
        sys.exit(1)
    
    # 啟用賽季日期／單日 context 快取（各步驟為獨立 process，靠 data/season_cache.json 共用）
    season.enable_cache(SEASON_CACHE_PATH)
    # 啟用 RSS 累積快取（data/feed_cache.json），補產舊日期時仍能取得當日新聞與深度分析
    feed_cache.enable_cache(FEED_CACHE_PATH)

    # --backfill：補產漏跑日（絕不發通知）
    if args.backfill:
        sys.exit(backfill.run_backfill(_taipei_today(args.date), args.backfill_days or BACKFILL_DAYS))

    # --watchlist：動態追蹤名單（不碰每日戰報流程與 metadata）
    if args.watchlist:
        _run_watchlist(args)
        return

    # --leaders：數據排行榜（不碰每日戰報流程與 metadata）
    if args.leaders:
        _run_leaders(args)
        return

    # --playoffs：季後賽對戰樹（不碰每日戰報流程與 metadata）
    if args.playoffs:
        _run_playoffs(args)
        return

    # --weekly / --notify-weekly：休賽季每週總整理（不碰每日戰報流程與 metadata）
    if weekly_mode:
        _run_weekly(args)
        return

    # --rosters: 只更新 data/teams，不產生戰報、不動 metadata、不發通知
    if args.rosters:
        if args.date:
            try:
                today = datetime.strptime(args.date, "%Y-%m-%d").date()
            except ValueError:
                print("Error: Date format must be YYYY-MM-DD", file=sys.stderr)
                sys.exit(1)
        else:
            today = timeutil.get_taipei_now().date()
        sys.exit(0 if rosters.fetch_rosters(today, team_page.make_extras) > 0 else 1)

    # 1. Resolve date
    if args.date:
        date_str = args.date
        # Validate date format
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            print("Error: Date format must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        date_str = timeutil.get_yesterday_str()
        
    print(f"Target Date: {date_str}")

    # --notify-only: 僅依既有 metadata 發送通知，不呼叫 API、不寫檔
    if args.notify_only:
        entry = storage.load_metadata("reports/metadata.json").get(date_str)
        if not entry:
            print(f"Error: reports/metadata.json 中找不到 {date_str} 的資料，無法發送通知。", file=sys.stderr)
            sys.exit(1)
        if not notify.should_notify(entry):
            label = "休兵日快報" if storage.entry_type(entry) == "rest_day" else "休賽季快報"
            print(f"{date_str} 為{label}且無新聞、無人事異動，不發送 Discord 通知。")
            return
        notify.send_discord_notification(entry)
        return

    # 2. Check wait loop if enabled
    if args.check_wait:
        max_checks = args.max_wait_checks
        print(f"Enabling check wait loop (max {max_checks} checks). Monitoring yesterday's games...")
        games = []
        have_games = False  # 是否曾成功取得賽程
        finalized = False
        for attempt in range(1, max_checks + 1):
            try:
                finalized, fetched_games = games_mod.check_all_games_finalized(date_str)
                games = fetched_games
                have_games = True
            except net.ApiError as e:
                print(f"Warning: {e}. Treating as not finalized.", file=sys.stderr)
                finalized = False
            if finalized:
                print("All games are finalized (Final, Postponed, Cancelled or Suspended). Proceeding to build.")
                break
            if attempt < max_checks:
                print(f"Some games are not finalized yet (check {attempt}/{max_checks}). Sleeping for 30 minutes...", flush=True)
                time.sleep(WAIT_SLEEP_SECONDS)
        if not finalized:
            if not have_games:
                print(f"Error: 已達等待上限（{max_checks} 次檢查）仍無法取得 {date_str} 的賽程。Aborting without generating any output.", file=sys.stderr)
                sys.exit(1)
            print(f"Warning: 已達等待上限（{max_checks} 次檢查），仍有未完成場次，將照常產出戰報，未完成場次標示為「未完賽」。", file=sys.stderr)
    else:
        try:
            finalized, games = games_mod.check_all_games_finalized(date_str)
        except net.ApiError as e:
            print(f"Error: {e}. Aborting without generating any output.", file=sys.stderr)
            sys.exit(1)
        if not finalized:
            print("Warning: Some games are still active or not finalized yet.", file=sys.stderr)

    # Check if there are games scheduled
    if not games:
        empty_meta, label = backfill.generate_no_game_report(date_str)
        index_page.update_index_page(empty_meta)
        if not args.no_notify:
            if notify.should_notify(empty_meta):
                notify.send_discord_notification(empty_meta)
            else:
                print(f"{label}無新聞、無人事異動，不發送 Discord 通知。")
        sys.exit(0)

    # 3. Generate report files
    new_meta = report.generate_report(date_str, games)
    
    # 4. Update index homepage listings
    index_page.update_index_page(new_meta)
    
    # 5. Send Discord notification
    if not args.no_notify:
        notify.send_discord_notification(new_meta)
    print("All tasks completed successfully.")
