"""Yahoo Daily Fantasy player pool fetching and CSV export."""
import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from selenium.webdriver.remote.webdriver import WebDriver

from ..common.config import get_config
from ..common.database import get_database, PlayerPoolDB
from ..common.exceptions import YahooPlayerPoolError, YahooAPIError
from ..common.models import Player, Sport
from .api import get_api_client, parse_api_player

logger = logging.getLogger(__name__)

YAHOO_DFS_BASE_URL = "https://sports.yahoo.com/dailyfantasy"


class PlayerPoolFetcher:
    """Fetches player pool data from Yahoo DFS contests.

    This class now primarily uses the Yahoo DFS API for fetching players,
    which is faster and more reliable than web scraping. It also provides
    additional data like Yahoo's built-in projections, odds, and weather.
    """

    def __init__(self):
        """Initialize player pool fetcher."""
        self.config = get_config()
        self.db = get_database()
        self.api_client = get_api_client()
        self.download_dir = Path(self.config.data_dir) / "player_pools"
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def fetch_player_pool(
        self,
        contest_id: str,
        sport: Optional[Sport] = None,
        save_csv: bool = True,
        save_to_db: bool = True,
        driver: Optional[WebDriver] = None,  # No longer required
    ) -> list[Player]:
        """Fetch player pool for a contest from Yahoo DFS API.

        Args:
            contest_id: Yahoo contest ID
            sport: Sport for this contest (used for CSV naming, auto-detected from API)
            save_csv: Whether to save player pool to CSV
            save_to_db: Whether to save players to database
            driver: WebDriver (no longer required, kept for backward compatibility)

        Returns:
            List of Player objects
        """
        logger.info(f"Fetching player pool for contest {contest_id} from API...")

        try:
            # Fetch from API
            raw_players = self.api_client.get_contest_players(contest_id)

            # Parse players
            players = []
            for raw in raw_players:
                try:
                    parsed = parse_api_player(raw, contest_id)
                    player = Player(
                        yahoo_player_id=parsed["yahoo_player_id"],
                        player_game_code=parsed.get("player_game_code"),  # Full ID for CSV upload
                        name=parsed["name"],
                        team=parsed["team"],
                        position=parsed["position"],
                        salary=parsed["salary"],
                        game_time=parsed["game_time"],
                        opponent=parsed["opponent"],
                        projected_points=parsed["projected_points"],  # Yahoo's projections!
                        injury_status=parsed.get("injury_status"),
                        injury_note=parsed.get("injury_note"),
                    )
                    # Store extended data as attributes
                    player._api_data = parsed
                    players.append(player)
                except Exception as e:
                    logger.debug(f"Failed to parse player: {e}")
                    continue

            logger.info(f"Fetched {len(players)} players for contest {contest_id}")

            # Detect sport from player IDs if not provided
            if not sport and players:
                sport = self._detect_sport(players[0].yahoo_player_id)

            # Save to CSV
            if save_csv and sport:
                self._save_player_pool_csv(players, contest_id, sport)

            # Save to database
            if save_to_db and players:
                self._save_players_to_db(players, contest_id)

            return players

        except YahooAPIError as e:
            logger.error(f"API error fetching players: {e}")
            raise YahooPlayerPoolError(f"Player pool fetch failed: {e}") from e
        except Exception as e:
            logger.error(f"Failed to fetch player pool: {e}")
            raise YahooPlayerPoolError(f"Player pool fetch failed: {e}") from e

    def fetch_player_pool_extended(
        self,
        contest_id: str,
    ) -> list[dict]:
        """Fetch player pool with all extended API data.

        Returns raw parsed data including:
        - Yahoo's projected points
        - Fantasy points per game (FPPG)
        - Fantasy points history
        - Odds (spread, over/under)
        - Weather info
        - Injury status

        Args:
            contest_id: Yahoo contest ID

        Returns:
            List of player dictionaries with all available fields
        """
        raw_players = self.api_client.get_contest_players(contest_id)
        return [parse_api_player(raw, contest_id) for raw in raw_players]

    def _detect_sport(self, player_id: str) -> Sport:
        """Detect sport from player ID prefix.

        Args:
            player_id: Yahoo player ID (e.g., "nfl.p.30977")

        Returns:
            Sport enum value
        """
        sport_prefixes = {
            "nfl": Sport.NFL,
            "nba": Sport.NBA,
            "mlb": Sport.MLB,
            "nhl": Sport.NHL,
            "golf": Sport.PGA,
            "pga": Sport.PGA,
        }

        for prefix, sport in sport_prefixes.items():
            if player_id.lower().startswith(prefix):
                return sport

        return Sport.NFL  # Default

    def _save_player_pool_csv(
        self,
        players: list[Player],
        contest_id: str,
        sport: Sport,
    ) -> Path:
        """Save player pool to CSV file.

        Args:
            players: List of players
            contest_id: Contest ID
            sport: Sport name

        Returns:
            Path to saved CSV file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{sport.value}_{contest_id}_{timestamp}.csv"
        filepath = self.download_dir / filename

        # Include extended data if available
        fieldnames = [
            "ID", "Name", "Position", "Team", "Salary",
            "Opponent", "Game Time", "Yahoo Projected Points",
            "FPPG", "Spread", "Over/Under", "Weather", "Injury Status",
        ]

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for player in players:
                # Get extended data if available
                api_data = getattr(player, "_api_data", {})

                writer.writerow({
                    "ID": player.yahoo_player_id,
                    "Name": player.name,
                    "Position": player.position,
                    "Team": player.team,
                    "Salary": player.salary,
                    "Opponent": player.opponent or "",
                    "Game Time": player.game_time.isoformat() if player.game_time else "",
                    "Yahoo Projected Points": player.projected_points or "",
                    "FPPG": api_data.get("fppg", ""),
                    "Spread": api_data.get("spread", ""),
                    "Over/Under": api_data.get("over_under", ""),
                    "Weather": api_data.get("weather", ""),
                    "Injury Status": api_data.get("injury_status", ""),
                })

        logger.info(f"Saved player pool to {filepath}")
        return filepath

    def _save_players_to_db(self, players: list[Player], contest_id: str) -> None:
        """Save players to database.

        Args:
            players: List of players
            contest_id: Contest ID
        """
        session = self.db.get_session()
        try:
            for player in players:
                # Get extended API data if available
                api_data = getattr(player, '_api_data', {})

                # Check if player already exists for this contest
                existing = (
                    session.query(PlayerPoolDB)
                    .filter_by(contest_id=contest_id, yahoo_player_id=player.yahoo_player_id)
                    .first()
                )

                if existing:
                    # Update existing
                    existing.salary = player.salary
                    existing.is_active = not player.is_excluded
                    # Update Yahoo projections and extended data
                    existing.yahoo_projected_points = player.projected_points
                    existing.fppg = api_data.get("fppg")
                    existing.spread = api_data.get("spread")
                    existing.over_under = api_data.get("over_under")
                    existing.weather = api_data.get("weather")
                    existing.injury_status = player.injury_status
                    existing.injury_note = player.injury_note
                else:
                    # Insert new
                    db_player = PlayerPoolDB(
                        contest_id=contest_id,
                        yahoo_player_id=player.yahoo_player_id,
                        player_game_code=player.player_game_code,  # Required for CSV upload
                        name=player.name,
                        team=player.team,
                        position=player.position,
                        salary=player.salary,
                        game_time=player.game_time,
                        opponent=player.opponent,
                        injury_status=player.injury_status,
                        injury_note=player.injury_note,
                        # Yahoo projections and extended data
                        yahoo_projected_points=player.projected_points,
                        fppg=api_data.get("fppg"),
                        spread=api_data.get("spread"),
                        over_under=api_data.get("over_under"),
                        weather=api_data.get("weather"),
                    )
                    session.add(db_player)

            session.commit()
            logger.info(f"Saved {len(players)} players to database")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save players: {e}")
        finally:
            session.close()

    def get_player_pool_from_db(self, contest_id: str) -> list[Player]:
        """Load player pool from database.

        Args:
            contest_id: Contest ID

        Returns:
            List of Player objects
        """
        session = self.db.get_session()
        try:
            db_players = (
                session.query(PlayerPoolDB)
                .filter_by(contest_id=contest_id)
                .all()
            )

            return [
                Player(
                    yahoo_player_id=p.yahoo_player_id,
                    player_game_code=p.player_game_code,  # Required for CSV upload
                    name=p.name,
                    team=p.team,
                    position=p.position,
                    salary=p.salary,
                    game_time=p.game_time,
                    opponent=p.opponent,
                    injury_status=p.injury_status,
                    injury_note=p.injury_note,
                )
                for p in db_players
            ]
        finally:
            session.close()


def fetch_player_pool(
    contest_id: str,
    sport: Optional[Sport] = None,
    driver: Optional[WebDriver] = None,  # Kept for backward compatibility
) -> list[Player]:
    """Convenience function to fetch player pool.

    Args:
        contest_id: Contest ID
        sport: Sport (optional, auto-detected)
        driver: WebDriver (no longer required)

    Returns:
        List of Player objects
    """
    fetcher = PlayerPoolFetcher()
    return fetcher.fetch_player_pool(contest_id, sport)


def get_player_pool_with_odds(contest_id: str) -> list[dict]:
    """Get player pool with all extended data including odds and weather.

    Args:
        contest_id: Yahoo contest ID

    Returns:
        List of player dictionaries with extended data
    """
    fetcher = PlayerPoolFetcher()
    return fetcher.fetch_player_pool_extended(contest_id)
