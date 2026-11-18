"""命令列入口與流程控制。"""

import argparse
import sys
import time
from datetime import datetime

from . import games as games_mod, index_page, net, notify, report, rosters, storage, timeutil
from .config import MAX_WAIT_CHECKS, WAIT_SLEEP_SECONDS


def main():
    parser = argparse.ArgumentParser(description="MLB Daily Report Generator Script")
    parser.add_argument("--date", type=str, help="Target report date in YYYY-MM-DD format (defaults to yesterday in Taipei Time)")
    parser.add_argument("--check-wait", action="store_true", help="If active, loops and sleeps 30 minutes until yesterday's games are finished")
    parser.add_argument("--max-wait-checks", type=int, default=MAX_WAIT_CHECKS, help=f"Max number of checks in the --check-wait loop (default {MAX_WAIT_CHECKS})")
    
    parser.add_argument("--rosters", action="store_true", help="Fetch all 30 team rosters with season stats and write data/teams/*.json (no report, no notification)")
    parser.add_argument("--no-notify", action="store_true", help="Generate report and metadata but do not send Discord notification")
    parser.add_argument("--notify-only", action="store_true", help="Only send Discord notification from existing metadata (no API calls, no file writes)")

    args = parser.parse_args()
    if args.notify_only and (args.check_wait or args.no_notify):
        print("Error: --notify-only cannot be combined with --check-wait or --no-notify", file=sys.stderr)
        sys.exit(1)
    if args.rosters and (args.notify_only or args.check_wait):
        print("Error: --rosters cannot be combined with --notify-only or --check-wait", file=sys.stderr)
        sys.exit(1)
    if args.max_wait_checks < 1:
        print("Error: --max-wait-checks must be >= 1", file=sys.stderr)
        sys.exit(1)
    
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
        sys.exit(0 if rosters.fetch_rosters(today) > 0 else 1)

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
            print(f"{date_str} 為休賽季快報且無新聞、無人事異動，不發送 Discord 通知。")
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
        print(f"No games scheduled or found on {date_str}. Generating offseason report.")
        empty_meta = report.generate_offseason_report(date_str)
        index_page.update_index_page(empty_meta)
        if not args.no_notify:
            if notify.should_notify(empty_meta):
                notify.send_discord_notification(empty_meta)
            else:
                print("休賽季快報無新聞、無人事異動，不發送 Discord 通知。")
        sys.exit(0)

    # 3. Generate report files
    new_meta = report.generate_report(date_str, games)
    
    # 4. Update index homepage listings
    index_page.update_index_page(new_meta)
    
    # 5. Send Discord notification
    if not args.no_notify:
        notify.send_discord_notification(new_meta)
    print("All tasks completed successfully.")
