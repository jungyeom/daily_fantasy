"""Late swap logic - detect needed swaps and re-optimize lineups."""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..common.config import get_config
from ..common.database import get_database, LineupDB, LineupPlayerDB, SwapLogDB, ContestDB, PlayerPoolDB
from ..common.models import Lineup, LineupPlayer, LineupStatus, Player, Sport
from ..common.notifications import get_notifier
from .tracker import LineupTracker

logger = logging.getLogger(__name__)


@dataclass
class SwapCandidate:
    """A player that should be swapped out."""
    lineup_id: int
    player_id: str
    player_name: str
    position: str
    original_projection: float
    current_projection: float
    reason: str  # 'projection_drop', 'inactive', 'injury'


@dataclass
class SwapResult:
    """Result of a swap operation."""
    lineup_id: int
    old_player_id: str
    old_player_name: str
    new_player_id: str
    new_player_name: str
    projection_change: float
    success: bool
    error: Optional[str] = None


class LateSwapManager:
    """Manages late swap detection and execution."""

    # Projection drop threshold to trigger swap (e.g., 0.2 = 20% drop)
    PROJECTION_DROP_THRESHOLD = 0.2

    def __init__(self):
        """Initialize late swap manager."""
        self.config = get_config()
        self.db = get_database()
        self.tracker = LineupTracker()
        self.notifier = get_notifier()

    def find_swap_candidates(
        self,
        lineups: list[Lineup],
        current_projections: dict[str, float],
        inactive_players: Optional[set[str]] = None,
    ) -> list[SwapCandidate]:
        """Find players in lineups that need to be swapped.

        Args:
            lineups: Submitted lineups to check
            current_projections: Current projections by player_id
            inactive_players: Set of inactive player IDs

        Returns:
            List of SwapCandidate objects
        """
        inactive_players = inactive_players or set()
        candidates = []

        for lineup in lineups:
            for player in lineup.players:
                # Check if player is inactive
                if player.yahoo_player_id in inactive_players:
                    candidates.append(SwapCandidate(
                        lineup_id=lineup.id,
                        player_id=player.yahoo_player_id,
                        player_name=player.name,
                        position=player.roster_position,
                        original_projection=player.projected_points,
                        current_projection=0.0,
                        reason="inactive",
                    ))
                    continue

                # Check for projection drop
                current_proj = current_projections.get(player.yahoo_player_id)
                if current_proj is None:
                    # Player not in current projections - might be inactive
                    candidates.append(SwapCandidate(
                        lineup_id=lineup.id,
                        player_id=player.yahoo_player_id,
                        player_name=player.name,
                        position=player.roster_position,
                        original_projection=player.projected_points,
                        current_projection=0.0,
                        reason="projection_drop",
                    ))
                    continue

                # Check percentage drop
                if player.projected_points > 0:
                    drop_pct = (player.projected_points - current_proj) / player.projected_points
                    if drop_pct >= self.PROJECTION_DROP_THRESHOLD:
                        candidates.append(SwapCandidate(
                            lineup_id=lineup.id,
                            player_id=player.yahoo_player_id,
                            player_name=player.name,
                            position=player.roster_position,
                            original_projection=player.projected_points,
                            current_projection=current_proj,
                            reason="projection_drop",
                        ))

        logger.info(f"Found {len(candidates)} swap candidates across {len(lineups)} lineups")
        return candidates

    def find_replacement(
        self,
        candidate: SwapCandidate,
        lineup: Lineup,
        available_players: list[Player],
        current_projections: dict[str, float],
    ) -> Optional[Player]:
        """Find best replacement for a swap candidate.

        Args:
            candidate: Player to swap out
            lineup: Lineup containing the player
            available_players: All available players
            current_projections: Current projections

        Returns:
            Best replacement Player or None
        """
        # Calculate remaining salary
        current_salary = sum(
            p.salary for p in lineup.players
            if p.yahoo_player_id != candidate.player_id
        )
        old_player_salary = next(
            (p.salary for p in lineup.players if p.yahoo_player_id == candidate.player_id),
            0
        )
        available_salary = lineup.total_salary - current_salary + old_player_salary

        # Get player IDs already in lineup
        lineup_player_ids = {p.yahoo_player_id for p in lineup.players}

        # Filter eligible replacements
        eligible = []
        for player in available_players:
            # Skip if already in lineup
            if player.yahoo_player_id in lineup_player_ids:
                continue

            # Must match position
            if player.position != candidate.position and candidate.position not in ["FLEX", "UTIL"]:
                continue

            # Must fit salary
            if player.salary > available_salary:
                continue

            # Must have game not started (would need game time data)
            # For now, assume all are available

            # Get current projection
            proj = current_projections.get(player.yahoo_player_id, player.projected_points or 0)
            if proj <= 0:
                continue

            eligible.append((player, proj))

        if not eligible:
            logger.warning(f"No eligible replacements for {candidate.player_name}")
            return None

        # Sort by projection and return best
        eligible.sort(key=lambda x: x[1], reverse=True)
        best_player, best_proj = eligible[0]

        logger.info(f"Best replacement for {candidate.player_name}: {best_player.name} ({best_proj:.1f} pts)")
        return best_player

    def execute_swap(
        self,
        lineup: Lineup,
        candidate: SwapCandidate,
        replacement: Player,
        current_projection: float,
    ) -> SwapResult:
        """Execute a player swap in a lineup.

        Args:
            lineup: Lineup to modify
            candidate: Player to swap out
            replacement: Replacement player
            current_projection: Current projection for replacement

        Returns:
            SwapResult object
        """
        session = self.db.get_session()
        try:
            # Find lineup player to update
            db_player = (
                session.query(LineupPlayerDB)
                .filter_by(lineup_id=lineup.id, yahoo_player_id=candidate.player_id)
                .first()
            )

            if not db_player:
                return SwapResult(
                    lineup_id=lineup.id,
                    old_player_id=candidate.player_id,
                    old_player_name=candidate.player_name,
                    new_player_id=replacement.yahoo_player_id,
                    new_player_name=replacement.name,
                    projection_change=0,
                    success=False,
                    error="Player not found in lineup",
                )

            # Calculate projection change
            proj_change = current_projection - candidate.original_projection

            # Log the swap
            swap_log = SwapLogDB(
                lineup_id=lineup.id,
                old_player_id=candidate.player_id,
                old_player_name=candidate.player_name,
                new_player_id=replacement.yahoo_player_id,
                new_player_name=replacement.name,
                reason=candidate.reason,
                old_projection=candidate.original_projection,
                new_projection=current_projection,
            )
            session.add(swap_log)

            # Update lineup player
            db_player.yahoo_player_id = replacement.yahoo_player_id
            db_player.name = replacement.name
            db_player.salary = replacement.salary
            db_player.projected_points = current_projection

            # Update lineup projected points
            db_lineup = session.query(LineupDB).filter_by(id=lineup.id).first()
            if db_lineup:
                db_lineup.projected_points += proj_change
                db_lineup.status = LineupStatus.SWAPPED.value

            session.commit()

            logger.info(f"Swapped {candidate.player_name} -> {replacement.name} in lineup {lineup.id}")

            return SwapResult(
                lineup_id=lineup.id,
                old_player_id=candidate.player_id,
                old_player_name=candidate.player_name,
                new_player_id=replacement.yahoo_player_id,
                new_player_name=replacement.name,
                projection_change=proj_change,
                success=True,
            )

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to execute swap: {e}")
            return SwapResult(
                lineup_id=lineup.id,
                old_player_id=candidate.player_id,
                old_player_name=candidate.player_name,
                new_player_id=replacement.yahoo_player_id,
                new_player_name=replacement.name,
                projection_change=0,
                success=False,
                error=str(e),
            )
        finally:
            session.close()

    def process_late_swaps(
        self,
        contest_id: str,
        current_projections: dict[str, float],
        available_players: list[Player],
        inactive_players: Optional[set[str]] = None,
        notify: bool = True,
    ) -> list[SwapResult]:
        """Process all late swaps for a contest.

        Args:
            contest_id: Contest ID
            current_projections: Current projections by player_id
            available_players: Available player pool
            inactive_players: Set of inactive player IDs
            notify: Whether to send notifications

        Returns:
            List of SwapResult objects
        """
        # Get submitted lineups for contest
        lineups = self.tracker.get_lineups_for_contest(
            contest_id,
            status=LineupStatus.SUBMITTED,
        )

        if not lineups:
            logger.info(f"No submitted lineups for contest {contest_id}")
            return []

        # Find swap candidates
        candidates = self.find_swap_candidates(
            lineups,
            current_projections,
            inactive_players,
        )

        if not candidates:
            logger.info(f"No swap candidates found for contest {contest_id}")
            return []

        # Group candidates by lineup
        lineup_lookup = {l.id: l for l in lineups}
        results = []

        for candidate in candidates:
            lineup = lineup_lookup.get(candidate.lineup_id)
            if not lineup:
                continue

            # Find replacement
            replacement = self.find_replacement(
                candidate,
                lineup,
                available_players,
                current_projections,
            )

            if not replacement:
                results.append(SwapResult(
                    lineup_id=candidate.lineup_id,
                    old_player_id=candidate.player_id,
                    old_player_name=candidate.player_name,
                    new_player_id="",
                    new_player_name="",
                    projection_change=0,
                    success=False,
                    error="No eligible replacement found",
                ))
                continue

            # Execute swap
            result = self.execute_swap(
                lineup,
                candidate,
                replacement,
                current_projections.get(replacement.yahoo_player_id, replacement.projected_points or 0),
            )
            results.append(result)

            # Send notification
            if notify and result.success:
                self.notifier.notify_late_swap(
                    sport="Unknown",  # Would need to look up
                    contest_name=contest_id,
                    lineup_id=result.lineup_id,
                    old_player=result.old_player_name,
                    new_player=result.new_player_name,
                    reason=candidate.reason,
                )

        successful = sum(1 for r in results if r.success)
        logger.info(f"Processed {len(results)} swaps, {successful} successful")

        return results

    def get_swap_history(
        self,
        lineup_id: Optional[int] = None,
        contest_id: Optional[str] = None,
    ) -> list[dict]:
        """Get swap history.

        Args:
            lineup_id: Optional lineup ID filter
            contest_id: Optional contest ID filter

        Returns:
            List of swap log dicts
        """
        session = self.db.get_session()
        try:
            query = session.query(SwapLogDB)

            if lineup_id:
                query = query.filter_by(lineup_id=lineup_id)

            if contest_id:
                query = query.join(LineupDB).filter(LineupDB.contest_id == contest_id)

            query = query.order_by(SwapLogDB.swapped_at.desc())

            return [
                {
                    "lineup_id": log.lineup_id,
                    "old_player": log.old_player_name,
                    "new_player": log.new_player_name,
                    "reason": log.reason,
                    "old_projection": log.old_projection,
                    "new_projection": log.new_projection,
                    "swapped_at": log.swapped_at,
                }
                for log in query.all()
            ]
        finally:
            session.close()


def get_late_swap_manager() -> LateSwapManager:
    """Get late swap manager instance."""
    return LateSwapManager()
