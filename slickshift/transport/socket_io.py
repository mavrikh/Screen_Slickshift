"""
Slickshift mainline -- TCP transport layer.

Framing protocol:
    [uint32 big-endian payload length][UTF-8 JSON payload]

All socket I/O runs on a background thread. Received messages are delivered to
the Qt main thread via a pyqtSignal -- never call Qt widgets directly from the
receive thread.

Outbound queue design:
- A bounded outbound queue (size DELTA_QUEUE_MAX) serializes all sends through
  a dedicated sender thread. This decouples the Qt main thread and the capture
  polling thread from the TCP write path. When the queue is full, the oldest
  item is dropped and _deltas_dropped is incremented.
- enqueue_message() is the fast path for all per-event traffic: mouse deltas,
  clicks, scrolls, and keyboard events. It never blocks.
- send() is kept for low-volume control messages (hello, ping, pong,
  mirror_start/stop). It writes directly to the socket from the calling thread
  -- only call it from the sender thread or from threads where blocking is
  acceptable.

PLAINTEXT WARNING: This transport sends data unencrypted over the LAN. It is
suitable for a two-machine dev test on a trusted private network only. TLS
encryption is deferred per roadmap.
"""

from __future__ import annotations

import json
import logging
import platform
import queue
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from slickshift import config
from slickshift.transport.topology import MonitorInfo, enumerate_local_monitors

logger = logging.getLogger(__name__)

# Struct format for the 4-byte big-endian length prefix.
_FRAME_HEADER = struct.Struct("!I")  # unsigned int, network (big-endian) byte order

# App version string for hello payload.
_APP_VERSION = "0.1.0"


def _apply_socket_options(sock: socket.socket) -> None:
    """
    Apply socket-level keepalive and (where available) TCP_USER_TIMEOUT to a
    freshly connected or accepted peer socket.

    SO_KEEPALIVE is the primary secondary defense: the OS will probe the peer
    after the kernel's keepalive idle period and close the socket if no ACK
    arrives. Default kernel idle is long (2 hours on most OSes) but we tune it
    down on Linux. On macOS the minimum is 1 second via TCP_KEEPALIVE; on Windows
    the default is 2 hours but shortening it requires WSAIoctl, which we skip --
    the QTimer dead-man monitor is the primary detection path on Windows.

    TCP_USER_TIMEOUT (Linux/Windows only) tells the kernel to abort the
    connection if sent data goes unacknowledged for N milliseconds. This unblocks
    a stuck sendall() within ~3 s instead of waiting for the full TCP retransmit
    window (~30-120 s). The try/except is platform-detection scaffolding, not
    control-flow: the constant does not exist on macOS so we must probe at runtime.
    """
    # Enable TCP keepalive probing.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    system = platform.system()
    if system == "Linux":
        # Probe after 3 s idle, retry every 1 s, give up after 3 failures.
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 3)   # type: ignore[attr-defined]
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 1)  # type: ignore[attr-defined]
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)    # type: ignore[attr-defined]
    elif system == "Darwin":
        # macOS: TCP_KEEPALIVE sets the idle-before-first-probe interval (seconds).
        # The constant is 0x10 on IPPROTO_TCP; use getattr so the linter stays quiet
        # on other platforms.
        tcp_keepalive = getattr(socket, "TCP_KEEPALIVE", 0x10)
        sock.setsockopt(socket.IPPROTO_TCP, tcp_keepalive, 3)

    # TCP_USER_TIMEOUT (milliseconds): abort if unacked data sits this long.
    # Platform-detection scaffolding -- the constant does not exist on macOS;
    # silently no-op rather than crashing.
    try:
        tcp_user_timeout = getattr(socket, "TCP_USER_TIMEOUT")  # Linux 2.6.37+, Windows 10+
        sock.setsockopt(socket.IPPROTO_TCP, tcp_user_timeout, 3000)
    except AttributeError:
        pass  # macOS: not supported; QTimer monitor is the primary detection path


