import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from secrets_loader import _gopass_get, load_secrets


def test_gopass_get_success():
    mock_result = MagicMock(returncode=0, stdout="test@example.com\n")
    with patch("subprocess.run", return_value=mock_result):
        result = _gopass_get("ecobee/email")
    assert result == "test@example.com"


def test_gopass_get_failure():
    mock_result = MagicMock(returncode=1, stderr="not found")
    with patch("subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="gopass failed"):
            _gopass_get("ecobee/email")


def test_gopass_get_file_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(FileNotFoundError):
            _gopass_get("ecobee/email")


def test_gopass_get_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gopass", timeout=10)):
        with pytest.raises(subprocess.TimeoutExpired):
            _gopass_get("ecobee/email")


def test_load_secrets_already_set():
    with patch.dict(os.environ, {"ECOBEE_EMAIL": "existing@test.com", "ECOBEE_PASSWORD": "pass"}):
        with patch("secrets_loader._gopass_get") as mock_gopass:
            load_secrets()
        mock_gopass.assert_not_called()


def test_load_secrets_success(monkeypatch):
    monkeypatch.delenv("ECOBEE_EMAIL", raising=False)
    monkeypatch.delenv("ECOBEE_PASSWORD", raising=False)
    with patch("secrets_loader._gopass_get", side_effect=["user@test.com", "secret"]):
        load_secrets()
    assert os.environ.get("ECOBEE_EMAIL") == "user@test.com"
    assert os.environ.get("ECOBEE_PASSWORD") == "secret"
    monkeypatch.delenv("ECOBEE_EMAIL", raising=False)
    monkeypatch.delenv("ECOBEE_PASSWORD", raising=False)


def test_load_secrets_only_email_set(monkeypatch):
    monkeypatch.setenv("ECOBEE_EMAIL", "existing@test.com")
    monkeypatch.delenv("ECOBEE_PASSWORD", raising=False)
    with patch("secrets_loader._gopass_get", side_effect=["user@test.com", "secret"]):
        load_secrets()
    # ECOBEE_EMAIL already set; setdefault skips it
    assert os.environ["ECOBEE_EMAIL"] == "existing@test.com"
    monkeypatch.delenv("ECOBEE_PASSWORD", raising=False)


def test_load_secrets_gopass_not_found(monkeypatch):
    monkeypatch.delenv("ECOBEE_EMAIL", raising=False)
    monkeypatch.delenv("ECOBEE_PASSWORD", raising=False)
    with patch("secrets_loader._gopass_get", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="gopass is not installed"):
            load_secrets()


def test_load_secrets_timeout(monkeypatch):
    monkeypatch.delenv("ECOBEE_EMAIL", raising=False)
    monkeypatch.delenv("ECOBEE_PASSWORD", raising=False)
    with patch("secrets_loader._gopass_get", side_effect=subprocess.TimeoutExpired(cmd="gopass", timeout=10)):
        with pytest.raises(RuntimeError, match="timed out"):
            load_secrets()
