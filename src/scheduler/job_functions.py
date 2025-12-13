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


def job_refresh_projections_dynamic(context: JobContext, sport: Sport) -> int:
    """Refresh projections for contests based on time-to-lock.

    Uses tiered refresh intervals:
    - > 24 hours to lock: every 6 hours (default_interval)
    - 6-24 hours to lock: every 2 hours (day_of_interval)
    - 1-6 hours to lock: every 30 min (approaching_interval)
    - < 1 hour to lock: every 10 min (imminent_interval)

    Args:
        context: Job context
        sport: Sport to refresh projections for

    Returns:
        Number of contests refreshed
    """
    logger.info(f"Running job: refresh_projections_dynamic for {sport.value}")

    try:
        from datetime import timedelta

        # Get contests from database
        session = context.db.get_session()
        try:
            now = datetime.now()
            contests = (
                session.query(ContestDB)
                .filter(ContestDB.sport == sport.value)
                .filter(ContestDB.slate_start > now)
                .all()
            )

            if not contests:
                logger.info(f"No upcoming {sport.value} contests")
                return 0

            refreshed_count = 0

            for contest in contests:
                time_to_lock = contest.slate_start - now
                hours_to_lock = time_to_lock.total_seconds() / 3600

                # Determine if we should refresh based on time-to-lock tier
                should_refresh = _should_refresh_projections(
                    contest.id, hours_to_lock, context.config.scheduler
                )

                if should_refresh:
                    logger.info(
                        f"Refreshing projections for contest {contest.id} "
                        f"({hours_to_lock:.1f}h to lock)"
                    )

                    # Fetch fresh projections (call local function directly)
                    job_fetch_projections(context, sport, contest.id)
                    refreshed_count += 1

            logger.info(f"Refreshed projections for {refreshed_count} contests")
            return refreshed_count

        finally:
            session.close()

    except Exception as e:
        logger.error(f"Job refresh_projections_dynamic failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "refresh_projections_dynamic", "sport": sport.value},
        )
        return 0


# Cache to track last refresh time per contest
_last_projection_refresh: dict[str, datetime] = {}


def _should_refresh_projections(contest_id: str, hours_to_lock: float, scheduler_config) -> bool:
    """Determine if projections should be refreshed based on time-to-lock tier.

    Uses tiered refresh intervals - refreshes more frequently as lock time approaches.
    Tracks last refresh time per contest to avoid redundant refreshes.

    Args:
        contest_id: Contest ID
        hours_to_lock: Hours until contest lock
        scheduler_config: Scheduler configuration

    Returns:
        True if projections should be refreshed
    """
    from datetime import timedelta

    now = datetime.now()

    # Get refresh intervals from config (in minutes)
    projection_config = getattr(scheduler_config, 'projection_refresh', {})
    imminent_interval = projection_config.get('imminent_interval', 10)  # < 1 hour
    approaching_interval = projection_config.get('approaching_interval', 30)  # 1-6 hours
    day_of_interval = projection_config.get('day_of_interval', 120)  # 6-24 hours
    default_interval = projection_config.get('default_interval', 360)  # > 24 hours

    # Determine required interval based on time-to-lock tier
    if hours_to_lock <= 1:
        required_interval = timedelta(minutes=imminent_interval)
        tier = "imminent"
    elif hours_to_lock <= 6:
        required_interval = timedelta(minutes=approaching_interval)
        tier = "approaching"
    elif hours_to_lock <= 24:
        required_interval = timedelta(minutes=day_of_interval)
        tier = "day_of"
    else:
        required_interval = timedelta(minutes=default_interval)
        tier = "default"

    # Check if we've refreshed recently
    last_refresh = _last_projection_refresh.get(contest_id)
    if last_refresh is None:
        # Never refreshed - do it now
        _last_projection_refresh[contest_id] = now
        logger.debug(f"Contest {contest_id}: First refresh ({tier} tier)")
        return True

    time_since_refresh = now - last_refresh
    if time_since_refresh >= required_interval:
        # Enough time has passed - refresh
        _last_projection_refresh[contest_id] = now
        logger.debug(
            f"Contest {contest_id}: Refreshing after {time_since_refresh.total_seconds()/60:.0f} min "
            f"({tier} tier, interval={required_interval.total_seconds()/60:.0f} min)"
        )
        return True

    # Not time yet
    logger.debug(
        f"Contest {contest_id}: Skipping refresh, {time_since_refresh.total_seconds()/60:.0f} min "
        f"since last ({tier} tier, need {required_interval.total_seconds()/60:.0f} min)"
    )
    return False


