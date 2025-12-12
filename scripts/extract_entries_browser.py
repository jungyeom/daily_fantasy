#!/usr/bin/env python3
"""Extract entry IDs from Yahoo DFS using authenticated browser session."""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from selenium.webdriver.common.by import By
from src.yahoo.browser import BrowserManager
from src.yahoo.auth import YahooAuth

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def extract_entries_for_contest(contest_id: str) -> list[dict]:
    """Extract user's entry IDs for a contest using browser automation.

    Args:
        contest_id: Yahoo contest ID

    Returns:
        List of entry dicts with id, contestId, etc.
    """
    browser_manager = BrowserManager()
    auth = YahooAuth()

    try:
        driver = browser_manager.create_driver()
        logger.info("Browser created")

        # Authenticate
        auth.login(driver)
        logger.info("Authenticated")

        # Navigate to the contest detail page
        contest_url = f"https://sports.yahoo.com/dailyfantasy/contest/{contest_id}"
        logger.info(f"Navigating to: {contest_url}")
        driver.get(contest_url)
        time.sleep(3)

        # Save screenshot for debugging
        screenshot_dir = Path("data/screenshots")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        driver.save_screenshot(str(screenshot_dir / f"contest_{contest_id}.png"))
        logger.info("Saved contest page screenshot")

        # Try to extract entry IDs via JavaScript
        # The page likely has the user's entries in a React state or data attribute
        entries = []

        # Method 1: Look for data in window.__PRELOADED_STATE__ or similar
        js_data = driver.execute_script("""
            // Try various locations where Yahoo might store data
            var data = {};

            // Check window.__PRELOADED_STATE__
            if (window.__PRELOADED_STATE__) {
                data.preloadedState = window.__PRELOADED_STATE__;
            }

            // Check window.__INITIAL_STATE__
            if (window.__INITIAL_STATE__) {
                data.initialState = window.__INITIAL_STATE__;
            }

            // Check window.App
            if (window.App && window.App.data) {
                data.appData = window.App.data;
            }

            // Look for React root data
            var root = document.getElementById('root') || document.getElementById('app');
            if (root && root._reactRootContainer) {
                data.hasReactRoot = true;
            }

            return JSON.stringify(data);
        """)

        try:
            parsed_data = json.loads(js_data)
            logger.info(f"JavaScript data keys: {list(parsed_data.keys())}")

            # Save for analysis
            debug_dir = Path("data/debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            with open(debug_dir / f"contest_js_data_{contest_id}.json", "w") as f:
                json.dump(parsed_data, f, indent=2, default=str)
            logger.info("Saved JavaScript data to debug file")
        except:
            logger.warning("Could not parse JavaScript data")

        # Method 2: Look for entry links/elements on the page
        # Contest page might show "My Entries" section
        page_source = driver.page_source

        # Save page source
        with open(debug_dir / f"contest_page_{contest_id}.html", "w") as f:
            f.write(page_source)
        logger.info("Saved contest page source")

        # Look for entry IDs in the page
        import re
        entry_ids = set()

        # Pattern: entry_id or entryId in URLs or data
        patterns = [
            r'entryId["\']?\s*[:=]\s*["\']?(\d+)',
            r'/entry/(\d+)',
            r'"id"\s*:\s*(\d{8,})',  # Entry IDs are typically 9 digits
        ]

        for pattern in patterns:
            matches = re.findall(pattern, page_source)
            entry_ids.update(matches)

        logger.info(f"Found {len(entry_ids)} potential entry IDs: {list(entry_ids)[:10]}")

        # Method 3: Navigate to "My Entries" tab if it exists
        try:
            my_entries_tab = driver.find_elements(By.XPATH, "//*[contains(text(), 'My Entries')]")
            if my_entries_tab:
                logger.info("Found 'My Entries' tab, clicking...")
                driver.execute_script("arguments[0].click();", my_entries_tab[0])
                time.sleep(2)
                driver.save_screenshot(str(screenshot_dir / f"my_entries_{contest_id}.png"))

                # Extract entry data from this view
                new_page_source = driver.page_source
                for pattern in patterns:
                    matches = re.findall(pattern, new_page_source)
                    entry_ids.update(matches)
        except Exception as e:
            logger.debug(f"Could not find My Entries tab: {e}")

        # Convert to entry dicts
        for eid in entry_ids:
            entries.append({
                "id": eid,
                "contestId": contest_id,
            })

        logger.info(f"Extracted {len(entries)} entries for contest {contest_id}")
        return entries

    finally:
        browser_manager.close_driver()


def main():
    contest_ids = ["15255304", "15255305"]

    for contest_id in contest_ids:
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing contest {contest_id}")
        entries = extract_entries_for_contest(contest_id)
        logger.info(f"Found {len(entries)} entries")
        for entry in entries[:5]:
            logger.info(f"  Entry: {entry}")


if __name__ == "__main__":
    main()
