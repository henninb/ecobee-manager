import os
from datetime import datetime, timedelta, timezone

import pytest

from health_server import HealthServer, _require_api_key


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.delenv("HEALTH_API_KEY", raising=False)


@pytest.fixture
def server():
    return HealthServer(port=9999)


@pytest.fixture
def client(server):
    return server.app.test_client()


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_healthy(self, server, client):
        server.update_token_status(True)
        server.update_schedule_status(True)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "healthy"

    def test_unhealthy_no_token(self, server, client):
        server.update_token_status(False)
        server.update_schedule_status(True)
        resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.get_json()["status"] == "unhealthy"

    def test_degraded_no_schedule(self, server, client):
        server.update_token_status(True)
        server.update_schedule_status(False)
        resp = client.get("/health")
        assert resp.status_code == 503
        assert resp.get_json()["status"] == "degraded"

    def test_includes_uptime(self, server, client):
        data = client.get("/health").get_json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    def test_basic(self, server, client):
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "token_status" in data

    def test_token_expires_at_aware(self, server, client):
        expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        server.update_token_status(True, expires_at=expires)
        data = client.get("/status").get_json()
        assert data["token_expires_in_minutes"] is not None
        assert data["token_expires_in_minutes"] > 0

    def test_token_expires_at_naive(self, server, client):
        expires = datetime.now() + timedelta(minutes=30)
        server.update_token_status(True, expires_at=expires)
        data = client.get("/status").get_json()
        assert data["token_expires_in_minutes"] is not None

    def test_refresh_token_expires_aware(self, server, client):
        refresh = datetime.now(timezone.utc) + timedelta(days=30)
        server.update_token_status(True, refresh_expires_at=refresh)
        data = client.get("/status").get_json()
        assert data["refresh_token_expires_in_days"] is not None

    def test_refresh_token_expires_naive(self, server, client):
        refresh = datetime.now() + timedelta(days=30)
        server.update_token_status(True, refresh_expires_at=refresh)
        data = client.get("/status").get_json()
        assert data["refresh_token_expires_in_days"] is not None

    def test_temperature_match_true(self, server, client):
        server.update_temperature_status(68, 68)
        assert client.get("/status").get_json()["temperature_match"] is True

    def test_temperature_match_false(self, server, client):
        server.update_temperature_status(68, 70)
        assert client.get("/status").get_json()["temperature_match"] is False

    def test_temperature_none(self, server, client):
        server.update_temperature_status(None, None)
        assert client.get("/status").get_json()["temperature_match"] is None

    def test_last_check_in_response(self, server, client):
        server.increment_checks()
        data = client.get("/status").get_json()
        assert data["last_check"] is not None

    def test_last_revert_in_response(self, server, client):
        server.increment_reverts()
        data = client.get("/status").get_json()
        assert data["last_revert"] is not None

    def test_last_error_in_response(self, server, client):
        server.increment_errors()
        data = client.get("/status").get_json()
        assert data["last_error"] is not None


# ---------------------------------------------------------------------------
# /schedule
# ---------------------------------------------------------------------------

class TestScheduleEndpoint:
    def test_basic(self, server, client):
        server.update_temperature_status(68, 68)
        resp = client.get("/schedule")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "expected_temperature" in data
        assert "current_temperature" in data


# ---------------------------------------------------------------------------
# /stats
# ---------------------------------------------------------------------------

class TestStatsEndpoint:
    def test_zero_checks(self, server, client):
        data = client.get("/stats").get_json()
        assert data["revert_rate"] == 0
        assert data["error_rate"] == 0

    def test_revert_rate(self, server, client):
        server.increment_checks()
        server.increment_checks()
        server.increment_reverts()
        data = client.get("/stats").get_json()
        assert data["checks_performed"] == 2
        assert data["reverts_performed"] == 1
        assert data["revert_rate"] == 50.0

    def test_error_rate(self, server, client):
        server.increment_checks()
        server.increment_errors()
        data = client.get("/stats").get_json()
        assert data["error_rate"] == 100.0

    def test_uptime_fields(self, server, client):
        data = client.get("/stats").get_json()
        assert data["uptime_seconds"] >= 0
        assert "uptime_hours" in data
        assert "uptime_days" in data


# ---------------------------------------------------------------------------
# State mutators
# ---------------------------------------------------------------------------

class TestStateMutators:
    def test_increment_token_refreshes(self, server):
        server.increment_token_refreshes()
        assert server.stats["token_refreshes"] == 1

    def test_update_schedule_status(self, server):
        server.update_schedule_status(True)
        assert server.state["schedule_loaded"] is True

    def test_update_temperature_status(self, server):
        server.update_temperature_status(68, 70)
        assert server.state["current_temperature"] == 68
        assert server.state["expected_temperature"] == 70


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    def test_is_running_before_start(self, server):
        assert server.is_running() is False


# ---------------------------------------------------------------------------
# API key protection
# ---------------------------------------------------------------------------

class TestApiKeyProtection:
    def test_no_key_required_when_none_set(self, server, client):
        resp = client.get("/status")
        assert resp.status_code == 200

    def test_key_required_when_set(self, monkeypatch):
        monkeypatch.setenv("HEALTH_API_KEY", "mysecret")
        s = HealthServer(port=9998)
        c = s.app.test_client()
        assert c.get("/status").status_code == 403

    def test_correct_key_grants_access(self, monkeypatch):
        monkeypatch.setenv("HEALTH_API_KEY", "mysecret")
        s = HealthServer(port=9997)
        c = s.app.test_client()
        resp = c.get("/status", headers={"X-API-Key": "mysecret"})
        assert resp.status_code == 200

    def test_wrong_key_denied(self, monkeypatch):
        monkeypatch.setenv("HEALTH_API_KEY", "mysecret")
        s = HealthServer(port=9996)
        c = s.app.test_client()
        assert c.get("/status", headers={"X-API-Key": "wrong"}).status_code == 403

    def test_require_api_key_none(self):
        from flask import Flask
        app = Flask(__name__)

        @app.route("/t")
        @_require_api_key(None)
        def view():
            return "ok"

        with app.test_client() as c:
            assert c.get("/t").status_code == 200
