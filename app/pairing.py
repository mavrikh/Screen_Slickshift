from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app import config


DEFAULT_PERMISSIONS = {
    "mouse": True,
    "keyboard": False,
    "clipboard_read": False,
    "clipboard_write": False,
    "file_receive": False,
    "macros": False,
}
ALLOWED_IDLE_TIMEOUT_SECONDS = (60, 180, 300, 600, 1200, 1800)
DEFAULT_IDLE_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class TrustedDevice:
    device_id: str
    name: str
    secret_hash: str
    permissions: dict[str, bool] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_seen_at: Optional[float] = None
    review_required: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "permissions": self.permissions,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "review_required": self.review_required,
        }


@dataclass(frozen=True)
class PairingCode:
    code: str
    expires_at: float
    guest: bool


@dataclass(frozen=True)
class PairingCodeConsumeResult:
    pairing_code: Optional[PairingCode]
    rate_limited: bool = False
    regenerate_required: bool = False


@dataclass(frozen=True)
class PairingCredential:
    device: TrustedDevice
    shared_secret: str


@dataclass(frozen=True)
class PairingSession:
    session_id: str
    device_id: str
    guest: bool
    permissions: dict[str, bool]
    token_hash: str
    expires_at: float
    idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS
    last_active_at: float = field(default_factory=time.time)

    def public_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "guest": self.guest,
            "permissions": self.permissions,
            "expires_at": self.expires_at,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "last_active_at": self.last_active_at,
        }


@dataclass(frozen=True)
class PairingSessionGrant:
    session: PairingSession
    session_token: str


class PairingCodeBook:
    def __init__(
        self,
        ttl_seconds: int = 60,
        max_failed_attempts: int = 5,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_failed_attempts = max_failed_attempts
        self._codes: dict[str, PairingCode] = {}
        self._failed_attempts = 0
        self._regenerate_required = False

    def create_code(self, guest: bool = False) -> PairingCode:
        self.discard_expired()
        self._failed_attempts = 0
        self._regenerate_required = False

        code = _six_digit_code()
        while code in self._codes:
            code = _six_digit_code()

        pairing_code = PairingCode(
            code=code,
            expires_at=time.time() + self._ttl_seconds,
            guest=guest,
        )
        self._codes[code] = pairing_code
        return pairing_code

    def consume_code(self, code: str) -> Optional[PairingCode]:
        result = self.consume_code_result(code)
        if result.rate_limited:
            return None
        return result.pairing_code

    def consume_code_result(self, code: str) -> PairingCodeConsumeResult:
        now = time.time()
        if self._regenerate_required:
            return PairingCodeConsumeResult(
                pairing_code=None,
                rate_limited=True,
                regenerate_required=True,
            )

        pairing_code = self._codes.pop(code, None)
        if pairing_code is None:
            self._record_failed_attempt()
            return PairingCodeConsumeResult(
                pairing_code=None,
                rate_limited=self._regenerate_required,
                regenerate_required=self._regenerate_required,
            )
        if pairing_code.expires_at < now:
            return PairingCodeConsumeResult(pairing_code=None)

        self._failed_attempts = 0
        self._regenerate_required = False
        return PairingCodeConsumeResult(pairing_code=pairing_code)

    def discard_expired(self) -> None:
        now = time.time()
        expired_codes = [
            code
            for code, pairing_code in self._codes.items()
            if pairing_code.expires_at < now
        ]
        for code in expired_codes:
            self._codes.pop(code, None)

    def _record_failed_attempt(self) -> None:
        self._failed_attempts += 1
        if self._failed_attempts >= self._max_failed_attempts:
            self._codes.clear()
            self._regenerate_required = True
            self._failed_attempts = 0


class PairingSessionBook:
    def __init__(self, ttl_seconds: int = 8 * 60 * 60) -> None:
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, PairingSession] = {}

    def create_session(
        self,
        device_id: str,
        guest: bool,
        permissions: Optional[dict[str, bool]] = None,
        idle_timeout_seconds: int = DEFAULT_IDLE_TIMEOUT_SECONDS,
    ) -> PairingSessionGrant:
        self.discard_expired()

        session_token = secrets.token_urlsafe(32)
        now = time.time()
        session = PairingSession(
            session_id=secrets.token_urlsafe(18),
            device_id=device_id,
            guest=guest,
            permissions=_clean_permissions(permissions),
            token_hash=_hash_secret(session_token),
            expires_at=now + self._ttl_seconds,
            idle_timeout_seconds=_clean_idle_timeout(idle_timeout_seconds),
            last_active_at=now,
        )
        self._sessions[session.session_id] = session
        return PairingSessionGrant(session=session, session_token=session_token)

    def verify_session(self, session_id: str, session_token: str) -> Optional[PairingSession]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if self._is_session_expired(session):
            self._sessions.pop(session_id, None)
            return None

        actual = _hash_secret(session_token)
        if not hmac.compare_digest(session.token_hash, actual):
            return None
        return session

    def verify_session_permission(
        self,
        session_id: str,
        session_token: str,
        permission: str,
    ) -> Optional[PairingSession]:
        if permission not in DEFAULT_PERMISSIONS:
            return None

        session = self.verify_session(session_id, session_token)
        if session is None:
            return None
        if not session.permissions.get(permission, False):
            return None
        return session

    def mark_session_active(self, session_id: str, session_token: str) -> Optional[PairingSession]:
        session = self.verify_session(session_id, session_token)
        if session is None:
            return None

        updated_session = PairingSession(
            session_id=session.session_id,
            device_id=session.device_id,
            guest=session.guest,
            permissions=session.permissions,
            token_hash=session.token_hash,
            expires_at=session.expires_at,
            idle_timeout_seconds=session.idle_timeout_seconds,
            last_active_at=time.time(),
        )
        self._sessions[session_id] = updated_session
        return updated_session

    def list_sessions(self) -> list[PairingSession]:
        self.discard_expired()
        return list(self._sessions.values())

    def remove_session(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def remove_sessions_for_device(self, device_id: str) -> int:
        matching_session_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if session.device_id == device_id
        ]
        for session_id in matching_session_ids:
            self._sessions.pop(session_id, None)
        return len(matching_session_ids)

    def remove_all_sessions(self) -> int:
        count = len(self._sessions)
        self._sessions.clear()
        return count

    def discard_expired(self) -> None:
        expired_session_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if self._is_session_expired(session)
        ]
        for session_id in expired_session_ids:
            self._sessions.pop(session_id, None)

    def _is_session_expired(self, session: PairingSession) -> bool:
        now = time.time()
        return (
            session.expires_at < now
            or session.last_active_at + session.idle_timeout_seconds < now
        )


