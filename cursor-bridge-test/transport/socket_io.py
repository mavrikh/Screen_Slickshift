"""
cursor-bridge-test -- TCP transport layer skeleton.

Uses a persistent connection with a 4-byte length-prefix framing protocol:
    [uint32 big-endian payload length][payload bytes]

This eliminates the per-request overhead of HTTP POST and keeps latency under
the 5ms LAN budget. See brainstorm doc Section 3G for the full rationale.

Step 3 of the build order wires this up with real socket code.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class TcpTransport:
    """Persistent TCP socket transport for cross-machine mouse event delivery."""

    def start_server(self, host: str, port: int) -> None:
        """Bind and listen on (host, port). Accept exactly one peer connection."""
        raise NotImplementedError

    def connect_to_peer(self, host: str, port: int) -> None:
        """Open a persistent connection to the server at (host, port)."""
        raise NotImplementedError

    def send(self, payload: dict[str, Any]) -> None:
        """
        Serialize payload to JSON, frame with 4-byte length prefix, and send.

        All coordinate values in payload must already be normalized (0.0-1.0).
        Raw pixel values are never transmitted directly -- see architecture invariants
        in Slickshift CLAUDE.md.
        """
        raise NotImplementedError

    def receive(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """
        Start receiving loop. Calls callback with each decoded payload dict.

        Runs in a background thread so the UI event loop is not blocked.
        """
        raise NotImplementedError

    def send_heartbeat(self) -> None:
        """Send a lightweight heartbeat frame so the peer resets its dead-man timer."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the socket cleanly. Both sides must call this on shutdown."""
        raise NotImplementedError
