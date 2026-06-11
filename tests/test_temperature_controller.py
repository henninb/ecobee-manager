import pytest
import requests
from unittest.mock import MagicMock, patch

from temperature_controller import (
    TemperatureController,
    EcobeeAPIError,
    _to_ecobee,
    _from_ecobee,
    _HEAT_FLOOR,
    _COOL_CEILING,
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def test_to_ecobee_integer():
    assert _to_ecobee(70) == 700


def test_to_ecobee_float():
    assert _to_ecobee(68.5) == 685


def test_to_ecobee_zero():
    assert _to_ecobee(0) == 0


def test_from_ecobee():
    assert _from_ecobee(700) == 70.0


def test_from_ecobee_fraction():
    assert _from_ecobee(685) == 68.5


# ---------------------------------------------------------------------------
# TemperatureController construction
# ---------------------------------------------------------------------------

class TestInit:
    def test_explicit_params(self):
        c = TemperatureController("tok", base_url="http://test", timeout=5)
        assert c.access_token == "tok"
        assert c.base_url == "http://test"
        assert c.timeout == 5

    def test_defaults(self):
        c = TemperatureController("tok")
        assert "ecobee.com" in c.base_url
        assert isinstance(c.timeout, int)

    def test_update_token(self):
        c = TemperatureController("old")
        c.update_token("new")
        assert c.access_token == "new"

    def test_headers(self):
        c = TemperatureController("mytok")
        h = c._headers
        assert h["Authorization"] == "Bearer mytok"
        assert h["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def ctrl():
    return TemperatureController("test_token", base_url="http://api", timeout=5)


class TestGet:
    def test_success(self, ctrl):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"thermostatList": []}
        with patch.object(ctrl._session, "get", return_value=mock_resp):
            result = ctrl._get({"format": "json"})
        assert result == {"thermostatList": []}

    def test_connection_error(self, ctrl):
        with patch.object(ctrl._session, "get", side_effect=requests.exceptions.ConnectionError()):
            result = ctrl._get({})
        assert result is None

    def test_http_error(self, ctrl):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError()
        with patch.object(ctrl._session, "get", return_value=mock_resp):
            result = ctrl._get({})
        assert result is None


class TestPost:
    def test_success(self, ctrl):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": {"code": 0}}
        with patch.object(ctrl._session, "post", return_value=mock_resp):
            result = ctrl._post({"key": "val"})
        assert result == {"status": {"code": 0}}

    def test_error_with_response_body(self, ctrl):
        exc = requests.exceptions.HTTPError("error")
        exc.response = MagicMock()
        exc.response.text = "server error text"
        with patch.object(ctrl._session, "post", side_effect=exc):
            result = ctrl._post({})
        assert result is None

    def test_error_without_response(self, ctrl):
        exc = requests.exceptions.ConnectionError("refused")
        with patch.object(ctrl._session, "post", side_effect=exc):
            result = ctrl._post({})
        assert result is None


class TestOk:
    def test_code_zero(self):
        assert TemperatureController._ok({"status": {"code": 0}}, "op") is True

    def test_code_nonzero(self):
        assert TemperatureController._ok({"status": {"code": 4, "message": "bad"}}, "op") is False

    def test_no_status(self):
        assert TemperatureController._ok({}, "op") is False

    def test_status_no_code(self):
        assert TemperatureController._ok({"status": {}}, "op") is False


# ---------------------------------------------------------------------------
# Thermostat lookup
# ---------------------------------------------------------------------------

class TestGetThermostats:
    def test_success(self, ctrl):
        with patch.object(ctrl, "_get", return_value={"thermostatList": [{"id": "1"}]}):
            result = ctrl.get_thermostats()
        assert result == [{"id": "1"}]

    def test_none(self, ctrl):
        with patch.object(ctrl, "_get", return_value=None):
            assert ctrl.get_thermostats() is None

    def test_empty_list(self, ctrl):
        with patch.object(ctrl, "_get", return_value={"thermostatList": []}):
            assert ctrl.get_thermostats() == []


class TestGetThermostat:
    def test_returns_first_when_no_id(self, ctrl):
        t = [{"identifier": "1"}, {"identifier": "2"}]
        with patch.object(ctrl, "get_thermostats", return_value=t):
            assert ctrl._get_thermostat() == {"identifier": "1"}

    def test_match_by_id(self, ctrl):
        t = [{"identifier": "1"}, {"identifier": "2"}]
        with patch.object(ctrl, "get_thermostats", return_value=t):
            assert ctrl._get_thermostat("2") == {"identifier": "2"}

    def test_not_found(self, ctrl):
        t = [{"identifier": "1"}]
        with patch.object(ctrl, "get_thermostats", return_value=t):
            assert ctrl._get_thermostat("99") is None

    def test_none_thermostats(self, ctrl):
        with patch.object(ctrl, "get_thermostats", return_value=None):
            assert ctrl._get_thermostat() is None

    def test_empty_thermostats(self, ctrl):
        with patch.object(ctrl, "get_thermostats", return_value=[]):
            assert ctrl._get_thermostat() is None


class TestFetchThermostatData:
    def test_success_first(self, ctrl):
        data = {"thermostatList": [{"identifier": "1"}, {"identifier": "2"}]}
        with patch.object(ctrl, "_get", return_value=data):
            result = ctrl._fetch_thermostat_data("{}", None)
        assert result == {"identifier": "1"}

    def test_success_by_id(self, ctrl):
        data = {"thermostatList": [{"identifier": "1"}, {"identifier": "2"}]}
        with patch.object(ctrl, "_get", return_value=data):
            result = ctrl._fetch_thermostat_data("{}", "2")
        assert result == {"identifier": "2"}

    def test_id_not_found(self, ctrl):
        data = {"thermostatList": [{"identifier": "1"}]}
        with patch.object(ctrl, "_get", return_value=data):
            result = ctrl._fetch_thermostat_data("{}", "99")
        assert result is None

    def test_get_returns_none(self, ctrl):
        with patch.object(ctrl, "_get", return_value=None):
            assert ctrl._fetch_thermostat_data("{}") is None

    def test_empty_list(self, ctrl):
        with patch.object(ctrl, "_get", return_value={"thermostatList": []}):
            assert ctrl._fetch_thermostat_data("{}") is None


# ---------------------------------------------------------------------------
# Temperature reading
# ---------------------------------------------------------------------------

class TestGetCurrentTemperatureSetting:
    def _thermostat(self, events=None, runtime=None):
        return {"events": events or [], "runtime": runtime or {}}

    def test_from_heat_hold(self, ctrl):
        t = self._thermostat(
            events=[{"running": True, "type": "hold", "heatHoldTemp": 680}]
        )
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting() == 68

    def test_from_cool_hold_fallback_in_heating_mode(self, ctrl):
        # heat key missing → falls through to coolHoldTemp
        t = self._thermostat(
            events=[{"running": True, "type": "hold", "coolHoldTemp": 780}]
        )
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting() == 78

    def test_cooling_mode_from_cool_hold(self, ctrl):
        t = self._thermostat(
            events=[{"running": True, "type": "hold", "coolHoldTemp": 740}]
        )
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting(mode="cooling") == 74

    def test_non_hold_event_ignored(self, ctrl):
        t = self._thermostat(
            events=[{"running": True, "type": "vacation"}],
            runtime={"desiredHeat": 670},
        )
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting() == 67

    def test_non_running_hold_ignored(self, ctrl):
        t = self._thermostat(
            events=[{"running": False, "type": "hold", "heatHoldTemp": 999}],
            runtime={"desiredHeat": 670},
        )
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting() == 67

    def test_from_runtime_heat(self, ctrl):
        t = self._thermostat(runtime={"desiredHeat": 670})
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting() == 67

    def test_from_runtime_cool_fallback_in_heating_mode(self, ctrl):
        t = self._thermostat(runtime={"desiredCool": 780})
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting() == 78

    def test_from_runtime_cool_in_cooling_mode(self, ctrl):
        t = self._thermostat(runtime={"desiredCool": 740})
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting(mode="cooling") == 74

    def test_no_data_returns_none(self, ctrl):
        t = self._thermostat()
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            assert ctrl.get_current_temperature_setting() is None

    def test_no_thermostat(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value=None):
            assert ctrl.get_current_temperature_setting() is None

    def test_invalid_mode_returns_none(self, ctrl):
        assert ctrl.get_current_temperature_setting(mode="auto") is None


class TestTemperaturesMatch:
    def test_exact(self, ctrl):
        assert ctrl.temperatures_match(68, 68) is True

    def test_within_tolerance(self, ctrl):
        # tolerance is 0.5 — ints will differ by 0 or more
        assert ctrl.temperatures_match(68, 68) is True

    def test_outside_tolerance(self, ctrl):
        assert ctrl.temperatures_match(68, 70) is False


class TestHasActiveDemandResponse:
    def test_returns_true_when_dr_running(self, ctrl):
        thermostat = {"events": [{"type": "demandResponse", "running": True}]}
        with patch.object(ctrl, "_get_thermostat", return_value=thermostat):
            assert ctrl.has_active_demand_response() is True

    def test_returns_false_when_dr_not_running(self, ctrl):
        thermostat = {"events": [{"type": "demandResponse", "running": False}]}
        with patch.object(ctrl, "_get_thermostat", return_value=thermostat):
            assert ctrl.has_active_demand_response() is False

    def test_returns_false_for_hold_event(self, ctrl):
        thermostat = {"events": [{"type": "hold", "running": True}]}
        with patch.object(ctrl, "_get_thermostat", return_value=thermostat):
            assert ctrl.has_active_demand_response() is False

    def test_returns_false_no_events(self, ctrl):
        thermostat = {"events": []}
        with patch.object(ctrl, "_get_thermostat", return_value=thermostat):
            assert ctrl.has_active_demand_response() is False

    def test_returns_false_no_thermostat(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value=None):
            assert ctrl.has_active_demand_response() is False


# ---------------------------------------------------------------------------
# Hold helpers
# ---------------------------------------------------------------------------

class TestSetHold:
    def test_success(self, ctrl):
        with patch.object(ctrl, "_post", return_value={"status": {"code": 0}}):
            assert ctrl._set_hold("t1", 670, 780, 60) is True

    def test_api_error(self, ctrl):
        with patch.object(ctrl, "_post", return_value={"status": {"code": 1}}):
            assert ctrl._set_hold("t1", 670, 780, 60) is False

    def test_post_returns_none(self, ctrl):
        with patch.object(ctrl, "_post", return_value=None):
            assert ctrl._set_hold("t1", 670, 780, 60) is False

    def test_short_duration_rounds_to_one_hour(self, ctrl):
        with patch.object(ctrl, "_post", return_value={"status": {"code": 0}}) as m:
            ctrl._set_hold("t1", 670, 780, 30)
        assert m.call_args[0][0]["functions"][0]["params"]["holdHours"] == 1

    def test_long_duration(self, ctrl):
        with patch.object(ctrl, "_post", return_value={"status": {"code": 0}}) as m:
            ctrl._set_hold("t1", 670, 780, 180)
        assert m.call_args[0][0]["functions"][0]["params"]["holdHours"] == 3


class TestSetTemperature:
    def test_success(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value={"identifier": "t1"}):
            with patch.object(ctrl, "_set_hold", return_value=True):
                assert ctrl.set_temperature(70) is True

    def test_no_thermostat(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value=None):
            assert ctrl.set_temperature(70) is False

    def test_hold_fails(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value={"identifier": "t1"}):
            with patch.object(ctrl, "_set_hold", return_value=False):
                assert ctrl.set_temperature(70) is False


class TestSetCoolTemperature:
    def test_success_uses_heat_floor(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value={"identifier": "t1"}):
            with patch.object(ctrl, "_set_hold", return_value=True) as m:
                result = ctrl.set_cool_temperature(74)
        assert result is True
        m.assert_called_once_with("t1", _HEAT_FLOOR, 740, 60)

    def test_no_thermostat(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value=None):
            assert ctrl.set_cool_temperature(74) is False


class TestSetHeatTemperature:
    def test_success_uses_cool_ceiling(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value={"identifier": "t1"}):
            with patch.object(ctrl, "_set_hold", return_value=True) as m:
                result = ctrl.set_heat_temperature(67)
        assert result is True
        m.assert_called_once_with("t1", 670, _COOL_CEILING, 60)

    def test_no_thermostat(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value=None):
            assert ctrl.set_heat_temperature(67) is False

    def test_hold_fails(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value={"identifier": "t1"}):
            with patch.object(ctrl, "_set_hold", return_value=False):
                assert ctrl.set_heat_temperature(67) is False


class TestSetTemperatureForMode:
    def test_cooling_mode_delegates_to_set_cool_temperature(self, ctrl):
        with patch.object(ctrl, "set_cool_temperature", return_value=True) as m:
            result = ctrl.set_temperature_for_mode(74, "cooling")
        assert result is True
        m.assert_called_once_with(74, None, 60)

    def test_heating_mode_delegates_to_set_heat_temperature(self, ctrl):
        with patch.object(ctrl, "set_heat_temperature", return_value=True) as m:
            result = ctrl.set_temperature_for_mode(67, "heating")
        assert result is True
        m.assert_called_once_with(67, None, 60)

    def test_unknown_mode_returns_false(self, ctrl):
        result = ctrl.set_temperature_for_mode(68, "auto")
        assert result is False

    def test_passes_thermostat_id_and_duration(self, ctrl):
        with patch.object(ctrl, "set_cool_temperature", return_value=True) as m:
            ctrl.set_temperature_for_mode(74, "cooling", thermostat_id="t1", duration_minutes=120)
        m.assert_called_once_with(74, "t1", 120)


# ---------------------------------------------------------------------------
# Sensor helpers
# ---------------------------------------------------------------------------

class TestGetSensors:
    def _sensor(self, name, temp_value, occupancy="false", in_use=True):
        caps = [{"type": "temperature", "value": temp_value}]
        if occupancy:
            caps.append({"type": "occupancy", "value": occupancy})
        return {"name": name, "type": "ecobee3_remote_sensor", "inUse": in_use, "capability": caps}

    def test_success(self, ctrl):
        thermostat = {"remoteSensors": [self._sensor("Living Room", "720")]}
        with patch.object(ctrl, "_fetch_thermostat_data", return_value=thermostat):
            sensors = ctrl.get_sensors()
        assert len(sensors) == 1
        assert sensors[0]["name"] == "Living Room"
        assert sensors[0]["temperature"] == 72.0
        assert sensors[0]["in_use"] is True

    def test_unknown_temperature(self, ctrl):
        thermostat = {"remoteSensors": [self._sensor("Bedroom", "unknown")]}
        with patch.object(ctrl, "_fetch_thermostat_data", return_value=thermostat):
            sensors = ctrl.get_sensors()
        assert sensors[0]["temperature"] is None

    def test_no_thermostat(self, ctrl):
        with patch.object(ctrl, "_fetch_thermostat_data", return_value=None):
            assert ctrl.get_sensors() is None

    def test_empty_sensors(self, ctrl):
        with patch.object(ctrl, "_fetch_thermostat_data", return_value={"remoteSensors": []}):
            assert ctrl.get_sensors() == []


# ---------------------------------------------------------------------------
# Climate / sensor info
# ---------------------------------------------------------------------------

class TestGetClimateSensorInfo:
    def _make_thermostat(self, events=None, hold_climate_ref=None):
        ev = []
        if events:
            ev = events
        elif hold_climate_ref:
            ev = [{"running": True, "type": "hold", "holdClimateRef": hold_climate_ref}]
        return {
            "identifier": "t1",
            "program": {
                "currentClimateRef": "home",
                "climates": [
                    {"climateRef": "home", "sensors": [{"id": "rs2:1:1", "name": "Hall"}]},
                    {"climateRef": "sleep", "sensors": []},
                ],
                "schedule": [],
            },
            "events": ev,
            "remoteSensors": [],
        }

    def test_basic(self, ctrl):
        with patch.object(ctrl, "_fetch_thermostat_data", return_value=self._make_thermostat()):
            info = ctrl.get_climate_sensor_info()
        assert info["thermostat_id"] == "t1"
        assert info["current_climate_ref"] == "home"
        assert "Hall" in info["climate_sensor_map"]

    def test_active_hold_overrides_climate_ref(self, ctrl):
        with patch.object(ctrl, "_fetch_thermostat_data", return_value=self._make_thermostat(hold_climate_ref="sleep")):
            info = ctrl.get_climate_sensor_info()
        assert info["current_climate_ref"] == "sleep"

    def test_hold_without_climate_ref(self, ctrl):
        # running hold with no holdClimateRef — should break but not override
        t = self._make_thermostat(events=[{"running": True, "type": "hold"}])
        with patch.object(ctrl, "_fetch_thermostat_data", return_value=t):
            info = ctrl.get_climate_sensor_info()
        assert info["current_climate_ref"] == "home"

    def test_duplicate_sensor_names_keep_first(self, ctrl):
        t = self._make_thermostat()
        # add a second climate with same sensor name
        t["program"]["climates"].append({
            "climateRef": "away",
            "sensors": [{"id": "rs2:1:2", "name": "Hall"}],
        })
        with patch.object(ctrl, "_fetch_thermostat_data", return_value=t):
            info = ctrl.get_climate_sensor_info()
        assert info["climate_sensor_map"]["Hall"]["id"] == "rs2:1:1"

    def test_none_thermostat(self, ctrl):
        with patch.object(ctrl, "_fetch_thermostat_data", return_value=None):
            assert ctrl.get_climate_sensor_info() is None


# ---------------------------------------------------------------------------
# Sensor selection
# ---------------------------------------------------------------------------

def _raw(name, temp_value):
    return {"id": name, "name": name, "capability": [{"type": "temperature", "value": temp_value}]}


class TestSelectSensorsTowardTarget:
    def test_above_target_picks_cooler(self, ctrl):
        sensors = [_raw("Cold", "650"), _raw("Hot", "800")]
        result = ctrl.select_sensors_toward_target(sensors, 68.0)
        names = [s["name"] for s in result]
        assert "Cold" in names

    def test_above_target_no_candidate_below_picks_coldest_half(self, ctrl):
        sensors = [_raw("A", "800"), _raw("B", "900")]
        result = ctrl.select_sensors_toward_target(sensors, 60.0)
        # avg=85 > 60; no sensors <=60 → picks coldest half
        assert len(result) >= 1
        assert result[0]["name"] == "A"  # A is colder

    def test_below_target_picks_warmer(self, ctrl):
        sensors = [_raw("Cold", "600"), _raw("Hot", "800")]
        result = ctrl.select_sensors_toward_target(sensors, 75.0)
        names = [s["name"] for s in result]
        assert "Hot" in names

    def test_below_target_no_candidate_above_picks_warmest_half(self, ctrl):
        sensors = [_raw("A", "600"), _raw("B", "650")]
        result = ctrl.select_sensors_toward_target(sensors, 80.0)
        # avg=62.5 < 80; no sensors >=80 → picks warmest half
        assert len(result) >= 1
        assert result[0]["name"] == "B"  # B is warmer

    def test_at_target_returns_all(self, ctrl):
        sensors = [_raw("A", "700"), _raw("B", "700")]
        result = ctrl.select_sensors_toward_target(sensors, 70.0)
        assert len(result) == 2

    def test_no_readable_returns_all_raw(self, ctrl):
        sensors = [{"id": "x", "name": "X", "capability": []}]
        result = ctrl.select_sensors_toward_target(sensors, 70.0)
        assert result == [{"id": "x", "name": "X"}]

    def test_unknown_temp_treated_as_unreadable(self, ctrl):
        sensors = [{"id": "x", "name": "X", "capability": [{"type": "temperature", "value": "unknown"}]}]
        result = ctrl.select_sensors_toward_target(sensors, 70.0)
        assert result == [{"id": "x", "name": "X"}]

    def test_with_climate_map_rewrites_id(self, ctrl):
        sensors = [_raw("Hall", "700")]
        climate_map = {"Hall": {"id": "rs2:1:1", "name": "Hall"}}
        result = ctrl.select_sensors_toward_target(sensors, 65.0, climate_map)
        assert result[0]["id"] == "rs2:1:1"

    def test_with_climate_map_missing_entry_uses_original(self, ctrl):
        sensors = [_raw("Bedroom", "700")]
        climate_map = {"Hall": {"id": "rs2:1:1", "name": "Hall"}}
        result = ctrl.select_sensors_toward_target(sensors, 65.0, climate_map)
        assert result[0]["id"] == "Bedroom"


# ---------------------------------------------------------------------------
# Climate / program update
# ---------------------------------------------------------------------------

class TestBuildClimateUpdateBody:
    def test_updates_target_climate(self, ctrl):
        climates = [{"climateRef": "home", "sensors": []}, {"climateRef": "sleep", "sensors": []}]
        sensors = [{"id": "1", "name": "A"}]
        body = ctrl.build_climate_update_body("t1", "home", climates, sensors)
        updated = body["thermostat"]["program"]["climates"]
        home = next(c for c in updated if c["climateRef"] == "home")
        sleep = next(c for c in updated if c["climateRef"] == "sleep")
        assert home["sensors"] == sensors
        assert sleep["sensors"] == []

    def test_with_schedule(self, ctrl):
        climates = [{"climateRef": "home", "sensors": []}]
        sched = [["home"] * 48]
        body = ctrl.build_climate_update_body("t1", "home", climates, [], sched)
        assert body["thermostat"]["program"]["schedule"] == sched

    def test_without_schedule(self, ctrl):
        body = ctrl.build_climate_update_body("t1", "home", [], [])
        assert "schedule" not in body["thermostat"]["program"]


class TestUpdateClimatesSensors:
    def test_success(self, ctrl):
        with patch.object(ctrl, "_post", return_value={"status": {"code": 0}}):
            assert ctrl.update_climate_sensors("t1", "home", [{"climateRef": "home", "sensors": []}], []) is True

    def test_post_returns_none(self, ctrl):
        with patch.object(ctrl, "_post", return_value=None):
            assert ctrl.update_climate_sensors("t1", "home", [], []) is False


class TestSendProgramUpdate:
    def test_dry_run_returns_body(self, ctrl):
        result = ctrl._send_program_update("t1", [], [], "op", dry_run=True)
        assert isinstance(result, dict)
        assert result["selection"]["selectionMatch"] == "t1"

    def test_live_success(self, ctrl):
        with patch.object(ctrl, "_post", return_value={"status": {"code": 0}}):
            assert ctrl._send_program_update("t1", [], [], "op") is True

    def test_live_post_none(self, ctrl):
        with patch.object(ctrl, "_post", return_value=None):
            assert ctrl._send_program_update("t1", [], [], "op") is False

    def test_live_api_error(self, ctrl):
        with patch.object(ctrl, "_post", return_value={"status": {"code": 5}}):
            assert ctrl._send_program_update("t1", [], [], "op") is False


class TestResolveClimate:
    def test_found(self, ctrl):
        climates = [{"climateRef": "home"}, {"climateRef": "sleep"}]
        assert ctrl._resolve_climate("home", climates) == {"climateRef": "home"}

    def test_not_found(self, ctrl):
        assert ctrl._resolve_climate("away", [{"climateRef": "home"}]) is None


# ---------------------------------------------------------------------------
# Schedule updates
# ---------------------------------------------------------------------------

def _make_info(climate_refs=("sleep", "smart1"), with_schedule=True):
    climates = [{"climateRef": r, "heatTemp": 670, "coolTemp": 720} for r in climate_refs]
    schedule = [["home"] * 48 for _ in range(7)] if with_schedule else []
    return {
        "thermostat_id": "t1",
        "climates": climates,
        "schedule": schedule,
    }


class TestUpdateNightSchedule:
    def test_dry_run_midnight_cross(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info()):
            result = ctrl.update_night_schedule(67, climate_ref="sleep", alt_climate_ref="smart1",
                                                start_hour=23, end_hour=6, dry_run=True)
        assert isinstance(result, dict)

    def test_dry_run_normal_window(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info()):
            result = ctrl.update_night_schedule(67, climate_ref="sleep",
                                                start_hour=6, end_hour=22, dry_run=True)
        assert isinstance(result, dict)

    def test_all_day_when_same_hour(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info(("sleep",))):
            result = ctrl.update_night_schedule(67, climate_ref="sleep",
                                                start_hour=0, end_hour=0, dry_run=True)
        day = result["thermostat"]["program"]["schedule"][0]
        assert all(s == "sleep" for s in day)

    def test_no_heat_temp_update(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info(("sleep",))):
            with patch.object(ctrl, "_send_program_update", return_value=True) as m:
                ctrl.update_night_schedule(67, climate_ref="sleep",
                                           start_hour=23, end_hour=6,
                                           update_heat_temp=False)
        # climates passed to _send_program_update should be unchanged (no heatTemp override)
        call_climates = m.call_args[0][1]
        assert call_climates[0]["heatTemp"] == 670  # untouched

    def test_live_success(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info(("sleep",))):
            with patch.object(ctrl, "_send_program_update", return_value=True):
                result = ctrl.update_night_schedule(67, climate_ref="sleep")
        assert result is True

    def test_live_success_no_heat_update(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info(("sleep",))):
            with patch.object(ctrl, "_send_program_update", return_value=True):
                result = ctrl.update_night_schedule(67, climate_ref="sleep", update_heat_temp=False)
        assert result is True

    def test_none_info(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=None):
            assert ctrl.update_night_schedule(67) is False

    def test_invalid_primary_climate(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info(("home",))):
            assert ctrl.update_night_schedule(67, climate_ref="sleep") is False

    def test_invalid_alt_climate(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info(("sleep",))):
            assert ctrl.update_night_schedule(67, climate_ref="sleep", alt_climate_ref="nonexistent") is False


class TestUpdateDaySchedule:
    def _day_info(self):
        return _make_info(climate_refs=("home", "away", "sleep"))

    def test_dry_run(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=self._day_info()):
            result = ctrl.update_day_schedule(74, 72, dry_run=True)
        assert isinstance(result, dict)

    def test_dry_run_no_alt(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info(("home", "sleep"))):
            result = ctrl.update_day_schedule(74, 72, day_alt_climate_ref=None, dry_run=True)
        assert isinstance(result, dict)

    def test_live_success(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=self._day_info()):
            with patch.object(ctrl, "_send_program_update", return_value=True):
                assert ctrl.update_day_schedule(74, 72) is True

    def test_none_info(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=None):
            assert ctrl.update_day_schedule(74, 72) is False

    def test_invalid_climate(self, ctrl):
        with patch.object(ctrl, "get_climate_sensor_info", return_value=_make_info(("home", "sleep"))):
            assert ctrl.update_day_schedule(74, 72, night_climate_ref="missing") is False


# ---------------------------------------------------------------------------
# Thermostat info
# ---------------------------------------------------------------------------

class TestGetThermostatInfo:
    def test_success(self, ctrl):
        t = {
            "identifier": "t1",
            "name": "My Thermo",
            "modelNumber": "EBE-1",
            "runtime": {"actualTemperature": 720, "desiredHeat": 670, "desiredCool": 780},
            "settings": {"hvacMode": "heat"},
            "events": [],
        }
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            info = ctrl.get_thermostat_info()
        assert info["identifier"] == "t1"
        assert info["actual_temperature"] == 72.0
        assert info["desired_heat"] == 67.0
        assert info["hvac_mode"] == "heat"
        assert info["has_active_hold"] is False

    def test_with_active_hold(self, ctrl):
        t = {
            "identifier": "t1", "name": "T", "modelNumber": "M",
            "runtime": {"actualTemperature": 0, "desiredHeat": 0, "desiredCool": 0},
            "settings": {},
            "events": [{"type": "hold"}],
        }
        with patch.object(ctrl, "_get_thermostat", return_value=t):
            info = ctrl.get_thermostat_info()
        assert info["has_active_hold"] is True

    def test_none(self, ctrl):
        with patch.object(ctrl, "_get_thermostat", return_value=None):
            assert ctrl.get_thermostat_info() is None