class TrustedDeviceStore:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or config.TRUSTED_DEVICES_FILE

    def list_devices(self) -> list[TrustedDevice]:
        return list(self._load().values())

    def get_device(self, device_id: str) -> Optional[TrustedDevice]:
        return self._load().get(device_id)

    def trust_device(
        self,
        device_id: str,
        name: str,
        permissions: Optional[dict[str, bool]] = None,
    ) -> PairingCredential:
        shared_secret = secrets.token_urlsafe(32)
        device = TrustedDevice(
            device_id=device_id,
            name=name,
            secret_hash=_hash_secret(shared_secret),
            permissions=_clean_permissions(permissions),
        )

        devices = self._load()
        devices[device.device_id] = device
        self._save(devices)

        return PairingCredential(device=device, shared_secret=shared_secret)

    def verify_secret(self, device_id: str, shared_secret: str) -> bool:
        device = self.get_device(device_id)
        if device is None:
            return False

        expected = device.secret_hash
        actual = _hash_secret(shared_secret)
        return hmac.compare_digest(expected, actual)

    def verify_and_mark_seen(self, device_id: str, shared_secret: str) -> Optional[TrustedDevice]:
        devices = self._load()
        device = devices.get(device_id)
        if device is None:
            return None
        if device.review_required:
            return None

        expected = device.secret_hash
        actual = _hash_secret(shared_secret)
        if not hmac.compare_digest(expected, actual):
            return None

        updated_device = TrustedDevice(
            device_id=device.device_id,
            name=device.name,
            secret_hash=device.secret_hash,
            permissions=device.permissions,
            created_at=device.created_at,
            last_seen_at=time.time(),
            review_required=device.review_required,
        )
        devices[device_id] = updated_device
        self._save(devices)
        return updated_device

    def update_permissions(
        self,
        device_id: str,
        permissions: dict[str, bool],
    ) -> Optional[TrustedDevice]:
        devices = self._load()
        device = devices.get(device_id)
        if device is None:
            return None

        updated_device = TrustedDevice(
            device_id=device.device_id,
            name=device.name,
            secret_hash=device.secret_hash,
            permissions=_clean_permissions({**device.permissions, **permissions}),
            created_at=device.created_at,
            last_seen_at=device.last_seen_at,
            review_required=device.review_required,
        )
        devices[device_id] = updated_device
        self._save(devices)
        return updated_device

    def mark_review_required(self, device_ids: set[str]) -> int:
        if not device_ids:
            return 0

        devices = self._load()
        changed = 0
        for device_id in device_ids:
            device = devices.get(device_id)
            if device is None or device.review_required:
                continue
            devices[device_id] = TrustedDevice(
                device_id=device.device_id,
                name=device.name,
                secret_hash=device.secret_hash,
                permissions=device.permissions,
                created_at=device.created_at,
                last_seen_at=device.last_seen_at,
                review_required=True,
            )
            changed += 1

        if changed:
            self._save(devices)
        return changed

    def resolve_review(self, device_id: str, keep_trust: bool) -> Optional[TrustedDevice]:
        devices = self._load()
        device = devices.get(device_id)
        if device is None:
            return None

        if not keep_trust:
            del devices[device_id]
            self._save(devices)
            return None

        updated_device = TrustedDevice(
            device_id=device.device_id,
            name=device.name,
            secret_hash=device.secret_hash,
            permissions=device.permissions,
            created_at=device.created_at,
            last_seen_at=device.last_seen_at,
            review_required=False,
        )
        devices[device_id] = updated_device
        self._save(devices)
        return updated_device

    def remove_device(self, device_id: str) -> bool:
        devices = self._load()
        if device_id not in devices:
            return False
        del devices[device_id]
        self._save(devices)
        return True

    def _load(self) -> dict[str, TrustedDevice]:
        if not self.path.exists():
            return {}

        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        devices = data.get("devices", [])
        if not isinstance(devices, list):
            return {}

        trusted_devices: dict[str, TrustedDevice] = {}
        for item in devices:
            if not isinstance(item, dict):
                continue
            device = _trusted_device_from_dict(item)
            if device is not None:
                trusted_devices[device.device_id] = device

        return trusted_devices

    def _save(self, devices: dict[str, TrustedDevice]) -> None:
        config.ensure_directories()
        data = {
            "devices": [
                _trusted_device_to_dict(device)
                for device in sorted(devices.values(), key=lambda item: item.name.lower())
            ]
        }
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
            file.write("\n")


