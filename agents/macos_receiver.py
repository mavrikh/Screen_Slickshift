from __future__ import annotations

import argparse
import logging
import secrets
from dataclasses import dataclass
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, Query, WebSocket, WebSocketDisconnect

from app.pairing import PairingSessionBook
from app.protocol import parse_event, parse_message, protocol_capabilities


logger = logging.getLogger("macos_receiver")


@dataclass
class ReceiverState:
    token: str
    disabled: bool = False


state = ReceiverState(token="")
app = FastAPI(title="Screen Slickshift macOS Receiver")
_pyautogui_backend = None
pairing_session_book = PairingSessionBook()


@app.get("/")
async def index() -> dict:
    return receiver_index_payload()


@app.get("/api/status")
async def status() -> dict:
    return receiver_status_payload()


@app.get("/api/permissions")
async def permissions() -> dict:
    return receiver_permissions_payload()


def receiver_index_payload() -> dict[str, Any]:
    return {
        "app": "Screen Slickshift macOS Receiver",
        "role": "experimental_receiver",
        "screen_capture": "not_supported",
        "disabled": state.disabled,
        "input_allowed": not state.disabled,
        "capabilities": receiver_capabilities(),
        "auth": receiver_auth_description(),
    }


def receiver_status_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "role": "receiver",
        "disabled": state.disabled,
        "input_allowed": not state.disabled,
        "screen_capture": False,
        "capabilities": receiver_capabilities(),
        "auth": receiver_auth_description(),
        "protocol": protocol_capabilities(),
    }


def receiver_capabilities() -> dict[str, Any]:
    return {
        "receive_input": True,
        "mouse": True,
        "keyboard": False,
        "clipboard": False,
        "file_transfer": False,
        "screen_capture": False,
        "requires_accessibility_permission": True,
        "requires_screen_recording_permission": False,
    }


def receiver_auth_description() -> dict[str, Any]:
    return {
        "websocket": {
            "preferred": "first-message",
            "supported": ["first-message", "session-auth", "query-token"],
        },
        "http": {
            "preferred": "x-pairing-token-header",
            "supported": ["x-pairing-token-header", "query-token"],
        },
        "token_exposed": False,
    }


def receiver_permissions_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "platform": "macos",
        "permissions": {
            "accessibility": {
                "required": True,
                "purpose": "mouse control",
                "macos_path": "System Settings -> Privacy & Security -> Accessibility",
                "when_needed": "before receiving mouse input",
            },
            "screen_recording": {
                "required": False,
                "purpose": "not implemented",
                "macos_path": "not needed",
                "when_needed": "never in the current prototype",
            },
        },
        "emergency_stop": [
            "press Ctrl+C in the receiver terminal",
            "move the mouse to a screen corner for pyautogui failsafe",
        ],
    }


def startup_banner(host: str, port: int, token: str) -> str:
    return "\n".join(
        [
            "",
            "============================================================",
            "Experimental macOS Receiver",
            f"Listening on: http://{host}:{port}",
            f"Pairing token: {token}",
            "Accessibility permission may be required for mouse control.",
            "Screen Recording: not needed and not implemented.",
            "Emergency stop: press Ctrl+C or move mouse to a screen corner for pyautogui failsafe.",
            "============================================================",
            "",
        ]
    )


@app.post("/api/lockout")
async def lockout(
    token: str = Query(default=""),
    disabled: bool = Query(default=True),
    x_pairing_token: str = Header(default=""),
) -> dict:
    if not receiver_token_is_valid(x_pairing_token or token):
        return {"ok": False, "error": "invalid token"}
    state.disabled = disabled
    logger.warning("Emergency lockout set to %s.", state.disabled)
    return lockout_payload()


def lockout_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "disabled": state.disabled,
        "input_allowed": not state.disabled,
    }


def receiver_token_is_valid(token: Any) -> bool:
    return isinstance(token, str) and bool(token) and token == state.token


