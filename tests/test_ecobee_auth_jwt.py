import base64
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from ecobee_auth_jwt import EcobeeAuthJWT, _get_jwks_client


def _fake_jwt(exp=None, iat=None) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    payload = {}
    if exp is not None:
        payload["exp"] = exp
    if iat is not None:
        payload["iat"] = iat
    enc = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{enc}.fakesig"


@pytest.fixture
def auth(tmp_path):
    cfg = str(tmp_path / "jwt.json")
    return EcobeeAuthJWT("user@test.com", "pass", config_file=cfg)


# ---------------------------------------------------------------------------
# Module-level helper
# ---------------------------------------------------------------------------

def test_get_jwks_client_singleton():
    import ecobee_auth_jwt
    ecobee_auth_jwt._jwks_client = None
    with patch("ecobee_auth_jwt.PyJWKClient") as MockJWKS:
        MockJWKS.return_value = MagicMock()
        c1 = _get_jwks_client()
        c2 = _get_jwks_client()
    assert c1 is c2


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_init(auth):
    assert auth.email == "user@test.com"
    assert auth.jwt_token is None
    assert auth.token_expires_at is None
    assert auth.last_refreshed is None
    assert auth.driver is None


def test_selenium_timeouts_from_env(monkeypatch):
    monkeypatch.setenv("SELENIUM_TIMEOUT", "15")
    monkeypatch.setenv("SELENIUM_REDIRECT_TIMEOUT", "90")
    a = EcobeeAuthJWT("u", "p")
    assert a.selenium_timeout == 15
    assert a.selenium_redirect_timeout == 90


# ---------------------------------------------------------------------------
# _parse_jwt_timestamps — fallback path (no JWKS)
# ---------------------------------------------------------------------------

class TestParseJwtTimestamps:
    def test_with_valid_exp_iat(self, auth):
        now = datetime.now(timezone.utc)
        exp = int((now + timedelta(hours=1)).timestamp())
        iat = int(now.timestamp())
        token = _fake_jwt(exp=exp, iat=iat)
        with patch("ecobee_auth_jwt._get_jwks_client", side_effect=Exception("no net")):
            expires_at, issued_at = auth._parse_jwt_timestamps(token)
        assert abs((expires_at.timestamp() - exp)) < 2

    def test_fallback_no_exp(self, auth):
        token = _fake_jwt()
        with patch("ecobee_auth_jwt._get_jwks_client", side_effect=Exception("no net")):
            expires_at, issued_at = auth._parse_jwt_timestamps(token)
        assert expires_at is not None
        assert (expires_at - datetime.now(timezone.utc)).total_seconds() > 0

    def test_fallback_bad_token(self, auth):
        with patch("ecobee_auth_jwt._get_jwks_client", side_effect=Exception("no net")):
            expires_at, issued_at = auth._parse_jwt_timestamps("not.a.token")
        assert expires_at is not None

    def test_jwks_pyjwt_error_falls_back(self, auth):
        import jwt as pyjwt
        now = datetime.now(timezone.utc)
        token = _fake_jwt(exp=int((now + timedelta(hours=1)).timestamp()))
        mock_client = MagicMock()
        mock_client.get_signing_key_from_jwt.return_value = MagicMock()
        with patch("ecobee_auth_jwt._get_jwks_client", return_value=mock_client):
            with patch("ecobee_auth_jwt.pyjwt.decode", side_effect=pyjwt.exceptions.DecodeError("bad")):
                expires_at, _ = auth._parse_jwt_timestamps(token)
        assert expires_at is not None


# ---------------------------------------------------------------------------
# _chromedriver_log_path
# ---------------------------------------------------------------------------

def test_chromedriver_log_path(auth):
    path = auth._chromedriver_log_path()
    assert path.endswith("chromedriver.log")


# ---------------------------------------------------------------------------
# save_token / load_token
# ---------------------------------------------------------------------------

