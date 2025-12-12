"""Yahoo-specific optimizer configuration using pydfs-lineup-optimizer."""
import logging
from typing import Optional

from pydfs_lineup_optimizer import Site, Sport as PDFSSport, get_optimizer, LineupOptimizer
from pydfs_lineup_optimizer.player import Player as PDFSPlayer
from pydfs_lineup_optimizer.settings import BaseSettings, LineupPosition
from pydfs_lineup_optimizer.sites.sites_registry import SitesRegistry
from pydfs_lineup_optimizer.sites.yahoo.importer import YahooCSVImporter
from pydfs_lineup_optimizer.lineup_exporter import CSVLineupExporter

from ..common.config import get_config
from ..common.models import Sport, Player

logger = logging.getLogger(__name__)


# Map our Sport enum to pydfs-lineup-optimizer Sport enum
SPORT_MAPPING = {
    Sport.NFL: PDFSSport.FOOTBALL,
    Sport.NBA: PDFSSport.BASKETBALL,
    Sport.MLB: PDFSSport.BASEBALL,
    Sport.NHL: PDFSSport.HOCKEY,
    Sport.PGA: PDFSSport.GOLF,
    Sport.NASCAR: PDFSSport.NASCAR,
    Sport.SOCCER: PDFSSport.SOCCER,
}


# Yahoo roster configurations by sport (multi-game / classic)
YAHOO_ROSTER_CONFIG = {
    Sport.NFL: {
        "salary_cap": 200,  # Yahoo uses $200 cap
        "positions": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DEF"],
        "max_from_team": None,  # No limit
    },
    Sport.NBA: {
        "salary_cap": 200,
        "positions": ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL"],
        "max_from_team": 4,
    },
    Sport.MLB: {
        "salary_cap": 200,
        "positions": ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"],
        "max_from_team": None,
    },
    Sport.NHL: {
        "salary_cap": 200,
        "positions": ["C", "C", "W", "W", "W", "D", "D", "G", "UTIL"],
        "max_from_team": None,
    },
}

# Yahoo single-game roster configurations by sport
# Structure: 1 SUPERSTAR (1.5x points) + 4 FLEX
# Note: No kickers (K) in Yahoo single-game
YAHOO_SINGLE_GAME_CONFIG = {
    Sport.NFL: {
        # Salary cap varies by seriesId - this is just the default
        "salary_cap": 200,
        "positions": ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"],
        # Eligible positions for each slot (any player can be SUPERSTAR)
        "superstar_positions": ("QB", "RB", "WR", "TE", "DEF"),
        "flex_positions": ("QB", "RB", "WR", "TE", "DEF"),
        "max_from_team": None,
        "superstar_multiplier": 1.5,
    },
    Sport.NBA: {
        "salary_cap": 200,
        "positions": ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"],
        "superstar_positions": ("PG", "SG", "SF", "PF", "C"),
        "flex_positions": ("PG", "SG", "SF", "PF", "C"),
        "max_from_team": None,
        "superstar_multiplier": 1.5,
    },
    Sport.MLB: {
        "salary_cap": 200,
        "positions": ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"],
        "superstar_positions": ("P", "C", "1B", "2B", "3B", "SS", "OF"),
        "flex_positions": ("P", "C", "1B", "2B", "3B", "SS", "OF"),
        "max_from_team": None,
        "superstar_multiplier": 1.5,
    },
    Sport.NHL: {
        "salary_cap": 200,
        "positions": ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"],
        "superstar_positions": ("C", "W", "D", "G"),
        "flex_positions": ("C", "W", "D", "G"),
        "max_from_team": None,
        "superstar_multiplier": 1.5,
    },
}


# Custom site ID for Yahoo Single Game (not in pydfs-lineup-optimizer natively)
class YahooSingleGameSite:
    """Pseudo-site identifier for Yahoo Single Game."""
    YAHOO_SINGLE_GAME = "YAHOO_SINGLE_GAME"


class YahooSingleGameSettingsBase(BaseSettings):
    """Base settings for Yahoo Single Game contests.

    Yahoo single-game format:
    - 1 SUPERSTAR position (any player, 1.5x points - handled in player loading)
    - 4 FLEX positions (any player)
    - Salary cap varies by seriesId
    """
    site = Site.YAHOO  # Use Yahoo site for compatibility
    budget = 200  # Default, overridden per-contest
    csv_importer = YahooCSVImporter
    csv_exporter = CSVLineupExporter


