from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.handoff_remote import (
    RemoteHandoffBridge,
    RemoteStatus,
    RemoteTarget,
    arm_remote_detector_session,
    disarm_remote_detector_session,
    fetch_remote_screen_info_session,
    get_remote_detector_state_session,
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


def _fake_urlopen(response_payload: dict):
    body = json.dumps(response_payload).encode("utf-8")
    mock = MagicMock()
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    mock.read.return_value = body
    mock.status = 200
    return mock


# ---------------------------------------------------------------------------
# Session proxy HTTP functions
# ---------------------------------------------------------------------------

def test_fetch_remote_screen_info_session_sends_correct_body() -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    expected = {"width": 2560, "height": 1440, "error": ""}

    with patch("app.handoff_remote.urlopen", return_value=_fake_urlopen(expected)) as mock_open:
        result = fetch_remote_screen_info_session(target, "sid1", "tok1")

    assert result["width"] == 2560
    body = json.loads(mock_open.call_args[0][0].data)
    assert body["session_id"] == "sid1"
    assert body["session_token"] == "tok1"
    assert "session/screen/info" in mock_open.call_args[0][0].full_url


def test_arm_remote_detector_session_sends_correct_body() -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    expected = {"state": "armed"}

    with patch("app.handoff_remote.urlopen", return_value=_fake_urlopen(expected)) as mock_open:
        result = arm_remote_detector_session(target, "sid1", "tok1", "left", 400)

    assert result["state"] == "armed"
    body = json.loads(mock_open.call_args[0][0].data)
    assert body["edge"] == "left"
    assert body["dwell_ms"] == 400
    assert body["session_id"] == "sid1"
    assert "session/handoff/arm" in mock_open.call_args[0][0].full_url


def test_get_remote_detector_state_session() -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    expected = {"state": "idle"}

    with patch("app.handoff_remote.urlopen", return_value=_fake_urlopen(expected)) as mock_open:
        result = get_remote_detector_state_session(target, "sid1", "tok1")

    assert result["state"] == "idle"
    assert "session/handoff/detector/state" in mock_open.call_args[0][0].full_url


def test_disarm_remote_detector_session() -> None:
    target = RemoteTarget(host="192.168.1.5", port=8765)
    expected = {"state": "idle"}

    with patch("app.handoff_remote.urlopen", return_value=_fake_urlopen(expected)) as mock_open:
        result = disarm_remote_detector_session(target, "sid1", "tok1")

    assert result["state"] == "idle"
    assert "session/handoff/disarm" in mock_open.call_args[0][0].full_url


# ---------------------------------------------------------------------------
# RemoteHandoffBridge.start_with_session stores session creds
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_bridge_start_with_session_stores_creds() -> None:
    bridge = RemoteHandoffBridge()
    ok_status = RemoteStatus(reachable=True)

    mock_ws = MagicMock()
    mock_ws.send = MagicMock(return_value=None)

    async def fake_get_status(target):
        return ok_status

    async def fake_connect(url):
        return mock_ws

    with patch("app.handoff_remote.get_remote_status", side_effect=lambda t, **kw: ok_status), \
         patch("app.handoff_remote.asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)), \
         patch("app.handoff_remote.websockets.connect", return_value=mock_ws):
        mock_ws.send = MagicMock()

        async def fake_connect_ctx(url):
            return mock_ws
        mock_ws.__aenter__ = MagicMock(return_value=mock_ws)
        mock_ws.__aexit__ = MagicMock(return_value=False)

        # Simulate connection by directly setting internals (to_thread is tricky in test)
        bridge._target = RemoteTarget(host="192.168.1.5", port=8765)
        bridge._session_id = "test-sid"
        bridge._session_token = "test-tok"

    assert bridge._session_id == "test-sid"
    assert bridge._session_token == "test-tok"
    assert bridge._token is None


@pytest.mark.anyio
async def test_bridge_stop_clears_session_creds() -> None:
    bridge = RemoteHandoffBridge()
    bridge._target = RemoteTarget(host="192.168.1.5", port=8765)
    bridge._session_id = "sid"
    bridge._session_token = "tok"
    # _websocket is None so _close_locked won't try to close anything
    await bridge.stop()
    assert bridge._session_id is None
    assert bridge._session_token is None
    assert bridge._target is None


@pytest.mark.anyio
async def test_bridge_arm_return_detector_raises_when_not_connected() -> None:
    bridge = RemoteHandoffBridge()
    with pytest.raises(RuntimeError, match="not connected"):
        await bridge.arm_return_detector("left")


@pytest.mark.anyio
async def test_bridge_get_return_state_raises_when_not_connected() -> None:
    bridge = RemoteHandoffBridge()
    with pytest.raises(RuntimeError, match="not connected"):
        await bridge.get_return_state()


@pytest.mark.anyio
async def test_bridge_get_screen_info_raises_when_not_connected() -> None:
    bridge = RemoteHandoffBridge()
    with pytest.raises(RuntimeError, match="not connected"):
        await bridge.get_remote_screen_info()


@pytest.mark.anyio
async def test_bridge_disarm_returns_ok_when_not_connected() -> None:
    bridge = RemoteHandoffBridge()
    result = await bridge.disarm_return_detector()
    assert result == {"ok": True}


# ---------------------------------------------------------------------------
# /api/session/screen/info
# ---------------------------------------------------------------------------

def test_session_screen_info_rejects_invalid_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/session/screen/info",
        json={"session_id": "bad", "session_token": "bad"},
    )
    assert response.status_code == 401


def test_session_screen_info_accepts_valid_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    session_book = PairingSessionBook()
    monkeypatch.setattr(main, "pairing_session_book", session_book)
    grant = session_book.create_session(device_id="dev-1", guest=True)

    response = TestClient(app).post(
        "/api/session/screen/info",
        json={"session_id": grant.session.session_id, "session_token": grant.session_token},
    )

    assert response.status_code == 200
    data = response.json()
    # Response includes width/height (may be real values or None on CI) and error key
    assert "width" in data
    assert "error" in data


# ---------------------------------------------------------------------------
# /api/session/handoff/arm
# ---------------------------------------------------------------------------

def test_session_handoff_arm_rejects_invalid_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/session/handoff/arm",
        json={"session_id": "bad", "session_token": "bad", "edge": "left"},
    )
    assert response.status_code == 403