class TestSaveLoadToken:
    def test_round_trip(self, auth):
        now = datetime.now(timezone.utc)
        auth.jwt_token = "fake.jwt.token"
        auth.token_expires_at = now + timedelta(hours=1)
        auth.last_refreshed = now
        auth.api_base_url = "https://api.ecobee.com/1"
        auth.save_token()

        auth2 = EcobeeAuthJWT("u", "p", config_file=auth.config_file)
        assert auth2.load_token() is True
        assert auth2.jwt_token == "fake.jwt.token"
        assert auth2.api_base_url == "https://api.ecobee.com/1"

    def test_save_with_none_fields(self, auth):
        auth.jwt_token = "tok"
        auth.token_expires_at = None
        auth.last_refreshed = None
        auth.api_base_url = None
        auth.save_token()
        assert os.path.exists(auth.config_file)

    def test_load_file_missing(self, tmp_path):
        a = EcobeeAuthJWT("u", "p", config_file=str(tmp_path / "nofile.json"))
        assert a.load_token() is False

    def test_load_invalid_json(self, auth):
        with open(auth.config_file, "w") as f:
            f.write("bad json {{")
        assert auth.load_token() is False

    def test_file_permissions_set(self, auth):
        auth.jwt_token = "tok"
        auth.save_token()
        mode = oct(os.stat(auth.config_file).st_mode)[-3:]
        assert mode == "600"


# ---------------------------------------------------------------------------
# needs_refresh / is_token_valid
# ---------------------------------------------------------------------------

class TestRefreshAndValid:
    def test_needs_refresh_no_expiry(self, auth):
        auth.token_expires_at = None
        assert auth.needs_refresh() is True

    def test_needs_refresh_near_expiry(self, auth):
        auth.token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=100)
        assert auth.needs_refresh() is True

    def test_needs_refresh_far_enough(self, auth):
        auth.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
        assert auth.needs_refresh() is False

    def test_is_valid_no_token(self, auth):
        assert auth.is_token_valid() is False

    def test_is_valid_no_expiry(self, auth):
        auth.jwt_token = "t"
        auth.token_expires_at = None
        assert auth.is_token_valid() is False

    def test_is_valid_expired(self, auth):
        auth.jwt_token = "t"
        auth.token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert auth.is_token_valid() is False

    def test_is_valid_ok(self, auth):
        auth.jwt_token = "t"
        auth.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        assert auth.is_token_valid() is True


# ---------------------------------------------------------------------------
# get_token_status
# ---------------------------------------------------------------------------

class TestGetTokenStatus:
    def test_no_token(self, auth):
        status = auth.get_token_status()
        assert status["valid"] is False
        assert status["token_present"] is False
        assert status["expires_at"] is None

    def test_valid_token(self, auth):
        auth.jwt_token = "t"
        auth.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        status = auth.get_token_status()
        assert status["valid"] is True
        assert status["token_present"] is True
        assert status["expires_in_minutes"] > 0
        assert status["needs_refresh"] is False


# ---------------------------------------------------------------------------
# get_token
# ---------------------------------------------------------------------------

class TestGetToken:
    def test_no_token_no_file(self, auth):
        with patch.object(auth, "load_token", return_value=False):
            assert auth.get_token() is None

    def test_valid_token_returned(self, auth):
        auth.jwt_token = "valid"
        auth.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        assert auth.get_token() == "valid"

    def test_refresh_fails(self, auth):
        auth.jwt_token = "old"
        auth.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        with patch.object(auth, "refresh_token", return_value=False):
            assert auth.get_token() is None

    def test_refresh_succeeds(self, auth):
        auth.jwt_token = "old"
        auth.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)

        def do_refresh():
            auth.jwt_token = "new"
            auth.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
            return True

        with patch.object(auth, "refresh_token", side_effect=do_refresh):
            assert auth.get_token() == "new"

    def test_no_memory_loads_from_file_then_valid(self, auth):
        auth.jwt_token = None

        def fake_load():
            auth.jwt_token = "loaded"
            auth.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
            return True

        with patch.object(auth, "load_token", side_effect=fake_load):
            assert auth.get_token() == "loaded"


# ---------------------------------------------------------------------------
# _parse_jwt_timestamps — verified decode (success) path
# ---------------------------------------------------------------------------

def test_parse_jwt_timestamps_verified_success(auth):
    now = datetime.now(timezone.utc)
    exp = int((now + timedelta(hours=1)).timestamp())
    token = _fake_jwt(exp=exp, iat=int(now.timestamp()))

    mock_key = MagicMock()
    mock_key.key = "fake_key"
    mock_client = MagicMock()
    mock_client.get_signing_key_from_jwt.return_value = mock_key

    with patch("ecobee_auth_jwt._get_jwks_client", return_value=mock_client):
        with patch("ecobee_auth_jwt.pyjwt.decode", return_value={"exp": exp, "iat": int(now.timestamp())}):
            expires_at, issued_at = auth._parse_jwt_timestamps(token)

    assert abs(expires_at.timestamp() - exp) < 2


