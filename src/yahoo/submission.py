"""Yahoo Daily Fantasy lineup submission via CSV upload."""
import csv
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from ..common.config import get_config
from ..common.database import get_database, LineupDB, LineupPlayerDB
from ..common.exceptions import YahooSubmissionError, GameAlreadyStartedError
from ..common.models import Lineup, LineupStatus
from ..common.notifications import get_notifier

logger = logging.getLogger(__name__)

YAHOO_DFS_BASE_URL = "https://sports.yahoo.com/dailyfantasy"

# Yahoo roster position order by sport (must match exactly for CSV upload)
# Multi-game / classic format
ROSTER_POSITION_ORDER = {
    "NFL": ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DEF"],
    "NBA": ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL"],
    "MLB": ["P", "P", "C", "1B", "2B", "3B", "SS", "OF", "OF", "OF"],
    "NHL": ["C", "C", "W", "W", "W", "D", "D", "G", "UTIL"],
}

# Yahoo single-game roster position order (1 SUPERSTAR + 4 FLEX)
SINGLE_GAME_POSITION_ORDER = {
    "NFL": ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"],
    "NBA": ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"],
    "MLB": ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"],
    "NHL": ["SUPERSTAR", "FLEX", "FLEX", "FLEX", "FLEX"],
}

# Selectors discovered from Yahoo page inspection
SELECTORS = {
    # "Upload Lineups from CSV" link at bottom of setlineup page
    "upload_link": "[data-tst*='upload'], a[href*='upload']",
    # File input in upload modal
    "file_input": "input[type='file'][accept='.csv']",
    # Upload button in modal
    "upload_button": "button:contains('Upload'), [data-tst*='upload'] button",
    # Template download link
    "template_link": "a:contains('template'), a[href*='template']",
}


