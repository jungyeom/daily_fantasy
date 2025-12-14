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
from datetime import datetime
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


def get_player_pool_for_slate(entries: list[dict], sport: Sport) -> tuple[list[Player], bool]:
    """Fetch player pool for a slate based on entries.

    Uses the contest_id from entries to fetch the player pool from Yahoo API.
    Also determines if the slate has started (some games already in progress).

    Args:
        entries: List of entry dicts from template (contains contest_id)
        sport: Sport enum

    Returns:
        Tuple of (List of Player objects, slate_has_started bool)
    """
    if not entries:
        return [], False

    # Get a contest_id from entries
    contest_id = None
    for entry in entries:
        cid = entry.get("contest_id")
        if cid:
            contest_id = str(cid)
            break

    if not contest_id:
        logger.error("No contest_id found in entries")
        return [], False

    logger.info(f"Fetching player pool for contest {contest_id}")

    api = YahooDFSApiClient()
    now = datetime.now()

    try:
        raw_players = api.get_contest_players(contest_id)

        players = []
        injured_count = 0
        locked_count = 0
        unlocked_count = 0

        for raw in raw_players:
            parsed = parse_api_player(raw, contest_id)

            # Get injury status
            status = parsed.get("status", "")

            # Skip players with INJ or O status (but keep GTD/Q)
            if status in ("INJ", "O"):
                injured_count += 1
                logger.debug(f"Skipping injured player: {parsed['name']} ({status})")
                continue

            # Check if player's game has started
            game_time = parsed.get("game_time")
            is_locked = False
            if game_time and game_time <= now:
                is_locked = True
                locked_count += 1
            else:
                unlocked_count += 1

            player = Player(
                yahoo_player_id=parsed["yahoo_player_id"],
                player_game_code=parsed.get("player_game_code"),
                name=parsed["name"],
                team=parsed["team"],
                position=parsed["position"],
                salary=parsed["salary"],
                injury_status=status if status else None,
                game_time=game_time,
                is_locked=is_locked,
            )
            players.append(player)

        # Slate has started if any games have started
        slate_has_started = locked_count > 0

        logger.info(f"Loaded {len(players)} players (excluded {injured_count} injured/out)")
        logger.info(f"Game status: {locked_count} locked (game started), {unlocked_count} unlocked (upcoming)")

        if slate_has_started:
            logger.info("LATE-SWAP MODE: Slate has started, will only swap unlocked players")
        else:
            logger.info("FULL OPTIMIZATION MODE: Slate has not started, can fully re-optimize")

        return players, slate_has_started

    except Exception as e:
        logger.error(f"Failed to fetch player pool: {e}")
        return [], False


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

    Handles two scenarios:
    1. Slate not started: Full re-optimization with updated projections/injuries
    2. Slate started (late-swap): Only swap players whose games haven't started

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

    # Fetch player pool and determine if late-swap is needed
    players, slate_has_started = get_player_pool_for_slate(entries, sport)

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
        if slate_has_started:
            # LATE-SWAP MODE: Only swap unlocked players
            lineups = generate_late_swap_lineups(
                entries=entries,
                players=players,
                sport=sport,
                is_single_game=is_single_game,
                contest_id=contest_id,
            )
        else:
            # FULL OPTIMIZATION MODE: Generate completely new lineups
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
                locked_marker = " [LOCKED]" if getattr(p, 'is_locked', False) else ""
                logger.info(f"  {p.roster_position:10} {p.name:25} ${p.salary:5} {p.projected_points:.1f}{locked_marker}")

        return lineups

    except Exception as e:
        logger.error(f"Lineup generation failed: {e}")
        import traceback
        traceback.print_exc()
        return []


