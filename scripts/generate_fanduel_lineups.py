#!/usr/bin/env python3
"""Generate optimized lineups for FanDuel contests.

Uses DailyFantasyFuel projections combined with actual FanDuel player
salaries fetched from the FanDuel API.

Usage:
    # Manual mode - specify contest details
    python scripts/generate_fanduel_lineups.py --sport NHL --num-lineups 3
    python scripts/generate_fanduel_lineups.py --sport NHL --fixture-list-id 124451 --num-lineups 3

    # Auto mode - find eligible contests and generate max lineups for each
    python scripts/generate_fanduel_lineups.py --sport NHL --auto-select-contest
"""
import argparse
import csv
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from pydfs_lineup_optimizer import Site, Sport as PDFSSport, get_optimizer, PlayersGroup, TeamStack, PositionsStack
from pydfs_lineup_optimizer.player import Player as PDFSPlayer, GameInfo

from src.common.models import Sport
from src.contests.selector import ContestSelector
from src.fanduel.api import FanDuelApiClient, parse_fixture_list, parse_player, parse_contest
from src.projections.sources.dailyfantasyfuel import DailyFantasyFuelSource
from src.projections.vegas_lines import (
    fetch_nhl_odds,
    filter_low_total_teams,
    create_vegas_strategy,
    print_odds_summary,
    get_game_totals,
    get_favorites,
)


