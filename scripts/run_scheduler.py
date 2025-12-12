#!/usr/bin/env python3
"""Run the DFS automation scheduler.

This script runs continuously, managing all automated tasks:
- Contest syncing (fetches eligible contests from Yahoo)
- Projection syncing (fetches projections from DailyFantasyFuel)
- Lineup submission (monitors fill rates and submits when ready)
- Injury monitoring (swaps OUT players in submitted lineups)

Usage:
    # Run in production mode
    python scripts/run_scheduler.py

    # Run in dry-run mode (simulates actions without executing)
    python scripts/run_scheduler.py --dry-run

    # Run for specific sports
    python scripts/run_scheduler.py --sports nfl nba

    # Run a single job manually
    python scripts/run_scheduler.py --run-once contest-sync
    python scripts/run_scheduler.py --run-once projection-sync --force
    python scripts/run_scheduler.py --run-once submission
    python scripts/run_scheduler.py --run-once injury-check

    # Check scheduler status
    python scripts/run_scheduler.py --status

Environment Variables:
    DFS_SENDGRID_API_KEY: SendGrid API key for email alerts
    DFS_ALERTS_ENABLED: Set to 'false' to disable email alerts
    DFS_ALERT_TO: Email address for alerts (default: jungyeom0213@gmail.com)
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.database import init_database
from src.scheduler.manager import DFSSchedulerManager


def setup_logging(verbose: bool = False) -> None:
    """Set up logging configuration.

    Args:
        verbose: If True, set DEBUG level; otherwise INFO
    """
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(project_root / "data" / "scheduler.log"),
        ],
    )

    # Reduce noise from third-party libraries
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)


def run_single_job(manager: DFSSchedulerManager, job_name: str, **kwargs) -> dict:
    """Run a single job manually.

    Args:
        manager: Scheduler manager instance
        job_name: Name of job to run
        **kwargs: Additional arguments for the job

    Returns:
        Job result dict
    """
    job_map = {
        "contest-sync": manager.run_contest_sync,
        "projection-sync": manager.run_projection_sync,
        "submission": manager.run_submission_check,
        "injury-check": manager.run_injury_check,
    }

    if job_name not in job_map:
        raise ValueError(f"Unknown job: {job_name}. Valid jobs: {list(job_map.keys())}")

    return job_map[job_name](**kwargs)


def main():
    parser = argparse.ArgumentParser(
        description="DFS Automation Scheduler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate actions without executing (no submissions, no swaps)",
    )

    parser.add_argument(
        "--sports",
        nargs="+",
        default=["nfl"],
        help="Sports to track (default: nfl)",
    )

    parser.add_argument(
        "--run-once",
        choices=["contest-sync", "projection-sync", "submission", "injury-check"],
        help="Run a single job and exit",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force refresh (for projection-sync)",
    )

    parser.add_argument(
        "--sport",
        help="Specific sport for --run-once (default: all configured sports)",
    )

    parser.add_argument(
        "--status",
        action="store_true",
        help="Print scheduler status and exit",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Set up logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Ensure data directory exists
    data_dir = project_root / "data"
    data_dir.mkdir(exist_ok=True)

    # Initialize database
    logger.info("Initializing database...")
    init_database()

    # Create scheduler manager
    manager = DFSSchedulerManager(
        dry_run=args.dry_run,
        sports=args.sports,
    )

    if args.dry_run:
        logger.info("=" * 50)
        logger.info("RUNNING IN DRY-RUN MODE")
        logger.info("No lineups will be submitted, no swaps will be made")
        logger.info("=" * 50)

    # Handle different modes
    if args.status:
        # Print status and exit
        manager.start()
        status = manager.get_status()
        print(json.dumps(status, indent=2))
        manager.stop()
        return

    if args.run_once:
        # Run single job and exit
        logger.info(f"Running single job: {args.run_once}")

        kwargs = {}
        if args.sport:
            kwargs["sport"] = args.sport
        if args.force:
            kwargs["force"] = args.force

        try:
            result = run_single_job(manager, args.run_once, **kwargs)
            print(json.dumps(result, indent=2, default=str))
            logger.info("Job completed successfully")
        except Exception as e:
            logger.error(f"Job failed: {e}")
            sys.exit(1)
        return

    # Run scheduler continuously
    logger.info("Starting DFS Scheduler in continuous mode...")
    logger.info(f"Sports: {args.sports}")

    try:
        manager.run_forever()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
    except Exception as e:
        logger.error(f"Scheduler crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