def generate_late_swap_lineups(
    entries: list[dict],
    players: list[Player],
    sport: Sport,
    is_single_game: bool,
    contest_id: str,
) -> list[Lineup]:
    """Generate lineups for late-swap scenario.

    For each existing entry, keeps locked players and optimizes unlocked positions.

    Args:
        entries: List of entry dicts from template (with current player codes)
        players: Player pool with is_locked flags set
        sport: Sport enum
        is_single_game: Whether this is a single-game contest
        contest_id: Contest ID

    Returns:
        List of Lineup objects with locked players preserved
    """
    logger.info(f"Late-swap: Processing {len(entries)} entries")

    # Create lookup maps
    player_by_code = {p.player_game_code: p for p in players if p.player_game_code}
    player_by_id = {p.yahoo_player_id: p for p in players}

    # Get unlocked players for optimization
    unlocked_players = [p for p in players if not p.is_locked and p.projected_points and p.projected_points > 0]
    locked_players = [p for p in players if p.is_locked]

    logger.info(f"Late-swap pool: {len(locked_players)} locked, {len(unlocked_players)} unlocked available")

    lineups = []

    for entry in entries:
        entry_id = entry.get("entry_id")
        current_players = entry.get("players", {})

        # Parse current lineup from entry
        locked_in_lineup = []
        positions_to_optimize = []
        locked_salary = 0

        for pos, player_code in current_players.items():
            if not player_code:
                positions_to_optimize.append(pos)
                continue

            # Find player by code
            player = player_by_code.get(player_code)
            if not player:
                # Try by ID (extract from code like "nfl.p.12345$nfl.g.xxx")
                player_id = player_code.split("$")[0] if "$" in player_code else player_code
                player = player_by_id.get(player_id)

            if player and player.is_locked:
                # Player's game has started - must keep them
                locked_in_lineup.append((pos, player))
                locked_salary += player.salary
            else:
                # Player's game hasn't started - can swap them
                positions_to_optimize.append(pos)

        logger.debug(f"Entry {entry_id}: {len(locked_in_lineup)} locked, {len(positions_to_optimize)} to optimize")

        # If all positions are locked, keep the lineup as-is
        if not positions_to_optimize:
            lineup = create_lineup_from_entry(entry, players, player_by_code, player_by_id)
            if lineup:
                lineups.append(lineup)
            continue

        # Optimize the unlocked positions
        remaining_salary = 200 - locked_salary  # Yahoo salary cap is $200

        # Create a lineup with locked players and best available for unlocked positions
        lineup = optimize_unlocked_positions(
            locked_players=locked_in_lineup,
            positions_to_optimize=positions_to_optimize,
            available_players=unlocked_players,
            remaining_salary=remaining_salary,
            is_single_game=is_single_game,
            entry_id=entry_id,
            contest_id=contest_id,
        )

        if lineup:
            lineups.append(lineup)

    logger.info(f"Late-swap: Generated {len(lineups)} optimized lineups")
    return lineups


def create_lineup_from_entry(
    entry: dict,
    players: list[Player],
    player_by_code: dict,
    player_by_id: dict,
) -> Optional[Lineup]:
    """Create a Lineup object from an entry dict.

    Args:
        entry: Entry dict from template
        players: Player pool
        player_by_code: Lookup by player_game_code
        player_by_id: Lookup by yahoo_player_id

    Returns:
        Lineup object or None
    """
    lineup_players = []
    total_salary = 0
    total_projected = 0

    for pos, player_code in entry.get("players", {}).items():
        if not player_code:
            continue

        player = player_by_code.get(player_code)
        if not player:
            player_id = player_code.split("$")[0] if "$" in player_code else player_code
            player = player_by_id.get(player_id)

        if player:
            lineup_player = LineupPlayer(
                yahoo_player_id=player.yahoo_player_id,
                player_game_code=player.player_game_code or player.yahoo_player_id,
                name=player.name,
                roster_position=pos.replace("1", "").replace("2", "").replace("3", ""),  # RB1 -> RB
                actual_position=player.position,
                salary=player.salary,
                projected_points=player.projected_points or 0,
            )
            lineup_players.append(lineup_player)
            total_salary += player.salary
            total_projected += player.projected_points or 0

    if not lineup_players:
        return None

    return Lineup(
        series_id=0,  # Placeholder for edit lineups
        players=lineup_players,
        total_salary=total_salary,
        projected_points=total_projected,
        entry_id=entry.get("entry_id"),
        contest_id=entry.get("contest_id"),
    )


