#!/usr/bin/env python3
"""Full submission test with debugging and automatic error handling."""
import logging
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

COOKIES_FILE = Path("data/.yahoo_cookies.pkl")
SCREENSHOTS_DIR = Path("data/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# NBA contest with $0.50 entry fee and 8 lineups
CONTEST_ID = "15283303"
SPORT = "NBA"


def save_screenshot(driver, name: str) -> str:
    """Save screenshot with timestamp."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{name}_{timestamp}.png"
    driver.save_screenshot(str(path))
    logger.info(f"Screenshot: {path}")
    return str(path)


def save_page_source(driver, name: str) -> str:
    """Save HTML source for debugging."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{name}_{timestamp}.html"
    with open(path, "w") as f:
        f.write(driver.page_source)
    logger.info(f"Page source: {path}")
    return str(path)


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


def load_cookies_and_login(driver) -> bool:
    """Load cookies and verify login."""
    if not COOKIES_FILE.exists():
        logger.error("No cookies file found")
        return False

    with open(COOKIES_FILE, "rb") as f:
        data = pickle.load(f)

    logger.info(f"Loaded {len(data['cookies'])} cookies")

    # Go directly to DFS page
    logger.info("Navigating to sports.yahoo.com/dailyfantasy...")
    driver.get("https://sports.yahoo.com/dailyfantasy")
    time.sleep(2)

    # Add cookies
    for cookie in data["cookies"]:
        try:
            cookie.pop("sameSite", None)
            cookie.pop("expiry", None)
            driver.add_cookie(cookie)
        except:
            pass

    # Refresh
    driver.refresh()
    time.sleep(3)

    # Verify login
    try:
        wait = WebDriverWait(driver, 10)
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-rapid_p='Account']")),
                EC.presence_of_element_located((By.ID, "ybarAccountMenu")),
            )
        )
        logger.info("Login verified!")
        return True
    except:
        logger.warning("Login verification failed")
        save_screenshot(driver, "login_failed")
        return False


def get_lineup_csv(contest_id: str) -> Path:
    """Get or generate CSV for lineup submission."""
    from src.common.database import get_database, LineupDB, LineupPlayerDB

    db = get_database()
    session = db.get_session()

    # Get one lineup for this contest
    lineup = session.query(LineupDB).filter_by(contest_id=contest_id).first()
    if not lineup:
        logger.error(f"No lineup found for contest {contest_id}")
        return None

    # Get players
    players = session.query(LineupPlayerDB).filter_by(lineup_id=lineup.id).all()
    logger.info(f"Found lineup {lineup.id} with {len(players)} players, projected: {lineup.projected_points}")

    # Generate CSV
    from src.yahoo.submission import ROSTER_POSITION_ORDER

    positions = ROSTER_POSITION_ORDER.get(SPORT, [])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"data/lineups/test_upload_{contest_id}_{timestamp}.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Build position to players mapping
    pos_players = {}
    for p in players:
        pos = p.roster_position
        if pos not in pos_players:
            pos_players[pos] = []
        pos_players[pos].append(p)

    # Write CSV
    import csv
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(positions)  # Header

        row = []
        pos_used = {}
        for pos in positions:
            idx = pos_used.get(pos, 0)
            if pos in pos_players and idx < len(pos_players[pos]):
                player = pos_players[pos][idx]
                row.append(player.player_game_code or player.yahoo_player_id)
                pos_used[pos] = idx + 1
            else:
                row.append("")

        writer.writerow(row)

    logger.info(f"Generated CSV: {csv_path}")

    # Print CSV contents
    with open(csv_path) as f:
        logger.info(f"CSV contents:\n{f.read()}")

    session.close()
    return csv_path


def navigate_to_setlineup(driver, contest_id: str) -> bool:
    """Navigate to contest setlineup page."""
    url = f"https://sports.yahoo.com/dailyfantasy/contest/{contest_id}/setlineup"
    logger.info(f"Navigating to: {url}")

    driver.get(url)
    time.sleep(3)

    current_url = driver.current_url
    logger.info(f"Current URL: {current_url}")

    save_screenshot(driver, "setlineup_page")

    # Check if we're on the right page
    if "setlineup" in current_url or contest_id in current_url:
        return True

    logger.warning(f"May have been redirected. Current URL: {current_url}")
    return True  # Continue anyway


