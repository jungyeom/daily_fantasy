"""Scheduled job definitions for automation pipeline."""
import logging
from datetime import datetime
from typing import Optional

from ..common.config import get_config
from ..common.database import get_database, ContestDB
from ..common.models import Sport
from ..common.notifications import get_notifier

logger = logging.getLogger(__name__)


class JobContext:
    """Context passed to jobs with shared resources."""

    def __init__(self):
        """Initialize job context."""
        self.config = get_config()
        self.db = get_database()
        self.notifier = get_notifier()
        self._driver = None

    def get_driver(self):
        """Get or create authenticated WebDriver."""
        if self._driver is None:
            from ..yahoo.browser import get_browser_manager
            from ..yahoo.auth import YahooAuth

            browser = get_browser_manager()
            self._driver = browser.create_driver()

            # Authenticate
            auth = YahooAuth()
            auth.login(self._driver)

        return self._driver

    def close_driver(self):
        """Close WebDriver if open."""
        if self._driver:
            from ..yahoo.browser import get_browser_manager
            get_browser_manager().close_driver()
            self._driver = None


def job_fetch_contests(context: JobContext, sport: Sport) -> int:
    """Fetch available contests for a sport.

    Args:
        context: Job context
        sport: Sport to fetch

    Returns:
        Number of contests found
    """
    logger.info(f"Running job: fetch_contests for {sport.value}")

    try:
        from ..yahoo.contests import ContestFetcher

        driver = context.get_driver()
        fetcher = ContestFetcher()

        contests = fetcher.fetch_contests(driver, sport)
        logger.info(f"Fetched {len(contests)} {sport.value} contests")

        return len(contests)

    except Exception as e:
        logger.error(f"Job fetch_contests failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "fetch_contests", "sport": sport.value},
        )
        return 0


def job_fetch_player_pool(
    context: JobContext,
    contest_id: str,
    sport: Sport,
) -> int:
    """Fetch player pool for a contest.

    Args:
        context: Job context
        contest_id: Contest ID
        sport: Sport

    Returns:
        Number of players fetched
    """
    logger.info(f"Running job: fetch_player_pool for contest {contest_id}")

    try:
        from ..yahoo.players import PlayerPoolFetcher

        driver = context.get_driver()
        fetcher = PlayerPoolFetcher()

        players = fetcher.fetch_player_pool(driver, contest_id, sport)
        logger.info(f"Fetched {len(players)} players for contest {contest_id}")

        return len(players)

    except Exception as e:
        logger.error(f"Job fetch_player_pool failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "fetch_player_pool", "contest_id": contest_id},
        )
        return 0


def job_fetch_projections(
    context: JobContext,
    sport: Sport,
    contest_id: str,
) -> int:
    """Fetch and merge projections for a contest.

    Args:
        context: Job context
        sport: Sport
        contest_id: Contest ID

    Returns:
        Number of players with projections
    """
    logger.info(f"Running job: fetch_projections for {sport.value}")

    try:
        from ..projections.aggregator import ProjectionAggregator
        from ..yahoo.players import PlayerPoolFetcher

        # Get player pool
        fetcher = PlayerPoolFetcher()
        players = fetcher.get_player_pool_from_db(contest_id)

        if not players:
            logger.warning(f"No player pool found for contest {contest_id}")
            return 0

        # Fetch and merge projections
        aggregator = ProjectionAggregator()
        players_with_proj = aggregator.get_projections_for_contest(sport, players)

        # Count players with projections
        with_proj = sum(1 for p in players_with_proj if p.projected_points and p.projected_points > 0)
        logger.info(f"Fetched projections for {with_proj}/{len(players)} players")

        return with_proj

    except Exception as e:
        logger.error(f"Job fetch_projections failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "fetch_projections", "sport": sport.value},
        )
        return 0


