import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ecobee_cli
from ecobee_cli import (
    _fmt_hour,
    cmd_dump_program,
    cmd_get,
    cmd_lean,
    cmd_schedule,
    cmd_schedule_day,
    cmd_schedule_night,
    cmd_sensors,
    cmd_set,
    cmd_status,
    load_token,
    main,
    print_program_schedule,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ctrl():
    return MagicMock()


@pytest.fixture
def jwt_file(tmp_path, monkeypatch):
    path = tmp_path / "ecobee_jwt.json"
    monkeypatch.setattr(ecobee_cli, "JWT_FILE", str(path))
    return path


# ---------------------------------------------------------------------------
# load_token
# ---------------------------------------------------------------------------

class TestLoadToken:
    def test_file_not_found(self, jwt_file):
        with pytest.raises(SystemExit):
            load_token()

    def test_no_token_in_config(self, jwt_file):
        jwt_file.write_text(json.dumps({"api_base_url": None}))
        with pytest.raises(SystemExit):
            load_token()

    def test_valid_token(self, jwt_file):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        jwt_file.write_text(json.dumps({
            "jwt_token": "tok",
            "token_expires_at": future,
            "api_base_url": "https://api.ecobee.com/1",
        }))
        result = load_token()
        assert result["token"] == "tok"
        assert result["base_url"] == "https://api.ecobee.com/1"

    def test_expired_token_prints_warning(self, jwt_file, capsys):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        jwt_file.write_text(json.dumps({"jwt_token": "tok", "token_expires_at": past}))
        result = load_token()
        assert result["token"] == "tok"
        assert "Warning" in capsys.readouterr().out

    def test_naive_expiry_no_crash(self, jwt_file):
        # naive datetime string without timezone
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        jwt_file.write_text(json.dumps({"jwt_token": "tok", "token_expires_at": past}))
        result = load_token()
        assert result["token"] == "tok"

    def test_no_expires_at(self, jwt_file):
        jwt_file.write_text(json.dumps({"jwt_token": "tok"}))
        result = load_token()
        assert result["token"] == "tok"


# ---------------------------------------------------------------------------
# _fmt_hour
# ---------------------------------------------------------------------------

def test_fmt_hour_midnight():
    assert _fmt_hour(0) == "12am"


def test_fmt_hour_noon():
    assert _fmt_hour(12) == "12pm"


def test_fmt_hour_am():
    assert _fmt_hour(9) == "9am"


def test_fmt_hour_pm():
    assert _fmt_hour(15) == "3pm"


def test_fmt_hour_1am():
    assert _fmt_hour(1) == "1am"


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_success(self, ctrl, capsys):
        ctrl.get_thermostat_info.return_value = {
            "name": "Home", "model": "X", "actual_temperature": 70.0,
            "desired_heat": 67.0, "desired_cool": 76.0, "hvac_mode": "heat",
            "has_active_hold": False,
        }
        cmd_status(ctrl)
        out = capsys.readouterr().out
        assert "Home" in out

    def test_no_info_exits(self, ctrl):
        ctrl.get_thermostat_info.return_value = None
        with pytest.raises(SystemExit):
            cmd_status(ctrl)


# ---------------------------------------------------------------------------
# cmd_get
# ---------------------------------------------------------------------------

class TestCmdGet:
    def test_success(self, ctrl, capsys):
        ctrl.get_current_temperature_setting.return_value = 68
        cmd_get(ctrl)
        assert "68" in capsys.readouterr().out

    def test_none_exits(self, ctrl):
        ctrl.get_current_temperature_setting.return_value = None
        with pytest.raises(SystemExit):
            cmd_get(ctrl)


# ---------------------------------------------------------------------------
# cmd_sensors
# ---------------------------------------------------------------------------

class TestCmdSensors:
    def test_success(self, ctrl, capsys):
        ctrl.get_sensors.return_value = [
            {"name": "Hall", "temperature": 72.0, "occupancy": "true", "in_use": True, "type": "ecobee3_remote_sensor"}
        ]
        cmd_sensors(ctrl)
        assert "Hall" in capsys.readouterr().out

    def test_none_temp(self, ctrl, capsys):
        ctrl.get_sensors.return_value = [
            {"name": "X", "temperature": None, "occupancy": None, "in_use": False, "type": "ecobee3_remote_sensor"}
        ]
        cmd_sensors(ctrl)
        out = capsys.readouterr().out
        assert "n/a" in out

    def test_none_exits(self, ctrl):
        ctrl.get_sensors.return_value = None
        with pytest.raises(SystemExit):
            cmd_sensors(ctrl)

    def test_empty_list(self, ctrl, capsys):
        ctrl.get_sensors.return_value = []
        cmd_sensors(ctrl)
        assert "No sensors" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_lean
# ---------------------------------------------------------------------------

class TestCmdLean:
    def _info(self):
        return {
            "thermostat_id": "t1",
            "current_climate_ref": "home",
            "climates": [{"climateRef": "home"}, {"climateRef": "sleep"}],
            "raw_sensors": [
                {"id": "1", "name": "Hall", "capability": [{"type": "temperature", "value": "700"}]}
            ],
            "climate_sensor_map": {},
            "schedule": [],
        }

    def test_no_args_exits(self, ctrl):
        with pytest.raises(SystemExit):
            cmd_lean(ctrl, [])

    def test_invalid_temp_exits(self, ctrl):
        with pytest.raises(SystemExit):
            cmd_lean(ctrl, ["notanumber"])

    def test_no_info_exits(self, ctrl):
        ctrl.get_climate_sensor_info.return_value = None
        with pytest.raises(SystemExit):
            cmd_lean(ctrl, ["70"])

    def test_dry_run(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = self._info()
        ctrl.select_sensors_toward_target.return_value = [{"id": "1", "name": "Hall"}]
        ctrl.build_climate_update_body.return_value = {"selection": {}, "thermostat": {}}
        cmd_lean(ctrl, ["70", "--dry-run"])
        assert "DRY RUN" in capsys.readouterr().out

    def test_success(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = self._info()
        ctrl.select_sensors_toward_target.return_value = [{"id": "1", "name": "Hall"}]
        ctrl.update_climate_sensors.return_value = True
        cmd_lean(ctrl, ["70"])
        assert "Done" in capsys.readouterr().out

    def test_failure_exits(self, ctrl):
        ctrl.get_climate_sensor_info.return_value = self._info()
        ctrl.select_sensors_toward_target.return_value = [{"id": "1", "name": "Hall"}]
        ctrl.update_climate_sensors.return_value = False
        with pytest.raises(SystemExit):
            cmd_lean(ctrl, ["70"])


# ---------------------------------------------------------------------------
# cmd_dump_program
# ---------------------------------------------------------------------------

class TestCmdDumpProgram:
    def test_success(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "home"}],
            "schedule": [["home"] * 48],
            "raw_sensors": [{"id": "1", "name": "Hall", "capability": [{"type": "temperature", "value": "700"}]}],
        }
        cmd_dump_program(ctrl)
        out = capsys.readouterr().out
        assert "CLIMATES" in out

    def test_missing_schedule_ref(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "home"}],
            "schedule": [["away"] * 48],
            "raw_sensors": [],
        }
        cmd_dump_program(ctrl)
        assert "WARNING" in capsys.readouterr().out

    def test_no_info_exits(self, ctrl):
        ctrl.get_climate_sensor_info.return_value = None
        with pytest.raises(SystemExit):
            cmd_dump_program(ctrl)


