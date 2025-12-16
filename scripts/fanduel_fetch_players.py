#!/usr/bin/env python3
"""Fetch player pool for a FanDuel fixture list (slate).

This script requires FanDuel auth tokens to be configured.
See src/fanduel/api.py for instructions on how to extract tokens.

Usage:
    # Set auth tokens via environment variables
    export FANDUEL_AUTH_TOKEN="Basic ..."
    export FANDUEL_SESSION_TOKEN="..."

    # Fetch players for a specific fixture list
    python scripts/fanduel_fetch_players.py --fixture-list-id 12345
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
    FanDuelPlayerPoolDB,
)
from src.fanduel.api import (
    FanDuelApiClient,
    parse_player,
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


def fetch_and_store_players(
    client: FanDuelApiClient,
    fixture_list_id: int,
) -> int:
    """Fetch players from FanDuel API and store in database.

    Args:
        client: Authenticated FanDuel API client
        fixture_list_id: FanDuel fixture list ID

    Returns:
        Number of players stored
    """
    db = get_database()
    session = db.get_session()
    player_count = 0

    try:
        # Verify fixture list exists
        fixture_list = session.query(FanDuelFixtureListDB).get(fixture_list_id)
        if not fixture_list:
            logger.warning(
                f"Fixture list {fixture_list_id} not in database. "
                "Run fanduel_fetch_contests.py first."
            )

        # Fetch players
        players_raw = client.get_players(fixture_list_id)
        logger.info(f"Fetched {len(players_raw)} players for fixture list {fixture_list_id}")

        for player_raw in players_raw:
            player = parse_player(player_raw, fixture_list_id)

            # Check for existing player
            existing = (
                session.query(FanDuelPlayerPoolDB)
                .filter(
                    FanDuelPlayerPoolDB.fixture_list_id == fixture_list_id,
                    FanDuelPlayerPoolDB.fanduel_player_id == player["fanduel_player_id"],
                )
                .first()
            )

            if not existing:
                player_db = FanDuelPlayerPoolDB(
                    fixture_list_id=fixture_list_id,
                    fanduel_player_id=player["fanduel_player_id"],
                    name=player["name"],
                    first_name=player["first_name"],
                    last_name=player["last_name"],
                    team=player["team"],
                    team_name=player["team_name"],
                    position=player["position"],
                    salary=player["salary"],
                    fppg=player["fppg"],
                    game_id=player["game_id"],
                    injury_status=player["injury_status"],
                    injury_details=player["injury_details"],
                )
                session.add(player_db)
            else:
                # Update existing player
                existing.salary = player["salary"]
                existing.fppg = player["fppg"]
                existing.injury_status = player["injury_status"]
                existing.injury_details = player["injury_details"]

            player_count += 1

        session.commit()

    except Exception as e:
        session.rollback()
        logger.error(f"Error fetching players: {e}")
        raise
    finally:
        session.close()

    return player_count


def display_players(fixture_list_id: int, position: str = None):
    """Display fetched FanDuel players.

    Args:
        fixture_list_id: Fixture list ID
        position: Optional position filter
    """
    db = get_database()
    session = db.get_session()

    try:
        query = (
            session.query(FanDuelPlayerPoolDB)
            .filter(FanDuelPlayerPoolDB.fixture_list_id == fixture_list_id)
        )

        if position:
            query = query.filter(FanDuelPlayerPoolDB.position == position.upper())

        players = query.order_by(
            FanDuelPlayerPoolDB.salary.desc()
        ).all()

        if not players:
            print(f"No players found for fixture list {fixture_list_id}")
            return

        print(f"\n{'='*80}")
        print(f"FANDUEL PLAYERS - Fixture List {fixture_list_id}")
        print(f"{'='*80}")
        print(f"{'Name':<25} {'Pos':<5} {'Team':<5} {'Salary':>8} {'FPPG':>8} {'Status':<10}")
        print("-" * 80)

        for player in players:
            status = player.injury_status or ""
            print(
                f"{player.name[:23]:<25} "
                f"{player.position:<5} "
                f"{player.team:<5} "
                f"${player.salary:>7,} "
                f"{player.fppg or 0:>8.1f} "
                f"{status:<10}"
            )

        print("-" * 80)
        print(f"Total: {len(players)} players")

    finally:
        session.close()


def list_fixture_lists(sport: str = None):
    """List available fixture lists from database.

    Args:
        sport: Optional sport filter
    """
    db = get_database()
    session = db.get_session()

    try:
        query = session.query(FanDuelFixtureListDB)

        if sport:
            query = query.filter(FanDuelFixtureListDB.sport == sport.upper())

        query = query.filter(FanDuelFixtureListDB.slate_start > datetime.now())
        fixture_lists = query.order_by(FanDuelFixtureListDB.slate_start).all()

        if not fixture_lists:
            print("No fixture lists found. Run fanduel_fetch_contests.py first.")
            return

        print(f"\n{'='*70}")
        print("AVAILABLE FIXTURE LISTS")
        print(f"{'='*70}")
        print(f"{'ID':<12} {'Sport':<6} {'Label':<30} {'Start':<20}")
        print("-" * 70)

        for fl in fixture_lists:
            print(
                f"{fl.id:<12} "
                f"{fl.sport:<6} "
                f"{fl.label[:28]:<30} "
                f"{fl.slate_start.strftime('%Y-%m-%d %H:%M'):<20}"
            )

        print("-" * 70)
        print(f"Total: {len(fixture_lists)} fixture lists")

    finally:
        session.close()


def main():
    """Fetch and display FanDuel players."""
    parser = argparse.ArgumentParser(description="Fetch FanDuel Player Pool")

    parser.add_argument(
        "--fixture-list-id",
        type=int,
        help="FanDuel fixture list ID to fetch players for",
    )

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA"],
        help="Filter fixture lists by sport (for --list)",
    )

    parser.add_argument(
        "--position",
        type=str,
        help="Filter displayed players by position (e.g., QB, RB)",
    )

    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_fixtures",
        help="List available fixture lists instead of fetching players",
    )

    args = parser.parse_args()

    # Initialize database
    init_database()

    # If just listing fixture lists, no auth needed
    if args.list_fixtures:
        list_fixture_lists(args.sport)
        return

    # Require fixture list ID for fetching players
    if not args.fixture_list_id:
        print("Error: --fixture-list-id required")
        print("Use --list to see available fixture lists")
        sys.exit(1)

    # Get auth tokens
    auth_token, session_token = get_auth_tokens()

    # Create client
    client = FanDuelApiClient(
        basic_auth_token=auth_token,
        x_auth_token=session_token,
    )

    # Fetch players
    try:
        logger.info(f"Fetching players for fixture list {args.fixture_list_id}...")
        count = fetch_and_store_players(client, args.fixture_list_id)
        logger.info(f"Stored {count} players")

        display_players(args.fixture_list_id, args.position)

    except Exception as e:
        logger.error(f"Failed to fetch players: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
