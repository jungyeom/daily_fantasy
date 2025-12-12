"""Projection sync job - fetches and updates projections from sources."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from ...common.database import get_database, ContestEntryDB, ProjectionDB
from ...common.models import Sport
from ...projections.sources.dailyfantasyfuel import DailyFantasyFuelSource
from .base import BaseJob

logger = logging.getLogger(__name__)


# Map sport strings to Sport enum
SPORT_MAP = {
    "nfl": Sport.NFL,
    "nba": Sport.NBA,
    "mlb": Sport.MLB,
    "nhl": Sport.NHL,
}


class ProjectionSyncJob(BaseJob):
    """Fetches projections and stores them in the database.

    Refresh intervals based on time to lock:
    - > 24 hours: every 6 hours
    - 6-24 hours: every 2 hours
    - 1-6 hours: every 30 min
    - < 1 hour: every 10 min
    """

    job_name = "projection_sync"

    # Refresh intervals in minutes
    INTERVALS = {
        "default": 360,  # 6 hours
        "day_of": 120,  # 2 hours
        "approaching": 30,
        "imminent": 10,
    }

    def __init__(self, dry_run: bool = False):
        """Initialize projection sync job.

        Args:
            dry_run: If True, don't save to database
        """
        super().__init__(dry_run)
        self.sources = {
            "dailyfantasyfuel": DailyFantasyFuelSource(),
        }

    def execute(self, sport: str = "nfl", force: bool = False, **kwargs) -> dict:
        """Fetch and store projections for a sport.

        Args:
            sport: Sport code (e.g., 'nfl')
            force: If True, refresh regardless of interval

        Returns:
            Dict with sync results
        """
        logger.info(f"Syncing projections for {sport}...")

        # Check if we should refresh based on upcoming contests
        should_refresh, reason = self._should_refresh(sport, force)

        if not should_refresh:
            logger.info(f"Skipping projection refresh: {reason}")
            return {
                "sport": sport,
                "refreshed": False,
                "reason": reason,
                "items_processed": 0,
            }

        # Get Sport enum
        sport_enum = SPORT_MAP.get(sport.lower())
        if not sport_enum:
            logger.error(f"Unknown sport: {sport}")
            return {"sport": sport, "error": f"Unknown sport: {sport}"}

        # Fetch from all enabled sources
        all_projections = []
        source_results = {}

        for source_name, source in self.sources.items():
            try:
                projections = source.fetch_projections(sport_enum)
                all_projections.extend(projections)
                source_results[source_name] = len(projections)
                logger.info(f"Fetched {len(projections)} projections from {source_name}")

            except Exception as e:
                logger.error(f"Failed to fetch from {source_name}: {e}")
                source_results[source_name] = {"error": str(e)}

        # Store projections (future: aggregate from multiple sources)
        stored_count = 0
        if not self.dry_run and all_projections:
            stored_count = self._store_projections(sport, all_projections)
        elif self.dry_run:
            logger.info(f"[DRY RUN] Would store {len(all_projections)} projections")

        return {
            "sport": sport,
            "refreshed": True,
            "reason": reason,
            "sources": source_results,
            "total_projections": len(all_projections),
            "stored_count": stored_count,
            "items_processed": stored_count,
        }

    def _should_refresh(self, sport: str, force: bool = False) -> tuple[bool, str]:
        """Determine if projections should be refreshed.

        Args:
            sport: Sport code
            force: If True, always refresh

        Returns:
            Tuple of (should_refresh, reason)
        """
        if force:
            return True, "Forced refresh"

        session = self.db.get_session()
        try:
            # Find the soonest lock time for pending contests
            # Note: lock_time is stored as local time, so compare with datetime.now()
            soonest_contest = (
                session.query(ContestEntryDB)
                .filter(
                    ContestEntryDB.sport == sport,
                    ContestEntryDB.status.in_(["eligible", "pending", "submitted"]),
                    ContestEntryDB.lock_time > datetime.now(),
                )
                .order_by(ContestEntryDB.lock_time)
                .first()
            )

            if not soonest_contest:
                return False, "No upcoming contests"

            # Calculate time to lock (using local time since lock_time is stored as local)
            time_to_lock = soonest_contest.lock_time - datetime.now()
            hours_to_lock = time_to_lock.total_seconds() / 3600

            # Determine appropriate interval
            if hours_to_lock > 24:
                interval = self.INTERVALS["default"]
                interval_name = "default"
            elif hours_to_lock > 6:
                interval = self.INTERVALS["day_of"]
                interval_name = "day_of"
            elif hours_to_lock > 1:
                interval = self.INTERVALS["approaching"]
                interval_name = "approaching"
            else:
                interval = self.INTERVALS["imminent"]
                interval_name = "imminent"

            # Check last refresh time
            last_projection = (
                session.query(ProjectionDB)
                .filter_by(source="dailyfantasyfuel")  # Check primary source
                .order_by(ProjectionDB.fetched_at.desc())
                .first()
            )

            if last_projection:
                time_since_refresh = datetime.utcnow() - last_projection.fetched_at
                minutes_since = time_since_refresh.total_seconds() / 60

                if minutes_since < interval:
                    return False, (
                        f"Last refresh {minutes_since:.0f} min ago, "
                        f"interval is {interval} min ({interval_name})"
                    )

            return True, (
                f"Time to lock: {hours_to_lock:.1f}h, "
                f"using {interval_name} interval ({interval} min)"
            )

        finally:
            session.close()

    def _store_projections(self, sport: str, projections: list) -> int:
        """Store projections in database.

        Args:
            sport: Sport code
            projections: List of Projection objects

        Returns:
            Number of projections stored
        """
        session = self.db.get_session()
        stored = 0

        try:
            # Get upcoming contest IDs for this sport (lock_time in future)
            contests = (
                session.query(ContestEntryDB)
                .filter(
                    ContestEntryDB.sport == sport,
                    ContestEntryDB.status.in_(["eligible", "pending", "submitted"]),
                    ContestEntryDB.lock_time > datetime.now(),
                )
                .order_by(ContestEntryDB.lock_time)
                .all()
            )

            if not contests:
                logger.warning(f"No active contests for {sport}")
                return 0

            # For simplicity, store projections for the first contest
            # In production, you'd want to store per-contest or per-series
            contest_id = contests[0].contest_id

            for proj in projections:
                # Check if already exists
                existing = (
                    session.query(ProjectionDB)
                    .filter_by(
                        contest_id=contest_id,
                        source=proj.source,
                        source_player_name=proj.name,
                    )
                    .first()
                )

                if existing:
                    # Update existing
                    existing.projected_points = proj.projected_points
                    existing.projected_ownership = proj.projected_ownership
                    existing.fetched_at = datetime.utcnow()
                else:
                    # Create new
                    db_proj = ProjectionDB(
                        contest_id=contest_id,
                        source_player_name=proj.name,
                        team=proj.team,
                        position=proj.position,
                        source=proj.source,
                        projected_points=proj.projected_points,
                        projected_ownership=proj.projected_ownership,
                    )
                    session.add(db_proj)

                stored += 1

            session.commit()
            logger.info(f"Stored {stored} projections for {sport}")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to store projections: {e}")
            raise
        finally:
            session.close()

        return stored
