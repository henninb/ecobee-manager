#!/usr/bin/env python3
"""
ecobee JWT Authentication Module
Uses Selenium to login and extract JWT token from _TOKEN cookie
"""

import json
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
import time

logger = logging.getLogger(__name__)


class EcobeeAuthJWT:
    """Handles JWT authentication via web login"""

    TOKEN_LIFETIME = 3600  # 1 hour in seconds
    REFRESH_BUFFER = 300   # Refresh 5 minutes before expiry

    def __init__(self, email: str, password: str, config_file: str = "data/.ecobee_jwt.json"):
        self.email = email
        self.password = password
        self.config_file = config_file
        self.jwt_token = None
        self.token_expires_at = None
        self.last_refreshed = None
        self.driver = None

        # Configurable timeout for Selenium operations (useful for slower networks)
        self.selenium_timeout = int(os.getenv('SELENIUM_TIMEOUT', '30'))  # Default 30s
        self.selenium_redirect_timeout = int(os.getenv('SELENIUM_REDIRECT_TIMEOUT', '60'))  # Default 60s for redirects

    def _init_driver(self, headless: bool = True):
        """Initialize Chrome driver"""
        if self.driver:
            return

        chrome_options = Options()
        if headless:
            chrome_options.add_argument('--headless')
            chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_argument('user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

        # Disable automation flags
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)

        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': '''
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    })
                '''
            })
            self.driver.implicitly_wait(10)
            logger.info("Chrome driver initialized")
        except WebDriverException as e:
            logger.error(f"Failed to initialize Chrome driver: {e}")
            raise

    def _close_driver(self):
        """Close Chrome driver"""
        if self.driver:
            try:
                self.driver.quit()
                self.driver = None
                logger.info("Chrome driver closed")
            except Exception as e:
                logger.warning(f"Error closing driver: {e}")

    def login_and_extract_token(self, headless: bool = True) -> bool:
        """
        Login to Ecobee web portal via Auth0 and extract JWT from _TOKEN cookie

        Returns:
            True if successful, False otherwise
        """
        try:
            self._init_driver(headless)

            # Direct Auth0 login URL
            auth_url = "https://auth.ecobee.com/authorize?response_type=token&response_mode=form_post&client_id=183eORFPlXyz9BbDZwqexHPBQoVjgadh&redirect_uri=https://www.ecobee.com/home/authCallback&audience=https://prod.ecobee.com/api/v1&scope=openid%20smartWrite%20piiWrite%20piiRead%20smartRead%20deleteGrants"

            logger.info("Navigating to Ecobee Auth0 login page...")
            self.driver.get(auth_url)

            # Use configurable timeout for element waits
            wait = WebDriverWait(self.driver, self.selenium_timeout)
            logger.debug(f"Using Selenium timeout: {self.selenium_timeout}s for elements, {self.selenium_redirect_timeout}s for redirect")

            # Step 1: Wait for and fill email (Auth0 uses "username" for email field)
            logger.debug("Waiting for email field...")
            email_field = wait.until(EC.presence_of_element_located((By.ID, "username")))

            # Use JavaScript to set value and trigger events (works better with modern frameworks)
            logger.debug("Setting email value...")
            self.driver.execute_script("""
                var element = arguments[0];
                var value = arguments[1];
                element.value = value;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                element.dispatchEvent(new Event('blur', { bubbles: true }));
            """, email_field, self.email)
            logger.debug(f"Email entered: {self.email}")

            # Give the page a moment to process and enable the button
            time.sleep(2)

            # Click Continue button to go to password page
            logger.debug("Clicking Continue button...")
            try:
                continue_button = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[type='submit']")))
                # Use JavaScript click to bypass visibility checks
                self.driver.execute_script("arguments[0].click();", continue_button)
                logger.debug("Continue button clicked")
            except Exception as e:
                logger.error(f"Failed to click continue button: {e}")
                raise

            # Step 2: Wait for password page to load and fill password
            logger.debug("Waiting for password field...")
            password_field = wait.until(EC.presence_of_element_located((By.ID, "password")))

            # Use JavaScript to set value and trigger events
            logger.debug("Setting password value...")
            self.driver.execute_script("""
                var element = arguments[0];
                var value = arguments[1];
                element.value = value;
                element.dispatchEvent(new Event('input', { bubbles: true }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                element.dispatchEvent(new Event('blur', { bubbles: true }));
            """, password_field, self.password)
            logger.debug("Password entered")

            # Give the page a moment to process and enable the button
            time.sleep(2)

            # Click login button
            logger.debug("Clicking login button...")
            try:
                login_button = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "button[type='submit']")))
                # Use JavaScript click to bypass visibility checks
                self.driver.execute_script("arguments[0].click();", login_button)
                logger.info("Login button clicked, waiting for authentication...")
            except Exception as e:
                logger.error(f"Failed to click login button: {e}")
                raise

            # Wait longer for redirect back to ecobee.com (this can be slow on remote servers)
            logger.debug(f"Waiting for redirect to ecobee.com (timeout: {self.selenium_redirect_timeout}s)...")
            redirect_wait = WebDriverWait(self.driver, self.selenium_redirect_timeout)
            redirect_wait.until(lambda driver: "ecobee.com" in driver.current_url and "auth.ecobee.com" not in driver.current_url)
            logger.debug(f"Redirected to: {self.driver.current_url}")
            time.sleep(5)  # Give extra time for cookies to be fully set

            # Extract _TOKEN cookie
            logger.debug("Extracting _TOKEN cookie...")
            cookies = self.driver.get_cookies()

            token_cookie = None
            for cookie in cookies:
                if cookie['name'] == '_TOKEN':
                    token_cookie = cookie['value']
                    break

            if not token_cookie:
                logger.error("_TOKEN cookie not found after login")
                logger.debug(f"Available cookies: {[c['name'] for c in cookies]}")
                logger.debug(f"Final URL: {self.driver.current_url}")
                return False

            # Successfully extracted token
            self.jwt_token = token_cookie
            now = datetime.now(timezone.utc)
            self.token_expires_at = now + timedelta(seconds=self.TOKEN_LIFETIME)
            self.last_refreshed = now

            logger.info(f"Successfully extracted JWT token (expires: {self.token_expires_at})")

            # Save token
            self.save_token()

            return True

        except TimeoutException as e:
            current_url = self.driver.current_url if self.driver else 'N/A'
            logger.error(f"Login failed - timeout waiting for page elements (current URL: {current_url})")
            if self.driver:
                logger.debug(f"Page title: {self.driver.title}")
                logger.debug(f"Available cookies: {[c['name'] for c in self.driver.get_cookies()]}")
            return False
        except Exception as e:
            logger.error(f"Login failed: {e}", exc_info=True)
            if self.driver:
                logger.debug(f"Current URL at failure: {self.driver.current_url}")
            return False
        finally:
            self._close_driver()

    def save_token(self):
        """Save JWT token to config file with expiration tracking"""
        config = {
            'jwt_token': self.jwt_token,
            'token_expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None,
            'last_refreshed': self.last_refreshed.isoformat() if self.last_refreshed else None
        }
        try:
            # Create data directory if it doesn't exist
            os.makedirs(os.path.dirname(self.config_file), exist_ok=True)

            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            os.chmod(self.config_file, 0o600)
            logger.info(f"Saved JWT token to {self.config_file}")
        except Exception as e:
            logger.error(f"Error saving token: {e}")

    def load_token(self) -> bool:
        """Load saved JWT token from config file"""
        if not os.path.exists(self.config_file):
            logger.info(f"No token file found at {self.config_file}")
            return False

        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)

            self.jwt_token = config.get('jwt_token')

            # Load expiration times
            if config.get('token_expires_at'):
                self.token_expires_at = datetime.fromisoformat(config['token_expires_at'])
            if config.get('last_refreshed'):
                self.last_refreshed = datetime.fromisoformat(config['last_refreshed'])

            logger.info(f"Loaded JWT token from {self.config_file}")
            return True

        except Exception as e:
            logger.error(f"Error loading token: {e}")
            return False

    def needs_refresh(self) -> bool:
        """Check if token needs to be refreshed"""
        if not self.token_expires_at:
            return True

        time_until_expiry = (self.token_expires_at - datetime.now(timezone.utc)).total_seconds()
        needs_refresh = time_until_expiry < self.REFRESH_BUFFER

        if needs_refresh:
            logger.info(f"Token needs refresh (expires in {int(time_until_expiry)}s)")

        return needs_refresh

    def is_token_valid(self) -> bool:
        """Check if token is still valid"""
        if not self.token_expires_at or not self.jwt_token:
            return False

        return datetime.now(timezone.utc) < self.token_expires_at

    def get_token(self) -> Optional[str]:
        """
        Get valid JWT token, refreshing if necessary

        Returns:
            JWT token string or None if unable to obtain
        """
        # Load token if not in memory
        if not self.jwt_token:
            if not self.load_token():
                logger.warning("No token found, need to login")
                return None

        # Check if token needs refresh
        if self.needs_refresh():
            logger.info("Token needs refresh, re-logging in...")
            if not self.refresh_token():
                logger.error("Failed to refresh token")
                return None

        return self.jwt_token

    def refresh_token(self, max_retries: int = 3) -> bool:
        """
        Refresh JWT token by re-logging in

        Args:
            max_retries: Maximum number of retry attempts

        Returns:
            True if successful, False otherwise
        """
        for attempt in range(max_retries):
            try:
                logger.info(f"Refreshing token (attempt {attempt + 1}/{max_retries})...")

                if self.login_and_extract_token(headless=True):
                    logger.info("Token refreshed successfully")
                    return True
                else:
                    logger.warning(f"Token refresh attempt {attempt + 1} failed")

                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff

            except Exception as e:
                logger.error(f"Error during token refresh attempt {attempt + 1}: {e}")

                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        logger.error(f"All {max_retries} token refresh attempts failed")
        return False

    def get_token_status(self) -> dict:
        """Get current token status information"""
        if not self.jwt_token or not self.token_expires_at:
            return {
                'valid': False,
                'token_present': False,
                'expires_at': None,
                'expires_in_minutes': None
            }

        time_until_expiry = (self.token_expires_at - datetime.now(timezone.utc)).total_seconds()

        return {
            'valid': self.is_token_valid(),
            'token_present': True,
            'expires_at': self.token_expires_at.isoformat(),
            'expires_in_minutes': int(time_until_expiry / 60),
            'needs_refresh': self.needs_refresh()
        }


if __name__ == "__main__":
    # Test the JWT auth
    import sys

    logging.basicConfig(level=logging.DEBUG)

    email = os.environ.get('ECOBEE_EMAIL')
    password = os.environ.get('ECOBEE_PASSWORD')

    if not email or not password:
        print("Error: Set ECOBEE_EMAIL and ECOBEE_PASSWORD environment variables")
        sys.exit(1)

    auth = EcobeeAuthJWT(email, password)

    print("\nAttempting to login and extract JWT token...")
    if auth.login_and_extract_token(headless=False):
        print(f"\n✓ Success!")
        print(f"Token: {auth.jwt_token[:50]}...")
        print(f"Expires: {auth.token_expires_at}")

        # Test token status
        status = auth.get_token_status()
        print(f"\nToken Status:")
        print(json.dumps(status, indent=2))
    else:
        print("\n✗ Failed to extract token")
        sys.exit(1)
