from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from typing import Optional
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import websockets


VALID_BUTTONS = {"left", "right", "middle"}
AUTH_MODES = {"query-token", "first-message"}
PRESETS = {"wiggle", "click", "right-click", "middle-click", "scroll"}
REQUIRED_INPUT_EVENTS = ("mouse_move", "mouse_button", "scroll", "ping")
INTERNAL_TOOL_NOTICE = (
    "Internal testing prototype. Not the final user-facing sender; "
    "no global input capture is active."
)
COMMAND_HELP = """Commands:
  start | resume                  enable sending mouse input
  stop | pause | panic            stop sending mouse input
  move DX DY                      send relative mouse movement
  click [left|right|middle]       send a mouse click
  scroll AMOUNT                   send a scroll amount
  wait SECONDS                    pause scripted or interactive flow
  ping                            send a ping without starting control
  help                            show this command list
  quit | exit | q                 close the sender"""


@dataclass(frozen=True)
class SenderCommand:
    action: str
    message: Optional[dict] = None
    error: str = ""


@dataclass(frozen=True)
class ReceiverStatus:
    reachable: bool
    disabled: bool = False
    protocol_version: Optional[int] = None
    input_events: tuple[str, ...] = ()
    error: str = ""


@dataclass(frozen=True)
class SenderState:
    control_enabled: bool = False
    should_quit: bool = False
    outbound: Optional[dict] = None
    notice: str = ""


def parse_command(line: str) -> SenderCommand:
    parts = line.strip().split()
    if not parts:
        return SenderCommand(action="noop")

    command = parts[0].lower()
    if command in {"quit", "exit", "q"}:
        return SenderCommand(action="quit")
    if command in {"start", "resume"}:
        return SenderCommand(action="start")
    if command in {"stop", "pause", "panic"}:
        return SenderCommand(action="stop")
    if command in {"help", "?"}:
        return SenderCommand(action="help")
    if command == "ping":
        return SenderCommand(action="send", message={"type": "ping"})
    if command == "wait":
        if len(parts) != 2:
            return SenderCommand(action="error", error="Usage: wait SECONDS")
        try:
            seconds = float(parts[1])
        except ValueError:
            return SenderCommand(action="error", error="Wait value must be a number.")
        if seconds < 0:
            return SenderCommand(action="error", error="Wait value must be zero or greater.")
        return SenderCommand(action="wait", message={"seconds": seconds})
    if command == "move":
        if len(parts) != 3:
            return SenderCommand(action="error", error="Usage: move DX DY")
        try:
            dx = float(parts[1])
            dy = float(parts[2])
        except ValueError:
            return SenderCommand(action="error", error="Move values must be numbers.")
        return SenderCommand(action="send", message={"type": "mouse_move", "dx": dx, "dy": dy})
    if command == "click":
        button = parts[1].lower() if len(parts) > 1 else "left"
        if button not in VALID_BUTTONS:
            return SenderCommand(action="error", error="Button must be left, right, or middle.")
        return SenderCommand(action="send", message={"type": "mouse_button", "button": button, "down": True})
    if command == "scroll":
        if len(parts) != 2:
            return SenderCommand(action="error", error="Usage: scroll AMOUNT")
        try:
            amount = int(parts[1])
        except ValueError:
            return SenderCommand(action="error", error="Scroll amount must be an integer.")
        return SenderCommand(action="send", message={"type": "scroll", "amount": amount})

    return SenderCommand(action="error", error="Unknown command.")


def envelope_message(message: dict) -> dict:
    return {
        "version": 1,
        "payload": message,
    }


def preset_commands(preset: str, distance: int = 120, scroll_amount: int = -20) -> tuple[str, ...]:
    if preset == "wiggle":
        return (
            "start",
            f"move {distance} 0",
            f"move 0 {distance}",
            f"move {-distance} 0",
            f"move 0 {-distance}",
            "stop",
        )
    if preset == "click":
        return ("start", "click left", "stop")
    if preset == "right-click":
        return ("start", "click right", "stop")
    if preset == "middle-click":
        return ("start", "click middle", "stop")
    if preset == "scroll":
        return ("start", f"scroll {scroll_amount}", "stop")
    raise ValueError(f"Unknown preset: {preset}")


