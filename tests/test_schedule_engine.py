import json
import os
import tempfile
from datetime import datetime, time
from unittest.mock import patch

import pytz
import pytest

from schedule_engine import ScheduleEngine, TimeWindow


# ---------------------------------------------------------------------------
# TimeWindow
# ---------------------------------------------------------------------------

class TestTimeWindow:
    def test_from_config_full(self):
        cfg = {"name": "night", "start": "19:00", "end": "06:00", "temperature": 67, "enabled": True}
        w = TimeWindow.from_config(cfg)
        assert w.name == "night"
        assert w.start == time(19, 0)
        assert w.end == time(6, 0)
        assert w.temperature == 67
        assert w.enabled is True

    def test_from_config_default_enabled(self):
        cfg = {"name": "n", "start": "22:00", "end": "07:00", "temperature": 68}
        assert TimeWindow.from_config(cfg).enabled is True

    def test_contains_normal_window_inside(self):
        w = TimeWindow("day", time(8, 0), time(18, 0), 72)
        assert w.contains(time(12, 0)) is True
        assert w.contains(time(8, 0)) is True

    def test_contains_normal_window_at_end(self):
        w = TimeWindow("day", time(8, 0), time(18, 0), 72)
        assert w.contains(time(18, 0)) is False

    def test_contains_normal_window_outside(self):
        w = TimeWindow("day", time(8, 0), time(18, 0), 72)
        assert w.contains(time(7, 59)) is False
        assert w.contains(time(20, 0)) is False

    def test_contains_midnight_crossing_inside(self):
        w = TimeWindow("night", time(22, 0), time(6, 0), 67)
        assert w.contains(time(23, 0)) is True
        assert w.contains(time(0, 0)) is True
        assert w.contains(time(5, 59)) is True

    def test_contains_midnight_crossing_at_end(self):
        w = TimeWindow("night", time(22, 0), time(6, 0), 67)
        assert w.contains(time(6, 0)) is False

    def test_contains_midnight_crossing_outside(self):
        w = TimeWindow("night", time(22, 0), time(6, 0), 67)
        assert w.contains(time(12, 0)) is False


# ---------------------------------------------------------------------------
# ScheduleEngine helpers
# ---------------------------------------------------------------------------

