from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, WebSocket, status

from app.config import get_or_create_pairing_token


TOKEN_HEADER = "X-Pairing-Token"


def token_is_valid(token: Optional[str]) -> bool:
    expected = get_or_create_pairing_token()
    return bool(token and token == expected)


def verify_token(x_pairing_token: Optional[str] = Header(default=None)) -> None:
    if not token_is_valid(x_pairing_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid pairing token.",
        )


def verify_token_value(token: Optional[str]) -> None:
    if not token_is_valid(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid pairing token.",
        )


async def verify_websocket_token(websocket: WebSocket, token: Optional[str]) -> bool:
    if not token_is_valid(token):
        await websocket.close(code=1008, reason="Missing or invalid pairing token.")
        return False
    return True
