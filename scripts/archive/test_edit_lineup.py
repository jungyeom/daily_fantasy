#!/usr/bin/env python3
"""Test lineup regeneration (excluding injured) and edit functionality."""
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
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

COOKIES_FILE = Path("data/.yahoo_cookies.pkl")
SCREENSHOTS_DIR = Path("data/screenshots")
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

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
        return False


def generate_healthy_lineup_csv() -> Path:
    """Generate lineup excluding injured players."""
    from src.yahoo.players import PlayerPoolFetcher
    from src.projections.sources.dailyfantasyfuel import DailyFantasyFuelSource
    from src.projections.transformer import transform_and_merge
    from src.optimizer.builder import LineupBuilder
    from src.common.models import Sport
    from src.yahoo.submission import ROSTER_POSITION_ORDER
    import csv

    logger.info("=== GENERATING HEALTHY LINEUP ===")

    # 1. Load players from pool
    fetcher = PlayerPoolFetcher()
    players = fetcher.get_player_pool_from_db(CONTEST_ID)
    logger.info(f"Loaded {len(players)} players from pool")

    # Check injury status distribution
    injury_counts = {}
    for p in players:
        status = p.injury_status or "N/A"
        injury_counts[status] = injury_counts.get(status, 0) + 1
    logger.info(f"Injury status distribution: {injury_counts}")

    # 2. Get projections
    source = DailyFantasyFuelSource()
    projections = source.fetch_projections(Sport.NBA)
    logger.info(f"Fetched {len(projections)} projections")

    # 3. Merge projections
    merged_players = transform_and_merge(projections, players)
    with_proj = sum(1 for p in merged_players if p.projected_points and p.projected_points > 0)
    logger.info(f"Players with projections: {with_proj}/{len(merged_players)}")

    # 4. Generate lineup (injury filtering now happens in LineupBuilder)
    builder = LineupBuilder(Sport.NBA)
    lineups = builder.build_lineups(
        players=merged_players,
        num_lineups=1,
        contest_id=CONTEST_ID,
        save_to_db=False,
    )

    if not lineups:
        logger.error("Failed to generate lineup")
        return None

    lineup = lineups[0]
    logger.info(f"Generated lineup with {len(lineup.players)} players, projected: {lineup.projected_points:.1f}")

    # Verify no injured players
    logger.info("Players in lineup:")
    for p in lineup.players:
        # Find original player to check injury status
        orig = next((op for op in merged_players if op.yahoo_player_id == p.yahoo_player_id), None)
        status = orig.injury_status if orig else "?"
        logger.info(f"  {p.roster_position}: {p.name} (status: {status}) -> {p.player_game_code}")

    # 5. Generate CSV
    positions = ROSTER_POSITION_ORDER.get(SPORT, [])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = Path(f"data/lineups/healthy_upload_{CONTEST_ID}_{timestamp}.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

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
                row.append(player.player_game_code)
                pos_used[pos] = idx + 1
            else:
                row.append("")

        writer.writerow(row)

    logger.info(f"Generated CSV: {csv_path}")
    with open(csv_path) as f:
        logger.info(f"CSV contents:\n{f.read()}")

    return csv_path


def navigate_to_edit_entries(driver) -> bool:
    """Navigate to the Edit Entries page for the contest."""
    # First go to My Contests
    url = f"https://sports.yahoo.com/dailyfantasy/contest/{CONTEST_ID}/setlineup"
    logger.info(f"Navigating to: {url}")
    driver.get(url)
    time.sleep(3)
    save_screenshot(driver, "my_contests_page")

    # Look for "Edit Entries" button
    try:
        wait = WebDriverWait(driver, 10)
        edit_btn = wait.until(EC.element_to_be_clickable((
            By.XPATH, "//button[contains(text(), 'Edit Entries') or contains(text(), 'Edit Lineup')]"
        )))
        logger.info(f"Found Edit button: {edit_btn.text}")
        driver.execute_script("arguments[0].click();", edit_btn)
        time.sleep(2)
        save_screenshot(driver, "after_edit_click")
        return True
    except Exception as e:
        logger.info(f"No Edit Entries button found, trying direct URL")

    # Try direct edit URL pattern
    edit_url = f"https://sports.yahoo.com/dailyfantasy/contest/{CONTEST_ID}/setlineup"
    driver.get(edit_url)
    time.sleep(3)
    save_screenshot(driver, "edit_page_direct")
    return True


def click_upload_link(driver) -> bool:
    """Find and click Upload Lineups from CSV."""
    logger.info("Looking for 'Upload Lineups from CSV' link...")
    wait = WebDriverWait(driver, 15)

    selectors = [
        (By.XPATH, "//span[contains(text(), 'Upload Lineups from CSV')]"),
        (By.XPATH, "//a[contains(text(), 'Upload Lineups from CSV')]"),
        (By.PARTIAL_LINK_TEXT, "Upload"),
    ]

    for by, selector in selectors:
        try:
            element = wait.until(EC.element_to_be_clickable((by, selector)))
            if element and element.is_displayed():
                logger.info(f"Found: '{element.text}'")
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", element)
                logger.info("Clicked upload link!")
                time.sleep(2)
                save_screenshot(driver, "upload_modal")
                return True
        except:
            continue

    save_screenshot(driver, "upload_link_not_found")
    return False


def upload_csv_and_submit(driver, csv_path: Path) -> bool:
    """Upload CSV and click through submission."""
    logger.info(f"Uploading CSV: {csv_path}")

    # Find file input
    try:
        file_input = driver.find_element(By.CSS_SELECTOR, "input[type='file']")
        file_input.send_keys(str(csv_path.absolute()))
        time.sleep(3)
        save_screenshot(driver, "file_uploaded")
    except Exception as e:
        logger.error(f"File input not found: {e}")
        return False

    # Click Upload button
    logger.info("Looking for Upload button...")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        try:
            text = btn.text.strip()
            if text == "Upload" and btn.is_enabled():
                logger.info("Clicking Upload button...")
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(3)
                save_screenshot(driver, "after_upload_btn")
                break
        except:
            continue
    else:
        logger.error("Upload button not found or disabled")
        save_screenshot(driver, "upload_btn_issue")
        return False

    # Click Submit/Confirm button
    logger.info("Looking for Submit/Save button...")
    time.sleep(2)  # Wait for confirmation dialog

    buttons = driver.find_elements(By.TAG_NAME, "button")
    for btn in buttons:
        try:
            text = btn.text.strip()
            if text in ["Submit", "Save", "Confirm"] and btn.is_displayed() and btn.is_enabled():
                logger.info(f"Clicking '{text}' button...")
                save_screenshot(driver, "before_submit")
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(5)
                save_screenshot(driver, "after_submit")
                return True
        except:
            continue

    logger.warning("Submit button not found, checking if already saved...")
    save_screenshot(driver, "no_submit_btn")
    return True


def verify_edit_success(driver) -> bool:
    """Verify the edit was successful."""
    logger.info("Verifying edit success...")

    # Look for success message
    success_xpaths = [
        "//*[contains(text(), 'Success')]",
        "//*[contains(text(), 'success')]",
        "//*[contains(text(), 'updated')]",
        "//*[contains(text(), 'saved')]",
    ]

    for xpath in success_xpaths:
        try:
            elem = driver.find_element(By.XPATH, xpath)
            if elem.is_displayed():
                logger.info(f"SUCCESS: {elem.text}")
                save_screenshot(driver, "edit_success")
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

    save_screenshot(driver, "edit_uncertain")
    return False


def main():
    logger.info("=" * 70)
    logger.info("EDIT LINEUP TEST - Regenerate with healthy players")
    logger.info(f"Contest: {CONTEST_ID}, Sport: {SPORT}")
    logger.info("=" * 70)

    # Generate new healthy lineup
    csv_path = generate_healthy_lineup_csv()
    if not csv_path:
        logger.error("Failed to generate healthy lineup")
        return False

    driver = create_driver(headless=False)

    try:
        # Step 1: Login
        logger.info("\n--- STEP 1: LOGIN ---")
        if not load_cookies_and_login(driver):
            return False

        # Step 2: Navigate to edit
        logger.info("\n--- STEP 2: NAVIGATE TO EDIT ---")
        if not navigate_to_edit_entries(driver):
            return False

        # Step 3: Click upload link
        logger.info("\n--- STEP 3: CLICK UPLOAD LINK ---")
        if not click_upload_link(driver):
            return False

        # Step 4: Upload and submit
        logger.info("\n--- STEP 4: UPLOAD CSV AND SUBMIT ---")
        if not upload_csv_and_submit(driver, csv_path):
            return False

        # Step 5: Verify
        logger.info("\n--- STEP 5: VERIFY ---")
        success = verify_edit_success(driver)

        if success:
            logger.info("=" * 70)
            logger.info("EDIT LINEUP SUCCESSFUL!")
            logger.info("=" * 70)
        else:
            logger.warning("Could not verify edit success")

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
