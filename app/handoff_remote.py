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
    def input_status_url(self) -> str:
        return f"http://{self.host}:{self.port}/api/input/status?check_backend=true"

    @property
    def websocket_url(self) -> str:
        return f"ws://{self.host}:{self.port}{self.path}"


class RemoteHandoffBridge:
    def __init__(self) -> None:
        self._websocket = None
        self._target: Optional[RemoteTarget] = None
        self._token: Optional[str] = None
        self._session_id: Optional[str] = None
        self._session_token: Optional[str] = None
        self._lock = asyncio.Lock()

    async def start(self, target: RemoteTarget, token: str) -> RemoteStatus:
        if not token:
            raise ValueError("Remote pairing token is required.")

        status = await asyncio.to_thread(get_remote_status, target)
        if not should_connect(status):
            return status
        if not await asyncio.to_thread(remote_token_is_valid, target, token):
            raise ValueError("Remote token was rejected.")
        input_status = await asyncio.to_thread(get_remote_input_status, target, token)
        if input_status and not input_status.get("input_allowed", False):
            error = input_status.get("error") or "Remote input is not currently allowed."
            raise ValueError(str(error))

        async with self._lock:
            await self._close_locked()
            websocket = await websockets.connect(target.websocket_url)
            await websocket.send(json.dumps({"type": "auth", "token": token}))
            self._websocket = websocket
            self._target = target
            self._token = token
        return status

    async def start_with_session(
        self,
        target: RemoteTarget,
        session_id: str,
        session_token: str,
    ) -> RemoteStatus:
        status = await asyncio.to_thread(get_remote_status, target)
        if not status.reachable or status.disabled:
            return status

        async with self._lock:
            await self._close_locked()
            websocket = await websockets.connect(target.websocket_url)
            await websocket.send(json.dumps({
                "type": "session_auth",
                "session_id": session_id,
                "session_token": session_token,
            }))
            self._websocket = websocket
            self._target = target
            self._session_id = session_id
            self._session_token = session_token
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
        elif event.type == "keyboard":
            outbound = {
                "type": "keyboard",
                "key": event.key,
                "ctrl": event.ctrl,
                "alt": event.alt,
                "shift": event.shift,
                "meta": event.meta,
            }
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

    async def arm_return_detector(self, return_edge: str, dwell_ms: int = 400) -> dict[str, Any]:
        async with self._lock:
            if self._target is None:
                raise RuntimeError("Remote handoff is not connected.")
            target, token = self._target, self._token
            sid, stok = self._session_id, self._session_token
        if token is not None:
            return await asyncio.to_thread(arm_remote_detector, target, token, return_edge, dwell_ms)
        if sid is not None and stok is not None:
            return await asyncio.to_thread(arm_remote_detector_session, target, sid, stok, return_edge, dwell_ms)
        raise RuntimeError("Remote handoff is not connected.")

    async def get_return_state(self) -> dict[str, Any]:
        async with self._lock:
            if self._target is None:
                raise RuntimeError("Remote handoff is not connected.")
            target, token = self._target, self._token
            sid, stok = self._session_id, self._session_token
        if token is not None:
            return await asyncio.to_thread(get_remote_detector_state, target, token)
        if sid is not None and stok is not None:
            return await asyncio.to_thread(get_remote_detector_state_session, target, sid, stok)
        raise RuntimeError("Remote handoff is not connected.")

    async def get_remote_screen_info(self) -> dict[str, Any]:
        async with self._lock:
            if self._target is None:
                raise RuntimeError("Remote handoff is not connected.")
            target, token = self._target, self._token
            sid, stok = self._session_id, self._session_token
        if token is not None:
            return await asyncio.to_thread(fetch_remote_screen_info, target, token)
        if sid is not None and stok is not None:
            return await asyncio.to_thread(fetch_remote_screen_info_session, target, sid, stok)
        raise RuntimeError("Remote handoff is not connected.")

    async def disarm_return_detector(self) -> dict[str, Any]:
        async with self._lock:
            if self._target is None:
                return {"ok": True}
            target, token = self._target, self._token
            sid, stok = self._session_id, self._session_token
            if token is None and sid is None:
                return {"ok": True}
        try:
            if token is not None:
                return await asyncio.to_thread(disarm_remote_detector, target, token)
            return await asyncio.to_thread(disarm_remote_detector_session, target, sid, stok)
        except RuntimeError:
            return {"ok": True}

    async def warp_cursor(self) -> dict[str, Any]:
        async with self._lock:
            if self._target is None:
                raise RuntimeError("Remote handoff is not connected.")
            target, token = self._target, self._token
            sid, stok = self._session_id, self._session_token
        if token is not None:
            return await asyncio.to_thread(warp_remote_cursor, target, token)
        if sid is not None and stok is not None:
            return await asyncio.to_thread(warp_remote_cursor_session, target, sid, stok)
        raise RuntimeError("Remote handoff is not connected.")

    async def _close_locked(self) -> None:
        websocket = self._websocket
        self._websocket = None
        self._target = None
        self._token = None
        self._session_id = None
        self._session_token = None
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


