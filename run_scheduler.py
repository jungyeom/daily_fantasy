#!/usr/bin/env python3
"""Daily Fantasy Scheduler - Entry Point

This script runs the DFS automation scheduler for lineup generation,
submission, and injury monitoring.

Usage:
    # Run in production mode
    python run_scheduler.py

    # Run in dry-run mode (no actual submissions)
    python run_scheduler.py --dry-run

    # Run specific jobs manually
    python run_scheduler.py --run-job contest_sync
    python run_scheduler.py --run-job projection_sync
    python run_scheduler.py --run-job submission
    python run_scheduler.py --run-job injury_check

    # Submit lineups now (one-time)
    python run_scheduler.py --submit-now
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.scheduler.manager import DFSSchedulerManager
from src.scheduler.alerts import get_alerter


def setup_logging(level: str = "INFO") -> None:
    """Configure logging."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("data/logs/scheduler.log"),
        ],
    )
    # Create log directory if needed
    Path("data/logs").mkdir(parents=True, exist_ok=True)


def main():
    parser = argparse.ArgumentParser(
        description="Daily Fantasy Scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate actions without actually submitting",
    )
    parser.add_argument(
        "--sports",
        nargs="+",
        default=["nfl", "nba"],
        help="Sports to track (default: nfl nba)",
    )
    parser.add_argument(
        "--run-job",
        choices=["contest_sync", "projection_sync", "submission", "injury_check"],
        help="Run a specific job once and exit",
    )
    parser.add_argument(
        "--submit-now",
        action="store_true",
        help="Submit lineups immediately for all eligible contests",
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
    logger.info("Daily Fantasy Scheduler")
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'PRODUCTION'}")
    logger.info(f"Sports: {args.sports}")
    logger.info("=" * 60)

    # Initialize manager and alerter
    manager = DFSSchedulerManager(
        dry_run=args.dry_run,
        sports=args.sports,
    )
    alerter = get_alerter()

    try:
        if args.run_job:
            # Run a specific job once
            logger.info(f"Running job: {args.run_job}")

            if args.run_job == "contest_sync":
                result = manager.run_contest_sync()
            elif args.run_job == "projection_sync":
                result = manager.run_projection_sync(force=True)
            elif args.run_job == "submission":
                result = manager.run_submission_check()
            elif args.run_job == "injury_check":
                result = manager.run_injury_check()

            logger.info(f"Job result: {result}")
            return

        if args.submit_now:
            # Submit lineups immediately
            logger.info("Submitting lineups now...")

            # First sync contests
            logger.info("Step 1: Syncing contests...")
            sync_result = manager.run_contest_sync()
            logger.info(f"Contest sync: {sync_result}")

            # Then sync projections
            logger.info("Step 2: Syncing projections...")
            proj_result = manager.run_projection_sync(force=True)
            logger.info(f"Projection sync: {proj_result}")

            # Then submit
            logger.info("Step 3: Submitting lineups...")
            submit_result = manager.run_submission_check()
            logger.info(f"Submission result: {submit_result}")

            return

        # Run scheduler continuously
        logger.info("Starting scheduler in continuous mode...")
        logger.info("Press Ctrl+C to stop")

        # Send startup alert
        alerter.alert_scheduler_started(sports=args.sports, dry_run=args.dry_run)

        manager.run_forever()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        alerter.alert_scheduler_stopped(reason="User interrupted (Ctrl+C)")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        alerter.alert_scheduler_stopped(reason=f"Fatal error: {e}")
        sys.exit(1)
    finally:
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    main()
