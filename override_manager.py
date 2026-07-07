#!/usr/bin/env python3
"""Override Manager — a manual pause window during which schedule enforcement is skipped."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%S"


class OverrideManager:
    """Persists a single start/end override window to a JSON file.

    While *now* falls within [start, end), the caller should skip schedule
    enforcement entirely so manual thermostat changes are left alone.
    """

    def __init__(self, override_file: str = "override.json") -> None:
        self.override_file = override_file

    def _load(self) -> dict | None:
        path = Path(self.override_file)
        if not path.exists():
            return None
        text = path.read_text().strip()
        if not text:
            return None
        try:
            data = json.loads(text)
            return {
                "start": datetime.strptime(data["start"], _DATETIME_FORMAT),
                "end": datetime.strptime(data["end"], _DATETIME_FORMAT),
            }
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Invalid override file {self.override_file}: {e}")
            return None

    def set_override(self, start: datetime, end: datetime) -> None:
        """Persist a new override window, replacing any existing one."""
        if end <= start:
            raise ValueError("End time must be after start time")
        Path(self.override_file).write_text(json.dumps({
            "start": start.strftime(_DATETIME_FORMAT),
            "end": end.strftime(_DATETIME_FORMAT),
        }))
        logger.info(f"Override set: {start} -> {end}")

    def clear_override(self) -> None:
        """Remove any stored override.

        Writes an empty file rather than unlinking it: override_file is
        typically a hostPath bind-mounted single file in the k8s deployment,
        and a mount point can't be unlinked from inside the container
        (OSError: Device or resource busy).
        """
        path = Path(self.override_file)
        if path.exists() and path.read_text().strip():
            path.write_text("")
            logger.info("Override cleared")

    def get_status(self, now: datetime | None = None) -> dict:
        """Return the current override status.

        {'state': 'none'} when nothing is stored (or it has expired — an
        expired override is deleted automatically), {'state': 'upcoming',
        'start':, 'end':} when the window hasn't started yet, or
        {'state': 'active', 'start':, 'end':} while inside the window.
        """
        now = now or datetime.now()
        override = self._load()
        if override is None:
            return {"state": "none"}
        if now >= override["end"]:
            self.clear_override()
            return {"state": "none"}
        state = "upcoming" if now < override["start"] else "active"
        return {"state": state, **override}

    def is_active(self, now: datetime | None = None) -> bool:
        """Return True when *now* falls within the stored override window."""
        return self.get_status(now)["state"] == "active"


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    manager = OverrideManager("override.json")
    print(f"Status: {manager.get_status()}")