# ---------------------------------------------------------------------------
# _init_driver
# ---------------------------------------------------------------------------

class TestInitDriver:
    def test_already_initialized_returns_early(self, auth):
        orig = MagicMock()
        auth.driver = orig
        auth._init_driver()
        assert auth.driver is orig

    def test_no_binaries_headless(self, auth):
        mock_driver = MagicMock()
        with patch("ecobee_auth_jwt.webdriver.Chrome", return_value=mock_driver):
            with patch("ecobee_auth_jwt.os.path.exists", return_value=False):
                with patch("ecobee_auth_jwt.Service"):
                    auth._init_driver(headless=True)
        assert auth.driver is mock_driver

    def test_no_binaries_non_headless(self, auth):
        mock_driver = MagicMock()
        with patch("ecobee_auth_jwt.webdriver.Chrome", return_value=mock_driver):
            with patch("ecobee_auth_jwt.os.path.exists", return_value=False):
                with patch("ecobee_auth_jwt.Service"):
                    auth._init_driver(headless=False)
        assert auth.driver is mock_driver

    def test_all_binaries_present(self, auth):
        mock_driver = MagicMock()
        with patch("ecobee_auth_jwt.webdriver.Chrome", return_value=mock_driver):
            with patch("ecobee_auth_jwt.os.path.exists", return_value=True):
                with patch("ecobee_auth_jwt.shutil.rmtree"):
                    with patch("ecobee_auth_jwt.Service"):
                        auth._init_driver()
        assert auth.driver is mock_driver

    def test_chrome_exception_log_readable(self, auth):
        from selenium.common.exceptions import WebDriverException
        from unittest.mock import mock_open

        with patch("ecobee_auth_jwt.webdriver.Chrome", side_effect=WebDriverException("crash")):
            with patch("ecobee_auth_jwt.os.path.exists", return_value=False):
                with patch("ecobee_auth_jwt.Service"):
                    with patch("builtins.open", mock_open(read_data="log content")):
                        with pytest.raises(WebDriverException):
                            auth._init_driver()

    def test_chrome_exception_log_unreadable(self, auth):
        from selenium.common.exceptions import WebDriverException

        with patch("ecobee_auth_jwt.webdriver.Chrome", side_effect=WebDriverException("crash")):
            with patch("ecobee_auth_jwt.os.path.exists", return_value=False):
                with patch("ecobee_auth_jwt.Service"):
                    with patch("builtins.open", side_effect=Exception("no log")):
                        with pytest.raises(WebDriverException):
                            auth._init_driver()


# ---------------------------------------------------------------------------
# _close_driver
# ---------------------------------------------------------------------------

class TestCloseDriver:
    def test_success(self, auth):
        auth.driver = MagicMock()
        auth._close_driver()
        assert auth.driver is None

    def test_no_driver(self, auth):
        auth.driver = None
        auth._close_driver()  # no crash

    def test_quit_exception(self, auth):
        mock_d = MagicMock()
        mock_d.quit.side_effect = Exception("quit failed")
        auth.driver = mock_d
        auth._close_driver()  # no crash, just warning


# ---------------------------------------------------------------------------
# _fill_input_field / _click_submit_button
# ---------------------------------------------------------------------------

def test_fill_input_field(auth):
    auth.driver = MagicMock()
    auth._fill_input_field(MagicMock(), "value@test.com")
    auth.driver.execute_script.assert_called_once()


def test_click_submit_button(auth):
    auth.driver = MagicMock()
    mock_wait = MagicMock()
    mock_btn = MagicMock()
    mock_wait.until.return_value = mock_btn
    auth._click_submit_button(mock_wait, "Submit")
    auth.driver.execute_script.assert_called_with("arguments[0].click();", mock_btn)


# ---------------------------------------------------------------------------
# login_and_extract_token
# ---------------------------------------------------------------------------

