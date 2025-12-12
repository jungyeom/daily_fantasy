#!/usr/bin/env python3
"""Test Yahoo DFS lineup submission via CSV upload.

This script tests the submission flow:
1. Login to Yahoo (using saved cookies/profile)
2. Navigate to a contest setlineup page
3. Click "Upload Lineups from CSV"
4. Upload a test CSV file
5. Click "Upload" button

Usage:
    python scripts/test_submission.py --contest-id 15236144
"""
import argparse
import csv
import logging
import pickle
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = project_root / "data"
COOKIES_FILE = DATA_DIR / ".yahoo_cookies.pkl"
CHROME_PROFILE_DIR = DATA_DIR / ".chrome_profile"
DEBUG_DIR = DATA_DIR / "debug"

DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def create_driver(headless: bool = False) -> webdriver.Chrome:
    """Create Chrome driver with saved profile."""
    options = Options()

    if headless:
        options.add_argument("--headless=new")

    # Use persistent Chrome profile
    if CHROME_PROFILE_DIR.exists():
        options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
        logger.info(f"Using Chrome profile: {CHROME_PROFILE_DIR}")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.implicitly_wait(5)

    return driver


def load_cookies(driver: webdriver.Chrome) -> bool:
    """Load saved Yahoo cookies."""
    if not COOKIES_FILE.exists():
        logger.warning(f"No cookies file found at {COOKIES_FILE}")
        return False

    try:
        driver.get("https://sports.yahoo.com")
        time.sleep(1)

        with open(COOKIES_FILE, "rb") as f:
            cookies = pickle.load(f)

        for cookie in cookies:
            try:
                driver.add_cookie(cookie)
            except Exception as e:
                logger.debug(f"Skipped cookie: {e}")

        logger.info(f"Loaded {len(cookies)} cookies")
        driver.refresh()
        time.sleep(2)
        return True

    except Exception as e:
        logger.error(f"Failed to load cookies: {e}")
        return False


def create_test_csv(contest_id: str, player_ids: list[str]) -> Path:
    """Create a test CSV file for upload.

    Args:
        contest_id: Contest ID
        player_ids: List of Yahoo player IDs (from API)

    Returns:
        Path to created CSV file
    """
    # NFL roster positions for Yahoo (typical format)
    # Based on screenshot, header row should be position names
    positions = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DEF"]

    csv_path = DEBUG_DIR / f"test_lineup_{contest_id}.csv"

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        # Header row with positions
        writer.writerow(positions)

        # If we have player IDs, write them; otherwise use placeholders
        if player_ids and len(player_ids) >= len(positions):
            writer.writerow(player_ids[:len(positions)])
        else:
            # Placeholder - these won't work but show the format
            writer.writerow(["nfl.p.12345"] * len(positions))

    logger.info(f"Created test CSV: {csv_path}")
    return csv_path


def test_download_template(driver: webdriver.Chrome, contest_id: str) -> None:
    """Test downloading the CSV template to understand the format."""
    url = f"https://sports.yahoo.com/dailyfantasy/contest/{contest_id}/setlineup"
    logger.info(f"Navigating to: {url}")
    driver.get(url)

    wait = WebDriverWait(driver, 30)
    time.sleep(3)

    # Save screenshot
    driver.save_screenshot(str(DEBUG_DIR / "setlineup_page.png"))
    logger.info("Saved screenshot of setlineup page")

    # Look for "Upload Lineups from CSV" link
    try:
        upload_link = wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "[data-tst*='upload']")
        ))
        logger.info(f"Found upload link: {upload_link.text}")
        upload_link.click()
        time.sleep(2)

        # Save screenshot of modal
        driver.save_screenshot(str(DEBUG_DIR / "upload_modal.png"))
        logger.info("Saved screenshot of upload modal")

        # Look for template download link
        template_selectors = [
            "a[href*='template']",
            "a[href*='.csv']",
            "//a[contains(text(), 'template')]",
            "//a[contains(text(), 'Download')]",
        ]

        for selector in template_selectors:
            try:
                if selector.startswith("//"):
                    link = driver.find_element(By.XPATH, selector)
                else:
                    link = driver.find_element(By.CSS_SELECTOR, selector)

                href = link.get_attribute("href")
                logger.info(f"Found template link: {href}")

                # If it's a direct CSV link, we can fetch it
                if href and ".csv" in href:
                    logger.info(f"Template URL: {href}")
                break
            except Exception:
                continue

    except Exception as e:
        logger.error(f"Failed to find upload elements: {e}")
        driver.save_screenshot(str(DEBUG_DIR / "upload_error.png"))


