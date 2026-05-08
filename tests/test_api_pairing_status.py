from __future__ import annotations

import io
from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from starlette.websockets import WebSocketDisconnect

from app import config
from app import files as files_module
from app.main import app
from app import main
from app import websocket as websocket_module
from app.handoff_remote import RemoteStatus
from app.pairing import PairingCodeBook, PairingSessionBook, TrustedDeviceStore


def test_device_endpoint_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/device")

    assert response.status_code == 401


def test_status_endpoint_reports_upload_limit(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/status")

    assert response.status_code == 200
    assert response.json()["max_upload_bytes"] == config.settings.max_upload_bytes


def test_status_endpoint_reports_protocol_capabilities(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/status")

    assert response.status_code == 200
    protocol = response.json()["protocol"]
    assert protocol["version"] == 1
    assert protocol["input_events"] == ["mouse_move", "mouse_button", "scroll", "ping"]
    assert protocol["legacy_aliases"] == ["move", "click"]
    assert protocol["envelope"] == "v1-payload"
    assert protocol["limits"] == {
        "max_mouse_delta": 5000,
        "max_scroll_amount": 1000,
    }


def test_input_status_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/input/status")

    assert response.status_code == 401


def test_input_status_reports_receiver_state(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    main.lockout_state.set_disabled(False)

    response = TestClient(app).get(
        "/api/input/status",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["backend"] == "pyautogui"
    assert data["disabled"] is False
    assert data["input_allowed"] is True
    assert data["screen_recording_required"] is False
    assert data["error"] == ""


def test_input_status_reports_lockout_disabled(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    main.lockout_state.set_disabled(True)

    response = TestClient(app).get(
        "/api/input/status",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["disabled"] is True
    assert response.json()["input_allowed"] is False


def test_file_transfer_page_serves_static_window(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/file-transfer")

    assert response.status_code == 200
    assert "File Transfers" in response.text


def test_file_transfer_settings_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/file-transfer/settings")

    assert response.status_code == 401


def test_file_transfer_settings_returns_default_receive_dir(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).get(
        "/api/file-transfer/settings",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["receive_dir"] == str(config.default_receive_dir())
    assert response.json()["max_upload_bytes"] == config.settings.max_upload_bytes


def test_update_file_transfer_settings_sets_receive_dir(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    receive_dir = tmp_path / "Received Files"

    response = TestClient(app).patch(
        "/api/file-transfer/settings",
        headers={"X-Pairing-Token": token},
        json={"path": str(receive_dir)},
    )

    assert response.status_code == 200
    assert response.json()["receive_dir"] == str(receive_dir)
    assert receive_dir.is_dir()
    assert config.get_receive_dir() == receive_dir


def test_update_file_transfer_settings_rejects_relative_dir(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).patch(
        "/api/file-transfer/settings",
        headers={"X-Pairing-Token": token},
        json={"path": "relative-folder"},
    )

    assert response.status_code == 400


def test_file_transfer_records_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/file-transfer/transfers")

    assert response.status_code == 401


def test_file_transfer_records_lists_received_uploads(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    async def fake_save_upload(file):
        return {
            "filename": file.filename,
            "bytes": 5,
            "path": str(tmp_path / "received" / file.filename),
        }

    monkeypatch.setattr(main, "save_upload", fake_save_upload)

    upload_response = TestClient(app).post(
        "/api/upload",
        headers={"X-Pairing-Token": token},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    records_response = TestClient(app).get(
        "/api/file-transfer/transfers",
        headers={"X-Pairing-Token": token},
    )

    assert upload_response.status_code == 200
    assert records_response.status_code == 200
    records = records_response.json()["transfers"]
    assert len(records) == 1
    assert records[0]["filename"] == "note.txt"
    assert records[0]["bytes"] == 5
    assert records[0]["source"] == "owner"
    assert records[0]["status"] == "received"
    assert records[0]["detail"] == ""
    assert records[0]["path"].endswith("note.txt")
    assert records[0]["created_at"] > 0


def test_file_transfer_records_lists_rejected_uploads(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    async def fake_save_upload(file):
        raise RuntimeError("too large")

    monkeypatch.setattr(main, "save_upload", fake_save_upload)

    upload_response = TestClient(app).post(
        "/api/upload",
        headers={"X-Pairing-Token": token},
        files={"file": ("huge.txt", b"hello", "text/plain")},
    )
    records_response = TestClient(app).get(
        "/api/file-transfer/transfers",
        headers={"X-Pairing-Token": token},
    )

    assert upload_response.status_code == 400
    records = records_response.json()["transfers"]
    assert len(records) == 1
    assert records[0]["filename"] == "huge.txt"
    assert records[0]["bytes"] == 0
    assert records[0]["source"] == "owner"
    assert records[0]["status"] == "rejected"
    assert records[0]["detail"] == "too large"
    assert records[0]["path"] == ""


def test_clear_file_transfer_records_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    main.transfer_history.add_received(
        {"filename": "note.txt", "bytes": 5, "path": str(tmp_path / "note.txt")},
        source="owner",
    )

    response = TestClient(app).delete("/api/file-transfer/transfers")

    assert response.status_code == 401
    assert len(main.transfer_history.list_records()) == 1


def test_clear_file_transfer_records_removes_records(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    main.transfer_history.add_received(
        {"filename": "note.txt", "bytes": 5, "path": str(tmp_path / "note.txt")},
        source="owner",
    )

    response = TestClient(app).delete(
        "/api/file-transfer/transfers",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "removed": 1}
    assert main.transfer_history.list_records() == []


def test_file_transfer_targets_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/file-transfer/targets")

    assert response.status_code == 401


def test_file_transfer_targets_list_trusted_devices(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    TrustedDeviceStore().trust_device(
        "macbook-1",
        "MacBook",
        {"file_receive": True},
    )
    TrustedDeviceStore().trust_device("mini-1", "Mac Mini")

    response = TestClient(app).get(
        "/api/file-transfer/targets",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    targets = response.json()["targets"]
    targets_by_id = {target["device_id"]: target for target in targets}
    assert set(targets_by_id) == {"macbook-1", "mini-1"}
    assert targets_by_id["macbook-1"]["permissions"]["file_receive"] is True
    assert targets_by_id["macbook-1"]["can_receive_files"] is True
    assert targets_by_id["macbook-1"]["receive_blocked_reason"] == ""
    assert targets_by_id["mini-1"]["permissions"]["file_receive"] is False
    assert targets_by_id["mini-1"]["can_receive_files"] is False
    assert targets_by_id["mini-1"]["receive_blocked_reason"] == "file receive disabled"
    assert "secret_hash" not in targets_by_id["macbook-1"]


def test_file_transfer_targets_reflect_permission_updates(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    TrustedDeviceStore().trust_device("mini-1", "Mac Mini")

    update_response = TestClient(app).patch(
        "/api/trusted-devices/mini-1/permissions",
        headers={"X-Pairing-Token": token},
        json={"permissions": {"file_receive": True}},
    )
    targets_response = TestClient(app).get(
        "/api/file-transfer/targets",
        headers={"X-Pairing-Token": token},
    )

    assert update_response.status_code == 200
    targets_by_id = {
        target["device_id"]: target
        for target in targets_response.json()["targets"]
    }
    assert targets_by_id["mini-1"]["permissions"]["file_receive"] is True
    assert targets_by_id["mini-1"]["can_receive_files"] is True


def test_file_transfer_targets_mark_review_required_as_not_ready(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    TrustedDeviceStore().trust_device(
        "macbook-1",
        "MacBook",
        {"file_receive": True},
    )
    TrustedDeviceStore().mark_review_required({"macbook-1"})

    response = TestClient(app).get(
        "/api/file-transfer/targets",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    target = response.json()["targets"][0]
    assert target["permissions"]["file_receive"] is True
    assert target["review_required"] is True
    assert target["can_receive_files"] is False
    assert target["receive_blocked_reason"] == "trust review required"


def test_device_endpoint_returns_local_identity(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).get("/api/device", headers={"X-Pairing-Token": token})

    assert response.status_code == 200
    device = response.json()["device"]
    assert device["device_id"].startswith("screen-slickshift-")
    assert device["name"]
    assert device["os"]
    assert "token" not in device
    assert "secret" not in device


def test_touchpad_websocket_accepts_first_message_auth(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    with TestClient(app).websocket_connect("/ws/touchpad") as websocket:
        websocket.send_json({"type": "auth", "token": token})
        websocket.send_json({"type": "ping"})


def test_touchpad_websocket_keeps_query_token_compatibility(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    with TestClient(app).websocket_connect(f"/ws/touchpad?token={token}") as websocket:
        websocket.send_json({"type": "ping"})


def test_touchpad_websocket_accepts_session_auth_with_mouse_permission(
    tmp_path,
    monkeypatch,
) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    moves = []
    monkeypatch.setattr(websocket_module, "move_mouse", lambda dx, dy: moves.append((dx, dy)))
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": True},
    )
    original_last_active_at = grant.session.last_active_at
    time.sleep(0.001)

    with TestClient(app).websocket_connect("/ws/touchpad") as websocket:
        websocket.send_json(
            {
                "type": "session_auth",
                "session_id": grant.session.session_id,
                "session_token": grant.session_token,
            }
        )
        websocket.send_json({"type": "mouse_move", "dx": 4, "dy": -2})

    updated_session = main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    )
    assert moves == [(4, -2)]
    assert updated_session is not None
    assert updated_session.last_active_at > original_last_active_at


def test_touchpad_websocket_accepts_v1_session_auth_envelope(
    tmp_path,
    monkeypatch,
) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    moves = []
    monkeypatch.setattr(websocket_module, "move_mouse", lambda dx, dy: moves.append((dx, dy)))
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": True},
    )

    with TestClient(app).websocket_connect("/ws/touchpad") as websocket:
        websocket.send_json(
            {
                "version": 1,
                "payload": {
                    "type": "session_auth",
                    "session_id": grant.session.session_id,
                    "session_token": grant.session_token,
                },
            }
        )
        websocket.send_json(
            {
                "version": 1,
                "payload": {
                    "type": "mouse_move",
                    "dx": 4,
                    "dy": -2,
                },
            }
        )

    assert moves == [(4, -2)]


def test_touchpad_websocket_rejects_unsupported_auth_version(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    with TestClient(app).websocket_connect("/ws/touchpad") as websocket:
        websocket.send_json(
            {
                "version": 2,
                "payload": {
                    "type": "session_auth",
                    "session_id": "missing",
                    "session_token": "wrong",
                },
            }
        )
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()


def test_touchpad_websocket_rejects_session_without_mouse_permission(
    tmp_path,
    monkeypatch,
) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    moves = []
    monkeypatch.setattr(websocket_module, "move_mouse", lambda dx, dy: moves.append((dx, dy)))
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": False},
    )

    with TestClient(app).websocket_connect("/ws/touchpad") as websocket:
        websocket.send_json(
            {
                "type": "session_auth",
                "session_id": grant.session.session_id,
                "session_token": grant.session_token,
            }
        )
        websocket.send_json({"type": "mouse_move", "dx": 4, "dy": -2})
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()

    assert moves == []


def test_touchpad_websocket_ping_does_not_keep_session_active(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": True},
    )
    original_last_active_at = grant.session.last_active_at
    time.sleep(0.001)

    with TestClient(app).websocket_connect("/ws/touchpad") as websocket:
        websocket.send_json(
            {
                "type": "session_auth",
                "session_id": grant.session.session_id,
                "session_token": grant.session_token,
            }
        )
        websocket.send_json({"type": "ping"})

    unchanged_session = main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    )
    assert unchanged_session is not None
    assert unchanged_session.last_active_at == original_last_active_at


def test_touchpad_websocket_rejects_invalid_session_auth(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    with TestClient(app).websocket_connect("/ws/touchpad") as websocket:
        websocket.send_json(
            {
                "type": "session_auth",
                "session_id": "missing",
                "session_token": "wrong",
            }
        )
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()


def test_trusted_devices_endpoint_hides_secret_hash(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).trust_device("macbook-1", "MacBook")

    response = TestClient(app).get("/api/trusted-devices", headers={"X-Pairing-Token": token})

    assert response.status_code == 200
    devices = response.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["device_id"] == "macbook-1"
    assert devices[0]["name"] == "MacBook"
    assert "secret_hash" not in devices[0]
    assert devices[0]["review_required"] is False


def test_remove_trusted_device_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).trust_device("macbook-1", "MacBook")

    response = TestClient(app).delete("/api/trusted-devices/macbook-1")

    assert response.status_code == 401


def test_remove_trusted_device_revokes_saved_trust(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    store = TrustedDeviceStore(config.TRUSTED_DEVICES_FILE)
    credential = store.trust_device("macbook-1", "MacBook")

    response = TestClient(app).delete(
        "/api/trusted-devices/macbook-1",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "device_id": "macbook-1", "revoked_sessions": 0}
    assert store.verify_secret("macbook-1", credential.shared_secret) is False


def test_remove_trusted_device_revokes_sessions_for_device(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).trust_device("macbook-1", "MacBook")
    first = main.pairing_session_book.create_session("macbook-1", guest=False)
    second = main.pairing_session_book.create_session("macbook-1", guest=False)
    other = main.pairing_session_book.create_session("guest-laptop", guest=True)

    response = TestClient(app).delete(
        "/api/trusted-devices/macbook-1",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 2
    assert main.pairing_session_book.verify_session(
        first.session.session_id,
        first.session_token,
    ) is None
    assert main.pairing_session_book.verify_session(
        second.session.session_id,
        second.session_token,
    ) is None
    assert main.pairing_session_book.verify_session(
        other.session.session_id,
        other.session_token,
    )


def test_remove_unknown_trusted_device_returns_404(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).delete(
        "/api/trusted-devices/missing-device",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 404


def test_update_trusted_device_permissions_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).trust_device("macbook-1", "MacBook")

    response = TestClient(app).patch(
        "/api/trusted-devices/macbook-1/permissions",
        json={"permissions": {"keyboard": True}},
    )

    assert response.status_code == 401


def test_update_trusted_device_permissions(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).trust_device("macbook-1", "MacBook")

    response = TestClient(app).patch(
        "/api/trusted-devices/macbook-1/permissions",
        headers={"X-Pairing-Token": token},
        json={"permissions": {"keyboard": True, "clipboard_write": True}},
    )

    assert response.status_code == 200
    device = response.json()["device"]
    assert device["device_id"] == "macbook-1"
    assert device["permissions"]["mouse"] is True
    assert device["permissions"]["keyboard"] is True
    assert device["permissions"]["clipboard_read"] is False
    assert device["permissions"]["clipboard_write"] is True
    assert device["permissions"]["file_receive"] is False
    assert response.json()["revoked_sessions"] == 0


def test_update_trusted_device_permissions_revokes_sessions_for_device(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).trust_device("macbook-1", "MacBook")
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).patch(
        "/api/trusted-devices/macbook-1/permissions",
        headers={"X-Pairing-Token": token},
        json={"permissions": {"keyboard": True}},
    )

    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 1
    assert main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    ) is None


def test_update_trusted_device_permissions_ignores_unknown_keys(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).trust_device("macbook-1", "MacBook")

    response = TestClient(app).patch(
        "/api/trusted-devices/macbook-1/permissions",
        headers={"X-Pairing-Token": token},
        json={"permissions": {"screen_capture": True, "file_receive": True}},
    )

    assert response.status_code == 200
    permissions = response.json()["device"]["permissions"]
    assert "screen_capture" not in permissions
    assert permissions["file_receive"] is True


def test_update_unknown_trusted_device_permissions_returns_404(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).patch(
        "/api/trusted-devices/missing-device/permissions",
        headers={"X-Pairing-Token": token},
        json={"permissions": {"keyboard": True}},
    )

    assert response.status_code == 404


def test_create_pairing_code_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).post("/api/pairing-code", json={"guest": False})

    assert response.status_code == 401


def test_create_pairing_code_returns_six_digit_code(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).post(
        "/api/pairing-code",
        headers={"X-Pairing-Token": token},
        json={"guest": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["code"].isdigit()
    assert len(data["code"]) == 6
    assert data["guest"] is True
    assert data["expires_at"] > 0


def test_consume_pairing_code_trusts_device(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    client = TestClient(app)
    code = _create_code(client, token, guest=False)

    response = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={
            "code": code,
            "device_id": "macbook-1",
            "name": "MacBook",
            "remember_device": True,
            "idle_timeout_seconds": 180,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["trusted"] is True
    assert data["guest"] is False
    assert data["device"]["device_id"] == "macbook-1"
    assert data["shared_secret"]
    assert data["session"]["device_id"] == "macbook-1"
    assert data["session"]["guest"] is False
    assert data["session"]["idle_timeout_seconds"] == 180
    assert data["session"]["last_active_at"] > 0
    assert data["session_token"]
    assert main.pairing_session_book.verify_session(
        data["session"]["session_id"],
        data["session_token"],
    )
    assert TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).verify_secret(
        "macbook-1",
        data["shared_secret"],
    )


def test_consume_pairing_code_is_single_use(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    client = TestClient(app)
    code = _create_code(client, token, guest=False)

    first = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={"code": code, "device_id": "macbook-1", "name": "MacBook"},
    )
    second = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={"code": code, "device_id": "macbook-1", "name": "MacBook"},
    )

    assert first.status_code == 200
    assert second.status_code == 400


def test_consume_pairing_code_rate_limits_bad_guesses(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main,
        "pairing_code_book",
        PairingCodeBook(max_failed_attempts=2),
    )
    token = config.get_or_create_pairing_token()
    client = TestClient(app)

    first = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={"code": "111111", "device_id": "macbook-1", "name": "MacBook"},
    )
    second = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={"code": "222222", "device_id": "macbook-1", "name": "MacBook"},
    )

    assert first.status_code == 400
    assert second.status_code == 429
    assert "Generate a new pairing code" in second.json()["detail"]


def test_rate_limited_pairing_code_attempt_invalidates_active_code(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main,
        "pairing_code_book",
        PairingCodeBook(max_failed_attempts=1),
    )
    token = config.get_or_create_pairing_token()
    client = TestClient(app)
    code = _create_code(client, token, guest=False)

    bad_guess = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={"code": "111111", "device_id": "macbook-1", "name": "MacBook"},
    )
    original_code_after_limit = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={"code": code, "device_id": "macbook-1", "name": "MacBook"},
    )

    assert bad_guess.status_code == 429
    assert original_code_after_limit.status_code == 429


def test_rate_limited_pairing_code_attempt_does_not_log_code(tmp_path, monkeypatch, caplog) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main,
        "pairing_code_book",
        PairingCodeBook(max_failed_attempts=1),
    )
    token = config.get_or_create_pairing_token()
    guessed_code = "111111"

    response = TestClient(app).post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={"code": guessed_code, "device_id": "macbook-1", "name": "MacBook"},
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert response.status_code == 429
    assert guessed_code not in log_text


def test_consume_guest_pairing_code_does_not_save_device(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    client = TestClient(app)
    code = _create_code(client, token, guest=True)

    response = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={
            "code": code,
            "device_id": "guest-laptop",
            "name": "Guest Laptop",
            "remember_device": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["trusted"] is False
    assert data["guest"] is True
    assert data["device_id"] == "guest-laptop"
    assert data["session"]["device_id"] == "guest-laptop"
    assert data["session"]["guest"] is True
    assert data["session"]["idle_timeout_seconds"] == 600
    assert data["session_token"]
    assert main.pairing_session_book.verify_session(
        data["session"]["session_id"],
        data["session_token"],
    )
    assert TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).get_device("guest-laptop") is None


def test_consume_remember_false_does_not_save_device(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    client = TestClient(app)
    code = _create_code(client, token, guest=False)

    response = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={
            "code": code,
            "device_id": "guest-laptop",
            "name": "Guest Laptop",
            "remember_device": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["trusted"] is False
    assert response.json()["session_token"]
    assert TrustedDeviceStore(config.TRUSTED_DEVICES_FILE).get_device("guest-laptop") is None


def test_consume_pairing_code_falls_back_for_invalid_idle_timeout(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    client = TestClient(app)
    code = _create_code(client, token, guest=True)

    response = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={
            "code": code,
            "device_id": "guest-laptop",
            "name": "Guest Laptop",
            "idle_timeout_seconds": 999,
        },
    )

    assert response.status_code == 200
    assert response.json()["session"]["idle_timeout_seconds"] == 600


def test_pairing_code_and_secret_are_not_logged(tmp_path, monkeypatch, caplog) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    client = TestClient(app)
    code = _create_code(client, token, guest=False)

    response = client.post(
        "/api/pairing-code/consume",
        headers={"X-Pairing-Token": token},
        json={"code": code, "device_id": "macbook-1", "name": "MacBook"},
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert response.status_code == 200
    assert code not in log_text
    assert response.json()["shared_secret"] not in log_text
    assert response.json()["session_token"] not in log_text


def test_pairing_sessions_endpoint_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).get("/api/pairing-sessions")

    assert response.status_code == 401


def test_pairing_sessions_endpoint_lists_public_sessions(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"keyboard": True},
    )

    response = TestClient(app).get(
        "/api/pairing-sessions",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == grant.session.session_id
    assert sessions[0]["device_id"] == "macbook-1"
    assert sessions[0]["permissions"]["keyboard"] is True
    assert "token_hash" not in sessions[0]
    assert grant.session_token not in str(sessions[0])


def test_remove_pairing_session_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).delete(f"/api/pairing-sessions/{grant.session.session_id}")

    assert response.status_code == 401


def test_remove_pairing_session_revokes_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).delete(
        f"/api/pairing-sessions/{grant.session.session_id}",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "session_id": grant.session.session_id}
    assert main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    ) is None


def test_remove_unknown_pairing_session_returns_404(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).delete(
        "/api/pairing-sessions/missing-session",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 404


def test_remove_all_pairing_sessions_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).delete("/api/pairing-sessions")

    assert response.status_code == 401


def test_remove_all_pairing_sessions_revokes_every_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    first = main.pairing_session_book.create_session("macbook-1", guest=False)
    second = main.pairing_session_book.create_session("guest-laptop", guest=True)

    response = TestClient(app).delete(
        "/api/pairing-sessions",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "removed": 2}
    assert main.pairing_session_book.verify_session(
        first.session.session_id,
        first.session_token,
    ) is None
    assert main.pairing_session_book.verify_session(
        second.session.session_id,
        second.session_token,
    ) is None


def test_lockout_revokes_pairing_sessions(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    first = main.pairing_session_book.create_session("macbook-1", guest=False)
    second = main.pairing_session_book.create_session("guest-laptop", guest=True)

    response = TestClient(app).post(
        "/api/lockout",
        headers={"X-Pairing-Token": token},
        json={"disabled": True},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "disabled": True,
        "revoked_sessions": 2,
        "marked_review_required": 0,
    }
    assert main.pairing_session_book.verify_session(
        first.session.session_id,
        first.session_token,
    ) is None
    assert main.pairing_session_book.verify_session(
        second.session.session_id,
        second.session_token,
    ) is None


def test_reenable_lockout_does_not_revoke_pairing_sessions(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).post(
        "/api/lockout",
        headers={"X-Pairing-Token": token},
        json={"disabled": False},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "disabled": False,
        "revoked_sessions": 0,
        "marked_review_required": 0,
    }
    assert main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    )


def test_lockout_marks_trusted_session_devices_for_review(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    store = TrustedDeviceStore(config.TRUSTED_DEVICES_FILE)
    store.trust_device("macbook-1", "MacBook")
    trusted_session = main.pairing_session_book.create_session("macbook-1", guest=False)
    guest_session = main.pairing_session_book.create_session("guest-laptop", guest=True)

    response = TestClient(app).post(
        "/api/lockout",
        headers={"X-Pairing-Token": token},
        json={"disabled": True},
    )

    device = store.get_device("macbook-1")
    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 2
    assert response.json()["marked_review_required"] == 1
    assert device is not None
    assert device.review_required is True
    assert main.pairing_session_book.verify_session(
        trusted_session.session.session_id,
        trusted_session.session_token,
    ) is None
    assert main.pairing_session_book.verify_session(
        guest_session.session.session_id,
        guest_session.session_token,
    ) is None


def test_resolve_trust_review_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    store = TrustedDeviceStore(config.TRUSTED_DEVICES_FILE)
    store.trust_device("macbook-1", "MacBook")
    store.mark_review_required({"macbook-1"})

    response = TestClient(app).post(
        "/api/trusted-devices/macbook-1/trust-review",
        json={"keep_trust": True},
    )

    assert response.status_code == 401


def test_resolve_trust_review_can_keep_device(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    store = TrustedDeviceStore(config.TRUSTED_DEVICES_FILE)
    credential = store.trust_device("macbook-1", "MacBook")
    store.mark_review_required({"macbook-1"})

    response = TestClient(app).post(
        "/api/trusted-devices/macbook-1/trust-review",
        headers={"X-Pairing-Token": token},
        json={"keep_trust": True},
    )

    assert response.status_code == 200
    assert response.json()["kept"] is True
    assert response.json()["device"]["review_required"] is False
    assert store.verify_and_mark_seen("macbook-1", credential.shared_secret) is not None


def test_resolve_trust_review_can_remove_device(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    store = TrustedDeviceStore(config.TRUSTED_DEVICES_FILE)
    store.trust_device("macbook-1", "MacBook")
    store.mark_review_required({"macbook-1"})
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).post(
        "/api/trusted-devices/macbook-1/trust-review",
        headers={"X-Pairing-Token": token},
        json={"keep_trust": False},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "kept": False,
        "device_id": "macbook-1",
        "revoked_sessions": 1,
    }
    assert store.get_device("macbook-1") is None
    assert main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    ) is None


def test_resolve_trust_review_unknown_device_returns_404(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).post(
        "/api/trusted-devices/missing-device/trust-review",
        headers={"X-Pairing-Token": token},
        json={"keep_trust": True},
    )

    assert response.status_code == 404


def test_session_send_text_requires_keyboard_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    sent_text = []
    monkeypatch.setattr(main, "send_text_to_pc", lambda text: sent_text.append(text))
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"keyboard": True},
    )
    original_last_active_at = grant.session.last_active_at
    time.sleep(0.001)

    response = TestClient(app).post(
        "/api/session/send-text",
        json={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
            "text": "hello",
        },
    )

    updated_session = main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    )
    assert response.status_code == 200
    assert sent_text == ["hello"]
    assert updated_session is not None
    assert updated_session.last_active_at > original_last_active_at


def test_session_send_text_rejects_missing_keyboard_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    sent_text = []
    monkeypatch.setattr(main, "send_text_to_pc", lambda text: sent_text.append(text))
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).post(
        "/api/session/send-text",
        json={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
            "text": "hello",
        },
    )

    assert response.status_code == 403
    assert sent_text == []


def test_session_clipboard_read_requires_clipboard_read_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main.clipboard_service, "get_clipboard_text", lambda: "clipboard text")
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"clipboard_read": True},
    )
    original_last_active_at = grant.session.last_active_at
    time.sleep(0.001)

    response = TestClient(app).post(
        "/api/session/clipboard/read",
        json={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
        },
    )

    updated_session = main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    )
    assert response.status_code == 200
    assert response.json() == {"text": "clipboard text"}
    assert updated_session is not None
    assert updated_session.last_active_at > original_last_active_at


def test_session_clipboard_read_rejects_missing_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    read_attempts = []
    monkeypatch.setattr(main.clipboard_service, "get_clipboard_text", lambda: read_attempts.append(True))
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).post(
        "/api/session/clipboard/read",
        json={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
        },
    )

    assert response.status_code == 403
    assert read_attempts == []


def test_session_clipboard_write_requires_clipboard_write_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    written_text = []
    monkeypatch.setattr(main.clipboard_service, "set_clipboard_text", lambda text: written_text.append(text))
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"clipboard_write": True},
    )

    response = TestClient(app).post(
        "/api/session/clipboard/write",
        json={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
            "text": "new clipboard",
        },
    )

    assert response.status_code == 200
    assert written_text == ["new clipboard"]


def test_session_clipboard_write_rejects_missing_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    written_text = []
    monkeypatch.setattr(main.clipboard_service, "set_clipboard_text", lambda text: written_text.append(text))
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).post(
        "/api/session/clipboard/write",
        json={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
            "text": "new clipboard",
        },
    )

    assert response.status_code == 403
    assert written_text == []


def test_session_upload_requires_file_receive_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    saved_files = []

    async def fake_save_upload(file):
        saved_files.append(file.filename)
        return {"filename": file.filename, "bytes": 5, "path": str(tmp_path / file.filename)}

    monkeypatch.setattr(main, "save_upload", fake_save_upload)
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"file_receive": True},
    )
    original_last_active_at = grant.session.last_active_at
    time.sleep(0.001)

    response = TestClient(app).post(
        "/api/session/upload",
        data={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
        },
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    updated_session = main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    )
    assert response.status_code == 200
    assert saved_files == ["note.txt"]
    assert updated_session is not None
    assert updated_session.last_active_at > original_last_active_at


