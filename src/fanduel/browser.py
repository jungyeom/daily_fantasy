"""FanDuel browser automation with stealth and anti-detection.

This module provides browser automation for FanDuel with:
- Stealth mode to avoid bot detection
- Persistent session/cookie management
- Human-like behavior simulation
- Token extraction for API usage
- Future: lineup submission automation

Usage:
    from src.fanduel.browser import FanDuelBrowser

    async with FanDuelBrowser() as browser:
        # First run - will open browser for manual MFA
        await browser.login()

        # Extract tokens for API usage
        tokens = await browser.get_auth_tokens()

        # Save session for future use
        await browser.save_session()
"""

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

logger = logging.getLogger(__name__)

# Default session storage location (outside repo for security)
DEFAULT_SESSION_DIR = Path.home() / ".fanduel_session"

# FanDuel URLs
FANDUEL_BASE_URL = "https://www.fanduel.com"
FANDUEL_LOGIN_URL = "https://account.fanduel.com/login"
FANDUEL_DFS_URL = "https://www.fanduel.com/contests"


class HumanBehavior:
    """Utilities for simulating human-like behavior."""

    @staticmethod
    def random_delay(min_ms: int = 500, max_ms: int = 1500) -> float:
        """Generate a random delay in seconds."""
        return random.randint(min_ms, max_ms) / 1000

    @staticmethod
    async def human_delay(min_ms: int = 500, max_ms: int = 1500):
        """Wait for a random human-like delay."""
        delay = HumanBehavior.random_delay(min_ms, max_ms)
        await asyncio.sleep(delay)

    @staticmethod
    async def type_like_human(page: Page, selector: str, text: str):
        """Type text with human-like delays between keystrokes."""
        element = await page.wait_for_selector(selector, timeout=10000)
        if element:
            await element.click()
            await HumanBehavior.human_delay(200, 400)

            for char in text:
                await page.keyboard.type(char)
                # Random delay between keystrokes (50-150ms)
                await asyncio.sleep(random.randint(50, 150) / 1000)

            await HumanBehavior.human_delay(300, 600)

    @staticmethod
    async def click_like_human(page: Page, selector: str):
        """Click an element with human-like behavior."""
        element = await page.wait_for_selector(selector, timeout=10000)
        if element:
            # Get element bounding box
            box = await element.bounding_box()
            if box:
                # Click at a random position within the element
                x = box["x"] + random.uniform(0.2, 0.8) * box["width"]
                y = box["y"] + random.uniform(0.2, 0.8) * box["height"]

                # Move mouse to element first (human-like)
                await page.mouse.move(x, y)
                await HumanBehavior.human_delay(100, 300)

                # Click
                await page.mouse.click(x, y)
            else:
                await element.click()

            await HumanBehavior.human_delay(300, 700)

    @staticmethod
    async def scroll_randomly(page: Page):
        """Perform random scrolling like a human would."""
        # Random scroll amount
        scroll_amount = random.randint(100, 400)
        await page.mouse.wheel(0, scroll_amount)
        await HumanBehavior.human_delay(500, 1000)


