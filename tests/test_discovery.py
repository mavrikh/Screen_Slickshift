from __future__ import annotations

import time

import pytest

from app.discovery import (
    DiscoveredDevice,
    DiscoveryService,
    PendingPairRequest,
    get_local_ip,
)


# ---------------------------------------------------------------------------
# DiscoveredDevice
# ---------------------------------------------------------------------------

def test_discovered_device_public_dict() -> None:
    d = DiscoveredDevice(name="MacBook Air", device_id="abc-123", host="192.168.1.10", port=8765)
    result = d.public_dict()
    assert result == {
        "name": "MacBook Air",
        "device_id": "abc-123",
        "host": "192.168.1.10",
        "port": 8765,
    }


# ---------------------------------------------------------------------------
# PendingPairRequest
# ---------------------------------------------------------------------------

def test_pending_request_not_expired_when_fresh() -> None:
    req = PendingPairRequest(
        requester_id="dev-1",
        requester_name="Device 1",
        code="123456",
        expires_at=time.time() + 45,
    )
    assert not req.is_expired()
    assert req.seconds_remaining() > 0


def test_pending_request_expired_when_past() -> None:
    req = PendingPairRequest(
        requester_id="dev-1",
        requester_name="Device 1",
        code="123456",
        expires_at=time.time() - 1,
    )
    assert req.is_expired()
    assert req.seconds_remaining() == 0


def test_pending_request_public_dict_omits_nothing() -> None:
    expires_at = time.time() + 45
    req = PendingPairRequest(
        requester_id="dev-1",
        requester_name="Device 1",
        code="123456",
        expires_at=expires_at,
    )
    d = req.public_dict()
    assert d["requester_id"] == "dev-1"
    assert d["requester_name"] == "Device 1"
    assert d["code"] == "123456"
    assert d["expires_at"] == expires_at
    assert d["seconds_remaining"] > 0


# ---------------------------------------------------------------------------
# get_local_ip
# ---------------------------------------------------------------------------

def test_get_local_ip_returns_string() -> None:
    ip = get_local_ip()
    assert isinstance(ip, str)
    parts = ip.split(".")
    assert len(parts) == 4


# ---------------------------------------------------------------------------
# DiscoveryService (non-network)
# ---------------------------------------------------------------------------

def test_discovery_service_starts_not_advertising() -> None:
    svc = DiscoveryService()
    assert not svc.advertising
    assert svc.get_discovered() == []


def test_discovery_service_public_dict_idle() -> None:
    svc = DiscoveryService()
    d = svc.public_dict()
    assert d["advertising"] is False
    assert d["discovered"] == []


@pytest.mark.anyio
async def test_discovery_service_stop_when_not_started_is_safe() -> None:
    svc = DiscoveryService()
    await svc.stop()
    assert not svc.advertising