def find_and_click_upload_link(driver) -> bool:
    """Find and click 'Upload Lineups from CSV' link."""
    logger.info("Looking for 'Upload Lineups from CSV' link...")

    wait = WebDriverWait(driver, 15)

    # Try multiple selector strategies
    selectors = [
        # Text-based XPath
        (By.XPATH, "//a[contains(text(), 'Upload Lineups from CSV')]"),
        (By.XPATH, "//span[contains(text(), 'Upload Lineups from CSV')]"),
        (By.XPATH, "//a[contains(., 'Upload Lineups from CSV')]"),
        (By.XPATH, "//*[contains(text(), 'Upload') and contains(text(), 'CSV')]"),
        # Partial text
        (By.XPATH, "//a[contains(text(), 'Upload')]"),
        (By.XPATH, "//a[contains(text(), 'CSV')]"),
        # Link-based
        (By.PARTIAL_LINK_TEXT, "Upload"),
        (By.PARTIAL_LINK_TEXT, "CSV"),
    ]

    for by, selector in selectors:
        try:
            element = wait.until(EC.element_to_be_clickable((by, selector)))
            if element and element.is_displayed():
                text = element.text.strip()
                logger.info(f"Found element with selector '{selector}': '{text}'")

                # Scroll into view and click
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                time.sleep(0.5)

                try:
                    driver.execute_script("arguments[0].click();", element)
                except:
                    element.click()

                logger.info("Clicked upload link!")
                time.sleep(2)
                save_screenshot(driver, "after_upload_click")
                return True
        except TimeoutException:
            continue
        except Exception as e:
            logger.debug(f"Selector {selector} failed: {e}")
            continue

    # If not found, let's inspect the page
    logger.warning("Upload link not found with standard selectors. Inspecting page...")
    save_screenshot(driver, "upload_link_not_found")
    save_page_source(driver, "upload_link_not_found")

    # List all links
    logger.info("All links on page:")
    links = driver.find_elements(By.TAG_NAME, "a")
    for link in links:
        try:
            text = link.text.strip()
            href = link.get_attribute("href") or ""
            if text:
                logger.info(f"  <a>: '{text}' -> {href[:60]}")
        except:
            pass

    # List all spans that might contain the text
    logger.info("Spans containing 'Upload' or 'CSV':")
    spans = driver.find_elements(By.TAG_NAME, "span")
    for span in spans:
        try:
            text = span.text.strip()
            if "upload" in text.lower() or "csv" in text.lower():
                logger.info(f"  <span>: '{text}'")
                # Try clicking the parent
                parent = span.find_element(By.XPATH, "..")
                if parent.tag_name == "a":
                    logger.info("  -> Found parent <a>, clicking...")
                    driver.execute_script("arguments[0].click();", parent)
                    time.sleep(2)
                    save_screenshot(driver, "clicked_parent_link")
                    return True
        except:
            pass

    return False