class FanDuelBrowser:
    """Browser automation for FanDuel with stealth capabilities.

    Features:
    - Persistent browser context with saved cookies/storage
    - Stealth mode to avoid detection
    - Human-like behavior simulation
    - Token extraction for API usage
    - MFA support (pauses for manual input)

    Example:
        async with FanDuelBrowser() as browser:
            await browser.login()
            tokens = await browser.get_auth_tokens()
    """

    def __init__(
        self,
        session_dir: Optional[Path] = None,
        headless: bool = False,
        slow_mo: int = 0,
        use_system_chrome: bool = True,
    ):
        """Initialize FanDuel browser.

        Args:
            session_dir: Directory to store session data (cookies, storage).
                        Defaults to ~/.fanduel_session/
            headless: Run browser in headless mode. Default False for first login.
                     After session is established, can switch to True.
            slow_mo: Slow down operations by this many ms (for debugging)
            use_system_chrome: Use system-installed Chrome instead of Playwright's
                             Chromium. More stealthy but requires Chrome installed.
        """
        self.session_dir = session_dir or DEFAULT_SESSION_DIR
        self.headless = headless
        self.slow_mo = slow_mo
        self.use_system_chrome = use_system_chrome

        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

        # Ensure session directory exists
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Session state file
        self.state_file = self.session_dir / "browser_state.json"
        self.cookies_file = self.session_dir / "cookies.json"

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def start(self):
        """Start the browser with stealth settings."""
        logger.info("Starting FanDuel browser...")

        self._playwright = await async_playwright().start()

        # Use persistent context with a real Chrome user data directory
        # This makes the browser appear more like a real user's browser
        user_data_dir = self.session_dir / "chrome_profile"
        user_data_dir.mkdir(parents=True, exist_ok=True)

        # Browser launch arguments for maximum stealth
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--window-size=1920,1080",
            "--start-maximized",
            # Disable automation-related flags
            "--disable-component-extensions-with-background-pages",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-pings",
        ]

        # Use launch_persistent_context for a real browser profile
        # This is more stealthy than regular launch + new_context
        context_options = {
            "user_data_dir": str(user_data_dir),
            "headless": self.headless,
            "slow_mo": self.slow_mo,
            "args": launch_args,
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "geolocation": {"latitude": 40.7128, "longitude": -74.0060},
            "permissions": ["geolocation"],
            "ignore_default_args": ["--enable-automation"],
            "chromium_sandbox": False,
        }

        # Use system Chrome if available - much harder to detect
        if self.use_system_chrome:
            context_options["channel"] = "chrome"
            logger.info("Using system Chrome installation (more stealthy)")
        else:
            # Set custom user agent for Playwright's Chromium
            context_options["user_agent"] = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )

        self._context = await self._playwright.chromium.launch_persistent_context(
            **context_options
        )

        # Get the first page or create one
        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        # Apply stealth to page
        stealth = Stealth(
            navigator_platform_override="MacIntel",
            navigator_vendor_override="Google Inc.",
        )
        await stealth.apply_stealth_async(self._page)

        # Additional stealth scripts
        await self._page.add_init_script("""
            // Remove webdriver property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });

            // Make plugins array look realistic
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const plugins = [
                        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                        { name: 'Native Client', filename: 'internal-nacl-plugin' }
                    ];
                    plugins.length = 3;
                    return plugins;
                }
            });

            // Override permissions query
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );

            // Remove CDP detection
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
            delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;
        """)

        logger.info("Browser started successfully with persistent profile")

    async def close(self):
        """Close the browser and cleanup."""
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

        logger.info("Browser closed")

    async def save_session(self):
        """Save current session state (cookies, local storage)."""
        if self._context:
            await self._context.storage_state(path=str(self.state_file))
            logger.info(f"Session saved to {self.state_file}")

            # Also save cookies separately for debugging
            cookies = await self._context.cookies()
            with open(self.cookies_file, "w") as f:
                json.dump(cookies, f, indent=2)
            logger.debug(f"Cookies saved to {self.cookies_file}")

    async def is_logged_in(self) -> bool:
        """Check if currently logged into FanDuel."""
        if not self._page:
            return False

        try:
            # Navigate to FanDuel and check for login state
            await self._page.goto(FANDUEL_DFS_URL, wait_until="networkidle")
            await HumanBehavior.human_delay(1000, 2000)

            # Check if we're redirected to login page
            current_url = self._page.url
            if "login" in current_url or "account.fanduel.com" in current_url:
                logger.info("Not logged in - on login page")
                return False

            # Check for logged-in indicators (account menu, username, etc.)
            account_selector = '[data-testid="account-menu"], .account-menu, .user-menu'
            try:
                await self._page.wait_for_selector(account_selector, timeout=5000)
                logger.info("Logged in - found account menu")
                return True
            except:
                pass

            # Alternative check: look for login button (means not logged in)
            login_button = await self._page.query_selector('a[href*="login"], button:has-text("Log In")')
            if login_button:
                logger.info("Not logged in - found login button")
                return False

            # If we're on the contests page without being redirected, probably logged in
            if "contests" in current_url:
                logger.info("Appears to be logged in (on contests page)")
                return True

            return False

        except Exception as e:
            logger.error(f"Error checking login status: {e}")
            return False

    async def login(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        wait_for_mfa: bool = True,
        mfa_timeout: int = 120,
    ) -> bool:
        """Login to FanDuel.

        If MFA is required, the browser will pause and wait for manual input.

        Args:
            email: FanDuel email. If not provided, reads from FANDUEL_EMAIL env var.
            password: FanDuel password. If not provided, reads from FANDUEL_PASSWORD env var.
            wait_for_mfa: If True, waits for manual MFA completion
            mfa_timeout: Seconds to wait for MFA completion

        Returns:
            True if login successful, False otherwise
        """
        if not self._page:
            raise RuntimeError("Browser not started. Call start() first.")

        # Check if already logged in
        if await self.is_logged_in():
            logger.info("Already logged in")
            await self.save_session()
            return True

        # Get credentials
        email = email or os.environ.get("FANDUEL_EMAIL")
        password = password or os.environ.get("FANDUEL_PASSWORD")

        if not email or not password:
            raise ValueError(
                "Email and password required. Set FANDUEL_EMAIL and FANDUEL_PASSWORD "
                "environment variables or pass them as arguments."
            )

        logger.info("Navigating to login page...")
        await self._page.goto(FANDUEL_LOGIN_URL, wait_until="networkidle")
        await HumanBehavior.human_delay(1500, 2500)

        # Random scroll to look human
        await HumanBehavior.scroll_randomly(self._page)

        # Fill email
        logger.info("Entering email...")
        email_selectors = [
            'input[name="email"]',
            'input[type="email"]',
            '#email',
            'input[placeholder*="email" i]',
        ]

        email_entered = False
        for selector in email_selectors:
            try:
                await HumanBehavior.type_like_human(self._page, selector, email)
                email_entered = True
                break
            except:
                continue

        if not email_entered:
            logger.error("Could not find email input field")
            return False

        await HumanBehavior.human_delay(500, 1000)

        # Fill password
        logger.info("Entering password...")
        password_selectors = [
            'input[name="password"]',
            'input[type="password"]',
            '#password',
        ]

        password_entered = False
        for selector in password_selectors:
            try:
                await HumanBehavior.type_like_human(self._page, selector, password)
                password_entered = True
                break
            except:
                continue

        if not password_entered:
            logger.error("Could not find password input field")
            return False

        await HumanBehavior.human_delay(500, 1000)

        # Click login button
        logger.info("Clicking login button...")
        login_selectors = [
            'button[type="submit"]',
            'button:has-text("Log In")',
            'button:has-text("Sign In")',
            'input[type="submit"]',
        ]

        login_clicked = False
        for selector in login_selectors:
            try:
                await HumanBehavior.click_like_human(self._page, selector)
                login_clicked = True
                break
            except:
                continue

        if not login_clicked:
            logger.error("Could not find login button")
            return False

        # Wait for navigation/response
        await HumanBehavior.human_delay(2000, 3000)

        # Check for MFA
        current_url = self._page.url
        mfa_indicators = ["verification", "mfa", "2fa", "code", "verify"]

        if any(ind in current_url.lower() for ind in mfa_indicators):
            if wait_for_mfa:
                logger.info("=" * 60)
                logger.info("MFA REQUIRED - Please complete verification in the browser")
                logger.info(f"Waiting up to {mfa_timeout} seconds...")
                logger.info("=" * 60)

                # Wait for MFA completion (user navigates away from MFA page)
                start_time = time.time()
                while time.time() - start_time < mfa_timeout:
                    await asyncio.sleep(2)
                    current_url = self._page.url

                    # Check if we've moved past MFA
                    if not any(ind in current_url.lower() for ind in mfa_indicators):
                        logger.info("MFA appears to be completed")
                        break

                    # Check if we're now logged in
                    if "contests" in current_url or "lobby" in current_url:
                        logger.info("Successfully logged in after MFA")
                        break
                else:
                    logger.warning("MFA timeout - verification not completed in time")
                    return False
            else:
                logger.warning("MFA required but wait_for_mfa=False")
                return False

        # Verify login success
        await HumanBehavior.human_delay(2000, 3000)

        if await self.is_logged_in():
            logger.info("Login successful!")
            await self.save_session()
            return True
        else:
            logger.error("Login failed - could not verify logged in state")
            return False

    async def get_auth_tokens(self) -> dict:
        """Extract auth tokens from browser session.

        Returns:
            Dict with 'auth_token' and 'session_token' keys
        """
        if not self._page:
            raise RuntimeError("Browser not started")

        tokens = {
            "auth_token": None,
            "session_token": None,
        }

        # Method 1: Extract from cookies
        cookies = await self._context.cookies()
        for cookie in cookies:
            if cookie["name"] == "X-Auth-Token":
                tokens["auth_token"] = cookie["value"]
            elif cookie["name"] == "X-Session-Token":
                tokens["session_token"] = cookie["value"]

        # Method 2: Extract from localStorage
        if not tokens["auth_token"] or not tokens["session_token"]:
            try:
                local_storage = await self._page.evaluate("""
                    () => {
                        const items = {};
                        for (let i = 0; i < localStorage.length; i++) {
                            const key = localStorage.key(i);
                            items[key] = localStorage.getItem(key);
                        }
                        return items;
                    }
                """)

                for key, value in local_storage.items():
                    if "auth" in key.lower() and not tokens["auth_token"]:
                        tokens["auth_token"] = value
                    elif "session" in key.lower() and not tokens["session_token"]:
                        tokens["session_token"] = value

            except Exception as e:
                logger.debug(f"Could not read localStorage: {e}")

        # Method 3: Intercept network requests to find tokens
        if not tokens["auth_token"] or not tokens["session_token"]:
            logger.info("Tokens not found in cookies/storage. Making API request to capture...")

            captured_tokens = {}

            async def capture_request(request):
                headers = request.headers
                if "x-auth-token" in headers:
                    captured_tokens["auth_token"] = headers["x-auth-token"]
                if "x-session-token" in headers:
                    captured_tokens["session_token"] = headers["x-session-token"]

            self._page.on("request", capture_request)

            # Navigate to trigger API requests
            await self._page.goto(FANDUEL_DFS_URL, wait_until="networkidle")
            await HumanBehavior.human_delay(2000, 3000)

            # Update tokens from captured requests
            if captured_tokens.get("auth_token"):
                tokens["auth_token"] = captured_tokens["auth_token"]
            if captured_tokens.get("session_token"):
                tokens["session_token"] = captured_tokens["session_token"]

        if tokens["auth_token"]:
            logger.info(f"Auth token found: {tokens['auth_token'][:20]}...")
        else:
            logger.warning("Auth token not found")

        if tokens["session_token"]:
            logger.info(f"Session token found: {tokens['session_token'][:20]}...")
        else:
            logger.warning("Session token not found")

        return tokens

    async def update_env_file(self, tokens: dict, env_file: str = ".env"):
        """Update .env file with new tokens.

        Args:
            tokens: Dict with auth_token and session_token
            env_file: Path to .env file
        """
        env_path = Path(env_file)

        # Read existing .env content
        existing_lines = []
        if env_path.exists():
            with open(env_path, "r") as f:
                existing_lines = f.readlines()

        # Update or add token lines
        new_lines = []
        auth_updated = False
        session_updated = False

        for line in existing_lines:
            if line.startswith("FANDUEL_AUTH_TOKEN="):
                if tokens.get("auth_token"):
                    new_lines.append(f"FANDUEL_AUTH_TOKEN={tokens['auth_token']}\n")
                    auth_updated = True
                else:
                    new_lines.append(line)
            elif line.startswith("FANDUEL_SESSION_TOKEN="):
                if tokens.get("session_token"):
                    new_lines.append(f"FANDUEL_SESSION_TOKEN={tokens['session_token']}\n")
                    session_updated = True
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Add tokens if not already in file
        if not auth_updated and tokens.get("auth_token"):
            new_lines.append(f"FANDUEL_AUTH_TOKEN={tokens['auth_token']}\n")
        if not session_updated and tokens.get("session_token"):
            new_lines.append(f"FANDUEL_SESSION_TOKEN={tokens['session_token']}\n")

        # Write updated .env
        with open(env_path, "w") as f:
            f.writelines(new_lines)

        logger.info(f"Updated {env_file} with new tokens")

    @property
    def page(self) -> Optional[Page]:
        """Get the current page for advanced operations."""
        return self._page

    @property
    def context(self) -> Optional[BrowserContext]:
        """Get the browser context for advanced operations."""
        return self._context


