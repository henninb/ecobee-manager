import logging
import threading
from collections import deque
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ecobee_service import EcobeeServiceJWT


def _window(name, temperature, enabled=True):
    return SimpleNamespace(name=name, temperature=temperature, enabled=enabled)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def svc():
    """EcobeeServiceJWT with all external dependencies replaced by mocks."""
    s = EcobeeServiceJWT.__new__(EcobeeServiceJWT)
    s.check_interval_minutes = 40
    s.log_level = "INFO"
    s.error_threshold = 3
    s.running = False
    s._stop_event = threading.Event()
    s.auth = MagicMock()
    s.schedule = MagicMock()
    s.controller = MagicMock()
    s.health_server = MagicMock()
    s.override_manager = None
    s.consecutive_errors = 0
    s.recent_reverts = deque(maxlen=60)
    s._demand_response_active = False
    s.logger = logging.getLogger("test.ecobee_service")
    return s


def _setup_cooling_check(svc, *, current_temp, expected_temp=74, peak_cool_max=78, dr_active):
    """Wire the schedule/controller mocks for a single cooling-mode check cycle."""
    svc.schedule.schedule_file = "config/schedule_summer.json"
    svc.schedule.check_for_updates.return_value = False
    svc.schedule.get_expected_temperature.return_value = expected_temp
    svc.schedule.mode = "cooling"
    svc.schedule.peak_cool_max = peak_cool_max
    svc.schedule.get_windows.return_value = [_window("day", 74), _window("night", 73)]
    svc.controller.get_current_temperature_setting.return_value = current_temp
    svc.controller.has_active_demand_response.return_value = dr_active
    # Climate program matches the schedule by default — no drift, no reapply.
    svc.controller.get_climate_sensor_info.return_value = {
        "climates": [
            {"climateRef": "home", "coolTemp": 740},
            {"climateRef": "away", "coolTemp": 740},
            {"climateRef": "sleep", "coolTemp": 730},
        ]
    }
    svc.controller.temperatures_match.side_effect = lambda c, e: abs(c - e) <= 0.5
    svc.controller.set_temperature_for_mode.return_value = True


# ---------------------------------------------------------------------------
# Demand-response enforcement logic (_check_and_update_temperature)
# ---------------------------------------------------------------------------

class TestDemandResponseLogic:
    def test_dr_active_temp_at_ceiling_no_revert(self, svc):
        """DR active, current==ceiling → leave it, no revert."""
        _setup_cooling_check(svc, current_temp=78, dr_active=True)
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            svc._check_and_update_temperature()
        svc.controller.set_temperature_for_mode.assert_not_called()
        assert svc._demand_response_active is True

    def test_dr_active_temp_below_ceiling_no_revert(self, svc):
        """DR active, current < ceiling → also acceptable, no revert."""
        _setup_cooling_check(svc, current_temp=76, dr_active=True)
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            svc._check_and_update_temperature()
        svc.controller.set_temperature_for_mode.assert_not_called()

    def test_dr_active_temp_above_ceiling_reverts_to_ceiling(self, svc):
        """DR active, current > ceiling → cap to 78, not the schedule temp."""
        _setup_cooling_check(svc, current_temp=80, dr_active=True)
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            svc._check_and_update_temperature()
        svc.controller.set_temperature_for_mode.assert_called_once_with(78, "cooling", duration_minutes=120)

    def test_dr_inactive_reverts_to_schedule_temp(self, svc):
        """No DR event → normal enforcement, revert to schedule temp (74)."""
        _setup_cooling_check(svc, current_temp=78, dr_active=False)
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            svc._check_and_update_temperature()
        svc.controller.set_temperature_for_mode.assert_called_once_with(74, "cooling", duration_minutes=120)

    def test_heating_mode_skips_dr_check(self, svc):
        """DR check is bypassed entirely in heating mode."""
        svc.schedule.schedule_file = "config/schedule_winter.json"
        svc.schedule.check_for_updates.return_value = False
        svc.schedule.get_expected_temperature.return_value = 68
        svc.schedule.mode = "heating"
        svc.schedule.peak_cool_max = 78
        svc.schedule.get_windows.return_value = [_window("night", 68)]
        svc.controller.get_current_temperature_setting.return_value = 68
        svc.controller.temperatures_match.return_value = True
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "sleep", "heatTemp": 680}]
        }
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_winter.json"):
            svc._check_and_update_temperature()
        svc.controller.has_active_demand_response.assert_not_called()

    def test_peak_cool_max_none_skips_dr_check(self, svc):
        """DR check is bypassed when peak_cool_max is not configured."""
        _setup_cooling_check(svc, current_temp=78, dr_active=True)
        svc.schedule.peak_cool_max = None
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            svc._check_and_update_temperature()
        svc.controller.has_active_demand_response.assert_not_called()

    def test_demand_response_flag_reset_each_cycle(self, svc):
        """_demand_response_active is reset to False at the start of every check."""
        svc._demand_response_active = True
        _setup_cooling_check(svc, current_temp=74, dr_active=False)
        svc.controller.temperatures_match.return_value = True
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            svc._check_and_update_temperature()
        assert svc._demand_response_active is False


