#!/usr/bin/env python3
"""Debug script to test submission flow step by step."""
import logging
import pickle
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

COOKIES_FILE = Path("data/.yahoo_cookies.pkl")
CONTEST_ID = "15279104"  # Free contest for testing


def create_driver(headless: bool = False) -> webdriver.Chrome:
    """Create Chrome driver."""
    options = Options()
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    driver.implicitly_wait(5)

    return driver


def load_cookies(driver: webdriver.Chrome) -> bool:
    """Load cookies directly to sports.yahoo.com (skip yahoo.com)."""
    if not COOKIES_FILE.exists():
        logger.error("No cookies file found")
        return False

    with open(COOKIES_FILE, "rb") as f:
        data = pickle.load(f)

    logger.info(f"Loaded {len(data['cookies'])} cookies saved at {data['saved_at']}")

    # Navigate to sports.yahoo.com first (NOT yahoo.com)
    logger.info("Step 1: Navigating to sports.yahoo.com/dailyfantasy...")
    driver.get("https://sports.yahoo.com/dailyfantasy")
    time.sleep(2)

    # Add cookies
    logger.info("Step 2: Adding cookies...")
    for cookie in data["cookies"]:
        try:
            cookie.pop("sameSite", None)
            cookie.pop("expiry", None)
            driver.add_cookie(cookie)
        except Exception as e:
            logger.debug(f"Failed to add cookie: {e}")

    # Refresh to apply cookies
    logger.info("Step 3: Refreshing page to apply cookies...")
    driver.refresh()
    time.sleep(3)

    return True


def verify_login(driver: webdriver.Chrome) -> bool:
    """Check if we're logged in."""
    logger.info("Step 4: Verifying login status...")
    try:
        wait = WebDriverWait(driver, 10)
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-rapid_p='Account']")),
                EC.presence_of_element_located((By.ID, "ybarAccountMenu")),
            )
        )
        logger.info("SUCCESS: Logged in!")
        return True
    except:
        logger.warning("Not logged in or login indicator not found")
        return False


def navigate_to_contest(driver: webdriver.Chrome, contest_id: str) -> bool:
    """Navigate directly to the contest setlineup page."""
    url = f"https://sports.yahoo.com/dailyfantasy/contest/{contest_id}/setlineup"
    logger.info(f"Step 5: Navigating to {url}...")

    driver.get(url)
    time.sleep(3)

    # Check current URL
    current_url = driver.current_url
    logger.info(f"Current URL: {current_url}")

    # Take screenshot
    driver.save_screenshot("data/screenshots/debug_contest_page.png")
    logger.info("Screenshot saved to data/screenshots/debug_contest_page.png")

    return True


def find_upload_link(driver: webdriver.Chrome) -> bool:
    """Try to find the 'Upload Lineups from CSV' link."""
    logger.info("Step 6: Looking for 'Upload Lineups from CSV' link...")

    selectors = [
        (By.XPATH, "//a[contains(text(), 'Upload Lineups from CSV')]"),
        (By.XPATH, "//span[contains(text(), 'Upload Lineups from CSV')]"),
        (By.XPATH, "//*[contains(text(), 'Upload') and contains(text(), 'CSV')]"),
        (By.XPATH, "//a[contains(@href, 'upload')]"),
        (By.CSS_SELECTOR, "[data-tst*='upload']"),
    ]

    for by, selector in selectors:
        try:
            elements = driver.find_elements(by, selector)
            if elements:
                for elem in elements:
                    if elem.is_displayed():
                        logger.info(f"FOUND with {selector}: '{elem.text}' at {elem.location}")
                        # Highlight element
                        driver.execute_script("arguments[0].style.border='3px solid red'", elem)
                        return True
        except Exception as e:
            logger.debug(f"Selector {selector} failed: {e}")

    # If not found, list all links and buttons on page
    logger.info("Upload link not found. Listing all links on page...")
    links = driver.find_elements(By.TAG_NAME, "a")
    for link in links:
        try:
            text = link.text.strip()
            href = link.get_attribute("href") or ""
            if text or "upload" in href.lower():
                logger.info(f"  Link: '{text}' -> {href[:80]}")
        except:
            pass

    logger.info("Listing all buttons on page...")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        try:
            text = btn.text.strip()
            if text:
                logger.info(f"  Button: '{text}'")
        except:
            pass

    return False


def main():
    """Run debug flow."""
    logger.info("=" * 60)
    logger.info("SUBMISSION DEBUG FLOW")
    logger.info("=" * 60)

    driver = create_driver(headless=False)

    try:
        # Load cookies and verify
        if not load_cookies(driver):
            return

        if not verify_login(driver):
            logger.error("Login verification failed")
            input("Press Enter to continue anyway...")

        # Navigate to contest
        navigate_to_contest(driver, CONTEST_ID)

        # Try to find upload link
        find_upload_link(driver)

        # Wait for user inspection
        logger.info("=" * 60)
        logger.info("Browser is ready for inspection.")
        logger.info("Check the contest page and look for 'Upload Lineups from CSV'")
        logger.info("=" * 60)
        input("Press Enter to close browser...")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
