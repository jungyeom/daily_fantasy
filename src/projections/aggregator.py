"""Aggregates projections from multiple sources with configurable weights."""
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from ..common.config import get_config
from ..common.models import Player, Projection, Sport
from .sources.base import ProjectionSource
from .sources.dailyfantasyfuel import DailyFantasyFuelSource
from .sources.manual import ManualProjectionSource
from .transformer import ProjectionTransformer

logger = logging.getLogger(__name__)


class ProjectionAggregator:
    """Aggregates projections from multiple sources.

    Supports:
    - Weighted averaging of projections
    - Source prioritization
    - Manual overrides
    """

    def __init__(self):
        """Initialize aggregator with configured sources."""
        self.config = get_config()
        self.sources: list[ProjectionSource] = []
        self.transformer = ProjectionTransformer()

        # Initialize sources from config
        self._init_sources()

    def _init_sources(self) -> None:
        """Initialize projection sources from configuration."""
        proj_config = self.config.projections

        # DailyFantasyFuel
        dff_config = proj_config.dailyfantasyfuel
        if dff_config.get("enabled", True):
            weight = dff_config.get("weight", 1.0)
            self.sources.append(DailyFantasyFuelSource(weight=weight))
            logger.info(f"Initialized DailyFantasyFuel source (weight={weight})")

        # Manual overrides
        manual_config = proj_config.manual
        if manual_config.get("enabled", False):
            weight = manual_config.get("weight", 0.0)
            file_path = manual_config.get("file_path")
            self.sources.append(ManualProjectionSource(file_path=file_path, weight=weight))
            logger.info(f"Initialized Manual source (weight={weight})")

    def add_source(self, source: ProjectionSource) -> None:
        """Add a projection source.

        Args:
            source: ProjectionSource to add
        """
        self.sources.append(source)
        logger.info(f"Added projection source: {source.name}")

    def remove_source(self, name: str) -> bool:
        """Remove a projection source by name.

        Args:
            name: Source name to remove

        Returns:
            True if source was removed
        """
        for i, source in enumerate(self.sources):
            if source.name == name:
                del self.sources[i]
                logger.info(f"Removed projection source: {name}")
                return True
        return False

    def fetch_all_projections(
        self,
        sport: Sport,
        slate_date: Optional[datetime] = None,
    ) -> dict[str, list[Projection]]:
        """Fetch projections from all sources.

        Args:
            sport: Sport to fetch
            slate_date: Date of slate

        Returns:
            Dict mapping source name to list of projections
        """
        all_projections = {}

        for source in self.sources:
            if not source.is_available(sport):
                logger.debug(f"Source {source.name} not available for {sport}")
                continue

            try:
                projections = source.fetch_projections(sport, slate_date)
                all_projections[source.name] = projections
                logger.info(f"Fetched {len(projections)} projections from {source.name}")
            except Exception as e:
                logger.error(f"Failed to fetch from {source.name}: {e}")
                continue

        return all_projections

    def aggregate_projections(
        self,
        projections_by_source: dict[str, list[Projection]],
        players: list[Player],
    ) -> list[Projection]:
        """Aggregate projections from multiple sources.

        Uses weighted average based on source weights.
        Manual source with weight > 0 overrides other sources.

        Args:
            projections_by_source: Dict mapping source name to projections
            players: Yahoo player pool for matching

        Returns:
            List of aggregated projections
        """
        # First, transform all projections to match yahoo player IDs
        all_transformed: list[Projection] = []

        for source_name, projections in projections_by_source.items():
            self.transformer.transform_projections(projections, players)
            all_transformed.extend(projections)

        # Group projections by yahoo_player_id
        player_projections: dict[str, list[Projection]] = defaultdict(list)
        for proj in all_transformed:
            if proj.yahoo_player_id:
                player_projections[proj.yahoo_player_id].append(proj)

        # Aggregate each player's projections
        aggregated = []
        source_weights = {s.name: s.weight for s in self.sources}

        for yahoo_id, projs in player_projections.items():
            aggregated_proj = self._aggregate_player_projections(projs, source_weights)
            if aggregated_proj:
                aggregated_proj.yahoo_player_id = yahoo_id
                aggregated.append(aggregated_proj)

        logger.info(f"Aggregated projections for {len(aggregated)} players")
        return aggregated

    def _aggregate_player_projections(
        self,
        projections: list[Projection],
        source_weights: dict[str, float],
    ) -> Optional[Projection]:
        """Aggregate projections for a single player.

        Args:
            projections: All projections for this player
            source_weights: Source name to weight mapping

        Returns:
            Aggregated Projection or None
        """
        if not projections:
            return None

        # Check for manual override (highest priority)
        manual_projs = [p for p in projections if p.source == "manual"]
        if manual_projs and source_weights.get("manual", 0) > 0:
            # Use manual projection directly
            return manual_projs[0]

        # Calculate weighted average
        total_weight = 0.0
        weighted_points = 0.0
        weighted_ownership = 0.0
        weighted_floor = 0.0
        weighted_ceiling = 0.0
        has_ownership = False
        has_floor = False
        has_ceiling = False

        for proj in projections:
            weight = source_weights.get(proj.source, 1.0)
            if weight == 0:
                continue

            total_weight += weight
            weighted_points += proj.projected_points * weight

            if proj.projected_ownership is not None:
                weighted_ownership += proj.projected_ownership * weight
                has_ownership = True

            if proj.floor is not None:
                weighted_floor += proj.floor * weight
                has_floor = True

            if proj.ceiling is not None:
                weighted_ceiling += proj.ceiling * weight
                has_ceiling = True

        if total_weight == 0:
            return None

        # Use first projection as base for name/team/position
        base = projections[0]

        return Projection(
            yahoo_player_id=base.yahoo_player_id,
            name=base.name,
            team=base.team,
            position=base.position,
            source="aggregated",
            projected_points=weighted_points / total_weight,
            projected_ownership=weighted_ownership / total_weight if has_ownership else None,
            floor=weighted_floor / total_weight if has_floor else None,
            ceiling=weighted_ceiling / total_weight if has_ceiling else None,
        )

    def get_projections_for_contest(
        self,
        sport: Sport,
        players: list[Player],
        slate_date: Optional[datetime] = None,
    ) -> list[Player]:
        """Fetch, aggregate, and merge projections for a contest.

        This is the main entry point for getting projections.

        Args:
            sport: Sport
            players: Yahoo player pool
            slate_date: Optional slate date

        Returns:
            Players with projection data merged
        """
        # Fetch from all sources
        projections_by_source = self.fetch_all_projections(sport, slate_date)

        if not projections_by_source:
            logger.warning("No projections fetched from any source")
            return players

        # Aggregate projections
        aggregated = self.aggregate_projections(projections_by_source, players)

        # Merge into players
        return self.transformer.merge_projections_to_players(aggregated, players)


def get_projections(
    sport: Sport,
    players: list[Player],
    slate_date: Optional[datetime] = None,
) -> list[Player]:
    """Convenience function to get aggregated projections.

    Args:
        sport: Sport
        players: Yahoo player pool
        slate_date: Optional slate date

    Returns:
        Players with projection data
    """
    aggregator = ProjectionAggregator()
    return aggregator.get_projections_for_contest(sport, players, slate_date)