def get_remote_input_status(target: RemoteTarget, token: str, timeout: float = 2.0) -> dict[str, Any]:
    request = Request(target.input_status_url, headers={"X-Pairing-Token": token}, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def should_connect(status: RemoteStatus) -> bool:
    if not status.reachable or status.disabled:
        return False
    if status.input_events and not set(REQUIRED_REMOTE_EVENTS).issubset(set(status.input_events)):
        return False
    return True


def fetch_remote_screen_info(
    target: RemoteTarget, token: str, timeout: float = 2.0
) -> dict[str, Any]:
    request = Request(
        f"http://{target.host}:{target.port}/api/screen/info",
        headers={"X-Pairing-Token": token},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def arm_remote_detector(
    target: RemoteTarget, token: str, edge: str, dwell_ms: int = 400, timeout: float = 3.0
) -> dict[str, Any]:
    body = json.dumps({"edge": edge, "dwell_ms": dwell_ms, "zone_px": 5}).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/handoff/arm",
        data=body,
        headers={"X-Pairing-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def get_remote_detector_state(
    target: RemoteTarget, token: str, timeout: float = 2.0
) -> dict[str, Any]:
    request = Request(
        f"http://{target.host}:{target.port}/api/handoff/detector/state",
        headers={"X-Pairing-Token": token},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def disarm_remote_detector(
    target: RemoteTarget, token: str, timeout: float = 3.0
) -> dict[str, Any]:
    request = Request(
        f"http://{target.host}:{target.port}/api/handoff/disarm",
        data=b"",
        headers={"X-Pairing-Token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def fetch_remote_screen_info_session(
    target: RemoteTarget,
    session_id: str,
    session_token: str,
    timeout: float = 2.0,
) -> dict[str, Any]:
    body = json.dumps({"session_id": session_id, "session_token": session_token}).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/session/screen/info",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def arm_remote_detector_session(
    target: RemoteTarget,
    session_id: str,
    session_token: str,
    edge: str,
    dwell_ms: int = 400,
    timeout: float = 3.0,
) -> dict[str, Any]:
    body = json.dumps({
        "session_id": session_id,
        "session_token": session_token,
        "edge": edge,
        "dwell_ms": dwell_ms,
        "zone_px": 5,
    }).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/session/handoff/arm",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def get_remote_detector_state_session(
    target: RemoteTarget,
    session_id: str,
    session_token: str,
    timeout: float = 2.0,
) -> dict[str, Any]:
    body = json.dumps({"session_id": session_id, "session_token": session_token}).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/session/handoff/detector/state",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def disarm_remote_detector_session(
    target: RemoteTarget,
    session_id: str,
    session_token: str,
    timeout: float = 3.0,
) -> dict[str, Any]:
    body = json.dumps({"session_id": session_id, "session_token": session_token}).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/session/handoff/disarm",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def _extract_http_error_detail(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
        return json.loads(body).get("detail", body) or str(exc)
    except Exception:
        return str(exc)


def remote_request_pair(
    target: RemoteTarget,
    device_id: str,
    name: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    body = json.dumps({"device_id": device_id, "name": name}).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/discovery/request-pair",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(_extract_http_error_detail(exc)) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def remote_pair(
    target: RemoteTarget,
    code: str,
    device_id: str,
    name: str,
    permissions: Optional[dict[str, Any]],
    remember_device: bool,
    idle_timeout_seconds: int = 600,
    timeout: float = 10.0,
) -> dict[str, Any]:
    body = json.dumps({
        "code": code,
        "device_id": device_id,
        "name": name,
        "permissions": permissions or {},
        "remember_device": remember_device,
        "idle_timeout_seconds": idle_timeout_seconds,
    }).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/discovery/pair",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(_extract_http_error_detail(exc)) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def remote_trusted_reconnect(
    target: RemoteTarget,
    device_id: str,
    shared_secret: str,
    idle_timeout_seconds: int = 600,
    timeout: float = 5.0,
) -> dict[str, Any]:
    body = json.dumps({
        "device_id": device_id,
        "shared_secret": shared_secret,
        "idle_timeout_seconds": idle_timeout_seconds,
    }).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/pairing/trusted-reconnect",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(_extract_http_error_detail(exc)) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def warp_remote_cursor(
    target: RemoteTarget, token: str, timeout: float = 3.0
) -> dict[str, Any]:
    request = Request(
        f"http://{target.host}:{target.port}/api/handoff/warp-cursor",
        data=b"",
        headers={"X-Pairing-Token": token},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(_extract_http_error_detail(exc)) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def warp_remote_cursor_session(
    target: RemoteTarget,
    session_id: str,
    session_token: str,
    timeout: float = 3.0,
) -> dict[str, Any]:
    body = json.dumps({"session_id": session_id, "session_token": session_token}).encode("utf-8")
    request = Request(
        f"http://{target.host}:{target.port}/api/session/handoff/warp-cursor",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(_extract_http_error_detail(exc)) from exc
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
