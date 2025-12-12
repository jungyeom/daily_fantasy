"""Yahoo authentication and session management."""
import json
import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from selenium.webdriver.remote.webdriver import WebDriver

from ..common.config import get_config, YahooConfig
from ..common.exceptions import YahooAuthError, YahooSessionExpiredError

logger = logging.getLogger(__name__)

COOKIES_FILE = "data/.yahoo_cookies.pkl"
SESSION_TIMEOUT_HOURS = 168  # 1 week - Yahoo cookies typically last longer


class YahooAuth:
    """Manages Yahoo authentication and session persistence."""

    def __init__(self, config: Optional[YahooConfig] = None):
        """Initialize Yahoo auth manager.

        Args:
            config: Yahoo configuration. Uses global config if not provided.
        """
        if config is None:
            config = get_config().yahoo
        self.config = config
        self.cookies_path = Path(COOKIES_FILE)
        self._last_login: Optional[datetime] = None

    def login(self, driver: WebDriver, force: bool = False) -> bool:
        """Perform Yahoo login or restore session from cookies.

        Args:
            driver: Selenium WebDriver instance
            force: Force fresh login even if cookies exist

        Returns:
            True if login successful

        Raises:
            YahooAuthError: If login fails
        """
        # Try to restore session from cookies first
        if not force and self._restore_cookies(driver):
            if self._verify_session(driver):
                logger.info("Session restored from cookies")
                return True
            logger.info("Stored session expired, performing fresh login")

        # Perform fresh login
        return self._perform_login(driver)

    def _perform_login(self, driver: WebDriver) -> bool:
        """Perform fresh login to Yahoo.

        Args:
            driver: Selenium WebDriver instance

        Returns:
            True if login successful

        Raises:
            YahooAuthError: If login fails
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        if not self.config.username or not self.config.password:
            raise YahooAuthError("Yahoo username or password not configured")

        try:
            logger.info("Performing Yahoo login...")

            # Navigate to Yahoo login
            driver.get("https://login.yahoo.com")

            wait = WebDriverWait(driver, self.config.timeout)

            # Enter username
            username_input = wait.until(
                EC.presence_of_element_located((By.ID, "login-username"))
            )
            username_input.clear()
            username_input.send_keys(self.config.username)

            # Click next button
            next_button = driver.find_element(By.ID, "login-signin")
            next_button.click()

            # Wait for password field
            password_input = wait.until(
                EC.presence_of_element_located((By.ID, "login-passwd"))
            )
            password_input.clear()
            password_input.send_keys(self.config.password)

            # Submit login
            signin_button = driver.find_element(By.ID, "login-signin")
            signin_button.click()

            # Wait for successful login (redirect to Yahoo homepage or DFS)
            wait.until(
                EC.any_of(
                    EC.url_contains("yahoo.com"),
                    EC.presence_of_element_located((By.ID, "ybarAccountMenu")),
                )
            )

            # Verify login was successful
            if not self._verify_session(driver):
                raise YahooAuthError("Login appeared to succeed but session verification failed")

            # Save cookies for future sessions
            self._save_cookies(driver)
            self._last_login = datetime.utcnow()

            logger.info("Yahoo login successful")
            return True

        except YahooAuthError:
            raise
        except Exception as e:
            logger.error(f"Yahoo login failed: {e}")
            if self.config.screenshot_on_error:
                self._save_error_screenshot(driver, "login_error")
            raise YahooAuthError(f"Login failed: {e}") from e

    def _verify_session(self, driver: WebDriver) -> bool:
        """Verify that we have a valid Yahoo session.

        Args:
            driver: Selenium WebDriver instance

        Returns:
            True if session is valid
        """
        try:
            # Navigate to Yahoo DFS and check if logged in
            driver.get("https://sports.yahoo.com/dailyfantasy")

            from selenium.webdriver.common.by import By
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.support.ui import WebDriverWait

            wait = WebDriverWait(driver, 10)

            # Check for logged-in indicator (account menu or user-specific element)
            try:
                wait.until(
                    EC.any_of(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-rapid_p='Account']")),
                        EC.presence_of_element_located((By.ID, "ybarAccountMenu")),
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".user-account")),
                    )
                )
                return True
            except:
                return False

        except Exception as e:
            logger.debug(f"Session verification failed: {e}")
            return False

    def _save_cookies(self, driver: WebDriver) -> None:
        """Save browser cookies to file for session persistence.

        Args:
            driver: Selenium WebDriver instance
        """
        try:
            self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
            cookies = driver.get_cookies()

            with open(self.cookies_path, "wb") as f:
                pickle.dump({
                    "cookies": cookies,
                    "saved_at": datetime.utcnow().isoformat(),
                }, f)

            logger.debug(f"Saved {len(cookies)} cookies to {self.cookies_path}")
        except Exception as e:
            logger.warning(f"Failed to save cookies: {e}")

    def _restore_cookies(self, driver: WebDriver) -> bool:
        """Restore browser cookies from file.

        Args:
            driver: Selenium WebDriver instance

        Returns:
            True if cookies were restored successfully
        """
        if not self.cookies_path.exists():
            return False

        try:
            with open(self.cookies_path, "rb") as f:
                data = pickle.load(f)

            # Check if cookies are too old (only if we have credentials as fallback)
            saved_at = datetime.fromisoformat(data["saved_at"])
            age = datetime.utcnow() - saved_at
            if age > timedelta(hours=SESSION_TIMEOUT_HOURS):
                # Only skip cookies if we have credentials to fall back on
                if self.config.username and self.config.password:
                    logger.info("Stored cookies expired, will perform fresh login")
                    return False
                else:
                    logger.info(f"Cookies are {age.total_seconds() / 3600:.1f} hours old but no credentials configured, attempting anyway")

            # Navigate to Yahoo domain first (required to set cookies)
            # Use a shorter timeout for this initial page load
            original_timeout = driver.timeouts.page_load
            try:
                driver.set_page_load_timeout(15)  # 15 second timeout
                driver.get("https://www.yahoo.com")
            except Exception as e:
                logger.warning(f"Timeout loading yahoo.com, trying sports.yahoo.com: {e}")
                try:
                    driver.get("https://sports.yahoo.com")
                except:
                    pass
            finally:
                driver.set_page_load_timeout(original_timeout)

            # Restore cookies
            restored_count = 0
            for cookie in data["cookies"]:
                try:
                    # Remove problematic attributes
                    cookie.pop("sameSite", None)
                    cookie.pop("expiry", None)
                    driver.add_cookie(cookie)
                    restored_count += 1
                except Exception as e:
                    logger.debug(f"Failed to add cookie: {e}")

            logger.info(f"Restored {restored_count}/{len(data['cookies'])} cookies")
            return restored_count > 0

        except Exception as e:
            logger.warning(f"Failed to restore cookies: {e}")
            return False

    def _save_error_screenshot(self, driver: WebDriver, name: str) -> None:
        """Save screenshot for debugging.

        Args:
            driver: Selenium WebDriver instance
            name: Name for the screenshot file
        """
        try:
            screenshot_dir = Path("data/screenshots")
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            path = screenshot_dir / f"{name}_{timestamp}.png"
            driver.save_screenshot(str(path))
            logger.info(f"Screenshot saved: {path}")
        except Exception as e:
            logger.warning(f"Failed to save screenshot: {e}")

    def logout(self, driver: WebDriver) -> None:
        """Logout from Yahoo and clear stored session.

        Args:
            driver: Selenium WebDriver instance
        """
        try:
            driver.get("https://login.yahoo.com/account/logout")
            if self.cookies_path.exists():
                self.cookies_path.unlink()
            logger.info("Yahoo logout complete")
        except Exception as e:
            logger.warning(f"Logout failed: {e}")

    def clear_session(self) -> None:
        """Clear stored session cookies without logging out."""
        try:
            if self.cookies_path.exists():
                self.cookies_path.unlink()
                logger.info("Session cookies cleared")
        except Exception as e:
            logger.warning(f"Failed to clear session: {e}")
