"""Contest filtering for eligible contests based on entry fee and multi-entry rules."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..common.database import ContestDB, ContestEntryDB, SeriesDB, get_database

logger = logging.getLogger(__name__)


@dataclass
class ContestFilterConfig:
    """Configuration for contest filtering."""

    max_entry_fee: float = 1.0
    require_multi_entry: bool = True
    min_prize_pool: float = 0.0
    gpp_only: bool = True  # Only guaranteed prize pool contests


class ContestFilter:
    """Filters contests based on eligibility criteria.

    Criteria:
    - entry_fee < max_entry_fee (default $1, so only < $1 contests)
    - multi_entry = True (allows multiple lineup entries)
    - Optionally: GPP only, minimum prize pool
    """

    def __init__(self, config: Optional[ContestFilterConfig] = None):
        """Initialize contest filter.

        Args:
            config: Filter configuration. Uses defaults if not provided.
        """
        self.config = config or ContestFilterConfig()
        self.db = get_database()

    def filter_contests(self, contests: list[dict]) -> list[dict]:
        """Filter contests based on eligibility criteria.

        Args:
            contests: List of contest dictionaries from Yahoo API

        Returns:
            List of eligible contests
        """
        eligible = []

        for contest in contests:
            if self._is_eligible(contest):
                eligible.append(contest)

        logger.info(
            f"Filtered {len(contests)} contests -> {len(eligible)} eligible "
            f"(fee < ${self.config.max_entry_fee}, multi_entry={self.config.require_multi_entry})"
        )

        return eligible

    def _is_eligible(self, contest: dict) -> bool:
        """Check if a single contest meets eligibility criteria.

        Args:
            contest: Contest dictionary from Yahoo API

        Returns:
            True if contest is eligible
        """
        # Extract entry fee - handle both /contests and /contestsFilteredWeb formats
        entry_fee = self._get_entry_fee(contest)

        # Check entry fee (strict less than for < $1 requirement)
        if entry_fee >= self.config.max_entry_fee:
            return False

        # Check multi-entry
        if self.config.require_multi_entry:
            is_multi_entry = contest.get("multipleEntry", False)
            if not is_multi_entry:
                return False

        # Check GPP only
        if self.config.gpp_only:
            is_guaranteed = contest.get("guaranteed", False)
            if not is_guaranteed:
                return False

        # Check minimum prize pool
        if self.config.min_prize_pool > 0:
            prize_pool = 0.0
            paid_prize = contest.get("paidTotalPrize", {})
            if isinstance(paid_prize, dict):
                prize_pool = paid_prize.get("value", 0.0) or paid_prize.get("amount", 0.0)
            elif isinstance(paid_prize, (int, float)):
                prize_pool = float(paid_prize)

            if prize_pool < self.config.min_prize_pool:
                return False

        # Check contest state
        state = contest.get("state", "")
        if state not in ("upcoming", "open"):
            return False

        return True

    def sync_eligible_contests(self, contests: list[dict], sport: str) -> int:
        """Filter contests and sync eligible ones to ContestEntryDB and ContestDB.

        Args:
            contests: List of contest dictionaries from Yahoo API
            sport: Sport code (e.g., 'nfl')

        Returns:
            Number of new eligible contests added
        """
        eligible = self.filter_contests(contests)
        session = self.db.get_session()
        new_count = 0

        try:
            for contest in eligible:
                contest_id = str(contest.get("id", ""))
                if not contest_id:
                    continue

                # Check if already tracked in ContestEntryDB
                existing_entry = (
                    session.query(ContestEntryDB)
                    .filter_by(contest_id=contest_id)
                    .first()
                )

                if existing_entry:
                    # Update last checked time
                    existing_entry.last_checked_at = datetime.utcnow()
                    continue

                # Extract lock time
                lock_time = None
                start_time_ms = contest.get("startTime", 0)
                if start_time_ms:
                    lock_time = datetime.fromtimestamp(start_time_ms / 1000)

                if not lock_time:
                    logger.warning(f"Contest {contest_id} has no lock time, skipping")
                    continue

                # Extract contest details
                slate_type = contest.get("slateType", "MULTI_GAME")
                salary_cap = contest.get("salaryCap", 200)
                entry_fee = self._get_entry_fee(contest)
                max_entries = contest.get("multipleEntryLimit", 1)
                prize_pool = contest.get("totalPrize", 0)
                if isinstance(prize_pool, dict):
                    prize_pool = prize_pool.get("value", 0) or prize_pool.get("amount", 0)
                title = contest.get("title", "")
                series_id = contest.get("seriesId")

                # Ensure series exists in SeriesDB (or create it)
                if series_id:
                    existing_series = session.query(SeriesDB).filter_by(id=series_id).first()
                    if not existing_series:
                        series = SeriesDB(
                            id=series_id,
                            sport=sport.upper(),
                            slate_start=lock_time,
                            slate_type=slate_type,
                            salary_cap=salary_cap,
                        )
                        session.add(series)
                        session.flush()  # Get the ID

                # Create or update ContestDB entry (for ProjectionDB FK)
                existing_contest = session.query(ContestDB).filter_by(id=contest_id).first()
                if not existing_contest:
                    db_contest = ContestDB(
                        id=contest_id,
                        series_id=series_id,
                        sport=sport.upper(),
                        name=title,
                        entry_fee=entry_fee,
                        max_entries=max_entries,
                        total_entries=contest.get("entryCount", 0),
                        entry_limit=contest.get("entryLimit"),
                        prize_pool=float(prize_pool) if prize_pool else None,
                        slate_start=lock_time,
                        status="upcoming",
                        contest_type=contest.get("type"),
                        slate_type=slate_type,
                        is_guaranteed=contest.get("guaranteed", False),
                        is_multi_entry=contest.get("multipleEntry", False),
                        salary_cap=salary_cap,
                    )
                    session.add(db_contest)

                # Create new entry tracking record in ContestEntryDB
                entry = ContestEntryDB(
                    contest_id=contest_id,
                    sport=sport,
                    status="eligible",
                    max_entries_allowed=max_entries,
                    lock_time=lock_time,
                    slate_type=slate_type,
                    salary_cap=salary_cap,
                )
                session.add(entry)
                new_count += 1

                logger.debug(
                    f"Added eligible contest: {contest_id} "
                    f"(fee: ${entry_fee:.2f}, "
                    f"max_entries: {max_entries}, "
                    f"slate: {slate_type}, salary_cap: {salary_cap})"
                )

            session.commit()
            logger.info(f"Synced {new_count} new eligible contests for {sport}")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to sync eligible contests: {e}")
            raise
        finally:
            session.close()

        return new_count

    def get_pending_contests(self, sport: Optional[str] = None) -> list[ContestEntryDB]:
        """Get contests that are eligible but not yet submitted.

        Args:
            sport: Optional sport filter

        Returns:
            List of ContestEntryDB records with status 'eligible' or 'pending'
        """
        session = self.db.get_session()
        try:
            query = session.query(ContestEntryDB).filter(
                ContestEntryDB.status.in_(["eligible", "pending"]),
                ContestEntryDB.lock_time > datetime.now(),
            )

            if sport:
                query = query.filter(ContestEntryDB.sport == sport)

            return query.order_by(ContestEntryDB.lock_time).all()

        finally:
            session.close()

    def get_submitted_contests(self, sport: Optional[str] = None) -> list[ContestEntryDB]:
        """Get contests that have been submitted but not yet locked.

        Args:
            sport: Optional sport filter

        Returns:
            List of ContestEntryDB records with status 'submitted'
        """
        session = self.db.get_session()
        try:
            query = session.query(ContestEntryDB).filter(
                ContestEntryDB.status == "submitted",
                ContestEntryDB.lock_time > datetime.now(),
            )

            if sport:
                query = query.filter(ContestEntryDB.sport == sport)

            return query.order_by(ContestEntryDB.lock_time).all()

        finally:
            session.close()

    def _get_entry_fee(self, contest: dict) -> float:
        """Extract entry fee from contest dict.

        Handles both API formats:
        - /contests endpoint: uses 'entryFee' as direct float
        - /contestsFilteredWeb endpoint: uses 'paidEntryFee' as dict with 'value'
        """
        # First try direct entryFee (from /contests endpoint)
        direct_fee = contest.get("entryFee")
        if direct_fee is not None and isinstance(direct_fee, (int, float)):
            return float(direct_fee)

        # Fall back to paidEntryFee (from /contestsFilteredWeb endpoint)
        paid_fee = contest.get("paidEntryFee", {})
        if isinstance(paid_fee, dict):
            return paid_fee.get("value", 0.0) or paid_fee.get("amount", 0.0)
        elif isinstance(paid_fee, (int, float)):
            return float(paid_fee)

        return 0.0