class TestLoginAndExtractToken:
    def _mock_driver_with_token(self, token_val):
        mock_d = MagicMock()
        mock_d.get_cookies.return_value = [{"name": "_TOKEN", "value": token_val}]
        mock_d.current_url = "https://www.ecobee.com/home"
        return mock_d

    def test_success(self, auth):
        now = datetime.now(timezone.utc)
        token_val = _fake_jwt(exp=int((now + timedelta(hours=1)).timestamp()))
        mock_d = self._mock_driver_with_token(token_val)
        mock_wait = MagicMock()
        mock_wait.until.return_value = MagicMock()

        def _set_driver(headless=True):
            auth.driver = mock_d

        with patch.object(auth, "_init_driver", side_effect=_set_driver):
            with patch("ecobee_auth_jwt.WebDriverWait", return_value=mock_wait):
                with patch("ecobee_auth_jwt.time.sleep"):
                    with patch.object(auth, "_parse_jwt_timestamps", return_value=(now + timedelta(hours=1), now)):
                        with patch.object(auth, "_capture_api_context_from_logs"):
                            with patch.object(auth, "save_token"):
                                result = auth.login_and_extract_token()
        assert result is True

    def test_no_token_cookie(self, auth):
        mock_d = MagicMock()
        mock_d.get_cookies.return_value = [{"name": "other", "value": "x"}]
        mock_d.current_url = "https://www.ecobee.com/home"
        mock_wait = MagicMock()
        mock_wait.until.return_value = MagicMock()

        def _set_driver(headless=True):
            auth.driver = mock_d

        with patch.object(auth, "_init_driver", side_effect=_set_driver):
            with patch("ecobee_auth_jwt.WebDriverWait", return_value=mock_wait):
                with patch("ecobee_auth_jwt.time.sleep"):
                    result = auth.login_and_extract_token()
        assert result is False

    def test_timeout_exception_with_driver(self, auth):
        from selenium.common.exceptions import TimeoutException
        mock_d = MagicMock()
        mock_d.current_url = "https://auth.ecobee.com/login"
        mock_d.title = "Login"
        mock_d.get_cookies.return_value = []

        def _set_driver(headless=True):
            auth.driver = mock_d

        with patch.object(auth, "_init_driver", side_effect=_set_driver):
            with patch("ecobee_auth_jwt.WebDriverWait", side_effect=TimeoutException()):
                with patch("ecobee_auth_jwt.time.sleep"):
                    result = auth.login_and_extract_token()
        assert result is False

    def test_timeout_exception_no_driver(self, auth):
        from selenium.common.exceptions import TimeoutException

        def _set_driver(headless=True):
            pass  # don't set auth.driver

        with patch.object(auth, "_init_driver", side_effect=_set_driver):
            with patch("ecobee_auth_jwt.WebDriverWait", side_effect=TimeoutException()):
                result = auth.login_and_extract_token()
        assert result is False

    def test_generic_exception(self, auth):
        def _raise(headless=True):
            auth.driver = MagicMock()
            raise Exception("something broke")

        with patch.object(auth, "_init_driver", side_effect=_raise):
            result = auth.login_and_extract_token()
        assert result is False

    def test_generic_exception_no_driver(self, auth):
        with patch.object(auth, "_init_driver", side_effect=Exception("no browser")):
            result = auth.login_and_extract_token()
        assert result is False

    def test_continue_button_exception_propagates(self, auth):
        mock_d = MagicMock()
        mock_d.current_url = "https://auth.ecobee.com"

        def _set_driver(headless=True):
            auth.driver = mock_d

        mock_wait = MagicMock()
        # First until returns email field; second until (for continue button) raises
        call_count = [0]

        def wait_side_effect(condition):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock()  # email field
            raise Exception("no button found")

        mock_wait.until.side_effect = wait_side_effect

        with patch.object(auth, "_init_driver", side_effect=_set_driver):
            with patch("ecobee_auth_jwt.WebDriverWait", return_value=mock_wait):
                with patch("ecobee_auth_jwt.time.sleep"):
                    result = auth.login_and_extract_token()
        assert result is False

    def test_login_button_exception_propagates(self, auth):
        # wait.until is called 4 times: email field, continue btn, password field, login btn
        # Raise on 4th call to cover the "Failed to click Login button" exception handler (lines 265-267)
        mock_d = MagicMock()
        mock_d.current_url = "https://auth.ecobee.com"

        def _set_driver(headless=True):
            auth.driver = mock_d

        mock_wait = MagicMock()
        call_count = [0]

        def wait_side_effect(condition):
            call_count[0] += 1
            if call_count[0] <= 3:
                return MagicMock()  # email field, continue btn, password field
            raise Exception("login button gone")  # 4th call = login button

        mock_wait.until.side_effect = wait_side_effect

        with patch.object(auth, "_init_driver", side_effect=_set_driver):
            with patch("ecobee_auth_jwt.WebDriverWait", return_value=mock_wait):
                with patch("ecobee_auth_jwt.time.sleep"):
                    result = auth.login_and_extract_token()
        assert result is False