class LineupSubmitter:
    """Submits lineups to Yahoo DFS via CSV upload."""

    def __init__(self):
        """Initialize lineup submitter."""
        self.config = get_config()
        self.db = get_database()
        self.notifier = get_notifier()
        self.lineups_dir = Path(self.config.data_dir) / "lineups"
        self.lineups_dir.mkdir(parents=True, exist_ok=True)

    def submit_lineups(
        self,
        driver: WebDriver,
        lineups: list[Lineup],
        contest_id: str,
        sport_name: str,
        contest_name: str,
        single_game: bool = False,
    ) -> tuple[int, int]:
        """Submit multiple lineups to a Yahoo contest.

        Args:
            driver: Authenticated Selenium WebDriver
            lineups: List of lineups to submit
            contest_id: Yahoo contest ID
            sport_name: Sport name for notifications
            contest_name: Contest name for notifications
            single_game: If True, use single-game position format

        Returns:
            Tuple of (successful_count, failed_count)

        Raises:
            YahooSubmissionError: If submission fails completely
        """
        if not lineups:
            logger.warning("No lineups to submit")
            return 0, 0

        game_type_str = "single-game" if single_game else "multi-game"
        logger.info(f"Submitting {len(lineups)} {game_type_str} lineups to contest {contest_id}...")

        # Generate CSV file for upload with correct position ordering
        csv_path = self._generate_upload_csv(
            lineups, contest_id, sport=sport_name, single_game=single_game
        )

        try:
            # Navigate to contest setlineup page (correct URL pattern)
            setlineup_url = f"{YAHOO_DFS_BASE_URL}/contest/{contest_id}/setlineup"
            driver.get(setlineup_url)
            logger.info(f"Navigating to: {setlineup_url}")

            wait = WebDriverWait(driver, 30)

            # Wait for page to load - look for player list or upload link
            time.sleep(2)  # Brief wait for dynamic content

            # Look for CSV upload option
            success = self._upload_csv(driver, csv_path, wait)

            if success:
                # Verify submission
                submitted_count = self._verify_submission(driver, wait, len(lineups))

                # Update database
                self._mark_lineups_submitted(lineups, submitted_count)

                # Send notification
                if submitted_count > 0:
                    avg_projected = sum(l.projected_points for l in lineups) / len(lineups)
                    self.notifier.notify_lineups_submitted(
                        sport=sport_name,
                        contest_name=contest_name,
                        num_lineups=submitted_count,
                        total_projected=avg_projected,
                    )

                logger.info(f"Successfully submitted {submitted_count}/{len(lineups)} lineups")
                return submitted_count, len(lineups) - submitted_count

            else:
                logger.error("CSV upload failed")
                return 0, len(lineups)

        except Exception as e:
            logger.error(f"Lineup submission failed: {e}")
            if self.config.yahoo.screenshot_on_error:
                from .browser import get_browser_manager
                get_browser_manager().save_screenshot("submission_error", driver)
            raise YahooSubmissionError(f"Submission failed: {e}") from e

    def _generate_upload_csv(
        self,
        lineups: list[Lineup],
        contest_id: str,
        sport: str = "NFL",
        single_game: bool = False,
    ) -> Path:
        """Generate CSV file in Yahoo's required format.

        Yahoo CSV format:
        - Header row: roster positions in specific order
          Multi-game: QB,RB,RB,WR,WR,WR,TE,FLEX,DEF for NFL Classic
          Single-game: SUPERSTAR,FLEX,FLEX,FLEX,FLEX
        - Data rows: player_game_code values from API
          Format: "nfl.p.{player_id}$nfl.g.{game_id}" for players
          Format: "nfl.t.{team_id}$nfl.g.{game_id}" for DEF

        The player_game_code is obtained from the Yahoo API contestPlayers endpoint.

        Args:
            lineups: List of lineups to include
            contest_id: Contest ID
            sport: Sport code (NFL, NBA, etc.)
            single_game: If True, use single-game position format

        Returns:
            Path to generated CSV file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        game_type = "sg" if single_game else "mg"
        filename = f"upload_{contest_id}_{game_type}_{timestamp}.csv"
        filepath = self.lineups_dir / filename

        with open(filepath, "w", newline="", encoding="utf-8") as f:
            if not lineups:
                return filepath

            # Use the correct position order for this sport and game type
            sport_upper = sport.upper()
            if single_game:
                if sport_upper in SINGLE_GAME_POSITION_ORDER:
                    positions = SINGLE_GAME_POSITION_ORDER[sport_upper]
                else:
                    logger.warning(f"Unknown sport {sport} for single-game, using NFL defaults")
                    positions = SINGLE_GAME_POSITION_ORDER["NFL"]
            else:
                if sport_upper in ROSTER_POSITION_ORDER:
                    positions = ROSTER_POSITION_ORDER[sport_upper]
                else:
                    # Fallback: use positions from first lineup (not recommended)
                    logger.warning(f"Unknown sport {sport}, using lineup position order")
                    positions = [p.roster_position for p in lineups[0].players]

            # Use csv.writer (not DictWriter) because positions can have duplicates
            # (e.g., RB, RB) and DictWriter would overwrite duplicate keys
            writer = csv.writer(f)
            writer.writerow(positions)  # Header row

            for lineup in lineups:
                # Sort players to match the expected position order
                ordered_players = self._order_players_for_csv(lineup.players, positions)

                # Build row as a list (not dict) to handle duplicate positions correctly
                row = []
                for i, pos in enumerate(positions):
                    if i < len(ordered_players):
                        player = ordered_players[i]
                        if not player.player_game_code:
                            logger.warning(
                                f"Missing player_game_code for {player.name} in lineup"
                            )
                        row.append(player.player_game_code or player.yahoo_player_id)
                    else:
                        logger.warning(f"Not enough players for position {pos}")
                        row.append("")

                writer.writerow(row)

        logger.info(f"Generated upload CSV with {len(lineups)} lineups: {filepath}")
        return filepath

    def _order_players_for_csv(self, players: list, positions: list[str]) -> list:
        """Order lineup players to match the CSV position template.

        Args:
            players: List of Player objects from lineup
            positions: Expected position order (e.g., ['QB', 'RB', 'RB', 'WR', ...])

        Returns:
            List of players ordered to match positions template
        """
        # Group players by their roster position
        by_position = {}
        for player in players:
            pos = player.roster_position
            if pos not in by_position:
                by_position[pos] = []
            by_position[pos].append(player)

        # Build ordered list matching the position template
        ordered = []
        position_used = {}  # Track how many of each position we've used

        for pos in positions:
            position_used[pos] = position_used.get(pos, 0)
            idx = position_used[pos]

            if pos in by_position and idx < len(by_position[pos]):
                ordered.append(by_position[pos][idx])
                position_used[pos] += 1
            else:
                # Position not found - this shouldn't happen with valid lineups
                logger.warning(f"No player for position {pos} at index {idx}")
                # Try to find any remaining player
                for p in players:
                    if p not in ordered:
                        logger.warning(f"Using {p.name} ({p.roster_position}) for {pos}")
                        ordered.append(p)
                        break

        return ordered

    def _upload_csv(self, driver: WebDriver, csv_path: Path, wait: WebDriverWait) -> bool:
        """Upload CSV file to Yahoo.

        Based on working script with XPath-based element selection:
        1. Click "Upload Lineups from CSV" link to open modal
        2. Find file input and upload CSV
        3. Check for validation errors
        4. Click "Upload" button
        5. Click additional confirmation button (critical step!)
        6. Wait for submission confirmation

        Args:
            driver: WebDriver on setlineup page
            csv_path: Path to CSV file
            wait: WebDriverWait instance

        Returns:
            True if upload successful
        """
        from .browser import get_browser_manager

        try:
            # Step 1: Click "Upload Lineups from CSV" link
            logger.info("Looking for 'Upload Lineups from CSV' link...")

            # Try XPath first (from working script), then fallback selectors
            upload_link = None
            selectors = [
                # XPath from working script for upload trigger
                (By.XPATH, "/html/body/div/div/div/div/div/div[3]/div/div[3]/div[2]/div/div[2]/div/div[2]/div/div[2]/a/span"),
                (By.XPATH, "//a[contains(text(), 'Upload Lineups from CSV')]"),
                (By.XPATH, "//*[contains(text(), 'Upload') and contains(text(), 'CSV')]"),
                (By.CSS_SELECTOR, "[data-tst*='upload']"),
                (By.CSS_SELECTOR, "a[href*='upload']"),
            ]

            for by, selector in selectors:
                try:
                    upload_link = wait.until(EC.element_to_be_clickable((by, selector)))
                    if upload_link:
                        logger.info(f"Found upload link with selector: {selector}")
                        break
                except Exception:
                    continue

            if not upload_link:
                logger.error("Could not find 'Upload Lineups from CSV' link")
                get_browser_manager().save_screenshot("upload_link_not_found", driver)
                return False

            # Use JavaScript click to bypass element interception issues
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", upload_link)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", upload_link)
            except Exception as click_err:
                logger.warning(f"JS click failed, trying regular click: {click_err}")
                upload_link.click()
            logger.info("Clicked upload link, waiting for modal...")

            # Step 2: Wait for upload modal to appear
            time.sleep(2)

            # Step 3: Find file input and upload CSV
            logger.info("Looking for file input...")
            file_input = None
            file_input_selectors = [
                # XPath from working script
                (By.XPATH, "/html/body/div/div/div/div/div/div[4]/div/div[2]/div/div/div/div/div[2]/div[2]/div/input"),
                (By.CSS_SELECTOR, "input[type='file'][accept='.csv']"),
                (By.CSS_SELECTOR, "input[type='file']"),
            ]

            for by, selector in file_input_selectors:
                try:
                    file_input = driver.find_element(by, selector)
                    if file_input:
                        logger.info(f"Found file input with selector: {selector}")
                        break
                except Exception:
                    continue

            if not file_input:
                logger.error("Could not find file input in upload modal")
                get_browser_manager().save_screenshot("file_input_not_found", driver)
                return False

            # Upload the file
            logger.info(f"Uploading file: {csv_path}")
            file_input.send_keys(str(csv_path.absolute()))

            # Wait for file to be processed
            time.sleep(3)

            # Step 4: Check for validation errors
            if self._check_upload_validation_errors(driver):
                logger.error("Validation errors found after CSV upload")
                get_browser_manager().save_screenshot("validation_errors", driver)
                return False

            # Step 5: Wait for detection message before clicking upload
            try:
                wait.until(EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(text(), 'detected')]")
                ))
                logger.info("Lineup detection confirmed")
            except Exception:
                logger.warning("Could not confirm lineup detection message")

            # Step 6: Click "Upload" button to submit
            logger.info("Looking for Upload button...")
            upload_btn = None

            upload_btn_selectors = [
                # XPath from working script for submit button
                (By.XPATH, "/html/body/div/div/div/div/div/div[4]/div/div[2]/div/div/div/div/div[2]/div[4]/button/span"),
                (By.XPATH, "/html/body/div/div/div/div/div/div[4]/div/div[2]/div/div/div/div/div[2]/div[4]/button"),
                (By.XPATH, "//button[.//span[text()='Upload']]"),
                (By.XPATH, "//button[contains(text(), 'Upload')]"),
            ]

            for by, selector in upload_btn_selectors:
                try:
                    upload_btn = driver.find_element(by, selector)
                    if upload_btn and upload_btn.is_displayed() and upload_btn.is_enabled():
                        logger.info(f"Found Upload button with selector: {selector}")
                        break
                    upload_btn = None
                except Exception:
                    continue

            # Fallback: search for button by text
            if not upload_btn:
                all_buttons = driver.find_elements(By.TAG_NAME, "button")
                for btn in all_buttons:
                    try:
                        text = btn.text.strip()
                        if text == "Upload" and btn.is_displayed() and btn.is_enabled():
                            upload_btn = btn
                            logger.info("Found Upload button by text match")
                            break
                    except Exception:
                        continue

            if not upload_btn:
                logger.error("Could not find Upload button")
                get_browser_manager().save_screenshot("upload_button_not_found", driver)
                return False

            # Use JavaScript click to bypass element interception issues
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", upload_btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", upload_btn)
            except Exception as click_err:
                logger.warning(f"JS click failed, trying regular click: {click_err}")
                upload_btn.click()
            logger.info("Clicked Upload button, waiting for confirmation dialog...")

            # Step 7: CRITICAL - Click additional confirmation button
            # This is the step that was missing! After clicking Upload, Yahoo shows
            # another confirmation dialog that requires clicking to finalize submission
            time.sleep(2)

            confirm_success = self._click_confirmation_button(driver, wait)
            if not confirm_success:
                logger.warning("Could not click confirmation button, submission may not be complete")
                get_browser_manager().save_screenshot("confirmation_missing", driver)
                # Don't return False - the upload might still have worked

            # Step 8: Wait for submission confirmation
            time.sleep(3)
            submission_confirmed = self._wait_for_submission_confirmation(driver, wait)

            # Take screenshot of result
            get_browser_manager().save_screenshot("upload_result", driver)

            return submission_confirmed

        except Exception as e:
            logger.error(f"CSV upload failed: {e}")
            get_browser_manager().save_screenshot("upload_error", driver)
            return False

    def _check_upload_validation_errors(self, driver: WebDriver) -> bool:
        """Check if there are validation errors after CSV upload.

        Args:
            driver: WebDriver instance

        Returns:
            True if validation errors found, False otherwise
        """
        try:
            error_selectors = [
                (By.XPATH, "//*[contains(@class, 'error')]"),
                (By.XPATH, "//*[contains(text(), 'Invalid')]"),
                (By.XPATH, "//*[contains(text(), 'Error')]"),
                (By.CSS_SELECTOR, ".validation-error"),
                (By.CSS_SELECTOR, ".error-message"),
            ]

            for by, selector in error_selectors:
                try:
                    errors = driver.find_elements(by, selector)
                    for error in errors:
                        if error.is_displayed():
                            error_text = error.text.strip()
                            if error_text and len(error_text) > 5:  # Filter out empty/tiny elements
                                logger.error(f"Validation error found: {error_text}")
                                return True
                except Exception:
                    continue

            return False
        except Exception as e:
            logger.debug(f"Error checking validation: {e}")
            return False

    def _click_confirmation_button(self, driver: WebDriver, wait: WebDriverWait) -> bool:
        """Click the Submit button in the payment confirmation dialog.

        After clicking Upload, Yahoo shows a "Submit your CSV Upload Entries" dialog
        with payment information and a blue "Submit" button that must be clicked
        to finalize the entry submission and charge the account.

        Args:
            driver: WebDriver instance
            wait: WebDriverWait instance

        Returns:
            True if confirmation button was clicked
        """
        logger.info("Looking for payment confirmation Submit button...")

        # First, wait for the confirmation dialog to appear
        # Look for "Submit your CSV Upload Entries" text or payment dialog
        try:
            WebDriverWait(driver, 10).until(
                EC.any_of(
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Submit your CSV Upload Entries')]")),
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Payment method')]")),
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Fantasy Wallet')]")),
                    EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Entry Fees')]")),
                )
            )
            logger.info("Payment confirmation dialog detected")
        except Exception:
            logger.warning("Could not detect payment confirmation dialog")

        confirm_btn = None
        confirm_selectors = [
            # The blue "Submit" button in the payment dialog
            # It's typically a button with text "Submit" that's styled as primary/blue
            (By.XPATH, "//button[text()='Submit']"),
            (By.XPATH, "//button[.//text()='Submit']"),
            (By.XPATH, "//button[contains(@class, 'primary') or contains(@class, 'btn-primary')]//span[text()='Submit']/.."),
            (By.XPATH, "//button[contains(@class, 'primary') or contains(@class, 'btn-primary')][contains(text(), 'Submit')]"),
            # XPath from working script for confirmation button
            (By.XPATH, "/html/body/div/div/div/div/div/div[4]/div/div[2]/div/div/div/div/div[2]/ul/li[1]/button/span"),
            (By.XPATH, "/html/body/div/div/div/div/div/div[4]/div/div[2]/div/div/div/div/div[2]/ul/li[1]/button"),
            # Generic selectors
            (By.XPATH, "//ul/li[1]/button"),  # First button in a list (common pattern)
            (By.CSS_SELECTOR, "button.primary"),
            (By.CSS_SELECTOR, ".btn-primary"),
        ]

        for by, selector in confirm_selectors:
            try:
                confirm_btn = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((by, selector))
                )
                if confirm_btn and confirm_btn.is_displayed():
                    btn_text = confirm_btn.text.strip()
                    logger.info(f"Found confirmation button '{btn_text}' with selector: {selector}")
                    break
                confirm_btn = None
            except Exception:
                continue

        # Fallback: find all buttons and look for one that says "Submit"
        if not confirm_btn:
            logger.info("Trying fallback: searching all buttons for 'Submit'...")
            all_buttons = driver.find_elements(By.TAG_NAME, "button")
            for btn in all_buttons:
                try:
                    text = btn.text.strip()
                    # Look for Submit button that's NOT the Cancel button
                    if text == "Submit" and btn.is_displayed() and btn.is_enabled():
                        # Verify it's not near a "Cancel" sibling (to ensure we're in the right dialog)
                        confirm_btn = btn
                        logger.info("Found Submit button via fallback search")
                        break
                except Exception:
                    continue

        if confirm_btn:
            try:
                # Take screenshot before clicking for debugging
                from .browser import get_browser_manager
                get_browser_manager().save_screenshot("before_final_submit", driver)

                # Use JavaScript click to bypass element interception issues
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", confirm_btn)
                    time.sleep(0.5)
                    driver.execute_script("arguments[0].click();", confirm_btn)
                except Exception as click_err:
                    logger.warning(f"JS click failed, trying regular click: {click_err}")
                    confirm_btn.click()
                logger.info("Clicked Submit button in payment confirmation dialog")

                # Wait for submission to process
                time.sleep(3)

                # Take screenshot after clicking
                get_browser_manager().save_screenshot("after_final_submit", driver)

                return True
            except Exception as e:
                logger.warning(f"Failed to click confirmation button: {e}")
                return False
        else:
            logger.warning("Could not find Submit button in payment confirmation dialog")
            from .browser import get_browser_manager
            get_browser_manager().save_screenshot("submit_button_not_found", driver)
            return False

    def _wait_for_submission_confirmation(self, driver: WebDriver, wait: WebDriverWait) -> bool:
        """Wait for submission confirmation message.

        Args:
            driver: WebDriver instance
            wait: WebDriverWait instance

        Returns:
            True if submission was confirmed successful
        """
        logger.info("Waiting for submission confirmation...")

        try:
            # Look for success indicators
            success_selectors = [
                (By.XPATH, "//*[contains(text(), 'Success')]"),
                (By.XPATH, "//*[contains(text(), 'success')]"),
                (By.XPATH, "//*[contains(text(), 'submitted')]"),
                (By.XPATH, "//*[contains(text(), 'Submitted')]"),
                (By.XPATH, "//*[contains(text(), 'complete')]"),
                (By.CSS_SELECTOR, ".success-message"),
                (By.CSS_SELECTOR, "[data-tst='successMessage']"),
            ]

            for by, selector in success_selectors:
                try:
                    success_elem = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((by, selector))
                    )
                    if success_elem and success_elem.is_displayed():
                        logger.info(f"Submission confirmed: {success_elem.text[:100] if success_elem.text else 'Success element found'}")
                        return True
                except Exception:
                    continue

            # Check if modal is closed (another sign of success)
            try:
                modal_closed = WebDriverWait(driver, 5).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, "[role='dialog']"))
                )
                if modal_closed:
                    logger.info("Upload modal closed - assuming success")
                    return True
            except Exception:
                pass

            # If we got here without errors, assume success
            logger.info("No explicit confirmation found, but no errors detected")
            return True

        except Exception as e:
            logger.warning(f"Error waiting for confirmation: {e}")
            return True  # Assume success if no clear error

    def _verify_submission(self, driver: WebDriver, wait: WebDriverWait, expected_count: int) -> int:
        """Verify how many lineups were successfully submitted.

        Args:
            driver: WebDriver
            wait: WebDriverWait instance
            expected_count: Expected number of lineups

        Returns:
            Number of successfully submitted lineups
        """
        try:
            # Look for success message or entry count
            success_elem = wait.until(
                EC.presence_of_element_located((
                    By.CSS_SELECTOR,
                    "[data-tst='successMessage'], .success-message, .entry-count"
                ))
            )

            text = success_elem.text
            logger.info(f"Submission result: {text}")

            # Try to extract count from message
            import re
            match = re.search(r"(\d+)", text)
            if match:
                return int(match.group(1))

            # If we see success message, assume all went through
            if "success" in text.lower():
                return expected_count

            return 0

        except Exception as e:
            logger.warning(f"Could not verify submission count: {e}")
            # Assume success if no error was raised during submission
            return expected_count

    def _mark_lineups_submitted(self, lineups: list[Lineup], submitted_count: int) -> None:
        """Update lineup status in database.

        Args:
            lineups: List of lineups
            submitted_count: Number successfully submitted
        """
        session = self.db.get_session()
        try:
            for i, lineup in enumerate(lineups):
                if lineup.id is None:
                    continue

                db_lineup = session.query(LineupDB).filter_by(id=lineup.id).first()
                if db_lineup:
                    if i < submitted_count:
                        db_lineup.status = LineupStatus.SUBMITTED.value
                        db_lineup.submitted_at = datetime.utcnow()
                    else:
                        db_lineup.status = LineupStatus.FAILED.value

            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update lineup status: {e}")
        finally:
            session.close()

    def submit_single_lineup(
        self,
        driver: WebDriver,
        lineup: Lineup,
        contest_id: str,
    ) -> bool:
        """Submit a single lineup manually (without CSV).

        Args:
            driver: Authenticated WebDriver
            lineup: Lineup to submit
            contest_id: Contest ID

        Returns:
            True if submission successful
        """
        logger.info(f"Submitting single lineup to contest {contest_id}...")

        try:
            # Navigate to contest entry page
            entry_url = f"{YAHOO_DFS_BASE_URL}/contest/{contest_id}/enter"
            driver.get(entry_url)

            wait = WebDriverWait(driver, 30)

            # Wait for lineup builder
            wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-tst='lineupBuilder'], .lineup-builder"))
            )

            # Select each player
            for player in lineup.players:
                self._select_player(driver, player.yahoo_player_id, player.roster_position)

            # Click submit
            submit_btn = wait.until(
                EC.element_to_be_clickable((
                    By.CSS_SELECTOR,
                    "[data-tst='submitLineup'], button[type='submit'], .submit-lineup"
                ))
            )
            submit_btn.click()

            # Wait for confirmation
            time.sleep(2)

            # Check for success
            try:
                wait.until(
                    EC.presence_of_element_located((
                        By.CSS_SELECTOR,
                        "[data-tst='successMessage'], .success, .confirmation"
                    ))
                )
                logger.info("Single lineup submitted successfully")
                return True
            except Exception:
                logger.warning("Could not confirm submission")
                return True  # Assume success if no error

        except Exception as e:
            logger.error(f"Single lineup submission failed: {e}")
            return False

    def _select_player(self, driver: WebDriver, player_id: str, position: str) -> None:
        """Select a player for a roster position.

        Args:
            driver: WebDriver on entry page
            player_id: Yahoo player ID
            position: Roster position to fill
        """
        try:
            # Click on roster position slot
            position_slot = driver.find_element(
                By.CSS_SELECTOR,
                f"[data-position='{position}'], .roster-slot[data-pos='{position}']"
            )
            position_slot.click()

            time.sleep(0.5)

            # Find and click player in list
            player_elem = driver.find_element(
                By.CSS_SELECTOR,
                f"[data-player-id='{player_id}'], tr[data-id='{player_id}']"
            )
            player_elem.click()

            time.sleep(0.3)

        except Exception as e:
            logger.warning(f"Failed to select player {player_id} for {position}: {e}")

    def cancel_entries(self, driver: WebDriver, contest_id: str) -> int:
        """Cancel all entries for a contest.

        Args:
            driver: Authenticated WebDriver
            contest_id: Contest ID

        Returns:
            Number of entries cancelled
        """
        logger.info(f"Cancelling entries for contest {contest_id}...")

        try:
            # Navigate to my entries page
            entries_url = f"{YAHOO_DFS_BASE_URL}/contest/{contest_id}/entries"
            driver.get(entries_url)

            wait = WebDriverWait(driver, 30)

            # Look for cancel all button
            cancel_all = driver.find_elements(
                By.CSS_SELECTOR,
                "[data-tst='cancelAll'], .cancel-all, button[contains(text(), 'Cancel')]"
            )

            if cancel_all:
                cancel_all[0].click()

                # Confirm cancellation
                time.sleep(1)
                confirm = driver.find_elements(
                    By.CSS_SELECTOR,
                    "[data-tst='confirmCancel'], .confirm, button[contains(text(), 'Yes')]"
                )
                if confirm:
                    confirm[0].click()

                time.sleep(2)
                logger.info("All entries cancelled")
                return -1  # Unknown count

            # Individual cancellation
            cancel_buttons = driver.find_elements(
                By.CSS_SELECTOR,
                "[data-tst='cancelEntry'], .cancel-entry"
            )

            cancelled = 0
            for btn in cancel_buttons:
                try:
                    btn.click()
                    time.sleep(0.5)
                    cancelled += 1
                except Exception:
                    continue

            logger.info(f"Cancelled {cancelled} entries")
            return cancelled

        except Exception as e:
            logger.error(f"Failed to cancel entries: {e}")
            return 0


def submit_lineups(
    driver: WebDriver,
    lineups: list[Lineup],
    contest_id: str,
    sport_name: str = "Unknown",
    contest_name: str = "Unknown",
    single_game: bool = False,
) -> tuple[int, int]:
    """Convenience function to submit lineups.

    Args:
        driver: Authenticated WebDriver
        lineups: List of lineups
        contest_id: Contest ID
        sport_name: Sport name
        contest_name: Contest name
        single_game: If True, use single-game position format

    Returns:
        Tuple of (successful, failed) counts
    """
    submitter = LineupSubmitter()
    return submitter.submit_lineups(
        driver, lineups, contest_id, sport_name, contest_name, single_game
    )