# Note: These settings classes are NOT registered via @SitesRegistry.register_settings
# because doing so would override Yahoo's default multi-game settings.
# They are used only when explicitly creating a single-game optimizer via YahooSingleGameOptimizer.


class YahooSingleGameFootballSettings(YahooSingleGameSettingsBase):
    """Yahoo Single Game NFL settings."""
    sport = PDFSSport.FOOTBALL
    positions = [
        LineupPosition('SUPERSTAR', ('SUPERSTAR',)),
        LineupPosition('FLEX', ('QB', 'RB', 'WR', 'TE', 'DEF')),
        LineupPosition('FLEX', ('QB', 'RB', 'WR', 'TE', 'DEF')),
        LineupPosition('FLEX', ('QB', 'RB', 'WR', 'TE', 'DEF')),
        LineupPosition('FLEX', ('QB', 'RB', 'WR', 'TE', 'DEF')),
    ]


class YahooSingleGameBasketballSettings(YahooSingleGameSettingsBase):
    """Yahoo Single Game NBA settings."""
    sport = PDFSSport.BASKETBALL
    positions = [
        LineupPosition('SUPERSTAR', ('SUPERSTAR',)),
        LineupPosition('FLEX', ('PG', 'SG', 'SF', 'PF', 'C')),
        LineupPosition('FLEX', ('PG', 'SG', 'SF', 'PF', 'C')),
        LineupPosition('FLEX', ('PG', 'SG', 'SF', 'PF', 'C')),
        LineupPosition('FLEX', ('PG', 'SG', 'SF', 'PF', 'C')),
    ]


class YahooSingleGameBaseballSettings(YahooSingleGameSettingsBase):
    """Yahoo Single Game MLB settings."""
    sport = PDFSSport.BASEBALL
    positions = [
        LineupPosition('SUPERSTAR', ('SUPERSTAR',)),
        LineupPosition('FLEX', ('P', 'C', '1B', '2B', '3B', 'SS', 'OF')),
        LineupPosition('FLEX', ('P', 'C', '1B', '2B', '3B', 'SS', 'OF')),
        LineupPosition('FLEX', ('P', 'C', '1B', '2B', '3B', 'SS', 'OF')),
        LineupPosition('FLEX', ('P', 'C', '1B', '2B', '3B', 'SS', 'OF')),
    ]


class YahooSingleGameHockeySettings(YahooSingleGameSettingsBase):
    """Yahoo Single Game NHL settings."""
    sport = PDFSSport.HOCKEY
    positions = [
        LineupPosition('SUPERSTAR', ('SUPERSTAR',)),
        LineupPosition('FLEX', ('C', 'W', 'D', 'G')),
        LineupPosition('FLEX', ('C', 'W', 'D', 'G')),
        LineupPosition('FLEX', ('C', 'W', 'D', 'G')),
        LineupPosition('FLEX', ('C', 'W', 'D', 'G')),
    ]


# Map sport to single-game settings class
YAHOO_SINGLE_GAME_SETTINGS = {
    Sport.NFL: YahooSingleGameFootballSettings,
    Sport.NBA: YahooSingleGameBasketballSettings,
    Sport.MLB: YahooSingleGameBaseballSettings,
    Sport.NHL: YahooSingleGameHockeySettings,
}