def _six_digit_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _clean_permissions(permissions: Optional[dict[str, bool]]) -> dict[str, bool]:
    clean = dict(DEFAULT_PERMISSIONS)
    if not permissions:
        return clean

    for key in clean:
        if key in permissions:
            clean[key] = bool(permissions[key])
    return clean


def _clean_idle_timeout(value: int) -> int:
    if value in ALLOWED_IDLE_TIMEOUT_SECONDS:
        return value
    return DEFAULT_IDLE_TIMEOUT_SECONDS


def _trusted_device_from_dict(data: dict[str, Any]) -> Optional[TrustedDevice]:
    device_id = data.get("device_id")
    name = data.get("name")
    secret_hash = data.get("secret_hash")
    if not isinstance(device_id, str) or not device_id:
        return None
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(secret_hash, str) or not secret_hash:
        return None

    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}

    return TrustedDevice(
        device_id=device_id,
        name=name,
        secret_hash=secret_hash,
        permissions=_clean_permissions(permissions),
        created_at=_float_or_default(data.get("created_at"), time.time()),
        last_seen_at=_optional_float(data.get("last_seen_at")),
        review_required=bool(data.get("review_required", False)),
    )


def _trusted_device_to_dict(device: TrustedDevice) -> dict[str, Any]:
    return {
        "device_id": device.device_id,
        "name": device.name,
        "secret_hash": device.secret_hash,
        "permissions": _clean_permissions(device.permissions),
        "created_at": device.created_at,
        "last_seen_at": device.last_seen_at,
        "review_required": device.review_required,
    }


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
