"""Contest sync job - fetches and filters eligible contests."""

import logging
from typing import Optional

from ...yahoo.api import get_api_client
from ..contest_filter import ContestFilter, ContestFilterConfig
from .base import BaseJob

logger = logging.getLogger(__name__)


class ContestSyncJob(BaseJob):
    """Fetches contests from Yahoo and syncs eligible ones to database.

    Eligibility criteria:
    - entry_fee < $1 (strict less than)
    - multi_entry = True
    - GPP (guaranteed prize pool)
    """

    job_name = "contest_sync"

    def __init__(
        self,
        dry_run: bool = False,
        filter_config: Optional[ContestFilterConfig] = None,
    ):
        """Initialize contest sync job.

        Args:
            dry_run: If True, don't save to database
            filter_config: Contest filter configuration
        """
        super().__init__(dry_run)
        self.filter_config = filter_config or ContestFilterConfig(
            max_entry_fee=1.0,
            require_multi_entry=True,
            gpp_only=True,
        )
        self.contest_filter = ContestFilter(self.filter_config)
        self.api_client = get_api_client()

    def execute(self, sport: str = "nfl", **kwargs) -> dict:
        """Fetch and sync contests for a sport.

        Args:
            sport: Sport code (e.g., 'nfl', 'nba')

        Returns:
            Dict with sync results
        """
        logger.info(f"Syncing contests for {sport}...")

        # Fetch contests from Yahoo
        try:
            contests = self.api_client.get_contests(sport=sport)
            logger.info(f"Fetched {len(contests)} contests from Yahoo API")
        except Exception as e:
            logger.error(f"Failed to fetch contests: {e}")
            return {
                "sport": sport,
                "total_contests": 0,
                "eligible_contests": 0,
                "new_contests": 0,
                "error": str(e),
            }

        # Filter eligible contests
        eligible = self.contest_filter.filter_contests(contests)

        # Sync to database (unless dry run)
        new_count = 0
        if not self.dry_run:
            new_count = self.contest_filter.sync_eligible_contests(eligible, sport)
        else:
            logger.info(f"[DRY RUN] Would sync {len(eligible)} eligible contests")

        return {
            "sport": sport,
            "total_contests": len(contests),
            "eligible_contests": len(eligible),
            "new_contests": new_count,
            "items_processed": new_count,
        }

    def sync_all_sports(self, sports: Optional[list[str]] = None) -> dict:
        """Sync contests for multiple sports.

        Args:
            sports: List of sport codes. Defaults to ['nfl'].

        Returns:
            Dict with results per sport
        """
        if sports is None:
            sports = ["nfl"]

        results = {}
        for sport in sports:
            try:
                results[sport] = self.run(sport=sport)
            except Exception as e:
                logger.error(f"Failed to sync {sport} contests: {e}")
                results[sport] = {"error": str(e)}

        return results
