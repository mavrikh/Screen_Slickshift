from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from app.input_control import click_mouse, move_mouse, scroll_mouse
from app.protocol import parse_event


logger = logging.getLogger(__name__)


MouseAuthorizer = Callable[[], bool]
SessionActivityMarker = Callable[[], None]


async def handle_touchpad_socket(
    websocket: WebSocket,
    accepted: bool = False,
    authorize_mouse: Optional[MouseAuthorizer] = None,
    mark_session_active: Optional[SessionActivityMarker] = None,
) -> None:
    if not accepted:
        await websocket.accept()
    logger.info("Touchpad WebSocket connected.")

    try:
        while True:
            message = await websocket.receive_json()
            event = parse_event(message)

            if event.type == "mouse_move":
                if not await _ensure_mouse_allowed(websocket, authorize_mouse):
                    return
                move_mouse(event.dx, event.dy)
                _mark_session_active(mark_session_active)
            elif event.type == "mouse_button" and event.down:
                if not await _ensure_mouse_allowed(websocket, authorize_mouse):
                    return
                click_mouse(event.button)
                _mark_session_active(mark_session_active)
            elif event.type == "scroll":
                if not await _ensure_mouse_allowed(websocket, authorize_mouse):
                    return
                scroll_mouse(event.amount)
                _mark_session_active(mark_session_active)
            elif event.type == "ping":
                continue
            else:
                logger.warning("Ignoring unknown WebSocket event type: %s", event.raw_type)
    except WebSocketDisconnect as exc:
        logger.info("Touchpad WebSocket disconnected: %s", exc)
    except Exception as exc:
        logger.exception("Touchpad WebSocket handler failed: %s", exc)


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
