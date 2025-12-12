"""GPP optimization strategies - exposure rules, stacking, and correlation."""
import logging
import random
from dataclasses import dataclass, field
from typing import Optional

from ..common.config import get_config, SportConfig
from ..common.models import Sport, Player

logger = logging.getLogger(__name__)


@dataclass
class ExposureRule:
    """Exposure limit rule for a player or group."""
    player_id: Optional[str] = None
    player_name: Optional[str] = None
    position: Optional[str] = None
    team: Optional[str] = None
    min_exposure: float = 0.0
    max_exposure: float = 1.0


@dataclass
class StackRule:
    """Team stacking rule for GPP correlation."""
    team: str
    positions: list[str]
    min_count: int = 2
    max_count: int = 4


@dataclass
class CorrelationRule:
    """Position correlation rule (e.g., QB + WR)."""
    primary_position: str
    correlated_positions: list[str]
    same_team: bool = True
    min_correlated: int = 1


@dataclass
class GPPStrategy:
    """Complete GPP optimization strategy."""
    name: str = "default"

    # Exposure settings
    default_max_exposure: float = 0.5
    default_min_exposure: float = 0.0

    # Specific exposure rules
    exposure_rules: list[ExposureRule] = field(default_factory=list)

    # Stacking rules
    stack_rules: list[StackRule] = field(default_factory=list)

    # Correlation rules
    correlation_rules: list[CorrelationRule] = field(default_factory=list)

    # Randomness for lineup diversity
    randomness: float = 0.1

    # Ownership leverage
    fade_high_ownership: bool = True
    high_ownership_threshold: float = 0.25  # 25%+
    high_ownership_max_exposure: float = 0.3

    # Salary optimization
    min_salary_usage: float = 0.95  # Use at least 95% of cap
    target_unique_players: int = 0  # Min unique players across lineups


class StrategyBuilder:
    """Builds GPP strategies for different sports."""

    def __init__(self, sport: Sport):
        """Initialize strategy builder.

        Args:
            sport: Sport to build strategy for
        """
        self.sport = sport
        self.config = get_config()
        self.sport_config = self.config.get_sport_config(sport.value)

    def build_default_strategy(self) -> GPPStrategy:
        """Build default GPP strategy for sport.

        Returns:
            GPPStrategy instance
        """
        strategy = GPPStrategy(
            name=f"{self.sport.value}_default",
            default_max_exposure=self.config.optimizer.default_max_exposure,
            default_min_exposure=self.config.optimizer.default_min_exposure,
            randomness=self.config.optimizer.randomness,
            min_salary_usage=self.config.optimizer.min_salary_usage,
        )

        # Add sport-specific rules
        if self.sport == Sport.NFL:
            strategy = self._add_nfl_rules(strategy)
        elif self.sport == Sport.NBA:
            strategy = self._add_nba_rules(strategy)
        elif self.sport == Sport.MLB:
            strategy = self._add_mlb_rules(strategy)
        elif self.sport == Sport.NHL:
            strategy = self._add_nhl_rules(strategy)

        return strategy

    def _add_nfl_rules(self, strategy: GPPStrategy) -> GPPStrategy:
        """Add NFL-specific strategy rules.

        Args:
            strategy: Base strategy to modify

        Returns:
            Modified strategy
        """
        # QB + pass catcher correlation
        strategy.correlation_rules.append(CorrelationRule(
            primary_position="QB",
            correlated_positions=["WR", "TE"],
            same_team=True,
            min_correlated=1,
        ))

        # Position exposure limits
        strategy.exposure_rules.extend([
            ExposureRule(position="QB", max_exposure=0.4),
            ExposureRule(position="DEF", max_exposure=0.3),
            ExposureRule(position="TE", max_exposure=0.4),
        ])

        return strategy

    def _add_nba_rules(self, strategy: GPPStrategy) -> GPPStrategy:
        """Add NBA-specific strategy rules.

        Args:
            strategy: Base strategy to modify

        Returns:
            Modified strategy
        """
        # Higher exposure allowed in NBA (more chalk)
        strategy.default_max_exposure = 0.6

        # Stars get higher exposure
        strategy.exposure_rules.extend([
            ExposureRule(position="PG", max_exposure=0.5),
            ExposureRule(position="C", max_exposure=0.5),
        ])

        return strategy

    def _add_mlb_rules(self, strategy: GPPStrategy) -> GPPStrategy:
        """Add MLB-specific strategy rules.

        Args:
            strategy: Base strategy to modify

        Returns:
            Modified strategy
        """
        # Pitcher exposure lower (high variance)
        strategy.exposure_rules.append(
            ExposureRule(position="P", max_exposure=0.35)
        )

        # Stack batters from same team
        strategy.stack_rules.append(StackRule(
            team="*",  # Any team
            positions=["C", "1B", "2B", "3B", "SS", "OF"],
            min_count=3,
            max_count=5,
        ))

        return strategy

    def _add_nhl_rules(self, strategy: GPPStrategy) -> GPPStrategy:
        """Add NHL-specific strategy rules.

        Args:
            strategy: Base strategy to modify

        Returns:
            Modified strategy
        """
        # Goalie exposure lower
        strategy.exposure_rules.append(
            ExposureRule(position="G", max_exposure=0.3)
        )

        # Line stacking
        strategy.stack_rules.append(StackRule(
            team="*",
            positions=["C", "W"],
            min_count=2,
            max_count=3,
        ))

        return strategy

    def build_custom_strategy(
        self,
        max_exposure: float = 0.5,
        randomness: float = 0.1,
        fade_chalk: bool = True,
        stack_size: int = 2,
    ) -> GPPStrategy:
        """Build custom GPP strategy with specified parameters.

        Args:
            max_exposure: Maximum player exposure
            randomness: Lineup diversity factor
            fade_chalk: Whether to fade high-owned players
            stack_size: Target stack size

        Returns:
            Customized GPPStrategy
        """
        strategy = GPPStrategy(
            name=f"{self.sport.value}_custom",
            default_max_exposure=max_exposure,
            randomness=randomness,
            fade_high_ownership=fade_chalk,
        )

        # Add basic stack rule
        if stack_size >= 2:
            strategy.stack_rules.append(StackRule(
                team="*",
                positions=["*"],
                min_count=stack_size,
            ))

        return strategy


