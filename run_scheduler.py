#!/usr/bin/env python3
"""Daily Fantasy Scheduler - Entry Point

This script runs the DFS automation scheduler using AutomationRunner.

Features:
- Daily contest fetching and pipeline scheduling for all sports
- Fill rate monitoring (submit when >= 70% full or < 2 hours to lock)
- Injury monitoring (swap OUT/INJ players)
- Dynamic projection refresh (tiered intervals based on time-to-lock)
- Edit lineups 30 min before lock to replace injured players
- Auto-debug: Invokes Claude Code to fix errors automatically

Usage:
    # Run in production mode
    uv run python run_scheduler.py

    # Run in dry-run mode (no actual submissions)
    uv run python run_scheduler.py --dry-run

    # Run full pipeline for a specific contest
    uv run python run_scheduler.py --run-pipeline --sport nba --contest-id <id> --contest-name "Contest Name"

    # List scheduled jobs
    uv run python run_scheduler.py --list-jobs
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.scheduler.runner import AutomationRunner, run_full_pipeline
from src.common.models import Sport
from src.common.notifications import get_notifier


def setup_logging(level: str = "INFO") -> None:
    """Configure logging."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # Create log directory if needed
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / "scheduler.log"),
        ],
    )


def main():
    parser = argparse.ArgumentParser(
        description="Daily Fantasy Scheduler (AutomationRunner)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate actions without actually submitting (not yet implemented)",
    )
    parser.add_argument(
        "--run-pipeline",
        action="store_true",
        help="Run full pipeline for a specific contest and exit",
    )
    parser.add_argument(
        "--sport",
        choices=["nfl", "nba", "mlb", "nhl"],
        default="nba",
        help="Sport for --run-pipeline (default: nba)",
    )
    parser.add_argument(
        "--contest-id",
        help="Contest ID for --run-pipeline",
    )
    parser.add_argument(
        "--contest-name",
        default="Contest",
        help="Contest name for --run-pipeline",
    )
    parser.add_argument(
        "--list-jobs",
        action="store_true",
        help="List all scheduled jobs and exit",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("Daily Fantasy Scheduler (AutomationRunner)")
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'PRODUCTION'}")
    logger.info("=" * 60)

    # Get notifier for alerts
    notifier = get_notifier()

    # Handle --run-pipeline
    if args.run_pipeline:
        if not args.contest_id:
            logger.error("--contest-id is required with --run-pipeline")
            sys.exit(1)

        sport = Sport(args.sport.upper())
        logger.info(f"Running full pipeline for {sport.value} contest {args.contest_id}")

        try:
            run_full_pipeline(
                sport=sport,
                contest_id=args.contest_id,
                contest_name=args.contest_name,
            )
            logger.info("Pipeline completed successfully")
        except Exception as e:
            logger.error(f"Pipeline failed: {e}", exc_info=True)
            notifier.notify_error(
                error_type="PipelineError",
                error_message=str(e),
                context={"sport": args.sport, "contest_id": args.contest_id},
            )
            sys.exit(1)
        return

    # Initialize runner
    runner = AutomationRunner()

    # Handle --list-jobs
    if args.list_jobs:
        runner.schedule_daily_jobs()
        runner.start()  # Must start scheduler for jobs to have next_run_time
        jobs = runner.list_scheduled_jobs()
        logger.info(f"Scheduled jobs ({len(jobs)}):")
        for job in jobs:
            logger.info(f"  - {job['id']}: next run at {job['next_run']}")
        runner.stop()
        return

    # Set up signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        runner.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # Schedule all daily jobs
        runner.schedule_daily_jobs()

        # Start the scheduler
        runner.start()

        # Send startup notification
        try:
            notifier.notify_error(
                error_type="SchedulerStarted",
                error_message="Scheduler started successfully",
                context={
                    "mode": "DRY RUN" if args.dry_run else "PRODUCTION",
                    "jobs_scheduled": len(runner.list_scheduled_jobs()),
                },
            )
        except Exception as e:
            logger.warning(f"Failed to send startup notification: {e}")

        logger.info("Scheduler running. Press Ctrl+C to stop.")
        logger.info(f"Jobs scheduled: {len(runner.list_scheduled_jobs())}")

        # Keep main thread alive
        while True:
            time.sleep(60)
            # Periodic status log
            jobs = runner.list_scheduled_jobs()
            logger.debug(f"Scheduler alive, {len(jobs)} jobs scheduled")

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        notifier.notify_error(
            error_type="SchedulerCrash",
            error_message=str(e),
            context={"fatal": True},
        )
        sys.exit(1)
    finally:
        runner.stop()
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
