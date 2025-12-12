"""Yahoo Daily Fantasy API client.

This module provides direct API access to Yahoo DFS endpoints,
replacing Selenium-based web scraping with faster, more reliable API calls.

Discovered endpoints at https://dfyql-ro.sports.yahoo.com/v2/:
- /contestsFilteredWeb - List available contests (filterable by sportCode)
- /contestPlayers?contestId={id} - Get player pool for a specific contest
"""
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

import requests

from ..common.exceptions import YahooAPIError
from ..common.models import Sport

logger = logging.getLogger(__name__)

YAHOO_DFS_API_BASE = "https://dfyql-ro.sports.yahoo.com/v2"

# Sport code mappings for API
SPORT_CODES = {
    Sport.NFL: "nfl",
    Sport.NBA: "nba",
    Sport.MLB: "mlb",
    Sport.NHL: "nhl",
    Sport.PGA: "golf",
    Sport.NASCAR: "nascar",
    Sport.SOCCER: "soccer",
}

SPORT_CODE_REVERSE = {v: k for k, v in SPORT_CODES.items()}


class YahooDFSApiClient:
    """Client for Yahoo DFS read-only API.

    This API provides contest listings and player pool data without authentication.
    Note: This is a read-only API. Lineup submission still requires authenticated
    browser automation.
    """

    def __init__(self, timeout: int = 30):
        """Initialize API client.

        Args:
            timeout: Request timeout in seconds
        """
        self.base_url = YAHOO_DFS_API_BASE
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
        })

    def get_contests(
        self,
        sport: Sport | str | None = None,
    ) -> list[dict]:
        """Fetch available contests from Yahoo DFS.

        Args:
            sport: Filter by sport - accepts Sport enum or string (e.g., "nfl").
                   None returns all sports.

        Returns:
            List of contest dictionaries with raw API data

        Raises:
            YahooAPIError: If API request fails
        """
        # Use /contests endpoint instead of /contestsFilteredWeb
        # /contests returns paid contests, /contestsFilteredWeb only returns free ones
        # /contests returns both single-game and multi-game contests by default
        url = f"{self.base_url}/contests"

        # Build query string
        query_parts = []

        if sport:
            # Handle both Sport enum and string
            if isinstance(sport, Sport):
                sport_code = SPORT_CODES.get(sport)
            else:
                # String sport code (e.g., "nfl")
                sport_code = sport.lower()
            if sport_code:
                query_parts.append(f"sport={sport_code}")

        full_url = url
        if query_parts:
            full_url = f"{url}?{'&'.join(query_parts)}"

        try:
            logger.debug(f"Fetching contests from {full_url}")
            response = self.session.get(full_url, timeout=self.timeout)
            response.raise_for_status()

            data = response.json()

            # API returns contests under 'contests' -> 'result' key
            contests_data = data.get("contests", {})
            contests = contests_data.get("result", [])

            # Count by slate type for logging
            single_count = sum(1 for c in contests if c.get("slateType") == "SINGLE_GAME")
            multi_count = sum(1 for c in contests if c.get("slateType") == "MULTI_GAME")
            logger.info(f"Fetched {len(contests)} contests from API ({single_count} single-game, {multi_count} multi-game)")

            return contests

        except requests.RequestException as e:
            logger.error(f"Failed to fetch contests: {e}")
            raise YahooAPIError(f"Contest API request failed: {e}") from e

    def get_contest_players(self, contest_id: str | int) -> list[dict]:
        """Fetch player pool for a specific contest.

        Args:
            contest_id: Yahoo contest ID

        Returns:
            List of player dictionaries with raw API data

        Raises:
            YahooAPIError: If API request fails
        """
        url = f"{self.base_url}/contestPlayers"
        params = {"contestId": str(contest_id)}

        try:
            logger.debug(f"Fetching players for contest {contest_id}")
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()

            data = response.json()

            # API returns players under 'players' -> 'result' key
            players_data = data.get("players", {})
            players = players_data.get("result", [])
            logger.info(f"Fetched {len(players)} players for contest {contest_id}")

            return players

        except requests.RequestException as e:
            logger.error(f"Failed to fetch players: {e}")
            raise YahooAPIError(f"Player pool API request failed: {e}") from e