def upload_csv_file(driver, csv_path: Path) -> bool:
    """Upload CSV file in the modal."""
    logger.info(f"Looking for file input to upload {csv_path}...")

    wait = WebDriverWait(driver, 10)

    # Find file input
    file_input_selectors = [
        (By.CSS_SELECTOR, "input[type='file'][accept='.csv']"),
        (By.CSS_SELECTOR, "input[type='file']"),
        (By.XPATH, "//input[@type='file']"),
    ]

    file_input = None
    for by, selector in file_input_selectors:
        try:
            file_input = driver.find_element(by, selector)
            if file_input:
                logger.info(f"Found file input with selector: {selector}")
                break
        except:
            continue

    if not file_input:
        logger.error("File input not found!")
        save_screenshot(driver, "file_input_not_found")
        save_page_source(driver, "file_input_not_found")
        return False

    # Upload file
    logger.info(f"Uploading: {csv_path.absolute()}")
    file_input.send_keys(str(csv_path.absolute()))
    time.sleep(3)

    save_screenshot(driver, "after_file_upload")

    # Check for validation errors
    try:
        errors = driver.find_elements(By.XPATH, "//*[contains(@class, 'error') or contains(text(), 'Invalid') or contains(text(), 'Error')]")
        for err in errors:
            if err.is_displayed() and err.text.strip():
                logger.error(f"Validation error: {err.text}")
                return False
    except:
        pass

    # Look for detection message
    try:
        detection = driver.find_element(By.XPATH, "//*[contains(text(), 'detected') or contains(text(), 'lineup')]")
        logger.info(f"Detection message: {detection.text}")
    except:
        logger.warning("No detection message found")

    return True


def click_upload_button(driver) -> bool:
    """Click the Upload button in the modal."""
    logger.info("Looking for Upload button...")

    wait = WebDriverWait(driver, 10)

    # Try to find Upload button
    upload_btn_selectors = [
        (By.XPATH, "//button[.//span[text()='Upload']]"),
        (By.XPATH, "//button[text()='Upload']"),
        (By.XPATH, "//button[contains(text(), 'Upload')]"),
        (By.XPATH, "//button[contains(@class, 'primary')]"),
    ]

    for by, selector in upload_btn_selectors:
        try:
            btn = driver.find_element(by, selector)
            if btn and btn.is_displayed() and btn.is_enabled():
                text = btn.text.strip()
                logger.info(f"Found button '{text}' with selector: {selector}")

                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", btn)

                logger.info("Clicked Upload button!")
                time.sleep(3)
                save_screenshot(driver, "after_upload_button")
                return True
        except:
            continue

    # Fallback: find all buttons and look for Upload
    logger.info("Trying fallback button search...")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        try:
            text = btn.text.strip()
            if text == "Upload" and btn.is_displayed() and btn.is_enabled():
                logger.info(f"Found Upload button via fallback: '{text}'")
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)
                save_screenshot(driver, "after_upload_button_fallback")
                return True
        except:
            continue

    logger.error("Upload button not found!")
    save_screenshot(driver, "upload_button_not_found")

    # List all buttons
    logger.info("All buttons on page:")
    for btn in buttons:
        try:
            text = btn.text.strip()
            if text:
                logger.info(f"  <button>: '{text}' enabled={btn.is_enabled()} displayed={btn.is_displayed()}")
        except:
            pass

    return False


def click_submit_confirmation(driver) -> bool:
    """Click the Submit button in the payment confirmation dialog."""
    logger.info("Looking for Submit confirmation button...")

    wait = WebDriverWait(driver, 15)

    # Wait for confirmation dialog
    try:
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Submit your CSV')]")),
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Payment method')]")),
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Entry Fees')]")),
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Fantasy Wallet')]")),
            )
        )
        logger.info("Payment confirmation dialog detected!")
        save_screenshot(driver, "payment_dialog")
    except:
        logger.warning("Payment dialog not detected, continuing anyway...")

    # Find Submit button
    submit_btn_selectors = [
        (By.XPATH, "//button[text()='Submit']"),
        (By.XPATH, "//button[.//span[text()='Submit']]"),
        (By.XPATH, "//button[contains(text(), 'Submit')]"),
        (By.XPATH, "//ul/li[1]/button"),  # First button in list pattern
    ]

    for by, selector in submit_btn_selectors:
        try:
            btn = wait.until(EC.element_to_be_clickable((by, selector)))
            if btn and btn.is_displayed():
                text = btn.text.strip()
                logger.info(f"Found Submit button '{text}' with selector: {selector}")

                save_screenshot(driver, "before_final_submit")

                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", btn)

                logger.info("Clicked Submit confirmation!")
                time.sleep(5)
                save_screenshot(driver, "after_final_submit")
                return True
        except:
            continue

    # Fallback
    logger.info("Trying fallback for Submit button...")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        try:
            text = btn.text.strip()
            if text == "Submit" and btn.is_displayed() and btn.is_enabled():
                logger.info(f"Found Submit button via fallback")
                save_screenshot(driver, "before_final_submit_fallback")
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(5)
                save_screenshot(driver, "after_final_submit_fallback")
                return True
        except:
            continue

    logger.error("Submit confirmation button not found!")
    save_screenshot(driver, "submit_button_not_found")
    return False