# ---------------------------------------------------------------------------
# _capture_api_context_from_logs
# ---------------------------------------------------------------------------

class TestCaptureApiContextFromLogs:
    def _make_log_entry(self, url, headers=None):
        import json as jsonlib
        msg = {
            "message": {
                "method": "Network.requestWillBeSent",
                "params": {
                    "request": {"url": url, "headers": headers or {}}
                }
            }
        }
        return {"message": jsonlib.dumps(msg)}

    def test_bearer_in_network_log(self, auth):
        import json as jsonlib
        now = datetime.now(timezone.utc)
        token_val = _fake_jwt(exp=int((now + timedelta(hours=1)).timestamp()))
        mock_d = MagicMock()
        mock_d.execute_script.return_value = "{}"
        mock_d.get_log.return_value = [
            self._make_log_entry(
                "https://prod.ecobee.com/api/v1/thermostat",
                {"authorization": f"Bearer {token_val}"},
            )
        ]
        auth.driver = mock_d
        with patch.object(auth, "_parse_jwt_timestamps", return_value=(now + timedelta(hours=1), now)):
            auth._capture_api_context_from_logs()
        assert auth.api_base_url == "https://prod.ecobee.com/api/v1"

    def test_bearer_no_url_match(self, auth):
        mock_d = MagicMock()
        mock_d.execute_script.return_value = "{}"
        mock_d.get_log.return_value = [
            self._make_log_entry(
                "https://www.ecobee.com/random/path",
                {"authorization": "Bearer sometoken"},
            )
        ]
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # no crash

    def test_ecobee_url_no_bearer(self, auth):
        mock_d = MagicMock()
        mock_d.execute_script.return_value = "{}"
        mock_d.get_log.return_value = [
            self._make_log_entry("https://www.ecobee.com/api/resource")
        ]
        auth.driver = mock_d
        auth._capture_api_context_from_logs()

    def test_jwt_in_localstorage(self, auth):
        import json as jsonlib
        now = datetime.now(timezone.utc)
        token_val = _fake_jwt(exp=int((now + timedelta(hours=1)).timestamp()))
        mock_d = MagicMock()
        mock_d.execute_script.return_value = jsonlib.dumps({"authKey": token_val})
        mock_d.get_log.return_value = []
        auth.driver = mock_d
        with patch.object(auth, "_parse_jwt_timestamps", return_value=(now + timedelta(hours=1), now)):
            auth._capture_api_context_from_logs()
        assert auth.api_base_url == "https://prod.ecobee.com/api/v1"

    def test_access_token_in_localstorage_blob(self, auth):
        import json as jsonlib
        now = datetime.now(timezone.utc)
        token_val = _fake_jwt(exp=int((now + timedelta(hours=1)).timestamp()))
        blob = jsonlib.dumps({"access_token": token_val})
        mock_d = MagicMock()
        mock_d.execute_script.return_value = jsonlib.dumps({"state": blob})
        mock_d.get_log.return_value = []
        auth.driver = mock_d
        with patch.object(auth, "_parse_jwt_timestamps", return_value=(now + timedelta(hours=1), now)):
            auth._capture_api_context_from_logs()
        assert auth.jwt_token == token_val

    def test_access_token_in_body_key(self, auth):
        import json as jsonlib
        now = datetime.now(timezone.utc)
        token_val = _fake_jwt(exp=int((now + timedelta(hours=1)).timestamp()))
        blob = jsonlib.dumps({"body": {"access_token": token_val}})
        mock_d = MagicMock()
        mock_d.execute_script.return_value = jsonlib.dumps({"state": blob})
        mock_d.get_log.return_value = []
        auth.driver = mock_d
        with patch.object(auth, "_parse_jwt_timestamps", return_value=(now + timedelta(hours=1), now)):
            auth._capture_api_context_from_logs()
        assert auth.jwt_token == token_val

    def test_localstorage_read_exception(self, auth):
        mock_d = MagicMock()
        mock_d.execute_script.side_effect = Exception("no localstorage")
        mock_d.get_log.return_value = []
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # no crash

    def test_performance_log_read_exception(self, auth):
        mock_d = MagicMock()
        mock_d.execute_script.return_value = "{}"
        mock_d.get_log.side_effect = Exception("no logs")
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # no crash

    def test_malformed_log_entry(self, auth):
        mock_d = MagicMock()
        mock_d.execute_script.return_value = "{}"
        mock_d.get_log.return_value = [{"message": "not-valid-json{"}]
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # no crash

    def test_no_ecobee_requests_and_no_token(self, auth):
        mock_d = MagicMock()
        mock_d.execute_script.return_value = "{}"
        mock_d.get_log.return_value = []  # no logs at all
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # logs warning, no crash

    def test_localstorage_null(self, auth):
        mock_d = MagicMock()
        mock_d.execute_script.return_value = None  # null from JS
        mock_d.get_log.return_value = []
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # no crash

    def test_localstorage_non_string_value(self, auth):
        import json as jsonlib
        mock_d = MagicMock()
        mock_d.execute_script.return_value = jsonlib.dumps({"key": 42})
        mock_d.get_log.return_value = []
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # non-string value is skipped

    def test_localstorage_access_token_invalid_json(self, auth):
        import json as jsonlib
        # Value contains 'access_token' but is not parseable JSON (line 349-350)
        mock_d = MagicMock()
        mock_d.execute_script.return_value = jsonlib.dumps({"key": "access_token not_valid_json {"})
        mock_d.get_log.return_value = []
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # json.loads raises, caught by except Exception: pass

    def test_log_entry_non_network_event(self, auth):
        import json as jsonlib
        # Line 366: method != 'Network.requestWillBeSent' → continue
        mock_d = MagicMock()
        mock_d.execute_script.return_value = "{}"
        log_msg = jsonlib.dumps({
            "message": {
                "method": "Page.loadEventFired",
                "params": {}
            }
        })
        mock_d.get_log.return_value = [{"message": log_msg}]
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # skips non-network event

    def test_log_entry_non_ecobee_url(self, auth):
        import json as jsonlib
        # Line 373: 'ecobee.com' not in url → continue
        mock_d = MagicMock()
        mock_d.execute_script.return_value = "{}"
        log_msg = jsonlib.dumps({
            "message": {
                "method": "Network.requestWillBeSent",
                "params": {
                    "request": {
                        "url": "https://fonts.googleapis.com/css",
                        "headers": {"authorization": "Bearer tok"}
                    }
                }
            }
        })
        mock_d.get_log.return_value = [{"message": log_msg}]
        auth.driver = mock_d
        auth._capture_api_context_from_logs()  # skips non-ecobee URL


