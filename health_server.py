#!/usr/bin/env python3
"""Health Server — HTTP endpoints for liveness, status, and statistics."""

from __future__ import annotations

import functools
import logging
import os
import threading
from datetime import datetime

from flask import Flask, abort, jsonify, request

logger = logging.getLogger(__name__)


def _require_api_key(api_key: str | None):
    """Return a decorator that enforces the X-API-Key header when *api_key* is set."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if api_key:
                if request.headers.get("X-API-Key") != api_key:
                    abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


class HealthServer:
    """HTTP server for health monitoring, exposed on a background thread."""

    def __init__(self, port: int = 8080) -> None:
        self.port = port
        self.app = Flask(__name__)
        self._server_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._api_key = os.environ.get("HEALTH_API_KEY")

        self.start_time = datetime.now()
        self.stats: dict = {
            "checks_performed": 0,
            "reverts_performed": 0,
            "token_refreshes": 0,
            "errors": 0,
            "last_check": None,
            "last_revert": None,
            "last_error": None,
        }
        self.state: dict = {
            "token_valid": False,
            "token_expires_at": None,
            "refresh_token_expires_at": None,
            "current_temperature": None,
            "expected_temperature": None,
            "schedule_loaded": False,
        }

        self._setup_routes()

    # ------------------------------------------------------------------
    # Route setup
    # ------------------------------------------------------------------

    def _setup_routes(self) -> None:
        protected = _require_api_key(self._api_key)

        @self.app.route("/health", methods=["GET"])
        def health():
            uptime = int((datetime.now() - self.start_time).total_seconds())
            if not self.state["token_valid"]:
                status, code = "unhealthy", 503
            elif not self.state["schedule_loaded"]:
                status, code = "degraded", 503
            else:
                status, code = "healthy", 200
            return jsonify({
                "status": status,
                "uptime_seconds": uptime,
                "timestamp": datetime.now().isoformat(),
            }), code

        @self.app.route("/status", methods=["GET"])
        @protected
        def status():
            now = datetime.now()
            uptime = int((now - self.start_time).total_seconds())

            token_expires_in_minutes = None
            if self.state["token_expires_at"]:
                delta = (self.state["token_expires_at"] - now).total_seconds()
                token_expires_in_minutes = int(delta / 60)

            refresh_expires_in_days = None
            if self.state["refresh_token_expires_at"]:
                refresh_expires_in_days = (
                    self.state["refresh_token_expires_at"] - now
                ).days

            cur = self.state["current_temperature"]
            exp = self.state["expected_temperature"]
            return jsonify({
                "status": "healthy" if self.state["token_valid"] else "unhealthy",
                "uptime_seconds": uptime,
                "start_time": self.start_time.isoformat(),
                "current_time": now.isoformat(),
                "token_status": "valid" if self.state["token_valid"] else "invalid",
                "token_expires_in_minutes": token_expires_in_minutes,
                "refresh_token_expires_in_days": refresh_expires_in_days,
                "current_temperature": cur,
                "expected_temperature": exp,
                "temperature_match": (cur == exp) if cur is not None and exp is not None else None,
                "schedule_loaded": self.state["schedule_loaded"],
                "checks_performed": self.stats["checks_performed"],
                "reverts_performed": self.stats["reverts_performed"],
                "token_refreshes": self.stats["token_refreshes"],
                "errors": self.stats["errors"],
                "last_check": self.stats["last_check"].isoformat() if self.stats["last_check"] else None,
                "last_revert": self.stats["last_revert"].isoformat() if self.stats["last_revert"] else None,
                "last_error": self.stats["last_error"].isoformat() if self.stats["last_error"] else None,
            })

        @self.app.route("/schedule", methods=["GET"])
        @protected
        def schedule():
            return jsonify({
                "schedule_loaded": self.state["schedule_loaded"],
                "current_time": datetime.now().isoformat(),
                "expected_temperature": self.state["expected_temperature"],
                "current_temperature": self.state["current_temperature"],
            })

        @self.app.route("/stats", methods=["GET"])
        @protected
        def stats():
            uptime = (datetime.now() - self.start_time).total_seconds()
            checks = self.stats["checks_performed"]
            reverts = self.stats["reverts_performed"]
            errors = self.stats["errors"]
            return jsonify({
                "uptime_seconds": int(uptime),
                "uptime_hours": round(uptime / 3600, 2),
                "uptime_days": round(uptime / 86400, 2),
                "checks_performed": checks,
                "reverts_performed": reverts,
                "token_refreshes": self.stats["token_refreshes"],
                "errors": errors,
                "revert_rate": round(reverts / checks * 100, 2) if checks else 0,
                "error_rate": round(errors / checks * 100, 2) if checks else 0,
            })

    # ------------------------------------------------------------------
    # State mutators (called from the service loop)
    # ------------------------------------------------------------------

    def update_token_status(
        self,
        valid: bool,
        expires_at: datetime | None = None,
        refresh_expires_at: datetime | None = None,
    ) -> None:
        """Update token validity and expiry timestamps."""
        self.state["token_valid"] = valid
        if expires_at is not None:
            self.state["token_expires_at"] = expires_at
        if refresh_expires_at is not None:
            self.state["refresh_token_expires_at"] = refresh_expires_at

    def update_temperature_status(
        self, current: int | None, expected: int | None
    ) -> None:
        """Update the last-seen current and expected temperatures."""
        self.state["current_temperature"] = current
        self.state["expected_temperature"] = expected

    def update_schedule_status(self, loaded: bool) -> None:
        """Record whether the schedule was loaded successfully."""
        self.state["schedule_loaded"] = loaded

    def increment_checks(self) -> None:
        self.stats["checks_performed"] += 1
        self.stats["last_check"] = datetime.now()

    def increment_reverts(self) -> None:
        self.stats["reverts_performed"] += 1
        self.stats["last_revert"] = datetime.now()

    def increment_token_refreshes(self) -> None:
        self.stats["token_refreshes"] += 1

    def increment_errors(self) -> None:
        self.stats["errors"] += 1
        self.stats["last_error"] = datetime.now()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the health server in a background daemon thread via waitress."""
        def _run() -> None:
            from waitress import serve
            logger.info(f"Health server listening on 0.0.0.0:{self.port}")
            serve(self.app, host="0.0.0.0", port=self.port, threads=4)

        self._server_thread = threading.Thread(target=_run, daemon=True, name="health-server")
        self._server_thread.start()
        logger.info(f"Health server started on http://0.0.0.0:{self.port}")

    def is_running(self) -> bool:
        """Return True when the server thread is alive."""
        return self._server_thread is not None and self._server_thread.is_alive()


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)

    server = HealthServer(port=8080)
    server.update_token_status(True, datetime.now())
    server.update_temperature_status(68, 68)
    server.update_schedule_status(True)
    server.increment_checks()
    server.start()

    print("Health server running on http://localhost:8080")
    print("Endpoints: /health  /status  /schedule  /stats")
    print("Press Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping server...")
