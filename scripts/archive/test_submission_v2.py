#!/usr/bin/env python3
"""Full submission test - generates fresh lineup with player_game_code."""
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
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

COOKIES_FILE = Path("data/.yahoo_cookies.pkl")
SCREENSHOTS_DIR = Path("data/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# NBA contest with $0.50 entry fee
CONTEST_ID = "15283303"
SPORT = "NBA"


def save_screenshot(driver, name: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{name}_{timestamp}.png"
    driver.save_screenshot(str(path))
    logger.info(f"Screenshot: {path}")
    return str(path)


def create_driver(headless: bool = False) -> webdriver.Chrome:
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
    if not COOKIES_FILE.exists():
        logger.error("No cookies file found")
        return False

    with open(COOKIES_FILE, "rb") as f:
        data = pickle.load(f)

    logger.info(f"Loaded {len(data['cookies'])} cookies")
    driver.get("https://sports.yahoo.com/dailyfantasy")
    time.sleep(2)

    for cookie in data["cookies"]:
        try:
            cookie.pop("sameSite", None)
            cookie.pop("expiry", None)
            driver.add_cookie(cookie)
        except:
            pass

    driver.refresh()
    time.sleep(3)

    try:
        wait = WebDriverWait(driver, 10)
        wait.until(EC.any_of(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-rapid_p='Account']")),
            EC.presence_of_element_located((By.ID, "ybarAccountMenu")),
        ))
        logger.info("Login verified!")
        return True
    except:
        logger.warning("Login verification failed")
        save_screenshot(driver, "login_failed")
        return False


def generate_fresh_lineup_csv() -> Path:
    """Generate a fresh lineup with correct player_game_code."""
    from src.yahoo.players import PlayerPoolFetcher
    from src.projections.sources.dailyfantasyfuel import DailyFantasyFuelSource
    from src.projections.transformer import transform_and_merge
    from src.optimizer.builder import LineupBuilder
    from src.common.models import Sport
    from src.yahoo.submission import ROSTER_POSITION_ORDER
    import csv

    logger.info("=== GENERATING FRESH LINEUP ===")

    # 1. Load players from pool (has player_game_code)
    fetcher = PlayerPoolFetcher()
    players = fetcher.get_player_pool_from_db(CONTEST_ID)
    logger.info(f"Loaded {len(players)} players from pool")

    # Verify player_game_code
    with_code = sum(1 for p in players if p.player_game_code)
    logger.info(f"Players with player_game_code: {with_code}/{len(players)}")

    # 2. Get projections
    source = DailyFantasyFuelSource()
    projections = source.fetch_projections(Sport.NBA)
    logger.info(f"Fetched {len(projections)} projections")

    # 3. Merge projections into players (preserves player_game_code)
    merged_players = transform_and_merge(projections, players)
    with_proj = sum(1 for p in merged_players if p.projected_points and p.projected_points > 0)
    logger.info(f"Players with projections: {with_proj}/{len(merged_players)}")

    # 4. Generate one lineup (don't save to DB, we'll create CSV directly)
    builder = LineupBuilder(Sport.NBA)
    lineups = builder.build_lineups(
        players=merged_players,
        num_lineups=1,
        contest_id=CONTEST_ID,
        save_to_db=False,  # Don't save - just generate
    )

    if not lineups:
        logger.error("Failed to generate lineup")
        return None

    lineup = lineups[0]
    logger.info(f"Generated lineup with {len(lineup.players)} players, projected: {lineup.projected_points:.1f}")

    # Verify player_game_code in lineup
    for p in lineup.players:
        logger.info(f"  {p.roster_position}: {p.name} -> {p.player_game_code}")

    # 5. Generate CSV
    positions = ROSTER_POSITION_ORDER.get(SPORT, [])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"data/lineups/fresh_upload_{CONTEST_ID}_{timestamp}.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # Build position to players mapping
    pos_players = {}
    for p in lineup.players:
        pos = p.roster_position
        if pos not in pos_players:
            pos_players[pos] = []
        pos_players[pos].append(p)

    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(positions)

        row = []
        pos_used = {}
        for pos in positions:
            idx = pos_used.get(pos, 0)
            if pos in pos_players and idx < len(pos_players[pos]):
                player = pos_players[pos][idx]
                code = player.player_game_code
                if not code:
                    logger.error(f"Missing player_game_code for {player.name}!")
                    code = player.yahoo_player_id
                row.append(code)
                pos_used[pos] = idx + 1
            else:
                row.append("")

        writer.writerow(row)

    logger.info(f"Generated CSV: {csv_path}")
    with open(csv_path) as f:
        logger.info(f"CSV contents:\n{f.read()}")

    return csv_path


def navigate_to_setlineup(driver, contest_id: str) -> bool:
    url = f"https://sports.yahoo.com/dailyfantasy/contest/{contest_id}/setlineup"
    logger.info(f"Navigating to: {url}")
    driver.get(url)
    time.sleep(3)
    logger.info(f"Current URL: {driver.current_url}")
    save_screenshot(driver, "setlineup_page")
    return True


def find_and_click_upload_link(driver) -> bool:
    logger.info("Looking for 'Upload Lineups from CSV' link...")
    wait = WebDriverWait(driver, 15)

    selectors = [
        (By.XPATH, "//a[contains(text(), 'Upload Lineups from CSV')]"),
        (By.XPATH, "//span[contains(text(), 'Upload Lineups from CSV')]"),
        (By.XPATH, "//a[contains(., 'Upload Lineups from CSV')]"),
        (By.PARTIAL_LINK_TEXT, "Upload"),
    ]

    for by, selector in selectors:
        try:
            element = wait.until(EC.element_to_be_clickable((by, selector)))
            if element and element.is_displayed():
                logger.info(f"Found element: '{element.text}'")
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", element)
                logger.info("Clicked upload link!")
                time.sleep(2)
                save_screenshot(driver, "after_upload_click")
                return True
        except:
            continue

    # Try clicking span's parent
    try:
        spans = driver.find_elements(By.TAG_NAME, "span")
        for span in spans:
            text = span.text.strip()
            if "Upload Lineups from CSV" in text:
                parent = span.find_element(By.XPATH, "..")
                driver.execute_script("arguments[0].click();", parent)
                logger.info("Clicked parent of span!")
                time.sleep(2)
                save_screenshot(driver, "after_span_parent_click")
                return True
    except:
        pass

    save_screenshot(driver, "upload_link_not_found")
    return False


def upload_csv_file(driver, csv_path: Path) -> bool:
    logger.info(f"Uploading CSV: {csv_path}")

    file_input = None
    for selector in ["input[type='file'][accept='.csv']", "input[type='file']"]:
        try:
            file_input = driver.find_element(By.CSS_SELECTOR, selector)
            if file_input:
                break
        except:
            continue

    if not file_input:
        logger.error("File input not found!")
        save_screenshot(driver, "file_input_not_found")
        return False

    file_input.send_keys(str(csv_path.absolute()))
    time.sleep(3)
    save_screenshot(driver, "after_file_upload")

    # Check for validation errors
    try:
        errors = driver.find_elements(By.XPATH, "//*[contains(@class, 'error') and contains(text(), 'not in the contest')]")
        for err in errors:
            if err.is_displayed():
                logger.error(f"Validation error: {err.text}")
                return False
    except:
        pass

    return True


def click_upload_button(driver) -> bool:
    logger.info("Looking for Upload button...")

    # The button may be disabled initially, wait for it to become enabled
    wait = WebDriverWait(driver, 10)

    # First, look for detection message to confirm file was parsed
    try:
        detection = driver.find_element(By.XPATH, "//*[contains(text(), 'detected') or contains(text(), 'lineup')]")
        logger.info(f"Detection: {detection.text}")
    except:
        logger.warning("No detection message")

    # Find Upload button
    buttons = driver.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        try:
            text = btn.text.strip()
            if text == "Upload":
                if btn.is_enabled():
                    logger.info(f"Found enabled Upload button")
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(3)
                    save_screenshot(driver, "after_upload_button")
                    return True
                else:
                    logger.warning("Upload button is disabled - CSV may have errors")
                    save_screenshot(driver, "upload_button_disabled")
                    return False
        except:
            continue

    logger.error("Upload button not found")
    save_screenshot(driver, "upload_button_not_found")
    return False


def click_submit_confirmation(driver) -> bool:
    logger.info("Looking for Submit confirmation...")
    wait = WebDriverWait(driver, 15)

    # Wait for confirmation dialog
    try:
        wait.until(EC.any_of(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Submit your CSV')]")),
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'Entry Fees')]")),
        ))
        logger.info("Confirmation dialog detected!")
        save_screenshot(driver, "confirmation_dialog")
    except:
        logger.warning("Confirmation dialog not detected")

    # Find Submit button
    buttons = driver.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        try:
            text = btn.text.strip()
            if text == "Submit" and btn.is_displayed() and btn.is_enabled():
                logger.info("Found Submit button - clicking...")
                save_screenshot(driver, "before_final_submit")
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(5)
                save_screenshot(driver, "after_final_submit")
                return True
        except:
            continue

    logger.error("Submit button not found")
    save_screenshot(driver, "submit_not_found")
    return False


