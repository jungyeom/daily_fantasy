"""Yahoo Daily Fantasy lineup editor for modifying existing entries.

This module handles editing existing contest lineups via Yahoo's CSV edit endpoint.
The flow:
1. Navigate to /contest/csv/edit
2. Select "Edit" action and the correct sport/slate
3. Download the CSV template (contains our entry_ids)
4. Parse the template to get entry_ids and current lineup data
5. Generate our edit CSV with swapped players
6. Upload the edited CSV

Yahoo Edit CSV Format:
    Contest Title, Entry Fee, Prizes, Contest ID, Entry ID, [Roster Positions...]

The Entry ID is crucial - it identifies which existing entry to modify.
"""

import csv
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver

from ..common.database import get_database, LineupDB, ContestDB
from ..common.models import Lineup

logger = logging.getLogger(__name__)

YAHOO_DFS_BASE_URL = "https://sports.yahoo.com/dailyfantasy"
YAHOO_EDIT_URL = f"{YAHOO_DFS_BASE_URL}/contest/csv/edit"


class LineupEditor:
    """Handles editing existing contest lineups via Yahoo's CSV edit endpoint.

    The edit flow:
    1. Navigate to edit page and select Edit action
    2. Select the correct sport and game slate
    3. Download CSV template (contains entry_ids for all our entries in that slate)
    4. Parse template to extract entry_ids and match to our lineups
    5. Generate edit CSV with updated player codes
    6. Upload to Yahoo's edit endpoint

    Usage:
        editor = LineupEditor()
        result = editor.edit_lineups_for_slate(driver, sport, slate_info, lineups)
    """

    def __init__(self, download_dir: Optional[Path] = None):
        """Initialize lineup editor.

        Args:
            download_dir: Directory for CSV downloads. Defaults to data/downloads.
        """
        self.db = get_database()
        self.download_dir = download_dir or Path("data/downloads")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.edit_dir = Path("data/edits")
        self.edit_dir.mkdir(parents=True, exist_ok=True)

    def edit_lineups_for_contest(
        self,
        driver: WebDriver,
        contest_id: str,
        lineups: list[Lineup],
        sport: str = "nfl",
        contest_start_time: Optional[datetime] = None,
        contest_title: Optional[str] = None,
    ) -> dict:
        """Edit lineups for a specific contest.

        This is the main entry point. It:
        1. Looks up the contest to find its slate
        2. Navigates to edit page and selects the slate
        3. Downloads template to get entry_ids
        4. Matches entry_ids to our lineups
        5. Generates and uploads edit CSV

        Args:
            driver: Authenticated Selenium WebDriver
            contest_id: Yahoo contest ID
            lineups: List of lineups with updated players
            sport: Sport code (nfl, nba, etc.)
            contest_start_time: Optional start time (if not provided, looks up in DB)
            contest_title: Optional title (if not provided, looks up in DB)

        Returns:
            Dict with success status and details
        """
        if not lineups:
            logger.warning("No lineups to edit")
            return {
                "success": False,
                "message": "No lineups provided",
                "edited_count": 0,
            }

        logger.info(f"Editing {len(lineups)} lineups for contest {contest_id}")

        # Get contest info for slate matching - use provided params or lookup in DB
        if contest_start_time is None or contest_title is None:
            session = self.db.get_session()
            try:
                contest = session.query(ContestDB).filter_by(id=contest_id).first()
                if not contest:
                    logger.warning(f"Contest {contest_id} not found in database, using defaults")
                    # Use current time + 1 day as fallback if not in DB
                    if contest_start_time is None:
                        contest_start_time = datetime.now()
                    if contest_title is None:
                        contest_title = f"Contest {contest_id}"
                else:
                    if contest_start_time is None:
                        contest_start_time = contest.start_time
                    if contest_title is None:
                        contest_title = contest.title
            finally:
                session.close()

        try:
            # Step 1: Navigate to edit page
            logger.info(f"Navigating to edit page: {YAHOO_EDIT_URL}")
            driver.get(YAHOO_EDIT_URL)
            time.sleep(3)

            # Step 2: Select Edit action (not Create)
            if not self._select_edit_action(driver):
                return {
                    "success": False,
                    "message": "Failed to select Edit action",
                    "edited_count": 0,
                }

            # Step 3: Select sport
            if not self._select_sport(driver, sport):
                return {
                    "success": False,
                    "message": f"Failed to select sport: {sport}",
                    "edited_count": 0,
                }

            # Step 4: Select the correct slate based on contest start time
            if not self._select_slate(driver, contest_start_time, contest_title):
                return {
                    "success": False,
                    "message": "Failed to select correct slate",
                    "edited_count": 0,
                }

            # Step 5: Download template CSV
            template_path = self._download_template(driver)
            if not template_path:
                return {
                    "success": False,
                    "message": "Failed to download template CSV",
                    "edited_count": 0,
                }

            # Step 6: Parse template to get entry_ids
            template_entries = self._parse_template(template_path)
            if not template_entries:
                return {
                    "success": False,
                    "message": "Failed to parse template or no entries found",
                    "edited_count": 0,
                }

            logger.info(f"Found {len(template_entries)} entries in template")

            # Step 7: Match entry_ids to our lineups and update them
            matched_lineups = self._match_entries_to_lineups(
                template_entries, lineups, contest_id
            )

            if not matched_lineups:
                return {
                    "success": False,
                    "message": "Could not match any entries to lineups",
                    "edited_count": 0,
                }

            logger.info(f"Matched {len(matched_lineups)} lineups with entry_ids")

            # Step 8: Generate edit CSV with our updated players
            edit_csv_path = self._generate_edit_csv(
                template_entries=template_entries,
                lineups=matched_lineups,
                contest_id=contest_id,
            )

            if not edit_csv_path:
                return {
                    "success": False,
                    "message": "Failed to generate edit CSV",
                    "edited_count": 0,
                }

            # Step 9: Upload the edit CSV
            success = self._upload_edit_csv(driver, edit_csv_path)

            if success:
                # Update lineup status and entry_ids in database
                self._update_lineups_in_db(matched_lineups)

                logger.info(f"Successfully edited {len(matched_lineups)} lineups")
                return {
                    "success": True,
                    "message": f"Edited {len(matched_lineups)} lineups",
                    "edited_count": len(matched_lineups),
                    "csv_path": str(edit_csv_path),
                }
            else:
                return {
                    "success": False,
                    "message": "CSV upload failed",
                    "edited_count": 0,
                    "csv_path": str(edit_csv_path),
                }

        except Exception as e:
            logger.error(f"Lineup editing failed: {e}")
            self._save_debug_screenshot(driver, "edit_error")
            return {
                "success": False,
                "message": f"Error: {str(e)}",
                "edited_count": 0,
            }

    def _select_edit_action(self, driver: WebDriver) -> bool:
        """Select the 'Edit' action button (not 'Create').

        Args:
            driver: WebDriver on edit page

        Returns:
            True if Edit was selected successfully
        """
        try:
            # Look for Edit button/tab
            edit_selectors = [
                "button:contains('Edit')",
                "[data-tst*='edit']",
                ".edit-action",
                "button.edit",
            ]

            # Try finding by text content first
            buttons = driver.find_elements(By.TAG_NAME, "button")
            for btn in buttons:
                try:
                    if btn.text.strip().lower() == "edit" and btn.is_displayed():
                        btn.click()
                        logger.info("Selected 'Edit' action")
                        time.sleep(1)
                        return True
                except Exception:
                    continue

            # Try CSS selectors
            for selector in edit_selectors:
                try:
                    elem = driver.find_element(By.CSS_SELECTOR, selector)
                    if elem.is_displayed():
                        elem.click()
                        logger.info(f"Selected Edit via: {selector}")
                        time.sleep(1)
                        return True
                except Exception:
                    continue

            # Check if Edit is already selected (might be default)
            page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
            if "choose a sport" in page_text or "choose a game slate" in page_text:
                logger.info("Edit action appears to be already selected")
                return True

            logger.warning("Could not find Edit action button")
            self._save_debug_screenshot(driver, "edit_action_not_found")
            return False

        except Exception as e:
            logger.error(f"Failed to select Edit action: {e}")
            return False

    def _select_sport(self, driver: WebDriver, sport: str) -> bool:
        """Select the sport (NFL, NBA, etc.).

        Args:
            driver: WebDriver on edit page
            sport: Sport code (nfl, nba, etc.)

        Returns:
            True if sport was selected successfully
        """
        try:
            sport_upper = sport.upper()
            time.sleep(1)

            # Look for sport buttons/options
            # Try finding by text content
            all_elements = driver.find_elements(By.XPATH, f"//*[contains(text(), '{sport_upper}')]")
            for elem in all_elements:
                try:
                    if elem.is_displayed() and elem.is_enabled():
                        # Check if it's clickable (button, div with click handler, etc.)
                        tag = elem.tag_name.lower()
                        if tag in ("button", "div", "span", "a", "li"):
                            elem.click()
                            logger.info(f"Selected sport: {sport_upper}")
                            time.sleep(1)
                            return True
                except Exception:
                    continue

            # Try data attributes
            sport_selectors = [
                f"[data-sport='{sport.lower()}']",
                f"[data-tst*='{sport.lower()}']",
                f".sport-{sport.lower()}",
            ]

            for selector in sport_selectors:
                try:
                    elem = driver.find_element(By.CSS_SELECTOR, selector)
                    if elem.is_displayed():
                        elem.click()
                        logger.info(f"Selected sport via: {selector}")
                        time.sleep(1)
                        return True
                except Exception:
                    continue

            logger.warning(f"Could not find sport selector for: {sport}")
            self._save_debug_screenshot(driver, "sport_not_found")
            return False

        except Exception as e:
            logger.error(f"Failed to select sport: {e}")
            return False

    def _select_slate(
        self,
        driver: WebDriver,
        contest_start_time: Optional[datetime],
        contest_title: str,
    ) -> bool:
        """Select the correct game slate based on contest info.

        The page shows multiple slates (e.g., "Tomorrow, 1:00 PM - 10 NFL Games").
        We need to select the one that matches our contest's start time.

        Args:
            driver: WebDriver on edit page
            contest_start_time: When the contest starts
            contest_title: Contest title (may contain slate info)

        Returns:
            True if slate was selected successfully
        """
        try:
            time.sleep(2)  # Wait for page to fully render

            # The slate is rendered as an <a> tag with class 'fade-bg' inside a 'ys-pillChoose' ul
            # Look for anchor tags containing game info patterns
            slate_elements = []

            # First try: look for anchor tags with slate info (most reliable)
            all_anchors = driver.find_elements(By.TAG_NAME, "a")
            for anchor in all_anchors:
                try:
                    text = anchor.text.strip()
                    # Look for slate patterns: "X NBA Games", "X NFL Games", time patterns
                    if text and re.search(r'\d+\s+(NFL|NBA|MLB|NHL)\s+Games?', text, re.I):
                        # Check if it's visible and likely a slate (not nav)
                        if anchor.is_displayed() and "skip" not in text.lower():
                            slate_elements.append(anchor)
                            logger.debug(f"Found slate anchor: {text[:60]}")
                except Exception:
                    continue

            # Second try: look for elements in ys-pillChoose (Yahoo's pill chooser component)
            if not slate_elements:
                try:
                    pill_container = driver.find_element(By.CSS_SELECTOR, ".ys-pillChoose")
                    anchors_in_pill = pill_container.find_elements(By.TAG_NAME, "a")
                    for anchor in anchors_in_pill:
                        if anchor.is_displayed():
                            slate_elements.append(anchor)
                except Exception:
                    pass

            # Third try: data attributes
            if not slate_elements:
                slate_selectors = [
                    "[data-tst*='slate']",
                    "[data-tst*='game-slate']",
                    ".slate-option",
                    ".game-slate",
                ]
                for selector in slate_selectors:
                    try:
                        elements = driver.find_elements(By.CSS_SELECTOR, selector)
                        slate_elements.extend(elements)
                    except Exception:
                        continue

            if not slate_elements:
                logger.warning("No slate elements found, trying to continue anyway")
                return True

            logger.info(f"Found {len(slate_elements)} potential slate options")

            # If we have a contest start time, try to match it
            if contest_start_time:
                for elem in slate_elements:
                    try:
                        text = elem.text.strip()
                        if not text or "skip" in text.lower() or len(text) > 100:
                            continue
                        # Check if the slate time matches our contest
                        if self._slate_matches_time(text.lower(), contest_start_time):
                            if elem.is_displayed():
                                # Use JavaScript click to ensure it works
                                driver.execute_script("arguments[0].click();", elem)
                                logger.info(f"Clicked slate matching contest time: {text[:50]}")
                                time.sleep(3)  # Wait longer for download link to appear
                                return True
                    except Exception as e:
                        logger.debug(f"Failed to click slate: {e}")
                        continue

            # If no time match, just select the first available slate
            for elem in slate_elements:
                try:
                    if elem.is_displayed() and elem.is_enabled():
                        driver.execute_script("arguments[0].click();", elem)
                        logger.info(f"Clicked first available slate: {elem.text[:50] if elem.text else 'unknown'}")
                        time.sleep(3)
                        return True
                except Exception:
                    continue

            logger.warning("Could not select any slate")
            self._save_debug_screenshot(driver, "slate_not_found")
            return False

        except Exception as e:
            logger.error(f"Failed to select slate: {e}")
            return False

    def _slate_matches_time(self, slate_text: str, contest_time: datetime) -> bool:
        """Check if slate text matches the contest start time.

        Args:
            slate_text: Text from slate element
            contest_time: Contest start datetime

        Returns:
            True if they appear to match
        """
        try:
            # Extract time from slate text (e.g., "1:00 PM", "4:25 PM")
            time_match = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM)', slate_text, re.I)
            if not time_match:
                return False

            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
            ampm = time_match.group(3).upper()

            # Convert to 24-hour
            if ampm == "PM" and hour != 12:
                hour += 12
            elif ampm == "AM" and hour == 12:
                hour = 0

            # Check if contest hour matches
            if contest_time.hour == hour:
                return True

            # Allow some flexibility (within 1 hour)
            if abs(contest_time.hour - hour) <= 1:
                return True

            return False

        except Exception:
            return False

    def _download_template(self, driver: WebDriver) -> Optional[Path]:
        """Download the CSV template from Yahoo.

        The template contains all our existing entries with their entry_ids.

        Args:
            driver: WebDriver on edit page after selecting slate

        Returns:
            Path to downloaded CSV file, or None if failed
        """
        try:
            # Wait for download link to appear (it's dynamically rendered after slate selection)
            time.sleep(3)

            # Clear old CSVs from download dir
            for old_csv in self.download_dir.glob("*.csv"):
                old_csv.unlink()

            # Try multiple times with increasing waits
            for attempt in range(3):
                logger.info(f"Looking for download link (attempt {attempt + 1}/3)...")

                # PRIORITY 1: Look for the specific ".csv template" link text
                # Yahoo's edit page has "Download a .csv template" link
                specific_selectors = [
                    "//a[contains(text(), '.csv template')]",
                    "//a[contains(text(), 'csv template')]",
                    "//a[contains(., '.csv template')]",
                    "//a[contains(., 'Download a .csv')]",
                ]

                for selector in specific_selectors:
                    try:
                        from selenium.webdriver.common.by import By
                        download_link = driver.find_element(By.XPATH, selector)
                        if download_link and download_link.is_displayed():
                            href = download_link.get_attribute("href") or ""
                            logger.info(f"Found template link: '{download_link.text}' -> {href[:60]}")
                            driver.execute_script("arguments[0].click();", download_link)
                            time.sleep(2)

                            result = self._wait_for_download(timeout=30)
                            if result:
                                return result
                    except Exception:
                        continue

                # PRIORITY 2: Look for links with export/template in href
                links = driver.find_elements(By.TAG_NAME, "a")
                for link in links:
                    try:
                        text = link.text.lower().strip()
                        href = link.get_attribute("href") or ""

                        # Look for template download links by href pattern
                        is_template_link = (
                            "template" in href.lower() or
                            "export" in href.lower() or
                            ("csv" in text and "template" in text)
                        )

                        # Skip navigation links
                        is_nav = "skip" in text or "app" in text.lower() or len(text) > 100

                        if is_template_link and not is_nav and link.is_displayed():
                            logger.info(f"Found download link: '{text}' -> {href[:60]}")
                            driver.execute_script("arguments[0].click();", link)
                            time.sleep(2)

                            result = self._wait_for_download(timeout=30)
                            if result:
                                return result
                    except Exception as e:
                        logger.debug(f"Error checking link: {e}")
                        continue

                # PRIORITY 3: Iterate all links looking for "csv" and "template" text
                for link in links:
                    try:
                        text = link.text.lower() if link.text else ""
                        if "csv" in text and "template" in text and link.is_displayed():
                            logger.info(f"Found link by iteration: {text[:50]}")
                            driver.execute_script("arguments[0].click();", link)
                            time.sleep(2)

                            result = self._wait_for_download(timeout=30)
                            if result:
                                return result
                    except Exception:
                        continue

                # Wait and try again
                if attempt < 2:
                    logger.info("Download link not found, waiting and retrying...")
                    time.sleep(3)

            logger.error("Could not find download link for template after multiple attempts")
            self._save_debug_screenshot(driver, "editor_download_not_found")
            self._save_page_source(driver, "editor_page")
            return None

        except Exception as e:
            logger.error(f"Failed to download template: {e}")
            return None

    def _wait_for_download(self, timeout: int = 30) -> Optional[Path]:
        """Wait for CSV file to appear in download directory.

        Args:
            timeout: Max seconds to wait

        Returns:
            Path to downloaded file, or None if timeout
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            # Look for CSV files
            csv_files = list(self.download_dir.glob("*.csv"))
            # Filter out partial downloads
            csv_files = [f for f in csv_files if not f.name.endswith(".crdownload")]

            if csv_files:
                # Return the most recent one
                latest = max(csv_files, key=lambda f: f.stat().st_mtime)
                logger.info(f"Downloaded template: {latest}")
                return latest

            time.sleep(0.5)

        logger.error("Timeout waiting for template download")
        return None

    def _parse_template(self, template_path: Path) -> list[dict]:
        """Parse the downloaded template CSV to extract entry data.

        Args:
            template_path: Path to template CSV

        Returns:
            List of entry dicts with entry_id, contest_id, and player codes
        """
        entries = []

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                headers = reader.fieldnames

                if not headers:
                    logger.error("Template has no headers")
                    return []

                logger.info(f"Template headers: {headers}")

                for row in reader:
                    entry = {
                        "contest_title": row.get("Contest Title", ""),
                        "entry_fee": row.get("Entry Fee", ""),
                        "prizes": row.get("Prizes", ""),
                        "contest_id": row.get("Contest ID", ""),
                        "entry_id": row.get("Entry ID", ""),
                        "players": {},
                    }

                    # Extract player codes for each roster position
                    for header in headers:
                        if header not in ["Contest Title", "Entry Fee", "Prizes", "Contest ID", "Entry ID"]:
                            entry["players"][header] = row.get(header, "")

                    if entry["entry_id"]:
                        entries.append(entry)

            logger.info(f"Parsed {len(entries)} entries from template")
            return entries

        except Exception as e:
            logger.error(f"Failed to parse template: {e}")
            return []

    def _match_entries_to_lineups(
        self,
        template_entries: list[dict],
        lineups: list[Lineup],
        contest_id: str,
    ) -> list[Lineup]:
        """Match template entry_ids to our lineups.

        Strategy:
        1. Filter template entries by contest_id
        2. Match by comparing player codes (since we may have modified some)
        3. Assign entry_ids to our Lineup objects

        Args:
            template_entries: Entries parsed from template
            lineups: Our lineups to edit
            contest_id: Contest ID to filter by

        Returns:
            Lineups with entry_id set
        """
        matched = []

        # Filter template entries for our contest
        contest_entries = [e for e in template_entries if str(e["contest_id"]) == str(contest_id)]

        if not contest_entries:
            # Maybe all entries are for this contest (single-contest template)
            contest_entries = template_entries
            logger.info(f"No contest filter applied, using all {len(contest_entries)} entries")

        logger.info(f"Matching {len(lineups)} lineups against {len(contest_entries)} template entries")

        # For each lineup, try to find a matching entry
        # We'll match by finding the entry with the most player overlap
        used_entry_ids = set()

        for lineup in lineups:
            if lineup.entry_id:
                # Already has entry_id
                matched.append(lineup)
                used_entry_ids.add(lineup.entry_id)
                continue

            lineup_player_ids = {p.yahoo_player_id for p in lineup.players}
            lineup_player_codes = {p.player_game_code for p in lineup.players if p.player_game_code}

            best_match = None
            best_overlap = 0

            for entry in contest_entries:
                if entry["entry_id"] in used_entry_ids:
                    continue

                entry_codes = set(entry["players"].values())

                # Count overlaps
                overlap = len(lineup_player_codes & entry_codes)

                # Also try matching by player_id in the codes
                for code in entry_codes:
                    for pid in lineup_player_ids:
                        if pid in code:
                            overlap += 0.5  # Partial match

                if overlap > best_overlap:
                    best_overlap = overlap
                    best_match = entry

            if best_match:
                lineup.entry_id = best_match["entry_id"]
                used_entry_ids.add(best_match["entry_id"])
                matched.append(lineup)
                logger.debug(f"Matched lineup {lineup.id} to entry {lineup.entry_id} (overlap: {best_overlap})")
            else:
                logger.warning(f"Could not match lineup {lineup.id} to any entry")

        return matched

    def _generate_edit_csv(
        self,
        template_entries: list[dict],
        lineups: list[Lineup],
        contest_id: str,
    ) -> Optional[Path]:
        """Generate the edit CSV with our updated player codes.

        Uses the template structure but replaces player codes with our swapped players.

        Args:
            template_entries: Original entries from template (for headers/structure)
            lineups: Our lineups with updated players and entry_ids
            contest_id: Contest ID

        Returns:
            Path to generated CSV file
        """
        try:
            if not template_entries:
                logger.error("No template entries to base CSV on")
                return None

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"edit_{contest_id}_{timestamp}.csv"
            filepath = self.edit_dir / filename

            # Get roster positions from template
            roster_positions = list(template_entries[0]["players"].keys())

            # Build entry lookup by entry_id
            lineup_by_entry_id = {l.entry_id: l for l in lineups if l.entry_id}

            with open(filepath, "w", newline="", encoding="utf-8") as f:
                headers = ["Contest Title", "Entry Fee", "Prizes", "Contest ID", "Entry ID"] + roster_positions
                writer = csv.writer(f)
                writer.writerow(headers)

                for entry in template_entries:
                    entry_id = entry["entry_id"]

                    if entry_id in lineup_by_entry_id:
                        # Use our updated lineup
                        lineup = lineup_by_entry_id[entry_id]

                        row = [
                            entry["contest_title"],
                            entry["entry_fee"],
                            entry["prizes"],
                            entry["contest_id"],
                            entry_id,
                        ]

                        # Build player codes by roster position
                        player_by_position = {
                            p.roster_position: p for p in lineup.players
                        }

                        for pos in roster_positions:
                            player = player_by_position.get(pos)
                            if player:
                                code = player.player_game_code or player.yahoo_player_id
                                row.append(code)
                            else:
                                # Keep original if we don't have this position
                                row.append(entry["players"].get(pos, ""))

                        writer.writerow(row)
                    else:
                        # Keep original entry unchanged
                        row = [
                            entry["contest_title"],
                            entry["entry_fee"],
                            entry["prizes"],
                            entry["contest_id"],
                            entry_id,
                        ]
                        for pos in roster_positions:
                            row.append(entry["players"].get(pos, ""))
                        writer.writerow(row)

            logger.info(f"Generated edit CSV: {filepath}")

            # Log preview
            with open(filepath, "r") as f:
                reader = csv.reader(f)
                for i, row in enumerate(reader):
                    if i < 3:
                        logger.debug(f"  Row {i}: {row[:7]}...")

            return filepath

        except Exception as e:
            logger.error(f"Failed to generate edit CSV: {e}")
            return None

    def _upload_edit_csv(self, driver: WebDriver, csv_path: Path) -> bool:
        """Upload the edit CSV to Yahoo.

        Args:
            driver: WebDriver on edit page
            csv_path: Path to our edit CSV

        Returns:
            True if upload successful
        """
        try:
            time.sleep(1)

            # Find file input
            file_input = None
            file_input_selectors = [
                "input[type='file'][accept='.csv']",
                "input[type='file']",
            ]

            for selector in file_input_selectors:
                try:
                    file_input = driver.find_element(By.CSS_SELECTOR, selector)
                    if file_input:
                        logger.info(f"Found file input: {selector}")
                        break
                except Exception:
                    continue

            if not file_input:
                logger.error("Could not find file input on edit page")
                self._save_debug_screenshot(driver, "upload_file_input_not_found")
                return False

            # Upload the file
            logger.info(f"Uploading edit CSV: {csv_path}")
            file_input.send_keys(str(csv_path.absolute()))

            # Wait for file to be processed
            time.sleep(3)

            # Look for submit button
            submit_btn = self._find_submit_button(driver)

            if not submit_btn:
                logger.error("Could not find submit button on edit page")
                self._save_debug_screenshot(driver, "upload_submit_not_found")
                return False

            # Click submit
            try:
                driver.execute_script("arguments[0].scrollIntoView(true);", submit_btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", submit_btn)
                logger.info("Clicked submit button")
            except Exception as e:
                logger.warning(f"JavaScript click failed, trying regular click: {e}")
                try:
                    submit_btn.click()
                except Exception as e2:
                    logger.error(f"Both click methods failed: {e2}")
                    return False

            # Wait for processing
            time.sleep(5)

            # Check for success/error
            success = self._check_upload_result(driver)
            self._save_debug_screenshot(driver, "upload_result")

            return success

        except Exception as e:
            logger.error(f"Edit CSV upload failed: {e}")
            self._save_debug_screenshot(driver, "upload_error")
            return False

    def _find_submit_button(self, driver: WebDriver):
        """Find the submit/upload button on the edit page."""
        # PRIORITY 1: Try XPATH selectors for upload-related buttons
        upload_selectors = [
            "//button[contains(text(), 'Upload and edit')]",
            "//button[contains(text(), 'Upload')]",
            "//button[contains(., 'Upload')]",
            "//button[contains(., 'edit entries')]",
        ]
        for selector in upload_selectors:
            try:
                btn = driver.find_element(By.XPATH, selector)
                if btn and btn.is_displayed() and btn.is_enabled():
                    logger.info(f"Found upload button with: {selector}")
                    return btn
            except Exception:
                continue

        # PRIORITY 2: Try by button text iteration
        buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in buttons:
            try:
                text = btn.text.strip().lower()
                if "upload" in text and btn.is_displayed() and btn.is_enabled():
                    logger.info(f"Found button by text: {btn.text}")
                    return btn
            except Exception:
                continue

        # PRIORITY 3: Other submit-like buttons
        for btn in buttons:
            try:
                text = btn.text.strip().lower()
                if text in ("submit", "save", "edit", "update") and btn.is_displayed():
                    return btn
            except Exception:
                continue

        # Try input[type='submit']
        try:
            inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='submit']")
            for inp in inputs:
                if inp.is_displayed():
                    return inp
        except Exception:
            pass

        # Try common selectors
        for selector in ["button[type='submit']", ".submit-button", "[data-tst*='submit']"]:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in elements:
                    if elem.is_displayed() and elem.is_enabled():
                        return elem
            except Exception:
                continue

        return None

    def _check_upload_result(self, driver: WebDriver) -> bool:
        """Check if upload was successful."""
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text.lower()

            # Check for success indicators - Yahoo shows "X Contest entries edited successfully!"
            success_indicators = [
                "edited successfully",
                "entries edited",
                "success",
                "updated successfully",
                "saved",
                "complete",
            ]
            for indicator in success_indicators:
                if indicator in page_text:
                    logger.info(f"Success indicator found: '{indicator}'")
                    return True

            # Check for specific error indicators
            error_indicators = [
                "error",
                "failed",
                "invalid entry id",
                "player not in contest",
                "could not",
                "unable to",
            ]
            for indicator in error_indicators:
                if indicator in page_text:
                    logger.warning(f"Error indicator found: '{indicator}'")
                    # Check if it's a real error or just page text
                    if "your csv file" in page_text and indicator in page_text:
                        logger.error(f"CSV validation error detected")
                        return False

            logger.info("No clear result indicator - assuming success")
            return True

        except Exception as e:
            logger.warning(f"Could not check upload result: {e}")
            return True

    def _update_lineups_in_db(self, lineups: list[Lineup]) -> None:
        """Update lineup status and entry_ids in database.

        Args:
            lineups: Lineups that were edited
        """
        session = self.db.get_session()
        try:
            for lineup in lineups:
                if lineup.id is None:
                    continue

                db_lineup = session.query(LineupDB).filter_by(id=lineup.id).first()
                if db_lineup:
                    db_lineup.status = "edited"
                    if lineup.entry_id:
                        db_lineup.entry_id = lineup.entry_id

            session.commit()
            logger.info(f"Updated {len(lineups)} lineups in database")

        except Exception as e:
            session.rollback()
            logger.error(f"Failed to update lineups in DB: {e}")
        finally:
            session.close()

    def _save_debug_screenshot(self, driver: WebDriver, name: str) -> None:
        """Save screenshot for debugging."""
        try:
            screenshot_dir = Path("data/screenshots")
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = screenshot_dir / f"editor_{name}_{timestamp}.png"
            driver.save_screenshot(str(path))
            logger.debug(f"Saved screenshot: {path}")
        except Exception as e:
            logger.warning(f"Could not save screenshot: {e}")

    def _save_page_source(self, driver: WebDriver, name: str) -> None:
        """Save page source for debugging."""
        try:
            debug_dir = Path("data/debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = debug_dir / f"{name}_{timestamp}.html"
            with open(path, "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            logger.info(f"Saved page source: {path}")
        except Exception as e:
            logger.warning(f"Could not save page source: {e}")


# Convenience function
def edit_contest_lineups(
    driver: WebDriver,
    contest_id: str,
    lineups: list[Lineup],
    sport: str = "nfl",
) -> dict:
    """Edit contest lineups via Yahoo's CSV edit endpoint.

    Args:
        driver: Authenticated WebDriver
        contest_id: Contest ID
        lineups: Lineups to edit
        sport: Sport code

    Returns:
        Edit result dict
    """
    editor = LineupEditor()
    return editor.edit_lineups_for_contest(
        driver=driver,
        contest_id=contest_id,
        lineups=lineups,
        sport=sport,
    )
