from __future__ import annotations

import io
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.discovery import PendingPairRequest
from app.handoff_remote import (
    RemoteTarget,
    remote_pair,
    remote_request_pair,
    remote_trusted_reconnect,
)
from app.main import app
from app.pairing import PairingCodeBook, PairingSessionBook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _use_temp_config(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "TOKEN_FILE", config_dir / "pairing_token.txt")
    monkeypatch.setattr(config, "TRUSTED_DEVICES_FILE", config_dir / "trusted_devices.json")
    monkeypatch.setattr(config, "DEVICE_IDENTITY_FILE", config_dir / "device_identity.json")
    monkeypatch.setattr(config, "RECEIVE_DIR_FILE", config_dir / "receive_dir.txt")
    monkeypatch.setattr(main, "pairing_code_book", PairingCodeBook())
    monkeypatch.setattr(main, "pairing_session_book", PairingSessionBook())
    monkeypatch.setattr(main, "_pending_pair_request", None)
    main.lockout_state.set_disabled(False)
    main.transfer_history.clear()


def _fake_urlopen(response_payload: dict, status: int = 200):
    body = json.dumps(response_payload).encode("utf-8")
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.read.return_value = body
    mock.status = status
    return mock


def _fake_http_error(status: int, detail: str):
    body = json.dumps({"detail": detail}).encode("utf-8")
    exc = HTTPError(url="http://x", code=status, msg="err", hdrs={}, fp=io.BytesIO(body))
    return exc


# ---------------------------------------------------------------------------
# remote_request_pair
# ---------------------------------------------------------------------------

def test_remote_request_pair_success(monkeypatch) -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    response = {"ok": True, "expires_in": 45}

    with patch("app.handoff_remote.urlopen", return_value=_fake_urlopen(response)) as mock_open:
        result = remote_request_pair(target, "my-device", "My Mac")

    assert result["ok"] is True
    assert result["expires_in"] == 45
    call_args = mock_open.call_args[0][0]
    assert "request-pair" in call_args.full_url
    body = json.loads(call_args.data)
    assert body["device_id"] == "my-device"
    assert body["name"] == "My Mac"


def test_remote_request_pair_raises_on_http_error(monkeypatch) -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    with patch("app.handoff_remote.urlopen", side_effect=_fake_http_error(429, "already pending")):
        with pytest.raises(RuntimeError, match="already pending"):
            remote_request_pair(target, "dev", "Mac")


# ---------------------------------------------------------------------------
# remote_pair
# ---------------------------------------------------------------------------

def test_remote_pair_success(monkeypatch) -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    response = {
        "ok": True, "trusted": True, "guest": False,
        "shared_secret": "abc123",
        "session_token": "tok",
        "session": {"session_id": "sid"},
    }

    with patch("app.handoff_remote.urlopen", return_value=_fake_urlopen(response)) as mock_open:
        result = remote_pair(
            target, code="123456", device_id="dev-1", name="Mac A",
            permissions={"mouse": True}, remember_device=True,
        )

    assert result["ok"] is True
    assert result["shared_secret"] == "abc123"
    body = json.loads(mock_open.call_args[0][0].data)
    assert body["code"] == "123456"
    assert body["remember_device"] is True


def test_remote_pair_raises_on_bad_pin(monkeypatch) -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    with patch("app.handoff_remote.urlopen", side_effect=_fake_http_error(400, "Invalid or expired pairing code.")):
        with pytest.raises(RuntimeError, match="Invalid or expired pairing code."):
            remote_pair(target, "000000", "dev", "Mac", {}, False)


# ---------------------------------------------------------------------------
# remote_trusted_reconnect
# ---------------------------------------------------------------------------

def test_remote_trusted_reconnect_success(monkeypatch) -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    response = {
        "ok": True,
        "session_token": "new-tok",
        "session": {"session_id": "new-sid"},
        "device": {"device_id": "dev-1"},
    }

    with patch("app.handoff_remote.urlopen", return_value=_fake_urlopen(response)) as mock_open:
        result = remote_trusted_reconnect(target, "dev-1", "secret-abc")

    assert result["ok"] is True
    body = json.loads(mock_open.call_args[0][0].data)
    assert body["device_id"] == "dev-1"
    assert body["shared_secret"] == "secret-abc"


def test_remote_trusted_reconnect_raises_on_wrong_secret(monkeypatch) -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    with patch("app.handoff_remote.urlopen", side_effect=_fake_http_error(401, "Unknown device or invalid credentials.")):
        with pytest.raises(RuntimeError, match="Unknown device"):
            remote_trusted_reconnect(target, "dev-1", "wrong")


# ---------------------------------------------------------------------------
# /api/discovery/remote/request-pair (proxy route)
# ---------------------------------------------------------------------------

def test_proxy_request_pair_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post("/api/discovery/remote/request-pair", json={"host": "1.2.3.4"})
    assert response.status_code == 401


