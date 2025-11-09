#!/usr/bin/env python3
"""
Temperature Controller Module
Handles checking and setting thermostat temperature via Ecobee API
"""

import logging
import requests
from typing import Optional, Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)


class TemperatureController:
    """Controls and monitors Ecobee thermostat temperature"""

    BASE_URL = "https://api.ecobee.com/1"
    TEMPERATURE_TOLERANCE = 0.5  # ±0.5°F tolerance

    def __init__(self, access_token: str):
        self.access_token = access_token

    def update_token(self, access_token: str):
        """Update the access token"""
        self.access_token = access_token

    def get_thermostats(self) -> Optional[List[Dict]]:
        """Get list of thermostats"""
        url = f"{self.BASE_URL}/thermostat"
        params = {
            'format': 'json',
            'body': '{"selection":{"selectionType":"registered","selectionMatch":"","includeRuntime":true,"includeSettings":true}}'
        }
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()

            thermostats = data.get('thermostatList', [])
            logger.info(f"Found {len(thermostats)} thermostat(s)")
            return thermostats

        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting thermostats: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting thermostats: {e}")
            return None

    def get_current_temperature_setting(self, thermostat_id: Optional[str] = None) -> Optional[int]:
        """
        Get current temperature setting (hold or schedule)
        Returns temperature in Fahrenheit
        """
        thermostats = self.get_thermostats()
        if not thermostats:
            return None

        # Use first thermostat if none specified
        thermostat = thermostats[0] if not thermostat_id else None
        if thermostat_id:
            thermostat = next((t for t in thermostats if t['identifier'] == thermostat_id), None)

        if not thermostat:
            logger.error(f"Thermostat {thermostat_id} not found")
            return None

        # Check for active hold/event first
        events = thermostat.get('events', [])
        if events:
            # Get the most recent active hold
            for event in events:
                if event.get('running', False) and event.get('type') == 'hold':
                    # Get heat or cool setting depending on climate
                    heat_hold = event.get('heatHoldTemp')
                    cool_hold = event.get('coolHoldTemp')

                    # Convert from Ecobee format (degrees F * 10) to Fahrenheit
                    if heat_hold is not None:
                        temp_f = heat_hold / 10
                        logger.debug(f"Active heat hold found: {temp_f}°F")
                        return int(temp_f)
                    if cool_hold is not None:
                        temp_f = cool_hold / 10
                        logger.debug(f"Active cool hold found: {temp_f}°F")
                        return int(temp_f)

        # No active hold, check runtime settings
        runtime = thermostat.get('runtime', {})
        desired_heat = runtime.get('desiredHeat')
        desired_cool = runtime.get('desiredCool')

        # Convert from Ecobee format (degrees F * 10) to Fahrenheit
        if desired_heat is not None:
            temp_f = desired_heat / 10
            logger.debug(f"Desired heat from runtime: {temp_f}°F")
            return int(temp_f)
        if desired_cool is not None:
            temp_f = desired_cool / 10
            logger.debug(f"Desired cool from runtime: {temp_f}°F")
            return int(temp_f)

        logger.warning("Could not determine current temperature setting")
        return None

    def set_temperature(self, target_temp: int, thermostat_id: Optional[str] = None,
                       duration_minutes: int = 30) -> bool:
        """
        Set thermostat temperature hold

        Args:
            target_temp: Target temperature in Fahrenheit
            thermostat_id: Optional thermostat ID (uses first if not specified)
            duration_minutes: Duration of hold in minutes (default 30 min)

        Returns:
            True if successful, False otherwise
        """
        # Get thermostat info
        thermostats = self.get_thermostats()
        if not thermostats:
            return False

        # Use first thermostat if none specified
        thermostat = thermostats[0] if not thermostat_id else None
        if thermostat_id:
            thermostat = next((t for t in thermostats if t['identifier'] == thermostat_id), None)

        if not thermostat:
            logger.error(f"Thermostat {thermostat_id} not found")
            return False

        tid = thermostat['identifier']

        # Convert Fahrenheit to Ecobee format (degrees F * 10)
        ecobee_temp = int(target_temp * 10)

        url = f"{self.BASE_URL}/thermostat"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

        # Create hold function
        body = {
            "selection": {
                "selectionType": "thermostats",
                "selectionMatch": tid
            },
            "functions": [
                {
                    "type": "setHold",
                    "params": {
                        "holdType": "holdHours",
                        "holdHours": duration_minutes // 60 if duration_minutes >= 60 else 1,
                        "heatHoldTemp": ecobee_temp,
                        "coolHoldTemp": ecobee_temp
                    }
                }
            ]
        }

        try:
            response = requests.post(url, json=body, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()

            status = result.get('status', {})
            if status.get('code') == 0:
                logger.info(f"Successfully set temperature to {target_temp}°F")
                return True
            else:
                logger.error(f"API returned error: {status}")
                return False

        except requests.exceptions.RequestException as e:
            logger.error(f"Error setting temperature: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error setting temperature: {e}")
            return False

    def temperatures_match(self, current: int, expected: int) -> bool:
        """
        Check if temperatures match within tolerance

        Args:
            current: Current temperature setting
            expected: Expected temperature from schedule

        Returns:
            True if within tolerance, False otherwise
        """
        diff = abs(current - expected)
        matches = diff <= self.TEMPERATURE_TOLERANCE
        logger.debug(f"Temperature comparison: current={current}°F, expected={expected}°F, diff={diff}°F, matches={matches}")
        return matches

    def get_thermostat_info(self, thermostat_id: Optional[str] = None) -> Optional[Dict]:
        """Get detailed thermostat information"""
        thermostats = self.get_thermostats()
        if not thermostats:
            return None

        # Use first thermostat if none specified
        thermostat = thermostats[0] if not thermostat_id else None
        if thermostat_id:
            thermostat = next((t for t in thermostats if t['identifier'] == thermostat_id), None)

        if not thermostat:
            return None

        # Extract useful information
        runtime = thermostat.get('runtime', {})
        settings = thermostat.get('settings', {})

        info = {
            'identifier': thermostat.get('identifier'),
            'name': thermostat.get('name'),
            'model': thermostat.get('modelNumber'),
            'actual_temperature': runtime.get('actualTemperature', 0) / 10,  # Convert to F
            'desired_heat': runtime.get('desiredHeat', 0) / 10,
            'desired_cool': runtime.get('desiredCool', 0) / 10,
            'hvac_mode': settings.get('hvacMode'),
            'has_active_hold': len(thermostat.get('events', [])) > 0
        }

        return info


if __name__ == "__main__":
    # Test the temperature controller
    import os
    from ecobee_auth import EcobeeAuth

    logging.basicConfig(level=logging.DEBUG)

    api_key = os.environ.get('ECOBEE_API_KEY')
    if not api_key:
        print("Error: ECOBEE_API_KEY environment variable not set")
        exit(1)

    auth = EcobeeAuth(api_key)
    token = auth.get_access_token()

    if not token:
        print("Error: Could not get access token")
        exit(1)

    controller = TemperatureController(token)

    print("\nGetting thermostat info...")
    info = controller.get_thermostat_info()
    if info:
        print(f"Name: {info['name']}")
        print(f"Model: {info['model']}")
        print(f"Actual temp: {info['actual_temperature']}°F")
        print(f"Desired heat: {info['desired_heat']}°F")
        print(f"HVAC mode: {info['hvac_mode']}")

    print("\nGetting current temperature setting...")
    current = controller.get_current_temperature_setting()
    if current:
        print(f"Current setting: {current}°F")
