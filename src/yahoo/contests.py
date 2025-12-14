"""Yahoo Daily Fantasy contest discovery and management."""
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from selenium.webdriver.remote.webdriver import WebDriver

from ..common.config import get_config, ContestFilterConfig
from ..common.database import get_database, ContestDB
from ..common.exceptions import YahooContestNotFoundError, YahooError, YahooAPIError
from ..common.models import Contest, ContestStatus, Sport
from .api import get_api_client, parse_api_contest, SPORT_CODES

logger = logging.getLogger(__name__)

YAHOO_DFS_BASE_URL = "https://sports.yahoo.com/dailyfantasy"


class ContestFetcher:
    """Fetches and filters contests from Yahoo Daily Fantasy.

    This class now primarily uses the Yahoo DFS API for fetching contests,
    which is faster and more reliable than web scraping.
    """

    def __init__(self, filter_config: Optional[ContestFilterConfig] = None):
        """Initialize contest fetcher.

        Args:
            filter_config: Contest filtering criteria. Uses global config if not provided.
        """
        if filter_config is None:
            filter_config = get_config().contest_filter
        self.filter_config = filter_config
        self.db = get_database()
        self.api_client = get_api_client()

    def fetch_contests(
        self,
        sport: Sport,
        save_to_db: bool = True,
        driver: Optional[WebDriver] = None,  # No longer required
    ) -> list[Contest]:
        """Fetch available contests for a sport from Yahoo DFS.

        Uses the Yahoo DFS API directly instead of web scraping.

        Args:
            sport: Sport to fetch contests for
            save_to_db: Whether to save contests to database
            driver: WebDriver (no longer required, kept for backward compatibility)

        Returns:
            List of Contest objects matching filter criteria
        """
        logger.info(f"Fetching {sport.value} contests from Yahoo DFS API...")

        try:
            # Fetch from API
            raw_contests = self.api_client.get_contests(sport)

            # Parse contests
            contests = []
            for raw in raw_contests:
                try:
                    parsed = parse_api_contest(raw)
                    contest = Contest(
                        id=parsed["id"],
                        series_id=parsed.get("series_id"),
                        sport=parsed["sport"],
                        name=parsed["name"],
                        entry_fee=parsed["entry_fee"],
                        max_entries=parsed["max_entries"],
                        total_entries=parsed["total_entries"],
                        entry_limit=parsed.get("entry_limit"),
                        prize_pool=parsed["prize_pool"],
                        slate_start=parsed["slate_start"],
                        status=ContestStatus.UPCOMING,
                        contest_type=parsed.get("contest_type"),
                        slate_type=parsed.get("slate_type"),
                        is_guaranteed=parsed.get("is_guaranteed", False),
                        is_multi_entry=parsed.get("is_multi_entry", False),
                        salary_cap=parsed.get("salary_cap", 200),
                    )
                    contests.append(contest)
                except Exception as e:
                    logger.debug(f"Failed to parse contest: {e}")
                    continue

            # Apply filters
            filtered_contests = self._apply_filters(contests)

            logger.info(
                f"Found {len(contests)} total contests, "
                f"{len(filtered_contests)} match filters"
            )

            # Save to database
            if save_to_db and filtered_contests:
                self._save_contests(filtered_contests)

            return filtered_contests

        except YahooAPIError as e:
            logger.error(f"API error fetching contests: {e}")
            raise YahooError(f"Contest fetch failed: {e}") from e
        except Exception as e:
            logger.error(f"Failed to fetch contests: {e}")
            raise YahooError(f"Contest fetch failed: {e}") from e

    def fetch_all_sports_contests(
        self,
        sports: Optional[list[Sport]] = None,
        save_to_db: bool = True,
    ) -> dict[Sport, list[Contest]]:
        """Fetch contests for multiple sports.

        Args:
            sports: List of sports to fetch. If None, fetches all enabled sports.
            save_to_db: Whether to save contests to database

        Returns:
            Dictionary mapping Sport to list of filtered Contest objects
        """
        if sports is None:
            # Default to main sports
            sports = [Sport.NFL, Sport.NBA, Sport.MLB, Sport.NHL]

        results = {}
        for sport in sports:
            try:
                contests = self.fetch_contests(sport, save_to_db=save_to_db)
                results[sport] = contests
            except Exception as e:
                logger.error(f"Failed to fetch {sport.value} contests: {e}")
                results[sport] = []

        return results

    def get_contest_by_id(self, contest_id: str) -> Optional[Contest]:
        """Get contest details by ID.

        Searches through all contests to find the one with matching ID.
        First checks database, then queries API if not found.

        Args:
            contest_id: Yahoo contest ID

        Returns:
            Contest object or None if not found
        """
        # Check database first
        session = self.db.get_session()
        try:
            db_contest = session.query(ContestDB).filter_by(id=contest_id).first()
            if db_contest:
                return Contest(
                    id=db_contest.id,
                    sport=Sport(db_contest.sport),
                    name=db_contest.name,
                    entry_fee=Decimal(str(db_contest.entry_fee)),
                    max_entries=db_contest.max_entries,
                    total_entries=db_contest.total_entries,
                    prize_pool=Decimal(str(db_contest.prize_pool)) if db_contest.prize_pool else None,
                    slate_start=db_contest.slate_start,
                    status=ContestStatus(db_contest.status),
                )
        finally:
            session.close()

        # Query API for all contests and find matching one
        try:
            raw_contests = self.api_client.get_contests()
            for raw in raw_contests:
                if str(raw.get("id")) == str(contest_id):
                    parsed = parse_api_contest(raw)
                    return Contest(
                        id=parsed["id"],
                        sport=parsed["sport"],
                        name=parsed["name"],
                        entry_fee=parsed["entry_fee"],
                        max_entries=parsed["max_entries"],
                        total_entries=parsed["total_entries"],
                        prize_pool=parsed["prize_pool"],
                        slate_start=parsed["slate_start"],
                        status=ContestStatus.UPCOMING,
                    )
        except Exception as e:
            logger.error(f"Failed to fetch contest {contest_id}: {e}")

        return None

    def _apply_filters(self, contests: list[Contest]) -> list[Contest]:
        """Apply filter criteria to contest list.

        Args:
            contests: List of all contests

        Returns:
            Filtered list of contests
        """
        filtered = []

        for contest in contests:
            # Entry fee filter
            if float(contest.entry_fee) > self.filter_config.max_entry_fee:
                continue

            # Multi-entry filter (GPP)
            if self.filter_config.multi_entry_only and contest.max_entries <= 1:
                continue

            # Prize pool filter
            if contest.prize_pool and float(contest.prize_pool) < self.filter_config.min_prize_pool:
                continue

            filtered.append(contest)

        return filtered

    def _save_contests(self, contests: list[Contest]) -> None:
        """Save contests to database.

        Args:
            contests: List of contests to save
        """
        session = self.db.get_session()
        try:
            for contest in contests:
                # Check if contest already exists
                existing = session.query(ContestDB).filter_by(id=contest.id).first()

                if existing:
                    # Update existing contest
                    existing.total_entries = contest.total_entries
                    existing.prize_pool = float(contest.prize_pool) if contest.prize_pool else None
                    existing.status = contest.status.value
                    # Update slate info if available
                    if contest.slate_type:
                        existing.slate_type = contest.slate_type
                    if contest.salary_cap:
                        existing.salary_cap = contest.salary_cap
                else:
                    # Insert new contest
                    db_contest = ContestDB(
                        id=contest.id,
                        sport=contest.sport.value,
                        name=contest.name,
                        entry_fee=float(contest.entry_fee),
                        max_entries=contest.max_entries,
                        total_entries=contest.total_entries,
                        prize_pool=float(contest.prize_pool) if contest.prize_pool else None,
                        slate_start=contest.slate_start,
                        slate_end=contest.slate_end,
                        status=contest.status.value,
                        slate_type=contest.slate_type,
                        salary_cap=contest.salary_cap,
                    )
                    session.add(db_contest)

            session.commit()
            logger.info(f"Saved {len(contests)} contests to database")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save contests: {e}")
        finally:
            session.close()


def fetch_upcoming_contests(
    sport: Sport,
    driver: Optional[WebDriver] = None,  # Kept for backward compatibility
) -> list[Contest]:
    """Convenience function to fetch upcoming contests.

    Args:
        sport: Sport to fetch
        driver: WebDriver (no longer required)

    Returns:
        List of filtered Contest objects
    """
    fetcher = ContestFetcher()
    return fetcher.fetch_contests(sport)


def get_all_available_contests() -> list[dict]:
    """Get all available contests from API (unfiltered).

    Returns raw API data for inspection/debugging.

    Returns:
        List of raw contest dictionaries
    """
    client = get_api_client()
    return client.get_contests()
