#!/usr/bin/env python3
"""
Ecobee Temperature Management Service (JWT-based)
Main daemon that monitors and maintains thermostat temperature according to schedule
Uses JWT token extracted from web portal login
"""

import os
import sys
import time
import signal
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from collections import deque

from ecobee_auth_jwt import EcobeeAuthJWT
from schedule_engine import ScheduleEngine
from temperature_controller import TemperatureController
from health_server import HealthServer


class EcobeeServiceJWT:
    """Main service for managing Ecobee temperature using JWT authentication"""

    def __init__(self):
        self.running = False
        self.email = None
        self.password = None
        self.auth = None
        self.schedule = None
        self.controller = None
        self.health_server = None

        # Configuration
        self.check_interval_minutes = int(os.environ.get('CHECK_INTERVAL_MINUTES', 10))
        self.log_level = os.environ.get('LOG_LEVEL', 'INFO')

        # Error tracking
        self.consecutive_errors = 0
        self.error_threshold = 3

        # Change tracking (for excessive change detection)
        self.recent_reverts = deque(maxlen=60)  # Track last 60 reverts

        self._setup_logging()
        self._setup_signal_handlers()

    def _setup_logging(self):
        """Setup logging with rotation"""
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)

        log_file = os.path.join(log_dir, "ecobee_service.log")

        # Create rotating file handler
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=100 * 1024 * 1024,  # 100MB
            backupCount=30  # Keep 30 days
        )
        file_handler.setLevel(getattr(logging, self.log_level))

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, self.log_level))

        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.log_level))
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

        self.logger = logging.getLogger(__name__)

    def _setup_signal_handlers(self):
        """Setup handlers for graceful shutdown"""
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    def initialize(self) -> bool:
        """Initialize all service components"""
        self.logger.info("Initializing Ecobee Temperature Management Service (JWT)...")

        # Get credentials
        self.email = os.environ.get('ECOBEE_EMAIL')
        self.password = os.environ.get('ECOBEE_PASSWORD')

        if not self.email or not self.password:
            self.logger.error("ECOBEE_EMAIL and ECOBEE_PASSWORD environment variables not set")
            return False

        # Initialize JWT authentication
        self.logger.info("Initializing JWT authentication...")
        self.auth = EcobeeAuthJWT(self.email, self.password, config_file="data/.ecobee_jwt.json")

        # Try to load existing token
        if self.auth.load_token():
            self.logger.info("Loaded existing JWT token")
            if not self.auth.is_token_valid():
                self.logger.warning("Loaded token is expired, will refresh")
        else:
            self.logger.info("No existing token found, performing initial login...")
            if not self.auth.login_and_extract_token(headless=True):
                self.logger.error("Initial login failed")
                return False
            self.logger.info("Initial login successful!")

        # Get valid token (will refresh if needed)
        token = self.auth.get_token()
        if not token:
            self.logger.error("Failed to get valid JWT token")
            return False

        self.logger.info("JWT authentication initialized successfully")

        # Initialize schedule engine
        self.logger.info("Loading schedule...")
        self.schedule = ScheduleEngine("config/schedule.json")
        if not self.schedule.load_schedule():
            self.logger.error("Failed to load schedule")
            return False

        # Validate schedule
        warnings = self.schedule.validate_schedule()
        if warnings:
            self.logger.warning("Schedule validation warnings:")
            for warning in warnings:
                self.logger.warning(f"  - {warning}")

        self.logger.info("Schedule loaded successfully")

        # Initialize temperature controller
        self.logger.info("Initializing temperature controller...")
        self.controller = TemperatureController(token)
        self.logger.info("Temperature controller initialized")

        # Initialize health server
        self.logger.info("Initializing health server...")
        self.health_server = HealthServer(port=8080)
        self.health_server.update_schedule_status(True)
        self.health_server.update_token_status(
            True,
            self.auth.token_expires_at,
            None  # No refresh token with JWT approach
        )
        self.health_server.start()
        self.logger.info("Health server initialized")

        self.logger.info("All components initialized successfully")
        return True

    def check_and_update_temperature(self):
        """Main temperature check and update logic"""
        try:
            self.logger.debug("Starting temperature check...")

            # Check for schedule updates
            self.schedule.check_for_updates()

            # Get expected temperature from schedule
            expected_temp = self.schedule.get_expected_temperature()
            self.logger.info(f"Expected temperature: {expected_temp}°F")

            # Get current temperature setting
            current_temp = self.controller.get_current_temperature_setting()
            if current_temp is None:
                self.logger.error("Failed to get current temperature setting")
                self.health_server.increment_errors()
                self.consecutive_errors += 1
                return

            self.logger.info(f"Current temperature: {current_temp}°F")

            # Update health status
            self.health_server.update_temperature_status(current_temp, expected_temp)
            self.health_server.increment_checks()

            # Compare temperatures
            if not self.controller.temperatures_match(current_temp, expected_temp):
                self.logger.warning(f"Temperature mismatch! Expected: {expected_temp}°F, Found: {current_temp}°F")

                # Revert to expected temperature
                if self.controller.set_temperature(expected_temp):
                    self.logger.info(f"Successfully reverted temperature to {expected_temp}°F")
                    self.health_server.increment_reverts()

                    # Track revert
                    self.recent_reverts.append(datetime.now())

                    # Check for excessive changes
                    self._check_excessive_changes()
                else:
                    self.logger.error(f"Failed to set temperature to {expected_temp}°F")
                    self.health_server.increment_errors()
                    self.consecutive_errors += 1
                    return
            else:
                self.logger.info("Temperature matches schedule ✓")

            # Reset error counter on success
            self.consecutive_errors = 0

        except Exception as e:
            self.logger.error(f"Error in temperature check: {e}", exc_info=True)
            self.health_server.increment_errors()
            self.consecutive_errors += 1

    def _check_excessive_changes(self):
        """Check for excessive temperature changes in the last hour"""
        now = datetime.now()
        one_hour_ago = datetime.fromtimestamp(now.timestamp() - 3600)

        # Count reverts in last hour
        recent_count = sum(1 for revert_time in self.recent_reverts if revert_time > one_hour_ago)

        if recent_count > 10:
            self.logger.warning(f"Excessive temperature changes detected: {recent_count} in last hour")

    def refresh_token_if_needed(self) -> bool:
        """Check and refresh token if needed"""
        if self.auth.needs_refresh():
            self.logger.info("JWT token needs refresh...")

            if self.auth.refresh_token():
                self.logger.info("Token refreshed successfully")
                self.health_server.increment_token_refreshes()
                self.health_server.update_token_status(
                    True,
                    self.auth.token_expires_at,
                    None
                )

                # Update controller with new token
                new_token = self.auth.get_token()
                if new_token:
                    self.controller.update_token(new_token)

                return True
            else:
                self.logger.error("Failed to refresh token!")
                self.health_server.update_token_status(False)
                return False

        return True

    def run(self):
        """Main service loop"""
        self.running = True
        self.logger.info("=" * 60)
        self.logger.info("Ecobee Temperature Management Service Started (JWT)")
        self.logger.info(f"Check interval: {self.check_interval_minutes} minutes")
        self.logger.info("=" * 60)

        while self.running:
            try:
                # Refresh JWT token if needed (re-login)
                if not self.refresh_token_if_needed():
                    self.logger.error("Cannot continue without valid token")
                    # Still continue loop, will retry next cycle
                    time.sleep(60)  # Wait a minute before retry
                    continue

                # Check and update temperature
                self.check_and_update_temperature()

                # Check for consecutive errors
                if self.consecutive_errors >= self.error_threshold:
                    self.logger.error(f"Reached error threshold ({self.error_threshold} consecutive errors)")
                    self.consecutive_errors = 0  # Reset after logging

                # Sleep until next check
                if self.running:
                    self.logger.info(f"Sleeping for {self.check_interval_minutes} minutes...")
                    for _ in range(self.check_interval_minutes * 60):
                        if not self.running:
                            break
                        time.sleep(1)

            except KeyboardInterrupt:
                self.logger.info("Received keyboard interrupt")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
                time.sleep(60)  # Wait a minute before continuing

        self.shutdown()

    def shutdown(self):
        """Shutdown service gracefully"""
        self.logger.info("Shutting down service...")
        self.logger.info("Service stopped")


def main():
    """Main entry point"""
    service = EcobeeServiceJWT()

    if not service.initialize():
        print("Failed to initialize service. Check logs for details.")
        sys.exit(1)

    try:
        service.run()
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
