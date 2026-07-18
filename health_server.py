#!/usr/bin/env python3
"""Health Server — HTTP endpoints for liveness, status, and statistics."""

from __future__ import annotations

import functools
import hmac
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from flask import Flask, abort, jsonify, redirect, render_template_string, request, url_for

from override_manager import OverrideManager

logger = logging.getLogger(__name__)


def _require_api_key(api_key: str | None):
    """Return a decorator that enforces the X-API-Key header when *api_key* is set."""
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if api_key:
                provided = request.headers.get("X-API-Key") or ""
                if not hmac.compare_digest(provided.encode(), api_key.encode()):
                    abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def _format_duration(delta: timedelta) -> str:
    """Render a timedelta as a short human string, e.g. '2h 15m' or '3d 4h'."""
    total_seconds = max(0, int(delta.total_seconds()))
    if total_seconds < 60:
        return "under a minute"
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


_OVERRIDE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ecobee Manager — Override</title>
<style>
  :root {
    --bg: #14171c;
    --panel: #1b2027;
    --panel-border: #262c35;
    --text: #e9ecf1;
    --muted: #8992a3;
    --ember: #ff8a3d;
    --ember-dim: rgba(255, 138, 61, 0.14);
    --teal: #4fd1c5;
    --teal-dim: rgba(79, 209, 197, 0.14);
    --danger: #ff6b6b;
    --track: #262c35;
    --mono: ui-monospace, "SF Mono", "Cascadia Code", "Roboto Mono", Consolas, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    margin: 0;
    padding: 48px 20px;
    display: flex;
    justify-content: center;
  }
  main { width: 100%; max-width: 420px; }
  .eyebrow {
    font-family: var(--mono);
    font-size: 12px;
    letter-spacing: 0.14em;
    color: var(--muted);
    text-transform: uppercase;
    margin: 0 0 6px;
  }
  h1 { font-size: 24px; font-weight: 600; margin: 0 0 28px; letter-spacing: -0.01em; }
  .banner {
    font-family: var(--mono);
    font-size: 13px;
    color: var(--danger);
    background: rgba(255, 107, 107, 0.1);
    border: 1px solid rgba(255, 107, 107, 0.3);
    border-radius: 8px;
    padding: 10px 14px;
    margin: 0 0 20px;
  }
  .card { background: var(--panel); border: 1px solid var(--panel-border); border-radius: 12px; padding: 20px; }
  .status-row { display: flex; align-items: center; gap: 10px; }
  .led { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .led.enforcing { background: var(--ember); box-shadow: 0 0 0 4px var(--ember-dim); }
  .led.paused { background: var(--teal); box-shadow: 0 0 0 4px var(--teal-dim); }
  @media (prefers-reduced-motion: no-preference) {
    .led.enforcing { animation: pulse-ember 2.6s ease-in-out infinite; }
    .led.paused { animation: pulse-teal 2.6s ease-in-out infinite; }
  }
  @keyframes pulse-ember {
    0%, 100% { box-shadow: 0 0 0 4px var(--ember-dim); }
    50% { box-shadow: 0 0 0 8px var(--ember-dim); }
  }
  @keyframes pulse-teal {
    0%, 100% { box-shadow: 0 0 0 4px var(--teal-dim); }
    50% { box-shadow: 0 0 0 8px var(--teal-dim); }
  }
  .status-word {
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .status-word.enforcing { color: var(--ember); }
  .status-word.paused { color: var(--teal); }
  .status-detail { margin: 10px 0 0; font-size: 14px; color: var(--muted); line-height: 1.5; }
  .bar-track { margin-top: 16px; height: 6px; border-radius: 3px; background: var(--track); overflow: hidden; }
  .bar-fill { height: 100%; border-radius: 3px; background: var(--teal); }
  .bar-caption { margin: 8px 0 0; font-family: var(--mono); font-size: 12px; color: var(--muted); }
  .card-actions { margin-top: 18px; }
  .override-list { display: flex; flex-direction: column; gap: 12px; margin-bottom: 8px; }
  h2 {
    font-family: var(--mono);
    font-size: 12px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 600;
    margin: 32px 0 12px;
  }
  .field-row { display: flex; flex-wrap: wrap; gap: 12px; }
  .field { flex: 1 1 160px; display: flex; flex-direction: column; gap: 6px; }
  label { font-size: 12px; color: var(--muted); }
  input[type="datetime-local"] {
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 10px 12px;
    color: var(--text);
    font-family: var(--sans);
    font-size: 14px;
    color-scheme: dark;
    width: 100%;
  }
  button {
    font-family: var(--sans);
    font-size: 14px;
    font-weight: 600;
    border: none;
    border-radius: 8px;
    padding: 11px 18px;
    cursor: pointer;
    margin-top: 16px;
  }
  .btn-pause { background: var(--teal); color: #0b1210; }
  .btn-resume { background: var(--ember); color: #241300; }
  .btn-pause:hover, .btn-resume:hover { filter: brightness(1.08); }
  input:focus-visible, button:focus-visible { outline: 2px solid var(--teal); outline-offset: 2px; }
  p.hint { font-size: 13px; color: var(--muted); margin: 0 0 4px; }
</style>
</head>
<body>
<main>
  <p class="eyebrow">Ecobee Manager</p>
  <h1>Manual override</h1>

  {% if error %}<p class="banner">{{ error }}</p>{% endif %}

  <div class="card">
    <div class="status-row">
      <span class="led {{ badge_class }}"></span>
      <span class="status-word {{ badge_class }}">{{ status_label }}</span>
    </div>
    {% if not overrides %}
      <p class="status-detail">Automatic enforcement is running normally.</p>
    {% endif %}
  </div>

  {% if overrides %}
  <h2>Scheduled pauses</h2>
  <div class="override-list">
    {% for o in overrides %}
    <div class="card">
      <div class="status-row">
        <span class="led {{ 'paused' if o.state == 'active' else 'enforcing' }}"></span>
        <span class="status-word {{ 'paused' if o.state == 'active' else 'enforcing' }}">
          {{ 'Active now' if o.state == 'active' else 'Upcoming' }}
        </span>
      </div>
      {% if o.state == 'active' %}
        <p class="status-detail">Paused until {{ o.end_human }}. The thermostat won't be touched until then.</p>
        <div class="bar-track"><div class="bar-fill" style="width: {{ o.percent }}%"></div></div>
        <p class="bar-caption">{{ o.caption }}</p>
      {% else %}
        <p class="status-detail">Pausing from {{ o.start_human }} to {{ o.end_human }}.</p>
        <p class="bar-caption">{{ o.caption }}</p>
      {% endif %}
      <div class="card-actions">
        <form method="post" action="{{ url_for('override_cancel_one', override_id=o.id) }}">
          <button type="submit" class="btn-resume">Cancel this pause</button>
        </form>
      </div>
    </div>
    {% endfor %}
    {% if overrides|length > 1 %}
    <form method="post" action="{{ url_for('override_cancel_all') }}">
      <button type="submit" class="btn-resume">Cancel all</button>
    </form>
    {% endif %}
  </div>
  {% endif %}

  <h2>Schedule a pause</h2>
  <p class="hint">The thermostat won't be adjusted during this window. You can schedule more than one.</p>
  <form method="post" action="{{ url_for('override_submit') }}">
    <div class="field-row">
      <div class="field">
        <label for="start">From</label>
        <input id="start" type="datetime-local" name="start" value="{{ now_value }}" required>
      </div>
      <div class="field">
        <label for="end">Until</label>
        <input id="end" type="datetime-local" name="end" required>
      </div>
    </div>
    <button type="submit" class="btn-pause">Schedule pause</button>
  </form>
</main>
</body>
</html>
"""


class HealthServer:
    """HTTP server for health monitoring, exposed on a background thread."""

    def __init__(self, port: int = 8080, override_manager: OverrideManager | None = None) -> None:
        self.port = port
        self.app = Flask(__name__)
        self._server_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._api_key = os.environ.get("HEALTH_API_KEY")
        self._lock = threading.Lock()
        self.override_manager = override_manager

        self.start_time = datetime.now(timezone.utc)
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

        @self.app.after_request
        def _security_headers(response):
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            if response.content_type.startswith("text/html"):
                # The /override page needs an inline <style> block; still no scripts.
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; style-src 'unsafe-inline'; script-src 'none'"
                )
            else:
                response.headers["Content-Security-Policy"] = "default-src 'none'"
            return response

        @self.app.route("/health", methods=["GET"])
        def health():
            with self._lock:
                token_valid = self.state["token_valid"]
                schedule_loaded = self.state["schedule_loaded"]
            uptime = int((datetime.now(timezone.utc) - self.start_time).total_seconds())
            if not token_valid:
                status, code = "unhealthy", 503
            elif not schedule_loaded:
                status, code = "degraded", 503
            else:
                status, code = "healthy", 200
            return jsonify({
                "status": status,
                "uptime_seconds": uptime,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }), code

        @self.app.route("/status", methods=["GET"])
        @protected
        def status():
            with self._lock:
                state = dict(self.state)
                stats = dict(self.stats)
            now = datetime.now(timezone.utc)
            uptime = int((now - self.start_time).total_seconds())

            token_expires_in_minutes = None
            if state["token_expires_at"]:
                exp_at = state["token_expires_at"]
                if exp_at.tzinfo is None:
                    exp_at = exp_at.replace(tzinfo=timezone.utc)
                token_expires_in_minutes = int((exp_at - now).total_seconds() / 60)

            refresh_expires_in_days = None
            if state["refresh_token_expires_at"]:
                ref_at = state["refresh_token_expires_at"]
                if ref_at.tzinfo is None:
                    ref_at = ref_at.replace(tzinfo=timezone.utc)
                refresh_expires_in_days = (ref_at - now).days

            cur = state["current_temperature"]
            exp = state["expected_temperature"]
            return jsonify({
                "status": "healthy" if state["token_valid"] else "unhealthy",
                "uptime_seconds": uptime,
                "start_time": self.start_time.isoformat(),
                "current_time": now.isoformat(),
                "token_status": "valid" if state["token_valid"] else "invalid",
                "token_expires_in_minutes": token_expires_in_minutes,
                "refresh_token_expires_in_days": refresh_expires_in_days,
                "current_temperature": cur,
                "expected_temperature": exp,
                "temperature_match": (cur == exp) if cur is not None and exp is not None else None,
                "schedule_loaded": state["schedule_loaded"],
                "checks_performed": stats["checks_performed"],
                "reverts_performed": stats["reverts_performed"],
                "token_refreshes": stats["token_refreshes"],
                "errors": stats["errors"],
                "last_check": stats["last_check"].isoformat() if stats["last_check"] else None,
                "last_revert": stats["last_revert"].isoformat() if stats["last_revert"] else None,
                "last_error": stats["last_error"].isoformat() if stats["last_error"] else None,
            })

        @self.app.route("/schedule", methods=["GET"])
        @protected
        def schedule():
            with self._lock:
                state = dict(self.state)
            return jsonify({
                "schedule_loaded": state["schedule_loaded"],
                "current_time": datetime.now(timezone.utc).isoformat(),
                "expected_temperature": state["expected_temperature"],
                "current_temperature": state["current_temperature"],
            })

        @self.app.route("/stats", methods=["GET"])
        @protected
        def stats():
            with self._lock:
                s = dict(self.stats)
            uptime = (datetime.now(timezone.utc) - self.start_time).total_seconds()
            checks = s["checks_performed"]
            reverts = s["reverts_performed"]
            errors = s["errors"]
            return jsonify({
                "uptime_seconds": int(uptime),
                "uptime_hours": round(uptime / 3600, 2),
                "uptime_days": round(uptime / 86400, 2),
                "checks_performed": checks,
                "reverts_performed": reverts,
                "token_refreshes": s["token_refreshes"],
                "errors": errors,
                "revert_rate": round(reverts / checks * 100, 2) if checks else 0,
                "error_rate": round(errors / checks * 100, 2) if checks else 0,
            })

        @self.app.route("/override", methods=["GET"])
        def override_page():
            context = self._override_context()
            return render_template_string(
                _OVERRIDE_TEMPLATE, error=request.args.get("error"), **context
            )

        @self.app.route("/override", methods=["POST"])
        def override_submit():
            if self.override_manager is None:
                abort(404)
            try:
                start = datetime.strptime(request.form.get("start", ""), "%Y-%m-%dT%H:%M")
                end = datetime.strptime(request.form.get("end", ""), "%Y-%m-%dT%H:%M")
            except ValueError:
                return redirect(
                    url_for("override_page", error="Enter a valid start and end date/time.")
                )
            try:
                self.override_manager.add_override(start, end)
            except ValueError as e:
                return redirect(url_for("override_page", error=str(e)))
            return redirect(url_for("override_page"))

        @self.app.route("/override/cancel", methods=["POST"])
        def override_cancel_all():
            if self.override_manager is None:
                abort(404)
            self.override_manager.clear_override()
            return redirect(url_for("override_page"))

        @self.app.route("/override/cancel/<override_id>", methods=["POST"])
        def override_cancel_one(override_id: str):
            if self.override_manager is None:
                abort(404)
            self.override_manager.remove_override(override_id)
            return redirect(url_for("override_page"))

    # ------------------------------------------------------------------
    # Override page helpers
    # ------------------------------------------------------------------

    def _override_context(self) -> dict:
        """Build the template context for the /override page."""
        now = datetime.now()
        windows = self.override_manager.list_overrides(now) if self.override_manager else []
        paused_now = any(w["state"] == "active" for w in windows)
        context: dict = {
            "now_value": now.strftime("%Y-%m-%dT%H:%M"),
            "badge_class": "paused" if paused_now else "enforcing",
            "status_label": "Schedule paused" if paused_now else "Schedule active",
            "overrides": [],
        }

        for w in windows:
            start, end = w["start"], w["end"]
            item: dict = {
                "id": w["id"],
                "state": w["state"],
                "start_human": start.strftime("%a %b %-d, %-I:%M %p"),
                "end_human": end.strftime("%a %b %-d, %-I:%M %p"),
            }
            if w["state"] == "active":
                total = (end - start).total_seconds()
                elapsed = (now - start).total_seconds()
                item["percent"] = max(0, min(100, int(elapsed / total * 100))) if total > 0 else 100
                item["caption"] = f"Ends in {_format_duration(end - now)}"
            else:
                item["caption"] = (
                    f"Starts in {_format_duration(start - now)}, runs {_format_duration(end - start)}"
                )
            context["overrides"].append(item)

        return context

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
        with self._lock:
            self.state["token_valid"] = valid
            if expires_at is not None:
                self.state["token_expires_at"] = expires_at
            if refresh_expires_at is not None:
                self.state["refresh_token_expires_at"] = refresh_expires_at

    def update_temperature_status(
        self, current: int | None, expected: int | None
    ) -> None:
        """Update the last-seen current and expected temperatures."""
        with self._lock:
            self.state["current_temperature"] = current
            self.state["expected_temperature"] = expected

    def update_schedule_status(self, loaded: bool) -> None:
        """Record whether the schedule was loaded successfully."""
        with self._lock:
            self.state["schedule_loaded"] = loaded

    def _increment(self, key: str, timestamp_key: str | None = None) -> None:
        with self._lock:
            self.stats[key] += 1
            if timestamp_key:
                self.stats[timestamp_key] = datetime.now(timezone.utc)

    def increment_checks(self) -> None:
        self._increment("checks_performed", "last_check")

    def increment_reverts(self) -> None:
        self._increment("reverts_performed", "last_revert")

    def increment_token_refreshes(self) -> None:
        self._increment("token_refreshes")

    def increment_errors(self) -> None:
        self._increment("errors", "last_error")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the health server in a background daemon thread via waitress."""
        host = os.environ.get("HEALTH_HOST", "127.0.0.1")

        def _run() -> None:
            from waitress import serve
            logger.info("Health server listening on %s:%s", host, self.port)
            serve(self.app, host=host, port=self.port, threads=4)

        self._server_thread = threading.Thread(target=_run, daemon=True, name="health-server")
        self._server_thread.start()
        logger.info("Health server started on http://%s:%s", host, self.port)

    def is_running(self) -> bool:
        """Return True when the server thread is alive."""
        return self._server_thread is not None and self._server_thread.is_alive()


if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)

    server = HealthServer(port=8080)
    server.update_token_status(True, datetime.now(timezone.utc))
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
