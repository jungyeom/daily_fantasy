#!/usr/bin/env python3
"""End-to-End Testing Runner for Daily Fantasy Automation.

This script tests all modules of the DFS automation system in sequence:
1. Contest Sync - Fetch and filter contests from Yahoo API
2. Player Pool - Fetch player data for contests
3. Projection Sync - Fetch projections from DailyFantasyFuel
4. Lineup Generation - Generate optimized lineups
5. Lineup Submission - Validate submission flow (dry-run by default)

Usage:
    # Run full test suite (dry-run, no actual submissions)
    python scripts/test_e2e.py

    # Test specific sport
    python scripts/test_e2e.py --sport NBA
    python scripts/test_e2e.py --sport NFL

    # Test specific module only
    python scripts/test_e2e.py --module contest_sync
    python scripts/test_e2e.py --module projections
    python scripts/test_e2e.py --module lineup_gen
    python scripts/test_e2e.py --module submission

    # Run with actual submission (CAUTION: will submit real lineups!)
    python scripts/test_e2e.py --live

    # Verbose output
    python scripts/test_e2e.py --verbose

    # Test specific contest
    python scripts/test_e2e.py --contest-id 15283303
"""

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.config import get_config
from src.common.database import (
    init_database,
    get_database,
    ContestDB,
    ContestEntryDB,
    PlayerPoolDB,
    ProjectionDB,
    LineupDB,
)
from src.common.models import Sport, LineupStatus


class TestStatus(Enum):
    """Test result status."""
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    WARNING = "WARNING"


@dataclass
class TestResult:
    """Result of a single test."""
    name: str
    status: TestStatus
    message: str = ""
    duration: float = 0.0
    details: dict = field(default_factory=dict)


@dataclass
class TestSuite:
    """Collection of test results."""
    name: str
    results: list[TestResult] = field(default_factory=list)
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASSED)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAILED)

    @property
    def warnings(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.WARNING)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.SKIPPED)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def duration(self) -> float:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0


