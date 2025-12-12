"""Fill rate monitoring for contest submission timing."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..common.database import ContestDB, ContestEntryDB, get_database

logger = logging.getLogger(__name__)


@dataclass
class FillMonitorConfig:
    """Configuration for fill rate monitoring."""

    fill_rate_threshold: float = 0.70  # Submit when >= 70% full
    time_before_lock_minutes: int = 120  # Or submit when < 2 hours to lock
    stop_editing_minutes: int = 5  # Stop edits 5 min before lock


@dataclass
class ContestStatus:
    """Current status of a contest for submission decision."""

    contest_id: str
    entry_count: int
    entry_limit: int
    fill_rate: float
    lock_time: datetime
    time_to_lock: timedelta
    should_submit: bool
    reason: str  # Why we should/shouldn't submit


class FillMonitor:
    """Monitors contest fill rates to determine optimal submission timing.

    Submission logic:
    1. Submit if fill_rate >= threshold (e.g., 70%)
    2. Submit if time_to_lock < threshold (e.g., 2 hours)
    3. Don't submit if already submitted or contest is locked
    """

    def __init__(self, config: Optional[FillMonitorConfig] = None):
        """Initialize fill monitor.

        Args:
            config: Monitor configuration. Uses defaults if not provided.
        """
        self.config = config or FillMonitorConfig()
        self.db = get_database()

    def check_contest(self, contest: dict, entry_record: ContestEntryDB) -> ContestStatus:
        """Check a contest's fill rate and determine if we should submit.

        Args:
            contest: Contest data from Yahoo API (fresh)
            entry_record: Our tracking record for this contest

        Returns:
            ContestStatus with submission decision
        """
        contest_id = str(contest.get("id", ""))
        entry_count = contest.get("entryCount", 0)
        entry_limit = contest.get("entryLimit", 1)

        # Calculate fill rate
        fill_rate = entry_count / entry_limit if entry_limit > 0 else 0.0

        # Calculate time to lock
        # Note: lock_time is stored in local time (from Yahoo API timestamps)
        lock_time = entry_record.lock_time
        now = datetime.now()
        time_to_lock = lock_time - now

        # Determine if we should submit
        should_submit = False
        reason = ""

        # Already submitted?
        if entry_record.status == "submitted":
            reason = "Already submitted"
        # Already locked?
        elif time_to_lock <= timedelta(0):
            reason = "Contest already locked"
        # Too close to lock to submit (within stop_editing window)?
        elif time_to_lock <= timedelta(minutes=self.config.stop_editing_minutes):
            reason = f"Too close to lock ({time_to_lock.total_seconds() / 60:.0f} min remaining)"
        # Fill rate threshold reached?
        elif fill_rate >= self.config.fill_rate_threshold:
            should_submit = True
            reason = f"Fill rate {fill_rate:.1%} >= {self.config.fill_rate_threshold:.0%} threshold"
        # Time threshold reached?
        elif time_to_lock <= timedelta(minutes=self.config.time_before_lock_minutes):
            should_submit = True
            reason = f"Time to lock {time_to_lock.total_seconds() / 60:.0f} min <= {self.config.time_before_lock_minutes} min threshold"
        else:
            reason = (
                f"Waiting (fill: {fill_rate:.1%}, "
                f"time: {time_to_lock.total_seconds() / 3600:.1f}h to lock)"
            )

        return ContestStatus(
            contest_id=contest_id,
            entry_count=entry_count,
            entry_limit=entry_limit,
            fill_rate=fill_rate,
            lock_time=lock_time,
            time_to_lock=time_to_lock,
            should_submit=should_submit,
            reason=reason,
        )

    def get_contests_to_submit(
        self,
        contests: list[dict],
        sport: Optional[str] = None,
    ) -> list[tuple[dict, ContestEntryDB, ContestStatus]]:
        """Get list of contests that should be submitted now.

        Args:
            contests: Fresh contest data from Yahoo API
            sport: Optional sport filter

        Returns:
            List of (contest_dict, entry_record, status) tuples for contests to submit
        """
        session = self.db.get_session()
        to_submit = []

        try:
            # Get our tracking records for pending contests
            query = session.query(ContestEntryDB).filter(
                ContestEntryDB.status.in_(["eligible", "pending"]),
                ContestEntryDB.lock_time > datetime.now(),
            )

            if sport:
                query = query.filter(ContestEntryDB.sport == sport)

            entry_records = {e.contest_id: e for e in query.all()}

            # Check each contest
            for contest in contests:
                contest_id = str(contest.get("id", ""))

                if contest_id not in entry_records:
                    continue

                entry_record = entry_records[contest_id]
                status = self.check_contest(contest, entry_record)

                if status.should_submit:
                    to_submit.append((contest, entry_record, status))
                    logger.info(f"Contest {contest_id} ready to submit: {status.reason}")
                else:
                    logger.debug(f"Contest {contest_id}: {status.reason}")

        finally:
            session.close()

        return to_submit

    def can_still_edit(self, entry_record: ContestEntryDB) -> bool:
        """Check if we can still make edits to a submitted contest.

        Args:
            entry_record: Our tracking record for the contest

        Returns:
            True if edits are still allowed
        """
        if entry_record.status != "submitted":
            return False

        now = datetime.now()
        time_to_lock = entry_record.lock_time - now

        # Can't edit if already locked
        if time_to_lock <= timedelta(0):
            return False

        # Can't edit if within stop_editing window
        if time_to_lock <= timedelta(minutes=self.config.stop_editing_minutes):
            return False

        return True

    def get_editable_contests(self, sport: Optional[str] = None) -> list[ContestEntryDB]:
        """Get submitted contests that can still be edited.

        Args:
            sport: Optional sport filter

        Returns:
            List of ContestEntryDB records that can be edited
        """
        session = self.db.get_session()
        try:
            # Calculate cutoff time (lock_time - stop_editing_minutes)
            cutoff = datetime.now() + timedelta(minutes=self.config.stop_editing_minutes)

            query = session.query(ContestEntryDB).filter(
                ContestEntryDB.status == "submitted",
                ContestEntryDB.lock_time > cutoff,
            )

            if sport:
                query = query.filter(ContestEntryDB.sport == sport)

            return query.order_by(ContestEntryDB.lock_time).all()

        finally:
            session.close()

    def mark_submitted(
        self,
        contest_id: str,
        lineups_count: int,
        fill_rate: float,
    ) -> None:
        """Mark a contest as submitted.

        Args:
            contest_id: Contest ID
            lineups_count: Number of lineups submitted
            fill_rate: Fill rate at time of submission
        """
        session = self.db.get_session()
        try:
            entry = (
                session.query(ContestEntryDB)
                .filter_by(contest_id=contest_id)
                .first()
            )

            if entry:
                entry.status = "submitted"
                entry.lineups_submitted = lineups_count
                entry.fill_rate_at_submit = fill_rate
                entry.submitted_at = datetime.utcnow()
                session.commit()
                logger.info(
                    f"Marked contest {contest_id} as submitted "
                    f"({lineups_count} lineups, fill rate: {fill_rate:.1%})"
                )
            else:
                logger.warning(f"No entry record found for contest {contest_id}")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to mark contest {contest_id} as submitted: {e}")
            raise
        finally:
            session.close()

    def mark_locked(self, contest_id: str) -> None:
        """Mark a contest as locked.

        Args:
            contest_id: Contest ID
        """
        session = self.db.get_session()
        try:
            entry = (
                session.query(ContestEntryDB)
                .filter_by(contest_id=contest_id)
                .first()
            )

            if entry:
                entry.status = "locked"
                session.commit()
                logger.info(f"Marked contest {contest_id} as locked")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to mark contest {contest_id} as locked: {e}")
        finally:
            session.close()

    def update_locked_contests(self) -> int:
        """Update status of contests that have passed their lock time.

        Returns:
            Number of contests marked as locked
        """
        session = self.db.get_session()
        count = 0

        try:
            now = datetime.now()

            # Find submitted contests past lock time
            locked = (
                session.query(ContestEntryDB)
                .filter(
                    ContestEntryDB.status == "submitted",
                    ContestEntryDB.lock_time <= now,
                )
                .all()
            )

            for entry in locked:
                entry.status = "locked"
                count += 1

            # Also mark eligible/pending contests past lock time as skipped
            missed = (
                session.query(ContestEntryDB)
                .filter(
                    ContestEntryDB.status.in_(["eligible", "pending"]),
                    ContestEntryDB.lock_time <= now,
                )
                .all()
            )

            for entry in missed:
                entry.status = "skipped"
                entry.skip_reason = "Missed lock time"
                count += 1

            session.commit()

            if count > 0:
                logger.info(f"Updated {count} contests to locked/skipped status")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update locked contests: {e}")
        finally:
            session.close()

        return count