def job_generate_lineups(
    context: JobContext,
    sport: Sport,
    contest_id: str,
    num_lineups: Optional[int] = None,
) -> int:
    """Generate optimized lineups for a contest.

    Args:
        context: Job context
        sport: Sport
        contest_id: Contest ID
        num_lineups: Number of lineups (default: max entries)

    Returns:
        Number of lineups generated
    """
    logger.info(f"Running job: generate_lineups for contest {contest_id}")

    try:
        from ..optimizer.builder import LineupBuilder
        from ..yahoo.players import PlayerPoolFetcher
        from ..projections.aggregator import ProjectionAggregator

        # Get player pool
        fetcher = PlayerPoolFetcher()
        players = fetcher.get_player_pool_from_db(contest_id)

        if not players:
            logger.warning(f"No player pool found for contest {contest_id}")
            return 0

        # Get projections
        aggregator = ProjectionAggregator()
        players = aggregator.get_projections_for_contest(sport, players)

        # Build lineups
        builder = LineupBuilder(sport)

        if num_lineups:
            lineups = builder.build_lineups(players, num_lineups, contest_id)
        else:
            lineups = builder.build_lineups_for_contest(players, contest_id)

        logger.info(f"Generated {len(lineups)} lineups for contest {contest_id}")

        return len(lineups)

    except Exception as e:
        logger.error(f"Job generate_lineups failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "generate_lineups", "contest_id": contest_id},
        )
        return 0


def job_submit_lineups(
    context: JobContext,
    contest_id: str,
    sport_name: str,
    contest_name: str,
) -> tuple[int, int]:
    """Submit generated lineups to Yahoo.

    Args:
        context: Job context
        contest_id: Contest ID
        sport_name: Sport name
        contest_name: Contest name

    Returns:
        Tuple of (successful, failed) counts
    """
    logger.info(f"Running job: submit_lineups for contest {contest_id}")

    try:
        from ..yahoo.submission import LineupSubmitter
        from ..lineup_manager.tracker import LineupTracker
        from ..common.models import LineupStatus

        driver = context.get_driver()
        tracker = LineupTracker()
        submitter = LineupSubmitter()

        # Get pending lineups
        lineups = tracker.get_lineups_for_contest(contest_id, status=LineupStatus.GENERATED)

        if not lineups:
            logger.info(f"No pending lineups for contest {contest_id}")
            return 0, 0

        # Submit
        successful, failed = submitter.submit_lineups(
            driver=driver,
            lineups=lineups,
            contest_id=contest_id,
            sport_name=sport_name,
            contest_name=contest_name,
        )

        logger.info(f"Submitted {successful} lineups, {failed} failed")

        return successful, failed

    except Exception as e:
        logger.error(f"Job submit_lineups failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "submit_lineups", "contest_id": contest_id},
        )
        return 0, 0


def job_check_late_swaps(context: JobContext, sport: Sport) -> int:
    """Check for and execute late swaps.

    Args:
        context: Job context
        sport: Sport to check

    Returns:
        Number of swaps executed
    """
    logger.info(f"Running job: check_late_swaps for {sport.value}")

    try:
        from ..lineup_manager.swap_executor import SwapExecutor
        from ..yahoo.players import PlayerPoolFetcher
        from ..lineup_manager.tracker import LineupTracker

        driver = context.get_driver()
        tracker = LineupTracker()
        executor = SwapExecutor()

        # Get active contests
        active_contests = tracker.get_active_contests(sport)

        if not active_contests:
            logger.info(f"No active {sport.value} contests")
            return 0

        total_swaps = 0

        for contest in active_contests:
            contest_id = contest["id"]

            # Get player pool
            fetcher = PlayerPoolFetcher()
            players = fetcher.get_player_pool_from_db(contest_id)

            if not players:
                continue

            # Check and execute swaps
            results = executor.check_and_execute_swaps(driver, sport, players)

            if contest_id in results:
                successful = sum(1 for r in results[contest_id] if r.success)
                total_swaps += successful

        logger.info(f"Executed {total_swaps} late swaps for {sport.value}")
        return total_swaps

    except Exception as e:
        logger.error(f"Job check_late_swaps failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "check_late_swaps", "sport": sport.value},
        )
        return 0


def job_fetch_results(context: JobContext, sport: Optional[Sport] = None) -> int:
    """Fetch results for completed contests.

    Args:
        context: Job context
        sport: Optional sport filter

    Returns:
        Number of results fetched
    """
    logger.info(f"Running job: fetch_results")

    try:
        from ..yahoo.results import ResultsFetcher
        from ..common.models import ContestStatus

        driver = context.get_driver()
        fetcher = ResultsFetcher()

        # Get completed contests from database
        session = context.db.get_session()
        try:
            query = session.query(ContestDB).filter(
                ContestDB.status == ContestStatus.COMPLETED.value
            )
            if sport:
                query = query.filter(ContestDB.sport == sport.value)

            # Or get from Yahoo
            contest_ids = fetcher.get_completed_contests(driver)

            total_results = 0
            for contest_id in contest_ids:
                results = fetcher.fetch_contest_results(driver, contest_id)
                total_results += len(results)

            logger.info(f"Fetched {total_results} results")
            return total_results

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Job fetch_results failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "fetch_results"},
        )
        return 0


