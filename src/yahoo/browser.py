"""Selenium WebDriver wrapper for Yahoo DFS browser automation."""
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from ..common.config import get_config, YahooConfig

logger = logging.getLogger(__name__)


class BrowserManager:
    """Manages Selenium WebDriver lifecycle and common operations."""

    def __init__(self, config: Optional[YahooConfig] = None):
        """Initialize browser manager.

        Args:
            config: Yahoo configuration. Uses global config if not provided.
        """
        if config is None:
            config = get_config().yahoo
        self.config = config
        self._driver: Optional[WebDriver] = None

    def create_driver(self) -> WebDriver:
        """Create and configure Chrome WebDriver.

        Returns:
            Configured WebDriver instance
        """
        options = Options()

        if self.config.headless:
            options.add_argument("--headless=new")

        # Common Chrome options for stability
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-popup-blocking")

        # Reduce detection as automation
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Set user agent if configured
        if self.config.user_agent:
            options.add_argument(f"--user-agent={self.config.user_agent}")

        # Create driver with auto-managed ChromeDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)

        # Set page load timeout
        driver.set_page_load_timeout(self.config.timeout)
        driver.implicitly_wait(5)

        # Remove webdriver flag
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        logger.info(f"Chrome WebDriver created (headless={self.config.headless})")
        self._driver = driver
        return driver

    def get_driver(self) -> WebDriver:
        """Get existing driver or create new one.

        Returns:
            WebDriver instance
        """
        if self._driver is None:
            return self.create_driver()
        return self._driver

    def close_driver(self) -> None:
        """Close the WebDriver and clean up."""
        if self._driver is not None:
            try:
                self._driver.quit()
                logger.info("WebDriver closed")
            except Exception as e:
                logger.warning(f"Error closing WebDriver: {e}")
            finally:
                self._driver = None

    @contextmanager
    def driver_context(self) -> Generator[WebDriver, None, None]:
        """Context manager for WebDriver lifecycle.

        Yields:
            WebDriver instance

        Example:
            with browser_manager.driver_context() as driver:
                driver.get("https://example.com")
        """
        driver = self.create_driver()
        try:
            yield driver
        finally:
            self.close_driver()

    def save_screenshot(self, name: str, driver: Optional[WebDriver] = None) -> Optional[str]:
        """Save screenshot of current page.

        Args:
            name: Name for the screenshot file
            driver: WebDriver instance (uses stored driver if not provided)

        Returns:
            Path to saved screenshot, or None if failed
        """
        driver = driver or self._driver
        if driver is None:
            logger.warning("No driver available for screenshot")
            return None

        try:
            screenshot_dir = Path("data/screenshots")
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            path = screenshot_dir / f"{name}_{timestamp}.png"
            driver.save_screenshot(str(path))
            logger.info(f"Screenshot saved: {path}")
            return str(path)
        except Exception as e:
            logger.warning(f"Failed to save screenshot: {e}")
            return None

    def save_page_source(self, name: str, driver: Optional[WebDriver] = None) -> Optional[str]:
        """Save HTML source of current page for debugging.

        Args:
            name: Name for the file
            driver: WebDriver instance (uses stored driver if not provided)

        Returns:
            Path to saved file, or None if failed
        """
        driver = driver or self._driver
        if driver is None:
            logger.warning("No driver available for page source")
            return None

        try:
            debug_dir = Path("data/debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            path = debug_dir / f"{name}_{timestamp}.html"
            with open(path, "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            logger.info(f"Page source saved: {path}")
            return str(path)
        except Exception as e:
            logger.warning(f"Failed to save page source: {e}")
            return None

    def wait_for_page_load(self, driver: WebDriver, timeout: Optional[int] = None) -> None:
        """Wait for page to finish loading.

        Args:
            driver: WebDriver instance
            timeout: Timeout in seconds (uses config default if not provided)
        """
        timeout = timeout or self.config.timeout

        def page_loaded(driver):
            return driver.execute_script("return document.readyState") == "complete"

        WebDriverWait(driver, timeout).until(page_loaded)

    def scroll_to_bottom(self, driver: WebDriver) -> None:
        """Scroll to bottom of page to trigger lazy loading.

        Args:
            driver: WebDriver instance
        """
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")

    def scroll_to_element(self, driver: WebDriver, element) -> None:
        """Scroll element into view.

        Args:
            driver: WebDriver instance
            element: WebElement to scroll to
        """
        driver.execute_script("arguments[0].scrollIntoView(true);", element)


# Singleton instance
_browser_manager: Optional[BrowserManager] = None


def get_browser_manager() -> BrowserManager:
    """Get the browser manager singleton."""
    global _browser_manager
    if _browser_manager is None:
        _browser_manager = BrowserManager()
    return _browser_manager
