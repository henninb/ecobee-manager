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

    def __init__(self, access_token: str, base_url: str = None):
        self.access_token = access_token
        if base_url:
            self.BASE_URL = base_url

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
                       duration_minutes: int = 60) -> bool:
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

    def get_sensors(self, thermostat_id: Optional[str] = None) -> Optional[List[Dict]]:
        """Get list of remote sensors with temperature and occupancy"""
        url = f"{self.BASE_URL}/thermostat"
        params = {
            'format': 'json',
            'body': '{"selection":{"selectionType":"registered","selectionMatch":"","includeSensors":true}}'
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
            if not thermostats:
                return None

            thermostat = thermostats[0] if not thermostat_id else next(
                (t for t in thermostats if t['identifier'] == thermostat_id), None
            )
            if not thermostat:
                return None

            sensors = []
            for s in thermostat.get('remoteSensors', []):
                capabilities = {c['type']: c['value'] for c in s.get('capability', [])}
                temp_raw = capabilities.get('temperature')
                temp_f = int(temp_raw) / 10 if temp_raw and temp_raw != 'unknown' else None
                sensors.append({
                    'name': s.get('name'),
                    'type': s.get('type'),
                    'in_use': s.get('inUse', False),
                    'temperature': temp_f,
                    'occupancy': capabilities.get('occupancy'),
                })
            return sensors

        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting sensors: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting sensors: {e}")
            return None

    def get_climate_sensor_info(self, thermostat_id: Optional[str] = None) -> Optional[Dict]:
        """
        Fetch thermostat with program and sensor data.
        Returns dict with: thermostat_id, current_climate_ref, climates, raw_sensors
        """
        url = f"{self.BASE_URL}/thermostat"
        params = {
            'format': 'json',
            'body': '{"selection":{"selectionType":"registered","selectionMatch":"","includeProgram":true,"includeSensors":true,"includeEvents":true}}'
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
            if not thermostats:
                return None

            thermostat = thermostats[0] if not thermostat_id else next(
                (t for t in thermostats if t['identifier'] == thermostat_id), None
            )
            if not thermostat:
                return None

            program = thermostat.get('program', {})
            current_climate_ref = program.get('currentClimateRef', 'home')

            # If a hold event is active, note it
            for event in thermostat.get('events', []):
                if event.get('running') and event.get('type') == 'hold':
                    hold_climate = event.get('holdClimateRef')
                    if hold_climate:
                        current_climate_ref = hold_climate
                    break

            # Build name→{id,name} map from existing climate sensor lists.
            # Climate sensors use a different ID format (e.g. "rs2:102:1") than
            # remoteSensors (e.g. "rs2:102"), so we use climate-derived IDs when
            # constructing sensor participation updates.
            climate_sensor_map: Dict[str, Dict] = {}
            for climate in program.get('climates', []):
                for s in climate.get('sensors', []):
                    name = s.get('name')
                    if name and name not in climate_sensor_map:
                        climate_sensor_map[name] = {'id': s['id'], 'name': name}

            return {
                'thermostat_id': thermostat['identifier'],
                'current_climate_ref': current_climate_ref,
                'climates': program.get('climates', []),
                'schedule': program.get('schedule', []),
                'raw_sensors': thermostat.get('remoteSensors', []),
                'climate_sensor_map': climate_sensor_map,
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting climate/sensor info: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting climate/sensor info: {e}")
            return None

    def select_sensors_toward_target(self, raw_sensors: List[Dict], target_temp: float,
                                      climate_sensor_map: Optional[Dict] = None) -> List[Dict]:
        """
        From all remote sensors, pick the subset whose average is closest to target_temp.
        Strategy: if current average > target, prefer cooler sensors; if below, prefer warmer.
        Always returns at least one sensor.

        climate_sensor_map: name→{id,name} dict built from existing climate sensor lists.
        Climate sensor IDs use a different format than remoteSensors IDs (e.g. "rs2:102:1"
        vs "rs2:102"), so we use the map to get the correct ID for each selected sensor.
        """
        readable = []
        for s in raw_sensors:
            caps = {c['type']: c['value'] for c in s.get('capability', [])}
            temp_raw = caps.get('temperature')
            if temp_raw and temp_raw != 'unknown':
                readable.append({'id': s['id'], 'name': s['name'], 'temp': int(temp_raw) / 10})

        if not readable:
            return [{'id': s['id'], 'name': s['name']} for s in raw_sensors]

        avg = sum(s['temp'] for s in readable) / len(readable)
        logger.info(f"Sensor average: {avg:.1f}°F, target: {target_temp}°F")

        if avg > target_temp:
            candidates = [s for s in readable if s['temp'] <= target_temp]
            if not candidates:
                candidates = sorted(readable, key=lambda s: s['temp'])[:max(1, len(readable) // 2)]
        elif avg < target_temp:
            candidates = [s for s in readable if s['temp'] >= target_temp]
            if not candidates:
                candidates = sorted(readable, key=lambda s: s['temp'], reverse=True)[:max(1, len(readable) // 2)]
        else:
            candidates = readable

        result = []
        for s in candidates:
            # Use climate-derived ID if available (correct format for API), else fall back
            if climate_sensor_map and s['name'] in climate_sensor_map:
                result.append(climate_sensor_map[s['name']])
            else:
                result.append({'id': s['id'], 'name': s['name']})
        return result

    def build_climate_update_body(self, thermostat_id: str, climate_ref: str,
                                  all_climates: List[Dict], selected_sensors: List[Dict],
                                  schedule: Optional[List] = None) -> Dict:
        """Build the POST body for a climate sensor update (exposed for dry-run/debugging)"""
        updated_climates = []
        for climate in all_climates:
            if climate.get('climateRef') == climate_ref:
                updated_climates.append({**climate, 'sensors': selected_sensors})
            else:
                updated_climates.append(climate)

        program: Dict = {"climates": updated_climates}
        if schedule is not None:
            program["schedule"] = schedule

        return {
            "selection": {
                "selectionType": "thermostats",
                "selectionMatch": thermostat_id
            },
            "thermostat": {
                "program": program
            }
        }

    def update_climate_sensors(self, thermostat_id: str, climate_ref: str,
                               all_climates: List[Dict], selected_sensors: List[Dict],
                               schedule: Optional[List] = None) -> bool:
        """
        Update sensor participation for a climate.
        Sends all climates back with only the target climate's sensor list changed.
        Only writable fields are included to avoid 500 errors from read-only fields.
        """
        url = f"{self.BASE_URL}/thermostat"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        body = self.build_climate_update_body(thermostat_id, climate_ref, all_climates, selected_sensors, schedule)

        try:
            response = requests.post(url, json=body, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()
            status = result.get('status', {})
            if status.get('code') == 0:
                logger.info(f"Updated sensors for climate '{climate_ref}'")
                return True
            else:
                logger.error(f"API returned error: {status}")
                return False
        except requests.exceptions.RequestException as e:
            try:
                body = e.response.text if e.response is not None else "no response"
            except Exception:
                body = "no response"
            logger.error(f"Error updating climate sensors: {e} — response: {body}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error updating climate sensors: {e}")
            return False

    def update_night_schedule(self, temp: int, climate_ref: str = "sleep",
                              alt_climate_ref: Optional[str] = None,
                              start_hour: int = 23, end_hour: int = 6,
                              thermostat_id: Optional[str] = None,
                              update_heat_temp: bool = True,
                              dry_run: bool = False) -> bool:
        """
        Update the Ecobee program from start_hour to end_hour every day of the week.

        If alt_climate_ref is given, alternates hourly: climate_ref, alt_climate_ref,
        climate_ref, ... starting with climate_ref at start_hour.

        If update_heat_temp is True (default), also updates the primary climate's heatTemp to temp.

        Slot layout: slot 0 = 00:00, slot 1 = 00:30, ..., slot 47 = 23:30.
        """
        info = self.get_climate_sensor_info(thermostat_id)
        if not info:
            return False

        tid = info['thermostat_id']
        climates = info['climates']
        schedule = info['schedule']

        target = next((c for c in climates if c.get('climateRef') == climate_ref), None)
        if not target:
            available = [c.get('climateRef') for c in climates]
            logger.error(f"Climate '{climate_ref}' not found. Available: {available}")
            return False

        if alt_climate_ref:
            alt_target = next((c for c in climates if c.get('climateRef') == alt_climate_ref), None)
            if not alt_target:
                available = [c.get('climateRef') for c in climates]
                logger.error(f"Alt climate '{alt_climate_ref}' not found. Available: {available}")
                return False

        # Build updated climates, optionally updating heatTemp for the primary climate
        ecobee_temp = temp * 10
        updated_climates = []
        for climate in climates:
            if update_heat_temp and climate.get('climateRef') == climate_ref:
                updated_climates.append({**climate, 'heatTemp': ecobee_temp})
            else:
                updated_climates.append(climate)

        # Ordered list of hours in the window
        # start_hour == end_hour → all 24 hours
        # start_hour > end_hour  → crosses midnight (e.g. 19:00–06:00)
        # start_hour < end_hour  → same day      (e.g. 06:00–19:00)
        if start_hour == end_hour:
            night_hours = list(range(24))
        elif start_hour > end_hour:
            night_hours = list(range(start_hour, 24)) + list(range(0, end_hour))
        else:
            night_hours = list(range(start_hour, end_hour))

        updated_schedule = []
        for day_slots in schedule:
            updated_day = list(day_slots)
            for i, hour in enumerate(night_hours):
                if alt_climate_ref and i % 2 == 1:
                    ref = alt_climate_ref
                else:
                    ref = climate_ref
                slot = hour * 2
                updated_day[slot] = ref
                updated_day[slot + 1] = ref
            updated_schedule.append(updated_day)

        body = {
            "selection": {
                "selectionType": "thermostats",
                "selectionMatch": tid
            },
            "thermostat": {
                "program": {
                    "climates": updated_climates,
                    "schedule": updated_schedule
                }
            }
        }

        if dry_run:
            return body

        url = f"{self.BASE_URL}/thermostat"
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.post(url, json=body, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()
            status = result.get('status', {})
            if status.get('code') == 0:
                logger.info(
                    f"Schedule updated: '{climate_ref}' at {temp}°F "
                    f"for {start_hour:02d}:00-{end_hour:02d}:00 every day"
                )
                return True
            else:
                logger.error(f"API error (code {status.get('code')}): {status.get('message', status)}")
                return False
        except requests.exceptions.RequestException as e:
            resp_text = e.response.text if e.response is not None else "no response"
            logger.error(f"Error updating night schedule: {e} — response: {resp_text}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error updating night schedule: {e}")
            return False

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
    from ecobee_auth_jwt import EcobeeAuthJWT

    logging.basicConfig(level=logging.DEBUG)

    # Load credentials from env.secrets.enc (SOPS) or env.secrets
    from secrets_loader import load_secrets
    load_secrets()

    email = os.environ.get('ECOBEE_EMAIL')
    password = os.environ.get('ECOBEE_PASSWORD')
    if not email or not password:
        print("Error: ECOBEE_EMAIL and ECOBEE_PASSWORD environment variables not set")
        exit(1)

    auth = EcobeeAuthJWT(email, password)
    token = auth.get_token()

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