class YahooOptimizer:
    """Wrapper around pydfs-lineup-optimizer for Yahoo DFS."""

    def __init__(self, sport: Sport, skip_config: bool = False):
        """Initialize Yahoo optimizer for a sport.

        Args:
            sport: Sport to optimize for
            skip_config: If True, skip loading config (useful for testing)
        """
        self.sport = sport
        self.sport_config = None

        if not skip_config:
            try:
                self.config = get_config()
                self.sport_config = self.config.get_sport_config(sport.value)
            except Exception as e:
                logger.warning(f"Could not load config: {e}. Using defaults.")

        # Create pydfs optimizer
        pdfs_sport = SPORT_MAPPING.get(sport)
        if not pdfs_sport:
            raise ValueError(f"Sport {sport} not supported")

        self._optimizer: LineupOptimizer = get_optimizer(Site.YAHOO, pdfs_sport)
        self._players_loaded = False

        logger.info(f"Initialized Yahoo optimizer for {sport.value}")

    @property
    def optimizer(self) -> LineupOptimizer:
        """Get the underlying pydfs-lineup-optimizer instance."""
        return self._optimizer

    def load_players(self, players: list[Player]) -> None:
        """Load players into the optimizer.

        Args:
            players: List of Player objects with projections
        """
        if not players:
            logger.warning("No players to load")
            return

        # Convert to pydfs player format
        pdfs_players = []
        for player in players:
            if player.projected_points is None or player.projected_points <= 0:
                continue  # Skip players without projections

            pdfs_player = self._convert_player(player)
            if pdfs_player:
                pdfs_players.append(pdfs_player)

        # Load into optimizer
        self._optimizer.load_players(pdfs_players)
        self._players_loaded = True

        # Yahoo requires lineups to have players from at least 3 different teams
        # This must be set AFTER players are loaded
        self._optimizer.set_total_teams(min_teams=3)

        logger.info(f"Loaded {len(pdfs_players)} players into optimizer")

    def _convert_player(self, player: Player) -> Optional[PDFSPlayer]:
        """Convert our Player model to pydfs Player.

        Args:
            player: Our Player object

        Returns:
            pydfs Player or None if conversion fails
        """
        try:
            # Create pydfs player
            # Note: pydfs-lineup-optimizer expects specific format
            pdfs_player = PDFSPlayer(
                player_id=player.yahoo_player_id,
                first_name=player.name.split()[0] if player.name else "",
                last_name=" ".join(player.name.split()[1:]) if player.name else "",
                positions=[player.position],
                team=player.team,
                salary=player.salary,
                fppg=player.projected_points or 0,
                is_injured=player.is_excluded,
            )

            # Set exposure limits if specified
            if player.max_exposure is not None:
                pdfs_player.max_exposure = player.max_exposure
            if player.min_exposure is not None:
                pdfs_player.min_exposure = player.min_exposure

            return pdfs_player

        except Exception as e:
            logger.debug(f"Failed to convert player {player.name}: {e}")
            return None

    def load_players_from_csv(self, csv_path: str) -> None:
        """Load players from Yahoo CSV export.

        Args:
            csv_path: Path to Yahoo player pool CSV
        """
        self._optimizer.load_players_from_csv(csv_path)
        self._players_loaded = True
        logger.info(f"Loaded players from CSV: {csv_path}")

    def set_player_exposure(
        self,
        player_id: str,
        min_exposure: Optional[float] = None,
        max_exposure: Optional[float] = None,
    ) -> None:
        """Set exposure limits for a specific player.

        Args:
            player_id: Yahoo player ID
            min_exposure: Minimum exposure (0.0-1.0)
            max_exposure: Maximum exposure (0.0-1.0)
        """
        player = self._optimizer.player_pool.get_player_by_id(player_id)
        if player:
            if min_exposure is not None:
                player.min_exposure = min_exposure
            if max_exposure is not None:
                player.max_exposure = max_exposure
            logger.debug(f"Set exposure for {player.full_name}: min={min_exposure}, max={max_exposure}")

    def lock_player(self, player_id: str) -> None:
        """Lock a player into all lineups.

        Args:
            player_id: Yahoo player ID
        """
        player = self._optimizer.player_pool.get_player_by_id(player_id)
        if player:
            self._optimizer.add_player_to_lineup(player)
            logger.info(f"Locked player: {player.full_name}")

    def exclude_player(self, player_id: str) -> None:
        """Exclude a player from all lineups.

        Args:
            player_id: Yahoo player ID
        """
        player = self._optimizer.player_pool.get_player_by_id(player_id)
        if player:
            self._optimizer.remove_player(player)
            logger.info(f"Excluded player: {player.full_name}")

    def set_max_from_team(self, team: str, max_players: int) -> None:
        """Set maximum players from a team.

        Args:
            team: Team abbreviation
            max_players: Maximum players allowed
        """
        self._optimizer.set_players_from_one_team({team: max_players})

    def add_stack(self, team: str, positions: list[str], count: int = 2) -> None:
        """Add a team stack requirement.

        Args:
            team: Team to stack
            positions: Positions to include in stack
            count: Number of players in stack
        """
        # pydfs-lineup-optimizer has various stacking options
        # This is a simplified version
        self._optimizer.add_players_from_one_team(team)
        logger.info(f"Added {count}-player stack for {team}")

    def set_global_exposure(
        self,
        min_exposure: float = 0.0,
        max_exposure: float = 1.0,
    ) -> None:
        """Set global exposure limits for all players.

        Args:
            min_exposure: Minimum exposure (0.0-1.0)
            max_exposure: Maximum exposure (0.0-1.0)
        """
        for player in self._optimizer.player_pool.all_players:
            player.min_exposure = min_exposure
            player.max_exposure = max_exposure

        logger.info(f"Set global exposure: min={min_exposure}, max={max_exposure}")

    def optimize(
        self,
        num_lineups: int = 1,
        randomness: Optional[float] = None,
    ) -> list:
        """Generate optimized lineups.

        Args:
            num_lineups: Number of lineups to generate
            randomness: Optional randomness factor (0.0-1.0)

        Returns:
            List of optimized Lineup objects from pydfs
        """
        if not self._players_loaded:
            raise RuntimeError("No players loaded. Call load_players() first.")

        # Set randomness if specified
        if randomness is not None:
            from pydfs_lineup_optimizer import RandomFantasyPointsStrategy
            self._optimizer.set_fantasy_points_strategy(
                RandomFantasyPointsStrategy(randomness)
            )

        # Generate lineups
        lineups = list(self._optimizer.optimize(n=num_lineups))
        logger.info(f"Generated {len(lineups)} optimized lineups")

        return lineups

    def reset(self) -> None:
        """Reset optimizer state for new optimization."""
        # Re-create optimizer
        pdfs_sport = SPORT_MAPPING.get(self.sport)
        self._optimizer = get_optimizer(Site.YAHOO, pdfs_sport)
        self._players_loaded = False
        logger.info("Optimizer reset")