def get_receiver_status(host: str, port: int, timeout: float = 2.0) -> ReceiverStatus:
    url = f"http://{host}:{port}/api/status"
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as exc:
        return ReceiverStatus(reachable=False, error=str(exc))

    protocol = payload.get("protocol") if isinstance(payload, dict) else None
    if not isinstance(protocol, dict):
        protocol = {}
    input_events = protocol.get("input_events", ())
    if not isinstance(input_events, list):
        input_events = []

    return ReceiverStatus(
        reachable=True,
        disabled=bool(payload.get("disabled")) if isinstance(payload, dict) else False,
        protocol_version=protocol.get("version") if isinstance(protocol.get("version"), int) else None,
        input_events=tuple(str(event) for event in input_events),
    )


def describe_receiver_status(status: ReceiverStatus) -> str:
    if not status.reachable:
        return f"Receiver status unavailable: {status.error}"
    protocol = f"protocol v{status.protocol_version}" if status.protocol_version is not None else "protocol unknown"
    events = ", ".join(status.input_events) if status.input_events else "no advertised input events"
    disabled = "disabled" if status.disabled else "enabled"
    return f"Receiver status: {disabled}, {protocol}, events: {events}"


def websocket_url(host: str, port: int, path: str, token: str, auth_mode: str) -> str:
    clean_path = path if path.startswith("/") else f"/{path}"
    url = f"ws://{host}:{port}{clean_path}"
    if auth_mode == "query-token":
        return f"{url}?{urlencode({'token': token})}"
    return url


def auth_message(token: str, auth_mode: str) -> Optional[dict]:
    if auth_mode == "first-message":
        return {"type": "auth", "token": token}
    return None


def should_connect_after_status(
    status: ReceiverStatus,
    allow_disabled_receiver: bool = False,
) -> bool:
    if not status.reachable:
        return False
    if status.disabled and not allow_disabled_receiver:
        return False
    if status.input_events and not receiver_supports_manual_sender(status):
        return False
    return True


def receiver_supports_manual_sender(status: ReceiverStatus) -> bool:
    return set(REQUIRED_INPUT_EVENTS).issubset(set(status.input_events))


def apply_sender_command(
    state: SenderState,
    parsed: SenderCommand,
) -> SenderState:
    if parsed.action == "noop":
        return state
    if parsed.action == "quit":
        return SenderState(
            control_enabled=state.control_enabled,
            should_quit=True,
            notice="Stopped manual sender.",
        )
    if parsed.action == "start":
        return SenderState(control_enabled=True, notice="Control started.")
    if parsed.action == "stop":
        return SenderState(control_enabled=False, notice="Control stopped.")
    if parsed.action == "error":
        return SenderState(control_enabled=state.control_enabled, notice=parsed.error)
    if parsed.action == "help":
        return SenderState(control_enabled=state.control_enabled, notice=COMMAND_HELP)
    if parsed.action == "wait":
        return SenderState(control_enabled=state.control_enabled, outbound=parsed.message)
    if not state.control_enabled and parsed.message and parsed.message.get("type") != "ping":
        return SenderState(
            control_enabled=state.control_enabled,
            notice="Control is stopped. Type 'start' before sending input.",
        )
    return SenderState(
        control_enabled=state.control_enabled,
        outbound=parsed.message,
    )


async def send_scripted_commands(
    websocket,
    commands: tuple[str, ...],
    envelope: bool,
    command_delay: float,
) -> None:
    state = SenderState()
    for line in commands:
        state = apply_sender_command(state, parse_command(line))
        if state.notice:
            print(state.notice)
        if state.outbound is not None:
            if "seconds" in state.outbound:
                await asyncio.sleep(float(state.outbound["seconds"]))
                if command_delay:
                    await asyncio.sleep(command_delay)
                continue
            message = envelope_message(state.outbound) if envelope else state.outbound
            await websocket.send(json.dumps(message))
            if command_delay:
                await asyncio.sleep(command_delay)
        if state.should_quit:
            return


