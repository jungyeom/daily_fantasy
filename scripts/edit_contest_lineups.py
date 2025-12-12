#!/usr/bin/env python3
"""Script to regenerate and edit contest lineups with updated projections.

This script:
1. Fetches the player pool for a contest
2. Applies updated projections (with INJ filter)
3. Generates new optimized lineups
4. Uses LineupEditor to update existing entries via Yahoo's CSV edit endpoint

Usage:
    python scripts/edit_contest_lineups.py           # Generate only (no browser)
    python scripts/edit_contest_lineups.py --edit   # Generate and edit via browser
"""

import argparse
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.models import Sport, Player
from src.yahoo.api import YahooDFSApiClient, parse_api_player, parse_api_contest
from src.projections.aggregator import ProjectionAggregator
from src.optimizer.builder import LineupBuilder
from src.yahoo.editor import LineupEditor
from src.yahoo.browser import BrowserManager
from src.yahoo.auth import YahooAuth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def fetch_players_for_contest(api: YahooDFSApiClient, contest_id: str) -> list[Player]:
    """Fetch and parse player pool for a contest, excluding INJ players."""
    raw_players = api.get_contest_players(contest_id)

    players = []
    injured_count = 0

    for raw in raw_players:
        parsed = parse_api_player(raw, contest_id)

        # Skip players with INJ status
        status = parsed.get("status", "")
        if status == "INJ":
            injured_count += 1
            logger.debug(f"Skipping INJ player: {parsed['name']}")
            continue

        player = Player(
            yahoo_player_id=parsed["yahoo_player_id"],
            player_game_code=parsed.get("player_game_code"),
            name=parsed["name"],
            team=parsed["team"],
            position=parsed["position"],
            salary=parsed["salary"],
            status=status,
        )
        players.append(player)

    logger.info(f"Loaded {len(players)} players (excluded {injured_count} INJ players)")
    return players


def apply_projections(players: list[Player], sport: Sport) -> list[Player]:
    """Apply external projections to players using the aggregator."""
    aggregator = ProjectionAggregator()

    # get_projections_for_contest fetches, aggregates, and merges projections
    players_with_projections = aggregator.get_projections_for_contest(sport, players)

    # Count how many have projections
    matched = sum(1 for p in players_with_projections if p.projected_points and p.projected_points > 0)
    logger.info(f"Matched {matched}/{len(players_with_projections)} players with projections")

    return players_with_projections


def generate_lineups_for_contest(
    contest_id: str,
    sport: Sport,
    single_game: bool,
    salary_cap: int,
    num_lineups: int,
):
    """Generate new lineups for a contest with updated projections."""
    api = YahooDFSApiClient()

    # Fetch players
    players = fetch_players_for_contest(api, contest_id)

    if not players:
        logger.error(f"No players found for contest {contest_id}")
        return []

    # Apply projections
    players = apply_projections(players, sport)

    # Log some player projections for verification
    players_with_proj = [p for p in players if p.projected_points and p.projected_points > 0]
    logger.info(f"Players with valid projections: {len(players_with_proj)}")

    # Show top 10 by projection
    top_players = sorted(players_with_proj, key=lambda p: p.projected_points or 0, reverse=True)[:10]
    logger.info("Top 10 players by projection:")
    for p in top_players:
        logger.info(f"  {p.name} ({p.position}) - {p.projected_points:.1f} pts, ${p.salary}")

    # Build lineups
    builder = LineupBuilder(sport, single_game=single_game, salary_cap=salary_cap)
    lineups = builder.build_lineups(
        players=players,
        num_lineups=num_lineups,
        contest_id=contest_id,
        save_to_db=False,  # Don't save yet
    )

    logger.info(f"Generated {len(lineups)} lineups")

    # Show first lineup
    if lineups:
        lineup = lineups[0]
        logger.info(f"Sample lineup - Projected: {lineup.projected_points:.1f} pts, Salary: ${lineup.total_salary}")
        for p in lineup.players:
            logger.info(f"  {p.roster_position:10} {p.name:25} ${p.salary:5} {p.projected_points:.1f}")

    return lineups


def edit_lineups_with_browser(
    contest_id: str,
    lineups: list,
    sport: str = "nba",
    contest_start_time=None,
    contest_title: str = None,
):
    """Edit lineups using browser automation.

    Args:
        contest_id: Yahoo contest ID
        lineups: List of Lineup objects to submit as edits
        sport: Sport code for editor
        contest_start_time: Contest start time for slate matching
        contest_title: Contest title
    """
    browser_manager = BrowserManager()
    auth = YahooAuth()
    editor = LineupEditor()

    try:
        # Create browser
        driver = browser_manager.create_driver()

        # Authenticate
        logger.info("Authenticating with Yahoo...")
        auth.login(driver)

        # Edit lineups
        logger.info(f"Editing {len(lineups)} lineups for contest {contest_id}...")
        result = editor.edit_lineups_for_contest(
            driver=driver,
            contest_id=contest_id,
            lineups=lineups,
            sport=sport,
            contest_start_time=contest_start_time,
            contest_title=contest_title,
        )

        if result["success"]:
            logger.info(f"Successfully edited {result['edited_count']} lineups!")
        else:
            logger.error(f"Edit failed: {result['message']}")

        return result

    except Exception as e:
        logger.error(f"Browser editing failed: {e}")
        return {"success": False, "message": str(e), "edited_count": 0}
    finally:
        browser_manager.close_driver()