def parse_api_contest(raw: dict) -> dict:
    """Parse raw API contest data into normalized format.

    Args:
        raw: Raw contest dictionary from API

    Returns:
        Normalized contest dictionary with parsed fields
    """
    # Extract entry fee
    entry_fee = Decimal("0")
    paid_entry = raw.get("paidEntryFee", {})
    if paid_entry:
        entry_fee = Decimal(str(paid_entry.get("value", 0)))

    # Extract prize pool
    prize_pool = None
    paid_prize = raw.get("paidTotalPrize", {})
    if paid_prize:
        prize_pool = Decimal(str(paid_prize.get("value", 0)))

    # Parse start time (API returns milliseconds)
    start_time_ms = raw.get("startTime", 0)
    slate_start = datetime.fromtimestamp(start_time_ms / 1000) if start_time_ms else datetime.now()

    # Determine sport
    sport_code = raw.get("sportCode", "").lower()
    sport = SPORT_CODE_REVERSE.get(sport_code, Sport.NFL)

    # Extract first place prize
    first_place = raw.get("firstPlacePayout", {})
    first_place_prize = Decimal(str(first_place.get("value", 0))) if first_place else None

    return {
        "id": str(raw.get("id", "")),
        "series_id": raw.get("seriesId"),  # Links contests with same player pool
        "sport": sport,
        "name": raw.get("title", ""),
        "entry_fee": entry_fee,
        "max_entries": raw.get("multipleEntryLimit", 1),
        "total_entries": raw.get("entryCount"),
        "entry_limit": raw.get("entryLimit"),
        "prize_pool": prize_pool,
        "first_place_prize": first_place_prize,
        "slate_start": slate_start,
        "is_guaranteed": raw.get("guaranteed", False),
        "is_multi_entry": raw.get("multipleEntry", False),
        "contest_type": raw.get("type", ""),
        "slate_type": raw.get("slateType", ""),
        "salary_cap": raw.get("salaryCap", 200),
        "restriction": raw.get("restriction"),
        # Raw data for debugging
        "_raw": raw,
    }


def parse_api_player(raw: dict, contest_id: str) -> dict:
    """Parse raw API player data into normalized format.

    Args:
        raw: Raw player dictionary from API
        contest_id: Contest ID this player belongs to

    Returns:
        Normalized player dictionary with parsed fields
    """
    # Extract team info
    team_data = raw.get("team", {})
    team_abbr = team_data.get("abbr", "")

    # Extract game info
    game_data = raw.get("game", {})
    game_time = None
    if game_data:
        game_time_ms = game_data.get("startTime", 0)
        if game_time_ms:
            game_time = datetime.fromtimestamp(game_time_ms / 1000)

    # Extract opponent
    opponent = None
    if game_data:
        home_team = game_data.get("homeTeam", {}).get("abbr", "")
        away_team = game_data.get("awayTeam", {}).get("abbr", "")
        if team_abbr == home_team:
            opponent = away_team
        elif team_abbr == away_team:
            opponent = home_team

    # Extract odds
    odds_data = game_data.get("odds", {})
    spread = odds_data.get("awaySpread") if team_abbr == game_data.get("awayTeam", {}).get("abbr") else odds_data.get("homeSpread")
    over_under = odds_data.get("overUnder")

    # Extract weather
    weather_data = game_data.get("forecast") or {}
    weather_text = weather_data.get("text", "")
    temperature = weather_data.get("highTemperature")

    # Extract positions
    eligible_positions = raw.get("eligiblePositions", [])
    primary_position = raw.get("primaryPosition", "")
    if not primary_position and eligible_positions:
        primary_position = eligible_positions[0]

    # playerGameCode is the ID format needed for CSV upload
    # Format: "nfl.p.{player_id}$nfl.g.{game_id}" or "nfl.t.{team_id}$nfl.g.{game_id}" for DEF
    player_game_code = raw.get("playerGameCode", "")

    return {
        "yahoo_player_id": raw.get("code", ""),
        "player_game_code": player_game_code,  # Full ID for CSV upload
        "name": f"{raw.get('firstName', '')} {raw.get('lastName', '')}".strip(),
        "first_name": raw.get("firstName", ""),
        "last_name": raw.get("lastName", ""),
        "team": team_abbr,
        "team_name": team_data.get("teamName", ""),
        "position": primary_position,
        "eligible_positions": eligible_positions,
        "salary": raw.get("salary", 0),
        "projected_points": raw.get("projectedPoints"),  # Yahoo's built-in projections!
        "game_time": game_time,
        "opponent": opponent,
        "game_code": game_data.get("code", ""),  # e.g., "nfl.g.13553497"
        "game_status": game_data.get("status", ""),
        "contest_id": contest_id,
        # Advanced stats from API
        "fppg": raw.get("fantasyPointsPerGame"),
        "fpts_history": raw.get("fantasyPointsHistory", []),
        "fpts_std_dev": raw.get("fantasyPointsStdDev"),
        # Odds and weather
        "spread": spread,
        "over_under": over_under,
        "weather": weather_text,
        "temperature": temperature,
        # Injury/status info
        # 'status' field contains: INJ (injured), O (out), GTD (game time decision), N/A (available)
        "status": raw.get("status", ""),
        "injury_status": raw.get("injuryStatus"),
        "injury_note": raw.get("injuryNote"),
        # Raw data for debugging
        "_raw": raw,
    }


# Module-level client instance for convenience
_client: Optional[YahooDFSApiClient] = None


def get_api_client() -> YahooDFSApiClient:
    """Get or create the module-level API client.

    Returns:
        YahooDFSApiClient instance
    """
    global _client
    if _client is None:
        _client = YahooDFSApiClient()
    return _client