def test_proxy_request_pair_calls_remote(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    remote_response = {"ok": True, "expires_in": 45}

    with patch("app.main.remote_request_pair", return_value=remote_response) as mock_fn:
        response = TestClient(app).post(
            "/api/discovery/remote/request-pair",
            json={"host": "192.168.1.5", "port": 8765},
            headers={"X-Pairing-Token": token},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    mock_fn.assert_called_once()
    _, called_device_id, called_name = mock_fn.call_args[0]
    assert isinstance(called_device_id, str) and len(called_device_id) > 0
    assert isinstance(called_name, str) and len(called_name) > 0


def test_proxy_request_pair_propagates_runtime_error(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    with patch("app.main.remote_request_pair", side_effect=RuntimeError("already pending")):
        response = TestClient(app).post(
            "/api/discovery/remote/request-pair",
            json={"host": "192.168.1.5"},
            headers={"X-Pairing-Token": token},
        )

    assert response.status_code == 400
    assert "already pending" in response.json()["detail"]


# ---------------------------------------------------------------------------
# /api/discovery/remote/pair (proxy route)
# ---------------------------------------------------------------------------

def test_proxy_pair_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/discovery/remote/pair",
        json={"host": "1.2.3.4", "code": "123456"},
    )
    assert response.status_code == 401


def test_proxy_pair_calls_remote(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    remote_response = {
        "ok": True, "trusted": True, "shared_secret": "sec",
        "session_token": "tok", "session": {"session_id": "sid"},
    }

    with patch("app.main.remote_pair", return_value=remote_response) as mock_fn:
        response = TestClient(app).post(
            "/api/discovery/remote/pair",
            json={"host": "192.168.1.5", "port": 8765, "code": "123456", "remember_device": True},
            headers={"X-Pairing-Token": token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["shared_secret"] == "sec"
    mock_fn.assert_called_once()
    # code and remember_device are passed through
    call_args = mock_fn.call_args[0]
    assert call_args[1] == "123456"  # code is 2nd positional arg after target


def test_proxy_pair_propagates_bad_pin(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    with patch("app.main.remote_pair", side_effect=RuntimeError("Invalid or expired pairing code.")):
        response = TestClient(app).post(
            "/api/discovery/remote/pair",
            json={"host": "192.168.1.5", "code": "000000"},
            headers={"X-Pairing-Token": token},
        )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /api/discovery/remote/reconnect (proxy route)
# ---------------------------------------------------------------------------

def test_proxy_reconnect_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/discovery/remote/reconnect",
        json={"host": "1.2.3.4", "shared_secret": "x"},
    )
    assert response.status_code == 401


def test_proxy_reconnect_calls_remote(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    remote_response = {
        "ok": True, "session_token": "tok",
        "session": {"session_id": "sid"}, "device": {"device_id": "d1"},
    }

    with patch("app.main.remote_trusted_reconnect", return_value=remote_response) as mock_fn:
        response = TestClient(app).post(
            "/api/discovery/remote/reconnect",
            json={"host": "192.168.1.5", "port": 8765, "shared_secret": "abc123"},
            headers={"X-Pairing-Token": token},
        )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    mock_fn.assert_called_once()


def test_proxy_reconnect_propagates_invalid_secret(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    with patch("app.main.remote_trusted_reconnect", side_effect=RuntimeError("Unknown device or invalid credentials.")):
        response = TestClient(app).post(
            "/api/discovery/remote/reconnect",
            json={"host": "192.168.1.5", "shared_secret": "bad"},
            headers={"X-Pairing-Token": token},
        )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /api/handoff/remote/start-session (proxy route)
# ---------------------------------------------------------------------------

def test_start_session_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/handoff/remote/start-session",
        json={"host": "1.2.3.4", "session_id": "s", "session_token": "t"},
    )
    assert response.status_code == 401


def test_start_session_returns_ok_on_success(tmp_path, monkeypatch) -> None:
    from app.handoff_remote import RemoteStatus
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    ok_status = RemoteStatus(reachable=True)

    with patch.object(main.remote_handoff_bridge, "start_with_session", return_value=ok_status):
        response = TestClient(app).post(
            "/api/handoff/remote/start-session",
            json={"host": "192.168.1.5", "session_id": "sid", "session_token": "tok"},
            headers={"X-Pairing-Token": token},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["connected"] is True


def test_start_session_returns_400_if_unreachable(tmp_path, monkeypatch) -> None:
    from app.handoff_remote import RemoteStatus
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    unreachable = RemoteStatus(reachable=False, error="timeout")

    with patch.object(main.remote_handoff_bridge, "start_with_session", return_value=unreachable):
        response = TestClient(app).post(
            "/api/handoff/remote/start-session",
            json={"host": "192.168.1.5", "session_id": "sid", "session_token": "tok"},
            headers={"X-Pairing-Token": token},
        )

    assert response.status_code == 400
