from __future__ import annotations

import logging

from fastapi import WebSocket, WebSocketDisconnect

from app.input_control import click_mouse, move_mouse, scroll_mouse
from app.protocol import parse_event


logger = logging.getLogger(__name__)


async def handle_touchpad_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("Touchpad WebSocket connected.")

    try:
        while True:
            message = await websocket.receive_json()
            event = parse_event(message)

            if event.type == "mouse_move":
                move_mouse(event.dx, event.dy)
            elif event.type == "mouse_button" and event.down:
                click_mouse(event.button)
            elif event.type == "scroll":
                scroll_mouse(event.amount)
            elif event.type == "ping":
                continue
            else:
                logger.warning("Ignoring unknown WebSocket event: %s", message)
    except WebSocketDisconnect as exc:
        logger.info("Touchpad WebSocket disconnected: %s", exc)
    except Exception as exc:
        logger.exception("Touchpad WebSocket handler failed: %s", exc)