# NHL Stacking Configuration
NHL_STACK_CONFIG = {
    # Line stack: C + W + W from same team (forward line)
    "line_stack_positions": ["C", "W", "W"],
    # Minimum players from primary stack team
    "primary_stack_size": 3,
    # Secondary stack size (from another team)
    "secondary_stack_size": 2,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Map our Sport enum to pydfs-lineup-optimizer Sport enum
SPORT_MAPPING = {
    Sport.NFL: PDFSSport.FOOTBALL,
    Sport.NBA: PDFSSport.BASKETBALL,
    Sport.MLB: PDFSSport.BASEBALL,
    Sport.NHL: PDFSSport.HOCKEY,
    Sport.PGA: PDFSSport.GOLF,
}

# FanDuel roster position order by sport (for CSV export)
FANDUEL_POSITION_ORDER = {
    Sport.NFL: ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DEF"],
    Sport.NBA: ["PG", "PG", "SG", "SG", "SF", "SF", "PF", "PF", "C"],
    Sport.MLB: ["P", "C/1B", "2B", "3B", "SS", "OF", "OF", "OF", "UTIL"],
    Sport.NHL: ["C", "C", "W", "W", "D", "D", "UTIL", "UTIL", "G"],
    Sport.PGA: ["G", "G", "G", "G", "G", "G"],
}


def fetch_projections(sport: Sport) -> list:
    """Fetch projections from DailyFantasyFuel.

    Args:
        sport: Sport to fetch projections for

    Returns:
        List of Projection objects
    """
    source = DailyFantasyFuelSource()
    projections = source.fetch_projections(sport)
    logger.info(f"Fetched {len(projections)} projections from DailyFantasyFuel")
    return projections


def get_fanduel_client() -> FanDuelApiClient:
    """Get authenticated FanDuel API client.

    Returns:
        Authenticated FanDuelApiClient

    Raises:
        SystemExit: If auth tokens not configured
    """
    auth_token = os.environ.get("FANDUEL_AUTH_TOKEN")
    session_token = os.environ.get("FANDUEL_SESSION_TOKEN")

    if not auth_token or not session_token:
        logger.error("FanDuel auth tokens not configured in .env file")
        logger.info("Set FANDUEL_AUTH_TOKEN and FANDUEL_SESSION_TOKEN")
        sys.exit(1)

    return FanDuelApiClient(
        basic_auth_token=auth_token,
        x_auth_token=session_token,
    )


def fetch_fanduel_players(sport: Sport, fixture_list_id: int = None) -> tuple[list[dict], int, dict]:
    """Fetch players with salaries from FanDuel API.

    Args:
        sport: Sport to fetch
        fixture_list_id: Optional specific fixture list ID. If None, uses first available.

    Returns:
        Tuple of (list of player dicts, salary_cap, team_opponents mapping)
    """
    client = get_fanduel_client()

    # Get fixture lists for sport
    if fixture_list_id is None:
        fixture_lists = client.get_fixture_lists(sport)
        if not fixture_lists:
            logger.error(f"No FanDuel fixture lists found for {sport.value}")
            return [], 55000, {}

        # Use first (main) fixture list
        fl = fixture_lists[0]
        fixture_list_id = fl.get("id")
        salary_cap = fl.get("salary_cap", 55000)
        logger.info(f"Using fixture list {fixture_list_id}: {fl.get('label', 'Unknown')}")
    else:
        # Fetch specific fixture list details
        fl_data = client.get_fixture_list(fixture_list_id)
        fl = fl_data.get("fixture_lists", [{}])[0] if fl_data.get("fixture_lists") else {}
        salary_cap = fl.get("salary_cap", 55000)

    # Fetch players - get raw response to also get teams and fixtures lookup
    raw_data = client._request("GET", f"/fixture-lists/{fixture_list_id}/players")
    players_raw = raw_data.get("players", [])
    teams_raw = raw_data.get("teams", [])
    fixtures_raw = raw_data.get("fixtures", [])

    # Build teams lookup by ID -> team code
    teams_lookup = {}
    for team in teams_raw:
        team_id = str(team.get("id", ""))
        teams_lookup[team_id] = team.get("code", "")

    # Build team opponents mapping from fixtures (games)
    # team_code -> opponent_team_code
    team_opponents = {}
    for fixture in fixtures_raw:
        home_team_ref = fixture.get("home_team", {}).get("team", {})
        away_team_ref = fixture.get("away_team", {}).get("team", {})

        home_team_id = home_team_ref.get("_members", [None])[0] if home_team_ref else None
        away_team_id = away_team_ref.get("_members", [None])[0] if away_team_ref else None

        if home_team_id and away_team_id:
            home_code = teams_lookup.get(str(home_team_id), "")
            away_code = teams_lookup.get(str(away_team_id), "")
            if home_code and away_code:
                team_opponents[home_code] = away_code
                team_opponents[away_code] = home_code

    logger.info(f"Fetched {len(players_raw)} players from FanDuel (fixture list {fixture_list_id})")
    logger.info(f"Game matchups: {team_opponents}")

    # Parse players
    players = []
    for p in players_raw:
        # Extract projected points - it's nested in a dict
        proj_data = p.get("projected_fantasy_points", {})
        if isinstance(proj_data, dict):
            proj_pts = proj_data.get("projected_fantasy_points", 0)
        else:
            proj_pts = proj_data or 0

        # Extract team from reference
        team_ref = p.get("team", {})
        team_code = ""
        if isinstance(team_ref, dict):
            # Team is a reference like {'_members': ['656'], '_ref': 'teams.id'}
            members = team_ref.get("_members", [])
            if members:
                team_id = str(members[0])
                team_code = teams_lookup.get(team_id, "")

        player = {
            "fanduel_id": str(p.get("id", "")),
            "name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "first_name": p.get("first_name", ""),
            "last_name": p.get("last_name", ""),
            "position": p.get("position", ""),
            "team": team_code,
            "salary": p.get("salary", 0),
            "fppg": p.get("fppg", 0.0),
            "projected_points": proj_pts,  # FanDuel's projection
            "injury_status": p.get("injury_status"),
        }
        players.append(player)

    return players, salary_cap, team_opponents


def merge_projections_with_salaries(
    fd_players: list[dict],
    dff_projections: list,
) -> list[dict]:
    """Merge DailyFantasyFuel projections with FanDuel salaries.

    Uses DFF projected points but FanDuel actual salaries.
    Filters out injured players (IR, O, OUT statuses).

    Args:
        fd_players: FanDuel players with salaries
        dff_projections: DailyFantasyFuel projections

    Returns:
        Merged player list with salaries and projections
    """
    # Injury statuses that should be excluded
    EXCLUDED_INJURY_STATUSES = {"ir", "o", "out", "d"}  # IR, Out, Doubtful

    # Build lookup from DFF projections by normalized name
    dff_lookup = {}
    for proj in dff_projections:
        if proj.projected_points and proj.projected_points > 0:
            # Normalize name for matching
            name_key = proj.name.lower().strip()
            dff_lookup[name_key] = proj

            # Also add without periods (J.T. Miller -> JT Miller)
            name_no_dots = name_key.replace(".", "")
            if name_no_dots != name_key:
                dff_lookup[name_no_dots] = proj

    merged = []
    matched = 0
    unmatched_fd = []
    injured_excluded = 0

    for fd_player in fd_players:
        name = fd_player["name"]
        name_key = name.lower().strip()

        # Check injury status - exclude IR, Out, Doubtful players
        injury_status = (fd_player.get("injury_status") or "").lower()
        if injury_status in EXCLUDED_INJURY_STATUSES:
            injured_excluded += 1
            logger.debug(f"Excluding injured player: {name} (status: {injury_status})")
            continue

        # Try to find DFF projection
        dff_proj = dff_lookup.get(name_key)

        # Try alternate name formats
        if not dff_proj:
            name_no_dots = name_key.replace(".", "")
            dff_proj = dff_lookup.get(name_no_dots)

        if dff_proj:
            fd_player["projected_points"] = dff_proj.projected_points
            fd_player["floor"] = dff_proj.floor
            fd_player["ceiling"] = dff_proj.ceiling
            matched += 1
        else:
            # No DFF projection - only use FanDuel's projection if it exists
            # Don't fall back to fppg for players not in DFF (likely injured/inactive)
            fd_proj = fd_player.get("projected_points", 0)
            if not fd_proj or fd_proj <= 0:
                # Skip players with no projection from either source
                logger.debug(f"Skipping player with no projection: {name}")
                continue

        merged.append(fd_player)

    logger.info(f"Matched {matched}/{len(fd_players)} FanDuel players with DFF projections")
    logger.info(f"Excluded {injured_excluded} injured players (IR/Out/Doubtful)")
    if unmatched_fd and len(unmatched_fd) <= 10:
        logger.debug(f"Unmatched FanDuel players: {unmatched_fd[:10]}")

    return merged


def create_fanduel_players(projections: list, sport: Sport) -> list[PDFSPlayer]:
    """Convert projections to FanDuel pydfs players.

    FanDuel uses different salary scale than Yahoo (typically $3500-$10000+).
    Since we're using DFF FanDuel projections, salaries should already be
    in the correct format.

    Args:
        projections: List of Projection objects from DFF
        sport: Sport for position mapping

    Returns:
        List of pydfs Player objects
    """
    players = []

    for proj in projections:
        if not proj.projected_points or proj.projected_points <= 0:
            continue

        # For NHL, DFF uses "W" but FanDuel optimizer expects "LW" or "RW"
        # pydfs handles this internally via position mapping
        position = proj.position

        # Create player with FanDuel-compatible format
        try:
            player = PDFSPlayer(
                player_id=f"dff_{proj.name.replace(' ', '_')}",
                first_name=proj.name.split()[0] if proj.name else "",
                last_name=" ".join(proj.name.split()[1:]) if proj.name else "",
                positions=[position],
                team=proj.team or "UNK",
                salary=5000,  # Placeholder - we'll use projections directly
                fppg=proj.projected_points,
            )
            players.append(player)
        except Exception as e:
            logger.debug(f"Failed to create player {proj.name}: {e}")
            continue

    logger.info(f"Created {len(players)} FanDuel players from projections")
    return players


def apply_nhl_stacking_rules(optimizer, players: list[PDFSPlayer], goalie_team: str = None, team_opponents: dict = None):
    """Apply NHL-specific stacking rules to the optimizer.

    Rules implemented:
    1. Line stack: Require C + W + W from same team (forward line correlation)
    2. No opposing skaters vs goalie: Goalie and opposing skaters cannot be in same lineup

    Args:
        optimizer: pydfs LineupOptimizer instance
        players: List of players loaded into optimizer
        goalie_team: Optional team code for goalie (for correlation) - DEPRECATED, not used
        team_opponents: Dict mapping team -> opponent team - DEPRECATED, not used
    """
    # Rule 1: Line Stack - Require at least one team with C + W + W
    # Using PositionsStack to enforce forward line stacking
    # This requires 3 players (C, W, W) from the same team
    try:
        line_stack = PositionsStack(
            positions=["C", "W", "W"],  # Forward line: Center + 2 Wingers
            max_exposure=0.8,  # Allow some diversity
        )
        optimizer.add_stack(line_stack)
        logger.info("Applied NHL line stack rule: C + W + W from same team")
    except Exception as e:
        logger.warning(f"Could not apply line stack: {e}")

    # Rule 2: No opposing skaters vs goalie
    # Use restrict_positions_for_opposing_team to ensure goalie and opposing skaters
    # are never in the same lineup. This is enforced per-lineup, not globally.
    # G cannot be paired with C, W, D from opposing team (0 allowed)
    try:
        optimizer.restrict_positions_for_opposing_team(
            ["G"],  # Goalie position
            ["C", "W", "D"],  # Skater positions (Center, Winger, Defenseman)
        )
        logger.info("Applied NHL goalie constraint: No opposing skaters vs goalie (per lineup)")
    except Exception as e:
        logger.warning(f"Could not apply goalie constraint: {e}")






def estimate_fanduel_salary(projected_points: float, position: str, sport: Sport) -> int:
    """Estimate FanDuel salary based on projected points.

    FanDuel NHL salary ranges roughly:
    - Elite players (20+ pts): $8,000-$9,500
    - Good players (15-20 pts): $6,500-$8,000
    - Average players (10-15 pts): $5,000-$6,500
    - Below average (5-10 pts): $4,000-$5,000
    - Low tier (<5 pts): $3,500-$4,000

    Args:
        projected_points: DFF projected points
        position: Player position
        sport: Sport

    Returns:
        Estimated FanDuel salary
    """
    if sport == Sport.NHL:
        # NHL specific estimation
        if position == "G":
            # Goalies have higher salaries
            if projected_points >= 20:
                return 8500
            elif projected_points >= 15:
                return 7500
            elif projected_points >= 10:
                return 6000
            else:
                return 4500
        else:
            # Skaters
            if projected_points >= 20:
                return 9000
            elif projected_points >= 15:
                return 7500
            elif projected_points >= 12:
                return 6500
            elif projected_points >= 10:
                return 5500
            elif projected_points >= 8:
                return 5000
            elif projected_points >= 5:
                return 4500
            else:
                return 3500
    else:
        # Generic estimation for other sports
        base = 3500
        per_point = 400
        return min(max(int(base + projected_points * per_point), 3500), 12000)


def generate_lineups(
    sport: Sport,
    num_lineups: int = 1,
    randomness: float = 0.1,
    fixture_list_id: int = None,
    use_estimated_salaries: bool = False,
    use_vegas_lines: bool = True,
    min_game_total: float = 5.0,
    vegas_weight: float = 0.5,
):
    """Generate optimized FanDuel lineups.

    Args:
        sport: Sport to optimize for
        num_lineups: Number of lineups to generate
        randomness: Randomness factor for diversity
        fixture_list_id: Optional FanDuel fixture list ID for real salaries
        use_estimated_salaries: If True, estimate salaries instead of fetching from FanDuel
        use_vegas_lines: If True, fetch Vegas lines and apply game environment filters
        min_game_total: Minimum O/U total to include a game (Phase 1)
        vegas_weight: Weight for Vegas-based projection adjustments (Phase 2)

    Returns:
        List of optimized lineups
    """
    # Fetch DFF projections
    dff_projections = fetch_projections(sport)

    if not dff_projections:
        logger.error("No projections available from DailyFantasyFuel")
        return []

    # Create optimizer
    pdfs_sport = SPORT_MAPPING.get(sport)
    if not pdfs_sport:
        logger.error(f"Sport {sport} not supported")
        return []

    optimizer = get_optimizer(Site.FANDUEL, pdfs_sport)

    # ==========================================================================
    # Vegas Lines Integration
    # ==========================================================================
    vegas_games = []
    exclude_teams_vegas = set()

    if use_vegas_lines and sport == Sport.NHL:
        logger.info("Fetching Vegas lines for game environment analysis...")
        vegas_games = fetch_nhl_odds()

        if vegas_games:
            print_odds_summary(vegas_games)

            # Phase 1: Hard filter - exclude low-total games
            exclude_teams_vegas = filter_low_total_teams(vegas_games, min_total=min_game_total)
            if exclude_teams_vegas:
                logger.info(f"Phase 1: Excluding teams from low-total games: {exclude_teams_vegas}")
        else:
            logger.warning("No Vegas lines available (check ODDS_API_KEY)")

    # Fetch real FanDuel salaries or use estimates
    if use_estimated_salaries:
        logger.info("Using estimated salaries (FanDuel API not used)")
        players = []
        for proj in dff_projections:
            if not proj.projected_points or proj.projected_points <= 0:
                continue

            estimated_salary = estimate_fanduel_salary(proj.projected_points, proj.position, sport)

            try:
                player = PDFSPlayer(
                    player_id=f"dff_{proj.name.replace(' ', '_').replace('.', '')}",
                    first_name=proj.name.split()[0] if proj.name else "",
                    last_name=" ".join(proj.name.split()[1:]) if proj.name else "",
                    positions=[proj.position],
                    team=proj.team or "UNK",
                    salary=estimated_salary,
                    fppg=proj.projected_points,
                )
                players.append(player)
            except Exception as e:
                logger.debug(f"Failed to create player {proj.name}: {e}")
                continue
    else:
        # Fetch real salaries from FanDuel API (also returns team matchups)
        fd_players, salary_cap, team_opponents = fetch_fanduel_players(sport, fixture_list_id)

        if not fd_players:
            logger.error("No players from FanDuel API. Try --use-estimated-salaries flag.")
            return []

        # Merge with DFF projections
        merged_players = merge_projections_with_salaries(fd_players, dff_projections)

        # Build GameInfo map from team_opponents
        # GameInfo needs home_team and away_team - we'll use team as home, opponent as away
        # The actual home/away doesn't matter for the opposing team constraint
        team_game_info = {}
        if team_opponents:
            for team, opponent in team_opponents.items():
                team_game_info[team] = GameInfo(home_team=team, away_team=opponent, starts_at=None)

        # Convert to pydfs players
        players = []
        for p in merged_players:
            proj_pts = p.get("projected_points", 0)
            if not proj_pts or proj_pts <= 0:
                continue

            try:
                team = p["team"] or "UNK"
                game_info = team_game_info.get(team)
                player = PDFSPlayer(
                    player_id=p["fanduel_id"],
                    first_name=p["first_name"],
                    last_name=p["last_name"],
                    positions=[p["position"]],
                    team=team,
                    salary=p["salary"],
                    fppg=proj_pts,
                    game_info=game_info,
                )
                players.append(player)
            except Exception as e:
                logger.debug(f"Failed to create player {p['name']}: {e}")
                continue

    # ==========================================================================
    # Phase 1: Apply Vegas Hard Filter - Exclude low-total game teams
    # ==========================================================================
    if exclude_teams_vegas:
        original_count = len(players)
        players = [p for p in players if p.team not in exclude_teams_vegas]
        excluded_count = original_count - len(players)
        logger.info(f"Phase 1 Vegas Filter: Excluded {excluded_count} players from low-total games")

    if len(players) < 9:
        logger.error(f"Not enough players with projections ({len(players)})")
        return []

    # Load players
    optimizer.load_players(players)
    logger.info(f"Loaded {len(players)} players into FanDuel optimizer")

    # Set constraints
    optimizer.set_total_teams(min_teams=3)

    # Apply NHL-specific stacking rules
    if sport == Sport.NHL and not use_estimated_salaries:
        # Apply stacking rules (line stack + goalie vs opposing skaters constraint)
        apply_nhl_stacking_rules(optimizer, players)

    # ==========================================================================
    # Phase 2 & 3: Vegas-Adjusted Fantasy Points Strategy
    # ==========================================================================
    if vegas_games and sport == Sport.NHL:
        # Create Vegas-adjusted strategy that boosts high-total games and favorites
        vegas_config = {
            "baseline_total": 6.0,
            "total_weight": vegas_weight,  # Phase 2: weight for O/U adjustments
            "favorite_boost": 0.05,  # Phase 3: flat boost for favorites
            "randomness": randomness,  # Still include randomness for diversity
        }
        vegas_strategy = create_vegas_strategy(vegas_games, vegas_config)
        optimizer.set_fantasy_points_strategy(vegas_strategy)
        logger.info(f"Applied Vegas-adjusted strategy (weight={vegas_weight}, randomness={randomness})")
    elif randomness > 0:
        # Fallback to pure randomness if no Vegas data
        from pydfs_lineup_optimizer import RandomFantasyPointsStrategy
        optimizer.set_fantasy_points_strategy(RandomFantasyPointsStrategy(randomness))

    # Generate lineups
    try:
        lineups = list(optimizer.optimize(n=num_lineups))
        logger.info(f"Generated {len(lineups)} FanDuel lineups")
        return lineups
    except Exception as e:
        logger.error(f"Optimization failed: {e}")
        return []


def export_lineups(
    lineups: list,
    sport: Sport,
    output_dir: Path,
    use_ids: bool = True,
    contest_id: int = None,
) -> Path:
    """Export lineups to FanDuel CSV format.

    Args:
        lineups: List of optimized lineups
        sport: Sport for position ordering
        output_dir: Output directory
        use_ids: If True, use FanDuel player IDs; if False, use player names
        contest_id: Optional contest ID to include in filename

    Returns:
        Path to exported CSV

    File naming convention:
        contest_{contest_id}_{timestamp}.csv
        Example: contest_124507_20251217_120000.csv
    """
    # Use organized folder structure: data/lineups/fanduel/{sport}/
    sport_dir = output_dir / "fanduel" / sport.value
    sport_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if contest_id:
        filename = f"contest_{contest_id}_{timestamp}.csv"
    else:
        filename = f"lineups_{timestamp}.csv"
    filepath = sport_dir / filename

    positions = FANDUEL_POSITION_ORDER.get(sport, [])

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(positions)  # Header

        for lineup in lineups:
            row = []
            players_by_pos = {}

            for player in lineup.players:
                pos = player.lineup_position
                if pos not in players_by_pos:
                    players_by_pos[pos] = []
                # Use player ID if use_ids is True, otherwise use name
                if use_ids:
                    players_by_pos[pos].append(player.id)
                else:
                    players_by_pos[pos].append(player.full_name)

            # Fill row by position order
            position_usage = {}
            for pos in positions:
                if pos not in position_usage:
                    position_usage[pos] = 0

                if pos in players_by_pos and position_usage[pos] < len(players_by_pos[pos]):
                    row.append(players_by_pos[pos][position_usage[pos]])
                    position_usage[pos] += 1
                else:
                    row.append("")

            writer.writerow(row)

    logger.info(f"Exported {len(lineups)} lineups to {filepath}")
    return filepath


def print_lineups(lineups: list, sport: Sport):
    """Print lineups to console.

    Args:
        lineups: List of optimized lineups
        sport: Sport
    """
    for i, lineup in enumerate(lineups, 1):
        print(f"\n{'='*60}")
        print(f"FANDUEL LINEUP {i}")
        print(f"Projected: {lineup.fantasy_points_projection:.1f} pts | Salary: ${lineup.salary_costs:,}")
        print("-" * 60)

        for player in lineup.players:
            print(
                f"{player.lineup_position:5} | {player.full_name:28} | "
                f"${player.salary:,} | {player.fppg:.1f} pts"
            )

        print("=" * 60)

    if lineups:
        avg_proj = sum(l.fantasy_points_projection for l in lineups) / len(lineups)
        avg_sal = sum(l.salary_costs for l in lineups) / len(lineups)
        print(f"\nTotal Lineups: {len(lineups)}")
        print(f"Avg Projected: {avg_proj:.1f} pts")
        print(f"Avg Salary: ${avg_sal:,.0f}")


def print_verification_summary(
    lineups: list,
    vegas_games: list,
    exclude_teams_vegas: set,
    sport: Sport,
):
    """Print verification summary showing constraints applied.

    Args:
        lineups: Generated lineups
        vegas_games: Vegas odds data
        exclude_teams_vegas: Teams excluded due to low totals
        sport: Sport
    """
    print("\n" + "=" * 70)
    print("VERIFICATION SUMMARY")
    print("=" * 70)

    # 1. Vegas Adjustments
    print("\n📊 VEGAS ADJUSTMENTS:")
    if vegas_games:
        game_totals = get_game_totals(vegas_games)
        favorites = get_favorites(vegas_games)

        print(f"  Games with odds: {len(vegas_games)}")
        print(f"  Favorites (boosted +5%): {', '.join(sorted(favorites)) if favorites else 'None'}")

        if exclude_teams_vegas:
            print(f"  ❌ Teams excluded (low total): {', '.join(sorted(exclude_teams_vegas))}")
        else:
            print("  ✅ No teams excluded for low totals")

        # Show boost/reduction by team
        print("\n  Projection adjustments by game total:")
        baseline = 6.0
        for team, total in sorted(game_totals.items(), key=lambda x: x[1], reverse=True):
            deviation = (total - baseline) / baseline * 0.5 * 100  # 0.5 is default weight
            direction = "↑" if deviation > 0 else "↓" if deviation < 0 else "→"
            print(f"    {team}: O/U {total} {direction} {abs(deviation):.1f}%")
    else:
        print("  Vegas lines not available")

    # 2. Stacking Rules (NHL specific)
    if sport == Sport.NHL and lineups:
        print("\n🏒 NHL STACKING RULES:")
        print("  ✅ Line stack enforced: C + W + W from same team")
        print("  ✅ Goalie constraint: Goalie and opposing skaters never in same lineup")

    # 3. Player Exposure
    print("\n👥 PLAYER EXPOSURE (Top 15):")
    player_counts = {}
    for lineup in lineups:
        for player in lineup.players:
            name = player.full_name
            if name not in player_counts:
                player_counts[name] = {"count": 0, "salary": player.salary, "fppg": player.fppg}
            player_counts[name]["count"] += 1

    # Sort by exposure
    sorted_players = sorted(player_counts.items(), key=lambda x: x[1]["count"], reverse=True)
    total_lineups = len(lineups)

    for name, data in sorted_players[:15]:
        exposure = data["count"] / total_lineups * 100
        print(f"  {name:28} | {data['count']:>3}/{total_lineups} ({exposure:>5.1f}%) | ${data['salary']:,} | {data['fppg']:.1f}pts")

    # 4. Team Distribution
    print("\n🏟️ TEAM DISTRIBUTION:")
    team_counts = {}
    for lineup in lineups:
        for player in lineup.players:
            team = player.team
            if team not in team_counts:
                team_counts[team] = 0
            team_counts[team] += 1

    for team, count in sorted(team_counts.items(), key=lambda x: x[1], reverse=True):
        avg_per_lineup = count / total_lineups
        print(f"  {team:4}: {count:>4} players ({avg_per_lineup:.1f} per lineup)")

    print("=" * 70)


def find_eligible_contests(sport: Sport) -> list[dict]:
    """Find eligible contests using contest selector.

    Args:
        sport: Sport to search

    Returns:
        List of eligible contest dicts with fixture_list_id
    """
    client = get_fanduel_client()
    selector = ContestSelector()

    all_eligible = []
    fixture_lists = client.get_fixture_lists(sport)

    logger.info(f"Searching {len(fixture_lists)} {sport.value} fixture lists for eligible contests...")

    for fl in fixture_lists:
        fl_id = fl.get("id")
        fl_label = fl.get("label", "Unknown")

        # Skip snake drafts
        if "snake" in fl_label.lower():
            continue

        try:
            raw_contests = client._request("GET", "/contests", params={"fixture_list": fl_id, "status": "open"})
            contests_raw = raw_contests.get("contests", [])
        except Exception as e:
            logger.warning(f"Failed to fetch contests for {fl_label}: {e}")
            continue

        # Parse and filter contests
        contests = [parse_contest(c) for c in contests_raw]
        eligible = selector.filter_contests(contests)

        for contest in eligible:
            contest["slate_label"] = fl_label
            all_eligible.append(contest)

    # Sort by score
    scored = selector.score_contests(all_eligible, min_score=0)

    logger.info(f"Found {len(scored)} eligible {sport.value} contests")
    return scored


def run_auto_select_mode(
    sport: Sport,
    randomness: float,
    use_vegas_lines: bool,
    min_game_total: float,
    vegas_weight: float,
    output_dir: Path,
    print_lineups_flag: bool,
):
    """Run auto-select mode: find eligible contests and generate lineups for each.

    Args:
        sport: Sport to generate lineups for
        randomness: Randomness factor
        use_vegas_lines: Whether to use Vegas lines
        min_game_total: Minimum O/U total
        vegas_weight: Vegas adjustment weight
        output_dir: Output directory
        print_lineups_flag: Whether to print lineups to console
    """
    print("\n" + "=" * 70)
    print(f"AUTO-SELECT MODE: {sport.value}")
    print("=" * 70)

    # Step 1: Find eligible contests
    print("\n📋 Step 1: Finding eligible contests...")
    eligible_contests = find_eligible_contests(sport)

    if not eligible_contests:
        logger.error(f"No eligible {sport.value} contests found")
        print("❌ No contests found matching criteria:")
        print("   - Entry fee < $3")
        print("   - Multi-entry with 50+ entries allowed")
        print("   - Contest size >= 50")
        print("   - Exposure ratio >= 2%")
        return

    # Display eligible contests
    print(f"\n✅ Found {len(eligible_contests)} eligible contests:\n")
    print(f"{'Score':>5} | {'Contest Name':<40} | {'Fee':>5} | {'Max':>5} | {'Slate':<15}")
    print("-" * 80)
    for c in eligible_contests:
        print(
            f"{c.get('score', 0):>5} | {c.get('name', '')[:40]:<40} | "
            f"${c.get('entry_fee', 0):>4} | {c.get('max_entries', 0):>5} | "
            f"{c.get('slate_label', '')[:15]:<15}"
        )

    # Step 2: Fetch DailyFantasyFuel projections (once for all contests)
    print("\n📊 Step 2: Fetching DailyFantasyFuel projections...")
    dff_projections = fetch_projections(sport)
    if not dff_projections:
        logger.error("No projections available from DailyFantasyFuel")
        return
    print(f"✅ Fetched {len(dff_projections)} projections")

    # Step 3: Fetch Vegas lines (once for all contests)
    vegas_games = []
    exclude_teams_vegas = set()
    if use_vegas_lines and sport == Sport.NHL:
        print("\n🎰 Step 3: Fetching Vegas lines...")
        vegas_games = fetch_nhl_odds()
        if vegas_games:
            print_odds_summary(vegas_games)
            exclude_teams_vegas = filter_low_total_teams(vegas_games, min_total=min_game_total)
            if exclude_teams_vegas:
                print(f"⚠️ Excluding teams from low-total games: {', '.join(exclude_teams_vegas)}")
        else:
            print("⚠️ No Vegas lines available")

    # Step 4: Generate lineups for each contest
    print("\n🎯 Step 4: Generating lineups for each contest...")
    results = []

    for contest in eligible_contests:
        contest_id = contest.get("id")
        contest_name = contest.get("name", "Unknown")[:50]
        fixture_list_id = contest.get("fixture_list_id")
        max_entries = contest.get("max_entries", 1)
        slate_label = contest.get("slate_label", "")

        print(f"\n{'─' * 70}")
        print(f"Contest: {contest_name}")
        print(f"ID: {contest_id} | Slate: {slate_label} | Max entries: {max_entries}")
        print("─" * 70)

        try:
            # Generate lineups for this contest
            lineups = generate_lineups(
                sport=sport,
                num_lineups=max_entries,
                randomness=randomness,
                fixture_list_id=fixture_list_id,
                use_estimated_salaries=False,
                use_vegas_lines=use_vegas_lines,
                min_game_total=min_game_total,
                vegas_weight=vegas_weight,
            )

            if lineups:
                # Export lineups
                filepath = export_lineups(lineups, sport, output_dir, contest_id=contest_id)

                # Print verification summary
                print_verification_summary(lineups, vegas_games, exclude_teams_vegas, sport)

                # Print lineups if requested
                if print_lineups_flag:
                    print_lineups(lineups, sport)

                results.append({
                    "contest_id": contest_id,
                    "contest_name": contest_name,
                    "lineups_generated": len(lineups),
                    "filepath": str(filepath),
                    "success": True,
                })

                print(f"\n✅ Generated {len(lineups)} lineups → {filepath}")
            else:
                results.append({
                    "contest_id": contest_id,
                    "contest_name": contest_name,
                    "lineups_generated": 0,
                    "filepath": None,
                    "success": False,
                })
                print(f"\n❌ Failed to generate lineups")

        except Exception as e:
            logger.error(f"Error generating lineups for contest {contest_id}: {e}")
            results.append({
                "contest_id": contest_id,
                "contest_name": contest_name,
                "lineups_generated": 0,
                "filepath": None,
                "success": False,
                "error": str(e),
            })
            print(f"\n❌ Error: {e}")

    # Final Summary
    print("\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print(f"\n✅ Successful: {len(successful)} contests")
    for r in successful:
        print(f"   - {r['contest_name'][:40]}: {r['lineups_generated']} lineups")
        print(f"     → {r['filepath']}")

    if failed:
        print(f"\n❌ Failed: {len(failed)} contests")
        for r in failed:
            print(f"   - {r['contest_name'][:40]}: {r.get('error', 'Unknown error')}")

    total_lineups = sum(r["lineups_generated"] for r in results)
    print(f"\n📊 Total lineups generated: {total_lineups}")
    print("=" * 70)


def main():
    """Generate FanDuel lineups."""
    parser = argparse.ArgumentParser(description="Generate FanDuel Lineups")

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA"],
        default="NHL",
        help="Sport (default: NHL)",
    )

    parser.add_argument(
        "--fixture-list-id",
        type=int,
        default=None,
        help="FanDuel fixture list ID (default: uses first available)",
    )

    parser.add_argument(
        "--contest-id",
        type=int,
        default=None,
        help="Contest ID to include in output filename",
    )

    parser.add_argument(
        "--num-lineups",
        type=int,
        default=3,
        help="Number of lineups to generate (default: 3)",
    )

    parser.add_argument(
        "--randomness",
        type=float,
        default=0.1,
        help="Randomness factor for lineup diversity (default: 0.1)",
    )

    parser.add_argument(
        "--use-estimated-salaries",
        action="store_true",
        help="Use estimated salaries instead of fetching from FanDuel API",
    )

    parser.add_argument(
        "--use-vegas-lines",
        action="store_true",
        default=True,
        help="Use Vegas lines for game environment filtering (default: True)",
    )

    parser.add_argument(
        "--no-vegas-lines",
        action="store_true",
        help="Disable Vegas lines integration",
    )

    parser.add_argument(
        "--min-game-total",
        type=float,
        default=5.0,
        help="Minimum O/U total to include a game (default: 5.0)",
    )

    parser.add_argument(
        "--vegas-weight",
        type=float,
        default=0.5,
        help="Weight for Vegas-based projection adjustments (default: 0.5)",
    )

    parser.add_argument(
        "--output",
        type=str,
        default="data/lineups",
        help="Output directory (default: data/lineups)",
    )

    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_lineups",
        help="Print lineups to console",
    )

    parser.add_argument(
        "--auto-select-contest",
        action="store_true",
        help="Auto-select eligible contests and generate max lineups for each",
    )

    args = parser.parse_args()

    sport = Sport(args.sport)

    # Determine if Vegas lines should be used
    use_vegas = args.use_vegas_lines and not args.no_vegas_lines

    # Auto-select mode
    if args.auto_select_contest:
        run_auto_select_mode(
            sport=sport,
            randomness=args.randomness,
            use_vegas_lines=use_vegas,
            min_game_total=args.min_game_total,
            vegas_weight=args.vegas_weight,
            output_dir=Path(args.output),
            print_lineups_flag=args.print_lineups,
        )
        return

    # Manual mode - Generate lineups
    lineups = generate_lineups(
        sport=sport,
        num_lineups=args.num_lineups,
        randomness=args.randomness,
        fixture_list_id=args.fixture_list_id,
        use_estimated_salaries=args.use_estimated_salaries,
        use_vegas_lines=use_vegas,
        min_game_total=args.min_game_total,
        vegas_weight=args.vegas_weight,
    )

    if not lineups:
        logger.error("No lineups generated")
        sys.exit(1)

    # Export
    output_dir = Path(args.output)
    filepath = export_lineups(lineups, sport, output_dir, contest_id=args.contest_id)

    # Print if requested
    if args.print_lineups:
        print_lineups(lineups, sport)

    # Summary
    print(f"\n{'='*60}")
    print(f"Generated {len(lineups)} FanDuel {sport.value} lineups")
    print(f"Exported to: {filepath}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