class E2ETestRunner:
    """End-to-end test runner for DFS automation."""

    def __init__(
        self,
        sport: Sport = Sport.NBA,
        dry_run: bool = True,
        verbose: bool = False,
        contest_id: Optional[str] = None,
    ):
        """Initialize test runner.

        Args:
            sport: Sport to test
            dry_run: If True, don't make actual submissions
            verbose: If True, show detailed output
            contest_id: Specific contest to test (optional)
        """
        self.sport = sport
        self.dry_run = dry_run
        self.verbose = verbose
        self.contest_id = contest_id
        self.config = get_config()
        self.db = init_database()
        self.suite = TestSuite(name=f"E2E Tests - {sport.value}")
        self.logger = logging.getLogger(__name__)

        # Track state between tests
        self._eligible_contests: list[str] = []
        self._test_contest_id: Optional[str] = None
        self._player_count: int = 0
        self._projection_count: int = 0
        self._lineup_count: int = 0

    def run_all(self) -> TestSuite:
        """Run all end-to-end tests.

        Returns:
            TestSuite with all results
        """
        self._print_header()

        # Run tests in sequence
        self._test_authentication()
        self._test_contest_sync()
        self._test_player_pool()
        self._test_projection_sync()
        self._test_lineup_generation()
        self._test_submission_flow()

        self.suite.end_time = datetime.now()
        self._print_summary()

        return self.suite

    def run_module(self, module: str) -> TestSuite:
        """Run tests for a specific module.

        Args:
            module: Module name to test

        Returns:
            TestSuite with results
        """
        self._print_header()

        module_map = {
            "auth": self._test_authentication,
            "contest_sync": self._test_contest_sync,
            "player_pool": self._test_player_pool,
            "projections": self._test_projection_sync,
            "lineup_gen": self._test_lineup_generation,
            "submission": self._test_submission_flow,
        }

        if module not in module_map:
            self._add_result(
                f"Module: {module}",
                TestStatus.FAILED,
                f"Unknown module. Available: {', '.join(module_map.keys())}",
            )
        else:
            # For individual modules, we may need to set up state
            if module in ("player_pool", "projections", "lineup_gen", "submission"):
                self._setup_test_contest()

            module_map[module]()

        self.suite.end_time = datetime.now()
        self._print_summary()

        return self.suite

    def _setup_test_contest(self) -> None:
        """Set up test contest from existing data or user-provided ID."""
        if self.contest_id:
            self._test_contest_id = self.contest_id
            return

        # Find an existing eligible contest
        session = self.db.get_session()
        try:
            entry = (
                session.query(ContestEntryDB)
                .filter(
                    ContestEntryDB.sport == self.sport.value.lower(),
                    ContestEntryDB.status == "eligible",
                    ContestEntryDB.lock_time > datetime.now(),
                )
                .first()
            )
            if entry:
                self._test_contest_id = entry.contest_id
                self._eligible_contests = [entry.contest_id]
        finally:
            session.close()

    # =========================================================================
    # Test Methods
    # =========================================================================

    def _test_authentication(self) -> None:
        """Test 0: Yahoo Authentication."""
        self._print_section("Module 0: Yahoo Authentication")

        # Test 0.1: Cookie File Exists
        start = time.time()
        try:
            import pickle

            cookie_path = Path(self.config.data_dir) / ".yahoo_cookies.pkl"

            if cookie_path.exists():
                with open(cookie_path, "rb") as f:
                    data = pickle.load(f)

                saved_at = datetime.fromisoformat(data["saved_at"])
                age_hours = (datetime.utcnow() - saved_at).total_seconds() / 3600
                cookie_count = len(data.get("cookies", []))

                self._add_result(
                    "Cookie File",
                    TestStatus.PASSED,
                    f"Found {cookie_count} cookies, {age_hours:.1f} hours old",
                    time.time() - start,
                    {"age_hours": age_hours, "cookie_count": cookie_count},
                )
            else:
                self._add_result(
                    "Cookie File",
                    TestStatus.FAILED,
                    f"Cookie file not found: {cookie_path}",
                    time.time() - start,
                )
                self._add_result(
                    "Cookie Age",
                    TestStatus.SKIPPED,
                    "No cookie file",
                )
                self._add_result(
                    "Session Verification",
                    TestStatus.SKIPPED,
                    "No cookie file",
                )
                return
        except Exception as e:
            self._add_result(
                "Cookie File",
                TestStatus.FAILED,
                f"Error reading cookies: {e}",
                time.time() - start,
            )
            return

        # Test 0.2: Cookie Age Check
        start = time.time()
        SESSION_TIMEOUT_HOURS = 168  # 7 days
        expires_in = SESSION_TIMEOUT_HOURS - age_hours

        if expires_in > 24:
            self._add_result(
                "Cookie Age",
                TestStatus.PASSED,
                f"Cookies valid for {expires_in:.0f} more hours ({expires_in/24:.1f} days)",
                time.time() - start,
            )
        elif expires_in > 0:
            self._add_result(
                "Cookie Age",
                TestStatus.WARNING,
                f"Cookies expire in {expires_in:.0f} hours - consider refreshing soon",
                time.time() - start,
            )
        else:
            self._add_result(
                "Cookie Age",
                TestStatus.WARNING,
                f"Cookies are {abs(expires_in):.0f} hours past timeout - may need refresh",
                time.time() - start,
            )

        # Test 0.3: Session Verification (optional - requires browser)
        start = time.time()
        try:
            from src.yahoo.auth import YahooAuth
            from src.yahoo.browser import BrowserManager

            browser_manager = BrowserManager()
            auth = YahooAuth()

            driver = browser_manager.create_driver()
            try:
                # Restore cookies
                auth._restore_cookies(driver)

                # Verify session
                verified = auth._verify_session(driver)

                if verified:
                    self._add_result(
                        "Session Verification",
                        TestStatus.PASSED,
                        "Yahoo session is active and valid",
                        time.time() - start,
                    )
                else:
                    self._add_result(
                        "Session Verification",
                        TestStatus.FAILED,
                        "Session expired - run: python scripts/yahoo_login.py",
                        time.time() - start,
                    )
            finally:
                driver.quit()
        except Exception as e:
            self._add_result(
                "Session Verification",
                TestStatus.FAILED,
                f"Browser test failed: {e}",
                time.time() - start,
            )

    def _test_contest_sync(self) -> None:
        """Test 1: Contest Sync from Yahoo API."""
        self._print_section("Module 1: Contest Sync")

        # Test 1.1: API Connection
        start = time.time()
        try:
            from src.yahoo.api import YahooDFSApiClient

            api = YahooDFSApiClient()
            contests = api.get_contests(self.sport)

            self._add_result(
                "API Connection",
                TestStatus.PASSED,
                f"Fetched {len(contests)} contests from Yahoo API",
                time.time() - start,
                {"contest_count": len(contests)},
            )
        except Exception as e:
            self._add_result(
                "API Connection",
                TestStatus.FAILED,
                f"Failed to connect to Yahoo API: {e}",
                time.time() - start,
            )
            return

        # Test 1.2: Contest Filtering
        start = time.time()
        try:
            from src.scheduler.contest_filter import ContestFilter, ContestFilterConfig

            filter_config = ContestFilterConfig(
                max_entry_fee=1.0,
                require_multi_entry=True,
                gpp_only=True,
            )
            contest_filter = ContestFilter(filter_config)
            eligible = contest_filter.filter_contests(contests)

            if eligible:
                self._add_result(
                    "Contest Filtering",
                    TestStatus.PASSED,
                    f"Found {len(eligible)} eligible contests (fee < $1, multi-entry, GPP)",
                    time.time() - start,
                    {"eligible_count": len(eligible)},
                )
                self._eligible_contests = [str(c.get("id")) for c in eligible[:5]]
            else:
                self._add_result(
                    "Contest Filtering",
                    TestStatus.WARNING,
                    "No eligible contests found (fee < $1, multi-entry, GPP)",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "Contest Filtering",
                TestStatus.FAILED,
                f"Filter failed: {e}",
                time.time() - start,
            )

        # Test 1.3: Database Sync
        start = time.time()
        try:
            from src.scheduler.jobs.contest_sync import ContestSyncJob

            job = ContestSyncJob(dry_run=self.dry_run)
            result = job.run(sport=self.sport.value.lower())

            status = TestStatus.PASSED if result.get("eligible_contests", 0) > 0 else TestStatus.WARNING
            self._add_result(
                "Database Sync",
                status,
                f"Synced {result.get('new_contests', 0)} new contests to database",
                time.time() - start,
                result,
            )

            # Set test contest for subsequent tests
            if self._eligible_contests and not self._test_contest_id:
                self._test_contest_id = self._eligible_contests[0]

        except Exception as e:
            self._add_result(
                "Database Sync",
                TestStatus.FAILED,
                f"Sync failed: {e}",
                time.time() - start,
            )

    def _test_player_pool(self) -> None:
        """Test 2: Player Pool Fetching."""
        self._print_section("Module 2: Player Pool")

        if not self._test_contest_id:
            self._add_result(
                "Player Pool",
                TestStatus.SKIPPED,
                "No test contest available",
            )
            return

        # Test 2.1: Fetch from API
        start = time.time()
        try:
            from src.yahoo.api import YahooDFSApiClient, parse_api_player

            api = YahooDFSApiClient()
            raw_players = api.get_contest_players(self._test_contest_id)

            self._add_result(
                "Fetch Players API",
                TestStatus.PASSED,
                f"Fetched {len(raw_players)} players for contest {self._test_contest_id}",
                time.time() - start,
                {"player_count": len(raw_players)},
            )
            self._player_count = len(raw_players)
        except Exception as e:
            self._add_result(
                "Fetch Players API",
                TestStatus.FAILED,
                f"API fetch failed: {e}",
                time.time() - start,
            )
            return

        # Test 2.2: Parse Player Data
        start = time.time()
        try:
            parsed_count = 0
            with_game_code = 0

            for raw in raw_players[:10]:  # Sample first 10
                parsed = parse_api_player(raw, self._test_contest_id)
                parsed_count += 1
                if parsed.get("player_game_code"):
                    with_game_code += 1

            if with_game_code == parsed_count:
                self._add_result(
                    "Parse Player Data",
                    TestStatus.PASSED,
                    f"All sampled players have player_game_code",
                    time.time() - start,
                )
            else:
                self._add_result(
                    "Parse Player Data",
                    TestStatus.WARNING,
                    f"{with_game_code}/{parsed_count} players have player_game_code",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "Parse Player Data",
                TestStatus.FAILED,
                f"Parse failed: {e}",
                time.time() - start,
            )

        # Test 2.3: Database Storage
        start = time.time()
        try:
            session = self.db.get_session()
            try:
                count = (
                    session.query(PlayerPoolDB)
                    .filter_by(contest_id=self._test_contest_id)
                    .count()
                )

                if count > 0:
                    self._add_result(
                        "Player Pool DB",
                        TestStatus.PASSED,
                        f"{count} players stored in database",
                        time.time() - start,
                    )
                else:
                    self._add_result(
                        "Player Pool DB",
                        TestStatus.WARNING,
                        "No players in database for this contest",
                        time.time() - start,
                    )
            finally:
                session.close()
        except Exception as e:
            self._add_result(
                "Player Pool DB",
                TestStatus.FAILED,
                f"DB query failed: {e}",
                time.time() - start,
            )

    def _test_projection_sync(self) -> None:
        """Test 3: Projection Sync from DailyFantasyFuel."""
        self._print_section("Module 3: Projection Sync")

        # Test 3.1: DailyFantasyFuel Scraping
        start = time.time()
        try:
            from src.projections.sources.dailyfantasyfuel import DailyFantasyFuelSource

            source = DailyFantasyFuelSource()
            projections = source.fetch_projections(self.sport)

            if projections:
                self._add_result(
                    "DailyFantasyFuel Fetch",
                    TestStatus.PASSED,
                    f"Fetched {len(projections)} projections",
                    time.time() - start,
                    {"projection_count": len(projections)},
                )
                self._projection_count = len(projections)
            else:
                self._add_result(
                    "DailyFantasyFuel Fetch",
                    TestStatus.WARNING,
                    "No projections fetched (source may be unavailable)",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "DailyFantasyFuel Fetch",
                TestStatus.FAILED,
                f"Fetch failed: {e}",
                time.time() - start,
            )

        # Test 3.2: Projection Sync Job
        start = time.time()
        try:
            from src.scheduler.jobs.projection_sync import ProjectionSyncJob

            job = ProjectionSyncJob(dry_run=self.dry_run)
            result = job.run(sport=self.sport.value.lower(), force=True)

            if result.get("refreshed"):
                self._add_result(
                    "Projection Sync Job",
                    TestStatus.PASSED,
                    f"Synced {result.get('total_projections', 0)} projections",
                    time.time() - start,
                    result,
                )
            else:
                self._add_result(
                    "Projection Sync Job",
                    TestStatus.WARNING,
                    f"Not refreshed: {result.get('reason', 'unknown')}",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "Projection Sync Job",
                TestStatus.FAILED,
                f"Job failed: {e}",
                time.time() - start,
            )

        # Test 3.3: Player Matching
        start = time.time()
        if not self._test_contest_id:
            self._add_result(
                "Player Matching",
                TestStatus.SKIPPED,
                "No test contest available",
            )
            return

        try:
            from src.yahoo.players import PlayerPoolFetcher
            from src.projections.aggregator import ProjectionAggregator

            fetcher = PlayerPoolFetcher()
            players = fetcher.get_player_pool_from_db(self._test_contest_id)

            if not players:
                self._add_result(
                    "Player Matching",
                    TestStatus.SKIPPED,
                    "No players in database to match",
                )
                return

            aggregator = ProjectionAggregator()
            players_with_proj = aggregator.get_projections_for_contest(self.sport, players)

            matched = sum(1 for p in players_with_proj if p.projected_points and p.projected_points > 0)
            match_rate = matched / len(players) * 100 if players else 0

            if match_rate >= 50:
                self._add_result(
                    "Player Matching",
                    TestStatus.PASSED,
                    f"Matched {matched}/{len(players)} players ({match_rate:.1f}%)",
                    time.time() - start,
                )
            else:
                self._add_result(
                    "Player Matching",
                    TestStatus.WARNING,
                    f"Low match rate: {matched}/{len(players)} ({match_rate:.1f}%)",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "Player Matching",
                TestStatus.FAILED,
                f"Matching failed: {e}",
                time.time() - start,
            )

    def _test_lineup_generation(self) -> None:
        """Test 4: Lineup Generation."""
        self._print_section("Module 4: Lineup Generation")

        if not self._test_contest_id:
            self._add_result(
                "Lineup Generation",
                TestStatus.SKIPPED,
                "No test contest available",
            )
            return

        # Test 4.1: Load Players with Projections
        start = time.time()
        try:
            from src.yahoo.players import PlayerPoolFetcher
            from src.projections.aggregator import ProjectionAggregator

            fetcher = PlayerPoolFetcher()
            players = fetcher.get_player_pool_from_db(self._test_contest_id)

            if not players:
                self._add_result(
                    "Load Players",
                    TestStatus.SKIPPED,
                    "No players in database",
                )
                return

            aggregator = ProjectionAggregator()
            players = aggregator.get_projections_for_contest(self.sport, players)

            players_with_proj = [p for p in players if p.projected_points and p.projected_points > 0]

            if len(players_with_proj) >= 20:
                self._add_result(
                    "Load Players",
                    TestStatus.PASSED,
                    f"Loaded {len(players_with_proj)} players with projections",
                    time.time() - start,
                )
            else:
                self._add_result(
                    "Load Players",
                    TestStatus.WARNING,
                    f"Only {len(players_with_proj)} players with projections (need 20+)",
                    time.time() - start,
                )
                return
        except Exception as e:
            self._add_result(
                "Load Players",
                TestStatus.FAILED,
                f"Load failed: {e}",
                time.time() - start,
            )
            return

        # Test 4.2: Optimizer Initialization
        start = time.time()
        try:
            from src.optimizer.builder import LineupBuilder

            builder = LineupBuilder(self.sport)

            self._add_result(
                "Optimizer Init",
                TestStatus.PASSED,
                f"Initialized {self.sport.value} optimizer",
                time.time() - start,
            )
        except Exception as e:
            self._add_result(
                "Optimizer Init",
                TestStatus.FAILED,
                f"Init failed: {e}",
                time.time() - start,
            )
            return

        # Test 4.3: Generate Lineups
        start = time.time()
        try:
            lineups = builder.build_lineups(
                players=players_with_proj,
                num_lineups=3,
                contest_id=self._test_contest_id,
                save_to_db=not self.dry_run,
            )

            if lineups:
                avg_proj = sum(l.projected_points for l in lineups) / len(lineups)
                self._add_result(
                    "Generate Lineups",
                    TestStatus.PASSED,
                    f"Generated {len(lineups)} lineups (avg {avg_proj:.1f} pts)",
                    time.time() - start,
                    {"lineup_count": len(lineups), "avg_projected": avg_proj},
                )
                self._lineup_count = len(lineups)
            else:
                self._add_result(
                    "Generate Lineups",
                    TestStatus.FAILED,
                    "No lineups generated",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "Generate Lineups",
                TestStatus.FAILED,
                f"Generation failed: {e}",
                time.time() - start,
            )

        # Test 4.4: CSV Export
        start = time.time()
        try:
            from src.optimizer.exporter import LineupExporter

            if lineups:
                exporter = LineupExporter(self.sport)
                csv_path = exporter.export_for_upload(lineups, self._test_contest_id)

                # Verify CSV has player_game_code format
                with open(csv_path, "r") as f:
                    content = f.read()
                    has_game_code = "$" in content  # player_game_code format: nba.p.123$nba.g.456

                if has_game_code:
                    self._add_result(
                        "CSV Export",
                        TestStatus.PASSED,
                        f"Exported to {csv_path.name} with player_game_code format",
                        time.time() - start,
                    )
                else:
                    self._add_result(
                        "CSV Export",
                        TestStatus.WARNING,
                        f"CSV exported but missing player_game_code format",
                        time.time() - start,
                    )
            else:
                self._add_result(
                    "CSV Export",
                    TestStatus.SKIPPED,
                    "No lineups to export",
                )
        except Exception as e:
            self._add_result(
                "CSV Export",
                TestStatus.FAILED,
                f"Export failed: {e}",
                time.time() - start,
            )

    def _test_submission_flow(self) -> None:
        """Test 5: Submission Flow."""
        self._print_section("Module 5: Submission Flow")

        if not self._test_contest_id:
            self._add_result(
                "Submission Flow",
                TestStatus.SKIPPED,
                "No test contest available",
            )
            return

        # Test 5.1: Fill Rate Check
        start = time.time()
        try:
            from src.scheduler.fill_monitor import FillMonitor
            from src.yahoo.api import YahooDFSApiClient

            api = YahooDFSApiClient()
            contests = api.get_contests(self.sport)

            monitor = FillMonitor()
            ready_contests = monitor.get_contests_to_submit(
                contests, self.sport.value.lower()
            )

            if ready_contests:
                self._add_result(
                    "Fill Rate Check",
                    TestStatus.PASSED,
                    f"{len(ready_contests)} contests ready for submission",
                    time.time() - start,
                )
            else:
                self._add_result(
                    "Fill Rate Check",
                    TestStatus.WARNING,
                    "No contests currently ready (fill rate < 70% or too far from lock)",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "Fill Rate Check",
                TestStatus.FAILED,
                f"Monitor failed: {e}",
                time.time() - start,
            )

        # Test 5.2: Lineup Tracker
        start = time.time()
        try:
            from src.lineup_manager.tracker import LineupTracker

            tracker = LineupTracker()
            lineups = tracker.get_lineups_for_contest(
                self._test_contest_id,
                status=LineupStatus.GENERATED,
            )

            if lineups:
                self._add_result(
                    "Lineup Tracker",
                    TestStatus.PASSED,
                    f"Found {len(lineups)} generated lineups ready for submission",
                    time.time() - start,
                )
            else:
                self._add_result(
                    "Lineup Tracker",
                    TestStatus.WARNING,
                    "No generated lineups found for contest",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "Lineup Tracker",
                TestStatus.FAILED,
                f"Tracker failed: {e}",
                time.time() - start,
            )

        # Test 5.3: Submission Job (dry-run)
        start = time.time()
        try:
            from src.scheduler.jobs.submission import SubmissionJob

            job = SubmissionJob(dry_run=True)  # Always dry-run for this test
            result = job.run(sport=self.sport.value.lower())

            submitted = result.get("total_lineups", 0)
            if submitted > 0:
                self._add_result(
                    "Submission Job (dry-run)",
                    TestStatus.PASSED,
                    f"Would submit {submitted} lineups to {result.get('contests_submitted', 0)} contests",
                    time.time() - start,
                    result,
                )
            else:
                self._add_result(
                    "Submission Job (dry-run)",
                    TestStatus.WARNING,
                    "No lineups would be submitted",
                    time.time() - start,
                )
        except Exception as e:
            self._add_result(
                "Submission Job (dry-run)",
                TestStatus.FAILED,
                f"Job failed: {e}",
                time.time() - start,
            )

        # Test 5.4: Live Submission (only if --live flag)
        if not self.dry_run:
            self._add_result(
                "Live Submission",
                TestStatus.SKIPPED,
                "Live submission test not implemented in automated runner",
            )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _add_result(
        self,
        name: str,
        status: TestStatus,
        message: str = "",
        duration: float = 0.0,
        details: dict = None,
    ) -> None:
        """Add a test result."""
        result = TestResult(
            name=name,
            status=status,
            message=message,
            duration=duration,
            details=details or {},
        )
        self.suite.results.append(result)

        # Print result
        status_symbols = {
            TestStatus.PASSED: "\033[92m✓\033[0m",  # Green checkmark
            TestStatus.FAILED: "\033[91m✗\033[0m",  # Red X
            TestStatus.WARNING: "\033[93m⚠\033[0m",  # Yellow warning
            TestStatus.SKIPPED: "\033[90m○\033[0m",  # Gray circle
        }
        symbol = status_symbols.get(status, "?")
        duration_str = f"({duration:.2f}s)" if duration > 0 else ""

        print(f"  {symbol} {name}: {message} {duration_str}")

        if self.verbose and details:
            for key, value in details.items():
                print(f"      {key}: {value}")

    def _print_header(self) -> None:
        """Print test header."""
        print()
        print("=" * 70)
        print(f"  Daily Fantasy E2E Test Suite")
        print(f"  Sport: {self.sport.value}")
        print(f"  Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        if self.contest_id:
            print(f"  Contest: {self.contest_id}")
        print(f"  Started: {self.suite.start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)

    def _print_section(self, title: str) -> None:
        """Print section header."""
        print()
        print(f"\n{title}")
        print("-" * 40)

    def _print_summary(self) -> None:
        """Print test summary."""
        print()
        print("=" * 70)
        print("  TEST SUMMARY")
        print("=" * 70)

        # Status counts
        print(f"  Total:    {self.suite.total}")
        print(f"  \033[92mPassed:   {self.suite.passed}\033[0m")
        print(f"  \033[91mFailed:   {self.suite.failed}\033[0m")
        print(f"  \033[93mWarnings: {self.suite.warnings}\033[0m")
        print(f"  \033[90mSkipped:  {self.suite.skipped}\033[0m")
        print(f"  Duration: {self.suite.duration:.2f}s")
        print()

        # Overall result
        if self.suite.failed == 0:
            print("  \033[92m✓ ALL TESTS PASSED\033[0m")
        else:
            print("  \033[91m✗ SOME TESTS FAILED\033[0m")
            print()
            print("  Failed tests:")
            for r in self.suite.results:
                if r.status == TestStatus.FAILED:
                    print(f"    - {r.name}: {r.message}")

        print("=" * 70)
        print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="End-to-End Test Runner for Daily Fantasy Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--sport",
        type=str,
        choices=["NFL", "NBA", "MLB", "NHL", "PGA"],
        default="NBA",
        help="Sport to test (default: NBA)",
    )

    parser.add_argument(
        "--module",
        type=str,
        choices=["auth", "contest_sync", "player_pool", "projections", "lineup_gen", "submission"],
        help="Test specific module only",
    )

    parser.add_argument(
        "--contest-id",
        type=str,
        help="Specific contest ID to test",
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live mode (CAUTION: will make real submissions!)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: WARNING)",
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Suppress noisy loggers during tests
    if args.log_level != "DEBUG":
        logging.getLogger("src.yahoo.api").setLevel(logging.WARNING)
        logging.getLogger("src.projections").setLevel(logging.WARNING)
        logging.getLogger("src.optimizer").setLevel(logging.WARNING)
        logging.getLogger("src.scheduler").setLevel(logging.WARNING)

    # Warning for live mode
    if args.live:
        print()
        print("\033[91m" + "=" * 70 + "\033[0m")
        print("\033[91m  WARNING: Running in LIVE mode!\033[0m")
        print("\033[91m  This will make REAL submissions to Yahoo DFS.\033[0m")
        print("\033[91m" + "=" * 70 + "\033[0m")
        response = input("  Are you sure you want to continue? [y/N]: ")
        if response.lower() != "y":
            print("  Aborted.")
            sys.exit(0)

    # Create and run test runner
    runner = E2ETestRunner(
        sport=Sport(args.sport),
        dry_run=not args.live,
        verbose=args.verbose,
        contest_id=args.contest_id,
    )

    if args.module:
        suite = runner.run_module(args.module)
    else:
        suite = runner.run_all()

    # Exit with appropriate code
    sys.exit(0 if suite.failed == 0 else 1)


if __name__ == "__main__":
    main()
