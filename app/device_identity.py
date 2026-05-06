from __future__ import annotations

import json
import platform
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import config


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    name: str
    os: str
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "os": self.os,
            "created_at": self.created_at,
        }


class DeviceIdentityStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.DEVICE_IDENTITY_FILE

    def get_or_create(self) -> DeviceIdentity:
        existing = self.load()
        if existing is not None:
            return existing

        identity = DeviceIdentity(
            device_id=_new_device_id(),
            name=_default_device_name(),
            os=_current_os_name(),
            created_at=time.time(),
        )
        self.save(identity)
        return identity

    def load(self) -> DeviceIdentity | None:
        if not self.path.exists():
            return None

        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            return None
        return _identity_from_dict(data)

    def save(self, identity: DeviceIdentity) -> None:
        config.ensure_directories()
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(identity.to_dict(), file, indent=2)
            file.write("\n")


def get_or_create_device_identity() -> DeviceIdentity:
    return DeviceIdentityStore().get_or_create()


def _new_device_id() -> str:
    return f"screen-slickshift-{secrets.token_hex(16)}"


def _default_device_name() -> str:
    hostname = socket.gethostname().strip()
    if hostname:
        return hostname
    return "Unnamed Device"


def _current_os_name() -> str:
    system = platform.system().strip().lower()
    if system == "darwin":
        return "macos"
    if system:
        return system
    return "unknown"


def _identity_from_dict(data: dict[str, Any]) -> DeviceIdentity | None:
    device_id = data.get("device_id")
    name = data.get("name")
    os_name = data.get("os")
    created_at = data.get("created_at")

    if not isinstance(device_id, str) or not device_id:
        return None
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(os_name, str) or not os_name:
        return None

    try:
        created_at_float = float(created_at)
    except (TypeError, ValueError):
        return None

    return DeviceIdentity(
        device_id=device_id,
        name=name,
        os=os_name,
        created_at=created_at_float,
    )
