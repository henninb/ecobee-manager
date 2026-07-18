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

    def test_list_overrides_empty_when_no_file(self, manager):
        assert manager.list_overrides() == []


class TestAddOverride:
    def test_add_then_active_within_window(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now - timedelta(hours=1), now + timedelta(hours=1))
        status = manager.get_status(now)
        assert status["state"] == "active"
        assert status["start"] == now - timedelta(hours=1)
        assert status["end"] == now + timedelta(hours=1)

    def test_is_active_true_within_window(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now - timedelta(hours=1), now + timedelta(hours=1))
        assert manager.is_active(now) is True

    def test_upcoming_before_start(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now + timedelta(hours=1), now + timedelta(hours=2))
        status = manager.get_status(now)
        assert status["state"] == "upcoming"
        assert manager.is_active(now) is False

    def test_end_must_be_after_start(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        with pytest.raises(ValueError):
            manager.add_override(now, now)
        with pytest.raises(ValueError):
            manager.add_override(now, now - timedelta(hours=1))

    def test_returns_an_id(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        override_id = manager.add_override(now, now + timedelta(hours=1))
        assert isinstance(override_id, str) and override_id

    def test_ids_are_unique(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        first = manager.add_override(now, now + timedelta(hours=1))
        second = manager.add_override(now + timedelta(hours=2), now + timedelta(hours=3))
        assert first != second


class TestMultipleOverrides:
    def test_second_add_does_not_replace_first(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now, now + timedelta(hours=1))
        manager.add_override(now + timedelta(hours=5), now + timedelta(hours=6))
        assert len(manager.list_overrides(now)) == 2

    def test_list_overrides_sorted_by_start(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now + timedelta(hours=5), now + timedelta(hours=6))
        manager.add_override(now + timedelta(hours=1), now + timedelta(hours=2))
        windows = manager.list_overrides(now)
        assert [w["start"] for w in windows] == [
            now + timedelta(hours=1),
            now + timedelta(hours=5),
        ]

    def test_list_overrides_reports_state_per_window(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now - timedelta(hours=1), now + timedelta(hours=1))
        manager.add_override(now + timedelta(hours=5), now + timedelta(hours=6))
        windows = manager.list_overrides(now)
        assert [w["state"] for w in windows] == ["active", "upcoming"]

    def test_get_status_prefers_the_active_window(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now + timedelta(hours=5), now + timedelta(hours=6))
        manager.add_override(now - timedelta(hours=1), now + timedelta(hours=1))
        status = manager.get_status(now)
        assert status["state"] == "active"

    def test_get_status_falls_back_to_earliest_upcoming(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now + timedelta(hours=5), now + timedelta(hours=6))
        manager.add_override(now + timedelta(hours=1), now + timedelta(hours=2))
        status = manager.get_status(now)
        assert status["state"] == "upcoming"
        assert status["start"] == now + timedelta(hours=1)

    def test_overlapping_windows_are_allowed(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now - timedelta(hours=1), now + timedelta(hours=2))
        manager.add_override(now, now + timedelta(hours=1))
        assert len(manager.list_overrides(now)) == 2
        assert manager.is_active(now) is True


class TestRemoveOverride:
    def test_remove_by_id(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        keep = manager.add_override(now, now + timedelta(hours=1))
        drop = manager.add_override(now + timedelta(hours=5), now + timedelta(hours=6))
        assert manager.remove_override(drop) is True
        remaining = manager.list_overrides(now)
        assert [w["id"] for w in remaining] == [keep]

    def test_remove_unknown_id_returns_false(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now, now + timedelta(hours=1))
        assert manager.remove_override("nonexistent") is False
        assert len(manager.list_overrides(now)) == 1


class TestExpiry:
    def test_expired_override_auto_purged(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now - timedelta(hours=2), now - timedelta(hours=1))
        status = manager.get_status(now)
        assert status["state"] == "none"
        assert manager.list_overrides(now) == []

    def test_end_boundary_is_exclusive_and_expired(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now - timedelta(hours=1), now)
        assert manager.get_status(now)["state"] == "none"

    def test_expired_windows_purged_without_disturbing_live_ones(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now - timedelta(hours=2), now - timedelta(hours=1))
        manager.add_override(now - timedelta(hours=1), now + timedelta(hours=1))
        windows = manager.list_overrides(now)
        assert len(windows) == 1
        assert windows[0]["state"] == "active"


class TestClearOverride:
    def test_clear_removes_every_window(self, manager):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager.add_override(now - timedelta(hours=1), now + timedelta(hours=1))
        manager.add_override(now + timedelta(hours=5), now + timedelta(hours=6))
        manager.clear_override()
        assert manager.get_status(now)["state"] == "none"
        assert manager.list_overrides(now) == []

    def test_clear_when_nothing_stored_is_a_noop(self, manager):
        manager.clear_override()
        assert manager.get_status()["state"] == "none"


class TestCorruptFile:
    def test_invalid_json_treated_as_no_override(self, override_file):
        with open(override_file, "w") as f:
            f.write("not json")
        manager = OverrideManager(override_file)
        assert manager.get_status()["state"] == "none"

    def test_missing_keys_in_entry_are_skipped(self, override_file):
        with open(override_file, "w") as f:
            f.write('{"overrides": [{"start": "2026-07-10T12:00:00"}]}')
        manager = OverrideManager(override_file)
        assert manager.get_status()["state"] == "none"

    def test_one_bad_entry_does_not_break_the_rest(self, override_file):
        now = datetime(2026, 7, 10, 12, 0, 0)
        manager = OverrideManager(override_file)
        manager.add_override(now - timedelta(hours=1), now + timedelta(hours=1))
        # Corrupt the stored payload by appending a malformed second entry.
        import json
        data = json.loads(open(override_file).read())
        data["overrides"].append({"id": "bad", "start": "not-a-date"})
        with open(override_file, "w") as f:
            f.write(json.dumps(data))
        windows = manager.list_overrides(now)
        assert len(windows) == 1
        assert windows[0]["state"] == "active"