# ---------------------------------------------------------------------------
# cmd_set
# ---------------------------------------------------------------------------

class TestCmdSet:
    def test_no_args_exits(self, ctrl):
        with pytest.raises(SystemExit):
            cmd_set(ctrl, [])

    def test_invalid_arg_exits(self, ctrl):
        with pytest.raises(SystemExit):
            cmd_set(ctrl, ["abc"])

    def test_out_of_range_exits(self, ctrl):
        with pytest.raises(SystemExit):
            cmd_set(ctrl, ["35"])

    def test_success(self, ctrl, capsys):
        ctrl.set_temperature.return_value = True
        cmd_set(ctrl, ["68"])
        assert "Done" in capsys.readouterr().out

    def test_failure_exits(self, ctrl):
        ctrl.set_temperature.return_value = False
        with pytest.raises(SystemExit):
            cmd_set(ctrl, ["68"])


# ---------------------------------------------------------------------------
# print_program_schedule
# ---------------------------------------------------------------------------

def test_print_program_schedule(capsys):
    info = {
        "climates": [
            {"climateRef": "home", "name": "Home", "heatTemp": 670, "coolTemp": 760}
        ],
        "schedule": [["home"] * 48 for _ in range(7)],
    }
    print_program_schedule(info)
    out = capsys.readouterr().out
    assert "Home" in out


