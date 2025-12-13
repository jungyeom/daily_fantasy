"""APScheduler runner for automated job execution."""
import logging
from datetime import datetime, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from ..common.config import get_config
from ..common.database import get_database, ContestDB
from ..common.models import Sport
from .jobs import JobContext

logger = logging.getLogger(__name__)


class AutomationRunner:
    """Runs automated jobs on schedule."""

    def __init__(self):
        """Initialize automation runner."""
        self.config = get_config()
        self.db = get_database()
        self.scheduler = BackgroundScheduler()
        self.context = JobContext()
        self._running = False

    def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self.scheduler.start()
        self._running = True
        logger.info("Scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return

        self.scheduler.shutdown()
        self.context.close_driver()
        self._running = False
        logger.info("Scheduler stopped")

    def schedule_contest_pipeline(
        self,
        contest_id: str,
        sport: Sport,
        contest_name: str,
        slate_start: datetime,
    ) -> None:
        """Schedule the full pipeline for a contest.

        Schedules:
        - Fetch player pool (4 hours before)
        - Fetch projections (3 hours before)
        - Generate lineups (2.5 hours before)
        - Submit lineups (2 hours before)
        - Edit lineups (30 min before) - replace injured players
        - Late swap checks (every 15 min until lock)

        Args:
            contest_id: Contest ID
            sport: Sport
            contest_name: Contest name
            slate_start: Slate lock time
        """
        scheduler_config = self.config.scheduler
        now = datetime.now()

        # Calculate job times
        fetch_pool_time = slate_start - timedelta(hours=scheduler_config.fetch_contests_hours_before)
        fetch_proj_time = slate_start - timedelta(hours=scheduler_config.generate_lineups_hours_before)
        generate_time = slate_start - timedelta(hours=scheduler_config.generate_lineups_hours_before - 0.5)
        submit_time = slate_start - timedelta(hours=scheduler_config.submit_lineups_hours_before)

        # Edit lineups 30 min before lock to replace any injured players
        edit_minutes_before = getattr(scheduler_config, 'edit_lineups_minutes_before', 30)
        edit_time = slate_start - timedelta(minutes=edit_minutes_before)

        # Stop editing 5 min before lock (from config or default)
        stop_editing_minutes = getattr(scheduler_config, 'stop_editing_minutes', 5)

        job_prefix = f"{sport.value}_{contest_id}"

        # Schedule fetch player pool
        if fetch_pool_time > now:
            self.scheduler.add_job(
                self._run_fetch_player_pool,
                trigger=DateTrigger(run_date=fetch_pool_time),
                args=[contest_id, sport],
                id=f"{job_prefix}_fetch_pool",
                replace_existing=True,
            )
            logger.info(f"Scheduled fetch_player_pool at {fetch_pool_time}")

        # Schedule fetch projections
        if fetch_proj_time > now:
            self.scheduler.add_job(
                self._run_fetch_projections,
                trigger=DateTrigger(run_date=fetch_proj_time),
                args=[sport, contest_id],
                id=f"{job_prefix}_fetch_proj",
                replace_existing=True,
            )
            logger.info(f"Scheduled fetch_projections at {fetch_proj_time}")

        # Schedule generate lineups
        if generate_time > now:
            self.scheduler.add_job(
                self._run_generate_lineups,
                trigger=DateTrigger(run_date=generate_time),
                args=[sport, contest_id],
                id=f"{job_prefix}_generate",
                replace_existing=True,
            )
            logger.info(f"Scheduled generate_lineups at {generate_time}")

        # Schedule submit lineups
        if submit_time > now:
            self.scheduler.add_job(
                self._run_submit_lineups,
                trigger=DateTrigger(run_date=submit_time),
                args=[contest_id, sport.value, contest_name],
                id=f"{job_prefix}_submit",
                replace_existing=True,
            )
            logger.info(f"Scheduled submit_lineups at {submit_time}")

        # Schedule edit lineups (replace injured players) 30 min before lock
        if edit_time > now and edit_time > submit_time:
            self.scheduler.add_job(
                self._run_edit_lineups,
                trigger=DateTrigger(run_date=edit_time),
                args=[contest_id, sport],
                id=f"{job_prefix}_edit",
                replace_existing=True,
            )
            logger.info(f"Scheduled edit_lineups at {edit_time}")

        # Schedule late swap checks
        if submit_time < slate_start:
            self.scheduler.add_job(
                self._run_late_swap_check,
                trigger=IntervalTrigger(
                    minutes=scheduler_config.late_swap_check_interval_minutes,
                    start_date=submit_time + timedelta(minutes=30),
                    end_date=slate_start - timedelta(minutes=stop_editing_minutes),
                ),
                args=[sport],
                id=f"{job_prefix}_late_swap",
                replace_existing=True,
            )
            logger.info(f"Scheduled late_swap checks every {scheduler_config.late_swap_check_interval_minutes} min")

    def schedule_daily_jobs(self) -> None:
        """Schedule recurring daily jobs.

        Note on NFL scheduling:
        - NFL games occur on Sun (main slate), Mon (MNF), Thu (TNF)
        - Occasionally Fri/Sat games during holidays
        - We fetch contests daily for all sports to catch all slates
        """
        # Daily contest fetch and pipeline scheduling (morning)
        # Run for all sports every day to catch all possible slates
        for sport in [Sport.NFL, Sport.NBA, Sport.MLB, Sport.NHL]:
            self.scheduler.add_job(
                self._run_fetch_and_schedule,
                trigger=CronTrigger(hour=8, minute=0),
                args=[sport],
                id=f"daily_pipeline_{sport.value}",
                replace_existing=True,
            )
            logger.info(f"Scheduled daily pipeline for {sport.value} at 8:00 AM")

        # Fill rate monitoring - check every 10 minutes for all sports
        # This will trigger submission when fill rate >= 70% or time threshold is met
        fill_rate_interval = getattr(self.config.scheduler, 'fill_rate_check_interval', 10)
        for sport in [Sport.NFL, Sport.NBA, Sport.MLB, Sport.NHL]:
            self.scheduler.add_job(
                self._run_check_fill_rates,
                trigger=IntervalTrigger(minutes=fill_rate_interval),
                args=[sport],
                id=f"fill_rate_check_{sport.value}",
                replace_existing=True,
            )
        logger.info(f"Scheduled fill rate checks every {fill_rate_interval} minutes")

        # Injury monitoring - check every 10 minutes for all sports
        # This will swap OUT/INJ players and re-upload edited lineups
        injury_check_interval = getattr(self.config.scheduler, 'injury_check_interval', 10)
        for sport in [Sport.NFL, Sport.NBA, Sport.MLB, Sport.NHL]:
            self.scheduler.add_job(
                self._run_check_injuries,
                trigger=IntervalTrigger(minutes=injury_check_interval),
                args=[sport],
                id=f"injury_check_{sport.value}",
                replace_existing=True,
            )
        logger.info(f"Scheduled injury checks every {injury_check_interval} minutes")

        # Daily results fetch (evening)
        self.scheduler.add_job(
            self._run_fetch_results,
            trigger=CronTrigger(hour=23, minute=0),
            id="daily_fetch_results",
            replace_existing=True,
        )

        # Daily report
        self.scheduler.add_job(
            self._run_daily_report,
            trigger=CronTrigger(hour=23, minute=30),
            id="daily_report",
            replace_existing=True,
        )

        logger.info("Scheduled daily jobs for all sports")

    def schedule_sport_day(self, sport: Sport, day_of_week: str) -> None:
        """Schedule full pipeline for a sport on a specific day.

        Note: This is largely superseded by schedule_daily_jobs() which runs
        daily for all sports. Use this only if you need sport-specific scheduling
        on particular days.

        Args:
            sport: Sport
            day_of_week: Day name (e.g., 'sunday', 'monday', 'thursday')
        """
        day_map = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }

        day_num = day_map.get(day_of_week.lower())
        if day_num is None:
            logger.error(f"Invalid day: {day_of_week}")
            return

        # Morning: fetch contests
        self.scheduler.add_job(
            self._run_fetch_and_schedule,
            trigger=CronTrigger(day_of_week=day_num, hour=9, minute=0),
            args=[sport],
            id=f"{sport.value}_{day_of_week}_pipeline",
            replace_existing=True,
        )

        logger.info(f"Scheduled {sport.value} pipeline for {day_of_week}s at 9:00 AM")

    # Job wrapper methods
    def _run_fetch_contests(self, sport: Sport) -> None:
        """Run fetch contests job."""
        from .jobs import job_fetch_contests
        job_fetch_contests(self.context, sport)

    def _run_fetch_player_pool(self, contest_id: str, sport: Sport) -> None:
        """Run fetch player pool job."""
        from .jobs import job_fetch_player_pool
        job_fetch_player_pool(self.context, contest_id, sport)

    def _run_fetch_projections(self, sport: Sport, contest_id: str) -> None:
        """Run fetch projections job."""
        from .jobs import job_fetch_projections
        job_fetch_projections(self.context, sport, contest_id)

    def _run_generate_lineups(self, sport: Sport, contest_id: str) -> None:
        """Run generate lineups job."""
        from .jobs import job_generate_lineups
        job_generate_lineups(self.context, sport, contest_id)

    def _run_submit_lineups(self, contest_id: str, sport_name: str, contest_name: str) -> None:
        """Run submit lineups job."""
        from .jobs import job_submit_lineups
        job_submit_lineups(self.context, contest_id, sport_name, contest_name)

    def _run_edit_lineups(self, contest_id: str, sport: Sport) -> None:
        """Run edit lineups job to replace injured players."""
        from .jobs import job_edit_lineups
        job_edit_lineups(self.context, contest_id, sport)

    def _run_check_fill_rates(self, sport: Sport) -> None:
        """Run fill rate check job."""
        from .jobs import job_check_fill_rates
        job_check_fill_rates(self.context, sport)

    def _run_check_injuries(self, sport: Sport) -> None:
        """Run injury check job."""
        from .jobs import job_check_injuries
        job_check_injuries(self.context, sport)

    def _run_late_swap_check(self, sport: Sport) -> None:
        """Run late swap check job."""
        from .jobs import job_check_late_swaps
        job_check_late_swaps(self.context, sport)

    def _run_fetch_results(self) -> None:
        """Run fetch results job."""
        from .jobs import job_fetch_results
        job_fetch_results(self.context)

    def _run_daily_report(self) -> None:
        """Run daily report job."""
        from .jobs import job_send_daily_report
        job_send_daily_report(self.context)

    def _run_fetch_and_schedule(self, sport: Sport) -> None:
        """Fetch contests and schedule pipelines for each."""
        from .jobs import job_fetch_contests

        # Fetch contests
        job_fetch_contests(self.context, sport)

        # Get contests from database
        session = self.db.get_session()
        try:
            contests = (
                session.query(ContestDB)
                .filter(ContestDB.sport == sport.value)
                .filter(ContestDB.slate_start > datetime.now())
                .filter(ContestDB.entry_fee < self.config.contest_filter.max_entry_fee)
                .all()
            )

            for contest in contests:
                self.schedule_contest_pipeline(
                    contest_id=contest.id,
                    sport=sport,
                    contest_name=contest.name,
                    slate_start=contest.slate_start,
                )

        finally:
            session.close()

    def list_scheduled_jobs(self) -> list[dict]:
        """List all scheduled jobs.

        Returns:
            List of job info dicts
        """
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time,
                "trigger": str(job.trigger),
            })
        return jobs


def get_runner() -> AutomationRunner:
    """Get automation runner instance."""
    return AutomationRunner()


def run_full_pipeline(sport: Sport, contest_id: str, contest_name: str, skip_edit: bool = False) -> None:
    """Run full pipeline immediately for a contest.

    Args:
        sport: Sport
        contest_id: Contest ID
        contest_name: Contest name
        skip_edit: If True, skip the edit lineups step
    """
    from .jobs import (
        job_fetch_player_pool,
        job_fetch_projections,
        job_generate_lineups,
        job_submit_lineups,
        job_edit_lineups,
        JobContext,
    )

    context = JobContext()

    try:
        # Run all steps
        logger.info(f"Running full pipeline for {sport.value} contest {contest_id}")

        job_fetch_player_pool(context, contest_id, sport)
        job_fetch_projections(context, sport, contest_id)
        job_generate_lineups(context, sport, contest_id)
        job_submit_lineups(context, contest_id, sport.value, contest_name)

        # Edit lineups to replace any injured players (runs after submission)
        if not skip_edit:
            logger.info("Running edit lineups to replace injured players...")
            job_edit_lineups(context, contest_id, sport)

        logger.info("Pipeline complete")

    finally:
        context.close_driver()