# ---------------------------------------------------------------------------
# save_token — error path
# ---------------------------------------------------------------------------

def test_save_token_write_error(auth):
    auth.jwt_token = "tok"
    auth.token_expires_at = datetime.now(timezone.utc)
    auth.last_refreshed = None
    auth.api_base_url = None
    with patch("ecobee_auth_jwt.Path.write_text", side_effect=PermissionError("denied")):
        auth.save_token()  # should not raise


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    def test_success_first_attempt(self, auth):
        with patch.object(auth, "login_and_extract_token", return_value=True):
            assert auth.refresh_token(max_retries=1) is True

    def test_fails_all_attempts(self, auth):
        with patch.object(auth, "login_and_extract_token", return_value=False):
            with patch("ecobee_auth_jwt.time.sleep"):
                assert auth.refresh_token(max_retries=2) is False

    def test_exception_then_success(self, auth):
        results = [Exception("err"), True]
        idx = [0]

        def side_effect(headless=True):
            r = results[idx[0]]
            idx[0] += 1
            if isinstance(r, Exception):
                raise r
            return r

        with patch.object(auth, "login_and_extract_token", side_effect=side_effect):
            with patch("ecobee_auth_jwt.time.sleep"):
                assert auth.refresh_token(max_retries=2) is True

    def test_all_exceptions(self, auth):
        with patch.object(auth, "login_and_extract_token", side_effect=Exception("err")):
            with patch("ecobee_auth_jwt.time.sleep"):
                assert auth.refresh_token(max_retries=2) is False
