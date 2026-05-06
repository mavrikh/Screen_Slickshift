from __future__ import annotations

import argparse
import logging
import secrets
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from app.protocol import parse_event


logger = logging.getLogger("macos_receiver")


@dataclass
class ReceiverState:
    token: str
    disabled: bool = False


state = ReceiverState(token="")
app = FastAPI(title="Screen Slickshift macOS Receiver")
_pyautogui_backend = None


@app.get("/")
async def index() -> dict:
    return {
        "app": "Screen Slickshift macOS Receiver",
        "role": "experimental_receiver",
        "screen_capture": "not_supported",
        "disabled": state.disabled,
    }


@app.get("/api/status")
async def status() -> dict:
    return {
        "ok": True,
        "role": "receiver",
        "disabled": state.disabled,
        "screen_capture": False,
    }


@app.post("/api/lockout")
async def lockout(token: str = Query(default=""), disabled: bool = Query(default=True)) -> dict:
    if token != state.token:
        return {"ok": False, "error": "invalid token"}
    state.disabled = disabled
    logger.warning("Emergency lockout set to %s.", state.disabled)
    return {"ok": True, "disabled": state.disabled}


@app.websocket("/ws/input")
async def input_socket(websocket: WebSocket, token: str = Query(default="")) -> None:
    if token != state.token:
        await websocket.close(code=1008, reason="Missing or invalid token.")
        return

    await websocket.accept()
    logger.info("Input WebSocket connected.")

    try:
        while True:
            message = await websocket.receive_json()
            event = parse_event(message)

            if state.disabled and event.type != "ping":
                continue

            if event.type == "mouse_move":
                pyautogui = _pyautogui()
                pyautogui.moveRel(int(event.dx), int(event.dy), duration=0)
            elif event.type == "mouse_button" and event.down:
                pyautogui = _pyautogui()
                pyautogui.click(button=event.button)
            elif event.type == "scroll":
                pyautogui = _pyautogui()
                pyautogui.scroll(event.amount)
            elif event.type == "ping":
                continue
            else:
                logger.warning("Ignoring unknown event: %s", message)
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

    print("")
    print("============================================================")
    print("Experimental macOS Receiver")
    print(f"Listening on: http://{args.host}:{args.port}")
    print(f"Pairing token: {state.token}")
    print("Screen capture: disabled/not implemented")
    print("Press Ctrl+C to stop. Move mouse to a screen corner for pyautogui failsafe.")
    print("============================================================")
    print("")

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