def job_check_injuries(context: JobContext, sport: Sport) -> int:
    """Check for injured players and trigger lineup edits.

    This job monitors player injury statuses and:
    1. Refreshes player pool injury data from Yahoo API
    2. Finds OUT/INJ players in submitted lineups
    3. Swaps them with best available replacements
    4. Re-uploads edited lineups to Yahoo

    Args:
        context: Job context
        sport: Sport to check

    Returns:
        Number of swaps performed
    """
    logger.info(f"Running job: check_injuries for {sport.value}")

    try:
        from ..scheduler.jobs.injury_monitor import InjuryMonitorJob
        from ..scheduler.fill_monitor import FillMonitorConfig

        # Get config from settings
        scheduler_cfg = context.config.scheduler

        # Create fill config for edit window checking
        fill_config = FillMonitorConfig(
            fill_rate_threshold=scheduler_cfg.fill_rate_threshold,
            time_before_lock_minutes=int(scheduler_cfg.submit_lineups_hours_before * 60),
            stop_editing_minutes=scheduler_cfg.stop_editing_minutes,
        )

        # Run injury monitor job
        job = InjuryMonitorJob(dry_run=False, fill_config=fill_config)
        result = job.execute(sport=sport.value.lower())

        total_swaps = result.get("total_swaps", 0)
        contests_checked = result.get("contests_checked", 0)

        logger.info(
            f"Injury check complete: {contests_checked} contests checked, "
            f"{total_swaps} swaps performed"
        )

        if total_swaps > 0:
            context.notifier.notify_success(
                title=f"{sport.value} Injury Swaps Complete",
                message=f"Made {total_swaps} player swaps across {contests_checked} contests",
            )

        return total_swaps

    except Exception as e:
        logger.error(f"Job check_injuries failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "check_injuries", "sport": sport.value},
        )
        return 0


def job_check_fill_rates(context: JobContext, sport: Sport) -> int:
    """Check contest fill rates and submit lineups when thresholds are met.

    This job monitors contest fill rates and triggers submission when:
    1. Fill rate >= threshold (e.g., 70%)
    2. Time to lock < threshold (e.g., 2 hours)

    Args:
        context: Job context
        sport: Sport to check

    Returns:
        Number of contests submitted
    """
    logger.info(f"Running job: check_fill_rates for {sport.value}")

    try:
        from ..scheduler.fill_monitor import FillMonitor, FillMonitorConfig
        from ..yahoo.contests import ContestFetcher

        # Get config from settings
        config = context.config
        scheduler_cfg = config.scheduler

        # Create fill monitor with config values
        monitor_config = FillMonitorConfig(
            fill_rate_threshold=getattr(scheduler_cfg, 'fill_rate_threshold', 0.70),
            time_before_lock_minutes=int(scheduler_cfg.submit_lineups_hours_before * 60),
            stop_editing_minutes=scheduler_cfg.stop_editing_minutes,
        )
        monitor = FillMonitor(monitor_config)

        # Fetch fresh contest data from Yahoo
        driver = context.get_driver()
        fetcher = ContestFetcher()
        contests = fetcher.fetch_contests(driver, sport)

        if not contests:
            logger.info(f"No {sport.value} contests found")
            return 0

        # Check which contests are ready to submit
        to_submit = monitor.get_contests_to_submit(
            [c.__dict__ if hasattr(c, '__dict__') else c for c in contests],
            sport=sport.value,
        )

        submitted_count = 0

        for contest, entry_record, status in to_submit:
            contest_id = status.contest_id
            logger.info(f"Submitting contest {contest_id}: {status.reason}")

            try:
                # Generate lineups and submit (call local functions directly)

                # Generate fresh lineups
                job_generate_lineups(context, sport, contest_id)

                # Submit
                contest_name = contest.get("name", f"{sport.value} Contest")
                successful, failed = job_submit_lineups(
                    context, contest_id, sport.value, contest_name
                )

                if successful > 0:
                    # Mark as submitted in fill monitor
                    monitor.mark_submitted(contest_id, successful, status.fill_rate)
                    submitted_count += 1

                    context.notifier.notify_success(
                        title=f"{sport.value} Contest Submitted",
                        message=f"Submitted {successful} lineups to {contest_name} (fill rate: {status.fill_rate:.1%})",
                    )

            except Exception as e:
                logger.error(f"Failed to submit contest {contest_id}: {e}")
                context.notifier.notify_error(
                    error_type="SubmissionError",
                    error_message=str(e),
                    context={"contest_id": contest_id, "sport": sport.value},
                )

        # Update any contests that have locked
        monitor.update_locked_contests()

        logger.info(f"Fill rate check complete: {submitted_count} contests submitted")
        return submitted_count

    except Exception as e:
        logger.error(f"Job check_fill_rates failed: {e}")
        context.notifier.notify_error(
            error_type="JobError",
            error_message=str(e),
            context={"job": "check_fill_rates", "sport": sport.value},
        )
        return 0