def main():
    """Main function to regenerate and edit lineups."""
    parser = argparse.ArgumentParser(description="Edit Yahoo DFS lineups with updated projections")
    parser.add_argument("--edit", action="store_true", help="Actually edit lineups via browser")
    parser.add_argument("--contest", type=str, help="Specific contest ID to edit")
    args = parser.parse_args()

    # Contest IDs from earlier submissions
    contests = [
        {"id": "15255304", "entries": 25},  # $6.25 entry fee
        {"id": "15255305", "entries": 34},  # $17.00 entry fee
    ]

    # Filter to specific contest if requested
    if args.contest:
        contests = [c for c in contests if c["id"] == args.contest]
        if not contests:
            logger.error(f"Contest {args.contest} not in predefined list")
            return

    sport = Sport.NBA
    single_game = False  # These are multi-game slates
    salary_cap = 200  # Yahoo NBA salary cap

    api = YahooDFSApiClient()

    # First, let's get contest info to verify they're still editable
    all_contests = api.get_contests(sport)

    all_lineups = {}  # Store generated lineups by contest_id
    contest_info_map = {}  # Store contest info for editing

    for contest_info in contests:
        contest_id = contest_info["id"]
        num_lineups = contest_info["entries"]

        # Try to find this contest in API for metadata
        contest_data = None
        for c in all_contests:
            if str(c.get("id")) == contest_id:
                contest_data = c
                break

        # Use API data if available, otherwise use defaults
        # (Paid contests may not appear in public API)
        if contest_data:
            parsed = parse_api_contest(contest_data)
            contest_name = parsed["name"]
            entry_fee = parsed["entry_fee"]
            start_time = parsed["slate_start"]
        else:
            # Default values for paid contests not in public API
            logger.info(f"Contest {contest_id} not in public API (likely paid contest)")
            contest_name = f"Contest {contest_id}"
            entry_fee = contest_info.get("fee", "N/A")
            # Default to 7pm EST today for NBA
            from datetime import datetime
            start_time = datetime.now().replace(hour=19, minute=0, second=0, microsecond=0)

        logger.info(f"\n{'='*60}")
        logger.info(f"Contest: {contest_name}")
        logger.info(f"ID: {contest_id}")
        logger.info(f"Entry Fee: ${entry_fee}")
        logger.info(f"Start Time: {start_time}")
        logger.info(f"Entries to edit: {num_lineups}")
        logger.info(f"{'='*60}\n")

        # Generate new lineups (player pool API works for any contest)
        lineups = generate_lineups_for_contest(
            contest_id=contest_id,
            sport=sport,
            single_game=single_game,
            salary_cap=salary_cap,
            num_lineups=num_lineups,
        )

        if not lineups:
            logger.error(f"Failed to generate lineups for contest {contest_id}")
            continue

        logger.info(f"Generated {len(lineups)} new lineups for contest {contest_id}")
        all_lineups[contest_id] = lineups
        contest_info_map[contest_id] = {
            "start_time": start_time,
            "title": contest_name,
        }

    # If --edit flag is set, perform browser-based editing
    if args.edit:
        if not all_lineups:
            logger.error("No lineups generated to edit")
            return

        logger.info("\n" + "="*60)
        logger.info("Starting browser-based lineup editing...")
        logger.info("="*60 + "\n")

        for contest_id, lineups in all_lineups.items():
            info = contest_info_map.get(contest_id, {})
            result = edit_lineups_with_browser(
                contest_id=contest_id,
                lineups=lineups,
                sport="nba",
                contest_start_time=info.get("start_time"),
                contest_title=info.get("title"),
            )
            if result["success"]:
                logger.info(f"Contest {contest_id}: {result['edited_count']} lineups edited")
            else:
                logger.error(f"Contest {contest_id}: Edit failed - {result['message']}")
    else:
        logger.info("\n" + "="*60)
        logger.info("DRY RUN - Lineups generated but NOT edited")
        logger.info("To actually edit lineups, run with --edit flag:")
        logger.info("  python scripts/edit_contest_lineups.py --edit")
        logger.info("="*60)


if __name__ == "__main__":
    main()
