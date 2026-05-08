from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import websockets

from app.protocol import SUPPORTED_INPUT_EVENTS, parse_event


REQUIRED_REMOTE_EVENTS = ("mouse_move", "mouse_button", "scroll", "ping")
REMOTE_PATHS = {"/ws/touchpad", "/ws/input"}


@dataclass(frozen=True)
class RemoteStatus:
    reachable: bool
    disabled: bool = False
    protocol_version: Optional[int] = None
    input_events: tuple[str, ...] = ()
    error: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "disabled": self.disabled,
            "protocol_version": self.protocol_version,
            "input_events": list(self.input_events),
            "error": self.error,
        }


@dataclass(frozen=True)
class RemoteTarget:
    host: str
    port: int
    path: str = "/ws/touchpad"

    @classmethod
    def from_values(cls, host: str, port: int, path: str = "/ws/touchpad") -> "RemoteTarget":
        clean_host = normalize_host(host)
        clean_port = normalize_port(port)
        clean_path = normalize_path(path)
        return cls(host=clean_host, port=clean_port, path=clean_path)

    @property
    def status_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/status"

    @property
    def auth_check_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/auth/check"

    @property
    def websocket_url(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"


class RemoteHandoffBridge:
    def __init__(self) -> None:
        self._websocket = None
        self._target: Optional[RemoteTarget] = None
        self._lock = asyncio.Lock()

    async def start(self, target: RemoteTarget, token: str) -> RemoteStatus:
        if not token:
            raise ValueError("Remote pairing token is required.")

        status = await asyncio.to_thread(get_remote_status, target)
        if not should_connect(status):
            return status
        if not await asyncio.to_thread(remote_token_is_valid, target, token):
            raise ValueError("Remote token was rejected.")

        async with self._lock:
            await self._close_locked()
            websocket = await websockets.connect(target.websocket_url)
            await websocket.send(json.dumps({"type": "auth", "token": token}))
            self._websocket = websocket
            self._target = target
        return status

    async def send_event(self, message: dict[str, Any]) -> dict[str, Any]:
        event = parse_event(message)
        if event.type not in SUPPORTED_INPUT_EVENTS:
            raise ValueError("Unsupported remote input event.")

        if event.type == "mouse_move":
            outbound = {"type": "mouse_move", "dx": event.dx, "dy": event.dy}
        elif event.type == "mouse_button":
            outbound = {"type": "mouse_button", "button": event.button, "down": event.down}
        elif event.type == "scroll":
            outbound = {"type": "scroll", "amount": event.amount}
        else:
            outbound = {"type": "ping"}

        async with self._lock:
            if self._websocket is None:
                raise RuntimeError("Remote handoff is not connected.")
            await self._websocket.send(json.dumps(outbound))
        return {"ok": True, "event": outbound["type"]}

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            await self._close_locked()
        return {"ok": True, "connected": False}

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._websocket is not None,
            "target": {
                "host": self._target.host,
                "port": self._target.port,
                "path": self._target.path,
            }
            if self._target is not None
            else None,
        }

    async def _close_locked(self) -> None:
        websocket = self._websocket
        self._websocket = None
        self._target = None
        if websocket is not None:
            await websocket.close()


def normalize_host(host: str) -> str:
    clean = str(host or "").strip()
    if not clean:
        raise ValueError("Remote host is required.")
    if "://" in clean:
        parsed = urlsplit(clean)
        clean = parsed.hostname or ""
    if not clean or any(char in clean for char in "/?#@"):
        raise ValueError("Remote host must be a hostname or IP address.")
    return clean


def normalize_port(port: int) -> int:
    try:
        clean_port = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Remote port must be a number.") from exc
    if clean_port < 1 or clean_port > 65535:
        raise ValueError("Remote port must be between 1 and 65535.")
    return clean_port


def normalize_path(path: str) -> str:
    clean = str(path or "/ws/touchpad").strip()
    if not clean.startswith("/"):
        clean = f"/{clean}"
    if clean not in REMOTE_PATHS:
        raise ValueError("Remote path must be /ws/touchpad or /ws/input.")
    return clean


def get_remote_status(target: RemoteTarget, timeout: float = 2.0) -> RemoteStatus:
    try:
        with urlopen(target.status_url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return RemoteStatus(reachable=False, error=str(exc))

    protocol = payload.get("protocol") if isinstance(payload, dict) else None
    if not isinstance(protocol, dict):
        protocol = {}
    input_events = protocol.get("input_events", ())
    if not isinstance(input_events, list):
        input_events = []

    return RemoteStatus(
        reachable=True,
        disabled=bool(payload.get("disabled")) if isinstance(payload, dict) else False,
        protocol_version=protocol.get("version") if isinstance(protocol.get("version"), int) else None,
        input_events=tuple(str(event) for event in input_events),
    )


def remote_token_is_valid(target: RemoteTarget, token: str, timeout: float = 2.0) -> bool:
    request = Request(target.auth_check_url, headers={"X-Pairing-Token": token}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (HTTPError, OSError, URLError):
        return False


def should_connect(status: RemoteStatus) -> bool:
    if not status.reachable or status.disabled:
        return False
    if status.input_events and not set(REQUIRED_REMOTE_EVENTS).issubset(set(status.input_events)):
        return False
    return True
