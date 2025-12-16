"""DailyFantasyFuel projection source - scrapes projections from dailyfantasyfuel.com."""
import logging
import re
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ...common.exceptions import ProjectionFetchError
from ...common.models import Projection, Sport
from .base import ProjectionSource

logger = logging.getLogger(__name__)


class DailyFantasyFuelSource(ProjectionSource):
    """Scrapes player projections from DailyFantasyFuel.

    DFF provides free projections for DraftKings and FanDuel formats.
    We scrape FanDuel projections since Yahoo scoring is closer to FanDuel.
    """

    BASE_URL = "https://www.dailyfantasyfuel.com"

    # Sport URL paths on DFF
    SPORT_PATHS = {
        Sport.NFL: "nfl",
        Sport.NBA: "nba",
        Sport.MLB: "mlb",
        Sport.NHL: "nhl",
        Sport.PGA: "pga",
    }

    # DFF uses DraftKings by default, map to Yahoo positions
    POSITION_MAP = {
        # NFL
        "QB": "QB",
        "RB": "RB",
        "WR": "WR",
        "TE": "TE",
        "K": "K",
        "DST": "DEF",
        "DEF": "DEF",
        "FLEX": "FLEX",
        # NBA
        "PG": "PG",
        "SG": "SG",
        "SF": "SF",
        "PF": "PF",
        "C": "C",
        "G": "G",
        "F": "F",
        "UTIL": "UTIL",
        # MLB
        "P": "P",
        "SP": "SP",
        "RP": "RP",
        "1B": "1B",
        "2B": "2B",
        "3B": "3B",
        "SS": "SS",
        "OF": "OF",
        # NHL
        "LW": "W",
        "RW": "W",
        "W": "W",
        "D": "D",
    }

    def __init__(self, weight: float = 1.0):
        """Initialize DailyFantasyFuel source.

        Args:
            weight: Weight for aggregation (0.0-1.0)
        """
        super().__init__(name="dailyfantasyfuel", weight=weight)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    def fetch_projections(
        self,
        sport: Sport,
        slate_date: Optional[datetime] = None,
    ) -> list[Projection]:
        """Fetch projections from DailyFantasyFuel.

        Args:
            sport: Sport to fetch projections for
            slate_date: Date of slate (not used - DFF shows current slate)

        Returns:
            List of Projection objects

        Raises:
            ProjectionFetchError: If fetch fails
        """
        if sport not in self.SPORT_PATHS:
            raise ProjectionFetchError(f"Sport {sport} not supported by DailyFantasyFuel")

        logger.info(f"Fetching {sport.value} projections from DailyFantasyFuel...")

        try:
            # Build URL for projections page
            # Use FanDuel projections - Yahoo scoring is closer to FanDuel than DraftKings
            sport_path = self.SPORT_PATHS[sport]
            url = f"{self.BASE_URL}/{sport_path}/projections/fanduel"

            # Fetch page
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            # Parse projections
            projections = self._parse_projections_page(response.text, sport)

            logger.info(f"Fetched {len(projections)} projections from DailyFantasyFuel")
            return projections

        except requests.RequestException as e:
            logger.error(f"Failed to fetch from DailyFantasyFuel: {e}")
            raise ProjectionFetchError(f"HTTP error: {e}") from e
        except Exception as e:
            logger.error(f"Failed to fetch projections: {e}")
            raise ProjectionFetchError(str(e)) from e

    def _parse_projections_page(self, html: str, sport: Sport) -> list[Projection]:
        """Parse projections from HTML page.

        DFF page structure (as of 2024):
        - Table with grouped header rows
        - Row 0: Grouped column headers (PLAYER, MATCHUP, PROJECTIONS, etc.)
        - Row 1: Detailed column headers (POS, NAME, SALARY, TEAM, OPP, etc.)
        - Row 2: Separator row
        - Row 3+: Player data

        Args:
            html: Page HTML content
            sport: Sport being parsed

        Returns:
            List of Projection objects
        """
        soup = BeautifulSoup(html, "html.parser")
        projections = []

        # Find the projections table
        table = soup.find("table")
        if not table:
            logger.warning("Could not find projections table")
            return []

        rows = table.find_all("tr")
        if len(rows) < 4:
            logger.warning(f"Table has insufficient rows: {len(rows)}")
            return []

        # Skip header rows (0, 1) and separator row (2), start parsing at row 3
        for row in rows[3:]:
            try:
                projection = self._parse_player_row(row, sport)
                if projection:
                    projections.append(projection)
            except Exception as e:
                logger.debug(f"Failed to parse row: {e}")
                continue

        return projections

    def _parse_player_row(self, row, sport: Sport) -> Optional[Projection]:
        """Parse a single player row.

        DFF FanDuel table cell structure varies by sport (as of Dec 2024):

        NFL structure (has DvP, no START):
        - Cell 0: Player card (combined info - skip)
        - Cell 1: POS
        - Cell 2: NAME
        - Cell 3: SALARY
        - Cell 4: TEAM
        - Cell 5: OPP
        - Cell 6: DvP
        - Cell 7: FD FP PROJECTED <- projection
        - Cell 8: VALUE
        - Cell 9+: Historical averages

        NBA structure (has START and DvP):
        - Cell 0: Player card (combined info - skip)
        - Cell 1: POS
        - Cell 2: NAME
        - Cell 3: SALARY
        - Cell 4: START
        - Cell 5: TEAM
        - Cell 6: OPP
        - Cell 7: DvP
        - Cell 8: FD FP PROJECTED <- projection
        - Cell 9: VALUE
        - Cell 10+: Historical averages

        NHL structure (no START, no DvP):
        - Cell 0: Player card (combined info - skip)
        - Cell 1: POS
        - Cell 2: NAME
        - Cell 3: SALARY
        - Cell 4: TEAM
        - Cell 5: OPP
        - Cell 6: FD FP PROJECTED <- projection
        - Cell 7: VALUE
        - Cell 8+: Lines/Historical averages

        Args:
            row: BeautifulSoup row element
            sport: Sport for position mapping

        Returns:
            Projection object or None
        """
        cells = row.find_all("td")
        if len(cells) < 8:  # Need at least 8 cells to get projected points
            return None

        try:
            # Extract core player info
            position = cells[1].get_text(strip=True).upper()
            name = cells[2].get_text(strip=True)
            salary_text = cells[3].get_text(strip=True)

            # Column indices differ by sport
            if sport == Sport.NBA:
                # NBA: has START (cell 4) and DvP (cell 7)
                team_idx, opp_idx, proj_idx, value_idx = 5, 6, 8, 9
                hist_start_idx = 10
            elif sport == Sport.NFL:
                # NFL: has DvP (cell 6), no START
                team_idx, opp_idx, proj_idx, value_idx = 4, 5, 7, 8
                hist_start_idx = 9
            else:
                # NHL/MLB/others: no START, no DvP
                team_idx, opp_idx, proj_idx, value_idx = 4, 5, 6, 7
                hist_start_idx = 8

            team = cells[team_idx].get_text(strip=True).upper() if len(cells) > team_idx else ""
            opponent = cells[opp_idx].get_text(strip=True).upper() if len(cells) > opp_idx else ""

            # Clean up name - remove injury status suffixes (Q, O, D, etc.)
            name = re.sub(r'[QODP]$', '', name).strip()

            # Clean up team - remove non-team strings like "EXP."
            if team in ("EXP.", "EXP", ""):
                team = ""

            # Skip empty rows
            if not name or not position:
                return None

            # Parse salary (format: "$8.3k" or "$8300")
            salary = self._parse_salary(salary_text)

            # Parse projected points (FD FP PROJECTED)
            proj_fpts_text = cells[proj_idx].get_text(strip=True) if len(cells) > proj_idx else "0"
            projected_points = self._parse_float(proj_fpts_text)

            # Parse value (points per $1000)
            value_text = cells[value_idx].get_text(strip=True) if len(cells) > value_idx else "0"
            value = self._parse_float(value_text)

            # Parse historical averages for floor/ceiling estimation
            l5_avg = self._parse_float(cells[hist_start_idx].get_text(strip=True)) if len(cells) > hist_start_idx else None
            l10_avg = self._parse_float(cells[hist_start_idx + 1].get_text(strip=True)) if len(cells) > hist_start_idx + 1 else None
            season_avg = self._parse_float(cells[hist_start_idx + 2].get_text(strip=True)) if len(cells) > hist_start_idx + 2 else None

            # Estimate floor and ceiling from historical data
            floor = None
            ceiling = None
            if l5_avg and season_avg:
                # Floor: lower of recent avg and season avg, minus some variance
                floor = min(l5_avg, season_avg) * 0.7
                # Ceiling: higher of recent avg and projection, plus some upside
                ceiling = max(l5_avg, projected_points) * 1.4

            # Map position to Yahoo format
            yahoo_position = self.POSITION_MAP.get(position, position)

            return Projection(
                name=name,
                team=team,
                position=yahoo_position,
                source=self.name,
                projected_points=projected_points,
                projected_ownership=None,  # DFF doesn't show ownership on this page
                floor=floor,
                ceiling=ceiling,
            )

        except Exception as e:
            logger.debug(f"Failed to parse player row: {e}")
            return None

    def _parse_salary(self, text: str) -> int:
        """Parse salary from text, handling various formats.

        Args:
            text: Salary text (e.g., "$8.3k", "$8300", "8300")

        Returns:
            Salary as integer
        """
        if not text:
            return 0

        # Check for "k" suffix (thousands)
        if "k" in text.lower():
            # Extract number and multiply by 1000
            cleaned = re.sub(r"[^\d.]", "", text)
            try:
                return int(float(cleaned) * 1000)
            except ValueError:
                return 0
        else:
            # Regular number
            cleaned = re.sub(r"[^\d]", "", text)
            try:
                return int(cleaned) if cleaned else 0
            except ValueError:
                return 0

    def _parse_float(self, text: str) -> float:
        """Parse float from text, handling various formats.

        Args:
            text: Text containing number

        Returns:
            Parsed float value
        """
        if not text:
            return 0.0
        # Remove non-numeric characters except decimal point and minus
        cleaned = re.sub(r"[^\d.\-]", "", text)
        try:
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0

    def _parse_percentage(self, text: str) -> Optional[float]:
        """Parse percentage from text.

        Args:
            text: Text containing percentage

        Returns:
            Float value (0.0-1.0) or None
        """
        if not text:
            return None
        # Remove % sign and parse
        cleaned = re.sub(r"[^\d.]", "", text)
        try:
            value = float(cleaned)
            # Convert from percentage to decimal if needed
            return value / 100 if value > 1 else value
        except ValueError:
            return None

    def is_available(self, sport: Sport) -> bool:
        """Check if DFF has projections for this sport.

        Args:
            sport: Sport to check

        Returns:
            True if sport is supported
        """
        return sport in self.SPORT_PATHS

    def get_available_slates(self, sport: Sport) -> list[str]:
        """Get available slate dates/times from DFF.

        Note: DFF typically shows projections for the current/next slate.

        Args:
            sport: Sport to check

        Returns:
            List of available slate identifiers
        """
        # DFF shows current slate by default
        return ["main"]
