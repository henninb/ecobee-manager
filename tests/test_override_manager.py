from datetime import datetime, timedelta

import pytest

from override_manager import OverrideManager


@pytest.fixture
def override_file(tmp_path):
    return str(tmp_path / "override.json")


@pytest.fixture
def manager(override_file):
    return OverrideManager(override_file)


class TestNoOverride:
    def test_get_status_none_when_no_file(self, manager):
        assert manager.get_status()["state"] == "none"

    def test_is_active_false_when_no_file(self, manager):
        assert manager.is_active() is False


class TestSetOverride:
    def test_set_then_active_within_window(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.set_override(now - timedelta(hours=1), now + timedelta(hours=1))
        status = manager.get_status(now)
        assert status["state"] == "active"
        assert status["start"] == now - timedelta(hours=1)
        assert status["end"] == now + timedelta(hours=1)

    def test_is_active_true_within_window(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.set_override(now - timedelta(hours=1), now + timedelta(hours=1))
        assert manager.is_active(now) is True

    def test_upcoming_before_start(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.set_override(now + timedelta(hours=1), now + timedelta(hours=2))
        status = manager.get_status(now)
        assert status["state"] == "upcoming"
        assert manager.is_active(now) is False

    def test_end_must_be_after_start(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        with pytest.raises(ValueError):
            manager.set_override(now, now)
        with pytest.raises(ValueError):
            manager.set_override(now, now - timedelta(hours=1))

    def test_second_set_replaces_first(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.set_override(now, now + timedelta(hours=1))
        manager.set_override(now + timedelta(hours=5), now + timedelta(hours=6))
        status = manager.get_status(now)
        assert status["state"] == "upcoming"
        assert status["start"] == now + timedelta(hours=5)


class TestExpiry:
    def test_expired_override_auto_cleared(self, manager, override_file):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.set_override(now - timedelta(hours=2), now - timedelta(hours=1))
        status = manager.get_status(now)
        assert status["state"] == "none"
        assert not manager._load()

    def test_end_boundary_is_exclusive_and_expired(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.set_override(now - timedelta(hours=1), now)
        assert manager.get_status(now)["state"] == "none"


class TestClearOverride:
    def test_clear_removes_active_override(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.set_override(now - timedelta(hours=1), now + timedelta(hours=1))
        manager.clear_override()
        assert manager.get_status(now)["state"] == "none"

    def test_clear_when_nothing_stored_is_a_noop(self, manager):
        manager.clear_override()
        assert manager.get_status()["state"] == "none"


class TestCorruptFile:
    def test_invalid_json_treated_as_no_override(self, override_file):
        with open(override_file, "w") as f:
            f.write("not json")
        manager = OverrideManager(override_file)
        assert manager.get_status()["state"] == "none"

    def test_missing_keys_treated_as_no_override(self, override_file):
        with open(override_file, "w") as f:
            f.write('{"start": "2026-07-10T12:00:00"}')
        manager = OverrideManager(override_file)
        assert manager.get_status()["state"] == "none"
