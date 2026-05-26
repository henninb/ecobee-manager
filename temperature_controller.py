#!/usr/bin/env python3
"""Temperature Controller — check and set Ecobee thermostat temperature via the API."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.ecobee.com/1"
_TEMP_FACTOR = 10       # Ecobee stores setpoints as °F × 10
_HEAT_FLOOR = 600       # 60 °F in Ecobee units — low enough to never trigger heating
_DEFAULT_HTTP_TIMEOUT = int(os.getenv("ECOBEE_HTTP_TIMEOUT", "10"))


def _to_ecobee(temp_f: int | float) -> int:
    """Convert Fahrenheit to Ecobee integer units (°F × 10)."""
    return int(temp_f * _TEMP_FACTOR)


def _from_ecobee(ecobee_temp: int | float) -> float:
    """Convert Ecobee integer units to Fahrenheit."""
    return ecobee_temp / _TEMP_FACTOR


class EcobeeAPIError(Exception):
    """Raised when the Ecobee API returns a non-zero status code."""


class TemperatureController:
    """Control and monitor an Ecobee thermostat via its REST API."""

    TEMPERATURE_TOLERANCE = 0.5  # ±0.5 °F

    def __init__(
        self,
        access_token: str,
        base_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.access_token = access_token
        self.base_url = base_url or _DEFAULT_BASE_URL
        self.timeout = timeout if timeout is not None else _DEFAULT_HTTP_TIMEOUT

    def update_token(self, access_token: str) -> None:
        """Replace the bearer token used for subsequent API calls."""
        self.access_token = access_token

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _get(self, params: dict[str, Any]) -> dict | None:
        """GET /thermostat; return parsed JSON or None on transport error."""
        url = f"{self.base_url}/thermostat"
        try:
            response = requests.get(url, params=params, headers=self._headers, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"GET /thermostat failed: {e}")
            return None

    def _post(self, body: dict) -> dict | None:
        """POST /thermostat; return parsed JSON or None on transport error."""
        url = f"{self.base_url}/thermostat"
        try:
            response = requests.post(url, json=body, headers=self._headers, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            resp_text = (
                e.response.text
                if getattr(e, "response", None) is not None
                else "no response body"
            )
            logger.error(f"POST /thermostat failed: {e} — {resp_text}")
            return None

    @staticmethod
    def _ok(result: dict, operation: str) -> bool:
        """Return True when the API status code is 0; log and return False otherwise."""
        status = result.get("status", {})
        if status.get("code") == 0:
            return True
        logger.error(
            f"{operation} — API error (code {status.get('code')}): "
            f"{status.get('message', status)}"
        )
        return False

    # ------------------------------------------------------------------
    # Thermostat lookup
    # ------------------------------------------------------------------

    def get_thermostats(self) -> list[dict] | None:
        """Return all registered thermostats, or None on error."""
        data = self._get({
            "format": "json",
            "body": (
                '{"selection":{"selectionType":"registered","selectionMatch":"",'
                '"includeRuntime":true,"includeSettings":true}}'
            ),
        })
        if data is None:
            return None
        thermostats = data.get("thermostatList", [])
        logger.info(f"Found {len(thermostats)} thermostat(s)")
        return thermostats

    def _get_thermostat(self, thermostat_id: str | None = None) -> dict | None:
        """Return the thermostat matching *thermostat_id*, or the first one found."""
        thermostats = self.get_thermostats()
        if not thermostats:
            return None
        if thermostat_id is None:
            return thermostats[0]
        match = next(
            (t for t in thermostats if t["identifier"] == thermostat_id), None
        )
        if match is None:
            logger.error(f"Thermostat {thermostat_id} not found")
        return match

    def _fetch_thermostat_data(
        self, body: str, thermostat_id: str | None = None
    ) -> dict | None:
        """GET /thermostat with *body* and return the matching thermostat dict."""
        data = self._get({"format": "json", "body": body})
        if data is None:
            return None
        thermostats = data.get("thermostatList", [])
        if not thermostats:
            return None
        if thermostat_id is None:
            return thermostats[0]
        match = next(
            (t for t in thermostats if t["identifier"] == thermostat_id), None
        )
        if match is None:
            logger.error(f"Thermostat {thermostat_id} not found")
        return match

    # ------------------------------------------------------------------
    # Temperature reading
    # ------------------------------------------------------------------

    def get_current_temperature_setting(
        self, thermostat_id: str | None = None, mode: str = "heating"
    ) -> int | None:
        """Return the active temperature setpoint in °F (hold overrides schedule).

        Pass mode='cooling' to read the cool setpoint, 'heating' for heat.
        """
        thermostat = self._get_thermostat(thermostat_id)
        if thermostat is None:
            return None

        if mode == "cooling":
            hold_keys = (("coolHoldTemp", "cool"), ("heatHoldTemp", "heat"))
            runtime_keys = (("desiredCool", "cool"), ("desiredHeat", "heat"))
        else:
            hold_keys = (("heatHoldTemp", "heat"), ("coolHoldTemp", "cool"))
            runtime_keys = (("desiredHeat", "heat"), ("desiredCool", "cool"))

        for event in thermostat.get("events", []):
            if event.get("running", False) and event.get("type") == "hold":
                for key, label in hold_keys:
                    raw = event.get(key)
                    if raw is not None:
                        temp_f = _from_ecobee(raw)
                        logger.debug(f"Active {label} hold: {temp_f}°F")
                        return int(temp_f)

        runtime = thermostat.get("runtime", {})
        for key, label in runtime_keys:
            raw = runtime.get(key)
            if raw is not None:
                temp_f = _from_ecobee(raw)
                logger.debug(f"Runtime {label} setpoint: {temp_f}°F")
                return int(temp_f)

        logger.warning("Could not determine current temperature setting")
        return None

    def temperatures_match(self, current: int, expected: int) -> bool:
        """Return True when *current* and *expected* are within tolerance."""
        diff = abs(current - expected)
        matches = diff <= self.TEMPERATURE_TOLERANCE
        logger.debug(
            f"Temp comparison: current={current}°F expected={expected}°F "
            f"diff={diff}°F matches={matches}"
        )
        return matches

    # ------------------------------------------------------------------
    # Hold helpers
    # ------------------------------------------------------------------

    def _set_hold(
        self,
        tid: str,
        heat_ecobee: int,
        cool_ecobee: int,
        duration_minutes: int,
    ) -> bool:
        """Send a setHold function call for thermostat *tid*."""
        hold_hours = max(1, duration_minutes // 60)
        body = {
            "selection": {"selectionType": "thermostats", "selectionMatch": tid},
            "functions": [{
                "type": "setHold",
                "params": {
                    "holdType": "holdHours",
                    "holdHours": hold_hours,
                    "heatHoldTemp": heat_ecobee,
                    "coolHoldTemp": cool_ecobee,
                },
            }],
        }
        result = self._post(body)
        return result is not None and self._ok(result, "setHold")

    def set_temperature(
        self,
        target_temp: int,
        thermostat_id: str | None = None,
        duration_minutes: int = 60,
    ) -> bool:
        """Set a heat-and-cool hold to *target_temp* °F."""
        thermostat = self._get_thermostat(thermostat_id)
        if thermostat is None:
            return False
        ecobee_temp = _to_ecobee(target_temp)
        ok = self._set_hold(thermostat["identifier"], ecobee_temp, ecobee_temp, duration_minutes)
        if ok:
            logger.info(f"Set temperature to {target_temp}°F")
        return ok

    def set_cool_temperature(
        self,
        target_temp: int,
        thermostat_id: str | None = None,
        duration_minutes: int = 60,
    ) -> bool:
        """Set a cooling hold to *target_temp* °F with a 60 °F heat floor."""
        thermostat = self._get_thermostat(thermostat_id)
        if thermostat is None:
            return False
        ok = self._set_hold(
            thermostat["identifier"],
            _HEAT_FLOOR,
            _to_ecobee(target_temp),
            duration_minutes,
        )
        if ok:
            logger.info(f"Set cool temperature to {target_temp}°F")
        return ok

    # ------------------------------------------------------------------
    # Sensor helpers
    # ------------------------------------------------------------------

    def get_sensors(self, thermostat_id: str | None = None) -> list[dict] | None:
        """Return remote sensors with temperature and occupancy data."""
        thermostat = self._fetch_thermostat_data(
            '{"selection":{"selectionType":"registered","selectionMatch":"",'
            '"includeSensors":true}}',
            thermostat_id,
        )
        if thermostat is None:
            return None

        sensors = []
        for s in thermostat.get("remoteSensors", []):
            caps = {c["type"]: c["value"] for c in s.get("capability", [])}
            temp_raw = caps.get("temperature")
            temp_f = (
                _from_ecobee(int(temp_raw))
                if temp_raw and temp_raw != "unknown"
                else None
            )
            sensors.append({
                "name": s.get("name"),
                "type": s.get("type"),
                "in_use": s.get("inUse", False),
                "temperature": temp_f,
                "occupancy": caps.get("occupancy"),
            })
        return sensors

    def get_climate_sensor_info(
        self, thermostat_id: str | None = None
    ) -> dict | None:
        """Return program, climate, and sensor data for a thermostat."""
        thermostat = self._fetch_thermostat_data(
            '{"selection":{"selectionType":"registered","selectionMatch":"",'
            '"includeProgram":true,"includeSensors":true,"includeEvents":true}}',
            thermostat_id,
        )
        if thermostat is None:
            return None

        program = thermostat.get("program", {})
        current_climate_ref = program.get("currentClimateRef", "home")

        for event in thermostat.get("events", []):
            if event.get("running") and event.get("type") == "hold":
                hold_climate = event.get("holdClimateRef")
                if hold_climate:
                    current_climate_ref = hold_climate
                break

        # Climate sensor IDs use a different format than remoteSensor IDs
        # (e.g. "rs2:102:1" vs "rs2:102"), so we build a name→{id,name} map
        # from the climate definitions to get the right IDs for API writes.
        climate_sensor_map: dict[str, dict] = {}
        for climate in program.get("climates", []):
            for s in climate.get("sensors", []):
                name = s.get("name")
                if name and name not in climate_sensor_map:
                    climate_sensor_map[name] = {"id": s["id"], "name": name}

        return {
            "thermostat_id": thermostat["identifier"],
            "current_climate_ref": current_climate_ref,
            "climates": program.get("climates", []),
            "schedule": program.get("schedule", []),
            "raw_sensors": thermostat.get("remoteSensors", []),
            "climate_sensor_map": climate_sensor_map,
        }

    def select_sensors_toward_target(
        self,
        raw_sensors: list[dict],
        target_temp: float,
        climate_sensor_map: dict | None = None,
    ) -> list[dict]:
        """Pick sensors whose average biases the reading toward *target_temp*.

        If the current average is above the target, prefer cooler sensors and
        vice-versa.  Always returns at least one sensor.

        *climate_sensor_map* (name→{id,name}) corrects the sensor IDs to the
        format the program-update API expects.
        """
        readable = [
            {
                "id": s["id"],
                "name": s["name"],
                "temp": _from_ecobee(int(caps["temperature"])),
            }
            for s in raw_sensors
            if (caps := {c["type"]: c["value"] for c in s.get("capability", [])})
            and caps.get("temperature") not in (None, "unknown")
        ]

        if not readable:
            return [{"id": s["id"], "name": s["name"]} for s in raw_sensors]

        avg = sum(s["temp"] for s in readable) / len(readable)
        logger.info(f"Sensor average: {avg:.1f}°F, target: {target_temp}°F")

        half = max(1, len(readable) // 2)
        if avg > target_temp:
            candidates = [s for s in readable if s["temp"] <= target_temp]
            if not candidates:
                candidates = sorted(readable, key=lambda s: s["temp"])[:half]
        elif avg < target_temp:
            candidates = [s for s in readable if s["temp"] >= target_temp]
            if not candidates:
                candidates = sorted(readable, key=lambda s: s["temp"], reverse=True)[:half]
        else:
            candidates = readable

        return [
            climate_sensor_map[s["name"]]
            if climate_sensor_map and s["name"] in climate_sensor_map
            else {"id": s["id"], "name": s["name"]}
            for s in candidates
        ]

    # ------------------------------------------------------------------
    # Climate / program update helpers
    # ------------------------------------------------------------------

    def build_climate_update_body(
        self,
        thermostat_id: str,
        climate_ref: str,
        all_climates: list[dict],
        selected_sensors: list[dict],
        schedule: list | None = None,
    ) -> dict:
        """Build the POST body for a climate sensor-list update (dry-run friendly)."""
        updated_climates = [
            {**c, "sensors": selected_sensors}
            if c.get("climateRef") == climate_ref
            else c
            for c in all_climates
        ]
        program: dict = {"climates": updated_climates}
        if schedule is not None:
            program["schedule"] = schedule
        return {
            "selection": {"selectionType": "thermostats", "selectionMatch": thermostat_id},
            "thermostat": {"program": program},
        }

    def update_climate_sensors(
        self,
        thermostat_id: str,
        climate_ref: str,
        all_climates: list[dict],
        selected_sensors: list[dict],
        schedule: list | None = None,
    ) -> bool:
        """Update the sensor list for *climate_ref*, leaving all other climates unchanged."""
        body = self.build_climate_update_body(
            thermostat_id, climate_ref, all_climates, selected_sensors, schedule
        )
        result = self._post(body)
        if result is None:
            return False
        ok = self._ok(result, f"update_climate_sensors('{climate_ref}')")
        if ok:
            logger.info(f"Updated sensors for climate '{climate_ref}'")
        return ok

    def _send_program_update(
        self,
        tid: str,
        updated_climates: list[dict],
        updated_schedule: list,
        operation: str,
        dry_run: bool = False,
    ) -> bool | dict:
        """Send a thermostat program update (climates + schedule).

        Return the POST body dict when *dry_run* is True, otherwise return a
        bool indicating API success.
        """
        body = {
            "selection": {"selectionType": "thermostats", "selectionMatch": tid},
            "thermostat": {
                "program": {
                    "climates": updated_climates,
                    "schedule": updated_schedule,
                }
            },
        }
        if dry_run:
            return body
        result = self._post(body)
        return result is not None and self._ok(result, operation)

    def _resolve_climate(
        self, ref: str, climates: list[dict]
    ) -> dict | None:
        """Return the climate dict for *ref*, logging an error if absent."""
        match = next((c for c in climates if c.get("climateRef") == ref), None)
        if match is None:
            available = [c.get("climateRef") for c in climates]
            logger.error(f"Climate '{ref}' not found. Available: {available}")
        return match

    # ------------------------------------------------------------------
    # Schedule update commands
    # ------------------------------------------------------------------

    def update_night_schedule(
        self,
        temp: int,
        climate_ref: str = "sleep",
        alt_climate_ref: str | None = None,
        start_hour: int = 23,
        end_hour: int = 6,
        thermostat_id: str | None = None,
        update_heat_temp: bool = True,
        dry_run: bool = False,
    ) -> bool | dict:
        """Set every slot in the *start_hour* → *end_hour* window to *climate_ref*.

        Alternates with *alt_climate_ref* each hour when provided.  When
        *update_heat_temp* is True the primary climate's heatTemp is also
        updated.

        Windows that cross midnight are supported (start_hour > end_hour).
        Slot layout: slot 0 = 00:00, slot 1 = 00:30, …, slot 47 = 23:30.

        Return the POST body on dry_run, otherwise True/False.
        """
        info = self.get_climate_sensor_info(thermostat_id)
        if info is None:
            return False

        tid, climates, schedule = (
            info["thermostat_id"], info["climates"], info["schedule"]
        )

        if self._resolve_climate(climate_ref, climates) is None:
            return False
        if alt_climate_ref and self._resolve_climate(alt_climate_ref, climates) is None:
            return False

        ecobee_temp = _to_ecobee(temp)
        updated_climates = [
            {**c, "heatTemp": ecobee_temp}
            if update_heat_temp and c.get("climateRef") == climate_ref
            else c
            for c in climates
        ]

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
                ref = alt_climate_ref if (alt_climate_ref and i % 2 == 1) else climate_ref
                slot = hour * 2
                updated_day[slot] = ref
                updated_day[slot + 1] = ref
            updated_schedule.append(updated_day)

        result = self._send_program_update(
            tid, updated_climates, updated_schedule,
            f"update_night_schedule('{climate_ref}', {temp}°F, "
            f"{start_hour:02d}:00–{end_hour:02d}:00)",
            dry_run=dry_run,
        )
        if result is True:
            if update_heat_temp:
                logger.info(
                    f"Schedule updated: '{climate_ref}' at {temp}°F "
                    f"for {start_hour:02d}:00–{end_hour:02d}:00 every day"
                )
            else:
                logger.info(
                    f"Schedule updated: '{climate_ref}' "
                    f"for {start_hour:02d}:00–{end_hour:02d}:00 every day"
                )
        return result

    def update_day_schedule(
        self,
        day_temp: int,
        night_temp: int,
        day_climate_ref: str = "home",
        day_alt_climate_ref: str | None = "away",
        night_climate_ref: str = "sleep",
        day_start_hour: int = 6,
        day_end_hour: int = 20,
        thermostat_id: str | None = None,
        dry_run: bool = False,
    ) -> bool | dict:
        """Set a summer cooling schedule.

        Day window (*day_start_hour* → *day_end_hour*): alternates
        *day_climate_ref* / *day_alt_climate_ref* every hour at *day_temp* °F.
        Night window (remaining hours): *night_climate_ref* at *night_temp* °F.

        Return the POST body on dry_run, otherwise True/False.
        """
        info = self.get_climate_sensor_info(thermostat_id)
        if info is None:
            return False

        tid, climates, schedule = (
            info["thermostat_id"], info["climates"], info["schedule"]
        )

        for ref in filter(None, [day_climate_ref, day_alt_climate_ref, night_climate_ref]):
            if self._resolve_climate(ref, climates) is None:
                return False

        day_ecobee = _to_ecobee(day_temp)
        night_ecobee = _to_ecobee(night_temp)
        day_refs = {day_climate_ref, day_alt_climate_ref} - {None}

        updated_climates = [
            {**c, "coolTemp": day_ecobee}
            if c.get("climateRef") in day_refs
            else {**c, "coolTemp": night_ecobee}
            if c.get("climateRef") == night_climate_ref
            else c
            for c in climates
        ]

        day_hours = list(range(day_start_hour, day_end_hour))
        night_hours = (
            list(range(0, day_start_hour)) + list(range(day_end_hour, 24))
        )

        updated_schedule = []
        for day_slots in schedule:
            updated_day = list(day_slots)
            for i, hour in enumerate(day_hours):
                ref = (
                    day_alt_climate_ref
                    if day_alt_climate_ref and i % 2 == 1
                    else day_climate_ref
                )
                slot = hour * 2
                updated_day[slot] = ref
                updated_day[slot + 1] = ref
            for hour in night_hours:
                slot = hour * 2
                updated_day[slot] = night_climate_ref
                updated_day[slot + 1] = night_climate_ref
            updated_schedule.append(updated_day)

        result = self._send_program_update(
            tid, updated_climates, updated_schedule,
            f"update_day_schedule(day={day_temp}°F, night={night_temp}°F)",
            dry_run=dry_run,
        )
        if result is True:
            logger.info(
                f"Summer schedule updated: '{day_climate_ref}'/'{day_alt_climate_ref}' "
                f"at {day_temp}°F ({day_start_hour:02d}:00–{day_end_hour:02d}:00), "
                f"'{night_climate_ref}' at {night_temp}°F (night)"
            )
        return result

    # ------------------------------------------------------------------
    # Thermostat info
    # ------------------------------------------------------------------

    def get_thermostat_info(self, thermostat_id: str | None = None) -> dict | None:
        """Return a summary of thermostat runtime and settings."""
        thermostat = self._get_thermostat(thermostat_id)
        if thermostat is None:
            return None

        runtime = thermostat.get("runtime", {})
        settings = thermostat.get("settings", {})

        return {
            "identifier": thermostat.get("identifier"),
            "name": thermostat.get("name"),
            "model": thermostat.get("modelNumber"),
            "actual_temperature": _from_ecobee(runtime.get("actualTemperature", 0)),
            "desired_heat": _from_ecobee(runtime.get("desiredHeat", 0)),
            "desired_cool": _from_ecobee(runtime.get("desiredCool", 0)),
            "hvac_mode": settings.get("hvacMode"),
            "has_active_hold": bool(thermostat.get("events")),
        }


if __name__ == "__main__":
    import os

    from ecobee_auth_jwt import EcobeeAuthJWT
    from secrets_loader import load_secrets

    logging.basicConfig(level=logging.DEBUG)
    load_secrets()

    email = os.environ.get("ECOBEE_EMAIL")
    password = os.environ.get("ECOBEE_PASSWORD")
    if not email or not password:
        raise SystemExit("ECOBEE_EMAIL and ECOBEE_PASSWORD environment variables not set")

    auth = EcobeeAuthJWT(email, password)
    token = auth.get_token()
    if not token:
        raise SystemExit("Could not get access token")

    controller = TemperatureController(token)

    print("\nGetting thermostat info...")
    info = controller.get_thermostat_info()
    if info:
        print(f"Name:         {info['name']}")
        print(f"Model:        {info['model']}")
        print(f"Actual temp:  {info['actual_temperature']}°F")
        print(f"Desired heat: {info['desired_heat']}°F")
        print(f"HVAC mode:    {info['hvac_mode']}")

    print("\nGetting current temperature setting...")
    current = controller.get_current_temperature_setting()
    if current is not None:
        print(f"Current setting: {current}°F")
