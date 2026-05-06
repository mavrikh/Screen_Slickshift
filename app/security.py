from __future__ import annotations

from fastapi import Header, HTTPException, WebSocket, status

from app.config import get_or_create_pairing_token


TOKEN_HEADER = "X-Pairing-Token"


def verify_token(x_pairing_token: str | None = Header(default=None)) -> None:
    expected = get_or_create_pairing_token()
    if not x_pairing_token or x_pairing_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid pairing token.",
        )


def verify_token_value(token: str | None) -> None:
    expected = get_or_create_pairing_token()
    if not token or token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid pairing token.",
        )


async def verify_websocket_token(websocket: WebSocket, token: str | None) -> bool:
    expected = get_or_create_pairing_token()
    if not token or token != expected:
        await websocket.close(code=1008, reason="Missing or invalid pairing token.")
        return False
    return True
