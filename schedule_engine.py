#!/usr/bin/env python3
"""
Schedule Engine Module
Handles parsing and lookup of temperature windows
"""

import json
import logging
from datetime import datetime, time as dt_time
from typing import Optional, Dict, List
from pathlib import Path
import pytz

logger = logging.getLogger(__name__)


class TimeWindow:
    """Represents a named time window with a target temperature"""

    def __init__(self, name: str, start: str, end: str, temperature: int, enabled: bool = True):
        self.name = name
        self.start = datetime.strptime(start, "%H:%M").time()
        self.end = datetime.strptime(end, "%H:%M").time()
        self.temperature = temperature
        self.enabled = enabled

    def contains(self, t: dt_time) -> bool:
        """Return True if time t falls within this window (handles midnight crossing)"""
        if self.start < self.end:
            return self.start <= t < self.end
        else:
            # Window crosses midnight (e.g. 19:00–06:00)
            return t >= self.start or t < self.end

    def __repr__(self):
        return f"TimeWindow(name={self.name}, {self.start}–{self.end}, {self.temperature}°F, enabled={self.enabled})"


class ScheduleEngine:
    """Manages temperature windows and lookups"""

    def __init__(self, schedule_file: str = "config/schedule.json"):
        self.schedule_file = schedule_file
        self.timezone = None
        self.windows: List[TimeWindow] = []
        self.last_modified = None

    def load_schedule(self) -> bool:
        """Load schedule from JSON file"""
        try:
            if not Path(self.schedule_file).exists():
                logger.error(f"Schedule file not found: {self.schedule_file}")
                return False

            with open(self.schedule_file, 'r') as f:
                data = json.load(f)

            # Load timezone
            tz_str = data.get('timezone', 'America/Chicago')
            try:
                self.timezone = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(f"Unknown timezone: {tz_str}, using America/Chicago")
                self.timezone = pytz.timezone('America/Chicago')

            # Load windows
            self.windows = []
            for entry in data.get('windows', []):
                try:
                    self.windows.append(TimeWindow(
                        name=entry['name'],
                        start=entry['start'],
                        end=entry['end'],
                        temperature=int(entry['temperature']),
                        enabled=entry.get('enabled', True),
                    ))
                except (KeyError, ValueError) as e:
                    logger.warning(f"Invalid window entry {entry}: {e}")

            self.last_modified = Path(self.schedule_file).stat().st_mtime
            logger.info(f"Loaded schedule from {self.schedule_file}")
            logger.info(f"Timezone: {self.timezone}")
            logger.info(f"Loaded {len(self.windows)} window(s)")
            for w in self.windows:
                status = "enabled" if w.enabled else "disabled"
                logger.info(f"  Window '{w.name}': {w.start}–{w.end} @ {w.temperature}°F ({status})")
            return True

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in schedule file: {e}")
            return False
        except Exception as e:
            logger.error(f"Error loading schedule: {e}")
            return False

    def check_for_updates(self) -> bool:
        """Check if schedule file has been modified and reload if needed"""
        try:
            current_mtime = Path(self.schedule_file).stat().st_mtime
            if self.last_modified and current_mtime > self.last_modified:
                logger.info("Schedule file modified, reloading...")
                return self.load_schedule()
        except Exception as e:
            logger.error(f"Error checking schedule updates: {e}")
        return False

    def get_expected_temperature(self, dt: Optional[datetime] = None) -> Optional[int]:
        """
        Get expected temperature for the given datetime.
        Returns the temperature of the first matching enabled window,
        or None if the current time falls outside all windows.
        """
        if dt is None:
            dt = datetime.now(self.timezone)
        elif dt.tzinfo is None:
            dt = self.timezone.localize(dt)

        current_time = dt.time()

        for window in self.windows:
            if window.enabled and window.contains(current_time):
                logger.debug(f"Time {current_time} matched window '{window.name}': {window.temperature}°F")
                return window.temperature

        logger.debug(f"Time {current_time} is outside all active windows")
        return None

    def get_windows(self) -> List[TimeWindow]:
        """Return the list of configured windows"""
        return self.windows

    def get_schedule_summary(self) -> Dict:
        """Get a summary of the current schedule"""
        return {
            'timezone': str(self.timezone),
            'windows': [
                {
                    'name': w.name,
                    'start': w.start.strftime('%H:%M'),
                    'end': w.end.strftime('%H:%M'),
                    'temperature': w.temperature,
                    'enabled': w.enabled,
                }
                for w in self.windows
            ]
        }

    def validate_schedule(self) -> List[str]:
        """Validate the schedule and return a list of warnings/errors"""
        warnings = []

        if not self.windows:
            warnings.append("No windows defined — temperature enforcement is disabled")
            return warnings

        for w in self.windows:
            if w.temperature < 40 or w.temperature > 90:
                warnings.append(f"Unusual temperature {w.temperature}°F in window '{w.name}'")
            if w.start == w.end:
                warnings.append(f"Window '{w.name}' has identical start and end time ({w.start})")

        return warnings


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    engine = ScheduleEngine("config/schedule.json")
    if engine.load_schedule():
        print("\nSchedule loaded successfully!")
        print(f"\nCurrent expected temperature: {engine.get_expected_temperature()}")

        print("\nSchedule summary:")
        summary = engine.get_schedule_summary()
        print(json.dumps(summary, indent=2))

        print("\nValidation warnings:")
        warnings = engine.validate_schedule()
        if warnings:
            for warning in warnings:
                print(f"  - {warning}")
        else:
            print("  No warnings")
