from pathlib import Path

from fastapi.testclient import TestClient

from app import config
from app.main import app
from app import main
from app.pairing import PairingCodeBook, PairingSessionBook, TrustedDeviceStore


def test_device_endpoint_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/device")

    assert response.status_code == 401


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
    monkeypatch.setattr(main, "pairing_code_book", PairingCodeBook())
    monkeypatch.setattr(main, "pairing_session_book", PairingSessionBook())
