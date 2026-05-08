from agents.send_test_input import envelope_messages
from agents.macos_receiver import app as macos_receiver_app
from app.protocol import (
    MAX_MOUSE_DELTA,
    MAX_SCROLL_AMOUNT,
    SUPPORTED_PROTOCOL_VERSION,
    parse_event,
    parse_message,
    protocol_capabilities,
)
from fastapi.testclient import TestClient


def test_parse_mouse_move() -> None:
    event = parse_event({"type": "mouse_move", "dx": "12.5", "dy": -4})

    assert event.type == "mouse_move"
    assert event.version == SUPPORTED_PROTOCOL_VERSION
    assert event.dx == 12.5
    assert event.dy == -4


def test_protocol_capabilities_reports_supported_subset() -> None:
    assert protocol_capabilities() == {
        "version": SUPPORTED_PROTOCOL_VERSION,
        "input_events": ["mouse_move", "mouse_button", "scroll", "ping"],
        "legacy_aliases": ["move", "click"],
        "envelope": "v1-payload",
        "limits": {
            "max_mouse_delta": MAX_MOUSE_DELTA,
            "max_scroll_amount": MAX_SCROLL_AMOUNT,
        },
    }


def test_parse_v1_payload_envelope() -> None:
    event = parse_event(
        {
            "version": 1,
            "payload": {
                "type": "mouse_move",
                "dx": 3,
                "dy": -2,
            },
        }
    )

    assert event.type == "mouse_move"
    assert event.version == 1
    assert event.dx == 3
    assert event.dy == -2


def test_parse_message_supports_v1_auth_payload_envelope() -> None:
    message = parse_message(
        {
            "version": 1,
            "payload": {
                "type": "session_auth",
                "session_id": "session-id",
                "session_token": "session-token",
            },
        }
    )

    assert message.version == 1
    assert message.type == "session_auth"
    assert message.supported_version is True
    assert message.payload["session_id"] == "session-id"


def test_parse_unsupported_version_fails_closed() -> None:
    event = parse_event(
        {
            "version": 2,
            "payload": {
                "type": "mouse_move",
                "dx": 3,
                "dy": -2,
            },
        }
    )

    assert event.type == "unknown"
    assert event.version == 2
    assert event.raw_type == "mouse_move"
    assert event.supported_version is False


def test_parse_mouse_move_rejects_non_finite_values() -> None:
    event = parse_event({"type": "mouse_move", "dx": "NaN", "dy": "Infinity"})

    assert event.type == "mouse_move"
    assert event.dx == 0
    assert event.dy == 0


def test_parse_mouse_move_clamps_large_values() -> None:
    event = parse_event({"type": "mouse_move", "dx": MAX_MOUSE_DELTA + 1, "dy": -MAX_MOUSE_DELTA - 1})

    assert event.dx == MAX_MOUSE_DELTA
    assert event.dy == -MAX_MOUSE_DELTA


def test_parse_scroll_clamps_large_values() -> None:
    down = parse_event({"type": "scroll", "amount": MAX_SCROLL_AMOUNT + 1})
    up = parse_event({"type": "scroll", "amount": -MAX_SCROLL_AMOUNT - 1})

    assert down.amount == MAX_SCROLL_AMOUNT
    assert up.amount == -MAX_SCROLL_AMOUNT


def test_sender_can_wrap_messages_in_v1_envelopes() -> None:
    messages = envelope_messages(
        [
            {"type": "mouse_move", "dx": 4, "dy": -2},
            {"type": "ping"},
        ]
    )

    assert messages == [
        {
            "version": 1,
            "payload": {"type": "mouse_move", "dx": 4, "dy": -2},
        },
        {
            "version": 1,
            "payload": {"type": "ping"},
        },
    ]


def test_macos_receiver_status_reports_protocol_capabilities() -> None:
    response = TestClient(macos_receiver_app).get("/api/status")

    assert response.status_code == 200
    protocol = response.json()["protocol"]
    assert protocol["version"] == SUPPORTED_PROTOCOL_VERSION
    assert protocol["input_events"] == ["mouse_move", "mouse_button", "scroll", "ping"]
    assert protocol["legacy_aliases"] == ["move", "click"]
    assert protocol["envelope"] == "v1-payload"
    assert protocol["limits"] == {
        "max_mouse_delta": MAX_MOUSE_DELTA,
        "max_scroll_amount": MAX_SCROLL_AMOUNT,
    }


def test_parse_legacy_move_alias() -> None:
    event = parse_event({"type": "move", "dx": 3, "dy": 2})

    assert event.type == "mouse_move"
    assert event.raw_type == "move"


def test_parse_mouse_button() -> None:
    event = parse_event({"type": "mouse_button", "button": "right", "down": True})

    assert event.type == "mouse_button"
    assert event.button == "right"
    assert event.down is True


def test_parse_legacy_click_alias() -> None:
    event = parse_event({"type": "click", "button": "middle"})

    assert event.type == "mouse_button"
    assert event.button == "middle"
    assert event.down is True


def test_parse_scroll() -> None:
    event = parse_event({"type": "scroll", "amount": "40"})

    assert event.type == "scroll"
    assert event.amount == 40


def test_unknown_button_falls_back_to_left() -> None:
    event = parse_event({"type": "mouse_button", "button": "side"})

    assert event.button == "left"
