"""Lineup tracking - store and retrieve submitted lineups."""
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import func

from ..common.database import get_database, LineupDB, LineupPlayerDB, ContestDB, ResultDB
from ..common.models import Lineup, LineupPlayer, LineupStatus, Sport

logger = logging.getLogger(__name__)


class LineupTracker:
    """Tracks submitted lineups and their status."""

    def __init__(self):
        """Initialize lineup tracker."""
        self.db = get_database()

    def get_lineup_by_id(self, lineup_id: int) -> Optional[Lineup]:
        """Get a specific lineup by ID.

        Args:
            lineup_id: Lineup database ID

        Returns:
            Lineup object or None
        """
        session = self.db.get_session()
        try:
            db_lineup = session.query(LineupDB).filter_by(id=lineup_id).first()
            if not db_lineup:
                return None

            return self._db_to_lineup(db_lineup)
        finally:
            session.close()

    def get_lineups_for_contest(
        self,
        contest_id: str,
        status: Optional[LineupStatus] = None,
    ) -> list[Lineup]:
        """Get all lineups for a contest.

        Args:
            contest_id: Contest ID
            status: Optional status filter

        Returns:
            List of Lineup objects
        """
        session = self.db.get_session()
        try:
            query = session.query(LineupDB).filter_by(contest_id=contest_id)

            if status:
                query = query.filter_by(status=status.value)

            query = query.order_by(LineupDB.projected_points.desc())

            return [self._db_to_lineup(db) for db in query.all()]
        finally:
            session.close()

    def get_pending_lineups(self, sport: Optional[Sport] = None) -> list[Lineup]:
        """Get all lineups pending submission.

        Args:
            sport: Optional sport filter

        Returns:
            List of pending lineups
        """
        session = self.db.get_session()
        try:
            query = (
                session.query(LineupDB)
                .filter_by(status=LineupStatus.GENERATED.value)
            )

            if sport:
                query = query.join(ContestDB).filter(ContestDB.sport == sport.value)

            return [self._db_to_lineup(db) for db in query.all()]
        finally:
            session.close()

    def get_submitted_lineups(
        self,
        sport: Optional[Sport] = None,
        before_lock: bool = True,
    ) -> list[Lineup]:
        """Get submitted lineups, optionally filtering by sport and lock status.

        Args:
            sport: Optional sport filter
            before_lock: Only include lineups for contests not yet locked

        Returns:
            List of submitted lineups
        """
        session = self.db.get_session()
        try:
            query = (
                session.query(LineupDB)
                .filter_by(status=LineupStatus.SUBMITTED.value)
                .join(ContestDB)
            )

            if sport:
                query = query.filter(ContestDB.sport == sport.value)

            if before_lock:
                query = query.filter(ContestDB.slate_start > datetime.utcnow())

            return [self._db_to_lineup(db) for db in query.all()]
        finally:
            session.close()

    def get_active_contests(self, sport: Optional[Sport] = None) -> list[dict]:
        """Get contests with submitted lineups that haven't locked yet.

        Args:
            sport: Optional sport filter

        Returns:
            List of contest dicts with lineup counts
        """
        session = self.db.get_session()
        try:
            query = (
                session.query(
                    ContestDB,
                    func.count(LineupDB.id).label("lineup_count"),
                )
                .join(LineupDB)
                .filter(LineupDB.status == LineupStatus.SUBMITTED.value)
                .filter(ContestDB.slate_start > datetime.utcnow())
                .group_by(ContestDB.id)
            )

            if sport:
                query = query.filter(ContestDB.sport == sport.value)

            results = []
            for contest, count in query.all():
                results.append({
                    "id": contest.id,
                    "sport": contest.sport,
                    "name": contest.name,
                    "entry_fee": contest.entry_fee,
                    "slate_start": contest.slate_start,
                    "lineup_count": count,
                })

            return results
        finally:
            session.close()

    def update_lineup_status(
        self,
        lineup_id: int,
        status: LineupStatus,
        submitted_at: Optional[datetime] = None,
    ) -> bool:
        """Update lineup status.

        Args:
            lineup_id: Lineup ID
            status: New status
            submitted_at: Optional submission timestamp

        Returns:
            True if updated successfully
        """
        session = self.db.get_session()
        try:
            db_lineup = session.query(LineupDB).filter_by(id=lineup_id).first()
            if not db_lineup:
                return False

            db_lineup.status = status.value
            if submitted_at:
                db_lineup.submitted_at = submitted_at

            session.commit()
            logger.info(f"Updated lineup {lineup_id} status to {status.value}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update lineup status: {e}")
            return False
        finally:
            session.close()

    def mark_submitted(self, lineup_id: int) -> bool:
        """Mark a lineup as submitted.

        Args:
            lineup_id: Lineup ID

        Returns:
            True if updated successfully
        """
        return self.update_lineup_status(
            lineup_id,
            LineupStatus.SUBMITTED,
            submitted_at=datetime.utcnow(),
        )

    def mark_swapped(self, lineup_id: int) -> bool:
        """Mark a lineup as swapped.

        Args:
            lineup_id: Lineup ID

        Returns:
            True if updated successfully
        """
        return self.update_lineup_status(lineup_id, LineupStatus.SWAPPED)

    def get_lineup_summary(self, sport: Optional[Sport] = None) -> dict:
        """Get summary statistics for lineups.

        Args:
            sport: Optional sport filter

        Returns:
            Dict with summary stats
        """
        session = self.db.get_session()
        try:
            query = session.query(LineupDB)
            if sport:
                query = query.join(ContestDB).filter(ContestDB.sport == sport.value)

            total = query.count()
            generated = query.filter(LineupDB.status == LineupStatus.GENERATED.value).count()
            submitted = query.filter(LineupDB.status == LineupStatus.SUBMITTED.value).count()
            swapped = query.filter(LineupDB.status == LineupStatus.SWAPPED.value).count()
            failed = query.filter(LineupDB.status == LineupStatus.FAILED.value).count()

            return {
                "total": total,
                "generated": generated,
                "submitted": submitted,
                "swapped": swapped,
                "failed": failed,
            }
        finally:
            session.close()

    def _db_to_lineup(self, db_lineup: LineupDB) -> Lineup:
        """Convert database lineup to model.

        Args:
            db_lineup: Database lineup object

        Returns:
            Lineup model object
        """
        players = [
            LineupPlayer(
                yahoo_player_id=p.yahoo_player_id,
                player_game_code=p.player_game_code or "",  # Required for CSV upload
                name=p.name,
                roster_position=p.roster_position,
                actual_position=p.actual_position,
                salary=p.salary,
                projected_points=p.projected_points,
                actual_points=p.actual_points,
            )
            for p in db_lineup.players
        ]

        return Lineup(
            id=db_lineup.id,
            series_id=db_lineup.series_id or 0,  # Series ID (may be 0 if not assigned)
            contest_id=db_lineup.contest_id,
            players=players,
            total_salary=db_lineup.total_salary,
            projected_points=db_lineup.projected_points,
            actual_points=db_lineup.actual_points,
            status=LineupStatus(db_lineup.status),
            lineup_hash=db_lineup.lineup_hash,
            created_at=db_lineup.created_at,
            submitted_at=db_lineup.submitted_at,
        )

    def delete_lineup(self, lineup_id: int) -> bool:
        """Delete a lineup (only if not submitted).

        Args:
            lineup_id: Lineup ID

        Returns:
            True if deleted successfully
        """
        session = self.db.get_session()
        try:
            db_lineup = session.query(LineupDB).filter_by(id=lineup_id).first()
            if not db_lineup:
                return False

            if db_lineup.status == LineupStatus.SUBMITTED.value:
                logger.warning("Cannot delete submitted lineup")
                return False

            session.delete(db_lineup)
            session.commit()
            logger.info(f"Deleted lineup {lineup_id}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to delete lineup: {e}")
            return False
        finally:
            session.close()


def get_tracker() -> LineupTracker:
    """Get lineup tracker instance."""
    return LineupTracker()