async def refresh_tokens(
    headless: bool = False,
    update_env: bool = True,
    use_system_chrome: bool = True,
) -> dict:
    """Convenience function to refresh FanDuel tokens.

    Args:
        headless: Run in headless mode (only works if session already established)
        update_env: Update .env file with new tokens
        use_system_chrome: Use system Chrome instead of Playwright's Chromium

    Returns:
        Dict with auth_token and session_token
    """
    async with FanDuelBrowser(headless=headless, use_system_chrome=use_system_chrome) as browser:
        # Try to login (will use existing session if available)
        success = await browser.login()

        if not success:
            logger.error("Failed to login to FanDuel")
            return {}

        # Extract tokens
        tokens = await browser.get_auth_tokens()

        if update_env and (tokens.get("auth_token") or tokens.get("session_token")):
            await browser.update_env_file(tokens)

        return tokens


# CLI entry point
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Refresh FanDuel authentication tokens")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode")
    parser.add_argument("--no-update-env", action="store_true", help="Don't update .env file")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    tokens = asyncio.run(refresh_tokens(
        headless=args.headless,
        update_env=not args.no_update_env,
    ))

    if tokens:
        print("\nTokens retrieved successfully!")
        if tokens.get("auth_token"):
            print(f"  Auth Token: {tokens['auth_token'][:30]}...")
        if tokens.get("session_token"):
            print(f"  Session Token: {tokens['session_token'][:30]}...")
    else:
        print("\nFailed to retrieve tokens")
