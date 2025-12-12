"""News/projection monitoring for late swap triggers."""
import logging
from datetime import datetime
from typing import Optional

from ..common.config import get_config
from ..common.models import Projection, Player, Sport
from ..projections.aggregator import ProjectionAggregator

logger = logging.getLogger(__name__)


class NewsMonitor:
    """Monitors projection changes and player status updates.

    Currently focused on re-fetching projections from sources.
    Could be extended to include Twitter API, RSS feeds, etc.
    """

    def __init__(self):
        """Initialize news monitor."""
        self.config = get_config()
        self.aggregator = ProjectionAggregator()
        self._last_projections: dict[str, dict[str, float]] = {}
        self._last_fetch_time: Optional[datetime] = None

    def fetch_current_projections(
        self,
        sport: Sport,
        players: list[Player],
    ) -> dict[str, float]:
        """Fetch current projections and compare to previous.

        Args:
            sport: Sport to fetch
            players: Current player pool

        Returns:
            Dict mapping player_id to current projection
        """
        logger.info(f"Fetching current {sport.value} projections...")

        try:
            # Fetch updated projections
            updated_players = self.aggregator.get_projections_for_contest(
                sport=sport,
                players=players,
            )

            # Build projection dict
            current = {}
            for player in updated_players:
                if player.projected_points:
                    current[player.yahoo_player_id] = player.projected_points

            # Store for comparison
            sport_key = sport.value
            self._last_projections[sport_key] = current
            self._last_fetch_time = datetime.utcnow()

            logger.info(f"Fetched projections for {len(current)} players")
            return current

        except Exception as e:
            logger.error(f"Failed to fetch projections: {e}")
            return {}

    def detect_projection_changes(
        self,
        sport: Sport,
        players: list[Player],
        threshold: float = 0.1,
    ) -> dict[str, tuple[float, float]]:
        """Detect significant projection changes since last fetch.

        Args:
            sport: Sport to check
            players: Current player pool
            threshold: Minimum change percentage to flag

        Returns:
            Dict mapping player_id to (old_proj, new_proj) for changed players
        """
        sport_key = sport.value
        previous = self._last_projections.get(sport_key, {})

        # Fetch current
        current = self.fetch_current_projections(sport, players)

        if not previous:
            logger.info("No previous projections to compare")
            return {}

        # Find changes
        changes = {}
        for player_id, new_proj in current.items():
            old_proj = previous.get(player_id)
            if old_proj is None:
                continue

            if old_proj > 0:
                change_pct = abs(new_proj - old_proj) / old_proj
                if change_pct >= threshold:
                    changes[player_id] = (old_proj, new_proj)
                    logger.info(
                        f"Projection change detected: {player_id} "
                        f"{old_proj:.1f} -> {new_proj:.1f} ({change_pct:.1%})"
                    )

        # Also check for players that disappeared (possible inactive)
        for player_id in previous:
            if player_id not in current:
                changes[player_id] = (previous[player_id], 0.0)
                logger.warning(f"Player {player_id} no longer in projections (inactive?)")

        return changes

    def get_inactive_players(
        self,
        sport: Sport,
        players: list[Player],
    ) -> set[str]:
        """Get set of players marked inactive or with zero projection.

        Args:
            sport: Sport to check
            players: Current player pool

        Returns:
            Set of inactive player IDs
        """
        current = self.fetch_current_projections(sport, players)

        inactive = set()
        for player in players:
            # Player not in projections or zero projection
            proj = current.get(player.yahoo_player_id, 0)
            if proj <= 0:
                inactive.add(player.yahoo_player_id)

        logger.info(f"Found {len(inactive)} inactive/zero-projection players")
        return inactive

    def get_projection_drops(
        self,
        sport: Sport,
        players: list[Player],
        drop_threshold: float = 0.2,
    ) -> list[dict]:
        """Get players with significant projection drops.

        Args:
            sport: Sport to check
            players: Current player pool
            drop_threshold: Minimum drop percentage

        Returns:
            List of dicts with player info and projection change
        """
        changes = self.detect_projection_changes(sport, players, threshold=drop_threshold)

        drops = []
        player_lookup = {p.yahoo_player_id: p for p in players}

        for player_id, (old_proj, new_proj) in changes.items():
            # Only include drops (not increases)
            if new_proj < old_proj:
                player = player_lookup.get(player_id)
                drops.append({
                    "player_id": player_id,
                    "player_name": player.name if player else "Unknown",
                    "old_projection": old_proj,
                    "new_projection": new_proj,
                    "drop_amount": old_proj - new_proj,
                    "drop_percentage": (old_proj - new_proj) / old_proj if old_proj > 0 else 0,
                })

        # Sort by drop percentage
        drops.sort(key=lambda x: x["drop_percentage"], reverse=True)
        return drops

    def should_check(self, interval_minutes: int = 15) -> bool:
        """Check if enough time has passed since last fetch.

        Args:
            interval_minutes: Minimum minutes between checks

        Returns:
            True if should check for updates
        """
        if self._last_fetch_time is None:
            return True

        elapsed = (datetime.utcnow() - self._last_fetch_time).total_seconds() / 60
        return elapsed >= interval_minutes


class ProjectionChangeDetector:
    """Detects projection changes by comparing snapshots."""

    def __init__(self):
        """Initialize change detector."""
        self._snapshots: list[dict[str, float]] = []
        self._snapshot_times: list[datetime] = []
        self._max_snapshots = 10

    def take_snapshot(self, projections: dict[str, float]) -> None:
        """Store a projection snapshot.

        Args:
            projections: Dict mapping player_id to projection
        """
        self._snapshots.append(projections.copy())
        self._snapshot_times.append(datetime.utcnow())

        # Trim old snapshots
        if len(self._snapshots) > self._max_snapshots:
            self._snapshots.pop(0)
            self._snapshot_times.pop(0)

    def compare_to_original(
        self,
        current: dict[str, float],
        threshold: float = 0.15,
    ) -> dict[str, tuple[float, float]]:
        """Compare current projections to original snapshot.

        Args:
            current: Current projections
            threshold: Change threshold

        Returns:
            Dict of changed players: player_id -> (original, current)
        """
        if not self._snapshots:
            return {}

        original = self._snapshots[0]
        changes = {}

        for player_id, curr_proj in current.items():
            orig_proj = original.get(player_id)
            if orig_proj and orig_proj > 0:
                change_pct = abs(curr_proj - orig_proj) / orig_proj
                if change_pct >= threshold:
                    changes[player_id] = (orig_proj, curr_proj)

        return changes

    def get_trend(self, player_id: str) -> list[tuple[datetime, float]]:
        """Get projection trend for a player.

        Args:
            player_id: Player ID

        Returns:
            List of (timestamp, projection) tuples
        """
        trend = []
        for i, snapshot in enumerate(self._snapshots):
            if player_id in snapshot:
                trend.append((self._snapshot_times[i], snapshot[player_id]))
        return trend


def get_news_monitor() -> NewsMonitor:
    """Get news monitor instance."""
    return NewsMonitor()