def optimize_unlocked_positions(
    locked_players: list[tuple[str, Player]],
    positions_to_optimize: list[str],
    available_players: list[Player],
    remaining_salary: int,
    is_single_game: bool,
    entry_id: str,
    contest_id: str,
) -> Optional[Lineup]:
    """Optimize unlocked positions while keeping locked players.

    Uses a greedy approach to fill unlocked positions with best value players.

    Args:
        locked_players: List of (position, Player) tuples for locked players
        positions_to_optimize: List of position strings to fill
        available_players: Pool of unlocked players
        remaining_salary: Salary cap remaining after locked players
        is_single_game: Whether this is a single-game contest
        entry_id: Entry ID
        contest_id: Contest ID

    Returns:
        Lineup object or None
    """
    lineup_players = []
    used_player_ids = set()

    # Add locked players first
    for pos, player in locked_players:
        base_pos = pos.replace("1", "").replace("2", "").replace("3", "")
        lineup_player = LineupPlayer(
            yahoo_player_id=player.yahoo_player_id,
            player_game_code=player.player_game_code or player.yahoo_player_id,
            name=player.name,
            roster_position=base_pos,
            actual_position=player.position,
            salary=player.salary,
            projected_points=player.projected_points or 0,
        )
        lineup_players.append(lineup_player)
        used_player_ids.add(player.yahoo_player_id)

    # Sort available players by value (points per dollar)
    sorted_players = sorted(
        [p for p in available_players if p.projected_points and p.projected_points > 0],
        key=lambda p: p.projected_points / max(p.salary, 1),
        reverse=True,
    )

    # Fill unlocked positions greedily
    salary_remaining = remaining_salary

    for pos in positions_to_optimize:
        # Determine which positions are eligible for this slot
        base_pos = pos.replace("1", "").replace("2", "").replace("3", "")

        if base_pos == "FLEX":
            # FLEX can be RB, WR, or TE
            eligible_positions = {"RB", "WR", "TE"}
        elif base_pos == "UTIL":
            # UTIL can be any position
            eligible_positions = None  # Any position
        elif is_single_game and base_pos in ("SUPERSTAR", "STAR", "PRO"):
            # Single-game positions can be any player
            eligible_positions = None
        else:
            eligible_positions = {base_pos}

        # Find best available player for this position
        best_player = None
        for player in sorted_players:
            if player.yahoo_player_id in used_player_ids:
                continue
            if player.salary > salary_remaining:
                continue
            if eligible_positions and player.position not in eligible_positions:
                continue

            best_player = player
            break

        if best_player:
            lineup_player = LineupPlayer(
                yahoo_player_id=best_player.yahoo_player_id,
                player_game_code=best_player.player_game_code or best_player.yahoo_player_id,
                name=best_player.name,
                roster_position=base_pos,
                actual_position=best_player.position,
                salary=best_player.salary,
                projected_points=best_player.projected_points or 0,
            )
            lineup_players.append(lineup_player)
            used_player_ids.add(best_player.yahoo_player_id)
            salary_remaining -= best_player.salary
        else:
            logger.warning(f"Could not find player for position {pos}, salary remaining: ${salary_remaining}")

    if len(lineup_players) < len(locked_players) + len(positions_to_optimize):
        logger.warning(f"Incomplete lineup: {len(lineup_players)} players")
        return None

    total_salary = sum(p.salary for p in lineup_players)
    total_projected = sum(p.projected_points for p in lineup_players)

    return Lineup(
        series_id=0,  # Placeholder for edit lineups
        players=lineup_players,
        total_salary=total_salary,
        projected_points=total_projected,
        entry_id=entry_id,
        contest_id=contest_id,
    )


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