def test_session_upload_rejects_missing_file_receive_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    saved_files = []

    async def fake_save_upload(file):
        saved_files.append(file.filename)
        return {"filename": file.filename, "bytes": 5, "path": str(tmp_path / file.filename)}

    monkeypatch.setattr(main, "save_upload", fake_save_upload)
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).post(
        "/api/session/upload",
        data={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
        },
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 403
    assert saved_files == []


def test_session_macro_requires_macros_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    ran_macros = []
    monkeypatch.setattr(main, "run_macro", lambda macro_id: ran_macros.append(macro_id) or {"id": macro_id})
    grant = main.pairing_session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"macros": True},
    )
    original_last_active_at = grant.session.last_active_at
    time.sleep(0.001)

    response = TestClient(app).post(
        "/api/session/macro",
        json={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
            "id": "open_notepad",
        },
    )

    updated_session = main.pairing_session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    )
    assert response.status_code == 200
    assert ran_macros == ["open_notepad"]
    assert updated_session is not None
    assert updated_session.last_active_at > original_last_active_at


def test_session_macro_rejects_missing_macros_permission(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    ran_macros = []
    monkeypatch.setattr(main, "run_macro", lambda macro_id: ran_macros.append(macro_id) or {"id": macro_id})
    grant = main.pairing_session_book.create_session("macbook-1", guest=False)

    response = TestClient(app).post(
        "/api/session/macro",
        json={
            "session_id": grant.session.session_id,
            "session_token": grant.session_token,
            "id": "open_notepad",
        },
    )

    assert response.status_code == 403
    assert ran_macros == []


@pytest.mark.anyio
async def test_save_upload_enforces_size_limit_and_removes_partial_file(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    main.lockout_state.set_disabled(False)
    config.set_receive_dir(str(tmp_path / "received"))
    upload = UploadFile(file=io.BytesIO(b"abcdef"), filename="too-large.txt")

    with pytest.raises(RuntimeError):
        await files_module.save_upload(upload, max_bytes=3)

    assert list((tmp_path / "received").iterdir()) == []


@pytest.mark.anyio
async def test_save_upload_uses_configured_receive_dir(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    main.lockout_state.set_disabled(False)
    receive_dir = config.set_receive_dir(str(tmp_path / "received"))
    upload = UploadFile(file=io.BytesIO(b"hello"), filename="../note.txt")

    result = await files_module.save_upload(upload)

    assert result["filename"] == "note.txt"
    assert Path(result["path"]).parent == receive_dir
    assert (receive_dir / "note.txt").read_bytes() == b"hello"


def test_handoff_layout_preview_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).post(
        "/api/handoff/layout/preview",
        json={"screens": []},
    )

    assert response.status_code == 401


def test_handoff_page_is_served(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/handoff")

    assert response.status_code == 200
    assert "Screen Slickshift Handoff" in response.text


def test_handoff_layout_preview_returns_derived_routes(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).post(
        "/api/handoff/layout/preview",
        headers={"X-Pairing-Token": token},
        json={
            "screens": [
                {
                    "screen_id": "local-main",
                    "device_id": "local",
                    "name": "This Mac",
                    "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                    "primary": True,
                },
                {
                    "screen_id": "target-main",
                    "device_id": "target",
                    "name": "Target Mac",
                    "rect": {"x": 1920, "y": 100, "width": 1440, "height": 900},
                    "primary": True,
                },
            ],
            "snap_tolerance_px": 24,
            "min_overlap_px": 80,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["screens"][0]["screen_id"] == "local-main"
    assert data["routes"][0] == {
        "from_screen_id": "local-main",
        "to_screen_id": "target-main",
        "from_device_id": "local",
        "to_device_id": "target",
        "exit_edge": "right",
        "enter_edge": "left",
        "overlap_px": 900,
    }


def test_handoff_layout_preview_honors_edge_disabled_monitor(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    response = TestClient(app).post(
        "/api/handoff/layout/preview",
        headers={"X-Pairing-Token": token},
        json={
            "screens": [
                {
                    "screen_id": "local-main",
                    "device_id": "local",
                    "name": "This Mac",
                    "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                    "edge_enabled": True,
                },
                {
                    "screen_id": "target-main",
                    "device_id": "target",
                    "name": "Target Mac",
                    "rect": {"x": 1920, "y": 100, "width": 1440, "height": 900},
                    "edge_enabled": False,
                },
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["screens"][1]["edge_enabled"] is False
    assert response.json()["routes"] == []


def test_handoff_remote_status_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/handoff/remote/status")

    assert response.status_code == 401


def test_handoff_remote_start_connects_bridge(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeRemoteBridge()
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).post(
        "/api/handoff/remote/start",
        headers={"X-Pairing-Token": token},
        json={"host": "192.168.1.25", "port": 8765, "token": "remote-token"},
    )

    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert bridge.started == {
        "host": "192.168.1.25",
        "port": 8765,
        "path": "/ws/touchpad",
        "token": "remote-token",
    }


def test_handoff_remote_start_rejects_unreachable_receiver(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeRemoteBridge(RemoteStatus(reachable=False, error="offline"))
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).post(
        "/api/handoff/remote/start",
        headers={"X-Pairing-Token": token},
        json={"host": "192.168.1.25", "port": 8765, "token": "remote-token"},
    )

    assert response.status_code == 400


def test_handoff_remote_event_sends_mouse_event(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeRemoteBridge()
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).post(
        "/api/handoff/remote/event",
        headers={"X-Pairing-Token": token},
        json={"type": "mouse_move", "dx": 4, "dy": -2},
    )

    assert response.status_code == 200
    assert bridge.events == [{"type": "mouse_move", "dx": 4.0, "dy": -2.0, "button": "left", "down": True, "amount": 0}]


def test_handoff_remote_stop_closes_bridge(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeRemoteBridge()
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).post(
        "/api/handoff/remote/stop",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert bridge.stopped is True


class FakeRemoteBridge:
    def __init__(self, start_status: RemoteStatus | None = None) -> None:
        self.start_status = start_status or RemoteStatus(
            reachable=True,
            input_events=("mouse_move", "mouse_button", "scroll", "ping"),
        )
        self.started = None
        self.events = []
        self.stopped = False

    def status(self) -> dict:
        return {"connected": self.started is not None, "target": None}

    async def start(self, target, token: str) -> RemoteStatus:
        self.started = {
            "host": target.host,
            "port": target.port,
            "path": target.path,
            "token": token,
        }
        return self.start_status

    async def send_event(self, message: dict) -> dict:
        self.events.append(message)
        return {"ok": True, "event": message["type"]}

    async def stop(self) -> dict:
        self.stopped = True
        return {"ok": True, "connected": False}


def _create_code(client: TestClient, token: str, guest: bool) -> str:
    response = client.post(
        "/api/pairing-code",
        headers={"X-Pairing-Token": token},
        json={"guest": guest},
    )
    assert response.status_code == 200
    return response.json()["code"]


def _use_temp_config(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "TOKEN_FILE", config_dir / "pairing_token.txt")
    monkeypatch.setattr(config, "TRUSTED_DEVICES_FILE", config_dir / "trusted_devices.json")
    monkeypatch.setattr(config, "DEVICE_IDENTITY_FILE", config_dir / "device_identity.json")
    monkeypatch.setattr(config, "RECEIVE_DIR_FILE", config_dir / "receive_dir.txt")
    monkeypatch.setattr(main, "pairing_code_book", PairingCodeBook())
    monkeypatch.setattr(main, "pairing_session_book", PairingSessionBook())
    main.lockout_state.set_disabled(False)
    main.transfer_history.clear()
