from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.discovery import DiscoveryService, PendingPairRequest
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


class FakeDiscoveryService:
    def __init__(self, advertising: bool = False) -> None:
        self._advertising = advertising
        self.started = False
        self.stopped = False

    @property
    def advertising(self) -> bool:
        return self._advertising

    def get_discovered(self):
        return []

    def public_dict(self) -> dict:
        return {"advertising": self._advertising, "discovered": []}

    async def start(self, port, device_name, device_id) -> None:
        self._advertising = True
        self.started = True

    async def stop(self) -> None:
        self._advertising = False
        self.stopped = True


# ---------------------------------------------------------------------------
# /api/discovery/advertise
# ---------------------------------------------------------------------------

def test_advertise_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post("/api/discovery/advertise")
    assert response.status_code == 401


def test_advertise_calls_discovery_service(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = FakeDiscoveryService()
    monkeypatch.setattr(main, "discovery_service", fake)

    response = TestClient(app).post(
        "/api/discovery/advertise",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert fake.started is True
    assert response.json()["advertising"] is True


# ---------------------------------------------------------------------------
# /api/discovery/stop
# ---------------------------------------------------------------------------

def test_stop_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).post("/api/discovery/stop")
    assert response.status_code == 401


def test_stop_calls_discovery_service(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = FakeDiscoveryService(advertising=True)
    monkeypatch.setattr(main, "discovery_service", fake)

    response = TestClient(app).post(
        "/api/discovery/stop",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert fake.stopped is True
    assert response.json()["advertising"] is False


# ---------------------------------------------------------------------------
# /api/discovery/browse
# ---------------------------------------------------------------------------

def test_browse_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).get("/api/discovery/browse")
    assert response.status_code == 401


def test_browse_returns_empty_list(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    monkeypatch.setattr(main, "discovery_service", FakeDiscoveryService())

    response = TestClient(app).get(
        "/api/discovery/browse",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["devices"] == []
    assert data["advertising"] is False


# ---------------------------------------------------------------------------
# /api/discovery/request-pair (unauthenticated)
# ---------------------------------------------------------------------------

def test_request_pair_creates_pending_request(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "discovery_code_book", PairingCodeBook(ttl_seconds=45))
    monkeypatch.setattr(main, "_pending_pair_request", None)

    response = TestClient(app).post(
        "/api/discovery/request-pair",
        json={"device_id": "remote-device-1", "name": "MacBook Air"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["expires_in"] > 0
    assert main._pending_pair_request is not None
    assert main._pending_pair_request.requester_name == "MacBook Air"


def test_request_pair_rejects_when_lockout(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    main.lockout_state.set_disabled(True)

    response = TestClient(app).post(
        "/api/discovery/request-pair",
        json={"device_id": "remote-device-1", "name": "MacBook Air"},
    )

    assert response.status_code == 503
    main.lockout_state.set_disabled(False)


def test_request_pair_rejects_when_already_pending(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "_pending_pair_request", PendingPairRequest(
        requester_id="existing",
        requester_name="Existing Device",
        code="000000",
        expires_at=time.time() + 45,
    ))

    response = TestClient(app).post(
        "/api/discovery/request-pair",
        json={"device_id": "remote-device-2", "name": "Another Device"},
    )

    assert response.status_code == 429


# ---------------------------------------------------------------------------
# /api/discovery/pending-request
# ---------------------------------------------------------------------------

def test_pending_request_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).get("/api/discovery/pending-request")
    assert response.status_code == 401


def test_pending_request_returns_false_when_none(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    monkeypatch.setattr(main, "_pending_pair_request", None)

    response = TestClient(app).get(
        "/api/discovery/pending-request",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["pending"] is False


def test_pending_request_returns_code_when_set(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    monkeypatch.setattr(main, "_pending_pair_request", PendingPairRequest(
        requester_id="dev-1",
        requester_name="Remote Mac",
        code="654321",
        expires_at=time.time() + 40,
    ))

    response = TestClient(app).get(
        "/api/discovery/pending-request",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["pending"] is True
    assert data["code"] == "654321"
    assert data["requester_name"] == "Remote Mac"


# ---------------------------------------------------------------------------
# /api/discovery/dismiss-request
# ---------------------------------------------------------------------------

def test_dismiss_clears_pending_request(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    monkeypatch.setattr(main, "_pending_pair_request", PendingPairRequest(
        requester_id="dev-1",
        requester_name="Remote Mac",
        code="111111",
        expires_at=time.time() + 40,
    ))

    response = TestClient(app).post(
        "/api/discovery/dismiss-request",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert main._pending_pair_request is None


# ---------------------------------------------------------------------------
# /api/discovery/pair (unauthenticated — code is the auth)
# ---------------------------------------------------------------------------

def test_discovery_pair_creates_guest_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    code_book = PairingCodeBook(ttl_seconds=45)
    code = code_book.create_code(guest=False)
    monkeypatch.setattr(main, "discovery_code_book", code_book)

    response = TestClient(app).post(
        "/api/discovery/pair",
        json={
            "code": code.code,
            "device_id": "client-1",
            "name": "Client Mac",
            "permissions": {"mouse": True},
            "remember_device": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["guest"] is True
    assert "session_token" in data


def test_discovery_pair_creates_trusted_session(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    code_book = PairingCodeBook(ttl_seconds=45)
    code = code_book.create_code(guest=False)
    monkeypatch.setattr(main, "discovery_code_book", code_book)

    response = TestClient(app).post(
        "/api/discovery/pair",
        json={
            "code": code.code,
            "device_id": "client-trusted-1",
            "name": "Trusted Mac",
            "permissions": {"mouse": True, "keyboard": False},
            "remember_device": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["trusted"] is True
    assert "shared_secret" in data
    assert "session_token" in data


def test_discovery_pair_rejects_invalid_code(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "discovery_code_book", PairingCodeBook(ttl_seconds=45))

    response = TestClient(app).post(
        "/api/discovery/pair",
        json={"code": "000000", "device_id": "bad", "name": "Bad Device", "remember_device": False},
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# /api/pairing/trusted-reconnect (unauthenticated)
# ---------------------------------------------------------------------------

def test_trusted_reconnect_accepts_valid_secret(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    # First, create a trusted device to get a shared secret
    from app.pairing import TrustedDeviceStore
    credential = TrustedDeviceStore().trust_device("dev-reconnect", "My Device")

    response = TestClient(app).post(
        "/api/pairing/trusted-reconnect",
        json={
            "device_id": "dev-reconnect",
            "shared_secret": credential.shared_secret,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "session_token" in data
    assert data["device"]["device_id"] == "dev-reconnect"


def test_trusted_reconnect_rejects_wrong_secret(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    from app.pairing import TrustedDeviceStore
    TrustedDeviceStore().trust_device("dev-reconnect-2", "My Device")

    response = TestClient(app).post(
        "/api/pairing/trusted-reconnect",
        json={"device_id": "dev-reconnect-2", "shared_secret": "wrong-secret"},
    )

    assert response.status_code == 401


def test_trusted_reconnect_rejects_unknown_device(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).post(
        "/api/pairing/trusted-reconnect",
        json={"device_id": "does-not-exist", "shared_secret": "anything"},
    )

    assert response.status_code == 401
