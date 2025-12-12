"""Player swapper for replacing OUT/injured players in submitted lineups."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..common.database import (
    get_database,
    LineupDB,
    LineupPlayerDB,
    PlayerPoolDB,
    SwapLogDB,
)
from ..common.models import Player, LineupPlayer

logger = logging.getLogger(__name__)


# Injury statuses that should trigger a swap
SWAP_TRIGGER_STATUSES = {"O", "OUT", "IR", "SUSP", "NA", "INJ"}  # OUT, Injured Reserve, Suspended, Not Active


@dataclass
class SwapCandidate:
    """A potential replacement player."""

    player: Player
    projected_points: float
    salary: int
    reason_score: float  # Higher is better


@dataclass
class SwapResult:
    """Result of a player swap operation."""

    lineup_id: int
    original_player_id: str
    original_player_name: str
    replacement_player_id: str
    replacement_player_name: str
    reason: str
    success: bool
    error_message: Optional[str] = None


class PlayerSwapper:
    """Handles swapping OUT players with available replacements.

    Strategy:
    1. Find the OUT player's roster position
    2. Find available players who can fill that position
    3. Filter by salary (must be <= original player's salary)
    4. Filter out players already in the lineup
    5. Filter out players who are also OUT
    6. Select the player with highest projected points
    """

    def __init__(self):
        """Initialize player swapper."""
        self.db = get_database()

    def find_out_players(self, contest_id: str) -> list[dict]:
        """Find players in submitted lineups who are now OUT.

        Args:
            contest_id: Contest ID to check

        Returns:
            List of dicts with lineup_id, player info, and injury status
        """
        session = self.db.get_session()
        out_players = []

        try:
            # Get submitted lineups for this contest
            lineups = (
                session.query(LineupDB)
                .filter_by(contest_id=contest_id, status="submitted")
                .all()
            )

            if not lineups:
                return []

            # Get current player injury statuses from player pool
            player_statuses = {}
            pool_players = (
                session.query(PlayerPoolDB)
                .filter_by(contest_id=contest_id)
                .all()
            )
            for p in pool_players:
                player_statuses[p.yahoo_player_id] = {
                    "injury_status": p.injury_status,
                    "is_active": p.is_active,
                }

            # Check each lineup player
            for lineup in lineups:
                for lp in lineup.players:
                    status_info = player_statuses.get(lp.yahoo_player_id, {})
                    injury_status = (status_info.get("injury_status") or "").upper()
                    is_active = status_info.get("is_active", True)

                    # Check if player should be swapped
                    if injury_status in SWAP_TRIGGER_STATUSES or not is_active:
                        out_players.append({
                            "lineup_id": lineup.id,
                            "lineup": lineup,
                            "player_id": lp.yahoo_player_id,
                            "player_name": lp.name,
                            "roster_position": lp.roster_position,
                            "actual_position": lp.actual_position,
                            "salary": lp.salary,
                            "projected_points": lp.projected_points,
                            "injury_status": injury_status,
                        })

            logger.info(f"Found {len(out_players)} OUT players in {len(lineups)} lineups for contest {contest_id}")

        finally:
            session.close()

        return out_players

    def find_replacement(
        self,
        contest_id: str,
        out_player: dict,
        lineup: LineupDB,
    ) -> Optional[SwapCandidate]:
        """Find the best replacement for an OUT player.

        Args:
            contest_id: Contest ID
            out_player: Dict with out player info
            lineup: The lineup containing the out player

        Returns:
            Best SwapCandidate or None if no valid replacement found
        """
        session = self.db.get_session()

        try:
            # Get IDs of players already in this lineup
            existing_ids = {lp.yahoo_player_id for lp in lineup.players}

            # Get available players from pool
            candidates = (
                session.query(PlayerPoolDB)
                .filter_by(contest_id=contest_id)
                .filter(PlayerPoolDB.salary <= out_player["salary"])  # Salary constraint
                .filter(PlayerPoolDB.is_active == True)  # Must be active
                .filter(~PlayerPoolDB.yahoo_player_id.in_(existing_ids))  # Not in lineup
                .all()
            )

            # Filter by position eligibility
            roster_position = out_player["roster_position"]
            actual_position = out_player["actual_position"]
            eligible_candidates = []

            for c in candidates:
                # Check injury status
                injury_status = (c.injury_status or "").upper()
                if injury_status in SWAP_TRIGGER_STATUSES:
                    continue

                # Check position eligibility
                eligible_positions = (c.eligible_positions or c.position or "").split(",")
                eligible_positions = [p.strip().upper() for p in eligible_positions]

                # Player must be eligible for the roster position
                # FLEX can be filled by RB, WR, TE
                # UTIL can be filled by any non-DEF position
                can_fill = False

                if roster_position.upper() in eligible_positions:
                    can_fill = True
                elif roster_position.upper() == "FLEX" and any(
                    p in eligible_positions for p in ["RB", "WR", "TE"]
                ):
                    can_fill = True
                elif roster_position.upper() == "UTIL" and "DEF" not in eligible_positions:
                    can_fill = True

                if can_fill:
                    # Use Yahoo's projection or our stored projection
                    proj_points = c.yahoo_projected_points or c.fppg or 0.0

                    eligible_candidates.append(SwapCandidate(
                        player=Player(
                            yahoo_player_id=c.yahoo_player_id,
                            player_game_code=c.player_game_code,
                            name=c.name,
                            team=c.team,
                            position=c.position,
                            salary=c.salary,
                        ),
                        projected_points=proj_points,
                        salary=c.salary,
                        reason_score=proj_points,  # Simple: use projected points as score
                    ))

            if not eligible_candidates:
                logger.warning(
                    f"No replacement found for {out_player['player_name']} "
                    f"(pos: {roster_position}, salary: ${out_player['salary']})"
                )
                return None

            # Select best candidate (highest projected points)
            best = max(eligible_candidates, key=lambda x: x.reason_score)

            logger.info(
                f"Found replacement for {out_player['player_name']}: "
                f"{best.player.name} ({best.projected_points:.1f} pts, ${best.salary})"
            )

            return best

        finally:
            session.close()

    def execute_swap(
        self,
        lineup_id: int,
        out_player: dict,
        replacement: SwapCandidate,
        reason: str = "OUT",
    ) -> SwapResult:
        """Execute a player swap in the database.

        Args:
            lineup_id: Lineup ID to modify
            out_player: Dict with out player info
            replacement: Replacement player candidate
            reason: Reason for swap (e.g., "OUT", "IR")

        Returns:
            SwapResult with success/failure info
        """
        session = self.db.get_session()

        try:
            # Find the lineup player record
            lineup_player = (
                session.query(LineupPlayerDB)
                .filter_by(
                    lineup_id=lineup_id,
                    yahoo_player_id=out_player["player_id"],
                )
                .first()
            )

            if not lineup_player:
                return SwapResult(
                    lineup_id=lineup_id,
                    original_player_id=out_player["player_id"],
                    original_player_name=out_player["player_name"],
                    replacement_player_id=replacement.player.yahoo_player_id,
                    replacement_player_name=replacement.player.name,
                    reason=reason,
                    success=False,
                    error_message="Lineup player not found",
                )

            # Update the lineup player record
            old_id = lineup_player.yahoo_player_id
            old_name = lineup_player.name
            old_projection = lineup_player.projected_points

            lineup_player.yahoo_player_id = replacement.player.yahoo_player_id
            lineup_player.player_game_code = replacement.player.player_game_code or ""
            lineup_player.name = replacement.player.name
            lineup_player.actual_position = replacement.player.position
            lineup_player.salary = replacement.salary
            lineup_player.projected_points = replacement.projected_points

            # Update lineup total projected points
            lineup = session.query(LineupDB).filter_by(id=lineup_id).first()
            if lineup:
                # Recalculate projected points
                total_proj = sum(lp.projected_points for lp in lineup.players)
                lineup.projected_points = total_proj
                lineup.status = "swapped"

            # Log the swap
            swap_log = SwapLogDB(
                lineup_id=lineup_id,
                old_player_id=old_id,
                old_player_name=old_name,
                new_player_id=replacement.player.yahoo_player_id,
                new_player_name=replacement.player.name,
                reason=reason,
                old_projection=old_projection,
                new_projection=replacement.projected_points,
            )
            session.add(swap_log)

            session.commit()

            logger.info(
                f"Swapped {old_name} -> {replacement.player.name} "
                f"in lineup {lineup_id} (reason: {reason})"
            )

            return SwapResult(
                lineup_id=lineup_id,
                original_player_id=old_id,
                original_player_name=old_name,
                replacement_player_id=replacement.player.yahoo_player_id,
                replacement_player_name=replacement.player.name,
                reason=reason,
                success=True,
            )

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to execute swap: {e}")
            return SwapResult(
                lineup_id=lineup_id,
                original_player_id=out_player["player_id"],
                original_player_name=out_player["player_name"],
                replacement_player_id=replacement.player.yahoo_player_id,
                replacement_player_name=replacement.player.name,
                reason=reason,
                success=False,
                error_message=str(e),
            )
        finally:
            session.close()

    def process_contest_swaps(self, contest_id: str, dry_run: bool = False) -> list[SwapResult]:
        """Process all needed swaps for a contest.

        Args:
            contest_id: Contest ID to process
            dry_run: If True, find swaps but don't execute them

        Returns:
            List of SwapResult for each swap attempted
        """
        results = []

        # Find OUT players
        out_players = self.find_out_players(contest_id)

        if not out_players:
            logger.info(f"No OUT players found in contest {contest_id}")
            return results

        logger.info(f"Processing {len(out_players)} OUT players in contest {contest_id}")

        for out_player in out_players:
            # Get the lineup
            session = self.db.get_session()
            try:
                lineup = session.query(LineupDB).filter_by(id=out_player["lineup_id"]).first()
                if not lineup:
                    continue

                # Find replacement
                replacement = self.find_replacement(contest_id, out_player, lineup)

                if not replacement:
                    results.append(SwapResult(
                        lineup_id=out_player["lineup_id"],
                        original_player_id=out_player["player_id"],
                        original_player_name=out_player["player_name"],
                        replacement_player_id="",
                        replacement_player_name="",
                        reason=out_player["injury_status"],
                        success=False,
                        error_message="No valid replacement found",
                    ))
                    continue

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would swap {out_player['player_name']} -> "
                        f"{replacement.player.name} in lineup {out_player['lineup_id']}"
                    )
                    results.append(SwapResult(
                        lineup_id=out_player["lineup_id"],
                        original_player_id=out_player["player_id"],
                        original_player_name=out_player["player_name"],
                        replacement_player_id=replacement.player.yahoo_player_id,
                        replacement_player_name=replacement.player.name,
                        reason=out_player["injury_status"],
                        success=True,  # Would succeed
                    ))
                else:
                    result = self.execute_swap(
                        lineup_id=out_player["lineup_id"],
                        out_player=out_player,
                        replacement=replacement,
                        reason=out_player["injury_status"],
                    )
                    results.append(result)

            finally:
                session.close()

        # Summary
        successful = sum(1 for r in results if r.success)
        logger.info(f"Swap results for contest {contest_id}: {successful}/{len(results)} successful")

        return results


    def get_swapped_lineups(self, contest_id: str) -> list:
        """Get lineups that have been swapped and need to be re-uploaded.

        Args:
            contest_id: Contest ID

        Returns:
            List of Lineup objects with updated players
        """
        from ..common.models import Lineup, LineupPlayer

        session = self.db.get_session()
        lineups = []

        try:
            # Get lineups with 'swapped' status
            db_lineups = (
                session.query(LineupDB)
                .filter_by(contest_id=contest_id, status="swapped")
                .all()
            )

            for db_lineup in db_lineups:
                players = []
                for lp in db_lineup.players:
                    players.append(LineupPlayer(
                        yahoo_player_id=lp.yahoo_player_id,
                        player_game_code=lp.player_game_code,
                        name=lp.name,
                        roster_position=lp.roster_position,
                        actual_position=lp.actual_position,
                        salary=lp.salary,
                        projected_points=lp.projected_points,
                        actual_points=lp.actual_points,
                    ))

                lineups.append(Lineup(
                    id=db_lineup.id,
                    series_id=db_lineup.series_id,
                    contest_id=db_lineup.contest_id,
                    entry_id=db_lineup.entry_id,
                    lineup_index=db_lineup.lineup_index,
                    players=players,
                    total_salary=db_lineup.total_salary,
                    projected_points=db_lineup.projected_points,
                    actual_points=db_lineup.actual_points,
                    lineup_hash=db_lineup.lineup_hash,
                ))

            logger.info(f"Found {len(lineups)} swapped lineups for contest {contest_id}")
            return lineups

        finally:
            session.close()

    def mark_lineups_uploaded(self, lineup_ids: list[int]) -> None:
        """Mark swapped lineups as successfully re-uploaded (edited status).

        Args:
            lineup_ids: List of lineup IDs to mark as edited
        """
        session = self.db.get_session()
        try:
            for lineup_id in lineup_ids:
                lineup = session.query(LineupDB).filter_by(id=lineup_id).first()
                if lineup:
                    lineup.status = "edited"

            session.commit()
            logger.info(f"Marked {len(lineup_ids)} lineups as edited")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to mark lineups as edited: {e}")
        finally:
            session.close()


def check_and_swap_injuries(contest_id: str, dry_run: bool = False) -> list[SwapResult]:
    """Convenience function to check and swap injuries for a contest.

    Args:
        contest_id: Contest ID to check
        dry_run: If True, simulate swaps without executing

    Returns:
        List of SwapResult
    """
    swapper = PlayerSwapper()
    return swapper.process_contest_swaps(contest_id, dry_run)
