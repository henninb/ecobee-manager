#!/usr/bin/env python3
"""
Schedule Engine Module
Handles parsing and lookup of temperature schedules
"""

import json
import logging
from datetime import datetime, time
from typing import Optional, Dict, List
from pathlib import Path
import pytz

logger = logging.getLogger(__name__)


class ScheduleEntry:
    """Represents a single schedule entry"""

    def __init__(self, time_str: str, temperature: int):
        self.time = datetime.strptime(time_str, "%H:%M").time()
        self.temperature = temperature

    def __repr__(self):
        return f"ScheduleEntry(time={self.time}, temp={self.temperature}°F)"


class ScheduleEngine:
    """Manages temperature schedule and lookups"""

    def __init__(self, schedule_file: str = "config/schedule.json"):
        self.schedule_file = schedule_file
        self.timezone = None
        self.default_temperature = 68
        self.schedule: Dict[str, List[ScheduleEntry]] = {}
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

            # Load default temperature
            self.default_temperature = data.get('default_temperature', 68)

            # Load schedule
            self.schedule = {}
            schedule_data = data.get('schedule', {})

            for day, entries in schedule_data.items():
                day_lower = day.lower()
                if day_lower not in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
                    logger.warning(f"Invalid day in schedule: {day}")
                    continue

                parsed_entries = []
                for entry in entries:
                    try:
                        time_str = entry['time']
                        temp = int(entry['temperature'])
                        parsed_entries.append(ScheduleEntry(time_str, temp))
                    except (KeyError, ValueError) as e:
                        logger.warning(f"Invalid schedule entry for {day}: {entry} - {e}")
                        continue

                # Sort entries by time
                parsed_entries.sort(key=lambda x: x.time)
                self.schedule[day_lower] = parsed_entries

            self.last_modified = Path(self.schedule_file).stat().st_mtime
            logger.info(f"Loaded schedule from {self.schedule_file}")
            logger.info(f"Timezone: {self.timezone}, Default temp: {self.default_temperature}°F")
            logger.info(f"Loaded {len(self.schedule)} days of schedules")
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

    def get_expected_temperature(self, dt: Optional[datetime] = None) -> int:
        """
        Get expected temperature for given datetime
        If dt is None, uses current time in configured timezone
        """
        if dt is None:
            dt = datetime.now(self.timezone)
        elif dt.tzinfo is None:
            # Assume naive datetime is in configured timezone
            dt = self.timezone.localize(dt)

        # Get day name
        day_name = dt.strftime('%A').lower()

        # Get schedule for this day
        day_schedule = self.schedule.get(day_name, [])

        if not day_schedule:
            logger.debug(f"No schedule for {day_name}, using default: {self.default_temperature}°F")
            return self.default_temperature

        # Find the most recent schedule entry before current time
        current_time = dt.time()
        applicable_entry = None

        for entry in day_schedule:
            if entry.time <= current_time:
                applicable_entry = entry
            else:
                break  # Entries are sorted, no need to continue

        if applicable_entry:
            logger.debug(f"Found schedule entry for {day_name} at {applicable_entry.time}: {applicable_entry.temperature}°F")
            return applicable_entry.temperature
        else:
            # No entry found before current time, use last entry from previous day
            previous_day_temp = self._get_last_temperature_from_previous_day(day_name)
            if previous_day_temp is not None:
                logger.debug(f"No entry before current time, using previous day's last entry: {previous_day_temp}°F")
                return previous_day_temp
            else:
                logger.debug(f"No previous entry found, using default: {self.default_temperature}°F")
                return self.default_temperature

    def _get_last_temperature_from_previous_day(self, current_day: str) -> Optional[int]:
        """Get the last temperature from the previous day's schedule"""
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        try:
            current_idx = days.index(current_day)
            previous_day = days[(current_idx - 1) % 7]

            previous_schedule = self.schedule.get(previous_day, [])
            if previous_schedule:
                return previous_schedule[-1].temperature
        except (ValueError, IndexError):
            pass

        return None

    def get_schedule_summary(self) -> Dict:
        """Get a summary of the current schedule"""
        summary = {
            'timezone': str(self.timezone),
            'default_temperature': self.default_temperature,
            'days_configured': len(self.schedule),
            'schedule': {}
        }

        for day, entries in self.schedule.items():
            summary['schedule'][day] = [
                {'time': entry.time.strftime('%H:%M'), 'temperature': entry.temperature}
                for entry in entries
            ]

        return summary

    def validate_schedule(self) -> List[str]:
        """Validate the schedule and return list of warnings/errors"""
        warnings = []

        # Check if all days are defined
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for day in days:
            if day not in self.schedule or not self.schedule[day]:
                warnings.append(f"No schedule defined for {day}")

        # Check for reasonable temperature ranges
        for day, entries in self.schedule.items():
            for entry in entries:
                if entry.temperature < 40 or entry.temperature > 90:
                    warnings.append(f"Unusual temperature {entry.temperature}°F on {day} at {entry.time}")

        return warnings


if __name__ == "__main__":
    # Test the schedule engine
    logging.basicConfig(level=logging.DEBUG)

    engine = ScheduleEngine("config/schedule.json")
    if engine.load_schedule():
        print("\nSchedule loaded successfully!")
        print(f"\nCurrent expected temperature: {engine.get_expected_temperature()}°F")

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
