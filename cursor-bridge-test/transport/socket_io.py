"""
cursor-bridge-test -- TCP transport layer.

Framing protocol:
    [uint32 big-endian payload length][UTF-8 JSON payload]

All socket I/O runs on a background thread. Received messages are delivered to
the Qt main thread via a pyqtSignal -- never call Qt widgets directly from the
receive thread.

PLAINTEXT WARNING: This transport sends data unencrypted over the LAN. It is
suitable for a two-machine dev test on a trusted private network only. Encryption
is deferred -- see brainstorm doc Section 3I and the deferred items in Section 6.

No reconnection logic is implemented here. That is Step 7 (failsafes).
"""

from __future__ import annotations

import json
import logging
import platform
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

import pyautogui
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QScreen
from PyQt6.QtWidgets import QApplication

import config

logger = logging.getLogger(__name__)

# Struct format for the 4-byte big-endian length prefix.
_FRAME_HEADER = struct.Struct("!I")  # unsigned int, network (big-endian) byte order

# App version for the test app. Separate from main Slickshift versioning.
_TEST_APP_VERSION = "0.0001"


def _build_hello_payload() -> dict[str, Any]:
    """
    Construct the hello payload this machine sends at handshake time.

    Fields:
        app_version     -- hardcoded test app version
        machine_name    -- socket.gethostname()
        platform        -- platform.system() ("Darwin" / "Windows" / "Linux")
        screen_logical  -- (width, height) in logical pixels via pyautogui.size()
        screen_physical -- (width, height) in physical pixels via QScreen
        dpi_scale       -- ratio of physical to logical (1.0 on non-Retina, 2.0 on Retina, varies on Windows)
        role_preference -- "either" (Step 5 introduces real role negotiation)
    """
    logical_w, logical_h = pyautogui.size()

    # Physical pixel size from Qt's primary screen. QScreen.physicalSize() returns
    # millimetre dimensions, not pixel counts, so we use geometry() for physical
    # pixels on Qt6 -- actually availableGeometry gives logical. We use
    # QScreen.size() (in physical pixels on high-DPI setups when devicePixelRatio is set).
    # Qt exposes physical size in dots via QScreen.physicalDotsPerInch() and size().
    # The reliable way: logical size * devicePixelRatio.
    screen: QScreen | None = QApplication.primaryScreen()
    if screen is not None:
        dpi_ratio: float = screen.devicePixelRatio()
        phys_w = int(logical_w * dpi_ratio)
        phys_h = int(logical_h * dpi_ratio)
    else:
        dpi_ratio = 1.0
        phys_w = logical_w
        phys_h = logical_h

    return {
        "type": "hello",
        "app_version": _TEST_APP_VERSION,
        "machine_name": socket.gethostname(),
        "platform": platform.system(),
        "screen_logical": [logical_w, logical_h],
        "screen_physical": [phys_w, phys_h],
        "dpi_scale": round(dpi_ratio, 4),
        "role_preference": "either",
    }


def _send_frame(sock: socket.socket, message: dict[str, Any]) -> None:
    """
    Serialize message to JSON, prefix with 4-byte big-endian length, and send.

    Raises OSError if the socket write fails. Callers catch this and surface it
    to the UI -- do not swallow socket errors.
    """
    payload: bytes = json.dumps(message).encode("utf-8")
    header: bytes = _FRAME_HEADER.pack(len(payload))
    sock.sendall(header + payload)


def _recv_frame(sock: socket.socket) -> dict[str, Any]:
    """
    Read one length-prefixed frame from sock and return the decoded dict.

    Raises OSError on socket error, json.JSONDecodeError on malformed payload,
    and ConnectionError if the peer closed the connection mid-frame.
    """
    header_bytes = _recv_exact(sock, _FRAME_HEADER.size)
    (length,) = _FRAME_HEADER.unpack(header_bytes)
    payload_bytes = _recv_exact(sock, length)
    return json.loads(payload_bytes.decode("utf-8"))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """
    Read exactly n bytes from sock, blocking until all bytes arrive.

    Raises ConnectionError if the connection closes before n bytes are read.
    Raises OSError on socket error.
    """
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Peer closed connection")
        buf.extend(chunk)
    return bytes(buf)


