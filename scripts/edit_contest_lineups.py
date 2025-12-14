#!/usr/bin/env python3
"""Script to regenerate and edit contest lineups with updated projections.

This script:
1. Logs into Yahoo and navigates to the CSV edit page
2. Discovers all slates with entries for the specified sport
3. For each slate with entries:
   a. Downloads the template to get current entries
   b. Fetches updated projections from DailyFantasyFuel
   c. Checks injury status from Yahoo API
   d. Re-optimizes lineups
   e. Generates and uploads the edit CSV

Usage:
    python scripts/edit_contest_lineups.py --sport nfl           # Dry run (no upload)
    python scripts/edit_contest_lineups.py --sport nfl --edit   # Actually edit lineups
    python scripts/edit_contest_lineups.py --sport nba --edit   # Edit NBA lineups
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.models import Sport, Player, Lineup, LineupPlayer, LineupStatus
from src.yahoo.api import YahooDFSApiClient, parse_api_player
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


# Map sport strings to Sport enum
SPORT_MAP = {
    "nfl": Sport.NFL,
    "nba": Sport.NBA,
    "mlb": Sport.MLB,
    "nhl": Sport.NHL,
}


def get_player_pool_for_slate(entries: list[dict], sport: Sport) -> list[Player]:
    """Fetch player pool for a slate based on entries.

    Uses the contest_id from entries to fetch the player pool from Yahoo API.

    Args:
        entries: List of entry dicts from template (contains contest_id)
        sport: Sport enum

    Returns:
        List of Player objects
    """
    if not entries:
        return []

    # Get a contest_id from entries
    contest_id = None
    for entry in entries:
        cid = entry.get("contest_id")
        if cid:
            contest_id = str(cid)
            break

    if not contest_id:
        logger.error("No contest_id found in entries")
        return []

    logger.info(f"Fetching player pool for contest {contest_id}")

    api = YahooDFSApiClient()

    try:
        raw_players = api.get_contest_players(contest_id)

        players = []
        injured_count = 0

        for raw in raw_players:
            parsed = parse_api_player(raw, contest_id)

            # Get injury status
            status = parsed.get("status", "")

            # Skip players with INJ or O status (but keep GTD/Q)
            if status in ("INJ", "O"):
                injured_count += 1
                logger.debug(f"Skipping injured player: {parsed['name']} ({status})")
                continue

            player = Player(
                yahoo_player_id=parsed["yahoo_player_id"],
                player_game_code=parsed.get("player_game_code"),
                name=parsed["name"],
                team=parsed["team"],
                position=parsed["position"],
                salary=parsed["salary"],
                injury_status=status if status else None,
            )
            players.append(player)

        logger.info(f"Loaded {len(players)} players (excluded {injured_count} injured/out players)")
        return players

    except Exception as e:
        logger.error(f"Failed to fetch player pool: {e}")
        return []


def apply_projections(players: list[Player], sport: Sport) -> list[Player]:
    """Apply external projections to players using the aggregator.

    Args:
        players: List of Player objects
        sport: Sport enum

    Returns:
        Players with projections merged
    """
    if not players:
        return []

    aggregator = ProjectionAggregator()

    # get_projections_for_contest fetches, aggregates, and merges projections
    players_with_projections = aggregator.get_projections_for_contest(sport, players)

    # Count how many have projections
    matched = sum(1 for p in players_with_projections if p.projected_points and p.projected_points > 0)
    logger.info(f"Matched {matched}/{len(players_with_projections)} players with projections")

    # Log top players by projection
    top_players = sorted(
        [p for p in players_with_projections if p.projected_points],
        key=lambda p: p.projected_points or 0,
        reverse=True
    )[:10]

    if top_players:
        logger.info("Top 10 players by projection:")
        for p in top_players:
            injury = f" [{p.injury_status}]" if p.injury_status else ""
            logger.info(f"  {p.name} ({p.position}) - {p.projected_points:.1f} pts, ${p.salary}{injury}")

    return players_with_projections


def generate_lineups_for_entries(
    entries: list[dict],
    sport_str: str,
) -> list[Lineup]:
    """Generate optimized lineups for a set of entries.

    This is the lineup generator function passed to edit_all_slates().

    Args:
        entries: List of entry dicts from template
        sport_str: Sport string (nfl, nba, etc.)

    Returns:
        List of Lineup objects with optimized players
    """
    sport = SPORT_MAP.get(sport_str.lower())
    if not sport:
        logger.error(f"Unknown sport: {sport_str}")
        return []

    num_lineups = len(entries)
    logger.info(f"Generating {num_lineups} optimized lineups for {sport.value}")

    # Get a contest_id for the player pool
    contest_id = None
    for entry in entries:
        cid = entry.get("contest_id")
        if cid:
            contest_id = str(cid)
            break

    if not contest_id:
        logger.error("No contest_id found in entries")
        return []

    # Fetch player pool
    players = get_player_pool_for_slate(entries, sport)

    if not players:
        logger.error("No players found for slate")
        return []

    # Apply projections
    players = apply_projections(players, sport)

    # Check if we have enough players with projections
    players_with_proj = [p for p in players if p.projected_points and p.projected_points > 0]
    if len(players_with_proj) < 8:
        logger.error(f"Not enough players with projections: {len(players_with_proj)}")
        return []

    # Determine if single-game or multi-game
    # Check roster positions in entries to determine format
    if entries:
        roster_positions = list(entries[0].get("players", {}).keys())
        is_single_game = "SUPERSTAR" in roster_positions or len(roster_positions) <= 5
    else:
        is_single_game = False

    # Build lineups
    try:
        builder = LineupBuilder(
            sport=sport,
            single_game=is_single_game,
            salary_cap=200,  # Yahoo uses $200 cap
        )

        lineups = builder.build_lineups(
            players=players,
            num_lineups=num_lineups,
            contest_id=contest_id,
            save_to_db=False,  # Don't save to DB during edit
        )

        logger.info(f"Generated {len(lineups)} lineups")

        # Log sample lineup
        if lineups:
            lineup = lineups[0]
            logger.info(f"Sample lineup - Projected: {lineup.projected_points:.1f} pts, Salary: ${lineup.total_salary}")
            for p in lineup.players:
                logger.info(f"  {p.roster_position:10} {p.name:25} ${p.salary:5} {p.projected_points:.1f}")

        return lineups

    except Exception as e:
        logger.error(f"Lineup generation failed: {e}")
        return []


def run_discovery_only(sport: str) -> list[dict]:
    """Run discovery to find all slates with entries (no editing).

    Args:
        sport: Sport code

    Returns:
        List of slate info dicts
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

        # Discover slates
        logger.info(f"Discovering {sport.upper()} slates with entries...")
        slates = editor.discover_all_slates(driver, sport)

        if slates:
            logger.info(f"\nFound {len(slates)} slates with entries:")
            for i, slate in enumerate(slates, 1):
                logger.info(f"\n  Slate {i}: {slate['slate_text']}")
                logger.info(f"    Entries: {len(slate['entries'])}")
                logger.info(f"    Contests: {slate['contest_ids']}")
        else:
            logger.info("No slates with entries found")

        return slates

    except Exception as e:
        logger.error(f"Discovery failed: {e}")
        return []
    finally:
        browser_manager.close_driver()