def _write_schedule(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _minimal_engine(**kwargs):
    engine = ScheduleEngine.__new__(ScheduleEngine)
    engine.timezone = kwargs.get("timezone", pytz.utc)
    engine.windows = kwargs.get("windows", [])
    engine.default_temperature = kwargs.get("default_temperature", None)
    engine.mode = kwargs.get("mode", "heating")
    engine.last_modified = None
    engine.schedule_file = kwargs.get("schedule_file", "/tmp/fake.json")
    return engine


# ---------------------------------------------------------------------------
# load_schedule
# ---------------------------------------------------------------------------

class TestLoadSchedule:
    def test_success(self):
        data = {
            "timezone": "America/Chicago",
            "mode": "heating",
            "default_temperature": 67,
            "windows": [
                {"name": "night", "start": "19:00", "end": "06:00", "temperature": 67, "enabled": True}
            ],
        }
        path = _write_schedule(data)
        try:
            engine = ScheduleEngine(path)
            assert engine.load_schedule() is True
            assert engine.mode == "heating"
            assert engine.default_temperature == 67
            assert len(engine.windows) == 1
            assert engine.last_modified is not None
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        assert ScheduleEngine("/nonexistent/path.json").load_schedule() is False

    def test_invalid_json(self):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write("not json {{{")
        f.close()
        try:
            assert ScheduleEngine(f.name).load_schedule() is False
        finally:
            os.unlink(f.name)

    def test_invalid_window_skipped(self):
        data = {
            "timezone": "America/Chicago",
            "windows": [
                {"name": "bad", "start": "invalid-time", "end": "06:00", "temperature": 67},
                {"name": "good", "start": "19:00", "end": "06:00", "temperature": 67},
            ],
        }
        path = _write_schedule(data)
        try:
            engine = ScheduleEngine(path)
            assert engine.load_schedule() is True
            assert len(engine.windows) == 1
            assert engine.windows[0].name == "good"
        finally:
            os.unlink(path)

    def test_unknown_timezone_falls_back(self):
        data = {"timezone": "Unknown/Zone", "windows": []}
        path = _write_schedule(data)
        try:
            engine = ScheduleEngine(path)
            engine.load_schedule()
            assert engine.timezone == pytz.timezone("America/Chicago")
        finally:
            os.unlink(path)

    def test_no_default_temperature(self):
        data = {"timezone": "UTC", "windows": []}
        path = _write_schedule(data)
        try:
            engine = ScheduleEngine(path)
            engine.load_schedule()
            assert engine.default_temperature is None
        finally:
            os.unlink(path)

    def test_load_schedule_generic_exception(self):
        engine = ScheduleEngine("/fake/path.json")
        with patch("schedule_engine.Path.exists", return_value=True):
            with patch("schedule_engine.Path.read_text", side_effect=OSError("denied")):
                assert engine.load_schedule() is False

    def test_disabled_window_loaded(self):
        data = {
            "timezone": "UTC",
            "windows": [
                {"name": "off", "start": "22:00", "end": "06:00", "temperature": 67, "enabled": False}
            ],
        }
        path = _write_schedule(data)
        try:
            engine = ScheduleEngine(path)
            engine.load_schedule()
            assert len(engine.windows) == 1
            assert engine.windows[0].enabled is False
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# check_for_updates
# ---------------------------------------------------------------------------

class TestCheckForUpdates:
    def test_no_last_modified_returns_false(self):
        engine = _minimal_engine()
        assert engine.check_for_updates() is False

    def test_file_changed_reloads(self):
        data = {"timezone": "UTC", "windows": []}
        path = _write_schedule(data)
        try:
            engine = ScheduleEngine(path)
            engine.load_schedule()
            engine.last_modified = 1.0  # very old mtime, current_mtime > 1.0
            assert engine.check_for_updates() is True
        finally:
            os.unlink(path)

    def test_file_unchanged_returns_false(self):
        data = {"timezone": "UTC", "windows": []}
        path = _write_schedule(data)
        try:
            engine = ScheduleEngine(path)
            engine.load_schedule()
            assert engine.check_for_updates() is False
        finally:
            os.unlink(path)

    def test_file_missing_returns_false(self):
        engine = _minimal_engine(schedule_file="/nonexistent.json")
        engine.last_modified = 1.0
        assert engine.check_for_updates() is False


# ---------------------------------------------------------------------------
# get_expected_temperature
# ---------------------------------------------------------------------------

class TestGetExpectedTemperature:
    def test_window_match(self):
        engine = _minimal_engine(
            windows=[TimeWindow("night", time(22, 0), time(6, 0), 67)],
        )
        dt = datetime(2024, 1, 15, 23, 0, tzinfo=pytz.utc)
        assert engine.get_expected_temperature(dt) == 67

    def test_disabled_window_skipped(self):
        engine = _minimal_engine(
            windows=[TimeWindow("night", time(22, 0), time(6, 0), 67, enabled=False)],
            default_temperature=65,
        )
        dt = datetime(2024, 1, 15, 23, 0, tzinfo=pytz.utc)
        assert engine.get_expected_temperature(dt) == 65

    def test_falls_back_to_default(self):
        engine = _minimal_engine(default_temperature=68)
        dt = datetime(2024, 6, 15, 12, 0, tzinfo=pytz.utc)
        assert engine.get_expected_temperature(dt) == 68

    def test_no_match_no_default_returns_none(self):
        engine = _minimal_engine()
        dt = datetime(2024, 6, 15, 12, 0, tzinfo=pytz.utc)
        assert engine.get_expected_temperature(dt) is None

    def test_naive_datetime_gets_localized(self):
        engine = _minimal_engine(default_temperature=70)
        assert engine.get_expected_temperature(datetime(2024, 6, 15, 12, 0)) == 70

    def test_none_uses_now(self):
        engine = _minimal_engine(default_temperature=68)
        result = engine.get_expected_temperature(None)
        assert result == 68


# ---------------------------------------------------------------------------
# Accessors / summary / validation
# ---------------------------------------------------------------------------

class TestAccessors:
    def test_get_windows(self):
        engine = _minimal_engine(windows=[TimeWindow("n", time(22, 0), time(6, 0), 67)])
        assert len(engine.get_windows()) == 1

    def test_get_schedule_summary(self):
        engine = _minimal_engine(
            timezone=pytz.timezone("America/Chicago"),
            windows=[TimeWindow("night", time(22, 0), time(6, 0), 67)],
        )
        summary = engine.get_schedule_summary()
        assert "timezone" in summary
        assert summary["windows"][0]["name"] == "night"
        assert summary["windows"][0]["start"] == "22:00"


class TestValidateSchedule:
    def test_no_windows(self):
        engine = _minimal_engine()
        warnings = engine.validate_schedule()
        assert len(warnings) == 1
        assert "No windows" in warnings[0]

    def test_unusual_temperature_high(self):
        engine = _minimal_engine(windows=[TimeWindow("hot", time(0, 0), time(1, 0), 100)])
        assert any("Unusual" in w for w in engine.validate_schedule())

    def test_unusual_temperature_low(self):
        engine = _minimal_engine(windows=[TimeWindow("cold", time(0, 0), time(1, 0), 30)])
        assert any("Unusual" in w for w in engine.validate_schedule())

    def test_same_start_end(self):
        engine = _minimal_engine(windows=[TimeWindow("bad", time(22, 0), time(22, 0), 67)])
        assert any("identical" in w for w in engine.validate_schedule())

    def test_valid_no_warnings(self):
        engine = _minimal_engine(windows=[TimeWindow("night", time(22, 0), time(6, 0), 67)])
        assert engine.validate_schedule() == []
