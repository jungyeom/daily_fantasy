"""FanDuel API client for fetching contest and player data.

This module provides read-only API access to FanDuel's DFS platform.
Authentication tokens must be manually extracted from browser dev tools.

How to get auth tokens:
1. Log into FanDuel DFS (https://www.fanduel.com/contests)
2. Open browser dev tools (F12) -> Network tab
3. Refresh the page and find any request to api.fanduel.com
4. Copy the 'Authorization' header value (Basic auth token)
5. Copy the 'X-Auth-Token' header value (Session token)

Note: The X-Auth-Token expires periodically and will need to be refreshed.
"""

import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional

import requests

from ..common.exceptions import FanDuelAPIError
from ..common.models import Sport

logger = logging.getLogger(__name__)

FANDUEL_API_BASE = "https://api.fanduel.com"

# Sport code mappings for FanDuel API
SPORT_CODES = {
    Sport.NFL: "NFL",
    Sport.NBA: "NBA",
    Sport.MLB: "MLB",
    Sport.NHL: "NHL",
    Sport.PGA: "PGA",
    Sport.NASCAR: "NASCAR",
}

SPORT_CODE_REVERSE = {v: k for k, v in SPORT_CODES.items()}


class FanDuelApiClient:
    """Client for FanDuel DFS API (read-only).

    This client fetches contest listings, fixture lists (slates), and player
    pool data. It does NOT support lineup submission or entry management.

    Authentication requires manual token extraction from browser dev tools.
    """

    def __init__(
        self,
        basic_auth_token: Optional[str] = None,
        x_auth_token: Optional[str] = None,
        timeout: int = 30,
        rate_limit_delay: float = 0.5,
    ):
        """Initialize FanDuel API client.

        Args:
            basic_auth_token: Authorization header value (Basic auth).
                              Extract from browser dev tools.
            x_auth_token: X-Auth-Token header value (session token).
                          Extract from browser dev tools. Expires periodically.
            timeout: Request timeout in seconds.
            rate_limit_delay: Delay between API calls in seconds.
        """
        self.base_url = FANDUEL_API_BASE
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time = 0.0

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

        # Set auth headers if provided
        if basic_auth_token:
            self.set_auth_token(basic_auth_token)
        if x_auth_token:
            self.set_session_token(x_auth_token)

        self._authenticated = bool(basic_auth_token and x_auth_token)

    def set_auth_token(self, token: str) -> None:
        """Set the Basic Authorization header.

        Args:
            token: Authorization header value. Can be just the base64 token
                   or the full "Basic xyz..." format.
        """
        # Add "Basic " prefix if not present
        if token and not token.startswith("Basic "):
            token = f"Basic {token}"
        self.session.headers["Authorization"] = token
        logger.info("FanDuel auth token set")

    def set_session_token(self, token: str) -> None:
        """Set the X-Auth-Token header.

        Args:
            token: Session token value
        """
        self.session.headers["X-Auth-Token"] = token
        self._authenticated = True
        logger.info("FanDuel session token set")

    def is_authenticated(self) -> bool:
        """Check if auth tokens are configured."""
        return self._authenticated

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)
        self._last_request_time = time.time()

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an authenticated API request.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            **kwargs: Additional request arguments

        Returns:
            JSON response as dict

        Raises:
            FanDuelAPIError: If request fails or auth is missing
        """
        if not self._authenticated:
            raise FanDuelAPIError(
                "Authentication required. Set auth tokens using set_auth_token() "
                "and set_session_token(). See module docstring for instructions."
            )

        self._rate_limit()

        url = f"{self.base_url}{endpoint}"

        try:
            logger.debug(f"FanDuel API {method} {url}")
            response = self.session.request(
                method, url, timeout=self.timeout, **kwargs
            )

            # Check for auth errors
            if response.status_code == 401:
                raise FanDuelAPIError(
                    "Authentication failed. X-Auth-Token may have expired. "
                    "Please refresh tokens from browser dev tools."
                )

            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            logger.error(f"FanDuel API request failed: {e}")
            raise FanDuelAPIError(f"API request failed: {e}") from e

    def get_fixture_lists(self, sport: Optional[Sport] = None) -> list[dict]:
        """Fetch available fixture lists (slates).

        A fixture list represents a slate of games with a shared player pool.
        Multiple contests can share the same fixture list.

        Args:
            sport: Filter by sport. None returns all sports.

        Returns:
            List of fixture list dictionaries
        """
        data = self._request("GET", "/fixture-lists")

        fixture_lists = data.get("fixture_lists", [])

        # Filter by sport if specified
        if sport:
            sport_code = SPORT_CODES.get(sport, "")
            fixture_lists = [
                fl for fl in fixture_lists
                if fl.get("sport") == sport_code
            ]

        logger.info(f"Fetched {len(fixture_lists)} fixture lists from FanDuel")
        return fixture_lists

    def get_fixture_list(self, fixture_list_id: int) -> dict:
        """Fetch details for a specific fixture list (slate).

        Args:
            fixture_list_id: FanDuel fixture list ID

        Returns:
            Fixture list details including games and teams
        """
        data = self._request("GET", f"/fixture-lists/{fixture_list_id}")
        logger.info(f"Fetched fixture list {fixture_list_id} details")
        return data

    def get_contests(
        self,
        fixture_list_id: int,
        include_restricted: bool = False,
    ) -> list[dict]:
        """Fetch contests for a specific fixture list (slate).

        Args:
            fixture_list_id: FanDuel fixture list ID
            include_restricted: Include restricted contests

        Returns:
            List of contest dictionaries
        """
        params = {
            "fixture_list": fixture_list_id,
            "include_restricted": str(include_restricted).lower(),
        }

        data = self._request("GET", "/contests", params=params)

        contests = data.get("contests", [])
        logger.info(
            f"Fetched {len(contests)} contests for fixture list {fixture_list_id}"
        )
        return contests

    def get_players(self, fixture_list_id: int) -> list[dict]:
        """Fetch player pool for a fixture list (slate).

        Args:
            fixture_list_id: FanDuel fixture list ID

        Returns:
            List of player dictionaries with salaries and projections
        """
        data = self._request("GET", f"/fixture-lists/{fixture_list_id}/players")

        players = data.get("players", [])
        logger.info(
            f"Fetched {len(players)} players for fixture list {fixture_list_id}"
        )
        return players

    def verify_auth(self) -> dict:
        """Verify authentication by fetching current user info.

        Returns:
            User info dict if authenticated

        Raises:
            FanDuelAPIError: If not authenticated or token expired
        """
        data = self._request("GET", "/users/current")
        user_id = data.get("users", [{}])[0].get("id")
        logger.info(f"FanDuel auth verified for user {user_id}")
        return data


def parse_fixture_list(raw: dict) -> dict:
    """Parse raw API fixture list into normalized format.

    Args:
        raw: Raw fixture list dictionary from API

    Returns:
        Normalized fixture list dictionary
    """
    # Parse start time
    start_date = raw.get("start_date")
    if start_date:
        try:
            slate_start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            slate_start = datetime.now()
    else:
        slate_start = datetime.now()

    # Get sport
    sport_code = raw.get("sport", "")
    sport = SPORT_CODE_REVERSE.get(sport_code, Sport.NFL)

    return {
        "id": raw.get("id"),
        "sport": sport,
        "label": raw.get("label", ""),
        "slate_start": slate_start,
        "salary_cap": raw.get("salary_cap", 60000),  # FanDuel default
        "contest_count": raw.get("contest_count", 0),
        "games": raw.get("games", []),
        "_raw": raw,
    }


def parse_contest(raw: dict) -> dict:
    """Parse raw API contest data into normalized format.

    Args:
        raw: Raw contest dictionary from API

    Returns:
        Normalized contest dictionary
    """
    # Extract entry fee
    entry_fee_data = raw.get("entry_fee", {})
    if isinstance(entry_fee_data, dict):
        entry_fee = Decimal(str(entry_fee_data.get("value", 0)))
    else:
        entry_fee = Decimal(str(entry_fee_data or 0))

    # Extract prize pool
    prize_data = raw.get("prizes", {}).get("total", {})
    if isinstance(prize_data, dict):
        prize_pool = Decimal(str(prize_data.get("value", 0)))
    else:
        prize_pool = Decimal(str(prize_data or 0))

    # Parse start time
    start_date = raw.get("start_date")
    if start_date:
        try:
            slate_start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            slate_start = datetime.now()
    else:
        slate_start = datetime.now()

    return {
        "id": str(raw.get("id", "")),
        "fixture_list_id": raw.get("fixture_list", {}).get("id"),
        "name": raw.get("name", ""),
        "entry_fee": entry_fee,
        "max_entries": raw.get("max_entries", 1),
        "entry_count": raw.get("entry_count", 0),
        "size": raw.get("size", {}).get("max", 0),  # Max total entries
        "prize_pool": prize_pool,
        "slate_start": slate_start,
        "is_guaranteed": raw.get("guaranteed", False),
        "contest_type": raw.get("contest_type", ""),
        "salary_cap": raw.get("salary_cap", 60000),
        "_raw": raw,
    }


def parse_player(raw: dict, fixture_list_id: int) -> dict:
    """Parse raw API player data into normalized format.

    Args:
        raw: Raw player dictionary from API
        fixture_list_id: Fixture list this player belongs to

    Returns:
        Normalized player dictionary
    """
    # Extract team info
    team_data = raw.get("team", {})

    # Extract game info
    fixture_data = raw.get("fixture", {})

    return {
        "fanduel_player_id": str(raw.get("id", "")),
        "name": f"{raw.get('first_name', '')} {raw.get('last_name', '')}".strip(),
        "first_name": raw.get("first_name", ""),
        "last_name": raw.get("last_name", ""),
        "team": team_data.get("abbreviation", ""),
        "team_name": team_data.get("full_name", ""),
        "position": raw.get("position", ""),
        "salary": raw.get("salary", 0),
        "fppg": raw.get("fppg", 0.0),  # Fantasy points per game
        "fixture_list_id": fixture_list_id,
        "game_id": fixture_data.get("id"),
        "injury_status": raw.get("injury_status"),
        "injury_details": raw.get("injury_details"),
        "_raw": raw,
    }


# Module-level client instance
_client: Optional[FanDuelApiClient] = None


def get_api_client() -> FanDuelApiClient:
    """Get or create the module-level API client.

    Note: Tokens must be set separately using set_auth_token() and
    set_session_token() before making API calls.

    Returns:
        FanDuelApiClient instance
    """
    global _client
    if _client is None:
        _client = FanDuelApiClient()
    return _client
