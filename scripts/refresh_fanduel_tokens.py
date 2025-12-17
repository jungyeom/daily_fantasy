#!/usr/bin/env python3
"""Refresh FanDuel authentication tokens using browser automation.

This script:
1. Opens a browser with stealth settings to avoid bot detection
2. Logs into FanDuel (uses saved session if available)
3. Handles MFA if required (pauses for manual input)
4. Extracts auth tokens from the browser session
5. Updates .env file with fresh tokens

First run:
    uv run python scripts/refresh_fanduel_tokens.py

    This will open a visible browser. Complete MFA if prompted.
    Session will be saved for future use.

Subsequent runs (with saved session):
    uv run python scripts/refresh_fanduel_tokens.py --headless

    Uses saved session, no browser window needed (if session still valid).

Environment variables required:
    FANDUEL_EMAIL - Your FanDuel account email
    FANDUEL_PASSWORD - Your FanDuel account password

Usage:
    # First time or when session expired (visible browser, MFA support)
    uv run python scripts/refresh_fanduel_tokens.py

    # With saved session (headless, faster)
    uv run python scripts/refresh_fanduel_tokens.py --headless

    # Just check current session without updating .env
    uv run python scripts/refresh_fanduel_tokens.py --check-only

    # Clear saved session and start fresh
    uv run python scripts/refresh_fanduel_tokens.py --clear-session
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

from src.fanduel.browser import FanDuelBrowser, DEFAULT_SESSION_DIR, refresh_tokens

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


async def check_session() -> bool:
    """Check if saved session is still valid."""
    print("Checking saved session...")

    async with FanDuelBrowser(headless=True) as browser:
        is_valid = await browser.is_logged_in()

        if is_valid:
            print("Session is valid!")
            tokens = await browser.get_auth_tokens()
            if tokens.get("auth_token"):
                print(f"  Auth Token: {tokens['auth_token'][:30]}...")
            if tokens.get("session_token"):
                print(f"  Session Token: {tokens['session_token'][:30]}...")
            return True
        else:
            print("Session expired or invalid. Run without --check-only to refresh.")
            return False


def clear_session():
    """Clear saved session data."""
    if DEFAULT_SESSION_DIR.exists():
        shutil.rmtree(DEFAULT_SESSION_DIR)
        print(f"Cleared session data from {DEFAULT_SESSION_DIR}")
    else:
        print("No saved session to clear")


async def main():
    parser = argparse.ArgumentParser(
        description="Refresh FanDuel authentication tokens",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (only works with valid saved session)",
    )
    parser.add_argument(
        "--no-update-env",
        action="store_true",
        help="Don't update .env file with new tokens",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only check if current session is valid, don't refresh",
    )
    parser.add_argument(
        "--clear-session",
        action="store_true",
        help="Clear saved session and exit",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--use-playwright-chromium",
        action="store_true",
        help="Use Playwright's Chromium instead of system Chrome (less stealthy)",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Handle clear session
    if args.clear_session:
        clear_session()
        return

    # Handle check only
    if args.check_only:
        await check_session()
        return

    # Check for required environment variables
    email = os.environ.get("FANDUEL_EMAIL")
    password = os.environ.get("FANDUEL_PASSWORD")

    if not email or not password:
        print("ERROR: FANDUEL_EMAIL and FANDUEL_PASSWORD environment variables required")
        print("\nAdd to your .env file:")
        print("  FANDUEL_EMAIL=your_email@example.com")
        print("  FANDUEL_PASSWORD=your_password")
        sys.exit(1)

    # Refresh tokens
    print("=" * 60)
    print("FanDuel Token Refresh")
    print("=" * 60)

    if args.headless:
        print("Running in headless mode (using saved session)")
    else:
        print("Running in visible browser mode")
        print("If MFA is required, please complete it in the browser window")

    print()

    tokens = await refresh_tokens(
        headless=args.headless,
        update_env=not args.no_update_env,
        use_system_chrome=not args.use_playwright_chromium,
    )

    print()
    print("=" * 60)

    if tokens.get("auth_token") and tokens.get("session_token"):
        print("SUCCESS! Tokens retrieved successfully")
        print()
        print(f"  Auth Token: {tokens['auth_token'][:40]}...")
        print(f"  Session Token: {tokens['session_token'][:40]}...")
        print()

        if not args.no_update_env:
            print("Tokens have been saved to .env file")
        else:
            print("Tokens NOT saved to .env (--no-update-env flag)")

        print()
        print("You can now run the lineup generator:")
        print("  uv run python scripts/generate_fanduel_lineups.py --num-lineups 20")

    else:
        print("FAILED to retrieve tokens")
        print()
        if args.headless:
            print("Try running without --headless flag:")
            print("  uv run python scripts/refresh_fanduel_tokens.py")
        else:
            print("Check that:")
            print("  1. FANDUEL_EMAIL and FANDUEL_PASSWORD are correct")
            print("  2. MFA was completed successfully")
            print("  3. Your account is in good standing")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
