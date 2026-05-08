import json
from urllib.error import URLError

from agents import manual_sender
from agents.manual_sender import (
    COMMAND_HELP,
    INTERNAL_TOOL_NOTICE,
    ReceiverStatus,
    SenderState,
    apply_sender_command,
    auth_message,
    describe_receiver_status,
    envelope_message,
    get_receiver_status,
    parse_command,
    preset_commands,
    receiver_supports_manual_sender,
    should_connect_after_status,
    websocket_url,
)


def test_parse_start_command() -> None:
    command = parse_command("start")

    assert command.action == "start"
    assert command.message is None


def test_internal_tool_notice_marks_sender_as_prototype() -> None:
    assert "Internal testing prototype" in INTERNAL_TOOL_NOTICE
    assert "Not the final user-facing sender" in INTERNAL_TOOL_NOTICE
    assert "no global input capture" in INTERNAL_TOOL_NOTICE


def test_parse_stop_aliases() -> None:
    assert parse_command("stop").action == "stop"
    assert parse_command("pause").action == "stop"
    assert parse_command("panic").action == "stop"


def test_parse_move_command() -> None:
    command = parse_command("move 12.5 -4")

    assert command.action == "send"
    assert command.message == {"type": "mouse_move", "dx": 12.5, "dy": -4.0}


def test_parse_move_rejects_bad_values() -> None:
    command = parse_command("move nope 4")

    assert command.action == "error"
    assert command.error == "Move values must be numbers."


def test_parse_click_defaults_to_left() -> None:
    command = parse_command("click")

    assert command.action == "send"
    assert command.message == {"type": "mouse_button", "button": "left", "down": True}


def test_parse_click_accepts_known_button() -> None:
    command = parse_command("click right")

    assert command.action == "send"
    assert command.message == {"type": "mouse_button", "button": "right", "down": True}


def test_parse_click_rejects_unknown_button() -> None:
    command = parse_command("click side")

    assert command.action == "error"
    assert command.error == "Button must be left, right, or middle."


def test_parse_scroll_command() -> None:
    command = parse_command("scroll -40")

    assert command.action == "send"
    assert command.message == {"type": "scroll", "amount": -40}


def test_parse_wait_command() -> None:
    command = parse_command("wait 0.25")

    assert command.action == "wait"
    assert command.message == {"seconds": 0.25}


def test_parse_wait_rejects_bad_value() -> None:
    command = parse_command("wait nope")

    assert command.action == "error"
    assert command.error == "Wait value must be a number."


def test_parse_wait_rejects_negative_value() -> None:
    command = parse_command("wait -1")

    assert command.action == "error"
    assert command.error == "Wait value must be zero or greater."


def test_parse_quit_aliases() -> None:
    assert parse_command("quit").action == "quit"
    assert parse_command("exit").action == "quit"
    assert parse_command("q").action == "quit"


def test_parse_help_aliases() -> None:
    assert parse_command("help").action == "help"
    assert parse_command("?").action == "help"


def test_apply_help_command_returns_command_help() -> None:
    state = apply_sender_command(SenderState(control_enabled=True), parse_command("help"))

    assert state.control_enabled is True
    assert state.outbound is None
    assert state.notice == COMMAND_HELP


def test_apply_start_command_enables_control() -> None:
    state = apply_sender_command(SenderState(), parse_command("start"))

    assert state.control_enabled is True
    assert state.notice == "Control started."


def test_apply_stop_command_disables_control() -> None:
    state = apply_sender_command(SenderState(control_enabled=True), parse_command("stop"))

    assert state.control_enabled is False
    assert state.notice == "Control stopped."


def test_apply_quit_command_marks_done() -> None:
    state = apply_sender_command(SenderState(control_enabled=True), parse_command("quit"))

    assert state.should_quit is True
    assert state.control_enabled is True
    assert state.notice == "Stopped manual sender."


def test_apply_input_command_requires_started_control() -> None:
    state = apply_sender_command(SenderState(), parse_command("move 1 2"))

    assert state.outbound is None
    assert state.notice == "Control is stopped. Type 'start' before sending input."


def test_apply_ping_command_does_not_require_started_control() -> None:
    state = apply_sender_command(SenderState(), parse_command("ping"))

    assert state.outbound == {"type": "ping"}


def test_apply_wait_command_does_not_require_started_control() -> None:
    state = apply_sender_command(SenderState(), parse_command("wait 0.25"))

    assert state.outbound == {"seconds": 0.25}


def test_apply_input_command_after_start_returns_outbound_message() -> None:
    state = apply_sender_command(SenderState(control_enabled=True), parse_command("move 1 2"))

    assert state.control_enabled is True
    assert state.outbound == {"type": "mouse_move", "dx": 1.0, "dy": 2.0}


def test_envelope_message_wraps_v1_payload() -> None:
    assert envelope_message({"type": "ping"}) == {
        "version": 1,
        "payload": {"type": "ping"},
    }


