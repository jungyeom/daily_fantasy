"""Daily Faceoff data source - fetches goalie starts and line combinations."""
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ...common.models import Sport

logger = logging.getLogger(__name__)


# Team name to abbreviation mapping for Daily Faceoff
TEAM_ABBREV_MAP = {
    "Anaheim Ducks": "ANA",
    "Arizona Coyotes": "ARI",
    "Boston Bruins": "BOS",
    "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY",
    "Carolina Hurricanes": "CAR",
    "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL",
    "Columbus Blue Jackets": "CBJ",
    "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET",
    "Edmonton Oilers": "EDM",
    "Florida Panthers": "FLA",
    "Los Angeles Kings": "LA",
    "Minnesota Wild": "MIN",
    "Montreal Canadiens": "MTL",
    "Nashville Predators": "NSH",
    "New Jersey Devils": "NJ",
    "New York Islanders": "NYI",
    "New York Rangers": "NYR",
    "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI",
    "Pittsburgh Penguins": "PIT",
    "San Jose Sharks": "SJ",
    "Seattle Kraken": "SEA",
    "St. Louis Blues": "STL",
    "St Louis Blues": "STL",  # Alternate spelling without period
    "Tampa Bay Lightning": "TB",
    "Toronto Maple Leafs": "TOR",
    "Utah Hockey Club": "UTA",
    "Utah Mammoth": "UTA",  # Alternate name
    "Vancouver Canucks": "VAN",
    "Vegas Golden Knights": "VGK",
    "Washington Capitals": "WSH",
    "Winnipeg Jets": "WPG",
}

# Team slug mapping for line combination URLs
TEAM_SLUGS = {
    "ANA": "anaheim-ducks",
    "ARI": "arizona-coyotes",
    "BOS": "boston-bruins",
    "BUF": "buffalo-sabres",
    "CGY": "calgary-flames",
    "CAR": "carolina-hurricanes",
    "CHI": "chicago-blackhawks",
    "COL": "colorado-avalanche",
    "CBJ": "columbus-blue-jackets",
    "DAL": "dallas-stars",
    "DET": "detroit-red-wings",
    "EDM": "edmonton-oilers",
    "FLA": "florida-panthers",
    "LA": "los-angeles-kings",
    "MIN": "minnesota-wild",
    "MTL": "montreal-canadiens",
    "NSH": "nashville-predators",
    "NJ": "new-jersey-devils",
    "NYI": "new-york-islanders",
    "NYR": "new-york-rangers",
    "OTT": "ottawa-senators",
    "PHI": "philadelphia-flyers",
    "PIT": "pittsburgh-penguins",
    "SJ": "san-jose-sharks",
    "SEA": "seattle-kraken",
    "STL": "st-louis-blues",
    "TB": "tampa-bay-lightning",
    "TOR": "toronto-maple-leafs",
    "UTA": "utah-hockey-club",
    "VAN": "vancouver-canucks",
    "VGK": "vegas-golden-knights",
    "WSH": "washington-capitals",
    "WPG": "winnipeg-jets",
}


@dataclass
class GoalieStart:
    """Goalie starting information."""
    name: str
    team: str
    status: str  # "Confirmed", "Likely", "Unconfirmed"
    opponent: str
    wins: int = 0
    losses: int = 0
    otl: int = 0
    gaa: float = 0.0
    save_pct: float = 0.0


@dataclass
class LineCombination:
    """Line combination for a team."""
    team: str
    line_number: int  # 1, 2, 3, 4
    left_wing: str
    center: str
    right_wing: str


@dataclass
class DefensePairing:
    """Defense pairing for a team."""
    team: str
    pair_number: int  # 1, 2, 3
    left_defense: str
    right_defense: str


