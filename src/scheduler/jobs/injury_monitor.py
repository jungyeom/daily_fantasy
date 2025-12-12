"""Injury monitor job - checks for OUT players, swaps them, and re-uploads.

This job performs the complete injury handling flow:
1. Get all submitted contests that haven't locked
2. Refresh player pool injury statuses from Yahoo API
3. Find OUT players in our lineups
4. Swap with best available replacement (database update)
5. Re-upload edited lineups to Yahoo via CSV edit endpoint
"""

import logging
from typing import Optional

from ...common.database import get_database, ContestEntryDB, PlayerPoolDB
from ...yahoo.api import get_api_client, parse_api_player
from ...yahoo.auth import YahooAuth
from ...yahoo.editor import LineupEditor
from ..alerts import get_alerter
from ..fill_monitor import FillMonitor, FillMonitorConfig
from ..player_swapper import PlayerSwapper
from .base import BaseJob

logger = logging.getLogger(__name__)


class InjuryMonitorJob(BaseJob):
    """Monitors submitted lineups for OUT players, swaps them, and re-uploads.

    Complete flow:
    1. Get all submitted contests that haven't locked
    2. Refresh player pool injury statuses from API
    3. Find OUT players in our lineups
    4. Swap with best available replacement (in database)
    5. Re-upload swapped lineups to Yahoo via edit CSV endpoint
    """

    job_name = "injury_monitor"

    def __init__(
        self,
        dry_run: bool = False,
        fill_config: Optional[FillMonitorConfig] = None,
    ):
        """Initialize injury monitor job.

        Args:
            dry_run: If True, don't actually swap players or upload
            fill_config: Fill monitor config for edit window checking
        """
        super().__init__(dry_run)
        self.fill_config = fill_config or FillMonitorConfig()
        self.fill_monitor = FillMonitor(self.fill_config)
        self.swapper = PlayerSwapper()
        self.api_client = get_api_client()
        self.alerter = get_alerter()
        self.editor = LineupEditor()

    def execute(self, sport: str = "nfl", **kwargs) -> dict:
        """Check for OUT players and swap them.

        Args:
            sport: Sport code (e.g., 'nfl')

        Returns:
            Dict with swap results
        """
        logger.info(f"Checking injuries for {sport}...")

        # Get submitted contests that can still be edited
        editable_contests = self.fill_monitor.get_editable_contests(sport)

        if not editable_contests:
            logger.info(f"No editable contests for {sport}")
            return {
                "sport": sport,
                "contests_checked": 0,
                "total_swaps": 0,
                "items_processed": 0,
            }

        logger.info(f"Found {len(editable_contests)} editable contests")

        total_swaps = 0
        contest_results = []

        for entry in editable_contests:
            try:
                result = self._check_contest(entry)
                contest_results.append(result)
                total_swaps += result.get("swaps_made", 0)

            except Exception as e:
                logger.error(f"Failed to check contest {entry.contest_id}: {e}")
                contest_results.append({
                    "contest_id": entry.contest_id,
                    "error": str(e),
                })

        return {
            "sport": sport,
            "contests_checked": len(editable_contests),
            "total_swaps": total_swaps,
            "results": contest_results,
            "items_processed": total_swaps,
        }

    def _check_contest(self, entry: ContestEntryDB) -> dict:
        """Check a single contest for OUT players.

        Args:
            entry: Contest entry record

        Returns:
            Dict with check results
        """
        contest_id = entry.contest_id
        logger.info(f"Checking contest {contest_id} for injuries...")

        # Refresh player pool data from API
        self._refresh_player_pool(contest_id)

        # Find and swap OUT players
        swap_results = self.swapper.process_contest_swaps(
            contest_id=contest_id,
            dry_run=self.dry_run,
        )

        # Send alert if any swaps were made
        if swap_results and not self.dry_run:
            self.alerter.alert_swap_performed(contest_id, swap_results)

        successful_swaps = [r for r in swap_results if r.success]
        failed_swaps = [r for r in swap_results if not r.success]

        # Re-upload edited lineups to Yahoo if we made successful swaps
        edit_result = None
        if successful_swaps and not self.dry_run:
            edit_result = self._reupload_swapped_lineups(contest_id, entry)

        return {
            "contest_id": contest_id,
            "out_players_found": len(swap_results),
            "swaps_made": len(successful_swaps),
            "swaps_failed": len(failed_swaps),
            "swap_details": [
                {
                    "original": r.original_player_name,
                    "replacement": r.replacement_player_name,
                    "success": r.success,
                    "error": r.error_message,
                }
                for r in swap_results
            ],
            "edit_result": edit_result,
        }

    def _refresh_player_pool(self, contest_id: str) -> None:
        """Refresh player pool injury statuses from API.

        Args:
            contest_id: Contest ID to refresh
        """
        session = self.db.get_session()

        try:
            # Fetch fresh player data
            raw_players = self.api_client.get_contest_players(contest_id)

            for raw in raw_players:
                parsed = parse_api_player(raw, contest_id)

                # Update injury status in player pool
                player = (
                    session.query(PlayerPoolDB)
                    .filter_by(
                        contest_id=contest_id,
                        yahoo_player_id=parsed["yahoo_player_id"],
                    )
                    .first()
                )

                if player:
                    old_status = player.injury_status
                    new_status = parsed.get("injury_status")

                    if old_status != new_status:
                        logger.info(
                            f"Injury status changed: {player.name} "
                            f"{old_status} -> {new_status}"
                        )
                        player.injury_status = new_status
                        player.injury_note = parsed.get("injury_note")

                        # Mark as inactive if OUT
                        if new_status and new_status.upper() in {"O", "OUT", "IR"}:
                            player.is_active = False
                else:
                    # Player not in our pool yet, add them
                    new_player = PlayerPoolDB(
                        contest_id=contest_id,
                        yahoo_player_id=parsed["yahoo_player_id"],
                        player_game_code=parsed.get("player_game_code"),
                        name=parsed["name"],
                        team=parsed["team"],
                        position=parsed["position"],
                        salary=parsed["salary"],
                        injury_status=parsed.get("injury_status"),
                        injury_note=parsed.get("injury_note"),
                        is_active=parsed.get("injury_status", "").upper() not in {"O", "OUT", "IR"},
                    )
                    session.add(new_player)

            session.commit()
            logger.debug(f"Refreshed player pool for contest {contest_id}")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to refresh player pool: {e}")
            raise
        finally:
            session.close()

    def _reupload_swapped_lineups(self, contest_id: str, entry: ContestEntryDB) -> dict:
        """Re-upload swapped lineups to Yahoo via edit CSV endpoint.

        Args:
            contest_id: Contest ID
            entry: Contest entry record (contains contest info)

        Returns:
            Dict with edit results
        """
        logger.info(f"Re-uploading swapped lineups for contest {contest_id}")

        # Get swapped lineups from database
        swapped_lineups = self.swapper.get_swapped_lineups(contest_id)

        if not swapped_lineups:
            logger.info(f"No swapped lineups to upload for contest {contest_id}")
            return {"success": True, "message": "No lineups to upload", "edited_count": 0}

        # Get authenticated browser session for editing
        driver = None

        try:
            auth = YahooAuth()
            driver = auth.get_authenticated_driver()

            if not driver:
                logger.error("Failed to get authenticated driver for lineup editing")
                return {
                    "success": False,
                    "message": "Authentication failed",
                    "edited_count": 0,
                }

            # Edit lineups via CSV upload (downloads template, matches entry_ids, uploads)
            edit_result = self.editor.edit_lineups_for_contest(
                driver=driver,
                contest_id=contest_id,
                lineups=swapped_lineups,
                sport="nfl",  # TODO: Get from contest or pass as parameter
            )

            # Mark lineups as uploaded if successful
            if edit_result.get("success"):
                lineup_ids = [l.id for l in swapped_lineups if l.id]
                self.swapper.mark_lineups_uploaded(lineup_ids)
                logger.info(f"Successfully re-uploaded {len(swapped_lineups)} lineups")
            else:
                logger.error(f"Failed to re-upload lineups: {edit_result.get('message')}")

            return edit_result

        except Exception as e:
            logger.error(f"Failed to re-upload swapped lineups: {e}")
            return {
                "success": False,
                "message": str(e),
                "edited_count": 0,
            }
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
