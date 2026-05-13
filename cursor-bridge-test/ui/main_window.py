"""
cursor-bridge-test -- PyQt6 main window.

Five regions:
  1. Connection panel (top): peer address field, Listen/Connect/Disconnect buttons,
     connection state indicator with peer info. Added in Step 3.
  2. Mirroring panel (below connection): Start/Stop Mirroring toggle, role display,
     and Inject checkbox (receiver only). Added in Step 4; Inject added in Step 5.
  3. Status bar: current switch-state, cursor coordinates, FPS, role, delta stats.
  4. Canvas (center): red square represents the local cursor position (always).
     In RECEIVING state a second green square mirrors the incoming sender deltas.
  5. Log panel (bottom, collapsible): last N lines from the event log.

Step 4 wires the mirroring button to DeltaCapture and the transport send queue.
Step 5 adds OS-level injection on the receiver side via MouseInjector, an Inject
checkbox (defaults off), macOS Accessibility permission probe, Esc force-release,
and status bar distinction between canvas-only and injecting receiver roles.
"""

from __future__ import annotations

import collections
import datetime
import logging
import time
import pyautogui

from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import config
from capture.mouse_capture import DeltaCapture
from inject.mouse_inject import MouseInjector
from state.controller import StateController, SwitchState
from transport.socket_io import TcpTransport

logger = logging.getLogger(__name__)

# Dot size in logical pixels for the canvas cursor indicators.
_DOT_SIZE: int = 14

# Sentinel for "no green square position yet" -- center of canvas.
_NO_POSITION: float = 0.5


def _ts() -> str:
    """Return a short timestamp string for log lines."""
    return datetime.datetime.now().strftime("%H:%M:%S")


class _Canvas(QFrame):
    """
    Central canvas. Draws two squares:
    - Red square: local cursor position (always visible).
    - Green square: mirrors the sender's incoming deltas (RECEIVING state only).

    In RECEIVING state the green square starts at canvas center and accumulates
    normalized deltas from the peer. It does NOT represent any OS cursor position --
    it is a visual confirmation that delta math is working before injection is added
    in Step 5.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(QSize(400, 300))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: #1a1a2e;")

        # Red square: local cursor position (normalized 0.0-1.0).
        self._local_x: float = _NO_POSITION
        self._local_y: float = _NO_POSITION

        # Green square: accumulated sender delta position (normalized 0.0-1.0).
        # Only painted when _show_remote is True.
        self._remote_x: float = _NO_POSITION
        self._remote_y: float = _NO_POSITION
        self._show_remote: bool = False

    def set_local_position(self, nx: float, ny: float) -> None:
        """Update the red (local) square. Triggers a repaint."""
        self._local_x = max(0.0, min(1.0, nx))
        self._local_y = max(0.0, min(1.0, ny))
        self.update()

    # Keep the legacy name for compatibility with existing callers.
    def set_normalized_position(self, nx: float, ny: float) -> None:
        self.set_local_position(nx, ny)

    def reset_remote_position(self) -> None:
        """
        Reset the green square to canvas center. Call when entering RECEIVING state
        so the square starts from a neutral position rather than a stale location.
        """
        self._remote_x = _NO_POSITION
        self._remote_y = _NO_POSITION
        self._show_remote = True
        self.update()

    def apply_remote_delta(self, ndx: float, ndy: float) -> None:
        """
        Accumulate an incoming normalized delta into the green square position.

        Clamps to [0.0, 1.0] so the square never leaves the canvas. The deltas
        are already normalized to the sender's screen; the receiver applies no
        further scaling here (naive pass-through per Step 4 spec -- DPI-aware
        normalization is Step 7).
        """
        self._remote_x = max(0.0, min(1.0, self._remote_x + ndx))
        self._remote_y = max(0.0, min(1.0, self._remote_y + ndy))
        self.update()

    def hide_remote(self) -> None:
        """Stop painting the green square. Call when leaving RECEIVING state."""
        self._show_remote = False
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Grid lines at 25% intervals for spatial reference.
        pen = QPen(QColor("#2a2a4e"))
        pen.setWidth(1)
        painter.setPen(pen)
        for i in range(1, 4):
            x = int(w * i / 4)
            y = int(h * i / 4)
            painter.drawLine(x, 0, x, h)
            painter.drawLine(0, y, w, y)

        # Red square: local cursor.
        lx = int(self._local_x * w) - _DOT_SIZE // 2
        ly = int(self._local_y * h) - _DOT_SIZE // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e94560"))
        painter.drawRect(lx, ly, _DOT_SIZE, _DOT_SIZE)

        # Green square: remote sender cursor mirror (only in RECEIVING state).
        if self._show_remote:
            rx = int(self._remote_x * w) - _DOT_SIZE // 2
            ry = int(self._remote_y * h) - _DOT_SIZE // 2
            painter.setBrush(QColor("#40e040"))
            painter.drawRect(rx, ry, _DOT_SIZE, _DOT_SIZE)

        painter.end()


class _StatusBar(QWidget):
    """
    Top status bar. Shows switch-state mode, cursor coordinates, role, and
    delta statistics (rate or drop count depending on role).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setStyleSheet(
            "background-color: #16213e; color: #e0e0e0; font-family: monospace; font-size: 13px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._label = QLabel("Mode: IDLE  |  Cursor: (0, 0)  |  Role: Idle")
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._label)

    def update_status(
        self,
        mode: str,
        x: int,
        y: int,
        role: str = "Idle",
        extra: str = "",
    ) -> None:
        parts = [f"Mode: {mode}", f"Cursor: ({x}, {y})", f"Role: {role}"]
        if extra:
            parts.append(extra)
        self._label.setText("  |  ".join(parts))


