"""FanDuel contest scheduler and automation.

This module provides automated scheduling for FanDuel DFS:
1. Daily contest scraping (fetches contests from FanDuel API)
2. Contest selection using configured criteria
3. Scheduled lineup generation based on slate lock times
4. Initial lineups: 2 hours before slate lock
5. Final lineups: 30-60 minutes before slate lock (updated projections)

Usage:
    from src.fanduel.scheduler import FanDuelScheduler

    scheduler = FanDuelScheduler()

    # Scrape and schedule lineups for all sports
    scheduler.scrape_and_schedule_all()

    # Or run for specific sport
    scheduler.scrape_and_schedule(Sport.NHL)

    # Start the scheduler daemon
    scheduler.start()

File naming convention for generated lineups:
    data/lineups/fanduel/{SPORT}/{YYYY-MM-DD}/
        {contest_id}_initial_{timestamp}.csv
        {contest_id}_final_{timestamp}.csv
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from ..common.models import Sport
from ..contests.selector import ContestSelector, ContestCriteria
from .api import (
    FanDuelApiClient,
    parse_fixture_list,
    parse_contest,
    SPORT_CODES,
)

logger = logging.getLogger(__name__)

# Default output directory for lineups
DEFAULT_OUTPUT_DIR = Path("data/lineups")

# FanDuel sports to monitor
FANDUEL_SPORTS = [Sport.NFL, Sport.NBA, Sport.MLB, Sport.NHL]


@dataclass
class ScheduledContest:
    """Represents a contest scheduled for lineup generation."""

    contest_id: str
    fixture_list_id: int
    sport: Sport
    name: str
    slate_start: datetime
    max_entries: int
    entry_fee: float
    score: int = 0

    # Tracking
    initial_generated: bool = False
    final_generated: bool = False
    initial_filepath: Optional[str] = None
    final_filepath: Optional[str] = None


@dataclass
class SchedulerConfig:
    """Configuration for FanDuel scheduler."""

    # Daily scraping time (24-hour format)
    daily_scrape_hour: int = 11
    daily_scrape_minute: int = 0

    # Lineup generation timing (relative to slate lock)
    initial_lineup_hours_before: float = 2.0
    final_lineup_minutes_before: int = 30

    # Contest selection criteria
    max_entry_fee: float = 2.0
    min_entries_per_user: int = 50
    min_exposure_ratio: float = 0.02  # 2%

    # Lineup generation settings
    randomness: float = 0.1
    use_vegas_lines: bool = True
    min_game_total: float = 5.0
    vegas_weight: float = 0.5

    # Sports to monitor (empty = all)
    sports: list[Sport] = field(default_factory=lambda: FANDUEL_SPORTS.copy())

    # Output directory
    output_dir: Path = DEFAULT_OUTPUT_DIR


class FanDuelScheduler:
    """Automated scheduler for FanDuel DFS lineup generation.

    Workflow:
    1. Daily at configured time: Scrape contests from FanDuel API
    2. Select eligible contests using ContestSelector criteria
    3. Schedule lineup generation jobs for each contest:
       - Initial lineups: X hours before slate lock
       - Final lineups: Y minutes before slate lock
    4. Handle multiple slates on same day (NFL 1pm + NHL 7pm)
    """

    def __init__(self, config: Optional[SchedulerConfig] = None):
        """Initialize scheduler.

        Args:
            config: Scheduler configuration (uses defaults if None)
        """
        self.config = config or SchedulerConfig()
        self.scheduler = BackgroundScheduler()
        self._scheduled_contests: dict[str, ScheduledContest] = {}
        self._running = False

        # Create contest selector with config criteria
        criteria = ContestCriteria(
            max_entry_fee=self.config.max_entry_fee,
            min_entries_per_user=self.config.min_entries_per_user,
            min_exposure_ratio=self.config.min_exposure_ratio,
        )
        self.selector = ContestSelector(criteria)

    def _get_api_client(self) -> FanDuelApiClient:
        """Get authenticated FanDuel API client.

        Returns:
            FanDuelApiClient instance

        Raises:
            ValueError: If auth tokens not configured
        """
        auth_token = os.environ.get("FANDUEL_AUTH_TOKEN")

        if not auth_token:
            raise ValueError(
                "FANDUEL_AUTH_TOKEN not configured. "
                "Set in .env or use browser extension."
            )

        return FanDuelApiClient(basic_auth_token=auth_token)

    def scrape_contests(self, sport: Sport) -> list[ScheduledContest]:
        """Scrape and select eligible contests for a sport.

        Args:
            sport: Sport to scrape

        Returns:
            List of ScheduledContest objects for eligible contests
        """
        logger.info(f"Scraping {sport.value} contests from FanDuel...")

        try:
            client = self._get_api_client()
        except ValueError as e:
            logger.error(f"Auth error: {e}")
            return []

        # Get all fixture lists for sport
        fixture_lists = client.get_fixture_lists(sport)
        logger.info(f"Found {len(fixture_lists)} {sport.value} fixture lists")

        all_contests = []

        for fl in fixture_lists:
            fl_id = fl.get("id")
            fl_label = fl.get("label", "Unknown")
            start_date_str = fl.get("start_date")

            # Skip snake drafts
            if "snake" in fl_label.lower():
                continue

            # Parse slate start time
            if start_date_str:
                try:
                    slate_start = datetime.fromisoformat(
                        start_date_str.replace("Z", "+00:00")
                    )
                    # Convert to local time (naive datetime)
                    slate_start = slate_start.replace(tzinfo=None)
                except ValueError:
                    slate_start = datetime.now() + timedelta(hours=24)
            else:
                slate_start = datetime.now() + timedelta(hours=24)

            # Skip if slate already started
            if slate_start < datetime.now():
                logger.debug(f"Skipping past slate: {fl_label}")
                continue

            # Fetch contests for this fixture list
            try:
                raw_contests = client._request(
                    "GET", "/contests",
                    params={"fixture_list": fl_id, "status": "open"}
                )
                contests_raw = raw_contests.get("contests", [])
            except Exception as e:
                logger.warning(f"Failed to fetch contests for {fl_label}: {e}")
                continue

            # Parse contests
            contests = [parse_contest(c) for c in contests_raw]

            # Add fixture list info
            for contest in contests:
                contest["fixture_list_id"] = fl_id
                contest["slate_label"] = fl_label
                contest["slate_start"] = slate_start

            all_contests.extend(contests)

        # Filter and score contests
        eligible = self.selector.filter_contests(all_contests)
        scored = self.selector.score_contests(eligible, min_score=0)

        logger.info(
            f"Selected {len(scored)} eligible {sport.value} contests "
            f"(from {len(all_contests)} total)"
        )

        # Convert to ScheduledContest objects
        scheduled = []
        for contest in scored:
            sc = ScheduledContest(
                contest_id=contest.get("id"),
                fixture_list_id=contest.get("fixture_list_id"),
                sport=sport,
                name=contest.get("name", "Unknown"),
                slate_start=contest.get("slate_start"),
                max_entries=contest.get("max_entries", 1),
                entry_fee=float(contest.get("entry_fee", 0)),
                score=contest.get("score", 0),
            )
            scheduled.append(sc)

        return scheduled

    def scrape_and_schedule_all(self) -> dict[str, list[ScheduledContest]]:
        """Scrape and schedule lineups for all configured sports.

        Returns:
            Dict mapping sport name to list of scheduled contests
        """
        results = {}

        for sport in self.config.sports:
            contests = self.scrape_contests(sport)
            if contests:
                self._schedule_lineup_jobs(contests)
                results[sport.value] = contests

        return results

    def scrape_and_schedule(self, sport: Sport) -> list[ScheduledContest]:
        """Scrape and schedule lineups for a single sport.

        Args:
            sport: Sport to scrape

        Returns:
            List of scheduled contests
        """
        contests = self.scrape_contests(sport)
        if contests:
            self._schedule_lineup_jobs(contests)
        return contests

    def _schedule_lineup_jobs(self, contests: list[ScheduledContest]) -> None:
        """Schedule lineup generation jobs for contests.

        Args:
            contests: List of contests to schedule
        """
        now = datetime.now()

        for contest in contests:
            contest_key = f"{contest.sport.value}_{contest.contest_id}"

            # Skip if already scheduled
            if contest_key in self._scheduled_contests:
                logger.debug(f"Contest {contest_key} already scheduled")
                continue

            # Store contest
            self._scheduled_contests[contest_key] = contest

            # Calculate job times
            initial_time = contest.slate_start - timedelta(
                hours=self.config.initial_lineup_hours_before
            )
            final_time = contest.slate_start - timedelta(
                minutes=self.config.final_lineup_minutes_before
            )

            # Schedule initial lineup generation
            if initial_time > now:
                self.scheduler.add_job(
                    self._generate_initial_lineups,
                    trigger=DateTrigger(run_date=initial_time),
                    args=[contest_key],
                    id=f"{contest_key}_initial",
                    replace_existing=True,
                )
                logger.info(
                    f"Scheduled initial lineups for {contest.name[:40]} "
                    f"at {initial_time.strftime('%Y-%m-%d %H:%M')}"
                )
            else:
                # If past initial time but before final, generate now
                if now < final_time:
                    logger.info(f"Generating immediate initial lineups for {contest.name[:40]}")
                    self._generate_initial_lineups(contest_key)

            # Schedule final lineup generation
            if final_time > now:
                self.scheduler.add_job(
                    self._generate_final_lineups,
                    trigger=DateTrigger(run_date=final_time),
                    args=[contest_key],
                    id=f"{contest_key}_final",
                    replace_existing=True,
                )
                logger.info(
                    f"Scheduled final lineups for {contest.name[:40]} "
                    f"at {final_time.strftime('%Y-%m-%d %H:%M')}"
                )

    def _generate_initial_lineups(self, contest_key: str) -> Optional[Path]:
        """Generate initial lineups for a contest.

        Args:
            contest_key: Contest key (sport_contestId)

        Returns:
            Path to generated CSV or None if failed
        """
        contest = self._scheduled_contests.get(contest_key)
        if not contest:
            logger.error(f"Contest {contest_key} not found")
            return None

        logger.info(f"Generating initial lineups for {contest.name}")

        filepath = self._run_lineup_generation(contest, lineup_type="initial")

        if filepath:
            contest.initial_generated = True
            contest.initial_filepath = str(filepath)
            logger.info(f"Initial lineups saved to {filepath}")

        return filepath

    def _generate_final_lineups(self, contest_key: str) -> Optional[Path]:
        """Generate final lineups for a contest with updated projections.

        Args:
            contest_key: Contest key (sport_contestId)

        Returns:
            Path to generated CSV or None if failed
        """
        contest = self._scheduled_contests.get(contest_key)
        if not contest:
            logger.error(f"Contest {contest_key} not found")
            return None

        logger.info(f"Generating final lineups for {contest.name}")

        filepath = self._run_lineup_generation(contest, lineup_type="final")

        if filepath:
            contest.final_generated = True
            contest.final_filepath = str(filepath)
            logger.info(f"Final lineups saved to {filepath}")

        return filepath

    def _run_lineup_generation(
        self,
        contest: ScheduledContest,
        lineup_type: str = "initial",
    ) -> Optional[Path]:
        """Run lineup generation for a contest.

        Args:
            contest: Contest to generate lineups for
            lineup_type: "initial" or "final"

        Returns:
            Path to generated CSV or None if failed
        """
        # Import here to avoid circular imports
        from scripts.generate_fanduel_lineups import (
            generate_lineups,
            export_lineups,
        )

        try:
            lineups = generate_lineups(
                sport=contest.sport,
                num_lineups=contest.max_entries,
                randomness=self.config.randomness,
                fixture_list_id=contest.fixture_list_id,
                use_estimated_salaries=False,
                use_vegas_lines=self.config.use_vegas_lines,
                min_game_total=self.config.min_game_total,
                vegas_weight=self.config.vegas_weight,
            )

            if not lineups:
                logger.error(f"No lineups generated for {contest.name}")
                return None

            # Create organized output path
            date_str = contest.slate_start.strftime("%Y-%m-%d")
            output_dir = (
                self.config.output_dir / "fanduel" /
                contest.sport.value / date_str
            )
            output_dir.mkdir(parents=True, exist_ok=True)

            # Export with descriptive filename
            timestamp = datetime.now().strftime("%H%M%S")
            filename = f"{contest.contest_id}_{lineup_type}_{timestamp}.csv"
            filepath = output_dir / filename

            # Use export function
            exported_path = export_lineups(
                lineups=lineups,
                sport=contest.sport,
                output_dir=output_dir.parent.parent.parent,  # data/lineups
                contest_id=contest.contest_id,
            )

            # Rename to our convention
            if exported_path and exported_path.exists():
                exported_path.rename(filepath)
                return filepath

            return exported_path

        except Exception as e:
            logger.error(f"Lineup generation failed for {contest.name}: {e}")
            return None

    def schedule_daily_scrape(self) -> None:
        """Schedule daily contest scraping job."""
        self.scheduler.add_job(
            self.scrape_and_schedule_all,
            trigger=CronTrigger(
                hour=self.config.daily_scrape_hour,
                minute=self.config.daily_scrape_minute,
            ),
            id="daily_contest_scrape",
            replace_existing=True,
        )
        logger.info(
            f"Scheduled daily scrape at "
            f"{self.config.daily_scrape_hour:02d}:{self.config.daily_scrape_minute:02d}"
        )

    def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        self.scheduler.start()
        self._running = True
        logger.info("FanDuel scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        if not self._running:
            return

        self.scheduler.shutdown()
        self._running = False
        logger.info("FanDuel scheduler stopped")

    def get_scheduled_contests(self) -> list[ScheduledContest]:
        """Get all scheduled contests.

        Returns:
            List of ScheduledContest objects
        """
        return list(self._scheduled_contests.values())

    def get_scheduled_jobs(self) -> list[dict]:
        """Get all scheduled jobs.

        Returns:
            List of job info dicts
        """
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time,
                "trigger": str(job.trigger),
            }
            for job in self.scheduler.get_jobs()
        ]

    def print_schedule(self) -> None:
        """Print current schedule to console."""
        print("\n" + "=" * 80)
        print("FANDUEL SCHEDULER STATUS")
        print("=" * 80)

        contests = sorted(
            self._scheduled_contests.values(),
            key=lambda c: c.slate_start
        )

        if not contests:
            print("No contests scheduled")
            print("=" * 80)
            return

        print(f"\n{'Sport':<5} | {'Contest':<35} | {'Slate Lock':<18} | {'Entries':>7} | {'Status':<15}")
        print("-" * 80)

        for contest in contests:
            status_parts = []
            if contest.initial_generated:
                status_parts.append("Initial✓")
            if contest.final_generated:
                status_parts.append("Final✓")
            status = ", ".join(status_parts) if status_parts else "Pending"

            print(
                f"{contest.sport.value:<5} | {contest.name[:35]:<35} | "
                f"{contest.slate_start.strftime('%Y-%m-%d %H:%M'):<18} | "
                f"{contest.max_entries:>7} | {status:<15}"
            )

        print("=" * 80)

        # Print upcoming jobs
        jobs = self.get_scheduled_jobs()
        if jobs:
            print(f"\nUpcoming Jobs ({len(jobs)}):")
            for job in sorted(jobs, key=lambda j: j["next_run"] or datetime.max)[:10]:
                next_run = job["next_run"]
                if next_run:
                    print(f"  - {job['id']}: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")

        print("=" * 80)


def run_scheduler_daemon():
    """Run the FanDuel scheduler as a daemon process.

    This is the main entry point for running the scheduler continuously.
    """
    import time
    import signal

    scheduler = FanDuelScheduler()

    def signal_handler(signum, frame):
        logger.info("Received shutdown signal")
        scheduler.stop()
        exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Schedule daily scraping
    scheduler.schedule_daily_scrape()

    # Do initial scrape
    logger.info("Running initial contest scrape...")
    scheduler.scrape_and_schedule_all()

    # Start scheduler
    scheduler.start()
    scheduler.print_schedule()

    # Keep running
    logger.info("Scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
            # Periodic status check
            scheduler.print_schedule()
    except KeyboardInterrupt:
        scheduler.stop()
