#!/usr/bin/env python3
"""Main automation pipeline - runs the full lineup generation and submission flow."""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import get_config
from src.common.database import init_database
from src.common.models import Sport
from src.scheduler.runner import run_full_pipeline, get_runner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Run the automation pipeline."""
    parser = argparse.ArgumentParser(description="Daily Fantasy Lineup Automation Pipeline")

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA"],
        required=True,
        help="Sport to run pipeline for",
    )

    parser.add_argument(
        "--contest-id",
        type=str,
        help="Specific contest ID (if not provided, runs for all eligible contests)",
    )

    parser.add_argument(
        "--mode",
        type=str,
        choices=["full", "fetch", "generate", "submit", "monitor"],
        default="full",
        help="Pipeline mode: full (all steps), or individual steps",
    )

    parser.add_argument(
        "--scheduler",
        action="store_true",
        help="Run in scheduler mode (background automation)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate lineups but don't submit",
    )

    args = parser.parse_args()

    # Initialize
    config = get_config()
    db = init_database()

    sport = Sport(args.sport)
    logger.info(f"Starting pipeline for {sport.value}")

    if args.scheduler:
        # Run in scheduler mode
        runner = get_runner()
        runner.schedule_daily_jobs()
        runner.schedule_sport_day(sport, "sunday" if sport == Sport.NFL else "daily")
        runner.start()

        logger.info("Scheduler running. Press Ctrl+C to stop.")
        try:
            while True:
                import time
                time.sleep(60)
        except KeyboardInterrupt:
            runner.stop()
            logger.info("Scheduler stopped")

    elif args.contest_id:
        # Run for specific contest
        if args.mode == "full":
            run_full_pipeline(sport, args.contest_id, f"{sport.value} Contest")
        else:
            run_single_step(args.mode, sport, args.contest_id, args.dry_run)

    else:
        # Run for all eligible contests
        run_for_all_contests(sport, args.mode, args.dry_run)


def run_single_step(mode: str, sport: Sport, contest_id: str, dry_run: bool = False):
    """Run a single pipeline step.

    Args:
        mode: Step to run
        sport: Sport
        contest_id: Contest ID
        dry_run: Don't submit if True
    """
    from src.scheduler.jobs import (
        JobContext,
        job_fetch_player_pool,
        job_fetch_projections,
        job_generate_lineups,
        job_submit_lineups,
        job_check_late_swaps,
    )

    context = JobContext()

    try:
        if mode == "fetch":
            job_fetch_player_pool(context, contest_id, sport)
            job_fetch_projections(context, sport, contest_id)

        elif mode == "generate":
            job_generate_lineups(context, sport, contest_id)

        elif mode == "submit":
            if dry_run:
                logger.info("Dry run - skipping submission")
            else:
                job_submit_lineups(context, contest_id, sport.value, f"{sport.value} Contest")

        elif mode == "monitor":
            job_check_late_swaps(context, sport)

    finally:
        context.close_driver()


def run_for_all_contests(sport: Sport, mode: str, dry_run: bool = False):
    """Run pipeline for all eligible contests.

    Args:
        sport: Sport
        mode: Pipeline mode
        dry_run: Don't submit if True
    """
    from src.scheduler.jobs import JobContext, job_fetch_contests
    from src.common.database import get_database, ContestDB
    from src.common.config import get_config

    config = get_config()
    db = get_database()
    context = JobContext()

    try:
        # First fetch available contests
        job_fetch_contests(context, sport)

        # Get eligible contests from database
        session = db.get_session()
        try:
            contests = (
                session.query(ContestDB)
                .filter(ContestDB.sport == sport.value)
                .filter(ContestDB.slate_start > datetime.now())
                .filter(ContestDB.entry_fee <= config.contest_filter.max_entry_fee)
                .all()
            )

            logger.info(f"Found {len(contests)} eligible contests")

            for contest in contests:
                logger.info(f"Processing contest: {contest.name} (ID: {contest.id})")

                if mode == "full":
                    run_full_pipeline(sport, contest.id, contest.name)
                else:
                    run_single_step(mode, sport, contest.id, dry_run)

        finally:
            session.close()

    finally:
        context.close_driver()


if __name__ == "__main__":
    main()
