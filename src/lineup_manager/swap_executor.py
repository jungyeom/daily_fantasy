"""Executes late swaps by re-submitting updated lineups to Yahoo."""
import logging
from datetime import datetime
from typing import Optional

from selenium.webdriver.remote.webdriver import WebDriver

from ..common.config import get_config
from ..common.database import get_database, ContestDB
from ..common.models import Lineup, LineupStatus, Player, Sport
from ..common.notifications import get_notifier
from ..yahoo.submission import LineupSubmitter
from .late_swap import LateSwapManager, SwapResult
from .news_monitor import NewsMonitor
from .tracker import LineupTracker

logger = logging.getLogger(__name__)


class SwapExecutor:
    """Executes late swaps end-to-end including Yahoo submission."""

    def __init__(self):
        """Initialize swap executor."""
        self.config = get_config()
        self.db = get_database()
        self.tracker = LineupTracker()
        self.swap_manager = LateSwapManager()
        self.news_monitor = NewsMonitor()
        self.submitter = LineupSubmitter()
        self.notifier = get_notifier()

    def check_and_execute_swaps(
        self,
        driver: WebDriver,
        sport: Sport,
        players: list[Player],
    ) -> dict[str, list[SwapResult]]:
        """Check for needed swaps and execute them.

        This is the main entry point for late swap processing.

        Args:
            driver: Authenticated Selenium WebDriver
            sport: Sport to check
            players: Current player pool

        Returns:
            Dict mapping contest_id to list of SwapResults
        """
        logger.info(f"Checking for {sport.value} late swaps...")

        results_by_contest = {}

        # Get active contests (submitted lineups, not yet locked)
        active_contests = self.tracker.get_active_contests(sport)

        if not active_contests:
            logger.info(f"No active {sport.value} contests with submitted lineups")
            return results_by_contest

        logger.info(f"Found {len(active_contests)} active contests to check")

        # Fetch current projections
        current_projections = self.news_monitor.fetch_current_projections(sport, players)

        if not current_projections:
            logger.warning("Failed to fetch current projections")
            return results_by_contest

        # Get inactive players
        inactive_players = self.news_monitor.get_inactive_players(sport, players)

        # Process each contest
        for contest_info in active_contests:
            contest_id = contest_info["id"]

            # Check if contest is still before lock
            if not self._is_before_lock(contest_id):
                logger.info(f"Contest {contest_id} is locked, skipping")
                continue

            # Process swaps for this contest
            swap_results = self.swap_manager.process_late_swaps(
                contest_id=contest_id,
                current_projections=current_projections,
                available_players=players,
                inactive_players=inactive_players,
                notify=False,  # We'll handle notifications after submission
            )

            if not swap_results:
                continue

            # Get lineups that were swapped
            swapped_lineups = self._get_swapped_lineups(contest_id, swap_results)

            if swapped_lineups:
                # Re-submit swapped lineups
                self._resubmit_lineups(
                    driver=driver,
                    lineups=swapped_lineups,
                    contest_id=contest_id,
                    contest_name=contest_info.get("name", contest_id),
                    sport_name=sport.value,
                )

                # Send notifications for successful swaps
                self._send_swap_notifications(swap_results, sport.value, contest_info.get("name", contest_id))

            results_by_contest[contest_id] = swap_results

        return results_by_contest

    def _is_before_lock(self, contest_id: str) -> bool:
        """Check if contest is before lock time.

        Args:
            contest_id: Contest ID

        Returns:
            True if before lock
        """
        session = self.db.get_session()
        try:
            contest = session.query(ContestDB).filter_by(id=contest_id).first()
            if not contest:
                return False

            return contest.slate_start > datetime.utcnow()
        finally:
            session.close()

    def _get_swapped_lineups(
        self,
        contest_id: str,
        swap_results: list[SwapResult],
    ) -> list[Lineup]:
        """Get lineups that were successfully swapped.

        Args:
            contest_id: Contest ID
            swap_results: Swap results

        Returns:
            List of updated Lineup objects
        """
        successful_lineup_ids = {r.lineup_id for r in swap_results if r.success}

        if not successful_lineup_ids:
            return []

        # Get updated lineups from database
        lineups = self.tracker.get_lineups_for_contest(
            contest_id,
            status=LineupStatus.SWAPPED,
        )

        return [l for l in lineups if l.id in successful_lineup_ids]

    def _resubmit_lineups(
        self,
        driver: WebDriver,
        lineups: list[Lineup],
        contest_id: str,
        contest_name: str,
        sport_name: str,
    ) -> bool:
        """Re-submit swapped lineups to Yahoo.

        Args:
            driver: WebDriver
            lineups: Lineups to re-submit
            contest_id: Contest ID
            contest_name: Contest name
            sport_name: Sport name

        Returns:
            True if submission successful
        """
        logger.info(f"Re-submitting {len(lineups)} swapped lineups for contest {contest_id}")

        try:
            # Yahoo may require canceling existing entries first
            # For now, assume we can update in place via CSV upload

            successful, failed = self.submitter.submit_lineups(
                driver=driver,
                lineups=lineups,
                contest_id=contest_id,
                sport_name=sport_name,
                contest_name=contest_name,
            )

            if successful > 0:
                logger.info(f"Re-submitted {successful} lineups successfully")

                # Update status back to submitted
                for lineup in lineups[:successful]:
                    self.tracker.mark_submitted(lineup.id)

                return True
            else:
                logger.error("Failed to re-submit any lineups")
                return False

        except Exception as e:
            logger.error(f"Failed to re-submit lineups: {e}")
            self.notifier.notify_error(
                error_type="LateSwapSubmissionError",
                error_message=str(e),
                context={
                    "contest_id": contest_id,
                    "sport": sport_name,
                    "lineup_count": len(lineups),
                },
            )
            return False

    def _send_swap_notifications(
        self,
        swap_results: list[SwapResult],
        sport_name: str,
        contest_name: str,
    ) -> None:
        """Send notifications for successful swaps.

        Args:
            swap_results: List of swap results
            sport_name: Sport name
            contest_name: Contest name
        """
        for result in swap_results:
            if result.success:
                self.notifier.notify_late_swap(
                    sport=sport_name,
                    contest_name=contest_name,
                    lineup_id=result.lineup_id,
                    old_player=result.old_player_name,
                    new_player=result.new_player_name,
                    reason="projection_drop",
                )

    def execute_manual_swap(
        self,
        driver: WebDriver,
        lineup_id: int,
        old_player_id: str,
        new_player_id: str,
        new_player: Player,
    ) -> bool:
        """Manually execute a specific swap.

        Args:
            driver: WebDriver
            lineup_id: Lineup ID
            old_player_id: Player ID to swap out
            new_player_id: Player ID to swap in
            new_player: New player object

        Returns:
            True if successful
        """
        lineup = self.tracker.get_lineup_by_id(lineup_id)
        if not lineup:
            logger.error(f"Lineup {lineup_id} not found")
            return False

        # Find the player to swap
        old_player = None
        for player in lineup.players:
            if player.yahoo_player_id == old_player_id:
                old_player = player
                break

        if not old_player:
            logger.error(f"Player {old_player_id} not found in lineup {lineup_id}")
            return False

        # Create swap candidate
        from .late_swap import SwapCandidate
        candidate = SwapCandidate(
            lineup_id=lineup_id,
            player_id=old_player_id,
            player_name=old_player.name,
            position=old_player.roster_position,
            original_projection=old_player.projected_points,
            current_projection=0,
            reason="manual",
        )

        # Execute swap in database
        result = self.swap_manager.execute_swap(
            lineup=lineup,
            candidate=candidate,
            replacement=new_player,
            current_projection=new_player.projected_points or 0,
        )

        if not result.success:
            logger.error(f"Swap failed: {result.error}")
            return False

        # Get updated lineup and re-submit
        updated_lineup = self.tracker.get_lineup_by_id(lineup_id)
        if updated_lineup:
            # Get contest info
            session = self.db.get_session()
            try:
                contest = session.query(ContestDB).filter_by(id=lineup.contest_id).first()
                sport_name = contest.sport if contest else "Unknown"
                contest_name = contest.name if contest else lineup.contest_id
            finally:
                session.close()

            return self._resubmit_lineups(
                driver=driver,
                lineups=[updated_lineup],
                contest_id=lineup.contest_id,
                contest_name=contest_name,
                sport_name=sport_name,
            )

        return False


def get_swap_executor() -> SwapExecutor:
    """Get swap executor instance."""
    return SwapExecutor()


def execute_late_swaps(
    driver: WebDriver,
    sport: Sport,
    players: list[Player],
) -> dict[str, list[SwapResult]]:
    """Convenience function to check and execute late swaps.

    Args:
        driver: Authenticated WebDriver
        sport: Sport
        players: Player pool

    Returns:
        Results by contest
    """
    executor = SwapExecutor()
    return executor.check_and_execute_swaps(driver, sport, players)