class _TransportSignals(QObject):
    """
    Qt signal carrier. Lives on the main thread so signals cross the thread boundary
    safely. All callbacks from the receive thread must go through these signals --
    never call Qt widgets directly from a non-Qt thread.
    """

    # Emitted when a complete message dict arrives from the peer.
    message_received = pyqtSignal(dict)

    # Emitted when the connection is fully established (TCP accept/connect succeeded).
    connected = pyqtSignal()

    # Emitted when the connection drops or the socket is closed.
    disconnected = pyqtSignal(str)  # reason string


class TcpTransport:
    """
    Persistent TCP socket transport for cross-machine mouse event delivery.

    Usage (server role):
        t = TcpTransport()
        t.register_message_callback(my_handler)
        t.register_connected_callback(on_connected)
        t.register_disconnected_callback(on_disconnected)
        t.start_server(port)          # returns immediately; thread listens in background

    Usage (client role):
        t = TcpTransport()
        t.register_message_callback(my_handler)
        t.register_connected_callback(on_connected)
        t.register_disconnected_callback(on_disconnected)
        t.connect_to_peer(host, port) # returns immediately; thread connects in background

    After connected fires: call t.send({...}) freely. Call t.close() to tear down.
    """

    def __init__(self) -> None:
        # Signals object must be created on the main thread (the constructor
        # is called from MainWindow, which is on the main thread).
        self._signals = _TransportSignals()

        self._sock: socket.socket | None = None         # active peer socket
        self._server_sock: socket.socket | None = None  # listening socket (server role only)
        self._connected: bool = False

        self._recv_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None

        # Threading event to signal all background threads to stop cleanly.
        self._stop_event = threading.Event()

        # Tracks when the last pong arrived. Used by the dead-man check in the
        # heartbeat thread. Initialized to now so the first interval is fair.
        self._last_pong_time: float = 0.0

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def register_message_callback(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Called on the Qt main thread for every incoming message (except ping/pong)."""
        self._signals.message_received.connect(callback)

    def register_connected_callback(self, callback: Callable[[], None]) -> None:
        """Called on the Qt main thread when TCP link is established."""
        self._signals.connected.connect(callback)

    def register_disconnected_callback(self, callback: Callable[[str], None]) -> None:
        """Called on the Qt main thread when the connection drops."""
        self._signals.disconnected.connect(callback)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start_server(self, port: int) -> None:
        """
        Bind a TCP server on all interfaces at the given port and wait for one
        inbound peer connection. Returns immediately -- the accept loop runs on
        a background thread.

        Only one peer connection is supported at a time. The server socket is
        closed after the first peer connects (no queue for a second peer).
        """
        self._stop_event.clear()
        t = threading.Thread(target=self._server_loop, args=(port,), daemon=True, name="transport-server")
        t.start()

    def connect_to_peer(self, host: str, port: int) -> None:
        """
        Open an outbound TCP connection to the server at (host, port). Returns
        immediately -- the connection attempt runs on a background thread.
        """
        self._stop_event.clear()
        t = threading.Thread(target=self._client_loop, args=(host, port), daemon=True, name="transport-client")
        t.start()

    def send(self, message: dict[str, Any]) -> None:
        """
        Serialize message to JSON, frame with 4-byte length prefix, and send.

        Must only be called after connected fires. Logs and surfaces errors
        if the send fails -- does not swallow them silently.
        """
        if self._sock is None or not self._connected:
            logger.warning("send() called but no active connection")
            return
        try:
            _send_frame(self._sock, message)
        except OSError as exc:
            logger.error("send failed: %s", exc)
            self._handle_disconnect(str(exc))

    def close(self) -> None:
        """
        Close the active connection and any server socket cleanly.

        Safe to call from any thread. Idempotent.
        """
        self._stop_event.set()
        self._connected = False
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

    # ------------------------------------------------------------------
    # Background threads
    # ------------------------------------------------------------------

    def _server_loop(self, port: int) -> None:
        """Background thread: bind, listen, accept one peer."""
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("0.0.0.0", port))
            srv.listen(1)
            srv.settimeout(1.0)  # allows the stop_event check below
            self._server_sock = srv
            logger.info("Listening on port %d", port)
        except OSError as exc:
            logger.error("Failed to bind port %d: %s", port, exc)
            self._signals.disconnected.emit(f"Bind failed: {exc}")
            return

        while not self._stop_event.is_set():
            try:
                peer_sock, peer_addr = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                # Server socket was closed by close() -- clean exit.
                break

            logger.info("Inbound connection from %s:%d", peer_addr[0], peer_addr[1])
            self._sock = peer_sock
            self._connected = True
            self._last_pong_time = time.monotonic()
            self._signals.connected.emit()
            self._start_recv_loop()
            self._start_heartbeat_loop()
            break  # only one peer at a time

    def _client_loop(self, host: str, port: int) -> None:
        """Background thread: connect to server at (host, port)."""
        logger.info("Connecting to %s:%d", host, port)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect((host, port))
            sock.settimeout(None)  # switch to blocking mode after connect
        except (OSError, TimeoutError) as exc:
            logger.error("Connection to %s:%d failed: %s", host, port, exc)
            self._signals.disconnected.emit(f"Connect failed: {exc}")
            return

        logger.info("Connected to %s:%d", host, port)
        self._sock = sock
        self._connected = True
        self._last_pong_time = time.monotonic()
        self._signals.connected.emit()
        self._start_recv_loop()
        self._start_heartbeat_loop()

    def _start_recv_loop(self) -> None:
        t = threading.Thread(target=self._recv_loop, daemon=True, name="transport-recv")
        self._recv_thread = t
        t.start()

    def _start_heartbeat_loop(self) -> None:
        t = threading.Thread(target=self._heartbeat_loop, daemon=True, name="transport-heartbeat")
        self._heartbeat_thread = t
        t.start()

    def _recv_loop(self) -> None:
        """Background thread: read frames from the peer and dispatch via signal."""
        while not self._stop_event.is_set() and self._sock is not None:
            try:
                message = _recv_frame(self._sock)
            except (OSError, ConnectionError) as exc:
                if not self._stop_event.is_set():
                    logger.error("Receive error: %s", exc)
                    self._handle_disconnect(str(exc))
                break
            except json.JSONDecodeError as exc:
                logger.error("Malformed frame received: %s", exc)
                # Malformed frame -- do not disconnect, log and keep reading.
                continue

            msg_type = message.get("type", "")

            if msg_type == "ping":
                # Respond with pong -- do not log (debug-level suppressed by default).
                logger.debug("Received ping, sending pong")
                self.send({"type": "pong"})

            elif msg_type == "pong":
                # Reset the dead-man timer.
                logger.debug("Received pong")
                self._last_pong_time = time.monotonic()

            else:
                # All other messages go to the Qt main thread via signal.
                self._signals.message_received.emit(message)

    def _heartbeat_loop(self) -> None:
        """
        Background thread: send a ping every HEARTBEAT_INTERVAL_S.
        If no pong arrives within HEARTBEAT_TIMEOUT_S, mark the connection dead.

        Dead-man switch: architecture invariant #4 in Slickshift CLAUDE.md.
        """
        self._last_pong_time = time.monotonic()
        while not self._stop_event.is_set() and self._connected:
            time.sleep(config.HEARTBEAT_INTERVAL_S)
            if self._stop_event.is_set():
                break
            logger.debug("Sending ping")
            self.send({"type": "ping"})
            elapsed = time.monotonic() - self._last_pong_time
            if elapsed > config.HEARTBEAT_TIMEOUT_S:
                logger.error(
                    "Heartbeat timeout: no pong for %.1fs (threshold %.1fs)",
                    elapsed,
                    config.HEARTBEAT_TIMEOUT_S,
                )
                self._handle_disconnect("heartbeat timeout")
                break

    def _handle_disconnect(self, reason: str) -> None:
        """Common path for unexpected disconnects. Called from background threads."""
        if not self._connected:
            return  # already handled
        self._connected = False
        self._stop_event.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._signals.disconnected.emit(reason)

    # ------------------------------------------------------------------
    # Handshake helpers
    # ------------------------------------------------------------------

    def send_hello(self) -> None:
        """
        Send this machine's hello payload to the peer.

        Called immediately after connected fires. The hello is the first
        message both sides send on a new connection.
        """
        payload = _build_hello_payload()
        logger.info("Sending hello")
        self.send(payload)