def test_upload_flow(driver: webdriver.Chrome, contest_id: str, csv_path: Path) -> bool:
    """Test the full CSV upload flow."""
    url = f"https://sports.yahoo.com/dailyfantasy/contest/{contest_id}/setlineup"
    logger.info(f"Navigating to: {url}")
    driver.get(url)

    wait = WebDriverWait(driver, 30)
    time.sleep(3)

    try:
        # Step 1: Click "Upload Lineups from CSV" link
        logger.info("Step 1: Looking for upload link...")
        upload_link = None

        selectors = [
            (By.CSS_SELECTOR, "[data-tst*='upload']"),
            (By.XPATH, "//a[contains(text(), 'Upload Lineups from CSV')]"),
            (By.XPATH, "//*[contains(text(), 'Upload') and contains(text(), 'CSV')]"),
        ]

        for by, selector in selectors:
            try:
                upload_link = wait.until(EC.element_to_be_clickable((by, selector)))
                logger.info(f"Found upload link with: {selector}")
                break
            except Exception:
                continue

        if not upload_link:
            logger.error("Could not find upload link")
            return False

        upload_link.click()
        logger.info("Clicked upload link")
        time.sleep(2)

        # Save screenshot of modal
        driver.save_screenshot(str(DEBUG_DIR / "upload_modal_opened.png"))

        # Step 2: Find file input
        logger.info("Step 2: Looking for file input...")
        file_input = None

        for selector in ["input[type='file'][accept='.csv']", "input[type='file']"]:
            try:
                file_input = driver.find_element(By.CSS_SELECTOR, selector)
                logger.info(f"Found file input with: {selector}")
                break
            except Exception:
                continue

        if not file_input:
            logger.error("Could not find file input")
            return False

        # Step 3: Upload file
        logger.info(f"Step 3: Uploading file: {csv_path}")
        file_input.send_keys(str(csv_path.absolute()))
        time.sleep(2)

        # Save screenshot after file selection
        driver.save_screenshot(str(DEBUG_DIR / "file_selected.png"))

        # Step 4: Click Upload button
        logger.info("Step 4: Looking for Upload button...")
        upload_btn = None

        btn_selectors = [
            (By.XPATH, "//button[text()='Upload']"),
            (By.XPATH, "//button[contains(text(), 'Upload')]"),
            (By.CSS_SELECTOR, "button[type='submit']"),
        ]

        for by, selector in btn_selectors:
            try:
                buttons = driver.find_elements(by, selector)
                for btn in buttons:
                    if btn.is_displayed() and btn.is_enabled():
                        upload_btn = btn
                        logger.info(f"Found upload button with: {selector}")
                        break
                if upload_btn:
                    break
            except Exception:
                continue

        if not upload_btn:
            logger.warning("Could not find Upload button - checking page state")
            driver.save_screenshot(str(DEBUG_DIR / "no_upload_button.png"))

            # Save page source for debugging
            with open(DEBUG_DIR / "upload_modal.html", "w") as f:
                f.write(driver.page_source)
            logger.info("Saved page source for debugging")
            return False

        logger.info("Clicking Upload button...")
        upload_btn.click()
        time.sleep(3)

        # Save final screenshot
        driver.save_screenshot(str(DEBUG_DIR / "upload_result.png"))
        logger.info("Upload flow completed - check screenshots for result")

        return True

    except Exception as e:
        logger.error(f"Upload flow failed: {e}")
        driver.save_screenshot(str(DEBUG_DIR / "upload_flow_error.png"))
        return False


def fetch_player_ids_for_contest(contest_id: str) -> list[str]:
    """Fetch player IDs from Yahoo API for a contest."""
    try:
        from src.yahoo.api import get_api_client

        client = get_api_client()
        players = client.get_contest_players(contest_id)

        # Extract player codes (IDs)
        player_ids = [p.get("code", "") for p in players if p.get("code")]
        logger.info(f"Fetched {len(player_ids)} player IDs from API")

        return player_ids

    except Exception as e:
        logger.warning(f"Could not fetch player IDs from API: {e}")
        return []


def main():
    parser = argparse.ArgumentParser(description="Test Yahoo DFS submission flow")
    parser.add_argument("--contest-id", required=True, help="Yahoo contest ID to test with")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--template-only", action="store_true", help="Only test template download")
    args = parser.parse_args()

    contest_id = args.contest_id
    logger.info(f"Testing submission flow for contest: {contest_id}")

    # Create driver
    driver = create_driver(headless=args.headless)

    try:
        # Load cookies for authentication
        load_cookies(driver)

        if args.template_only:
            # Just test template download
            test_download_template(driver, contest_id)
        else:
            # Fetch player IDs from API
            player_ids = fetch_player_ids_for_contest(contest_id)

            # Create test CSV
            csv_path = create_test_csv(contest_id, player_ids)

            # Test full upload flow
            success = test_upload_flow(driver, contest_id, csv_path)

            if success:
                logger.info("✓ Upload flow test completed successfully")
            else:
                logger.error("✗ Upload flow test failed")

        # Keep browser open for inspection
        logger.info("Press Enter to close browser...")
        input()

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