def verify_submission(driver) -> bool:
    logger.info("Verifying submission...")

    # Look for success indicators
    success_xpaths = [
        "//*[contains(text(), 'Success')]",
        "//*[contains(text(), 'success')]",
        "//*[contains(text(), 'submitted')]",
    ]

    for xpath in success_xpaths:
        try:
            elem = driver.find_element(By.XPATH, xpath)
            if elem.is_displayed():
                logger.info(f"SUCCESS: {elem.text}")
                save_screenshot(driver, "submission_success")
                return True
        except:
            continue

    # Check if modal closed
    try:
        WebDriverWait(driver, 5).until(
            EC.invisibility_of_element_located((By.CSS_SELECTOR, "[role='dialog']"))
        )
        logger.info("Modal closed - likely successful")
        save_screenshot(driver, "modal_closed")
        return True
    except:
        pass

    save_screenshot(driver, "submission_uncertain")
    return False


def main():
    logger.info("=" * 70)
    logger.info("SUBMISSION TEST v2 - Fresh lineup generation")
    logger.info(f"Contest: {CONTEST_ID}, Sport: {SPORT}, Entry: $0.50")
    logger.info("=" * 70)

    # Generate fresh lineup with correct player_game_code
    csv_path = generate_fresh_lineup_csv()
    if not csv_path:
        logger.error("Failed to generate lineup CSV")
        return False

    driver = create_driver(headless=False)

    try:
        # Step 1: Login
        logger.info("\n--- STEP 1: LOGIN ---")
        if not load_cookies_and_login(driver):
            return False

        # Step 2: Navigate
        logger.info("\n--- STEP 2: NAVIGATE ---")
        if not navigate_to_setlineup(driver, CONTEST_ID):
            return False

        # Step 3: Click upload link
        logger.info("\n--- STEP 3: CLICK UPLOAD LINK ---")
        if not find_and_click_upload_link(driver):
            return False

        # Step 4: Upload CSV
        logger.info("\n--- STEP 4: UPLOAD CSV ---")
        if not upload_csv_file(driver, csv_path):
            return False

        # Step 5: Click Upload button
        logger.info("\n--- STEP 5: CLICK UPLOAD BUTTON ---")
        if not click_upload_button(driver):
            return False

        # Step 6: Click Submit
        logger.info("\n--- STEP 6: CLICK SUBMIT ---")
        if not click_submit_confirmation(driver):
            return False

        # Step 7: Verify
        logger.info("\n--- STEP 7: VERIFY ---")
        success = verify_submission(driver)

        if success:
            logger.info("=" * 70)
            logger.info("SUBMISSION SUCCESSFUL!")
            logger.info("=" * 70)
        else:
            logger.warning("Could not verify submission")

        return success

    except Exception as e:
        logger.error(f"Error: {e}")
        save_screenshot(driver, "error")
        raise

    finally:
        logger.info("\nClosing browser in 5 seconds...")
        time.sleep(5)
        driver.quit()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
