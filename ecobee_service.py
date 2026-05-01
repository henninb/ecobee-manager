#!/usr/bin/env python3
"""Ecobee Temperature Management Service (JWT-based).

Main daemon that monitors and maintains thermostat temperature according to a
JSON schedule.  Uses a JWT token extracted from the Ecobee web portal login.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from collections import deque
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from ecobee_auth_jwt import EcobeeAuthJWT
from health_server import HealthServer
from schedule_engine import ScheduleEngine
from secrets_loader import load_secrets
from temperature_controller import TemperatureController


class EcobeeServiceJWT:
    """Daemon that enforces thermostat setpoints from a local schedule."""

    def __init__(self) -> None:
        self.check_interval_minutes = int(os.environ.get("CHECK_INTERVAL_MINUTES", 40))
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.error_threshold = 3

        self.running = False
        self._stop_event = threading.Event()

        self.auth: EcobeeAuthJWT | None = None
        self.schedule: ScheduleEngine | None = None
        self.controller: TemperatureController | None = None
        self.health_server: HealthServer | None = None

        self.consecutive_errors = 0
        self.recent_reverts: deque[datetime] = deque(maxlen=60)

        self._setup_logging()
        self._setup_signal_handlers()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_logging(self) -> None:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "ecobee_service.log")

        level = getattr(logging, self.log_level)
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = RotatingFileHandler(
            log_file, maxBytes=100 * 1024 * 1024, backupCount=30
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)

        root = logging.getLogger()
        root.setLevel(level)
        root.addHandler(file_handler)
        root.addHandler(console_handler)

        self.logger = logging.getLogger(__name__)

    def _setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum: int, frame: object) -> None:
        self.logger.info(f"Received signal {signum}, shutting down gracefully...")
        self._stop_event.set()
        self.running = False

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Initialize all service components.  Return False if any step fails."""
        self.logger.info("Initializing Ecobee Temperature Management Service (JWT)...")

        load_secrets()

        email = os.environ.get("ECOBEE_EMAIL")
        password = os.environ.get("ECOBEE_PASSWORD")
        if not email or not password:
            self.logger.error(
                "ECOBEE_EMAIL and ECOBEE_PASSWORD environment variables not set"
            )
            return False

        if not self._init_auth(email, password):
            return False
        if not self._init_schedule():
            return False
        if not self._init_controller():
            return False
        self._init_health_server()

        self.logger.info("All components initialized successfully")
        return True

    def _init_auth(self, email: str, password: str) -> bool:
        self.logger.info("Initializing JWT authentication...")
        self.auth = EcobeeAuthJWT(email, password, config_file="ecobee_jwt.json")

        if self.auth.load_token():
            self.logger.info("Loaded existing JWT token")
            if not self.auth.is_token_valid():
                self.logger.warning("Loaded token is expired, will refresh")
        else:
            self.logger.info("No existing token — performing initial login...")
            if not self.auth.login_and_extract_token(headless=True):
                self.logger.error("Initial login failed")
                return False
            self.logger.info("Initial login successful")

        token = self.auth.get_token()
        if not token:
            self.logger.error("Failed to obtain a valid JWT token")
            return False

        self.logger.info("JWT authentication initialized")
        return True

    def _init_schedule(self) -> bool:
        self.logger.info("Loading schedule...")
        self.schedule = ScheduleEngine("config/schedule.json")
        if not self.schedule.load_schedule():
            self.logger.error("Failed to load schedule")
            return False

        for warning in self.schedule.validate_schedule():
            self.logger.warning(f"Schedule warning: {warning}")

        self.logger.info("Schedule loaded successfully")
        return True

    def _init_controller(self) -> bool:
        self.logger.info("Initializing temperature controller...")
        token = self.auth.get_token()
        self.controller = TemperatureController(token)
        self._apply_ecobee_program()
        self.logger.info("Temperature controller initialized")
        return True

    def _init_health_server(self) -> None:
        self.logger.info("Starting health server...")
        self.health_server = HealthServer(port=8080)
        self.health_server.update_schedule_status(True)
        self.health_server.update_token_status(
            True, self.auth.token_expires_at, None
        )
        self.health_server.start()
        self.logger.info("Health server started")

    # ------------------------------------------------------------------
    # Program application
    # ------------------------------------------------------------------

    def _apply_ecobee_program(self) -> None:
        """Push the night window to the Ecobee program and clear daytime slots."""
        windows = self.schedule.get_windows()
        night = next((w for w in windows if w.enabled), None)
        if not night:
            self.logger.info(
                "No enabled windows configured — skipping Ecobee program update"
            )
            return

        start_hour = night.start.hour
        end_hour = night.end.hour
        temp = night.temperature

        self.logger.info(
            f"Setting night slots ({start_hour:02d}:00–{end_hour:02d}:00) "
            f"to sleep/smart1 @ {temp}°F..."
        )
        if not self.controller.update_night_schedule(
            temp=temp,
            climate_ref="sleep",
            alt_climate_ref="smart1",
            start_hour=start_hour,
            end_hour=end_hour,
        ):
            self.logger.warning("Failed to set night slots")
            return

        self.logger.info(
            f"Clearing daytime slots ({end_hour:02d}:00–{start_hour:02d}:00) to 'home'..."
        )
        if not self.controller.update_night_schedule(
            temp=0,
            climate_ref="home",
            start_hour=end_hour,
            end_hour=start_hour,
            update_heat_temp=False,
        ):
            self.logger.warning("Failed to clear daytime slots")
            return

        self.logger.info("Ecobee program updated successfully")

    # ------------------------------------------------------------------
    # Main loop helpers
    # ------------------------------------------------------------------

    def _check_and_update_temperature(self) -> None:
        """Single iteration of temperature enforcement logic."""
        try:
            if self.schedule.check_for_updates():
                self._apply_ecobee_program()

            expected_temp = self.schedule.get_expected_temperature()
            if expected_temp is None:
                self.logger.info(
                    "Outside active window and no default configured — skipping"
                )
                self.health_server.increment_checks()
                self.consecutive_errors = 0
                return

            self.logger.info(f"Expected temperature: {expected_temp}°F")

            current_temp = self.controller.get_current_temperature_setting()
            if current_temp is None:
                self.logger.error("Failed to read current temperature setting")
                self.health_server.increment_errors()
                self.consecutive_errors += 1
                return

            self.logger.info(f"Current temperature: {current_temp}°F")
            self.health_server.update_temperature_status(current_temp, expected_temp)
            self.health_server.increment_checks()

            if not self.controller.temperatures_match(current_temp, expected_temp):
                self.logger.warning(
                    f"Mismatch: expected {expected_temp}°F, found {current_temp}°F"
                )
                if self.controller.set_temperature(expected_temp):
                    self.logger.info(f"Reverted temperature to {expected_temp}°F")
                    self.health_server.increment_reverts()
                    self.recent_reverts.append(datetime.now())
                    self._check_excessive_changes()
                else:
                    self.logger.error(f"Failed to set temperature to {expected_temp}°F")
                    self.health_server.increment_errors()
                    self.consecutive_errors += 1
                    return
            else:
                self.logger.info("Temperature matches schedule ✓")

            self.consecutive_errors = 0

        except Exception as e:
            self.logger.error(f"Error in temperature check: {e}", exc_info=True)
            self.health_server.increment_errors()
            self.consecutive_errors += 1

    def _check_excessive_changes(self) -> None:
        one_hour_ago = datetime.now() - timedelta(hours=1)
        recent_count = sum(
            1 for t in self.recent_reverts if t > one_hour_ago
        )
        if recent_count > 10:
            self.logger.warning(
                f"Excessive temperature changes: {recent_count} reverts in the last hour"
            )

    def _refresh_token_if_needed(self) -> bool:
        """Re-login if the JWT is near expiry.  Return False when unable to refresh."""
        if not self.auth.needs_refresh():
            return True

        self.logger.info("JWT token needs refresh...")
        if not self.auth.refresh_token():
            self.logger.error("Failed to refresh token")
            self.health_server.update_token_status(False)
            return False

        self.logger.info("Token refreshed successfully")
        self.health_server.increment_token_refreshes()
        self.health_server.update_token_status(
            True, self.auth.token_expires_at, None
        )
        new_token = self.auth.get_token()
        if new_token:
            self.controller.update_token(new_token)
        return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the main service loop until signalled to stop."""
        self.running = True
        self.logger.info("=" * 60)
        self.logger.info("Ecobee Temperature Management Service Started (JWT)")
        self.logger.info(f"Check interval: {self.check_interval_minutes} minutes")
        self.logger.info("=" * 60)

        interval_seconds = self.check_interval_minutes * 60

        while self.running:
            try:
                if not self._refresh_token_if_needed():
                    self.logger.error("No valid token — retrying in 60 s")
                    self._stop_event.wait(60)
                    self._stop_event.clear()
                    continue

                self._check_and_update_temperature()

                if self.consecutive_errors >= self.error_threshold:
                    self.logger.error(
                        f"Reached error threshold ({self.error_threshold} consecutive errors)"
                    )
                    self.consecutive_errors = 0

                if self.running:
                    self.logger.info(
                        f"Sleeping for {self.check_interval_minutes} minutes..."
                    )
                    self._stop_event.wait(interval_seconds)
                    self._stop_event.clear()

            except KeyboardInterrupt:
                self.logger.info("Keyboard interrupt received")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
                self._stop_event.wait(60)
                self._stop_event.clear()

        self.shutdown()

    def shutdown(self) -> None:
        """Flush logs and clean up on exit."""
        self.logger.info("Shutting down service...")
        logging.shutdown()


def main() -> None:
    service = EcobeeServiceJWT()
    if not service.initialize():
        print("Failed to initialize service. Check logs for details.", file=sys.stderr)
        sys.exit(1)
    try:
        service.run()
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