def job_edit_lineups(
    context: JobContext,
    contest_id: str,
    sport: Sport,
) -> dict:
    """Edit existing lineups to replace injured players.

    This job should be run after initial submission to:
    1. Re-fetch player pool with updated injury status
    2. Generate new lineups excluding injured players
    3. Edit the existing entries on Yahoo

    Args:
        context: Job context
        contest_id: Contest ID
        sport: Sport

    Returns:
        Dict with success status and edit count
    """
    logger.info(f"Running job: edit_lineups for contest {contest_id}")

    try:
        from ..yahoo.editor import LineupEditor
        from ..yahoo.players import PlayerPoolFetcher
        from ..optimizer.builder import LineupBuilder
        from ..projections.aggregator import ProjectionAggregator

        driver = context.get_driver()

        # Step 1: Re-fetch player pool to get latest injury status
        fetcher = PlayerPoolFetcher()
        players = fetcher.fetch_player_pool(contest_id, sport, save_to_db=True)

        if not players:
            logger.warning(f"No players found for contest {contest_id}")
            return {"success": False, "message": "No players found", "edited_count": 0}

        # Count injured players
        injured_count = sum(1 for p in players if p.injury_status in {"INJ", "O"})
        logger.info(f"Found {injured_count} injured/out players in pool of {len(players)}")

        if injured_count == 0:
            logger.info("No injured players - no edits needed")
            return {"success": True, "message": "No injured players", "edited_count": 0}

        # Step 2: Get projections for healthy players
        aggregator = ProjectionAggregator()
        players_with_proj = aggregator.get_projections_for_contest(sport, players)

        # Step 3: Generate new lineups (LineupBuilder already filters injured)
        builder = LineupBuilder(sport)
        # Get max entries for this contest
        session = context.db.get_session()
        try:
            contest = session.query(ContestDB).filter_by(id=contest_id).first()
            max_entries = contest.max_entries if contest else 150
        finally:
            session.close()

        lineups = builder.build_lineups(
            players=players_with_proj,
            num_lineups=max_entries,
            contest_id=contest_id,
            save_to_db=False,  # Don't save - we're editing existing entries
        )

        if not lineups:
            logger.error("Failed to generate replacement lineups")
            return {"success": False, "message": "Failed to generate lineups", "edited_count": 0}

        logger.info(f"Generated {len(lineups)} healthy lineups for editing")

        # Step 4: Edit existing entries with new lineups
        editor = LineupEditor()
        result = editor.edit_lineups_for_contest(
            driver=driver,
            contest_id=contest_id,
            lineups=lineups,
            sport=sport.value.lower(),
        )

        if result["success"]:
            logger.info(f"Successfully edited {result['edited_count']} lineups")
            context.notifier.notify_success(
                title=f"{sport.value} Lineup Edits Complete",
                message=f"Edited {result['edited_count']} lineups for contest {contest_id}",
            )
        else:
            logger.error(f"Edit failed: {result['message']}")
            context.notifier.notify_error(
                error_type="EditError",
                error_message=result["message"],
                context={"contest_id": contest_id, "sport": sport.value},
            )

        return result

    except Exception as e:
        logger.error(f"Job edit_lineups failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "edit_lineups", "contest_id": contest_id},
        )
        return {"success": False, "message": str(e), "edited_count": 0}


def job_send_daily_report(context: JobContext) -> bool:
    """Send daily performance report via email.

    Args:
        context: Job context

    Returns:
        True if sent successfully
    """
    logger.info("Running job: send_daily_report")

    try:
        from ..monitoring.reports import ReportGenerator

        generator = ReportGenerator()
        report = generator.generate_daily_summary()

        # Send via email (simplified - would need HTML formatting)
        # For now, just log it
        logger.info(f"Daily Report:\n{report}")

        return True

    except Exception as e:
        logger.error(f"Job send_daily_report failed: {e}")
        return False