async def run_manual_sender(
    host: str,
    port: int,
    token: str,
    path: str = "/ws/input",
    auth_mode: str = "query-token",
    envelope: bool = False,
    skip_status_check: bool = False,
    allow_disabled_receiver: bool = False,
    commands: tuple[str, ...] = (),
    command_delay: float = 0,
) -> None:
    if auth_mode not in AUTH_MODES:
        raise ValueError(f"Unsupported auth mode: {auth_mode}")

    url = websocket_url(host, port, path, token, auth_mode)
    print(INTERNAL_TOOL_NOTICE)

    if not skip_status_check:
        status = await asyncio.to_thread(get_receiver_status, host, port)
        print(describe_receiver_status(status))
        if status.disabled and not allow_disabled_receiver:
            print("Receiver is disabled. Re-enable it locally or use --allow-disabled-receiver for diagnostics.")
        if status.input_events and not receiver_supports_manual_sender(status):
            print("Receiver does not advertise the input events required by the manual sender.")
        if not should_connect_after_status(status, allow_disabled_receiver):
            return

    async with websockets.connect(url) as websocket:
        initial_auth = auth_message(token, auth_mode)
        if initial_auth is not None:
            await websocket.send(json.dumps(initial_auth))
        print(f"Connected to receiver at {host}:{port}.")
        if commands:
            await send_scripted_commands(
                websocket,
                commands,
                envelope=envelope,
                command_delay=command_delay,
            )
            print("Scripted manual sender commands complete.")
            return

        print("Commands: start, stop, move DX DY, click [left|right|middle], scroll AMOUNT, wait SECONDS, ping, help, quit")
        print("Control starts stopped. Type 'start' before sending input. Type 'stop' or press Ctrl+C to halt.")
        state = SenderState()

        while True:
            try:
                line = await asyncio.to_thread(input, "slickshift> ")
            except (EOFError, KeyboardInterrupt):
                print("")
                print("Stopped manual sender.")
                return

            state = apply_sender_command(state, parse_command(line))
            if state.notice:
                print(state.notice)
            if state.outbound is not None:
                message = envelope_message(state.outbound) if envelope else state.outbound
                await websocket.send(json.dumps(message))
            if state.should_quit:
                return


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual Screen Slickshift sender prototype.")
    parser.add_argument("--host", required=True, help="Receiver host or IP address.")
    parser.add_argument("--port", default=8770, type=int, help="Receiver port.")
    parser.add_argument("--token", required=True, help="Receiver pairing token.")
    parser.add_argument("--path", default="/ws/input", help="Receiver WebSocket path.")
    parser.add_argument(
        "--auth-mode",
        choices=sorted(AUTH_MODES),
        default="query-token",
        help="How to authenticate the WebSocket.",
    )
    parser.add_argument("--envelope", action="store_true", help="Send protocol v1 payload envelopes.")
    parser.add_argument(
        "--preset",
        choices=sorted(PRESETS),
        help="Run a built-in non-interactive command preset.",
    )
    parser.add_argument("--distance", default=120, type=int, help="Distance for the wiggle preset.")
    parser.add_argument("--scroll", default=-20, type=int, help="Scroll amount for the scroll preset.")
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="Run one manual command non-interactively. May be repeated.",
    )
    parser.add_argument(
        "--command-delay",
        default=0,
        type=float,
        help="Delay in seconds after each scripted command.",
    )
    parser.add_argument("--skip-status-check", action="store_true", help="Skip the HTTP receiver status preflight.")
    parser.add_argument(
        "--allow-disabled-receiver",
        action="store_true",
        help="Connect even if /api/status reports emergency lockout disabled.",
    )
    args = parser.parse_args()
    commands = tuple(args.command)
    if args.preset:
        commands = preset_commands(args.preset, distance=args.distance, scroll_amount=args.scroll) + commands

    asyncio.run(
        run_manual_sender(
            args.host,
            args.port,
            args.token,
            path=args.path,
            auth_mode=args.auth_mode,
            envelope=args.envelope,
            skip_status_check=args.skip_status_check,
            allow_disabled_receiver=args.allow_disabled_receiver,
            commands=commands,
            command_delay=max(args.command_delay, 0),
        )
    )


if __name__ == "__main__":
    main()