def apply_strategy_to_optimizer(
    optimizer,
    strategy: GPPStrategy,
    players: list[Player],
) -> None:
    """Apply GPP strategy settings to optimizer.

    Args:
        optimizer: YahooOptimizer instance
        strategy: GPPStrategy to apply
        players: Player list for reference
    """
    # Set global exposure
    optimizer.set_global_exposure(
        min_exposure=strategy.default_min_exposure,
        max_exposure=strategy.default_max_exposure,
    )

    # Apply player-specific exposure rules
    for rule in strategy.exposure_rules:
        matching_players = _find_matching_players(players, rule)
        for player in matching_players:
            optimizer.set_player_exposure(
                player.yahoo_player_id,
                min_exposure=rule.min_exposure,
                max_exposure=rule.max_exposure,
            )

    # Apply ownership leverage
    if strategy.fade_high_ownership:
        for player in players:
            if player.projected_ownership and player.projected_ownership > strategy.high_ownership_threshold:
                optimizer.set_player_exposure(
                    player.yahoo_player_id,
                    max_exposure=strategy.high_ownership_max_exposure,
                )

    logger.info(f"Applied strategy: {strategy.name}")


def _find_matching_players(players: list[Player], rule: ExposureRule) -> list[Player]:
    """Find players matching an exposure rule.

    Args:
        players: All players
        rule: Exposure rule to match

    Returns:
        List of matching players
    """
    matches = []

    for player in players:
        # Match by player ID
        if rule.player_id and player.yahoo_player_id == rule.player_id:
            matches.append(player)
            continue

        # Match by name
        if rule.player_name and rule.player_name.lower() in player.name.lower():
            matches.append(player)
            continue

        # Match by position
        if rule.position and player.position.upper() == rule.position.upper():
            matches.append(player)
            continue

        # Match by team
        if rule.team and player.team.upper() == rule.team.upper():
            matches.append(player)
            continue

    return matches


def generate_exposure_weights(
    players: list[Player],
    strategy: GPPStrategy,
) -> dict[str, float]:
    """Generate exposure weights for all players based on strategy.

    Args:
        players: List of players
        strategy: GPP strategy

    Returns:
        Dict mapping player_id to max exposure
    """
    weights = {}

    for player in players:
        max_exp = strategy.default_max_exposure

        # Adjust for ownership
        if strategy.fade_high_ownership and player.projected_ownership:
            if player.projected_ownership > strategy.high_ownership_threshold:
                max_exp = min(max_exp, strategy.high_ownership_max_exposure)

        # Apply randomness
        if strategy.randomness > 0:
            variance = max_exp * strategy.randomness
            max_exp = max(0.1, min(1.0, max_exp + random.uniform(-variance, variance)))

        weights[player.yahoo_player_id] = max_exp

    return weights
