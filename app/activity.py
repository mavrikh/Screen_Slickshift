from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


_LOCK = threading.Lock()
_STARTED_AT = time.time()
_EVENTS: deque[dict[str, Any]] = deque(maxlen=80)
_COUNTERS: dict[str, Any] = {
    "sent": {"mouse_move": 0, "mouse_button": 0, "scroll": 0, "keyboard": 0, "ping": 0},
    "received": {"mouse_move": 0, "mouse_button": 0, "scroll": 0, "keyboard": 0, "ping": 0},
    "connections": {"opened": 0, "closed": 0, "errors": 0},
    "last_sent": None,
    "last_received": None,
}


def record_sent(event_type: str, detail: dict[str, Any] | None = None) -> None:
    _record("sent", event_type, detail)


def record_received(event_type: str, detail: dict[str, Any] | None = None) -> None:
    _record("received", event_type, detail)


def record_connection(kind: str, detail: dict[str, Any] | None = None) -> None:
    now = time.time()
    clean_kind = kind if kind in {"opened", "closed", "errors"} else "errors"
    event = {
        "time": now,
        "direction": "connection",
        "type": clean_kind,
        "detail": detail or {},
    }
    with _LOCK:
        _COUNTERS["connections"][clean_kind] += 1
        _EVENTS.append(event)


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            "started_at": _STARTED_AT,
            "now": time.time(),
            "counters": {
                "sent": dict(_COUNTERS["sent"]),
                "received": dict(_COUNTERS["received"]),
                "connections": dict(_COUNTERS["connections"]),
            },
            "last_sent": _copy_event(_COUNTERS["last_sent"]),
            "last_received": _copy_event(_COUNTERS["last_received"]),
            "events": [_copy_event(e) for e in list(_EVENTS)[-25:]],
        }


def _record(direction: str, event_type: str, detail: dict[str, Any] | None) -> None:
    now = time.time()
    clean_type = event_type if event_type in _COUNTERS[direction] else "ping"
    event = {
        "time": now,
        "direction": direction,
        "type": clean_type,
        "detail": _safe_detail(clean_type, detail or {}),
    }
    with _LOCK:
        _COUNTERS[direction][clean_type] += 1
        _COUNTERS[f"last_{direction}"] = event
        _EVENTS.append(event)


def _safe_detail(event_type: str, detail: dict[str, Any]) -> dict[str, Any]:
    if event_type == "mouse_move":
        return {"dx": int(detail.get("dx", 0)), "dy": int(detail.get("dy", 0))}
    if event_type == "mouse_button":
        return {"button": str(detail.get("button", "left")), "down": bool(detail.get("down", False))}
    if event_type == "scroll":
        return {"amount": int(detail.get("amount", 0))}
    if event_type == "keyboard":
        return {"forwarded": True}
    return {}


def _copy_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "time": event["time"],
        "direction": event["direction"],
        "type": event["type"],
        "detail": dict(event.get("detail") or {}),
    }