def test_print_program_schedule_unknown_ref(capsys):
    info = {
        "climates": [],
        "schedule": [["unknown"] * 48 for _ in range(7)],
    }
    print_program_schedule(info)
    capsys.readouterr()


# ---------------------------------------------------------------------------
# cmd_schedule
# ---------------------------------------------------------------------------

class TestCmdSchedule:
    def _info(self):
        return {
            "thermostat_id": "t1",
            "current_climate_ref": "home",
            "climates": [{"climateRef": "home", "name": "Home", "heatTemp": 670, "coolTemp": 760}],
            "raw_sensors": [],
            "schedule": [["home"] * 48 for _ in range(7)],
        }

    def test_success(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = self._info()
        ctrl.get_thermostat_info.return_value = {"desired_heat": 67.0}
        cmd_schedule(ctrl)
        capsys.readouterr()

    def test_wrong_desired_heat_prints_note(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = self._info()
        ctrl.get_thermostat_info.return_value = {"desired_heat": 70.0}
        cmd_schedule(ctrl)
        assert "Note" in capsys.readouterr().out

    def test_no_info_exits(self, ctrl):
        ctrl.get_climate_sensor_info.return_value = None
        with pytest.raises(SystemExit):
            cmd_schedule(ctrl)


# ---------------------------------------------------------------------------
# cmd_schedule_night
# ---------------------------------------------------------------------------

class TestCmdScheduleNight:
    def test_dry_run(self, ctrl, capsys):
        ctrl.update_night_schedule.return_value = {"selection": {}, "thermostat": {}}
        cmd_schedule_night(ctrl, ["--dry-run"])
        assert "DRY RUN" in capsys.readouterr().out

    def test_dry_run_api_error(self, ctrl):
        ctrl.update_night_schedule.return_value = False
        with pytest.raises(SystemExit):
            cmd_schedule_night(ctrl, ["--dry-run"])

    def test_success(self, ctrl, capsys):
        ctrl.update_night_schedule.return_value = True
        ctrl.get_climate_sensor_info.return_value = None  # skips schedule print
        cmd_schedule_night(ctrl, [])
        assert "Done" in capsys.readouterr().out

    def test_success_with_schedule_print(self, ctrl, capsys):
        ctrl.update_night_schedule.return_value = True
        ctrl.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "sleep", "name": "Sleep", "heatTemp": 670, "coolTemp": 760}],
            "schedule": [["sleep"] * 48 for _ in range(7)],
        }
        cmd_schedule_night(ctrl, [])
        capsys.readouterr()

    def test_failure_exits(self, ctrl):
        ctrl.update_night_schedule.return_value = False
        with pytest.raises(SystemExit):
            cmd_schedule_night(ctrl, [])


# ---------------------------------------------------------------------------
# cmd_schedule_day
# ---------------------------------------------------------------------------