# ---------------------------------------------------------------------------
# Manual override (_check_and_update_temperature)
# ---------------------------------------------------------------------------

class TestManualOverride:
    def test_active_override_skips_enforcement(self, svc):
        """An active override skips the check entirely — no reads, no reverts."""
        svc.override_manager = MagicMock()
        svc.override_manager.get_status.return_value = {
            "state": "active", "start": "x", "end": "2026-07-10T12:00:00"
        }
        with patch.object(svc, "_select_schedule_file"):
            svc._check_and_update_temperature()
        svc.controller.get_current_temperature_setting.assert_not_called()
        svc.controller.set_temperature_for_mode.assert_not_called()
        svc.health_server.increment_checks.assert_called_once()

    def test_upcoming_override_does_not_skip(self, svc):
        """A scheduled-but-not-yet-started override doesn't pause enforcement."""
        svc.override_manager = MagicMock()
        svc.override_manager.get_status.return_value = {
            "state": "upcoming", "start": "x", "end": "y"
        }
        _setup_cooling_check(svc, current_temp=78, dr_active=False)
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            svc._check_and_update_temperature()
        svc.controller.set_temperature_for_mode.assert_called_once_with(74, "cooling", duration_minutes=120)

    def test_no_override_does_not_skip(self, svc):
        """No override at all → normal enforcement."""
        svc.override_manager = MagicMock()
        svc.override_manager.get_status.return_value = {"state": "none"}
        _setup_cooling_check(svc, current_temp=78, dr_active=False)
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            svc._check_and_update_temperature()
        svc.controller.set_temperature_for_mode.assert_called_once_with(74, "cooling", duration_minutes=120)


# ---------------------------------------------------------------------------
# Climate program drift detection (_climate_program_matches_schedule)
# ---------------------------------------------------------------------------

class TestClimateProgramMatchesSchedule:
    def test_cooling_matches(self, svc):
        svc.schedule.mode = "cooling"
        svc.schedule.get_windows.return_value = [_window("day", 74), _window("night", 73)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [
                {"climateRef": "home", "coolTemp": 740},
                {"climateRef": "away", "coolTemp": 740},
                {"climateRef": "sleep", "coolTemp": 730},
            ]
        }
        assert svc._climate_program_matches_schedule() is True

    def test_cooling_day_climate_drifted(self, svc):
        """Someone "permanently" set 78°F on the thermostat — home climate drifts."""
        svc.schedule.mode = "cooling"
        svc.schedule.get_windows.return_value = [_window("day", 74), _window("night", 73)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [
                {"climateRef": "home", "coolTemp": 780},
                {"climateRef": "away", "coolTemp": 780},
                {"climateRef": "sleep", "coolTemp": 780},
            ]
        }
        assert svc._climate_program_matches_schedule() is False

    def test_cooling_night_climate_drifted(self, svc):
        svc.schedule.mode = "cooling"
        svc.schedule.get_windows.return_value = [_window("day", 74), _window("night", 73)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [
                {"climateRef": "home", "coolTemp": 740},
                {"climateRef": "away", "coolTemp": 740},
                {"climateRef": "sleep", "coolTemp": 780},
            ]
        }
        assert svc._climate_program_matches_schedule() is False

    def test_cooling_missing_windows_assumes_match(self, svc):
        svc.schedule.mode = "cooling"
        svc.schedule.get_windows.return_value = [_window("day", 74)]  # no night window
        svc.controller.get_climate_sensor_info.return_value = {"climates": []}
        assert svc._climate_program_matches_schedule() is True

    def test_heating_matches(self, svc):
        svc.schedule.mode = "heating"
        svc.schedule.get_windows.return_value = [_window("night", 67)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "sleep", "heatTemp": 670}]
        }
        assert svc._climate_program_matches_schedule() is True

    def test_heating_drifted(self, svc):
        svc.schedule.mode = "heating"
        svc.schedule.get_windows.return_value = [_window("night", 67)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [{"climateRef": "sleep", "heatTemp": 780}]
        }
        assert svc._climate_program_matches_schedule() is False

    def test_fetch_failure_assumes_match(self, svc):
        """A failed read shouldn't force a re-push — treat as matching."""
        svc.schedule.mode = "cooling"
        svc.controller.get_climate_sensor_info.return_value = None
        assert svc._climate_program_matches_schedule() is True


