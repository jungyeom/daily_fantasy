"""Yahoo Daily Fantasy contest results scraping."""
import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..common.config import get_config
from ..common.database import get_database, ResultDB, LineupDB, ContestDB
from ..common.exceptions import YahooError
from ..common.models import ContestResult

logger = logging.getLogger(__name__)

YAHOO_DFS_BASE_URL = "https://sports.yahoo.com/dailyfantasy"


class ResultsFetcher:
    """Fetches contest results from Yahoo DFS."""

    def __init__(self):
        """Initialize results fetcher."""
        self.config = get_config()
        self.db = get_database()

    def fetch_contest_results(
        self,
        driver: WebDriver,
        contest_id: str,
        save_to_db: bool = True,
    ) -> list[ContestResult]:
        """Fetch results for a completed contest.

        Args:
            driver: Authenticated Selenium WebDriver
            contest_id: Yahoo contest ID
            save_to_db: Whether to save results to database

        Returns:
            List of ContestResult objects for user's lineups
        """
        logger.info(f"Fetching results for contest {contest_id}...")

        try:
            # Navigate to contest results page
            results_url = f"{YAHOO_DFS_BASE_URL}/contest/{contest_id}/results"
            driver.get(results_url)

            wait = WebDriverWait(driver, 30)

            # Wait for results to load
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "[data-tst='resultsTable'], .results-table, .standings"
                ))
            )

            # Get total entries in contest
            total_entries = self._get_total_entries(driver)

            # Find user's entries in results
            results = self._parse_user_results(driver, contest_id, total_entries)

            logger.info(f"Found {len(results)} results for contest {contest_id}")

            # Save to database
            if save_to_db and results:
                self._save_results(results)

            return results

        except Exception as e:
            logger.error(f"Failed to fetch results: {e}")
            raise YahooError(f"Results fetch failed: {e}") from e

    def _get_total_entries(self, driver: WebDriver) -> int:
        """Get total number of entries in contest.

        Args:
            driver: WebDriver on results page

        Returns:
            Total entry count
        """
        try:
            entries_elem = driver.find_element(
                By.CSS_SELECTOR,
                "[data-tst='totalEntries'], .total-entries, .entry-count"
            )
            text = entries_elem.text
            match = re.search(r"([\d,]+)", text)
            if match:
                return int(match.group(1).replace(",", ""))
        except Exception:
            pass

        return 0

    def _parse_user_results(
        self,
        driver: WebDriver,
        contest_id: str,
        total_entries: int,
    ) -> list[ContestResult]:
        """Parse user's results from page.

        Args:
            driver: WebDriver on results page
            contest_id: Contest ID
            total_entries: Total entries in contest

        Returns:
            List of ContestResult objects
        """
        results = []

        try:
            # Look for "My Entries" section or filter
            try:
                my_entries_tab = driver.find_element(
                    By.CSS_SELECTOR,
                    "[data-tst='myEntries'], .my-entries-tab, a[contains(text(), 'My')]"
                )
                my_entries_tab.click()
                import time
                time.sleep(1)
            except Exception:
                pass  # May already be showing user's entries

            # Find all entry rows
            entry_rows = driver.find_elements(
                By.CSS_SELECTOR,
                "[data-tst='entryRow'], .entry-row, tr.user-entry"
            )

            for row in entry_rows:
                try:
                    result = self._parse_result_row(row, contest_id, total_entries)
                    if result:
                        results.append(result)
                except Exception as e:
                    logger.debug(f"Failed to parse result row: {e}")
                    continue

        except Exception as e:
            logger.error(f"Failed to parse user results: {e}")

        return results

    def _parse_result_row(
        self,
        row,
        contest_id: str,
        total_entries: int,
    ) -> Optional[ContestResult]:
        """Parse a single result row.

        Args:
            row: WebElement containing result info
            contest_id: Contest ID
            total_entries: Total entries in contest

        Returns:
            ContestResult object or None
        """
        try:
            # Extract finish position
            rank_elem = row.find_element(
                By.CSS_SELECTOR,
                "[data-tst='rank'], .rank, .position, td:first-child"
            )
            rank_text = rank_elem.text.strip()
            finish_position = int(re.sub(r"[^\d]", "", rank_text))

            # Extract points
            points_elem = row.find_element(
                By.CSS_SELECTOR,
                "[data-tst='points'], .points, .score"
            )
            points_text = points_elem.text.strip()
            actual_points = float(re.sub(r"[^\d.]", "", points_text))

            # Extract winnings
            winnings = Decimal("0")
            try:
                winnings_elem = row.find_element(
                    By.CSS_SELECTOR,
                    "[data-tst='winnings'], .winnings, .prize"
                )
                winnings_text = winnings_elem.text.strip()
                if winnings_text and winnings_text != "-":
                    winnings = Decimal(re.sub(r"[^\d.]", "", winnings_text))
            except Exception:
                pass

            # Try to get lineup ID (may be in data attribute or link)
            lineup_id = None
            try:
                lineup_id_attr = row.get_attribute("data-lineup-id")
                if lineup_id_attr:
                    lineup_id = int(lineup_id_attr)
            except Exception:
                pass

            # Calculate percentile
            percentile = None
            if total_entries > 0:
                percentile = (1 - (finish_position / total_entries)) * 100

            return ContestResult(
                lineup_id=lineup_id or 0,  # Will be matched later
                contest_id=contest_id,
                actual_points=actual_points,
                finish_position=finish_position,
                entries_count=total_entries,
                winnings=winnings,
            )

        except Exception as e:
            logger.debug(f"Failed to parse result row: {e}")
            return None

    def _save_results(self, results: list[ContestResult]) -> None:
        """Save results to database.

        Args:
            results: List of ContestResult objects
        """
        session = self.db.get_session()
        try:
            for result in results:
                # Try to match with existing lineup by points
                if result.lineup_id == 0:
                    lineup = (
                        session.query(LineupDB)
                        .filter_by(contest_id=result.contest_id)
                        .filter(LineupDB.projected_points.isnot(None))
                        .order_by(
                            # Match by closest projected points
                            (LineupDB.projected_points - result.actual_points).abs()
                        )
                        .first()
                    )
                    if lineup:
                        result.lineup_id = lineup.id

                # Check if result already exists
                existing = (
                    session.query(ResultDB)
                    .filter_by(lineup_id=result.lineup_id, contest_id=result.contest_id)
                    .first()
                )

                if existing:
                    # Update existing
                    existing.actual_points = result.actual_points
                    existing.finish_position = result.finish_position
                    existing.winnings = float(result.winnings)
                else:
                    # Insert new
                    db_result = ResultDB(
                        lineup_id=result.lineup_id,
                        contest_id=result.contest_id,
                        actual_points=result.actual_points,
                        finish_position=result.finish_position,
                        entries_count=result.entries_count,
                        percentile=(1 - (result.finish_position / result.entries_count)) * 100 if result.entries_count > 0 else None,
                        winnings=float(result.winnings),
                    )
                    session.add(db_result)

                # Update lineup actual points
                if result.lineup_id:
                    lineup = session.query(LineupDB).filter_by(id=result.lineup_id).first()
                    if lineup:
                        lineup.actual_points = result.actual_points

            session.commit()
            logger.info(f"Saved {len(results)} results to database")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save results: {e}")
        finally:
            session.close()

    def fetch_live_scores(
        self,
        driver: WebDriver,
        contest_id: str,
    ) -> list[dict]:
        """Fetch live scores for an in-progress contest.

        Args:
            driver: Authenticated WebDriver
            contest_id: Contest ID

        Returns:
            List of score dicts with lineup_id, current_points, projected_finish
        """
        logger.info(f"Fetching live scores for contest {contest_id}...")

        try:
            # Navigate to live contest page
            live_url = f"{YAHOO_DFS_BASE_URL}/contest/{contest_id}/live"
            driver.get(live_url)

            wait = WebDriverWait(driver, 30)

            # Wait for scores to load
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "[data-tst='liveScores'], .live-scores, .standings"
                ))
            )

            scores = []

            # Find user's entries
            entry_rows = driver.find_elements(
                By.CSS_SELECTOR,
                "[data-tst='entryRow'], .entry-row, tr.user-entry"
            )

            for row in entry_rows:
                try:
                    # Get current points
                    points_elem = row.find_element(
                        By.CSS_SELECTOR,
                        "[data-tst='points'], .points, .score"
                    )
                    current_points = float(re.sub(r"[^\d.]", "", points_elem.text))

                    # Get current rank
                    rank_elem = row.find_element(
                        By.CSS_SELECTOR,
                        "[data-tst='rank'], .rank"
                    )
                    current_rank = int(re.sub(r"[^\d]", "", rank_elem.text))

                    scores.append({
                        "current_points": current_points,
                        "current_rank": current_rank,
                    })
                except Exception:
                    continue

            return scores

        except Exception as e:
            logger.error(f"Failed to fetch live scores: {e}")
            return []

    def get_completed_contests(self, driver: WebDriver) -> list[str]:
        """Get list of completed contests that need results fetched.

        Args:
            driver: Authenticated WebDriver

        Returns:
            List of contest IDs
        """
        try:
            # Navigate to history page
            history_url = f"{YAHOO_DFS_BASE_URL}/history"
            driver.get(history_url)

            wait = WebDriverWait(driver, 30)

            # Wait for history to load
            wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "[data-tst='historyList'], .history-list, .contest-history"
                ))
            )

            contest_ids = []

            # Find completed contests
            contest_links = driver.find_elements(
                By.CSS_SELECTOR,
                "a[href*='/contest/']"
            )

            for link in contest_links:
                href = link.get_attribute("href")
                match = re.search(r"/contest/(\d+)", href)
                if match:
                    contest_ids.append(match.group(1))

            return list(set(contest_ids))  # Deduplicate

        except Exception as e:
            logger.error(f"Failed to get completed contests: {e}")
            return []


def fetch_results(driver: WebDriver, contest_id: str) -> list[ContestResult]:
    """Convenience function to fetch contest results.

    Args:
        driver: Authenticated WebDriver
        contest_id: Contest ID

    Returns:
        List of ContestResult objects
    """
    fetcher = ResultsFetcher()
    return fetcher.fetch_contest_results(driver, contest_id)