class _LogPanel(QPlainTextEdit):
    """
    Bottom collapsible log panel. Displays the last N event log lines.

    Auto-scrolls to the newest line. Accepts plain text appended via append_line().
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(config.LOG_PANEL_MAX_LINES)
        self.setFixedHeight(160)
        self.setStyleSheet(
            "background-color: #0f0f1a; color: #7aff7a; font-family: monospace; font-size: 11px;"
        )
        self.setPlainText("Awaiting first event...")

    def append_line(self, text: str) -> None:
        self.appendPlainText(text)
        sb = self.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())


class _ConnectionPanel(QGroupBox):
    """
    Step 3 connection panel.

    Contains:
    - Peer address field: "<ip>:<port>" or just "<ip>" (port defaults to DEFAULT_PORT)
    - Listen button: start a TCP server and wait for inbound connection
    - Connect button: initiate outbound connection to the entered address
    - Disconnect button: close any active connection
    - Status indicator: current connection state + peer info when connected
    """

    # Button labels.
    _LABEL_LISTEN = "Listen"
    _LABEL_CONNECT = "Connect"
    _LABEL_DISCONNECT = "Disconnect"

    # Status colours.
    _COLOR_DISCONNECTED = "#888888"
    _COLOR_LISTENING = "#f0c040"
    _COLOR_CONNECTING = "#f0c040"
    _COLOR_HANDSHAKING = "#f0c040"
    _COLOR_CONNECTED = "#40e040"
    _COLOR_FAILED = "#ff4444"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Connection", parent)
        self.setStyleSheet(
            "QGroupBox { color: #e0e0e0; font-family: monospace; font-size: 12px; "
            "border: 1px solid #2a2a4e; margin-top: 6px; padding-top: 4px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(8)

        # --- Address field ---
        addr_label = QLabel("Peer address:")
        addr_label.setStyleSheet("color: #b0b0b0; font-family: monospace; font-size: 12px;")
        layout.addWidget(addr_label)

        self._addr_field = QLineEdit()
        self._addr_field.setPlaceholderText(f"192.168.x.x:{config.DEFAULT_PORT}")
        self._addr_field.setFixedWidth(220)
        self._addr_field.setStyleSheet(
            "background-color: #1a1a2e; color: #e0e0e0; font-family: monospace; "
            "font-size: 12px; border: 1px solid #3a3a6e; padding: 3px 6px;"
        )
        layout.addWidget(self._addr_field)

        # --- Buttons ---
        btn_style = (
            "QPushButton { background-color: #16213e; color: #e0e0e0; "
            "font-family: monospace; font-size: 12px; border: 1px solid #3a3a6e; "
            "padding: 4px 12px; } "
            "QPushButton:hover { background-color: #1e2f5e; } "
            "QPushButton:disabled { color: #555555; border-color: #2a2a4e; }"
        )

        self._listen_btn = QPushButton(self._LABEL_LISTEN)
        self._listen_btn.setStyleSheet(btn_style)
        layout.addWidget(self._listen_btn)

        self._connect_btn = QPushButton(self._LABEL_CONNECT)
        self._connect_btn.setStyleSheet(btn_style)
        layout.addWidget(self._connect_btn)

        self._disconnect_btn = QPushButton(self._LABEL_DISCONNECT)
        self._disconnect_btn.setStyleSheet(btn_style)
        layout.addWidget(self._disconnect_btn)

        # --- Status indicator ---
        layout.addStretch()
        self._status_label = QLabel("Disconnected")
        self._status_label.setStyleSheet(
            f"color: {self._COLOR_DISCONNECTED}; font-family: monospace; font-size: 12px;"
        )
        self._status_label.setMinimumWidth(300)
        layout.addWidget(self._status_label)

        # Start in disconnected state.
        self._apply_state(SwitchState.IDLE, peer_info=None)

    def _apply_state(self, state: SwitchState, peer_info: dict | None) -> None:
        """Update button enable states and status label for the given switch state."""
        is_idle = state == SwitchState.IDLE
        is_connected = state == SwitchState.CONNECTED

        self._addr_field.setEnabled(is_idle)
        self._listen_btn.setEnabled(is_idle)
        self._connect_btn.setEnabled(is_idle)
        # Disconnect is available whenever not idle.
        self._disconnect_btn.setEnabled(not is_idle)

        if state == SwitchState.IDLE:
            color = self._COLOR_DISCONNECTED
            text = "Disconnected"
        elif state == SwitchState.LISTENING:
            color = self._COLOR_LISTENING
            text = f"Listening on port {config.DEFAULT_PORT}..."
        elif state == SwitchState.CONNECTING:
            color = self._COLOR_CONNECTING
            text = "Connecting..."
        elif state == SwitchState.HANDSHAKING:
            color = self._COLOR_HANDSHAKING
            text = "Handshaking..."
        elif state in (SwitchState.CONNECTED, SwitchState.CAPTURING, SwitchState.RECEIVING):
            color = self._COLOR_CONNECTED
            if peer_info is not None:
                name = peer_info.get("machine_name", "unknown")
                plat = peer_info.get("platform", "?")
                lw, lh = peer_info.get("screen_logical", [0, 0])
                dpi = peer_info.get("dpi_scale", 1.0)
                text = f"Connected: {name} ({plat})  {lw}x{lh}  dpi={dpi}"
            else:
                text = "Connected"
        else:
            color = self._COLOR_FAILED
            text = f"State: {state.value}"

        self._status_label.setStyleSheet(
            f"color: {color}; font-family: monospace; font-size: 12px;"
        )
        self._status_label.setText(text)

    def on_state_changed(self, state: SwitchState, peer_info: dict | None = None) -> None:
        """Called by MainWindow when the StateController fires a state change."""
        self._apply_state(state, peer_info)

    def parsed_address(self) -> tuple[str, int]:
        """
        Parse the address field into (host, port).

        Accepts "host:port" or bare "host" (port defaults to DEFAULT_PORT).
        Returns ("", 0) and logs a warning if the field is empty or malformed.
        """
        raw = self._addr_field.text().strip()
        if not raw:
            return ("", 0)
        if ":" in raw:
            parts = raw.rsplit(":", 1)
            host = parts[0].strip()
            try:
                port = int(parts[1].strip())
            except ValueError:
                logger.warning("Invalid port in address field: %r", raw)
                return ("", 0)
        else:
            host = raw
            port = config.DEFAULT_PORT
        return (host, port)

    @property
    def listen_btn(self) -> QPushButton:
        return self._listen_btn

    @property
    def connect_btn(self) -> QPushButton:
        return self._connect_btn

    @property
    def disconnect_btn(self) -> QPushButton:
        return self._disconnect_btn


class _MirrorPanel(QGroupBox):
    """
    Step 4 mirroring panel.

    Contains:
    - Start Mirroring button: only enabled in CONNECTED state. Clicking it makes
      this machine the sender (CAPTURING state) and notifies the peer.
    - Stop Mirroring button: only enabled in CAPTURING state.
    - Role label: displays "Role: Sender", "Role: Receiver", or "Role: Idle".

    The panel does not hold business logic. Button signals are connected in
    MainWindow which owns both StateController and TcpTransport.
    """

    _COLOR_IDLE = "#888888"
    _COLOR_SENDER = "#f0c040"
    _COLOR_RECEIVER = "#40a0ff"
    _COLOR_RECEIVER_INJECTING = "#40ffc0"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Mirroring", parent)
        self.setStyleSheet(
            "QGroupBox { color: #e0e0e0; font-family: monospace; font-size: 12px; "
            "border: 1px solid #2a2a4e; margin-top: 6px; padding-top: 4px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 8)
        layout.setSpacing(8)

        btn_style = (
            "QPushButton { background-color: #16213e; color: #e0e0e0; "
            "font-family: monospace; font-size: 12px; border: 1px solid #3a3a6e; "
            "padding: 4px 12px; } "
            "QPushButton:hover { background-color: #1e2f5e; } "
            "QPushButton:disabled { color: #555555; border-color: #2a2a4e; }"
        )

        self._start_btn = QPushButton("Start Mirroring")
        self._start_btn.setStyleSheet(btn_style)
        self._start_btn.setEnabled(False)
        layout.addWidget(self._start_btn)

        self._stop_btn = QPushButton("Stop Mirroring")
        self._stop_btn.setStyleSheet(btn_style)
        self._stop_btn.setEnabled(False)
        layout.addWidget(self._stop_btn)

        # Step 5: Inject checkbox. Defaults to unchecked so canvas-mirror-only
        # behavior (Step 4) is preserved until the user opts in. Only meaningful
        # when this machine is the Receiver.
        self._inject_chk = QCheckBox("Inject")
        self._inject_chk.setChecked(False)
        self._inject_chk.setEnabled(False)
        self._inject_chk.setStyleSheet(
            "QCheckBox { color: #b0b0b0; font-family: monospace; font-size: 12px; }"
            "QCheckBox:disabled { color: #555555; }"
        )
        layout.addWidget(self._inject_chk)

        layout.addStretch()

        self._role_label = QLabel("Role: Idle")
        self._role_label.setStyleSheet(
            f"color: {self._COLOR_IDLE}; font-family: monospace; font-size: 12px;"
        )
        layout.addWidget(self._role_label)

    def on_state_changed(self, state: SwitchState) -> None:
        """Update button states and role label to match the new switch state."""
        if state == SwitchState.CONNECTED:
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(False)
            self._set_role("Idle", self._COLOR_IDLE)
        elif state == SwitchState.CAPTURING:
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
            self._inject_chk.setEnabled(False)
            self._set_role("Sender", self._COLOR_SENDER)
        elif state == SwitchState.RECEIVING:
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(True)
            self._set_role("Receiver", self._COLOR_RECEIVER)
        else:
            # IDLE, LISTENING, CONNECTING, HANDSHAKING, TRANSITIONING -- all buttons off.
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(False)
            self._set_role("Idle", self._COLOR_IDLE)

    def set_role_injecting(self, injecting: bool) -> None:
        """
        Switch the role label between 'Receiver' and 'Receiver (injecting)'.
        Called by MainWindow when the Inject checkbox toggles while in RECEIVING.
        """
        if injecting:
            self._set_role("Receiver (injecting)", self._COLOR_RECEIVER_INJECTING)
        else:
            self._set_role("Receiver", self._COLOR_RECEIVER)

    def _set_role(self, role: str, color: str) -> None:
        self._role_label.setText(f"Role: {role}")
        self._role_label.setStyleSheet(
            f"color: {color}; font-family: monospace; font-size: 12px;"
        )

    @property
    def start_btn(self) -> QPushButton:
        return self._start_btn

    @property
    def stop_btn(self) -> QPushButton:
        return self._stop_btn

    @property
    def inject_chk(self) -> QCheckBox:
        return self._inject_chk


class MainWindow(QMainWindow):
    """Root application window. Owns all panels, state, transport, and capture."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Slickshift -- Cursor Bridge Test")
        self.setMinimumSize(QSize(700, 620))
        self.resize(960, 740)
        self.setStyleSheet("background-color: #0f0f1a;")

        # --- State, transport, capture, and injection ---
        self._state_ctrl = StateController()
        self._transport = TcpTransport()
        self._capture = DeltaCapture()
        self._injector = MouseInjector()
        self._peer_info: dict | None = None  # last received hello payload from peer

        # Whether OS cursor injection is currently active on this machine.
        # Controlled by the Inject checkbox; only meaningful in RECEIVING state.
        self._injection_enabled: bool = False

        # Wire transport signals to main-thread handlers.
        self._transport.register_connected_callback(self._on_transport_connected)
        self._transport.register_disconnected_callback(self._on_transport_disconnected)
        self._transport.register_message_callback(self._on_message_received)

        # Wire state changes to panels.
        self._state_ctrl.register_state_change_callback(self._on_state_changed)

        # --- Receiver delta rate tracking ---
        # Timestamps of recent incoming deltas in a rolling window.
        self._delta_timestamps: collections.deque[float] = collections.deque()
        self._deltas_received: int = 0
        # Last delta values (for the periodic log sample).
        self._last_ndx: float = 0.0
        self._last_ndy: float = 0.0
        self._last_seq: int = 0
        # Wall-clock time of the last sample log line.
        self._last_sample_log_time: float = 0.0

        # --- Widgets ---
        self._conn_panel = _ConnectionPanel()
        self._mirror_panel = _MirrorPanel()
        self._status_bar = _StatusBar()
        self._canvas = _Canvas()
        self._log_panel = _LogPanel()

        # Wire connection buttons.
        self._conn_panel.listen_btn.clicked.connect(self._on_listen_clicked)
        self._conn_panel.connect_btn.clicked.connect(self._on_connect_clicked)
        self._conn_panel.disconnect_btn.clicked.connect(self._on_disconnect_clicked)

        # Wire mirroring buttons.
        self._mirror_panel.start_btn.clicked.connect(self._on_start_mirroring_clicked)
        self._mirror_panel.stop_btn.clicked.connect(self._on_stop_mirroring_clicked)

        # Wire Inject checkbox (Step 5).
        self._mirror_panel.inject_chk.stateChanged.connect(self._on_inject_toggled)

        # Splitter lets the user resize the log panel vertically.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._canvas)
        splitter.addWidget(self._log_panel)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._conn_panel)
        root_layout.addWidget(self._mirror_panel)
        root_layout.addWidget(self._status_bar)
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

        # --- Cursor polling timer ---
        # Used for the local red-square canvas update and status bar coordinate display.
        # Also drives the sender delta dispatch when in CAPTURING state (the capture
        # module runs its own thread; this timer is only for the UI).
        self._screen_w, self._screen_h = pyautogui.size()
        pyautogui.FAILSAFE = config.PYAUTOGUI_FAILSAFE

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(config.CANVAS_REFRESH_MS)
        self._poll_timer.timeout.connect(self._poll_cursor)
        self._poll_timer.start()

        logger.info(
            "MainWindow initialized. Screen logical: %dx%d. Polling at %dms.",
            self._screen_w, self._screen_h, config.CANVAS_REFRESH_MS,
        )

    # ------------------------------------------------------------------
    # Cursor polling (local red square + status bar coordinates)
    # ------------------------------------------------------------------

    def _poll_cursor(self) -> None:
        x, y = pyautogui.position()
        nx = x / self._screen_w
        ny = y / self._screen_h
        self._canvas.set_local_position(nx, ny)

        state = self._state_ctrl.state
        mode = state.value.upper()
        role = self._role_label_for_state(state)
        extra = self._extra_status_for_state(state)
        self._status_bar.update_status(mode, x, y, role=role, extra=extra)

    def _role_label_for_state(self, state: SwitchState) -> str:
        if state == SwitchState.CAPTURING:
            return "Sender"
        if state == SwitchState.RECEIVING:
            return "Receiver (injecting)" if self._injection_enabled else "Receiver"
        return "Idle"

    def _extra_status_for_state(self, state: SwitchState) -> str:
        if state == SwitchState.CAPTURING:
            dropped = self._transport.deltas_dropped
            if dropped > 0:
                return f"Dropped: {dropped}"
            return ""
        if state == SwitchState.RECEIVING:
            rate = self._compute_delta_rate()
            return f"Deltas: {self._deltas_received} received, {rate:.1f} Hz"
        return ""

    def _compute_delta_rate(self) -> float:
        """
        Compute the number of deltas received in the last DELTA_RATE_WINDOW_S seconds.
        Prunes stale timestamps from the deque as a side effect.
        """
        now = time.monotonic()
        cutoff = now - config.DELTA_RATE_WINDOW_S
        while self._delta_timestamps and self._delta_timestamps[0] < cutoff:
            self._delta_timestamps.popleft()
        count = len(self._delta_timestamps)
        return count / config.DELTA_RATE_WINDOW_S

    # ------------------------------------------------------------------
    # Connection button handlers
    # ------------------------------------------------------------------

    def _on_listen_clicked(self) -> None:
        self._peer_info = None
        self._state_ctrl.begin_listening()
        self._conn_panel.on_state_changed(SwitchState.LISTENING)
        self._mirror_panel.on_state_changed(SwitchState.LISTENING)
        self._append_log(f"Listening on port {config.DEFAULT_PORT}")
        self._transport.start_server(config.DEFAULT_PORT)

    def _on_connect_clicked(self) -> None:
        host, port = self._conn_panel.parsed_address()
        if not host:
            self._append_log("Error: enter a peer address before connecting")
            return
        self._peer_info = None
        self._state_ctrl.begin_connecting()
        self._conn_panel.on_state_changed(SwitchState.CONNECTING)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTING)
        self._append_log(f"Connecting to {host}:{port}")
        self._transport.connect_to_peer(host, port)

    def _on_disconnect_clicked(self) -> None:
        self._stop_capture_if_running()
        self._canvas.hide_remote()
        self._transport.close()
        self._state_ctrl.force_release()
        self._peer_info = None
        self._conn_panel.on_state_changed(SwitchState.IDLE)
        self._mirror_panel.on_state_changed(SwitchState.IDLE)
        self._append_log("Disconnected by user")

    # ------------------------------------------------------------------
    # Mirroring button handlers
    # ------------------------------------------------------------------

    def _on_start_mirroring_clicked(self) -> None:
        """
        This machine becomes the sender. Transition to CAPTURING and notify peer.

        The peer will transition to RECEIVING on receipt of mirror_start.
        """
        self._state_ctrl.start_mirroring()
        self._transport.send({"type": "mirror_start"})
        self._capture.start(self._transport.enqueue_delta)
        self._conn_panel.on_state_changed(SwitchState.CAPTURING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CAPTURING)
        self._append_log("Mirroring started -- this machine is now the Sender")

    def _on_stop_mirroring_clicked(self) -> None:
        """Stop capturing and notify peer to exit RECEIVING."""
        self._stop_capture_if_running()
        self._state_ctrl.stop_mirroring()
        self._transport.send({"type": "mirror_stop"})
        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log("Mirroring stopped")

    def _stop_capture_if_running(self) -> None:
        """Stop DeltaCapture if it is currently polling."""
        if self._state_ctrl.state == SwitchState.CAPTURING:
            self._capture.stop()

    # ------------------------------------------------------------------
    # Transport event handlers (called on Qt main thread via signals)
    # ------------------------------------------------------------------

    def _on_transport_connected(self) -> None:
        """TCP link is up. Transition to HANDSHAKING and send hello."""
        self._state_ctrl.connection_established()
        self._conn_panel.on_state_changed(SwitchState.HANDSHAKING)
        self._mirror_panel.on_state_changed(SwitchState.HANDSHAKING)
        self._append_log("Sending hello")
        self._transport.send_hello()

    def _on_transport_disconnected(self, reason: str) -> None:
        """Connection dropped or timed out."""
        self._stop_capture_if_running()
        self._canvas.hide_remote()
        self._state_ctrl.connection_lost(reason)
        self._peer_info = None
        self._conn_panel.on_state_changed(SwitchState.IDLE, peer_info=None)
        self._mirror_panel.on_state_changed(SwitchState.IDLE)
        self._append_log(f"Connection lost: {reason}")

    def _on_message_received(self, message: dict) -> None:
        """Dispatch incoming messages by type."""
        msg_type = message.get("type", "")

        if msg_type == "hello":
            self._handle_hello(message)
        elif msg_type == "mirror_start":
            self._handle_mirror_start()
        elif msg_type == "mirror_stop":
            self._handle_mirror_stop()
        elif msg_type == "delta":
            self._handle_delta(message)
        else:
            logger.debug("Unknown message type: %r", msg_type)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _handle_hello(self, payload: dict) -> None:
        """
        Process the peer's hello payload. Transition to CONNECTED and display
        peer info in the connection panel and log.
        """
        self._peer_info = payload
        name = payload.get("machine_name", "unknown")
        plat = payload.get("platform", "?")
        lw, lh = payload.get("screen_logical", [0, 0])
        dpi = payload.get("dpi_scale", 1.0)

        self._state_ctrl.handshake_complete()
        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=payload)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log(
            f"Received hello from peer: {name} ({plat})  {lw}x{lh}  dpi={dpi}"
        )
        self._append_log("Connection established")

    def _handle_mirror_start(self) -> None:
        """
        Peer is starting to send deltas. Enter RECEIVING state.
        Reset receiver delta counters and the canvas green square.
        """
        self._state_ctrl.mirror_start_received()
        self._deltas_received = 0
        self._delta_timestamps.clear()
        self._last_sample_log_time = time.monotonic()
        self._canvas.reset_remote_position()
        self._conn_panel.on_state_changed(SwitchState.RECEIVING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.RECEIVING)
        self._append_log("Peer started mirroring -- this machine is now the Receiver")

    def _handle_mirror_stop(self) -> None:
        """Peer stopped sending deltas. Return to CONNECTED state."""
        self._state_ctrl.mirror_stop_received()
        self._canvas.hide_remote()
        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log(
            f"Peer stopped mirroring. Total deltas received: {self._deltas_received}"
        )

    def _handle_delta(self, message: dict) -> None:
        """
        Process an incoming cursor delta from the sender.

        Always updates the canvas green square. When in RECEIVING state and
        injection is enabled, also moves the OS cursor via MouseInjector.
        The receiver uses its own screen dimensions (captured at startup via
        pyautogui.size()), NOT the sender's, for pixel translation.
        """
        ndx: float = message.get("ndx", 0.0)
        ndy: float = message.get("ndy", 0.0)
        seq: int = message.get("seq", 0)

        self._deltas_received += 1
        self._delta_timestamps.append(time.monotonic())
        self._last_ndx = ndx
        self._last_ndy = ndy
        self._last_seq = seq

        # Apply to the canvas green square (always).
        self._canvas.apply_remote_delta(ndx, ndy)

        # OS cursor injection (only when RECEIVING and Inject is checked).
        if self._state_ctrl.state == SwitchState.RECEIVING and self._injection_enabled:
            self._injector.move_relative(ndx, ndy, self._screen_w, self._screen_h)

        # Log a sample line roughly once per second.
        now = time.monotonic()
        if now - self._last_sample_log_time >= config.DELTA_SAMPLE_LOG_INTERVAL_S:
            self._last_sample_log_time = now
            rate = self._compute_delta_rate()
            self._append_log(
                f"Last delta: ndx={ndx:.4f} ndy={ndy:.4f} seq={seq}  "
                f"({self._deltas_received} total, {rate:.1f} Hz)"
            )

    # ------------------------------------------------------------------
    # Inject toggle and force-release (Step 5)
    # ------------------------------------------------------------------

    def _on_inject_toggled(self, state: int) -> None:
        """
        Called when the Inject checkbox changes state.

        When enabling: run the macOS Accessibility probe first. If the probe
        fails, uncheck the box and leave injection disabled -- the log panel
        explains what to do. The probe runs only once per session after a
        successful pass (MouseInjector._injection_verified flag).

        When disabling: deactivate injection immediately. Reset the verified
        flag so the probe re-runs if the user re-enables after fixing permissions.
        """
        checked: bool = state != 0
        if checked:
            # Run (or re-run) the permission probe before committing to injection.
            if not self._injector.verify_permission_probe():
                # Permission denied -- surface the message to the log panel and
                # uncheck the box without enabling injection.
                self._append_log(
                    "macOS Accessibility permission missing. "
                    "Grant in System Settings > Privacy & Security > Accessibility, "
                    "then re-toggle Inject."
                )
                # Block signal temporarily to avoid re-entering this handler.
                self._mirror_panel.inject_chk.blockSignals(True)
                self._mirror_panel.inject_chk.setChecked(False)
                self._mirror_panel.inject_chk.blockSignals(False)
                return
            self._injection_enabled = True
            self._mirror_panel.set_role_injecting(True)
            self._append_log(
                "Injection enabled -- OS cursor will follow sender deltas. "
                "Press Esc in this window to force-release."
            )
            logger.info("OS cursor injection enabled")
        else:
            self._injection_enabled = False
            self._injector.reset_verification()
            if self._state_ctrl.state == SwitchState.RECEIVING:
                self._mirror_panel.set_role_injecting(False)
            logger.info("OS cursor injection disabled")

    def _force_release_injection(self) -> None:
        """
        Immediately disable OS injection while keeping RECEIVING state active.

        This is the minimum viable escape hatch for Step 5 testing. Canvas
        mirroring continues; only OS cursor movement stops. The system-wide
        hotkey (config.HANDOFF_HOTKEY_RELEASE) that works regardless of window
        focus is implemented in Step 7.
        """
        if not self._injection_enabled:
            return
        self._injection_enabled = False
        self._injector.reset_verification()
        # Uncheck without re-entering _on_inject_toggled.
        self._mirror_panel.inject_chk.blockSignals(True)
        self._mirror_panel.inject_chk.setChecked(False)
        self._mirror_panel.inject_chk.blockSignals(False)
        self._mirror_panel.set_role_injecting(False)
        self._append_log(
            "Force-released injection (Esc pressed). "
            f"Canvas mirroring continues. System-wide hotkey ({config.HANDOFF_HOTKEY_RELEASE}) lands in Step 7."
        )
        logger.info("Injection force-released via Esc")

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        """
        Intercept Esc while the window has focus to force-release OS injection.

        Only active when in RECEIVING state with injection enabled; otherwise
        the event passes through to the default handler.
        """
        if (
            event.key() == Qt.Key.Key_Escape
            and self._state_ctrl.state == SwitchState.RECEIVING
            and self._injection_enabled
        ):
            self._force_release_injection()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # State change handler
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: SwitchState) -> None:
        """Fired by StateController on every transition. Updates status bar."""
        mode = state.value.upper()
        x, y = pyautogui.position()
        role = self._role_label_for_state(state)
        self._status_bar.update_status(mode, x, y, role=role)

    # ------------------------------------------------------------------
    # Log panel
    # ------------------------------------------------------------------

    def _append_log(self, text: str) -> None:
        """Write a timestamped line to the log panel."""
        self._log_panel.append_line(f"[{_ts()}] {text}")

    def append_log(self, text: str) -> None:
        """Public interface for other components to write to the log panel."""
        self._append_log(text)