class YahooSingleGameOptimizer:
    """Optimizer for Yahoo single-game contests.

    Yahoo single-game format:
    - 1 SUPERSTAR position (1.5x points multiplier)
    - 4 FLEX positions
    - Any player (QB, RB, WR, TE, DEF for NFL) can be SUPERSTAR
    - Salary cap varies by seriesId

    The approach follows FanDuel's MVP pattern:
    - Duplicate each player with a SUPERSTAR version having 1.5x projected points
    - Let optimizer naturally select the best SUPERSTAR candidate
    """

    def __init__(
        self,
        sport: Sport,
        salary_cap: Optional[int] = None,
        skip_config: bool = False,
    ):
        """Initialize single-game optimizer.

        Args:
            sport: Sport to optimize for
            salary_cap: Salary cap from API (varies by seriesId)
            skip_config: If True, skip loading config
        """
        self.sport = sport
        self.salary_cap = salary_cap
        self.sport_config = None

        if not skip_config:
            try:
                self.config = get_config()
                self.sport_config = self.config.get_sport_config(sport.value)
            except Exception as e:
                logger.warning(f"Could not load config: {e}. Using defaults.")

        # Get single-game config for this sport
        if sport not in YAHOO_SINGLE_GAME_CONFIG:
            raise ValueError(f"Sport {sport} not supported for single-game")

        self.sg_config = YAHOO_SINGLE_GAME_CONFIG[sport]
        self.multiplier = self.sg_config["superstar_multiplier"]

        # Use provided salary cap or default
        if salary_cap is None:
            self.salary_cap = self.sg_config["salary_cap"]

        # Get the custom single-game settings class for this sport
        pdfs_sport = SPORT_MAPPING.get(sport)
        if not pdfs_sport:
            raise ValueError(f"Sport {sport} not supported")

        if sport not in YAHOO_SINGLE_GAME_SETTINGS:
            raise ValueError(f"Sport {sport} not supported for single-game")

        # Get our custom settings class and configure it with the salary cap
        base_settings_class = YAHOO_SINGLE_GAME_SETTINGS[sport]

        # Create a dynamic subclass with the correct budget
        # This avoids mutating the class-level attribute
        class DynamicSettings(base_settings_class):
            budget = self.salary_cap

        self._optimizer = LineupOptimizer(DynamicSettings)

        self._players_loaded = False
        self._original_players: list[Player] = []

        logger.info(
            f"Initialized Yahoo single-game optimizer for {sport.value} "
            f"(salary cap: ${self.salary_cap})"
        )

    @property
    def optimizer(self) -> LineupOptimizer:
        """Get the underlying pydfs-lineup-optimizer instance."""
        return self._optimizer

    def load_players(self, players: list[Player]) -> None:
        """Load players into the optimizer.

        Creates duplicates for each player:
        - Original player with FLEX-eligible positions
        - SUPERSTAR version with 1.5x projected points

        Args:
            players: List of Player objects with projections
        """
        if not players:
            logger.warning("No players to load")
            return

        self._original_players = players

        # Convert to pydfs player format with SUPERSTAR/FLEX handling
        pdfs_players = []
        superstar_count = 0
        flex_count = 0

        for player in players:
            if player.projected_points is None or player.projected_points <= 0:
                continue  # Skip players without projections

            # Create FLEX version (original position)
            flex_player = self._convert_player_flex(player)
            if flex_player:
                pdfs_players.append(flex_player)
                flex_count += 1

            # Create SUPERSTAR version (1.5x points)
            superstar_player = self._convert_player_superstar(player)
            if superstar_player:
                pdfs_players.append(superstar_player)
                superstar_count += 1

        # Load into optimizer
        self._optimizer.load_players(pdfs_players)
        self._players_loaded = True

        # For single-game contests, we only have 2 teams so don't enforce team diversity
        # For multi-game slates, require at least 3 different teams
        available_teams = len(set(p.team for p in players if p.team))
        if available_teams >= 3:
            self._optimizer.set_total_teams(min_teams=3)
        # If only 2 teams (single-game), skip team diversity constraint

        logger.info(
            f"Loaded {len(pdfs_players)} players into single-game optimizer "
            f"({superstar_count} SUPERSTAR + {flex_count} FLEX)"
        )

    def _convert_player_flex(self, player: Player) -> Optional[PDFSPlayer]:
        """Convert player to FLEX-eligible pydfs Player.

        Args:
            player: Our Player object

        Returns:
            pydfs Player or None if conversion fails
        """
        try:
            # FLEX players keep their original position for eligibility
            # but will be placed in FLEX slots
            pdfs_player = PDFSPlayer(
                player_id=f"{player.yahoo_player_id}_FLEX",
                first_name=player.name.split()[0] if player.name else "",
                last_name=" ".join(player.name.split()[1:]) if player.name else "",
                positions=[player.position],  # Original position for eligibility
                team=player.team,
                salary=player.salary,
                fppg=player.projected_points or 0,
                is_injured=player.is_excluded,
            )

            # Store original ID for later retrieval
            pdfs_player._yahoo_player_id = player.yahoo_player_id
            pdfs_player._is_superstar = False

            if player.max_exposure is not None:
                pdfs_player.max_exposure = player.max_exposure
            if player.min_exposure is not None:
                pdfs_player.min_exposure = player.min_exposure

            return pdfs_player

        except Exception as e:
            logger.debug(f"Failed to convert player {player.name} to FLEX: {e}")
            return None

    def _convert_player_superstar(self, player: Player) -> Optional[PDFSPlayer]:
        """Convert player to SUPERSTAR-eligible pydfs Player with 1.5x points.

        Args:
            player: Our Player object

        Returns:
            pydfs Player with boosted projection or None if conversion fails
        """
        try:
            # SUPERSTAR version gets 1.5x projected points
            boosted_points = (player.projected_points or 0) * self.multiplier

            pdfs_player = PDFSPlayer(
                player_id=f"{player.yahoo_player_id}_SUPERSTAR",
                first_name=player.name.split()[0] if player.name else "",
                last_name=" ".join(player.name.split()[1:]) if player.name else "",
                # SUPERSTAR position for the optimizer
                positions=["SUPERSTAR"],
                team=player.team,
                salary=player.salary,
                fppg=boosted_points,  # 1.5x projected points
                is_injured=player.is_excluded,
            )

            # Store original ID and superstar flag
            pdfs_player._yahoo_player_id = player.yahoo_player_id
            pdfs_player._is_superstar = True
            pdfs_player._original_fppg = player.projected_points or 0

            if player.max_exposure is not None:
                pdfs_player.max_exposure = player.max_exposure
            if player.min_exposure is not None:
                pdfs_player.min_exposure = player.min_exposure

            return pdfs_player

        except Exception as e:
            logger.debug(f"Failed to convert player {player.name} to SUPERSTAR: {e}")
            return None

    def optimize(
        self,
        num_lineups: int = 1,
        randomness: Optional[float] = None,
    ) -> list:
        """Generate optimized single-game lineups.

        Args:
            num_lineups: Number of lineups to generate
            randomness: Optional randomness factor (0.0-1.0)

        Returns:
            List of optimized Lineup objects from pydfs
        """
        if not self._players_loaded:
            raise RuntimeError("No players loaded. Call load_players() first.")

        # Set randomness if specified
        if randomness is not None:
            from pydfs_lineup_optimizer import RandomFantasyPointsStrategy
            self._optimizer.set_fantasy_points_strategy(
                RandomFantasyPointsStrategy(randomness)
            )

        # Generate lineups
        lineups = list(self._optimizer.optimize(n=num_lineups))
        logger.info(f"Generated {len(lineups)} optimized single-game lineups")

        return lineups

    def get_player_lookup(self) -> dict:
        """Get lookup from yahoo_player_id to original Player.

        Returns:
            Dict mapping yahoo_player_id to Player object
        """
        return {p.yahoo_player_id: p for p in self._original_players}

    def set_player_exposure(
        self,
        player_id: str,
        min_exposure: Optional[float] = None,
        max_exposure: Optional[float] = None,
    ) -> None:
        """Set exposure limits for a player (both SUPERSTAR and FLEX versions).

        Args:
            player_id: Yahoo player ID
            min_exposure: Minimum exposure (0.0-1.0)
            max_exposure: Maximum exposure (0.0-1.0)
        """
        for suffix in ["_SUPERSTAR", "_FLEX"]:
            full_id = f"{player_id}{suffix}"
            try:
                player = self._optimizer.player_pool.get_player_by_id(full_id)
                if player:
                    if min_exposure is not None:
                        player.min_exposure = min_exposure
                    if max_exposure is not None:
                        player.max_exposure = max_exposure
            except:
                pass

    def lock_player(self, player_id: str, as_superstar: bool = False) -> None:
        """Lock a player into all lineups.

        Args:
            player_id: Yahoo player ID
            as_superstar: If True, lock as SUPERSTAR; otherwise as FLEX
        """
        suffix = "_SUPERSTAR" if as_superstar else "_FLEX"
        full_id = f"{player_id}{suffix}"

        player = self._optimizer.player_pool.get_player_by_id(full_id)
        if player:
            self._optimizer.add_player_to_lineup(player)
            pos_name = "SUPERSTAR" if as_superstar else "FLEX"
            logger.info(f"Locked player as {pos_name}: {player.full_name}")

    def exclude_player(self, player_id: str) -> None:
        """Exclude a player from all lineups (both versions).

        Args:
            player_id: Yahoo player ID
        """
        for suffix in ["_SUPERSTAR", "_FLEX"]:
            full_id = f"{player_id}{suffix}"
            try:
                player = self._optimizer.player_pool.get_player_by_id(full_id)
                if player:
                    self._optimizer.remove_player(player)
            except:
                pass
        logger.info(f"Excluded player: {player_id}")

    def set_global_exposure(
        self,
        min_exposure: float = 0.0,
        max_exposure: float = 1.0,
    ) -> None:
        """Set global exposure limits for all players.

        Args:
            min_exposure: Minimum exposure (0.0-1.0)
            max_exposure: Maximum exposure (0.0-1.0)
        """
        for player in self._optimizer.player_pool.all_players:
            player.min_exposure = min_exposure
            player.max_exposure = max_exposure

        logger.info(f"Set global exposure: min={min_exposure}, max={max_exposure}")

    def set_max_from_team(self, team: str, max_players: int) -> None:
        """Set maximum players from a team.

        Args:
            team: Team abbreviation
            max_players: Maximum players allowed
        """
        self._optimizer.set_players_from_one_team({team: max_players})

    def reset(self) -> None:
        """Reset optimizer state for new optimization."""
        base_settings_class = YAHOO_SINGLE_GAME_SETTINGS[self.sport]

        class DynamicSettings(base_settings_class):
            budget = self.salary_cap

        self._optimizer = LineupOptimizer(DynamicSettings)
        self._players_loaded = False
        self._original_players = []
        logger.info("Single-game optimizer reset")


def create_optimizer(
    sport: Sport,
    single_game: bool = False,
    salary_cap: Optional[int] = None
) -> "YahooOptimizer | YahooSingleGameOptimizer":
    """Factory function to create Yahoo optimizer.

    Args:
        sport: Sport to optimize for
        single_game: If True, create single-game optimizer
        salary_cap: Salary cap (only used for single-game)

    Returns:
        Configured YahooOptimizer or YahooSingleGameOptimizer instance
    """
    if single_game:
        return YahooSingleGameOptimizer(sport, salary_cap=salary_cap)
    return YahooOptimizer(sport)


def is_single_game_contest(contest_data: dict) -> bool:
    """Determine if a contest is single-game based on API data.

    Args:
        contest_data: Contest data from Yahoo API (parsed format)

    Returns:
        True if single-game contest
    """
    slate_type = contest_data.get("slate_type", "")
    return slate_type.upper() == "SINGLE_GAME"
