from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable

import websockets


async def send_messages(url: str, messages: Iterable[dict], delay: float) -> None:
    async with websockets.connect(url) as websocket:
        for message in messages:
            await websocket.send(json.dumps(message))
            await asyncio.sleep(delay)


def wiggle_messages(distance: int) -> list[dict]:
    return [
        {"type": "mouse_move", "dx": distance, "dy": 0},
        {"type": "mouse_move", "dx": 0, "dy": distance},
        {"type": "mouse_move", "dx": -distance, "dy": 0},
        {"type": "mouse_move", "dx": 0, "dy": -distance},
    ]


def click_messages(button: str) -> list[dict]:
    return [{"type": "mouse_button", "button": button, "down": True}]


def scroll_messages(amount: int) -> list[dict]:
    return [{"type": "scroll", "amount": amount}]


def main() -> None:
    parser = argparse.ArgumentParser(description="Send test protocol input to a paired receiver.")
    parser.add_argument("--host", required=True, help="Receiver host or IP address.")
    parser.add_argument("--port", default=8770, type=int, help="Receiver port.")
    parser.add_argument("--token", required=True, help="Receiver pairing token.")
    parser.add_argument("--action", choices=["wiggle", "click", "right-click", "scroll"], default="wiggle")
    parser.add_argument("--distance", default=120, type=int, help="Wiggle distance in pixels.")
    parser.add_argument("--scroll", default=-20, type=int, help="Scroll amount for the scroll action.")
    parser.add_argument("--delay", default=0.15, type=float, help="Delay between messages.")
    args = parser.parse_args()

    url = f"ws://{args.host}:{args.port}/ws/input?token={args.token}"

    if args.action == "wiggle":
        messages = wiggle_messages(args.distance)
    elif args.action == "click":
        messages = click_messages("left")
    elif args.action == "right-click":
        messages = click_messages("right")
    else:
        messages = scroll_messages(args.scroll)

    asyncio.run(send_messages(url, messages, args.delay))
    print(f"Sent {args.action} test input to {args.host}:{args.port}.")


if __name__ == "__main__":
    main()