def apply_receiver_event(event, pyautogui_backend=None) -> bool:
    if state.disabled and event.type != "ping":
        return False

    if event.type == "mouse_move":
        pyautogui = pyautogui_backend or _pyautogui()
        pyautogui.moveRel(int(event.dx), int(event.dy), duration=0)
        return True
    if event.type == "mouse_button" and event.down:
        pyautogui = pyautogui_backend or _pyautogui()
        pyautogui.click(button=event.button)
        return True
    if event.type == "scroll":
        pyautogui = pyautogui_backend or _pyautogui()
        pyautogui.scroll(event.amount)
        return True
    if event.type == "ping":
        return True

    logger.warning("Ignoring unknown event type: %s", event.raw_type)
    return False


@app.websocket("/ws/input")
async def input_socket(websocket: WebSocket, token: str = Query(default="")) -> None:
    await websocket.accept()
    active_session_credentials: tuple[str, str] | None = None
    if token:
        if not receiver_token_is_valid(token):
            await websocket.close(code=1008, reason="Missing or invalid token.")
            return
    else:
        try:
            auth_payload = await websocket.receive_json()
        except Exception:
            await websocket.close(code=1008, reason="Missing or invalid token.")
            return
        auth = parse_message(auth_payload)
        if not auth.supported_version:
            await websocket.close(code=1008, reason="Unsupported protocol version.")
            return
        if auth.type == "auth" and receiver_token_is_valid(auth.payload.get("token")):
            pass
        elif auth.type == "session_auth":
            session_id = auth.payload.get("session_id")
            session_token = auth.payload.get("session_token")
            if not isinstance(session_id, str) or not isinstance(session_token, str):
                await websocket.close(code=1008, reason="Missing or invalid session credentials.")
                return
            if pairing_session_book.verify_session_permission(session_id, session_token, "mouse") is None:
                await websocket.close(code=1008, reason="Missing or invalid session credentials.")
                return
            active_session_credentials = (session_id, session_token)
        else:
            await websocket.close(code=1008, reason="Missing or invalid token.")
            return

    logger.info("Input WebSocket connected.")

    try:
        while True:
            message = await websocket.receive_json()
            event = parse_event(message)
            if active_session_credentials is not None and event.type != "ping":
                session_id, session_token = active_session_credentials
                if pairing_session_book.verify_session_permission(session_id, session_token, "mouse") is None:
                    await websocket.close(code=1008, reason="Mouse permission denied or session expired.")
                    return
            handled = apply_receiver_event(event)
            if handled and active_session_credentials is not None and event.type != "ping":
                session_id, session_token = active_session_credentials
                pairing_session_book.mark_session_active(session_id, session_token)
    except WebSocketDisconnect as exc:
        logger.info("Input WebSocket disconnected: %s", exc)
    except Exception as exc:
        logger.exception("Input WebSocket failed: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Experimental macOS receiver for local input sharing.")
    parser.add_argument("--host", default="0.0.0.0", help="Address to listen on. Use 127.0.0.1 for local-only testing.")
    parser.add_argument("--port", default=8770, type=int, help="Port to listen on.")
    parser.add_argument("--token", default="", help="Pairing token. If omitted, a temporary token is generated.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    state.token = args.token or secrets.token_urlsafe(24)

    print(startup_banner(args.host, args.port, state.token))

    uvicorn.run(app, host=args.host, port=args.port)


def _pyautogui():
    global _pyautogui_backend
    if _pyautogui_backend is not None:
        return _pyautogui_backend

    try:
        import pyautogui
    except Exception as exc:
        logger.exception("Desktop input backend is unavailable.")
        raise RuntimeError(
            "Desktop input backend is unavailable. Install pyautogui and grant Accessibility permission."
        ) from exc

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
    _pyautogui_backend = pyautogui
    return _pyautogui_backend


if __name__ == "__main__":
    main()