class TestCmdScheduleDay:
    def test_dry_run(self, ctrl, capsys):
        ctrl.update_day_schedule.return_value = {"selection": {}, "thermostat": {}}
        cmd_schedule_day(ctrl, ["--dry-run"])
        assert "DRY RUN" in capsys.readouterr().out

    def test_dry_run_api_error(self, ctrl):
        ctrl.update_day_schedule.return_value = False
        with pytest.raises(SystemExit):
            cmd_schedule_day(ctrl, ["--dry-run"])

    def test_success(self, ctrl, capsys):
        ctrl.update_day_schedule.return_value = True
        ctrl.get_climate_sensor_info.return_value = None
        cmd_schedule_day(ctrl, [])
        assert "Done" in capsys.readouterr().out

    def test_success_with_schedule_print(self, ctrl, capsys):
        ctrl.update_day_schedule.return_value = True
        ctrl.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "home", "name": "Home", "heatTemp": 670, "coolTemp": 760}],
            "schedule": [["home"] * 48 for _ in range(7)],
        }
        cmd_schedule_day(ctrl, [])
        capsys.readouterr()

    def test_failure_exits(self, ctrl):
        ctrl.update_day_schedule.return_value = False
        with pytest.raises(SystemExit):
            cmd_schedule_day(ctrl, [])


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _mock_ctx():
    return {"token": "tok", "base_url": "http://api"}


class TestMain:
    def _run(self, args, ctrl):
        with patch("sys.argv", args):
            with patch("ecobee_cli.load_token", return_value=_mock_ctx()):
                with patch("ecobee_cli.TemperatureController", return_value=ctrl):
                    main()

    def test_no_args_exits(self, ctrl):
        with patch("sys.argv", ["cli"]):
            with pytest.raises(SystemExit):
                main()

    def test_unknown_command_exits(self, ctrl):
        with patch("sys.argv", ["cli", "unknown"]):
            with patch("ecobee_cli.load_token", return_value=_mock_ctx()):
                with patch("ecobee_cli.TemperatureController", return_value=ctrl):
                    with pytest.raises(SystemExit):
                        main()

    def test_status_command(self, ctrl, capsys):
        ctrl.get_thermostat_info.return_value = {
            "name": "T", "model": "M", "actual_temperature": 70.0,
            "desired_heat": 67.0, "desired_cool": 76.0, "hvac_mode": "heat",
            "has_active_hold": False,
        }
        self._run(["cli", "status"], ctrl)
        assert "T" in capsys.readouterr().out

    def test_get_command(self, ctrl, capsys):
        ctrl.get_current_temperature_setting.return_value = 68
        self._run(["cli", "get"], ctrl)
        assert "68" in capsys.readouterr().out

    def test_set_command(self, ctrl, capsys):
        ctrl.set_temperature.return_value = True
        self._run(["cli", "set", "68"], ctrl)
        assert "Done" in capsys.readouterr().out

    def test_sensors_command(self, ctrl, capsys):
        ctrl.get_sensors.return_value = []
        self._run(["cli", "sensors"], ctrl)
        capsys.readouterr()

    def test_lean_command(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = {
            "thermostat_id": "t1",
            "current_climate_ref": "home",
            "climates": [{"climateRef": "home"}],
            "raw_sensors": [],
            "climate_sensor_map": {},
            "schedule": [],
        }
        ctrl.select_sensors_toward_target.return_value = []
        ctrl.update_climate_sensors.return_value = True
        self._run(["cli", "lean", "70"], ctrl)
        capsys.readouterr()

    def test_schedule_command(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "home", "name": "H", "heatTemp": 670, "coolTemp": 760}],
            "schedule": [["home"] * 48 for _ in range(7)],
        }
        ctrl.get_thermostat_info.return_value = {"desired_heat": 67.0}
        self._run(["cli", "schedule"], ctrl)
        capsys.readouterr()

    def test_schedule_night_command(self, ctrl, capsys):
        ctrl.update_night_schedule.return_value = True
        ctrl.get_climate_sensor_info.return_value = None
        self._run(["cli", "schedule-night"], ctrl)
        capsys.readouterr()

    def test_schedule_day_command(self, ctrl, capsys):
        ctrl.update_day_schedule.return_value = True
        ctrl.get_climate_sensor_info.return_value = None
        self._run(["cli", "schedule-day"], ctrl)
        capsys.readouterr()

    def test_dump_program_command(self, ctrl, capsys):
        ctrl.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "home"}],
            "schedule": [["home"] * 48],
            "raw_sensors": [],
        }
        self._run(["cli", "dump-program"], ctrl)
        capsys.readouterr()