def run_edit_all_slates(sport: str) -> dict:
    """Run full edit flow for all slates.

    Args:
        sport: Sport code

    Returns:
        Results dict
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

        # Run edit for all slates
        logger.info(f"Editing all {sport.upper()} slates with entries...")

        results = editor.edit_all_slates(
            driver=driver,
            sport=sport,
            lineup_generator=generate_lineups_for_entries,
        )

        return results

    except Exception as e:
        logger.error(f"Edit failed: {e}")
        return {
            "success": False,
            "message": str(e),
            "slates_processed": 0,
            "total_entries_edited": 0,
        }
    finally:
        browser_manager.close_driver()


def main():
    """Main function to discover and edit lineups."""
    parser = argparse.ArgumentParser(
        description="Edit Yahoo DFS lineups with updated projections"
    )
    parser.add_argument(
        "--sport",
        type=str,
        default="nfl",
        choices=["nfl", "nba", "mlb", "nhl"],
        help="Sport to edit lineups for (default: nfl)"
    )
    parser.add_argument(
        "--edit",
        action="store_true",
        help="Actually edit lineups (without this flag, only discovery is performed)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    sport = args.sport.lower()

    if args.edit:
        logger.info(f"\n{'='*60}")
        logger.info(f"EDIT MODE - Will modify lineups on Yahoo")
        logger.info(f"Sport: {sport.upper()}")
        logger.info(f"{'='*60}\n")

        results = run_edit_all_slates(sport)

        if results["success"]:
            logger.info(f"\nEdit completed successfully!")
            logger.info(f"  Slates processed: {results.get('slates_processed', 0)}")
            logger.info(f"  Total entries edited: {results.get('total_entries_edited', 0)}")
        else:
            logger.error(f"\nEdit failed: {results.get('message', 'Unknown error')}")
            sys.exit(1)
    else:
        logger.info(f"\n{'='*60}")
        logger.info(f"DISCOVERY MODE - Will NOT modify lineups")
        logger.info(f"Sport: {sport.upper()}")
        logger.info(f"Run with --edit to actually edit lineups")
        logger.info(f"{'='*60}\n")

        slates = run_discovery_only(sport)

        if slates:
            # Show what would be edited
            total_entries = sum(len(s["entries"]) for s in slates)
            logger.info(f"\nWould edit {total_entries} entries across {len(slates)} slates")
            logger.info(f"\nTo actually edit, run:")
            logger.info(f"  python scripts/edit_contest_lineups.py --sport {sport} --edit")
        else:
            logger.info("\nNo entries to edit")


if __name__ == "__main__":
    main()