class TestClimateProgramDriftIntegration:
    def test_drift_triggers_reapply(self, svc):
        """A drifted climate program gets reapplied during a normal check cycle."""
        _setup_cooling_check(svc, current_temp=74, dr_active=False)
        svc.schedule.get_windows.return_value = [_window("day", 74), _window("night", 73)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [
                {"climateRef": "home", "coolTemp": 780},
                {"climateRef": "away", "coolTemp": 780},
                {"climateRef": "sleep", "coolTemp": 780},
            ]
        }
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            with patch.object(svc, "_apply_ecobee_program") as apply_mock:
                svc._check_and_update_temperature()
        apply_mock.assert_called_once()

    def test_no_drift_skips_reapply(self, svc):
        _setup_cooling_check(svc, current_temp=74, dr_active=False)
        svc.schedule.get_windows.return_value = [_window("day", 74), _window("night", 73)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [
                {"climateRef": "home", "coolTemp": 740},
                {"climateRef": "away", "coolTemp": 740},
                {"climateRef": "sleep", "coolTemp": 730},
            ]
        }
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            with patch.object(svc, "_apply_ecobee_program") as apply_mock:
                svc._check_and_update_temperature()
        apply_mock.assert_not_called()

    def test_active_override_skips_drift_check_entirely(self, svc):
        """While an override is active, drift correction is skipped too —
        manual changes are left fully alone until the override ends."""
        svc.override_manager = MagicMock()
        svc.override_manager.get_status.return_value = {
            "state": "active", "start": "x", "end": "2026-07-10T12:00:00"
        }
        svc.schedule.get_windows.return_value = [_window("day", 74), _window("night", 73)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [
                {"climateRef": "home", "coolTemp": 780},
                {"climateRef": "away", "coolTemp": 780},
                {"climateRef": "sleep", "coolTemp": 780},
            ]
        }
        with patch.object(svc, "_apply_ecobee_program") as apply_mock:
            svc._check_and_update_temperature()
        svc.controller.get_climate_sensor_info.assert_not_called()
        apply_mock.assert_not_called()

    def test_drift_corrected_on_first_check_after_override_ends(self, svc):
        """As soon as the override clears, the very next check reapplies —
        no separate cooldown or extra cycle needed."""
        svc.override_manager = MagicMock()
        svc.override_manager.get_status.return_value = {"state": "none"}
        _setup_cooling_check(svc, current_temp=74, dr_active=False)
        svc.schedule.get_windows.return_value = [_window("day", 74), _window("night", 73)]
        svc.controller.get_climate_sensor_info.return_value = {
            "climates": [
                {"climateRef": "home", "coolTemp": 780},
                {"climateRef": "away", "coolTemp": 780},
                {"climateRef": "sleep", "coolTemp": 780},
            ]
        }
        with patch.object(svc, "_select_schedule_file", return_value="config/schedule_summer.json"):
            with patch.object(svc, "_apply_ecobee_program") as apply_mock:
                svc._check_and_update_temperature()
        apply_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Hold duration margin
# ---------------------------------------------------------------------------

class TestHoldDurationMargin:
    def test_hold_duration_exceeds_check_interval(self, svc):
        """The hold must outlast the check interval so a single missed check
        can't let it expire before the next check corrects it."""
        svc.check_interval_minutes = 40
        assert svc.hold_duration_minutes > svc.check_interval_minutes

    def test_hold_duration_scales_with_check_interval(self, svc):
        svc.check_interval_minutes = 20
        assert svc.hold_duration_minutes == 60


# ---------------------------------------------------------------------------
# Dynamic sleep interval (run loop)
# ---------------------------------------------------------------------------

class TestRunIntervalSelection:
    def _run_one_iteration(self, svc, dr_active):
        """Run one loop iteration and return the seconds passed to _stop_event.wait."""
        svc._demand_response_active = dr_active
        captured = []

        def fake_wait(seconds):
            captured.append(seconds)
            svc.running = False

        with patch.object(svc, "_refresh_token_if_needed", return_value=True):
            with patch.object(svc, "_check_and_update_temperature"):
                with patch.object(svc._stop_event, "wait", side_effect=fake_wait):
                    with patch.object(svc._stop_event, "clear"):
                        svc.run()

        return captured[0]

    def test_dr_active_sleeps_15_minutes(self, svc):
        seconds = self._run_one_iteration(svc, dr_active=True)
        assert seconds == 15 * 60

    def test_dr_inactive_sleeps_normal_interval(self, svc):
        seconds = self._run_one_iteration(svc, dr_active=False)
        assert seconds == 40 * 60

    def test_custom_interval_respected_outside_dr(self, svc):
        svc.check_interval_minutes = 20
        seconds = self._run_one_iteration(svc, dr_active=False)
        assert seconds == 20 * 60
