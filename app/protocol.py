from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal


SUPPORTED_PROTOCOL_VERSION = 1
SUPPORTED_INPUT_EVENTS = ["mouse_move", "mouse_button", "scroll", "keyboard", "ping"]
LEGACY_INPUT_ALIASES = ["move", "click"]
SUPPORTED_ENVELOPE = "v1-payload"
MAX_MOUSE_DELTA = 5000
MAX_SCROLL_AMOUNT = 1000
MAX_KEY_LENGTH = 16
MouseButton = Literal["left", "right", "middle"]
ProtocolEventType = Literal[
    "mouse_move",
    "mouse_button",
    "scroll",
    "keyboard",
    "ping",
    "unknown",
]

# Keys the protocol accepts by name; single printable characters are also allowed.
_ALLOWED_NAMED_KEYS = frozenset({
    "enter", "tab", "backspace", "delete", "up", "down", "left", "right",
    "home", "end", "pageup", "pagedown", "insert", "escape", "esc", "space",
    "capslock", "numlock", "scrolllock",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
})


@dataclass(frozen=True)
class ProtocolEvent:
    type: ProtocolEventType
    version: int = SUPPORTED_PROTOCOL_VERSION
    dx: float = 0
    dy: float = 0
    button: MouseButton = "left"
    down: bool = True
    amount: int = 0
    key: str = ""
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False
    raw_type: str = ""
    supported_version: bool = True


@dataclass(frozen=True)
class ProtocolMessage:
    version: int
    type: str
    payload: dict[str, Any]
    supported_version: bool


def parse_event(message: dict[str, Any]) -> ProtocolEvent:
    parsed = parse_message(message)
    event_type = parsed.type
    payload = parsed.payload

    if not parsed.supported_version:
        return ProtocolEvent(
            type="unknown",
            version=parsed.version,
            raw_type=event_type,
            supported_version=False,
        )

    if event_type in {"mouse_move", "move"}:
        return ProtocolEvent(
            type="mouse_move",
            version=parsed.version,
            dx=_float(payload.get("dx"), min_value=-MAX_MOUSE_DELTA, max_value=MAX_MOUSE_DELTA),
            dy=_float(payload.get("dy"), min_value=-MAX_MOUSE_DELTA, max_value=MAX_MOUSE_DELTA),
            raw_type=event_type,
        )

    if event_type in {"mouse_button", "click"}:
        return ProtocolEvent(
            type="mouse_button",
            version=parsed.version,
            button=_button(payload.get("button")),
            down=_bool(payload.get("down"), default=True),
            raw_type=event_type,
        )

    if event_type == "scroll":
        return ProtocolEvent(
            type="scroll",
            version=parsed.version,
            amount=_int(payload.get("amount"), min_value=-MAX_SCROLL_AMOUNT, max_value=MAX_SCROLL_AMOUNT),
            raw_type=event_type,
        )

    if event_type == "keyboard":
        raw_key = str(payload.get("key", ""))[:MAX_KEY_LENGTH]
        key = raw_key if (len(raw_key) == 1 or raw_key.lower() in _ALLOWED_NAMED_KEYS) else ""
        return ProtocolEvent(
            type="keyboard",
            version=parsed.version,
            key=key,
            ctrl=_bool(payload.get("ctrl"), default=False),
            alt=_bool(payload.get("alt"), default=False),
            shift=_bool(payload.get("shift"), default=False),
            meta=_bool(payload.get("meta"), default=False),
            raw_type=event_type,
        )

    if event_type == "ping":
        return ProtocolEvent(type="ping", version=parsed.version, raw_type=event_type)

    return ProtocolEvent(type="unknown", version=parsed.version, raw_type=event_type)


def parse_message(message: dict[str, Any]) -> ProtocolMessage:
    """Normalize flat v1 messages and the future v1 payload envelope."""
    if not isinstance(message, dict):
        return ProtocolMessage(
            version=SUPPORTED_PROTOCOL_VERSION,
            type="unknown",
            payload={},
            supported_version=True,
        )

    version = _int(message.get("version"), default=SUPPORTED_PROTOCOL_VERSION)
    payload = message.get("payload")
    if not isinstance(payload, dict):
        payload = message

    return ProtocolMessage(
        version=version,
        type=str(payload.get("type", message.get("type", "unknown"))),
        payload=payload,
        supported_version=version == SUPPORTED_PROTOCOL_VERSION,
    )


def protocol_capabilities() -> dict[str, Any]:
    return {
        "version": SUPPORTED_PROTOCOL_VERSION,
        "input_events": list(SUPPORTED_INPUT_EVENTS),
        "legacy_aliases": list(LEGACY_INPUT_ALIASES),
        "envelope": SUPPORTED_ENVELOPE,
        "limits": {
            "max_mouse_delta": MAX_MOUSE_DELTA,
            "max_scroll_amount": MAX_SCROLL_AMOUNT,
        },
    }


def _float(
    value: Any,
    min_value: float,
    max_value: float,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(result):
        return 0
    return min(max(result, min_value), max_value)


def _int(
    value: Any,
    default: int = 0,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    if min_value is not None:
        result = max(result, min_value)
    if max_value is not None:
        result = min(result, max_value)
    return result


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