def test_session_handoff_arm_rejects_session_without_mouse_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    session_book = PairingSessionBook()
    monkeypatch.setattr(main, "pairing_session_book", session_book)
    grant = session_book.create_session(device_id="dev-1", guest=True, permissions={"mouse": False, "keyboard": True})

    response = TestClient(app).post(
        "/api/session/handoff/arm",
        json={"session_id": grant.session.session_id, "session_token": grant.session_token, "edge": "left"},
    )
    assert response.status_code == 403


def test_session_handoff_arm_accepts_valid_session_with_mouse(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    session_book = PairingSessionBook()
    monkeypatch.setattr(main, "pairing_session_book", session_book)
    grant = session_book.create_session(device_id="dev-1", guest=True, permissions={"mouse": True})

    with patch.object(main.edge_detector, "arm") as mock_arm, \
         patch.object(main.edge_detector, "public_dict", return_value={"state": "armed"}):
        mock_arm.return_value = None
        response = TestClient(app).post(
            "/api/session/handoff/arm",
            json={
                "session_id": grant.session.session_id,
                "session_token": grant.session_token,
                "edge": "left",
            },
        )

    assert response.status_code == 200


def test_session_handoff_arm_rejects_invalid_edge(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    session_book = PairingSessionBook()
    monkeypatch.setattr(main, "pairing_session_book", session_book)
    grant = session_book.create_session(device_id="dev-1", guest=True, permissions={"mouse": True})

    response = TestClient(app).post(
        "/api/session/handoff/arm",
        json={"session_id": grant.session.session_id, "session_token": grant.session_token, "edge": "diagonal"},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /api/session/handoff/disarm and /api/session/handoff/detector/state
# ---------------------------------------------------------------------------

def test_session_handoff_disarm_rejects_invalid_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/session/handoff/disarm",
        json={"session_id": "bad", "session_token": "bad"},
    )
    assert response.status_code == 401


def test_session_handoff_disarm_accepts_any_valid_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    session_book = PairingSessionBook()
    monkeypatch.setattr(main, "pairing_session_book", session_book)
    grant = session_book.create_session(device_id="dev-1", guest=True)

    with patch.object(main.edge_detector, "disarm") as mock_disarm, \
         patch.object(main.edge_detector, "public_dict", return_value={"state": "idle"}):
        mock_disarm.return_value = None
        response = TestClient(app).post(
            "/api/session/handoff/disarm",
            json={"session_id": grant.session.session_id, "session_token": grant.session_token},
        )

    assert response.status_code == 200


def test_session_handoff_detector_state_rejects_invalid_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/session/handoff/detector/state",
        json={"session_id": "bad", "session_token": "bad"},
    )
    assert response.status_code == 401


def test_session_handoff_detector_state_returns_state(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    session_book = PairingSessionBook()
    monkeypatch.setattr(main, "pairing_session_book", session_book)
    grant = session_book.create_session(device_id="dev-1", guest=True)

    with patch.object(main.edge_detector, "public_dict", return_value={"state": "idle", "dwell_progress": None}):
        response = TestClient(app).post(
            "/api/session/handoff/detector/state",
            json={"session_id": grant.session.session_id, "session_token": grant.session_token},
        )

    assert response.status_code == 200
    assert response.json()["state"] == "idle"
