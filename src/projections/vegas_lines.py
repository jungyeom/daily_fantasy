"""Vegas lines integration for game environment filtering.

Fetches betting lines (totals, spreads) from The Odds API to inform
lineup optimization decisions.

Free tier: 500 requests/month
API docs: https://the-odds-api.com/

To get an API key:
1. Sign up at https://the-odds-api.com/
2. Get your free API key
3. Set ODDS_API_KEY environment variable
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
NHL_SPORT_KEY = "icehockey_nhl"

# Default configuration
DEFAULT_CONFIG = {
    "min_game_total": 5.0,       # Exclude games below this O/U
    "baseline_total": 6.0,       # Neutral point for adjustments
    "projection_weight": 0.3,    # How much to adjust projections (Phase 2)
    "favor_favorites": True,     # Boost teams with negative spread (Phase 3)
    "spread_weight": 0.1,        # Weight for spread adjustment (Phase 3)
}


@dataclass
class GameOdds:
    """Betting odds for a single game."""
    home_team: str
    away_team: str
    commence_time: datetime
    total: Optional[float] = None  # Over/under line
    home_spread: Optional[float] = None  # Puck line for home team
    away_spread: Optional[float] = None  # Puck line for away team
    home_moneyline: Optional[int] = None
    away_moneyline: Optional[int] = None

    @property
    def favorite(self) -> Optional[str]:
        """Return the favorite team based on moneyline."""
        if self.home_moneyline and self.away_moneyline:
            if self.home_moneyline < self.away_moneyline:
                return self.home_team
            else:
                return self.away_team
        return None

    @property
    def is_high_total(self) -> bool:
        """Check if game has high scoring potential."""
        return self.total is not None and self.total >= 6.0

    @property
    def is_low_total(self) -> bool:
        """Check if game has low scoring potential."""
        return self.total is not None and self.total < 5.5


# Team name mappings: Odds API name -> FanDuel abbreviation
TEAM_NAME_MAP = {
    "Colorado Avalanche": "COL",
    "Calgary Flames": "CGY",
    "San Jose Sharks": "SJ",
    "Seattle Kraken": "SEA",
    "Edmonton Oilers": "EDM",
    "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK",
    "Los Angeles Kings": "LA",
    "Anaheim Ducks": "ANH",
    "Arizona Coyotes": "ARI",
    "Utah Hockey Club": "UTA",
    "Utah Mammoth": "UTA",
    "Chicago Blackhawks": "CHI",
    "Dallas Stars": "DAL",
    "Minnesota Wild": "MIN",
    "Nashville Predators": "NSH",
    "St Louis Blues": "STL",
    "St. Louis Blues": "STL",
    "Winnipeg Jets": "WPG",
    "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF",
    "Detroit Red Wings": "DET",
    "Florida Panthers": "FLA",
    "Montreal Canadiens": "MTL",
    "Montréal Canadiens": "MTL",
    "Ottawa Senators": "OTT",
    "Tampa Bay Lightning": "TB",
    "Toronto Maple Leafs": "TOR",
    "Carolina Hurricanes": "CAR",
    "Columbus Blue Jackets": "CBJ",
    "New Jersey Devils": "NJ",
    "New York Islanders": "NYI",
    "New York Rangers": "NYR",
    "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT",
    "Washington Capitals": "WSH",
}


def get_api_key() -> Optional[str]:
    """Get The Odds API key from environment."""
    return os.environ.get("ODDS_API_KEY")


def fetch_nhl_odds(markets: list[str] = None) -> list[GameOdds]:
    """Fetch NHL odds from The Odds API.

    Args:
        markets: List of markets to fetch. Default: ["totals", "spreads", "h2h"]

    Returns:
        List of GameOdds objects for upcoming games

    Raises:
        ValueError: If API key not configured
    """
    api_key = get_api_key()
    if not api_key:
        logger.warning("ODDS_API_KEY not set. Vegas lines will not be available.")
        return []

    if markets is None:
        markets = ["totals", "spreads", "h2h"]

    url = f"{ODDS_API_BASE}/sports/{NHL_SPORT_KEY}/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": ",".join(markets),
        "oddsFormat": "american",
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Log remaining API requests
        remaining = response.headers.get("x-requests-remaining", "unknown")
        logger.info(f"Fetched NHL odds. API requests remaining: {remaining}")

        return _parse_odds_response(data)

    except requests.RequestException as e:
        logger.error(f"Failed to fetch NHL odds: {e}")
        return []


def _parse_odds_response(data: list[dict]) -> list[GameOdds]:
    """Parse The Odds API response into GameOdds objects."""
    games = []

    for game in data:
        home_team = game.get("home_team", "")
        away_team = game.get("away_team", "")

        # Convert to FanDuel team codes
        home_code = TEAM_NAME_MAP.get(home_team, home_team)
        away_code = TEAM_NAME_MAP.get(away_team, away_team)

        # Parse commence time
        commence_str = game.get("commence_time", "")
        try:
            commence_time = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            commence_time = datetime.now()

        game_odds = GameOdds(
            home_team=home_code,
            away_team=away_code,
            commence_time=commence_time,
        )

        # Parse bookmaker odds (use first available bookmaker)
        bookmakers = game.get("bookmakers", [])
        if bookmakers:
            bookmaker = bookmakers[0]  # Use first bookmaker
            for market in bookmaker.get("markets", []):
                market_key = market.get("key")
                outcomes = market.get("outcomes", [])

                if market_key == "totals":
                    for outcome in outcomes:
                        if outcome.get("name") == "Over":
                            game_odds.total = outcome.get("point")
                            break

                elif market_key == "spreads":
                    for outcome in outcomes:
                        if outcome.get("name") == home_team:
                            game_odds.home_spread = outcome.get("point")
                        elif outcome.get("name") == away_team:
                            game_odds.away_spread = outcome.get("point")

                elif market_key == "h2h":
                    for outcome in outcomes:
                        if outcome.get("name") == home_team:
                            game_odds.home_moneyline = outcome.get("price")
                        elif outcome.get("name") == away_team:
                            game_odds.away_moneyline = outcome.get("price")

        games.append(game_odds)

    logger.info(f"Parsed {len(games)} games with odds")
    return games


def get_game_totals(games: list[GameOdds]) -> dict[str, float]:
    """Build team -> game total mapping.

    Args:
        games: List of GameOdds

    Returns:
        Dict mapping team code to game total (O/U)
    """
    totals = {}
    for game in games:
        if game.total is not None:
            totals[game.home_team] = game.total
            totals[game.away_team] = game.total
    return totals


def get_favorites(games: list[GameOdds]) -> set[str]:
    """Get set of favorite teams.

    Args:
        games: List of GameOdds

    Returns:
        Set of team codes that are favorites
    """
    favorites = set()
    for game in games:
        if game.favorite:
            favorites.add(game.favorite)
    return favorites


def filter_low_total_teams(
    games: list[GameOdds],
    min_total: float = 5.0,
) -> set[str]:
    """Get teams to exclude based on low game totals.

    Args:
        games: List of GameOdds
        min_total: Minimum game total threshold

    Returns:
        Set of team codes to exclude
    """
    exclude_teams = set()
    for game in games:
        if game.total is not None and game.total < min_total:
            exclude_teams.add(game.home_team)
            exclude_teams.add(game.away_team)
            logger.info(
                f"Excluding {game.away_team}@{game.home_team} "
                f"(total: {game.total} < {min_total})"
            )
    return exclude_teams


def print_odds_summary(games: list[GameOdds]):
    """Print a summary of game odds."""
    print("\n" + "=" * 70)
    print("NHL GAME ODDS SUMMARY")
    print("=" * 70)
    print(f"{'Matchup':<25} {'Total':>8} {'Spread':>10} {'Favorite':>12}")
    print("-" * 70)

    for game in sorted(games, key=lambda g: g.commence_time):
        matchup = f"{game.away_team}@{game.home_team}"
        total = f"{game.total:.1f}" if game.total else "N/A"
        spread = f"{game.home_spread:+.1f}" if game.home_spread else "N/A"
        favorite = game.favorite or "N/A"
        print(f"{matchup:<25} {total:>8} {spread:>10} {favorite:>12}")

    print("=" * 70)


# =============================================================================
# Phase 2: Vegas-Adjusted Fantasy Points Strategy
# =============================================================================

class VegasAdjustedFantasyPointsStrategy:
    """Fantasy points strategy that adjusts projections based on Vegas lines.

    This strategy boosts projections for players in high-total games and
    reduces projections for players in low-total games.

    Example:
        - Baseline total: 6.0
        - Game with O/U 7.0: +16.7% boost (if weight=1.0)
        - Game with O/U 5.5: -8.3% reduction (if weight=1.0)
    """

    def __init__(
        self,
        game_totals: dict[str, float],
        baseline_total: float = 6.0,
        total_weight: float = 0.5,
        favorites: set[str] = None,
        favorite_boost: float = 0.05,
        randomness: float = 0.0,
    ):
        """Initialize Vegas-adjusted strategy.

        Args:
            game_totals: Dict mapping team code to game O/U total
            baseline_total: Neutral O/U value (typically 6.0 for NHL)
            total_weight: How much to weight the total adjustment (0.0-1.0)
            favorites: Set of team codes that are favorites
            favorite_boost: Flat boost for favorite teams (Phase 3)
            randomness: Random variance to add (for lineup diversity)
        """
        self.game_totals = game_totals
        self.baseline = baseline_total
        self.total_weight = total_weight
        self.favorites = favorites or set()
        self.favorite_boost = favorite_boost
        self.randomness = randomness

    def get_player_fantasy_points(self, player) -> float:
        """Calculate adjusted fantasy points for a player.

        Args:
            player: pydfs Player object

        Returns:
            Adjusted fantasy points projection
        """
        base_fppg = player.fppg
        adjustment = 1.0

        # Phase 2: Adjust based on game total
        team = player.team
        if team in self.game_totals:
            game_total = self.game_totals[team]
            # Calculate % deviation from baseline
            total_deviation = (game_total - self.baseline) / self.baseline
            adjustment += total_deviation * self.total_weight

        # Phase 3: Boost for favorites
        if team in self.favorites:
            adjustment += self.favorite_boost

        # Apply randomness for lineup diversity
        if self.randomness > 0:
            from random import uniform
            random_factor = uniform(-self.randomness, self.randomness)
            adjustment += random_factor

        return base_fppg * adjustment

    def set_previous_lineup(self, lineup):
        """Required method for pydfs compatibility."""
        pass


def create_vegas_strategy(
    games: list[GameOdds],
    config: dict = None,
) -> VegasAdjustedFantasyPointsStrategy:
    """Create a Vegas-adjusted fantasy points strategy from game odds.

    Args:
        games: List of GameOdds from fetch_nhl_odds()
        config: Optional config dict with keys:
            - baseline_total: float (default: 6.0)
            - total_weight: float (default: 0.5)
            - favorite_boost: float (default: 0.05)
            - randomness: float (default: 0.1)

    Returns:
        Configured VegasAdjustedFantasyPointsStrategy
    """
    config = config or {}

    game_totals = get_game_totals(games)
    favorites = get_favorites(games)

    strategy = VegasAdjustedFantasyPointsStrategy(
        game_totals=game_totals,
        baseline_total=config.get("baseline_total", 6.0),
        total_weight=config.get("total_weight", 0.5),
        favorites=favorites,
        favorite_boost=config.get("favorite_boost", 0.05),
        randomness=config.get("randomness", 0.1),
    )

    logger.info(
        f"Created Vegas strategy: {len(game_totals)} games, "
        f"{len(favorites)} favorites, "
        f"weight={strategy.total_weight}"
    )

    return strategy