class DailyFaceoffSource:
    """Fetches goalie starts and line combinations from Daily Faceoff."""

    BASE_URL = "https://www.dailyfaceoff.com"

    def __init__(self):
        """Initialize Daily Faceoff source."""
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    def fetch_goalie_starts(self) -> list[GoalieStart]:
        """Fetch today's goalie starting confirmations.

        Returns:
            List of GoalieStart objects for today's games
        """
        url = f"{self.BASE_URL}/starting-goalies/"
        logger.info("Fetching goalie starts from Daily Faceoff...")

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            # Extract JSON data from Next.js props
            data = self._extract_nextjs_data(response.text)
            if not data:
                logger.warning("Could not extract goalie data from Daily Faceoff")
                return []

            goalie_starts = []
            games = data.get("props", {}).get("pageProps", {}).get("data", [])

            for game in games:
                # Home goalie
                home_goalie = self._parse_goalie(game, is_home=True)
                if home_goalie:
                    goalie_starts.append(home_goalie)

                # Away goalie
                away_goalie = self._parse_goalie(game, is_home=False)
                if away_goalie:
                    goalie_starts.append(away_goalie)

            logger.info(f"Fetched {len(goalie_starts)} goalie starts from Daily Faceoff")
            return goalie_starts

        except requests.RequestException as e:
            logger.error(f"Failed to fetch goalie starts: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing goalie starts: {e}")
            return []

    def _parse_goalie(self, game: dict, is_home: bool) -> Optional[GoalieStart]:
        """Parse goalie information from game data.

        Args:
            game: Game data dict
            is_home: True for home goalie, False for away

        Returns:
            GoalieStart object or None
        """
        prefix = "home" if is_home else "away"
        opp_prefix = "away" if is_home else "home"

        name = game.get(f"{prefix}GoalieName")
        if not name:
            return None

        team_name = game.get(f"{prefix}TeamName", "")
        team = TEAM_ABBREV_MAP.get(team_name, "")

        opp_team_name = game.get(f"{opp_prefix}TeamName", "")
        opponent = TEAM_ABBREV_MAP.get(opp_team_name, "")

        # Determine confirmation status
        strength = game.get(f"{prefix}NewsStrengthName")
        if strength == "Confirmed":
            status = "Confirmed"
        elif strength:
            status = "Likely"
        else:
            status = "Unconfirmed"

        return GoalieStart(
            name=name,
            team=team,
            status=status,
            opponent=opponent,
            wins=game.get(f"{prefix}GoalieWins", 0) or 0,
            losses=game.get(f"{prefix}GoalieLosses", 0) or 0,
            otl=game.get(f"{prefix}GoalieOvertimeLosses", 0) or 0,
            gaa=game.get(f"{prefix}GoalieGoalsAgainstAvg", 0.0) or 0.0,
            save_pct=game.get(f"{prefix}GoalieSavePercentage", 0.0) or 0.0,
        )

    def fetch_line_combinations(self, team: str) -> tuple[list[LineCombination], list[DefensePairing]]:
        """Fetch line combinations for a specific team.

        Args:
            team: Team abbreviation (e.g., "WPG", "VGK")

        Returns:
            Tuple of (forward lines, defense pairings)
        """
        slug = TEAM_SLUGS.get(team)
        if not slug:
            logger.warning(f"Unknown team: {team}")
            return [], []

        url = f"{self.BASE_URL}/teams/{slug}/line-combinations/"
        logger.debug(f"Fetching line combinations for {team} from {url}")

        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            # Extract JSON data from Next.js props
            data = self._extract_nextjs_data(response.text)
            if not data:
                logger.warning(f"Could not extract line data for {team}")
                return [], []

            page_props = data.get("props", {}).get("pageProps", {})
            combinations = page_props.get("combinations", {})
            players = combinations.get("players", [])

            if not players:
                logger.warning(f"No players found for {team}")
                return [], []

            # Group players by their line/pairing
            # groupIdentifier: f1, f2, f3, f4 for forwards, d1, d2, d3 for defense
            # positionIdentifier: lw, c, rw for forwards, ld, rd for defense
            # categoryIdentifier: ev for even strength (what we want)
            forward_groups = {}  # f1 -> {lw: name, c: name, rw: name}
            defense_groups = {}  # d1 -> {ld: name, rd: name}

            for player in players:
                group_id = player.get("groupIdentifier", "")
                category = player.get("categoryIdentifier", "")
                pos_id = player.get("positionIdentifier", "")
                name = player.get("name", "")

                # Only use even strength lines
                if category != "ev":
                    continue

                if group_id.startswith("f") and group_id[1:].isdigit():
                    line_num = group_id
                    if line_num not in forward_groups:
                        forward_groups[line_num] = {}
                    forward_groups[line_num][pos_id] = name

                elif group_id.startswith("d") and group_id[1:].isdigit():
                    pair_num = group_id
                    if pair_num not in defense_groups:
                        defense_groups[pair_num] = {}
                    defense_groups[pair_num][pos_id] = name

            # Build LineCombination objects
            lines = []
            for i in range(1, 5):
                group_key = f"f{i}"
                if group_key in forward_groups:
                    group = forward_groups[group_key]
                    lw = group.get("lw", "")
                    c = group.get("c", "")
                    rw = group.get("rw", "")
                    if lw and c and rw:
                        lines.append(LineCombination(
                            team=team,
                            line_number=i,
                            left_wing=lw,
                            center=c,
                            right_wing=rw,
                        ))

            # Build DefensePairing objects
            pairings = []
            for i in range(1, 4):
                group_key = f"d{i}"
                if group_key in defense_groups:
                    group = defense_groups[group_key]
                    ld = group.get("ld", "")
                    rd = group.get("rd", "")
                    if ld and rd:
                        pairings.append(DefensePairing(
                            team=team,
                            pair_number=i,
                            left_defense=ld,
                            right_defense=rd,
                        ))

            logger.debug(f"Fetched {len(lines)} lines and {len(pairings)} pairings for {team}")
            return lines, pairings

        except requests.RequestException as e:
            logger.error(f"Failed to fetch line combinations for {team}: {e}")
            return [], []
        except Exception as e:
            logger.error(f"Error parsing line combinations for {team}: {e}")
            return [], []

    def fetch_all_line_combinations(self, teams: list[str]) -> dict[str, tuple[list[LineCombination], list[DefensePairing]]]:
        """Fetch line combinations for multiple teams.

        Args:
            teams: List of team abbreviations

        Returns:
            Dict mapping team -> (forward lines, defense pairings)
        """
        all_lines = {}
        for team in teams:
            lines, pairings = self.fetch_line_combinations(team)
            all_lines[team] = (lines, pairings)
        return all_lines

    def _extract_nextjs_data(self, html: str) -> Optional[dict]:
        """Extract Next.js JSON data from HTML page.

        Args:
            html: Page HTML content

        Returns:
            Parsed JSON data or None
        """
        soup = BeautifulSoup(html, "html.parser")

        # Look for Next.js data in script tag with id="__NEXT_DATA__"
        script = soup.find("script", {"id": "__NEXT_DATA__"})
        if script and script.string:
            try:
                return json.loads(script.string)
            except json.JSONDecodeError:
                pass

        # Fallback: look for JSON in any script tag
        for script in soup.find_all("script"):
            if script.string and '"props":{' in script.string:
                # Try to extract JSON object
                match = re.search(r'(\{.*"props":\{.*\})', script.string, re.DOTALL)
                if match:
                    try:
                        return json.loads(match.group(1))
                    except json.JSONDecodeError:
                        continue

        return None

    def get_confirmed_goalies(self) -> dict[str, str]:
        """Get dict of confirmed starting goalies by team.

        Returns:
            Dict mapping team abbreviation -> goalie name (confirmed starters only)
        """
        goalie_starts = self.fetch_goalie_starts()
        confirmed = {}

        for gs in goalie_starts:
            if gs.status == "Confirmed" and gs.team:
                confirmed[gs.team] = gs.name

        logger.info(f"Found {len(confirmed)} confirmed goalie starters")
        return confirmed

    def get_likely_goalies(self) -> dict[str, str]:
        """Get dict of likely starting goalies by team (confirmed + likely).

        Returns:
            Dict mapping team abbreviation -> goalie name
        """
        goalie_starts = self.fetch_goalie_starts()
        likely = {}

        for gs in goalie_starts:
            if gs.status in ("Confirmed", "Likely") and gs.team:
                likely[gs.team] = gs.name

        logger.info(f"Found {len(likely)} confirmed/likely goalie starters")
        return likely

    def get_line_stacks(self, teams: list[str]) -> dict[str, list[list[str]]]:
        """Get forward line stacks for teams.

        Returns a dict mapping team -> list of lines, where each line is
        a list of 3 player names [LW, C, RW].

        Args:
            teams: List of team abbreviations

        Returns:
            Dict mapping team -> list of [LW, C, RW] lines
        """
        stacks = {}

        for team in teams:
            lines, _ = self.fetch_line_combinations(team)
            team_stacks = []

            for line in lines:
                team_stacks.append([
                    line.left_wing,
                    line.center,
                    line.right_wing,
                ])

            if team_stacks:
                stacks[team] = team_stacks

        return stacks


def fetch_goalie_starts() -> list[GoalieStart]:
    """Convenience function to fetch goalie starts.

    Returns:
        List of GoalieStart objects
    """
    source = DailyFaceoffSource()
    return source.fetch_goalie_starts()


def get_confirmed_goalies() -> dict[str, str]:
    """Convenience function to get confirmed goalies.

    Returns:
        Dict mapping team -> goalie name
    """
    source = DailyFaceoffSource()
    return source.get_confirmed_goalies()


def get_line_stacks(teams: list[str]) -> dict[str, list[list[str]]]:
    """Convenience function to get line stacks.

    Args:
        teams: List of team abbreviations

    Returns:
        Dict mapping team -> list of lines
    """
    source = DailyFaceoffSource()
    return source.get_line_stacks(teams)