def test_wiggle_preset_commands() -> None:
    assert preset_commands("wiggle", distance=40) == (
        "start",
        "move 40 0",
        "move 0 40",
        "move -40 0",
        "move 0 -40",
        "stop",
    )


def test_click_preset_commands() -> None:
    assert preset_commands("click") == ("start", "click left", "stop")


def test_right_click_preset_commands() -> None:
    assert preset_commands("right-click") == ("start", "click right", "stop")


def test_middle_click_preset_commands() -> None:
    assert preset_commands("middle-click") == ("start", "click middle", "stop")


def test_scroll_preset_commands() -> None:
    assert preset_commands("scroll", scroll_amount=-40) == ("start", "scroll -40", "stop")


def test_unknown_preset_raises() -> None:
    try:
        preset_commands("unknown")
    except ValueError as exc:
        assert str(exc) == "Unknown preset: unknown"
    else:
        raise AssertionError("Expected unknown preset to raise ValueError.")


def test_websocket_url_uses_query_token_for_experimental_receiver() -> None:
    url = websocket_url("mac.local", 8770, "/ws/input", "token with spaces", "query-token")

    assert url == "ws://mac.local:8770/ws/input?token=token+with+spaces"


def test_websocket_url_omits_token_for_first_message_auth() -> None:
    url = websocket_url("mac.local", 8765, "ws/touchpad", "secret-token", "first-message")

    assert url == "ws://mac.local:8765/ws/touchpad"


def test_auth_message_for_first_message_mode() -> None:
    assert auth_message("secret-token", "first-message") == {
        "type": "auth",
        "token": "secret-token",
    }


def test_auth_message_for_query_token_mode() -> None:
    assert auth_message("secret-token", "query-token") is None


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_get_receiver_status_reads_protocol_capabilities(monkeypatch) -> None:
    def fake_urlopen(url: str, timeout: float):
        assert url == "http://mac.local:8770/api/status"
        assert timeout == 2.0
        return FakeResponse(
            {
                "disabled": False,
                "protocol": {
                    "version": 1,
                    "input_events": ["mouse_move", "scroll"],
                },
            }
        )

    monkeypatch.setattr(manual_sender, "urlopen", fake_urlopen)

    status = get_receiver_status("mac.local", 8770)

    assert status == ReceiverStatus(
        reachable=True,
        disabled=False,
        protocol_version=1,
        input_events=("mouse_move", "scroll"),
    )


def test_get_receiver_status_reports_unreachable(monkeypatch) -> None:
    def fake_urlopen(url: str, timeout: float):
        raise URLError("offline")

    monkeypatch.setattr(manual_sender, "urlopen", fake_urlopen)

    status = get_receiver_status("mac.local", 8770)

    assert status.reachable is False
    assert "offline" in status.error


def test_describe_receiver_status() -> None:
    summary = describe_receiver_status(
        ReceiverStatus(
            reachable=True,
            disabled=True,
            protocol_version=1,
            input_events=("mouse_move", "ping"),
        )
    )

    assert summary == "Receiver status: disabled, protocol v1, events: mouse_move, ping"


def test_describe_unreachable_receiver_status() -> None:
    summary = describe_receiver_status(ReceiverStatus(reachable=False, error="offline"))

    assert summary == "Receiver status unavailable: offline"


def test_should_connect_after_enabled_reachable_status() -> None:
    assert (
        should_connect_after_status(
            ReceiverStatus(
                reachable=True,
                disabled=False,
                input_events=("mouse_move", "mouse_button", "scroll", "ping"),
            )
        )
        is True
    )


def test_should_not_connect_after_unreachable_status() -> None:
    assert should_connect_after_status(ReceiverStatus(reachable=False, error="offline")) is False


def test_should_not_connect_after_disabled_status_by_default() -> None:
    assert should_connect_after_status(ReceiverStatus(reachable=True, disabled=True)) is False


def test_should_connect_after_disabled_status_when_allowed() -> None:
    assert (
        should_connect_after_status(
            ReceiverStatus(
                reachable=True,
                disabled=True,
                input_events=("mouse_move", "mouse_button", "scroll", "ping"),
            ),
            allow_disabled_receiver=True,
        )
        is True
    )


def test_receiver_supports_manual_sender_when_all_events_advertised() -> None:
    status = ReceiverStatus(
        reachable=True,
        input_events=("mouse_move", "mouse_button", "scroll", "ping"),
    )

    assert receiver_supports_manual_sender(status) is True


def test_receiver_does_not_support_manual_sender_when_event_is_missing() -> None:
    status = ReceiverStatus(
        reachable=True,
        input_events=("mouse_move", "scroll", "ping"),
    )

    assert receiver_supports_manual_sender(status) is False
    assert should_connect_after_status(status) is False


def test_should_connect_when_receiver_does_not_advertise_events() -> None:
    status = ReceiverStatus(reachable=True, input_events=())

    assert should_connect_after_status(status) is True
