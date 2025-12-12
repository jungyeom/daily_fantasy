#!/usr/bin/env python3
"""Submit maximum lineups to specified NFL contests."""
import argparse
import logging
import sys
from pathlib import Path

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


def fetch_players(api: YahooDFSApiClient, contest_id: str) -> list[Player]:
    """Fetch and parse player pool for a contest."""
    raw_players = api.get_contest_players(contest_id)

    players = []
    for raw in raw_players:
        parsed = parse_api_player(raw, contest_id)

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


def submit_lineups(lineups, contest_id: str, contest_name: str, single_game: bool) -> tuple[int, int]:
    """Submit lineups via browser."""
    from src.yahoo.browser import get_browser_manager
    from src.yahoo.auth import YahooAuth
    from src.yahoo.submission import LineupSubmitter

    browser = get_browser_manager()
    driver = browser.create_driver()

    try:
        logger.info("Authenticating with Yahoo...")
        auth = YahooAuth()
        auth.login(driver)

        logger.info(f"Submitting {len(lineups)} lineup(s) to contest {contest_id}...")
        submitter = LineupSubmitter()
        successful, failed = submitter.submit_lineups(
            driver=driver,
            lineups=lineups,
            contest_id=contest_id,
            sport_name="NFL",
            contest_name=contest_name,
            single_game=single_game,
        )

        return successful, failed

    except Exception as e:
        logger.error(f"Submission error: {e}")
        return 0, len(lineups)
    finally:
        browser.close_driver()


def main():
    parser = argparse.ArgumentParser(description="Submit max lineups to NFL contests")
    parser.add_argument("--contest", type=str, required=True, help="Contest ID")
    parser.add_argument("--num-lineups", type=int, default=None, help="Number of lineups (default: max allowed)")
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't submit")
    parser.add_argument("--single-game", action="store_true", help="Force single-game mode")
    args = parser.parse_args()

    api = YahooDFSApiClient()

    # Get contest info - try both single-game and multi-game endpoints
    logger.info(f"Fetching contest {args.contest} info...")

    # First try to get the contest info directly from the players endpoint
    # since it works regardless of contest type
    contest = None
    contest_name = f"Contest {args.contest}"
    max_entries = 150  # Default max entries
    current_entries = 0

    # Try regular contests first
    contests = api.get_contests(Sport.NFL)
    for c in contests:
        if str(c.get('id')) == args.contest:
            contest = c
            break

    # If not found, try single-game contests via different params
    if not contest:
        logger.info("Contest not found in multi-game list, trying single-game...")
        import requests
        try:
            # Try to get contest info from user entries or directly
            sg_url = f"https://dfyql-ro.sports.yahoo.com/v2/contests?sport=nfl&slateType=SINGLE_GAME"
            response = requests.get(sg_url, timeout=30)
            if response.ok:
                data = response.json()
                sg_contests = data.get("contests", {}).get("result", [])
                logger.info(f"Fetched {len(sg_contests)} single-game contests")
                for c in sg_contests:
                    if str(c.get('id')) == args.contest:
                        contest = c
                        break
        except Exception as e:
            logger.warning(f"Failed to fetch single-game contests: {e}")

    if contest:
        contest_name = contest.get('title', f'Contest {args.contest}')
        max_entries = contest.get('maxEntriesPerUser', 150)
        current_entries = contest.get('userEntryCount', 0)
    else:
        logger.warning(f"Contest {args.contest} not found in API, using defaults (max_entries=150)")
        # We'll proceed anyway if we can fetch players

    logger.info(f"Contest: {contest_name}")
    logger.info(f"Max entries per user: {max_entries}")
    logger.info(f"Current entries: {current_entries}")

    # Calculate how many lineups to generate
    num_lineups = args.num_lineups if args.num_lineups else (max_entries - current_entries)

    if num_lineups <= 0:
        logger.info("Already at max entries!")
        return

    logger.info(f"Need to generate {num_lineups} lineups")

    # Fetch players
    logger.info(f"Fetching players for contest {args.contest}...")
    players = fetch_players(api, args.contest)
    logger.info(f"Fetched {len(players)} players")

    if not players:
        logger.error("No players found")
        return

    # Check if single-game (from flag or detected from teams)
    teams = set(p.team for p in players)
    is_single_game = args.single_game or len(teams) <= 2
    logger.info(f"Teams in slate: {len(teams)} ({', '.join(sorted(teams))})")
    logger.info(f"Game type: {'Single-game' if is_single_game else 'Multi-game'}")

    # Apply projections
    logger.info("Applying projections...")
    players = apply_projections(players)
    matched = sum(1 for p in players if p.projected_points and p.projected_points > 0)
    logger.info(f"Players with projections: {matched}/{len(players)}")

    # Generate lineups
    logger.info(f"Generating {num_lineups} lineup(s)...")
    lineups = generate_lineups(
        players=players,
        num_lineups=num_lineups,
        contest_id=args.contest,
        single_game=is_single_game,
        salary_cap=100 if is_single_game else 200,
    )

    if not lineups:
        logger.error("Failed to generate lineups")
        return

    logger.info(f"Generated {len(lineups)} lineup(s)")

    # Display first few lineups
    for i, lineup in enumerate(lineups[:3], 1):
        print(f"\nLineup {i}:")
        print(f"  Projected: {lineup.projected_points:.1f} pts")
        print(f"  Salary: ${lineup.total_salary}")
        for p in lineup.players:
            print(f"    {p.roster_position:10} {p.name:25} ${p.salary:5} {p.projected_points:.1f}")

    if len(lineups) > 3:
        print(f"\n... and {len(lineups) - 3} more lineup(s)")

    if args.dry_run:
        print("\nDRY RUN - Lineups NOT submitted")
        return

    # Submit
    print(f"\n{'='*60}")
    print(f"SUBMITTING {len(lineups)} LINEUP(S)...")
    print(f"{'='*60}")

    successful, failed = submit_lineups(lineups, args.contest, contest_name, is_single_game)

    print(f"\n{'='*60}")
    print(f"SUBMISSION RESULT")
    print(f"{'='*60}")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
