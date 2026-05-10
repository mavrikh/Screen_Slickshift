from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from app.activity import record_connection, record_received
from app.input_control import click_mouse, mousedown_mouse, mouseup_mouse, move_mouse, press_key, scroll_mouse
from app.protocol import parse_event


logger = logging.getLogger(__name__)


MouseAuthorizer = Callable[[], bool]
SessionActivityMarker = Callable[[], None]


async def handle_touchpad_socket(
    websocket: WebSocket,
    accepted: bool = False,
    authorize_mouse: Optional[MouseAuthorizer] = None,
    authorize_keyboard: Optional[MouseAuthorizer] = None,
    mark_session_active: Optional[SessionActivityMarker] = None,
) -> None:
    if not accepted:
        await websocket.accept()
    logger.info("Touchpad WebSocket connected.")
    record_connection("opened")

    try:
        while True:
            message = await websocket.receive_json()
            event = parse_event(message)

            if event.type == "mouse_move":
                if not await _ensure_mouse_allowed(websocket, authorize_mouse):
                    return
                move_mouse(event.dx, event.dy)
                record_received("mouse_move", {"dx": event.dx, "dy": event.dy})
                _mark_session_active(mark_session_active)
            elif event.type == "mouse_button":
                if not await _ensure_mouse_allowed(websocket, authorize_mouse):
                    return
                if event.down:
                    mousedown_mouse(event.button)
                else:
                    mouseup_mouse(event.button)
                record_received("mouse_button", {"button": event.button, "down": event.down})
                _mark_session_active(mark_session_active)
            elif event.type == "scroll":
                if not await _ensure_mouse_allowed(websocket, authorize_mouse):
                    return
                scroll_mouse(event.amount)
                record_received("scroll", {"amount": event.amount})
                _mark_session_active(mark_session_active)
            elif event.type == "keyboard":
                if authorize_keyboard is not None and not authorize_keyboard():
                    continue
                if event.key:
                    press_key(event.key, ctrl=event.ctrl, alt=event.alt, shift=event.shift, meta=event.meta)
                    record_received("keyboard", {"forwarded": True})
                    _mark_session_active(mark_session_active)
            elif event.type == "ping":
                record_received("ping")
                continue
            else:
                logger.warning("Ignoring unknown WebSocket event type: %s", event.raw_type)
    except WebSocketDisconnect as exc:
        logger.info("Touchpad WebSocket disconnected: %s", exc)
        record_connection("closed")
    except Exception as exc:
        logger.exception("Touchpad WebSocket handler failed: %s", exc)
        record_connection("errors")


async def _ensure_mouse_allowed(
    websocket: WebSocket,
    authorize_mouse: Optional[MouseAuthorizer],
) -> bool:
    if authorize_mouse is None:
        return True
    if authorize_mouse():
        return True

    await websocket.close(code=1008, reason="Mouse permission denied or session expired.")
    return False


def _mark_session_active(mark_session_active: Optional[SessionActivityMarker]) -> None:
    if mark_session_active is not None:
        mark_session_active()
