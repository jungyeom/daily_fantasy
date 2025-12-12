"""Live scoring - track real-time contest scores during games."""
import logging
from datetime import datetime
from typing import Optional

from selenium.webdriver.remote.webdriver import WebDriver

from ..common.database import get_database, LineupDB, ContestDB
from ..common.models import Sport, LineupStatus
from ..yahoo.results import ResultsFetcher
from ..lineup_manager.tracker import LineupTracker

logger = logging.getLogger(__name__)


class LiveScoring:
    """Tracks live scores for active contests."""

    def __init__(self):
        """Initialize live scoring tracker."""
        self.db = get_database()
        self.tracker = LineupTracker()
        self.results_fetcher = ResultsFetcher()

    def get_live_scores(
        self,
        driver: WebDriver,
        contest_id: str,
    ) -> list[dict]:
        """Get live scores for a contest.

        Args:
            driver: Authenticated WebDriver
            contest_id: Contest ID

        Returns:
            List of score dicts for user's lineups
        """
        return self.results_fetcher.fetch_live_scores(driver, contest_id)

    def get_active_contest_scores(
        self,
        driver: WebDriver,
        sport: Optional[Sport] = None,
    ) -> dict[str, list[dict]]:
        """Get live scores for all active contests.

        Args:
            driver: Authenticated WebDriver
            sport: Optional sport filter

        Returns:
            Dict mapping contest_id to scores
        """
        scores_by_contest = {}

        # Get active contests
        active_contests = self.tracker.get_active_contests(sport)

        for contest in active_contests:
            contest_id = contest["id"]
            try:
                scores = self.get_live_scores(driver, contest_id)
                scores_by_contest[contest_id] = scores
            except Exception as e:
                logger.error(f"Failed to get live scores for {contest_id}: {e}")
                continue

        return scores_by_contest

    def get_contest_standings(
        self,
        contest_id: str,
    ) -> dict:
        """Get current standings summary for a contest.

        Args:
            contest_id: Contest ID

        Returns:
            Dict with standings summary
        """
        session = self.db.get_session()
        try:
            contest = session.query(ContestDB).filter_by(id=contest_id).first()
            if not contest:
                return {}

            lineups = (
                session.query(LineupDB)
                .filter_by(contest_id=contest_id, status=LineupStatus.SUBMITTED.value)
                .all()
            )

            if not lineups:
                return {}

            return {
                "contest_id": contest_id,
                "contest_name": contest.name,
                "entry_fee": contest.entry_fee,
                "lineups_entered": len(lineups),
                "total_entries": contest.total_entries,
                "slate_start": contest.slate_start,
                "prize_pool": contest.prize_pool,
            }
        finally:
            session.close()

    def format_live_scores(self, scores: list[dict], contest_name: str = "") -> str:
        """Format live scores for display.

        Args:
            scores: List of score dicts
            contest_name: Optional contest name

        Returns:
            Formatted string
        """
        if not scores:
            return "No scores available"

        lines = []
        if contest_name:
            lines.append(f"Live Scores - {contest_name}")
            lines.append("=" * 50)

        for i, score in enumerate(scores, 1):
            lines.append(
                f"Lineup {i}: {score.get('current_points', 0):.1f} pts "
                f"(Rank: {score.get('current_rank', 'N/A'):,})"
            )

        return "\n".join(lines)


class ScoreAlert:
    """Checks for score-based alerts (e.g., in the money)."""

    def __init__(self, cash_line_percentile: float = 0.2):
        """Initialize score alert checker.

        Args:
            cash_line_percentile: Percentile threshold for cash line (top 20%)
        """
        self.cash_line_percentile = cash_line_percentile

    def check_cash_line(
        self,
        current_rank: int,
        total_entries: int,
    ) -> bool:
        """Check if current rank is in the money.

        Args:
            current_rank: Current position
            total_entries: Total entries in contest

        Returns:
            True if in the money
        """
        if total_entries == 0:
            return False

        percentile = current_rank / total_entries
        return percentile <= self.cash_line_percentile

    def check_top_finishes(
        self,
        scores: list[dict],
        total_entries: int,
        thresholds: list[int] = None,
    ) -> list[dict]:
        """Check for lineups with top finishes.

        Args:
            scores: List of score dicts
            total_entries: Total entries
            thresholds: Rank thresholds to check (default: [1, 10, 100])

        Returns:
            List of alerts for top finishes
        """
        thresholds = thresholds or [1, 10, 100]
        alerts = []

        for score in scores:
            rank = score.get("current_rank", 0)
            if rank == 0:
                continue

            for threshold in thresholds:
                if rank <= threshold:
                    alerts.append({
                        "rank": rank,
                        "threshold": threshold,
                        "points": score.get("current_points", 0),
                        "message": f"Lineup in top {threshold}! (Rank: {rank})",
                    })
                    break

        return alerts


def get_live_scoring() -> LiveScoring:
    """Get live scoring instance."""
    return LiveScoring()