def verify_submission(driver) -> bool:
    """Verify submission was successful."""
    logger.info("Verifying submission...")

    # Look for success indicators
    success_indicators = [
        "//*[contains(text(), 'Success')]",
        "//*[contains(text(), 'success')]",
        "//*[contains(text(), 'submitted')]",
        "//*[contains(text(), 'Submitted')]",
        "//*[contains(text(), 'complete')]",
        "//*[contains(text(), 'confirmed')]",
    ]

    for xpath in success_indicators:
        try:
            elem = driver.find_element(By.XPATH, xpath)
            if elem.is_displayed():
                logger.info(f"SUCCESS INDICATOR: {elem.text}")
                save_screenshot(driver, "submission_success")
                return True
        except:
            continue

    # Check if modal closed (another success indicator)
    try:
        WebDriverWait(driver, 5).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, "[role='dialog']"))
        )
        logger.info("Modal closed - likely successful")
        save_screenshot(driver, "modal_closed_success")
        return True
    except:
        pass

    logger.warning("Could not confirm submission success")
    save_screenshot(driver, "submission_uncertain")
    return False


def main():
    """Run full submission test."""
    logger.info("=" * 70)
    logger.info("FULL SUBMISSION TEST")
    logger.info(f"Contest: {CONTEST_ID}, Sport: {SPORT}")
    logger.info("=" * 70)

    # Generate CSV first
    csv_path = get_lineup_csv(CONTEST_ID)
    if not csv_path:
        logger.error("Failed to generate CSV")
        return False

    driver = create_driver(headless=False)

    try:
        # Step 1: Login
        logger.info("\n--- STEP 1: LOGIN ---")
        if not load_cookies_and_login(driver):
            logger.error("Login failed!")
            return False

        # Step 2: Navigate to contest
        logger.info("\n--- STEP 2: NAVIGATE TO CONTEST ---")
        if not navigate_to_setlineup(driver, CONTEST_ID):
            logger.error("Navigation failed!")
            return False

        # Step 3: Find and click upload link
        logger.info("\n--- STEP 3: CLICK UPLOAD LINK ---")
        if not find_and_click_upload_link(driver):
            logger.error("Could not find/click upload link!")
            return False

        # Step 4: Upload CSV
        logger.info("\n--- STEP 4: UPLOAD CSV FILE ---")
        if not upload_csv_file(driver, csv_path):
            logger.error("CSV upload failed!")
            return False

        # Step 5: Click Upload button
        logger.info("\n--- STEP 5: CLICK UPLOAD BUTTON ---")
        if not click_upload_button(driver):
            logger.error("Could not click Upload button!")
            return False

        # Step 6: Click Submit confirmation
        logger.info("\n--- STEP 6: CLICK SUBMIT CONFIRMATION ---")
        if not click_submit_confirmation(driver):
            logger.error("Could not click Submit confirmation!")
            return False

        # Step 7: Verify
        logger.info("\n--- STEP 7: VERIFY SUBMISSION ---")
        success = verify_submission(driver)

        if success:
            logger.info("=" * 70)
            logger.info("SUBMISSION SUCCESSFUL!")
            logger.info("=" * 70)
        else:
            logger.warning("Submission may have succeeded but could not verify")

        return success

    except Exception as e:
        logger.error(f"Test failed with exception: {e}")
        save_screenshot(driver, "exception_error")
        save_page_source(driver, "exception_error")
        raise

    finally:
        logger.info("\nTest complete. Closing browser in 5 seconds...")
        time.sleep(5)
        driver.quit()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
