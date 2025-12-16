#!/usr/bin/env python3
"""Fetch and display available contests from FanDuel DFS.

This script requires FanDuel auth tokens to be configured.
See src/fanduel/api.py for instructions on how to extract tokens.

Usage:
    # First time: Set auth tokens via environment variables or config
    export FANDUEL_AUTH_TOKEN="Basic ..."
    export FANDUEL_SESSION_TOKEN="..."

    # Then fetch contests
    python scripts/fanduel_fetch_contests.py --sport NFL
"""
import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.database import (
    init_database,
    get_database,
    FanDuelFixtureListDB,
    FanDuelContestDB,
)
from src.common.models import Sport
from src.fanduel.api import (
    FanDuelApiClient,
    parse_fixture_list,
    parse_contest,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_auth_tokens() -> tuple[str, str]:
    """Get FanDuel auth tokens from environment or config.

    Checks in order:
    1. Environment variables (FANDUEL_AUTH_TOKEN, FANDUEL_SESSION_TOKEN)
    2. Config file (config/settings.yaml)

    Returns:
        Tuple of (basic_auth_token, session_token)

    Raises:
        SystemExit: If tokens not configured
    """
    from src.common.config import get_config

    # Check environment variables first
    auth_token = os.environ.get("FANDUEL_AUTH_TOKEN")
    session_token = os.environ.get("FANDUEL_SESSION_TOKEN")

    # Fall back to config file
    if not auth_token or not session_token:
        try:
            config = get_config()
            auth_token = auth_token or config.fanduel.auth_token
            session_token = session_token or config.fanduel.session_token
        except Exception:
            pass

    if not auth_token or not session_token:
        print("\n" + "=" * 70)
        print("FANDUEL AUTHENTICATION REQUIRED")
        print("=" * 70)
        print("\nAuth tokens not found in environment variables.")
        print("\nTo get tokens:")
        print("1. Log into FanDuel DFS (https://www.fanduel.com/contests)")
        print("2. Open browser dev tools (F12) -> Network tab")
        print("3. Refresh the page and find any request to api.fanduel.com")
        print("4. Copy the 'Authorization' header value")
        print("5. Copy the 'X-Auth-Token' header value")
        print("\nThen set environment variables:")
        print('  export FANDUEL_AUTH_TOKEN="Basic ..."')
        print('  export FANDUEL_SESSION_TOKEN="..."')
        print("=" * 70 + "\n")
        sys.exit(1)

    return auth_token, session_token


def fetch_and_store_contests(
    client: FanDuelApiClient,
    sport: Sport,
) -> int:
    """Fetch contests from FanDuel API and store in database.

    Args:
        client: Authenticated FanDuel API client
        sport: Sport to fetch

    Returns:
        Number of contests stored
    """
    db = get_database()
    session = db.get_session()
    total_contests = 0

    try:
        # Fetch fixture lists (slates) for this sport
        fixture_lists = client.get_fixture_lists(sport)
        logger.info(f"Found {len(fixture_lists)} fixture lists for {sport.value}")

        for fl_raw in fixture_lists:
            fl = parse_fixture_list(fl_raw)

            # Store fixture list
            fixture_list_db = session.get(FanDuelFixtureListDB, fl["id"])
            if not fixture_list_db:
                fixture_list_db = FanDuelFixtureListDB(
                    id=fl["id"],
                    sport=sport.value,
                    label=fl["label"],
                    slate_start=fl["slate_start"],
                    salary_cap=fl["salary_cap"],
                    contest_count=fl["contest_count"],
                )
                session.add(fixture_list_db)
            else:
                fixture_list_db.label = fl["label"]
                fixture_list_db.slate_start = fl["slate_start"]
                fixture_list_db.salary_cap = fl["salary_cap"]
                fixture_list_db.contest_count = fl["contest_count"]
                fixture_list_db.updated_at = datetime.utcnow()

            session.flush()

            # Fetch contests for this fixture list
            contests_raw = client.get_contests(fl["id"])

            for contest_raw in contests_raw:
                contest = parse_contest(contest_raw)

                contest_db = session.get(FanDuelContestDB, contest["id"])
                if not contest_db:
                    contest_db = FanDuelContestDB(
                        id=contest["id"],
                        fixture_list_id=fl["id"],
                        sport=sport.value,
                        name=contest["name"],
                        entry_fee=float(contest["entry_fee"]),
                        max_entries=contest["max_entries"],
                        entry_count=contest["entry_count"],
                        size=contest["size"],
                        prize_pool=float(contest["prize_pool"]) if contest["prize_pool"] else None,
                        slate_start=fl["slate_start"],  # Use fixture list start time
                        contest_type=contest["contest_type"],
                        is_guaranteed=contest["is_guaranteed"],
                        salary_cap=contest["salary_cap"],
                    )
                    session.add(contest_db)
                else:
                    contest_db.entry_count = contest["entry_count"]
                    contest_db.prize_pool = float(contest["prize_pool"]) if contest["prize_pool"] else None
                    contest_db.updated_at = datetime.utcnow()

                total_contests += 1

            session.commit()

    except Exception as e:
        session.rollback()
        logger.error(f"Error fetching contests: {e}")
        raise
    finally:
        session.close()

    return total_contests


def display_contests(sport: Sport, max_fee: float = None):
    """Display fetched FanDuel contests.

    Args:
        sport: Sport to display
        max_fee: Optional max entry fee filter
    """
    db = get_database()
    session = db.get_session()

    try:
        query = (
            session.query(FanDuelContestDB)
            .filter(FanDuelContestDB.sport == sport.value)
            .filter(FanDuelContestDB.slate_start > datetime.now())
        )

        if max_fee is not None:
            query = query.filter(FanDuelContestDB.entry_fee <= max_fee)

        contests = query.order_by(FanDuelContestDB.slate_start).all()

        if not contests:
            print(f"No {sport.value} contests found")
            return

        print(f"\n{'='*90}")
        print(f"FANDUEL {sport.value} CONTESTS")
        print(f"{'='*90}")
        print(f"{'ID':<12} {'Name':<35} {'Fee':>8} {'Max':>6} {'Entries':>8} {'Start':<20}")
        print("-" * 90)

        for contest in contests:
            print(
                f"{contest.id:<12} "
                f"{contest.name[:33]:<35} "
                f"${contest.entry_fee:>6.2f} "
                f"{contest.max_entries or '-':>6} "
                f"{contest.entry_count or 0:>8} "
                f"{contest.slate_start.strftime('%Y-%m-%d %H:%M'):<20}"
            )

        print("-" * 90)
        print(f"Total: {len(contests)} contests")

    finally:
        session.close()


def main():
    """Fetch and display FanDuel contests."""
    parser = argparse.ArgumentParser(description="Fetch FanDuel DFS Contests")

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA", "all"],
        default="NFL",
        help="Sport to fetch (default: NFL)",
    )

    parser.add_argument(
        "--max-fee",
        type=float,
        default=None,
        help="Maximum entry fee filter for display",
    )

    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify authentication, don't fetch contests",
    )

    args = parser.parse_args()

    # Get auth tokens
    auth_token, session_token = get_auth_tokens()

    # Initialize database
    init_database()

    # Create client
    client = FanDuelApiClient(
        basic_auth_token=auth_token,
        x_auth_token=session_token,
    )

    # Verify authentication
    try:
        logger.info("Verifying FanDuel authentication...")
        client.verify_auth()
        logger.info("Authentication verified successfully")

        if args.verify_only:
            print("\nAuthentication successful!")
            return

    except Exception as e:
        logger.error(f"Authentication failed: {e}")
        print("\nAuthentication failed. Your X-Auth-Token may have expired.")
        print("Please refresh tokens from browser dev tools.")
        sys.exit(1)

    # Fetch contests
    try:
        if args.sport == "all":
            sports = [Sport.NFL, Sport.NBA, Sport.MLB, Sport.NHL]
        else:
            sports = [Sport(args.sport)]

        for sport in sports:
            logger.info(f"\nFetching {sport.value} contests...")
            count = fetch_and_store_contests(client, sport)
            logger.info(f"Stored {count} {sport.value} contests")

            display_contests(sport, args.max_fee)

    except Exception as e:
        logger.error(f"Failed to fetch contests: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
