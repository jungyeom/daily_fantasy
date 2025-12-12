#!/usr/bin/env python3
"""Submit generated lineups to Yahoo DFS."""
import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import get_config
from src.common.database import init_database
from src.common.models import Sport, LineupStatus

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Submit lineups."""
    parser = argparse.ArgumentParser(description="Submit Lineups to Yahoo DFS")

    parser.add_argument(
        "--contest-id",
        type=str,
        required=True,
        help="Contest ID",
    )

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA"],
        required=True,
        help="Sport",
    )

    parser.add_argument(
        "--csv",
        type=str,
        help="CSV file with lineups (if not using generated lineups)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be submitted without actually submitting",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Submit even if lineups are already marked as submitted",
    )

    args = parser.parse_args()

    # Initialize
    config = get_config()
    db = init_database()

    sport = Sport(args.sport)

    # Import modules
    from src.yahoo.browser import get_browser_manager
    from src.yahoo.auth import YahooAuth
    from src.yahoo.submission import LineupSubmitter
    from src.lineup_manager.tracker import LineupTracker
    from src.common.database import get_database, ContestDB

    # Get contest info
    session = db.get_session()
    try:
        contest = session.query(ContestDB).filter_by(id=args.contest_id).first()
        if not contest:
            logger.error(f"Contest {args.contest_id} not found in database")
            sys.exit(1)
        contest_name = contest.name
    finally:
        session.close()

    # Get lineups to submit
    tracker = LineupTracker()

    if args.force:
        # Get all lineups including already submitted
        from src.common.database import LineupDB
        session = db.get_session()
        try:
            db_lineups = session.query(LineupDB).filter_by(contest_id=args.contest_id).all()
            lineup_ids = [l.id for l in db_lineups]
            lineups = [tracker.get_lineup_by_id(lid) for lid in lineup_ids]
            lineups = [l for l in lineups if l is not None]
        finally:
            session.close()
    else:
        lineups = tracker.get_lineups_for_contest(args.contest_id, status=LineupStatus.GENERATED)

    if not lineups:
        logger.error(f"No pending lineups found for contest {args.contest_id}")
        logger.info("Run 'python scripts/generate_lineups.py' first to generate lineups")
        sys.exit(1)

    logger.info(f"Found {len(lineups)} lineups to submit")

    # Dry run - just show what would be submitted
    if args.dry_run:
        print(f"\n{'='*60}")
        print(f"DRY RUN - Would submit {len(lineups)} lineups")
        print(f"Contest: {contest_name} (ID: {args.contest_id})")
        print(f"{'='*60}")

        for i, lineup in enumerate(lineups[:5], 1):
            print(f"\nLineup {i}:")
            print(f"  Projected: {lineup.projected_points:.1f} pts")
            print(f"  Salary: ${lineup.total_salary}")
            print(f"  Players: {', '.join(p.name for p in lineup.players)}")

        if len(lineups) > 5:
            print(f"\n... and {len(lineups) - 5} more lineups")

        print(f"\n{'='*60}")
        print("Run without --dry-run to submit")
        return

    # Submit lineups
    logger.info("Initializing browser...")
    browser = get_browser_manager()
    driver = browser.create_driver()

    try:
        # Authenticate
        auth = YahooAuth()
        auth.login(driver)

        # Submit
        submitter = LineupSubmitter()
        successful, failed = submitter.submit_lineups(
            driver=driver,
            lineups=lineups,
            contest_id=args.contest_id,
            sport_name=sport.value,
            contest_name=contest_name,
        )

        # Summary
        print(f"\n{'='*60}")
        print(f"SUBMISSION COMPLETE")
        print(f"{'='*60}")
        print(f"Contest: {contest_name}")
        print(f"Successful: {successful}")
        print(f"Failed: {failed}")
        print(f"{'='*60}")

        if failed > 0:
            logger.warning(f"{failed} lineups failed to submit")
            sys.exit(1)

    finally:
        browser.close_driver()


if __name__ == "__main__":
    main()
