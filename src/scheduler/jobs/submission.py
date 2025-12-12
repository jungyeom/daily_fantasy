"""Submission job - generates and submits lineups based on fill rate."""

import logging
from datetime import datetime
from typing import Optional

from ...common.database import get_database, ContestEntryDB, ContestDB
from ...common.models import Sport
from ...optimizer.builder import LineupBuilder
from ...projections.sources.dailyfantasyfuel import DailyFantasyFuelSource
from ...projections.transformer import ProjectionTransformer
from ...yahoo.api import get_api_client, parse_api_player
from ...yahoo.auth import YahooAuth
from ...yahoo.browser import get_browser_manager
from ...yahoo.submission import LineupSubmitter
from ..alerts import get_alerter
from ..fill_monitor import FillMonitor, FillMonitorConfig
from .base import BaseJob

logger = logging.getLogger(__name__)


# Map sport strings to Sport enum
SPORT_MAP = {
    "nfl": Sport.NFL,
    "nba": Sport.NBA,
    "mlb": Sport.MLB,
    "nhl": Sport.NHL,
}


class SubmissionJob(BaseJob):
    """Monitors fill rates and submits lineups when thresholds are met.

    Submission triggers:
    1. Fill rate >= 70%
    2. Time to lock < 2 hours (regardless of fill rate)
    """

    job_name = "submission"

    def __init__(
        self,
        dry_run: bool = False,
        fill_config: Optional[FillMonitorConfig] = None,
    ):
        """Initialize submission job.

        Args:
            dry_run: If True, don't actually submit
            fill_config: Fill monitor configuration
        """
        super().__init__(dry_run)
        self.fill_config = fill_config or FillMonitorConfig(
            fill_rate_threshold=0.70,
            time_before_lock_minutes=120,
            stop_editing_minutes=5,
        )
        self.fill_monitor = FillMonitor(self.fill_config)
        self.api_client = get_api_client()
        self.alerter = get_alerter()

    def execute(self, sport: str = "nfl", **kwargs) -> dict:
        """Check fill rates and submit lineups as needed.

        Args:
            sport: Sport code (e.g., 'nfl')

        Returns:
            Dict with submission results
        """
        logger.info(f"Checking submission status for {sport}...")

        # First, update locked contest statuses
        self.fill_monitor.update_locked_contests()

        # Get fresh contest data from Yahoo
        try:
            contests = self.api_client.get_contests(sport=sport)
        except Exception as e:
            logger.error(f"Failed to fetch contests: {e}")
            return {"sport": sport, "error": str(e), "items_processed": 0}

        # Check which contests should be submitted
        to_submit = self.fill_monitor.get_contests_to_submit(contests, sport)

        if not to_submit:
            logger.info(f"No contests ready for submission in {sport}")
            return {
                "sport": sport,
                "contests_checked": len(contests),
                "contests_submitted": 0,
                "items_processed": 0,
            }

        # Submit each contest
        results = []
        total_lineups = 0

        for contest, entry_record, status in to_submit:
            try:
                result = self._submit_contest(
                    contest=contest,
                    entry_record=entry_record,
                    status=status,
                    sport=sport,
                )
                results.append(result)
                total_lineups += result.get("lineups_submitted", 0)

            except Exception as e:
                logger.error(f"Failed to submit contest {contest['id']}: {e}")
                results.append({
                    "contest_id": str(contest["id"]),
                    "success": False,
                    "error": str(e),
                })
                # Send alert
                self.alerter.alert_submission_failure(str(contest["id"]), str(e))

        return {
            "sport": sport,
            "contests_checked": len(contests),
            "contests_submitted": len([r for r in results if r.get("success")]),
            "total_lineups": total_lineups,
            "results": results,
            "items_processed": total_lineups,
        }

    def _submit_contest(
        self,
        contest: dict,
        entry_record: ContestEntryDB,
        status,
        sport: str,
    ) -> dict:
        """Submit lineups for a single contest.

        Args:
            contest: Contest data from API
            entry_record: Our tracking record
            status: ContestStatus from fill monitor
            sport: Sport code

        Returns:
            Dict with submission result
        """
        contest_id = str(contest["id"])
        max_entries = entry_record.max_entries_allowed or 1

        logger.info(
            f"Submitting to contest {contest_id}: "
            f"{status.reason}, max {max_entries} entries"
        )

        if self.dry_run:
            logger.info(f"[DRY RUN] Would submit {max_entries} lineups to {contest_id}")
            return {
                "contest_id": contest_id,
                "success": True,
                "lineups_submitted": max_entries,
                "fill_rate": status.fill_rate,
                "dry_run": True,
            }

        # Get players and projections
        sport_enum = SPORT_MAP.get(sport.lower(), Sport.NFL)

        try:
            # Fetch player pool
            raw_players = self.api_client.get_contest_players(contest_id)
            from ...common.models import Player

            players = []
            injured_count = 0
            for raw in raw_players:
                parsed = parse_api_player(raw, contest_id)

                # Skip players with INJ (injured) status - they won't play
                # Keep O (out) for now as user requested, but log them
                status = parsed.get("status", "")
                if status == "INJ":
                    injured_count += 1
                    continue

                player = Player(
                    yahoo_player_id=parsed["yahoo_player_id"],
                    player_game_code=parsed.get("player_game_code"),
                    name=parsed["name"],
                    team=parsed["team"],
                    position=parsed["position"],
                    salary=parsed["salary"],
                    status=status,
                )
                players.append(player)

            logger.info(f"Loaded {len(players)} players for contest {contest_id} (excluded {injured_count} INJ players)")

            # Fetch projections
            dff = DailyFantasyFuelSource()
            projections = dff.fetch_projections(sport_enum)
            logger.info(f"Loaded {len(projections)} projections")

            # Match projections to players
            transformer = ProjectionTransformer()
            merged_players = transformer.merge_projections_to_players(projections, players)

            # Filter to players with projections
            players_with_proj = [
                p for p in merged_players
                if p.projected_points and p.projected_points > 0
            ]
            logger.info(f"{len(players_with_proj)} players with projections")

            # Single-game contests need fewer players
            min_players = 10 if entry_record.is_single_game else 20
            if len(players_with_proj) < min_players:
                raise ValueError(f"Not enough players with projections: {len(players_with_proj)}")

            # Generate lineups - use single_game and salary_cap from entry record
            is_single_game = entry_record.is_single_game
            salary_cap = entry_record.salary_cap or 200

            builder = LineupBuilder(
                sport_enum,
                single_game=is_single_game,
                salary_cap=salary_cap,
            )
            lineups = builder.build_lineups(
                players=players_with_proj,
                num_lineups=max_entries,
                contest_id=contest_id,
                save_to_db=True,
            )

            logger.info(
                f"Generated {len(lineups)} lineups "
                f"(single_game={is_single_game}, salary_cap={salary_cap})"
            )

            if not lineups:
                raise ValueError("Failed to generate lineups")

            # Get authenticated browser for submission
            browser_manager = get_browser_manager()
            driver = browser_manager.create_driver()

            try:
                # Login to Yahoo
                auth = YahooAuth()
                if not auth.login(driver):
                    raise ValueError("Failed to authenticate with Yahoo")

                # Submit via CSV
                submitter = LineupSubmitter()
                contest_name = contest.get("name", f"Contest {contest_id}")
                successful, failed = submitter.submit_lineups(
                    driver=driver,
                    lineups=lineups,
                    contest_id=contest_id,
                    sport_name=sport.upper(),
                    contest_name=contest_name,
                    single_game=is_single_game,
                )
                success = successful > 0
            finally:
                try:
                    driver.quit()
                except:
                    pass

            if success:
                # Update tracking
                self.fill_monitor.mark_submitted(
                    contest_id=contest_id,
                    lineups_count=len(lineups),
                    fill_rate=status.fill_rate,
                )

                # Send success alert
                self.alerter.alert_submission_success(
                    contest_id=contest_id,
                    lineup_count=len(lineups),
                    fill_rate=status.fill_rate,
                )

                return {
                    "contest_id": contest_id,
                    "success": True,
                    "lineups_submitted": len(lineups),
                    "fill_rate": status.fill_rate,
                }
            else:
                raise ValueError("Submission failed")

        except Exception as e:
            logger.error(f"Submission failed for {contest_id}: {e}")
            raise
