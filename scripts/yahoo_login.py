#!/usr/bin/env python3
"""Interactive Yahoo login script for initial authentication.

This script opens a browser window for you to manually log in to Yahoo.
Once logged in, it saves cookies for future automated sessions.

Usage:
    uv run python scripts/yahoo_login.py

The script will:
1. Open Chrome browser to Yahoo login page
2. Wait for you to complete login (including any 2FA/CAPTCHA)
3. Detect when you're logged in
4. Save session cookies for future use
5. Test the saved session
"""
import pickle
import sys
import time
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Cookie storage location
COOKIES_PATH = Path("data/.yahoo_cookies.pkl")


def create_driver(headless: bool = False) -> webdriver.Chrome:
    """Create a Chrome WebDriver instance."""
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")

    # Use a persistent user data directory for better session handling
    user_data_dir = Path("data/.chrome_profile")
    user_data_dir.mkdir(parents=True, exist_ok=True)
    options.add_argument(f"--user-data-dir={user_data_dir.absolute()}")

    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def save_cookies(driver: webdriver.Chrome) -> None:
    """Save browser cookies to file."""
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    cookies = driver.get_cookies()

    with open(COOKIES_PATH, "wb") as f:
        pickle.dump({
            "cookies": cookies,
            "saved_at": datetime.utcnow().isoformat(),
        }, f)

    print(f"\n✓ Saved {len(cookies)} cookies to {COOKIES_PATH}")


def check_logged_in(driver: webdriver.Chrome, timeout: int = 5) -> bool:
    """Check if user is logged in to Yahoo."""
    try:
        wait = WebDriverWait(driver, timeout)
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-rapid_p='Account']")),
                EC.presence_of_element_located((By.ID, "ybarAccountMenu")),
                EC.presence_of_element_located((By.CSS_SELECTOR, ".user-account")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-tst='user-profile']")),
            )
        )
        return True
    except:
        return False


def interactive_login() -> bool:
    """Perform interactive login to Yahoo."""
    print("=" * 60)
    print("Yahoo Interactive Login")
    print("=" * 60)
    print()
    print("This will open a browser window for you to log in to Yahoo.")
    print("Please complete the login process (including any 2FA prompts).")
    print()

    driver = None
    try:
        print("Starting Chrome browser...")
        driver = create_driver(headless=False)

        # Navigate to Yahoo login
        print("Navigating to Yahoo login page...")
        driver.get("https://login.yahoo.com")

        print()
        print("=" * 60)
        print("Please log in to Yahoo in the browser window.")
        print("The script will automatically detect when you're logged in.")
        print("=" * 60)
        print()

        # Poll for login success
        max_wait_minutes = 5
        check_interval = 2
        max_checks = (max_wait_minutes * 60) // check_interval

        for i in range(max_checks):
            # Check current URL and login status
            current_url = driver.current_url

            # If redirected away from login page, check if logged in
            if "login.yahoo.com" not in current_url:
                # Navigate to DFS page to verify
                driver.get("https://sports.yahoo.com/dailyfantasy")
                time.sleep(2)

                if check_logged_in(driver):
                    print("\n✓ Login detected!")
                    save_cookies(driver)
                    return True

            # Show progress
            remaining = max_wait_minutes - (i * check_interval // 60)
            print(f"\rWaiting for login... ({remaining} min remaining)", end="", flush=True)
            time.sleep(check_interval)

        print("\n\n✗ Login timeout - please try again")
        return False

    except KeyboardInterrupt:
        print("\n\nLogin cancelled by user")
        return False
    except Exception as e:
        print(f"\n\n✗ Error during login: {e}")
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


def test_saved_session() -> bool:
    """Test if saved session is still valid."""
    if not COOKIES_PATH.exists():
        print("No saved cookies found")
        return False

    print("\nTesting saved session...")
    driver = None

    try:
        driver = create_driver(headless=True)

        # Load cookies
        with open(COOKIES_PATH, "rb") as f:
            data = pickle.load(f)

        # Go to Yahoo first (required to set cookies)
        driver.get("https://www.yahoo.com")

        # Restore cookies
        for cookie in data["cookies"]:
            try:
                cookie.pop("sameSite", None)
                cookie.pop("expiry", None)
                driver.add_cookie(cookie)
            except:
                pass

        # Navigate to DFS and check login status
        driver.get("https://sports.yahoo.com/dailyfantasy")
        time.sleep(2)

        if check_logged_in(driver):
            print("✓ Saved session is valid!")

            # Get username if possible
            try:
                profile = driver.find_element(By.CSS_SELECTOR, "[data-rapid_p='Account']")
                print(f"  Logged in as: {profile.text}")
            except:
                pass

            return True
        else:
            print("✗ Saved session is expired or invalid")
            return False

    except Exception as e:
        print(f"✗ Error testing session: {e}")
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Yahoo login for Daily Fantasy automation")
    parser.add_argument("--test", action="store_true", help="Test saved session only")
    parser.add_argument("--force", action="store_true", help="Force new login even if session exists")
    args = parser.parse_args()

    if args.test:
        success = test_saved_session()
        sys.exit(0 if success else 1)

    # Check if we already have a valid session
    if not args.force and COOKIES_PATH.exists():
        print("Found existing session, testing...")
        if test_saved_session():
            print("\nExisting session is still valid. Use --force to login again.")
            sys.exit(0)
        print("\nExisting session expired, starting new login...\n")

    # Perform interactive login
    success = interactive_login()

    if success:
        print("\n" + "=" * 60)
        print("Login successful! Testing session...")
        print("=" * 60)
        test_saved_session()
        print("\nYou can now run the automation scripts.")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