def _build_hello_payload() -> dict[str, Any]:
    """
    Construct the hello payload this machine sends at handshake time.

    Fields:
        app_version     -- Slickshift mainline version string
        machine_name    -- socket.gethostname()
        platform        -- platform.system() ("Darwin" / "Windows" / "Linux")
        screen_logical  -- (width, height) of the primary monitor in logical pixels
                           Kept for backward compatibility with Phase 1 peers.
        screen_physical -- (width, height) of the primary monitor in physical pixels
                           Kept for backward compatibility with Phase 1 peers.
        dpi_scale       -- DPI ratio of the primary monitor
                           Kept for backward compatibility with Phase 1 peers.
        monitors        -- list of MonitorInfo dicts for all monitors (Task #5+).
                           Each dict: {x, y, width, height, dpi_scale, is_primary}
        role_preference -- "either" (real role negotiation is a future step)
    """
    monitors: list[MonitorInfo] = enumerate_local_monitors()

    # Derive legacy scalar fields from the primary monitor (index 0 by
    # convention in enumerate_local_monitors). This keeps old peers that only
    # understand screen_logical/screen_physical working without change.
    if monitors:
        primary = monitors[0]
        logical_w: int = primary.width
        logical_h: int = primary.height
        dpi_ratio: float = primary.dpi_scale
        phys_w: int = int(logical_w * dpi_ratio)
        phys_h: int = int(logical_h * dpi_ratio)
    else:
        # Fallback: no Qt screen available (headless / test environment).
        logical_w = 1920
        logical_h = 1080
        dpi_ratio = 1.0
        phys_w = 1920
        phys_h = 1080
        logger.warning(
            "_build_hello_payload: no monitors enumerated; using fallback 1920x1080"
        )

    return {
        "type": "hello",
        "app_version": _APP_VERSION,
        "machine_name": socket.gethostname(),
        "platform": platform.system(),
        "screen_logical": [logical_w, logical_h],
        "screen_physical": [phys_w, phys_h],
        "dpi_scale": round(dpi_ratio, 4),
        "monitors": [m.to_dict() for m in monitors],
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
        self._sender_thread: threading.Thread | None = None

        # Threading event to signal all background threads to stop cleanly.
        self._stop_event = threading.Event()

        # Tracks when the last pong arrived. Used by the dead-man check in the
        # heartbeat thread. Initialized to now so the first interval is fair.
        self._last_pong_time: float = 0.0

        # Bounded outbound queue for all per-event messages (mouse deltas,
        # clicks, scrolls, keys). The sentinel value None signals the sender
        # thread to exit.
        self._send_queue: queue.Queue[dict | None] = queue.Queue(maxsize=config.DELTA_QUEUE_MAX)

        # Monotonic counter: number of queued messages dropped because the queue was full.
        self._deltas_dropped: int = 0

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

    @property
    def deltas_dropped(self) -> int:
        """Number of outbound queued messages dropped due to send-queue backpressure."""
        return self._deltas_dropped

    def enqueue_message(self, message: dict[str, Any]) -> bool:
        """
        Non-blocking enqueue of a per-event message into the bounded send queue.

        Handles all high-frequency traffic: mouse deltas (from the capture loop),
        clicks, scrolls, and keyboard events. If the queue is full, the oldest
        item is dropped to make room and _deltas_dropped is incremented.
        Returns True if the message was accepted, False if a drop occurred.

        This is the only correct path for per-event traffic on the Qt main
        thread or the capture thread. Do NOT call send() for these -- it
        blocks the calling thread on TCP I/O.
        """
        if not self._connected:
            return False
        try:
            self._send_queue.put_nowait(message)
            return True
        except queue.Full:
            # Drop the oldest item to make room for the newest delta.
            try:
                self._send_queue.get_nowait()
            except queue.Empty:
                pass
            self._deltas_dropped += 1
            try:
                self._send_queue.put_nowait(message)
            except queue.Full:
                pass  # another thread raced -- just accept the loss
            return False

    def send(self, message: dict[str, Any]) -> None:
        """
        Serialize message to JSON, frame with 4-byte length prefix, and send
        directly on the calling thread.

        For low-volume control messages (hello, ping, pong, mirror_start/stop).
        Do NOT use this for per-event messages -- use enqueue_message() instead.
        Must only be called after connected fires. Logs and surfaces errors
        if the send fails -- does not swallow them silently.
        """
        if self._sock is None or not self._connected:
            logger.warning("send() called but no active connection")
            return
        try:
            _send_frame(self._sock, message)
        except OSError as exc:
            elapsed = self.seconds_since_last_pong()
            logger.error(
                "Connection lost: send failed (no pong for %.1fs): %s",
                elapsed,
                exc,
            )
            self._handle_disconnect(f"send failed (no pong for {elapsed:.1f}s): {exc}")

    def seconds_since_last_pong(self) -> float:
        """
        Return seconds elapsed since the last pong arrived.

        Returns 0.0 when not connected so the dead-man monitor in MainWindow
        does not fire while idle. Reading a float attribute under the GIL is
        atomic enough for this single-reader use case.
        """
        if not self._connected:
            return 0.0
        return time.monotonic() - self._last_pong_time

    def handle_disconnect(self, reason: str) -> None:
        """
        Public wrapper around _handle_disconnect.

        Exposed so the Qt-thread dead-man monitor in MainWindow can trigger
        the teardown path without reaching into a private method.
        """
        self._handle_disconnect(reason)

    def close(self) -> None:
        """
        Close the active connection and any server socket cleanly.

        Safe to call from any thread. Idempotent.
        """
        self._stop_event.set()
        self._connected = False
        # Unblock the sender thread by posting a sentinel.
        try:
            self._send_queue.put_nowait(None)
        except queue.Full:
            pass
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
            _apply_socket_options(peer_sock)
            self._sock = peer_sock
            self._connected = True
            self._last_pong_time = time.monotonic()
            self._signals.connected.emit()
            self._start_sender_loop()
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
        _apply_socket_options(sock)
        self._sock = sock
        self._connected = True
        self._last_pong_time = time.monotonic()
        self._signals.connected.emit()
        self._start_sender_loop()
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

    def _start_sender_loop(self) -> None:
        """
        Start the dedicated sender thread that drains the outbound queue.

        All delta messages flow through this thread so the capture polling thread
        never blocks on TCP I/O. Control messages (hello, ping, pong) bypass the
        queue via send() and are written on whichever thread calls them.
        """
        # Drain any stale items from a previous session.
        while not self._send_queue.empty():
            try:
                self._send_queue.get_nowait()
            except queue.Empty:
                break
        self._deltas_dropped = 0
        t = threading.Thread(target=self._sender_loop, daemon=True, name="transport-sender")
        self._sender_thread = t
        t.start()

    def _sender_loop(self) -> None:
        """
        Background thread: drain the outbound queue and write frames to the socket.

        Blocks on queue.get() so it uses zero CPU when the queue is empty.
        A None sentinel posted by close() causes a clean exit.
        """
        while not self._stop_event.is_set():
            try:
                item = self._send_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                # Sentinel from close() -- exit cleanly.
                break
            if self._sock is None or not self._connected:
                break
            try:
                _send_frame(self._sock, item)
            except OSError as exc:
                if not self._stop_event.is_set():
                    logger.error("sender_loop send failed: %s", exc)
                    self._handle_disconnect(str(exc))
                break

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
                    "Connection lost: heartbeat timeout (sender, no pong for %.1fs)",
                    elapsed,
                )
                self._handle_disconnect(
                    f"heartbeat timeout (sender, no pong for {elapsed:.1f}s)"
                )
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
