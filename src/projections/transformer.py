"""Transform projections to match Yahoo player pool format."""
import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from ..common.models import Player, Projection

logger = logging.getLogger(__name__)


class ProjectionTransformer:
    """Transforms and matches projections to Yahoo player pool.

    Handles:
    - Name matching (fuzzy matching for variations)
    - Position mapping between sites
    - Team abbreviation normalization
    """

    # Common name variations and corrections
    NAME_CORRECTIONS = {
        # NFL
        "patrick mahomes ii": "patrick mahomes",
        "marvin harrison jr": "marvin harrison",
        "travis etienne jr": "travis etienne",
        "michael pittman jr": "michael pittman",
        "odell beckham jr": "odell beckham",
        "melvin gordon iii": "melvin gordon",
        "darrell henderson jr": "darrell henderson",
        "kenneth walker iii": "kenneth walker",
        "brian robinson jr": "brian robinson",
        "pierre strong jr": "pierre strong",
        # NBA
        "lebron james": "lebron james",
        "stephen curry": "steph curry",
        "ja morant": "ja morant",
        # Common suffixes to remove
    }

    # Team abbreviation mappings (DK/FD/DFF -> Yahoo)
    TEAM_MAPPING = {
        # NFL - DFF uses different codes
        "JAC": "JAX",
        "JAX": "JAC",  # Some sources use JAX, Yahoo uses JAC
        "LAR": "LA",   # DFF uses LAR, Yahoo uses LA for Rams
        "LV": "LV",
        "LAC": "LAC",
        # NBA
        "BKN": "BRK",
        "PHX": "PHO",
        "CHA": "CHO",
        # MLB - generally consistent
        # NHL - generally consistent
    }

    # NFL team nickname to full name mapping (for DEF matching)
    # DFF shows "Rams", Yahoo shows "Los Angeles Rams"
    NFL_TEAM_NICKNAMES = {
        "49ers": "San Francisco 49ers",
        "bears": "Chicago Bears",
        "bengals": "Cincinnati Bengals",
        "bills": "Buffalo Bills",
        "broncos": "Denver Broncos",
        "browns": "Cleveland Browns",
        "buccaneers": "Tampa Bay Buccaneers",
        "cardinals": "Arizona Cardinals",
        "chargers": "Los Angeles Chargers",
        "chiefs": "Kansas City Chiefs",
        "colts": "Indianapolis Colts",
        "commanders": "Washington Commanders",
        "cowboys": "Dallas Cowboys",
        "dolphins": "Miami Dolphins",
        "eagles": "Philadelphia Eagles",
        "falcons": "Atlanta Falcons",
        "giants": "New York Giants",
        "jaguars": "Jacksonville Jaguars",
        "jets": "New York Jets",
        "lions": "Detroit Lions",
        "packers": "Green Bay Packers",
        "panthers": "Carolina Panthers",
        "patriots": "New England Patriots",
        "raiders": "Las Vegas Raiders",
        "rams": "Los Angeles Rams",
        "ravens": "Baltimore Ravens",
        "saints": "New Orleans Saints",
        "seahawks": "Seattle Seahawks",
        "steelers": "Pittsburgh Steelers",
        "texans": "Houston Texans",
        "titans": "Tennessee Titans",
        "vikings": "Minnesota Vikings",
    }

    def __init__(self, match_threshold: float = 0.85):
        """Initialize transformer.

        Args:
            match_threshold: Minimum similarity ratio for fuzzy matching (0.0-1.0)
        """
        self.match_threshold = match_threshold
        self._player_lookup: dict[str, Player] = {}

    def build_player_lookup(self, players: list[Player]) -> None:
        """Build lookup dict from player pool for fast matching.

        Args:
            players: List of Player objects from Yahoo
        """
        self._player_lookup.clear()

        for player in players:
            # Create multiple lookup keys for each player
            keys = self._generate_lookup_keys(player.name, player.team, player.position)
            for key in keys:
                if key not in self._player_lookup:
                    self._player_lookup[key] = player

        logger.info(f"Built player lookup with {len(self._player_lookup)} keys for {len(players)} players")

    def _generate_lookup_keys(self, name: str, team: str, position: str) -> list[str]:
        """Generate multiple lookup keys for a player.

        Args:
            name: Player name
            team: Team abbreviation
            position: Position

        Returns:
            List of normalized lookup keys
        """
        keys = []
        # Pass position to normalize_name for DEF special handling
        normalized_name = self._normalize_name(name, position)
        normalized_team = self._normalize_team(team)

        # Primary key: name + team
        keys.append(f"{normalized_name}|{normalized_team}")

        # Name only (for unique names)
        keys.append(normalized_name)

        # Name + position (for common names)
        keys.append(f"{normalized_name}|{position.upper()}")

        # Last name + team (for quick lookups)
        name_parts = normalized_name.split()
        if len(name_parts) >= 2:
            last_name = name_parts[-1]
            keys.append(f"{last_name}|{normalized_team}")

        return keys

    def _normalize_name(self, name: str, position: str = "") -> str:
        """Normalize player name for matching.

        For DEF positions, expands team nicknames to full names.
        E.g., "Rams" -> "Los Angeles Rams"

        Args:
            name: Raw player name
            position: Player position (used for DEF special handling)

        Returns:
            Normalized name
        """
        # Convert to lowercase
        name = name.lower().strip()

        # For DEF position, try to expand team nickname to full name
        if position.upper() == "DEF":
            if name in self.NFL_TEAM_NICKNAMES:
                name = self.NFL_TEAM_NICKNAMES[name].lower()

        # Apply known corrections
        if name in self.NAME_CORRECTIONS:
            name = self.NAME_CORRECTIONS[name]

        # Remove common suffixes
        name = re.sub(r"\s+(jr\.?|sr\.?|ii|iii|iv|v)$", "", name, flags=re.IGNORECASE)

        # Remove punctuation
        name = re.sub(r"[.'`\-]", "", name)

        # Normalize whitespace
        name = re.sub(r"\s+", " ", name).strip()

        return name

    def _normalize_team(self, team: str) -> str:
        """Normalize team abbreviation.

        Args:
            team: Raw team abbreviation

        Returns:
            Normalized abbreviation
        """
        team = team.upper().strip()
        return self.TEAM_MAPPING.get(team, team)

    def match_projection_to_player(
        self,
        projection: Projection,
        players: Optional[list[Player]] = None,
    ) -> Optional[Player]:
        """Match a projection to a Yahoo player.

        Args:
            projection: Projection to match
            players: Optional player list (uses built lookup if None)

        Returns:
            Matched Player or None if no match found
        """
        if players:
            self.build_player_lookup(players)

        if not self._player_lookup:
            logger.warning("No player lookup available")
            return None

        # Try exact match first
        keys_to_try = self._generate_lookup_keys(
            projection.name,
            projection.team,
            projection.position,
        )

        for key in keys_to_try:
            if key in self._player_lookup:
                return self._player_lookup[key]

        # Try fuzzy matching
        return self._fuzzy_match(projection)

    def _fuzzy_match(self, projection: Projection) -> Optional[Player]:
        """Attempt fuzzy name matching for projection.

        Args:
            projection: Projection to match

        Returns:
            Best matching Player or None
        """
        proj_name = self._normalize_name(projection.name)
        proj_team = self._normalize_team(projection.team)

        best_match: Optional[Player] = None
        best_ratio = 0.0

        # Get unique players from lookup
        seen_ids = set()
        for player in self._player_lookup.values():
            if player.yahoo_player_id in seen_ids:
                continue
            seen_ids.add(player.yahoo_player_id)

            player_name = self._normalize_name(player.name)

            # Calculate similarity
            ratio = SequenceMatcher(None, proj_name, player_name).ratio()

            # Boost score if team matches
            if self._normalize_team(player.team) == proj_team:
                ratio += 0.1

            # Boost score if position matches
            if player.position.upper() == projection.position.upper():
                ratio += 0.05

            if ratio > best_ratio and ratio >= self.match_threshold:
                best_ratio = ratio
                best_match = player

        if best_match:
            logger.debug(f"Fuzzy matched '{projection.name}' to '{best_match.name}' (ratio={best_ratio:.2f})")

        return best_match

    def transform_projections(
        self,
        projections: list[Projection],
        players: list[Player],
    ) -> list[Projection]:
        """Transform projections by matching to Yahoo players.

        Updates each projection with yahoo_player_id if match found.

        Args:
            projections: List of projections to transform
            players: Yahoo player pool

        Returns:
            List of projections with yahoo_player_id populated
        """
        self.build_player_lookup(players)

        matched = 0
        unmatched = []

        for projection in projections:
            player = self.match_projection_to_player(projection)

            if player:
                projection.yahoo_player_id = player.yahoo_player_id
                matched += 1
            else:
                unmatched.append(projection.name)

        logger.info(f"Matched {matched}/{len(projections)} projections")
        if unmatched:
            logger.warning(f"Unmatched projections: {unmatched[:10]}{'...' if len(unmatched) > 10 else ''}")

        return projections

    def merge_projections_to_players(
        self,
        projections: list[Projection],
        players: list[Player],
    ) -> list[Player]:
        """Merge projection data into player objects.

        Players retain their Yahoo API fields (yahoo_player_id, player_game_code, etc.)
        and receive projection data (projected_points, projected_ownership).

        Args:
            projections: List of projections
            players: Yahoo player pool

        Returns:
            Players with projection data populated
        """
        # Transform projections first
        self.transform_projections(projections, players)

        # Create projection lookup by yahoo_player_id
        proj_lookup = {
            p.yahoo_player_id: p
            for p in projections
            if p.yahoo_player_id
        }

        # Merge into players (preserving all Yahoo API fields)
        merged_count = 0
        for player in players:
            if player.yahoo_player_id in proj_lookup:
                proj = proj_lookup[player.yahoo_player_id]
                player.projected_points = proj.projected_points
                player.projected_ownership = proj.projected_ownership
                merged_count += 1

        logger.info(f"Merged projections for {merged_count}/{len(players)} players")
        return players


def transform_and_merge(
    projections: list[Projection],
    players: list[Player],
    match_threshold: float = 0.85,
) -> list[Player]:
    """Convenience function to transform and merge projections.

    Args:
        projections: List of projections from any source
        players: Yahoo player pool
        match_threshold: Fuzzy match threshold

    Returns:
        Players with projection data merged
    """
    transformer = ProjectionTransformer(match_threshold)
    return transformer.merge_projections_to_players(projections, players)
