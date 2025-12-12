#!/usr/bin/env python3
"""Manual NFL pipeline script - runs through the complete flow.

This script:
1. Fetches available NFL contests for today
2. Selects target contests (free contests for testing, or specify by ID)
3. Fetches player pool and projections
4. Generates optimized lineups
5. Submits to Yahoo via browser

Usage:
    python scripts/run_nfl_pipeline.py                     # Show available contests
    python scripts/run_nfl_pipeline.py --contest 15262909  # Target specific contest
    python scripts/run_nfl_pipeline.py --free              # Target free contests
    python scripts/run_nfl_pipeline.py --submit            # Actually submit (default is dry-run)
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.models import Sport, Player
from src.yahoo.api import YahooDFSApiClient, parse_api_player
from src.projections.aggregator import ProjectionAggregator
from src.optimizer.builder import LineupBuilder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def fetch_contests(api: YahooDFSApiClient, free_only: bool = False) -> list:
    """Fetch available NFL contests for today."""
    contests = api.get_contests(Sport.NFL)
    today = datetime.now().date()

    today_contests = []
    for c in contests:
        start_ts = c.get('startTime', 0)
        entry_fee = c.get('entryFee', 0)

        if start_ts:
            start_dt = datetime.fromtimestamp(start_ts / 1000)
            if start_dt.date() == today:
                if free_only and entry_fee > 0:
                    continue
                today_contests.append({
                    'id': str(c.get('id')),
                    'name': c.get('title', 'Unknown'),
                    'entry_fee': entry_fee / 100,  # Convert cents to dollars
                    'max_entries': c.get('maxEntriesPerUser', 1),
                    'current_entries': c.get('entryCount', 0),
                    'max_total': c.get('maxEntries', 0),
                    'prize_pool': c.get('prizePool', 0) / 100,
                    'start_time': start_dt,
                    'raw': c,
                })

    # Sort by start time
    today_contests.sort(key=lambda x: x['start_time'])
    return today_contests


def display_contests(contests: list) -> None:
    """Display available contests."""
    print(f"\n{'='*80}")
    print(f"Available NFL Contests for Today ({len(contests)} total)")
    print(f"{'='*80}\n")

    for c in contests[:30]:
        fee_str = f"${c['entry_fee']:.2f}" if c['entry_fee'] > 0 else "FREE"
        print(f"ID: {c['id']}")
        print(f"  Name: {c['name']}")
        print(f"  Entry Fee: {fee_str}")
        print(f"  Prize Pool: ${c['prize_pool']:.2f}")
        print(f"  Entries: {c['current_entries']}/{c['max_total']}")
        print(f"  Max per user: {c['max_entries']}")
        print(f"  Start: {c['start_time'].strftime('%I:%M %p')}")
        print()


def fetch_players(api: YahooDFSApiClient, contest_id: str) -> list[Player]:
    """Fetch and parse player pool for a contest."""
    raw_players = api.get_contest_players(contest_id)

    players = []
    for raw in raw_players:
        parsed = parse_api_player(raw, contest_id)

        # Skip injured players
        status = parsed.get('status', '')
        if status == 'INJ':
            continue

        player = Player(
            yahoo_player_id=parsed['yahoo_player_id'],
            player_game_code=parsed.get('player_game_code'),
            name=parsed['name'],
            team=parsed['team'],
            position=parsed['position'],
            salary=parsed['salary'],
            status=status,
        )
        players.append(player)

    return players


def apply_projections(players: list[Player]) -> list[Player]:
    """Apply external projections to players."""
    aggregator = ProjectionAggregator()
    return aggregator.get_projections_for_contest(Sport.NFL, players)


def generate_lineups(
    players: list[Player],
    num_lineups: int = 1,
    contest_id: str = None,
    single_game: bool = False,
    salary_cap: int = 200,
) -> list:
    """Generate optimized lineups."""
    # Filter to players with projections
    players_with_proj = [p for p in players if p.projected_points and p.projected_points > 0]

    if not players_with_proj:
        logger.error("No players with projections found")
        return []

    builder = LineupBuilder(Sport.NFL, single_game=single_game, salary_cap=salary_cap)

    lineups = builder.build_lineups(
        players=players_with_proj,
        num_lineups=num_lineups,
        contest_id=contest_id,
        save_to_db=False,
    )

    return lineups


def display_lineup(lineup, index: int = 1) -> None:
    """Display a lineup."""
    print(f"\nLineup {index}:")
    print(f"  Projected: {lineup.projected_points:.1f} pts")
    print(f"  Salary: ${lineup.total_salary}")
    print(f"  Players:")
    for p in lineup.players:
        print(f"    {p.roster_position:8} {p.name:25} ${p.salary:5} {p.projected_points:.1f}")


def submit_lineup(lineup, contest_id: str, contest_name: str) -> bool:
    """Submit lineup via browser."""
    from src.yahoo.browser import get_browser_manager
    from src.yahoo.auth import YahooAuth
    from src.yahoo.submission import LineupSubmitter

    browser = get_browser_manager()
    driver = browser.create_driver()

    try:
        # Authenticate
        logger.info("Authenticating with Yahoo...")
        auth = YahooAuth()
        auth.login(driver)

        # Submit
        logger.info(f"Submitting lineup to contest {contest_id}...")
        submitter = LineupSubmitter()
        successful, failed = submitter.submit_lineups(
            driver=driver,
            lineups=[lineup],
            contest_id=contest_id,
            sport_name="NFL",
            contest_name=contest_name,
            single_game=False,
        )

        if successful > 0:
            logger.info(f"Successfully submitted {successful} lineup(s)!")
            return True
        else:
            logger.error(f"Submission failed: {failed} lineup(s) failed")
            return False

    except Exception as e:
        logger.error(f"Submission error: {e}")
        return False
    finally:
        browser.close_driver()


def main():
    parser = argparse.ArgumentParser(description="Run NFL DFS pipeline manually")
    parser.add_argument("--contest", type=str, help="Specific contest ID to target")
    parser.add_argument("--free", action="store_true", help="Target free contests only")
    parser.add_argument("--submit", action="store_true", help="Actually submit (default is dry-run)")
    parser.add_argument("--num-lineups", type=int, default=1, help="Number of lineups to generate")
    args = parser.parse_args()

    api = YahooDFSApiClient()

    # Step 1: Fetch contests
    logger.info("Fetching NFL contests...")
    contests = fetch_contests(api, free_only=args.free)

    if not contests:
        logger.error("No NFL contests found for today")
        return

    # If no contest specified, just display and exit
    if not args.contest and not args.free:
        display_contests(contests)
        print("Use --contest <ID> to target a specific contest")
        print("Use --free to target free contests")
        return

    # Step 2: Select contest
    if args.contest:
        contest = next((c for c in contests if c['id'] == args.contest), None)
        if not contest:
            logger.error(f"Contest {args.contest} not found in today's contests")
            return
        selected_contests = [contest]
    else:
        # Use free contests
        selected_contests = [c for c in contests if c['entry_fee'] == 0][:1]  # Just first one for now

    if not selected_contests:
        logger.error("No contests selected")
        return

    for contest in selected_contests:
        print(f"\n{'='*80}")
        print(f"Processing Contest: {contest['name']}")
        print(f"ID: {contest['id']}")
        print(f"Entry Fee: ${contest['entry_fee']:.2f}")
        print(f"Start: {contest['start_time'].strftime('%I:%M %p')}")
        print(f"{'='*80}")

        # Step 3: Fetch players
        logger.info(f"Fetching players for contest {contest['id']}...")
        players = fetch_players(api, contest['id'])
        logger.info(f"Fetched {len(players)} players")

        if not players:
            logger.error("No players found")
            continue

        # Check teams in slate
        teams = set(p.team for p in players)
        is_single_game = len(teams) <= 2
        logger.info(f"Teams in slate: {len(teams)} ({', '.join(sorted(teams))})")
        logger.info(f"Game type: {'Single-game' if is_single_game else 'Multi-game'}")

        # Step 4: Apply projections
        logger.info("Applying projections...")
        players = apply_projections(players)
        matched = sum(1 for p in players if p.projected_points and p.projected_points > 0)
        logger.info(f"Players with projections: {matched}/{len(players)}")

        # Show top 10 projected
        top = sorted([p for p in players if p.projected_points],
                     key=lambda p: p.projected_points, reverse=True)[:10]
        print("\nTop 10 Projected Players:")
        for p in top:
            print(f"  {p.name:25} {p.position:5} {p.team:4} ${p.salary:5} {p.projected_points:.1f}")

        # Step 5: Generate lineups
        num_lineups = min(args.num_lineups, contest['max_entries'])
        logger.info(f"Generating {num_lineups} lineup(s)...")

        lineups = generate_lineups(
            players=players,
            num_lineups=num_lineups,
            contest_id=contest['id'],
            single_game=is_single_game,
            salary_cap=200,  # Yahoo NFL salary cap
        )

        if not lineups:
            logger.error("Failed to generate lineups")
            continue

        logger.info(f"Generated {len(lineups)} lineup(s)")

        # Display lineups
        for i, lineup in enumerate(lineups[:3], 1):  # Show first 3
            display_lineup(lineup, i)

        if len(lineups) > 3:
            print(f"\n... and {len(lineups) - 3} more lineup(s)")

        # Step 6: Submit
        if args.submit:
            print(f"\n{'='*80}")
            print("SUBMITTING LINEUP(S)...")
            print(f"{'='*80}")

            for lineup in lineups:
                success = submit_lineup(lineup, contest['id'], contest['name'])
                if success:
                    print("Submission successful!")
                else:
                    print("Submission failed!")
                    break
        else:
            print(f"\n{'='*80}")
            print("DRY RUN - Lineups NOT submitted")
            print("Use --submit to actually submit lineup(s)")
            print(f"{'='*80}")


if __name__ == "__main__":
    main()
