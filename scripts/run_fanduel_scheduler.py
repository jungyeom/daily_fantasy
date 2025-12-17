#!/usr/bin/env python3
"""FanDuel DFS Automation Scheduler.

This script runs the automated FanDuel lineup generation workflow:

1. Daily Contest Scraping (default 11am):
   - Fetches contests from FanDuel API for all configured sports
   - Selects eligible contests based on criteria (fee < $2, GPP, multi-entry > 50, etc.)

2. Automated Lineup Generation:
   - Initial lineups: 2 hours before slate lock
   - Final lineups: 30 minutes before slate lock (with updated projections)

3. Multi-Sport Support:
   - Handles NFL, NBA, MLB, NHL contests
   - Manages multiple slates on same day (e.g., NFL 1pm + NHL 7pm)

4. Organized Output:
   - Lineups saved to data/lineups/fanduel/{SPORT}/{DATE}/
   - Format: {contest_id}_{initial|final}_{timestamp}.csv

Usage:
    # Run scheduler daemon (continuous monitoring)
    uv run python scripts/run_fanduel_scheduler.py

    # Run once and exit (useful for cron jobs)
    uv run python scripts/run_fanduel_scheduler.py --once

    # Scrape only (no lineup generation)
    uv run python scripts/run_fanduel_scheduler.py --scrape-only

    # Preview what would be scheduled
    uv run python scripts/run_fanduel_scheduler.py --dry-run

    # Run for specific sport only
    uv run python scripts/run_fanduel_scheduler.py --sport NHL

    # Generate immediate lineups for all eligible contests
    uv run python scripts/run_fanduel_scheduler.py --generate-now

Configuration (via command line or environment):
    --daily-scrape-hour: Hour for daily scraping (default: 11)
    --initial-hours-before: Hours before lock for initial lineups (default: 2.0)
    --final-minutes-before: Minutes before lock for final lineups (default: 30)

Note: Requires browser extension tokens in ~/Downloads/fanduel_tokens.json
      or FANDUEL_AUTH_TOKEN environment variable.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src.common.models import Sport
from src.fanduel.scheduler import (
    FanDuelScheduler,
    SchedulerConfig,
    ScheduledContest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="FanDuel DFS Automation Scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Run modes
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--once",
        action="store_true",
        help="Run once (scrape and schedule) then exit",
    )
    mode_group.add_argument(
        "--scrape-only",
        action="store_true",
        help="Only scrape and display contests, don't schedule or generate",
    )
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be scheduled without actually running",
    )
    mode_group.add_argument(
        "--generate-now",
        action="store_true",
        help="Generate lineups immediately for all eligible contests",
    )

    # Sport filter
    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "ALL"],
        default="ALL",
        help="Sport to process (default: ALL)",
    )

    # Timing configuration
    parser.add_argument(
        "--daily-scrape-hour",
        type=int,
        default=11,
        help="Hour for daily scraping (default: 11)",
    )
    parser.add_argument(
        "--initial-hours-before",
        type=float,
        default=2.0,
        help="Hours before lock for initial lineups (default: 2.0)",
    )
    parser.add_argument(
        "--final-minutes-before",
        type=int,
        default=30,
        help="Minutes before lock for final lineups (default: 30)",
    )

    # Contest selection criteria
    parser.add_argument(
        "--max-entry-fee",
        type=float,
        default=2.0,
        help="Maximum entry fee (default: $2.00)",
    )
    parser.add_argument(
        "--min-entries",
        type=int,
        default=50,
        help="Minimum entries per user (default: 50)",
    )

    # Lineup generation settings
    parser.add_argument(
        "--randomness",
        type=float,
        default=0.1,
        help="Randomness factor for diversity (default: 0.1)",
    )
    parser.add_argument(
        "--no-vegas",
        action="store_true",
        help="Disable Vegas lines integration",
    )

    # Output
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/lineups",
        help="Output directory for lineups (default: data/lineups)",
    )

    # Debug
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    return parser.parse_args()


def print_contest_table(contests: list[ScheduledContest], title: str = "Eligible Contests"):
    """Print formatted table of contests.

    Args:
        contests: List of ScheduledContest objects
        title: Table title
    """
    if not contests:
        print(f"\n{title}: None found")
        return

    print(f"\n{'=' * 97}")
    print(f"{title} ({len(contests)} total)")
    print("=" * 97)
    print(
        f"{'Sport':<5} | {'Contest Name':<40} | {'Slate Lock':<16} | "
        f"{'Fee':>6} | {'Max':>5} | {'Score':>5}"
    )
    print("-" * 97)

    for contest in sorted(contests, key=lambda c: c.slate_start):
        # Format fee to show cents for small values
        fee_str = f"${contest.entry_fee:.2f}" if contest.entry_fee < 10 else f"${contest.entry_fee:.0f}"
        print(
            f"{contest.sport.value:<5} | {contest.name[:40]:<40} | "
            f"{contest.slate_start.strftime('%Y-%m-%d %H:%M'):<16} | "
            f"{fee_str:>6} | {contest.max_entries:>5} | {contest.score:>5}"
        )

    print("=" * 97)


def run_scrape_only(scheduler: FanDuelScheduler, sports: list[Sport]):
    """Run scrape-only mode: fetch and display eligible contests.

    Args:
        scheduler: FanDuelScheduler instance
        sports: List of sports to scrape
    """
    print("\n" + "=" * 60)
    print("FANDUEL CONTEST SCRAPER")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_contests = []
    for sport in sports:
        contests = scheduler.scrape_contests(sport)
        all_contests.extend(contests)
        print_contest_table(contests, f"{sport.value} Contests")

    if all_contests:
        print(f"\n📊 Summary: {len(all_contests)} eligible contests found")
        total_entries = sum(c.max_entries for c in all_contests)
        print(f"📊 Total lineup capacity: {total_entries} lineups")


def run_dry_run(scheduler: FanDuelScheduler, sports: list[Sport]):
    """Run dry-run mode: show what would be scheduled.

    Args:
        scheduler: FanDuelScheduler instance
        sports: List of sports to process
    """
    print("\n" + "=" * 60)
    print("FANDUEL SCHEDULER - DRY RUN")
    print("(No lineups will be generated)")
    print("=" * 60)

    all_contests = []
    for sport in sports:
        contests = scheduler.scrape_contests(sport)
        all_contests.extend(contests)

    print_contest_table(all_contests, "Would Schedule")

    # Show what jobs would be created
    now = datetime.now()
    print("\n" + "=" * 80)
    print("SCHEDULED JOBS (Preview)")
    print("=" * 80)
    print(f"{'Job Type':<10} | {'Contest':<35} | {'Scheduled Time':<20} | {'Status':<10}")
    print("-" * 80)

    from datetime import timedelta

    for contest in sorted(all_contests, key=lambda c: c.slate_start):
        initial_time = contest.slate_start - timedelta(
            hours=scheduler.config.initial_lineup_hours_before
        )
        final_time = contest.slate_start - timedelta(
            minutes=scheduler.config.final_lineup_minutes_before
        )

        initial_status = "Would run" if initial_time > now else "PAST"
        final_status = "Would run" if final_time > now else "PAST"

        print(
            f"{'Initial':<10} | {contest.name[:35]:<35} | "
            f"{initial_time.strftime('%Y-%m-%d %H:%M'):<20} | {initial_status:<10}"
        )
        print(
            f"{'Final':<10} | {contest.name[:35]:<35} | "
            f"{final_time.strftime('%Y-%m-%d %H:%M'):<20} | {final_status:<10}"
        )

    print("=" * 80)


def run_generate_now(scheduler: FanDuelScheduler, sports: list[Sport]):
    """Generate lineups immediately for all eligible contests.

    Args:
        scheduler: FanDuelScheduler instance
        sports: List of sports to process
    """
    print("\n" + "=" * 60)
    print("FANDUEL IMMEDIATE LINEUP GENERATION")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Scrape and get all eligible contests
    all_contests = []
    for sport in sports:
        contests = scheduler.scrape_contests(sport)
        all_contests.extend(contests)

    if not all_contests:
        print("\n❌ No eligible contests found")
        return

    print_contest_table(all_contests, "Generating Lineups For")

    # Generate lineups for each contest
    results = []
    for contest in all_contests:
        contest_key = f"{contest.sport.value}_{contest.contest_id}"
        scheduler._scheduled_contests[contest_key] = contest

        print(f"\n{'─' * 60}")
        print(f"Generating: {contest.name[:50]}")
        print(f"Sport: {contest.sport.value} | Entries: {contest.max_entries}")
        print("─" * 60)

        filepath = scheduler._generate_initial_lineups(contest_key)

        if filepath:
            results.append({
                "contest": contest.name,
                "success": True,
                "filepath": str(filepath),
                "lineups": contest.max_entries,
            })
            print(f"✅ Generated {contest.max_entries} lineups → {filepath}")
        else:
            results.append({
                "contest": contest.name,
                "success": False,
                "filepath": None,
                "lineups": 0,
            })
            print(f"❌ Failed to generate lineups")

    # Summary
    print("\n" + "=" * 60)
    print("GENERATION SUMMARY")
    print("=" * 60)

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print(f"\n✅ Successful: {len(successful)} contests")
    for r in successful:
        print(f"   - {r['contest'][:40]}: {r['lineups']} lineups")
        print(f"     → {r['filepath']}")

    if failed:
        print(f"\n❌ Failed: {len(failed)} contests")
        for r in failed:
            print(f"   - {r['contest'][:40]}")

    total_lineups = sum(r["lineups"] for r in results)
    print(f"\n📊 Total lineups generated: {total_lineups}")


def run_scheduler_daemon(scheduler: FanDuelScheduler):
    """Run scheduler as a daemon with continuous monitoring.

    Args:
        scheduler: FanDuelScheduler instance
    """
    import signal

    def signal_handler(signum, frame):
        logger.info("Received shutdown signal")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    print("\n" + "=" * 60)
    print("FANDUEL SCHEDULER DAEMON")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Schedule daily scraping
    scheduler.schedule_daily_scrape()
    print(
        f"\n📅 Daily scrape scheduled at "
        f"{scheduler.config.daily_scrape_hour:02d}:{scheduler.config.daily_scrape_minute:02d}"
    )

    # Do initial scrape
    print("\n🔍 Running initial contest scrape...")
    scheduler.scrape_and_schedule_all()

    # Start scheduler
    scheduler.start()
    scheduler.print_schedule()

    # Keep running
    print("\n🚀 Scheduler running. Press Ctrl+C to stop.")
    print("=" * 60)

    try:
        while True:
            time.sleep(300)  # Check every 5 minutes
            scheduler.print_schedule()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        scheduler.stop()


def main():
    """Main entry point."""
    args = parse_args()

    # Configure logging
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine sports to process
    if args.sport == "ALL":
        sports = [Sport.NFL, Sport.NBA, Sport.MLB, Sport.NHL]
    else:
        sports = [Sport(args.sport)]

    # Create config
    config = SchedulerConfig(
        daily_scrape_hour=args.daily_scrape_hour,
        initial_lineup_hours_before=args.initial_hours_before,
        final_lineup_minutes_before=args.final_minutes_before,
        max_entry_fee=args.max_entry_fee,
        min_entries_per_user=args.min_entries,
        randomness=args.randomness,
        use_vegas_lines=not args.no_vegas,
        sports=sports,
        output_dir=Path(args.output_dir),
    )

    # Create scheduler
    scheduler = FanDuelScheduler(config)

    # Run appropriate mode
    if args.scrape_only:
        run_scrape_only(scheduler, sports)
    elif args.dry_run:
        run_dry_run(scheduler, sports)
    elif args.generate_now:
        run_generate_now(scheduler, sports)
    elif args.once:
        print("\n" + "=" * 60)
        print("FANDUEL SCHEDULER - ONE-TIME RUN")
        print("=" * 60)
        results = scheduler.scrape_and_schedule_all()
        for sport_name, contests in results.items():
            print_contest_table(contests, f"{sport_name} Scheduled")
        scheduler.print_schedule()
    else:
        # Run as daemon
        run_scheduler_daemon(scheduler)


if __name__ == "__main__":
    main()
