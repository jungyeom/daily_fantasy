#!/usr/bin/env python3
"""Manual cookie refresh script.

Run this script to manually login to Yahoo and save fresh cookies.
The browser will open in non-headless mode so you can complete the login.
"""

import pickle
from datetime import datetime
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

COOKIES_FILE = "data/.yahoo_cookies.pkl"


def refresh_cookies():
    """Open browser for manual login and save cookies."""
    print("Opening browser for Yahoo login...")
    print("Please login manually, then press Enter when done.")

    # Create browser in non-headless mode
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        # Navigate to Yahoo DFS
        driver.get("https://sports.yahoo.com/dailyfantasy")

        # Wait for user to login
        input("\nPress Enter after you've logged in to Yahoo...")

        # Save cookies
        cookies_path = Path(COOKIES_FILE)
        cookies_path.parent.mkdir(parents=True, exist_ok=True)
        cookies = driver.get_cookies()

        with open(cookies_path, "wb") as f:
            pickle.dump({
                "cookies": cookies,
                "saved_at": datetime.utcnow().isoformat(),
            }, f)

        print(f"\nSaved {len(cookies)} cookies to {cookies_path}")
        print("You can now close this window and run the scheduler.")

    finally:
        input("\nPress Enter to close the browser...")
        driver.quit()


if __name__ == "__main__":
    refresh_cookies()
