from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


MouseButton = Literal["left", "right", "middle"]
ProtocolEventType = Literal[
    "mouse_move",
    "mouse_button",
    "scroll",
    "ping",
    "unknown",
]


@dataclass(frozen=True)
class ProtocolEvent:
    type: ProtocolEventType
    dx: float = 0
    dy: float = 0
    button: MouseButton = "left"
    down: bool = True
    amount: int = 0
    raw_type: str = ""


def parse_event(message: dict[str, Any]) -> ProtocolEvent:
    event_type = str(message.get("type", "unknown"))

    if event_type in {"mouse_move", "move"}:
        return ProtocolEvent(
            type="mouse_move",
            dx=_float(message.get("dx")),
            dy=_float(message.get("dy")),
            raw_type=event_type,
        )

    if event_type in {"mouse_button", "click"}:
        return ProtocolEvent(
            type="mouse_button",
            button=_button(message.get("button")),
            down=_bool(message.get("down"), default=True),
            raw_type=event_type,
        )

    if event_type == "scroll":
        return ProtocolEvent(
            type="scroll",
            amount=_int(message.get("amount")),
            raw_type=event_type,
        )

    if event_type == "ping":
        return ProtocolEvent(type="ping", raw_type=event_type)

    return ProtocolEvent(type="unknown", raw_type=event_type)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "down"}
    return bool(value)


def _button(value: Any) -> MouseButton:
    if value in {"left", "right", "middle"}:
        return value
    return "left"
