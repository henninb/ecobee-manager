#!/usr/bin/env python3
"""Override Manager — manual pause windows during which schedule enforcement is skipped."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class OverrideManager:
    """Persists a set of start/end override windows to a JSON file.

    While *now* falls within any stored [start, end) window, the caller should
    skip schedule enforcement entirely so manual thermostat changes are left
    alone. Any number of windows may be scheduled at once — they don't need
    to be contiguous or non-overlapping.
    """

    def __init__(self, override_file: str = "override.json") -> None:
        self.override_file = override_file

    def _load(self) -> list[dict]:
        """Return stored windows, sorted by start time. Bad entries are skipped."""
        path = Path(self.override_file)
        if not path.exists():
            return []
        text = path.read_text().strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid override file {self.override_file}: {e}")
            return []
        entries = raw.get("overrides", []) if isinstance(raw, dict) else []
        windows = []
        for entry in entries:
            try:
                windows.append({
                    "id": entry["id"],
                    "start": datetime.strptime(entry["start"], _DATETIME_FORMAT),
                    "end": datetime.strptime(entry["end"], _DATETIME_FORMAT),
                })
            except (KeyError, TypeError, ValueError) as e:
                logger.error(f"Skipping invalid override entry {entry!r}: {e}")
        windows.sort(key=lambda w: w["start"])
        return windows

    def _save(self, windows: list[dict]) -> None:
        Path(self.override_file).write_text(json.dumps({
            "overrides": [
                {
                    "id": w["id"],
                    "start": w["start"].strftime(_DATETIME_FORMAT),
                    "end": w["end"].strftime(_DATETIME_FORMAT),
                }
                for w in windows
            ],
        }))

    def add_override(self, start: datetime, end: datetime) -> str:
        """Schedule a new pause window alongside any existing ones. Returns its id."""
        if end <= start:
            raise ValueError("End time must be after start time")
        windows = self._load()
        override_id = uuid.uuid4().hex[:8]
        windows.append({"id": override_id, "start": start, "end": end})
        self._save(windows)
        logger.info(f"Override {override_id} scheduled: {start} -> {end}")
        return override_id

    def remove_override(self, override_id: str) -> bool:
        """Remove one scheduled window by id. Returns whether it was found."""
        windows = self._load()
        remaining = [w for w in windows if w["id"] != override_id]
        if len(remaining) == len(windows):
            return False
        self._save(remaining)
        logger.info(f"Override {override_id} cleared")
        return True

    def clear_override(self) -> None:
        """Remove every scheduled window.

        Writes an empty file rather than unlinking it: override_file is
        typically a hostPath bind-mounted single file in the k8s deployment,
        and a mount point can't be unlinked from inside the container
        (OSError: Device or resource busy).
        """
        path = Path(self.override_file)
        if path.exists() and path.read_text().strip():
            path.write_text("")
            logger.info("All overrides cleared")

    def list_overrides(self, now: datetime | None = None) -> list[dict]:
        """Return all non-expired windows, sorted by start time, with a computed state.

        Expired windows (end <= now) are purged from storage as a side effect.
        """
        now = now or datetime.now()
        windows = self._load()
        live = [w for w in windows if now < w["end"]]
        if len(live) != len(windows):
            self._save(live)
        return [
            {**w, "state": "active" if w["start"] <= now < w["end"] else "upcoming"}
            for w in live
        ]

    def get_status(self, now: datetime | None = None) -> dict:
        """Return the status of the window currently in effect, if any.

        {'state': 'none'} when nothing is stored (or everything has expired),
        {'state': 'active', ...} for the window currently pausing enforcement,
        otherwise {'state': 'upcoming', ...} for the earliest window still
        ahead. Use list_overrides() to see every scheduled window at once.
        """
        now = now or datetime.now()
        windows = self.list_overrides(now)
        if not windows:
            return {"state": "none"}
        active = next((w for w in windows if w["state"] == "active"), None)
        return dict(active or windows[0])

    def is_active(self, now: datetime | None = None) -> bool:
        """Return True when *now* falls within any stored override window."""
        return self.get_status(now)["state"] == "active"


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    manager = OverrideManager("override.json")
    print(f"Overrides: {manager.list_overrides()}")
