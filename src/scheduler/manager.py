"""Main scheduler manager - orchestrates all automated jobs."""

import logging
import signal
import sys
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .alerts import get_alerter, AlertSeverity
from .jobs.contest_sync import ContestSyncJob
from .jobs.projection_sync import ProjectionSyncJob
from .jobs.submission import SubmissionJob
from .jobs.injury_monitor import InjuryMonitorJob

logger = logging.getLogger(__name__)


class DFSSchedulerManager:
    """Main scheduler for Daily Fantasy automation.

    Manages all scheduled jobs:
    - Contest sync: Fetch and filter eligible contests
    - Projection sync: Fetch projections with dynamic intervals
    - Submission: Monitor fill rates and submit lineups
    - Injury monitor: Check for OUT players and swap

    Usage:
        manager = DFSSchedulerManager(dry_run=True)
        manager.start()

        # Or run interactively
        manager.run_forever()
    """

    def __init__(self, dry_run: bool = False, sports: Optional[list[str]] = None):
        """Initialize scheduler manager.

        Args:
            dry_run: If True, simulate all actions without executing
            sports: List of sports to track. Defaults to ['nfl'].
        """
        self.dry_run = dry_run
        self.sports = sports or ["nfl"]
        self.scheduler = BackgroundScheduler()
        self.alerter = get_alerter()
        self._running = False

        # Initialize jobs
        self.contest_sync_job = ContestSyncJob(dry_run=dry_run)
        self.projection_sync_job = ProjectionSyncJob(dry_run=dry_run)
        self.submission_job = SubmissionJob(dry_run=dry_run)
        self.injury_monitor_job = InjuryMonitorJob(dry_run=dry_run)

        logger.info(
            f"DFSSchedulerManager initialized "
            f"(dry_run={dry_run}, sports={sports})"
        )

    def start(self) -> None:
        """Start the scheduler with all jobs."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        logger.info("Starting DFS Scheduler...")

        # Add jobs
        self._schedule_contest_sync()
        self._schedule_monitoring_jobs()

        # Start scheduler
        self.scheduler.start()
        self._running = True

        # Run initial contest sync
        logger.info("Running initial contest sync...")
        for sport in self.sports:
            try:
                self.contest_sync_job.run(sport=sport)
            except Exception as e:
                logger.error(f"Initial contest sync failed for {sport}: {e}")

        logger.info("Scheduler started successfully")

    def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return

        logger.info("Stopping DFS Scheduler...")
        self.scheduler.shutdown(wait=False)
        self._running = False
        logger.info("Scheduler stopped")

    def run_forever(self) -> None:
        """Start scheduler and run until interrupted.

        Handles SIGINT (Ctrl+C) and SIGTERM gracefully.
        """
        # Set up signal handlers
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start scheduler
        self.start()

        # Keep main thread alive
        logger.info("Scheduler running. Press Ctrl+C to stop.")
        try:
            while self._running:
                import time
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def _schedule_contest_sync(self) -> None:
        """Schedule contest sync jobs for each sport."""
        # NFL contest sync schedule
        # Tuesday 10am - new week's contests
        # Thursday 5pm - TNF contests
        # Sunday 9am, 5pm - main slate and SNF
        # Monday 5pm - MNF

        if "nfl" in self.sports:
            # Tuesday 10am
            self.scheduler.add_job(
                self._run_contest_sync,
                CronTrigger(day_of_week="tue", hour=10, minute=0),
                args=["nfl"],
                id="contest_sync_nfl_tue",
                name="NFL Contest Sync (Tuesday)",
            )

            # Thursday 5pm
            self.scheduler.add_job(
                self._run_contest_sync,
                CronTrigger(day_of_week="thu", hour=17, minute=0),
                args=["nfl"],
                id="contest_sync_nfl_thu",
                name="NFL Contest Sync (Thursday)",
            )

            # Sunday 9am and 5pm
            self.scheduler.add_job(
                self._run_contest_sync,
                CronTrigger(day_of_week="sun", hour=9, minute=0),
                args=["nfl"],
                id="contest_sync_nfl_sun_am",
                name="NFL Contest Sync (Sunday AM)",
            )
            self.scheduler.add_job(
                self._run_contest_sync,
                CronTrigger(day_of_week="sun", hour=17, minute=0),
                args=["nfl"],
                id="contest_sync_nfl_sun_pm",
                name="NFL Contest Sync (Sunday PM)",
            )

            # Monday 5pm
            self.scheduler.add_job(
                self._run_contest_sync,
                CronTrigger(day_of_week="mon", hour=17, minute=0),
                args=["nfl"],
                id="contest_sync_nfl_mon",
                name="NFL Contest Sync (Monday)",
            )

        # Add other sports here as needed
        # NBA, NHL: daily sync
        if "nba" in self.sports or "nhl" in self.sports:
            for sport in ["nba", "nhl"]:
                if sport in self.sports:
                    self.scheduler.add_job(
                        self._run_contest_sync,
                        CronTrigger(hour=10, minute=0),  # Daily at 10am
                        args=[sport],
                        id=f"contest_sync_{sport}",
                        name=f"{sport.upper()} Contest Sync (Daily)",
                    )

    def _schedule_monitoring_jobs(self) -> None:
        """Schedule monitoring jobs (fill rate, injuries, projections)."""
        # Projection sync - every 5 minutes (job internally decides if refresh needed)
        self.scheduler.add_job(
            self._run_projection_sync,
            IntervalTrigger(minutes=5),
            id="projection_sync",
            name="Projection Sync",
        )

        # Fill rate check / submission - every 10 minutes
        self.scheduler.add_job(
            self._run_submission_check,
            IntervalTrigger(minutes=10),
            id="submission_check",
            name="Submission Check",
        )

        # Injury monitor - every 10 minutes
        self.scheduler.add_job(
            self._run_injury_check,
            IntervalTrigger(minutes=10),
            id="injury_monitor",
            name="Injury Monitor",
        )

    def _run_contest_sync(self, sport: str) -> None:
        """Run contest sync job with error handling."""
        try:
            result = self.contest_sync_job.run(sport=sport)
            logger.info(f"Contest sync completed: {result}")

        except Exception as e:
            logger.error(f"Contest sync failed: {e}")
            self.alerter.alert_scheduler_error("contest_sync", str(e))

    def _run_projection_sync(self) -> None:
        """Run projection sync for all sports."""
        for sport in self.sports:
            try:
                result = self.projection_sync_job.run(sport=sport)
                if result.get("refreshed"):
                    logger.info(f"Projection sync completed for {sport}: {result}")

            except Exception as e:
                logger.error(f"Projection sync failed for {sport}: {e}")
                self.alerter.alert_scheduler_error("projection_sync", str(e))

    def _run_submission_check(self) -> None:
        """Run submission check for all sports."""
        for sport in self.sports:
            try:
                result = self.submission_job.run(sport=sport)
                if result.get("contests_submitted", 0) > 0:
                    logger.info(f"Submission completed for {sport}: {result}")

            except Exception as e:
                logger.error(f"Submission check failed for {sport}: {e}")
                self.alerter.alert_scheduler_error("submission", str(e))

    def _run_injury_check(self) -> None:
        """Run injury check for all sports."""
        for sport in self.sports:
            try:
                result = self.injury_monitor_job.run(sport=sport)
                if result.get("total_swaps", 0) > 0:
                    logger.info(f"Injury check completed for {sport}: {result}")

            except Exception as e:
                logger.error(f"Injury check failed for {sport}: {e}")
                self.alerter.alert_scheduler_error("injury_monitor", str(e))

    # Manual job execution methods

    def run_contest_sync(self, sport: Optional[str] = None) -> dict:
        """Manually run contest sync.

        Args:
            sport: Sport to sync. If None, sync all configured sports.

        Returns:
            Dict with results
        """
        sports = [sport] if sport else self.sports
        results = {}

        for s in sports:
            try:
                results[s] = self.contest_sync_job.run(sport=s)
            except Exception as e:
                results[s] = {"error": str(e)}

        return results

    def run_projection_sync(self, sport: Optional[str] = None, force: bool = False) -> dict:
        """Manually run projection sync.

        Args:
            sport: Sport to sync. If None, sync all configured sports.
            force: If True, force refresh regardless of interval.

        Returns:
            Dict with results
        """
        sports = [sport] if sport else self.sports
        results = {}

        for s in sports:
            try:
                results[s] = self.projection_sync_job.run(sport=s, force=force)
            except Exception as e:
                results[s] = {"error": str(e)}

        return results

    def run_submission_check(self, sport: Optional[str] = None) -> dict:
        """Manually run submission check.

        Args:
            sport: Sport to check. If None, check all configured sports.

        Returns:
            Dict with results
        """
        sports = [sport] if sport else self.sports
        results = {}

        for s in sports:
            try:
                results[s] = self.submission_job.run(sport=s)
            except Exception as e:
                results[s] = {"error": str(e)}

        return results

    def run_injury_check(self, sport: Optional[str] = None) -> dict:
        """Manually run injury check.

        Args:
            sport: Sport to check. If None, check all configured sports.

        Returns:
            Dict with results
        """
        sports = [sport] if sport else self.sports
        results = {}

        for s in sports:
            try:
                results[s] = self.injury_monitor_job.run(sport=s)
            except Exception as e:
                results[s] = {"error": str(e)}

        return results

    def get_status(self) -> dict:
        """Get current scheduler status.

        Returns:
            Dict with scheduler status and job info
        """
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            })

        return {
            "running": self._running,
            "dry_run": self.dry_run,
            "sports": self.sports,
            "jobs": jobs,
            "timestamp": datetime.utcnow().isoformat(),
        }
