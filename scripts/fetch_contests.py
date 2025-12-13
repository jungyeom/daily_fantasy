#!/usr/bin/env python3
"""Fetch and display available contests from Yahoo DFS."""
import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import get_config
from src.common.database import init_database
from src.common.models import Sport
from src.scheduler.job_functions import JobContext, job_fetch_contests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Fetch and display contests."""
    parser = argparse.ArgumentParser(description="Fetch Yahoo DFS Contests")

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA", "all"],
        default="all",
        help="Sport to fetch (default: all)",
    )

    parser.add_argument(
        "--max-fee",
        type=float,
        default=1.0,
        help="Maximum entry fee (default: $1.00)",
    )

    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Show all contests, not just filtered ones",
    )

    args = parser.parse_args()

    # Initialize
    config = get_config()
    if args.max_fee != config.contest_filter.max_entry_fee:
        config.contest_filter.max_entry_fee = args.max_fee

    db = init_database()
    context = JobContext()

    try:
        sports = [Sport(args.sport)] if args.sport != "all" else list(Sport)

        for sport in sports:
            logger.info(f"\nFetching {sport.value} contests...")
            count = job_fetch_contests(context, sport)

            if count > 0:
                display_contests(sport, args.show_all)

    finally:
        context.close_driver()


def display_contests(sport: Sport, show_all: bool = False):
    """Display fetched contests.

    Args:
        sport: Sport to display
        show_all: Show all or filtered only
    """
    from src.common.database import get_database, ContestDB
    from src.common.config import get_config
    from datetime import datetime

    db = get_database()
    config = get_config()
    session = db.get_session()

    try:
        query = (
            session.query(ContestDB)
            .filter(ContestDB.sport == sport.value)
            .filter(ContestDB.slate_start > datetime.now())
        )

        if not show_all:
            query = query.filter(ContestDB.entry_fee <= config.contest_filter.max_entry_fee)

        contests = query.order_by(ContestDB.slate_start).all()

        if not contests:
            print(f"No {sport.value} contests found")
            return

        print(f"\n{'='*80}")
        print(f"{sport.value} CONTESTS")
        print(f"{'='*80}")
        print(f"{'ID':<12} {'Name':<30} {'Fee':>8} {'Max':>6} {'Start':<20}")
        print("-" * 80)

        for contest in contests:
            print(
                f"{contest.id:<12} "
                f"{contest.name[:28]:<30} "
                f"${contest.entry_fee:>6.2f} "
                f"{contest.max_entries:>6} "
                f"{contest.slate_start.strftime('%Y-%m-%d %H:%M'):<20}"
            )

        print("-" * 80)
        print(f"Total: {len(contests)} contests")

    finally:
        session.close()


if __name__ == "__main__":
    main()
