"""
cursor-bridge-test -- PyQt6 main window.

Four regions:
  1. Connection panel (top): peer address field, Listen/Connect/Disconnect buttons,
     connection state indicator with peer info. Added in Step 3.
  2. Status bar: current switch-state, cursor coordinates, FPS.
  3. Canvas (center): filled square representing the local cursor position.
  4. Log panel (bottom, collapsible): last N lines from the event log.

Step 3 wires the connection panel to TcpTransport and StateController.
Step 4+ will replace the polling cursor dot with state-machine-driven movement.
"""

from __future__ import annotations

import datetime
import logging
import pyautogui

from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
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
from state.controller import StateController, SwitchState
from transport.socket_io import TcpTransport

logger = logging.getLogger(__name__)

# Dot size in logical pixels for the canvas cursor indicator.
_DOT_SIZE: int = 14


def _ts() -> str:
    """Return a short timestamp string for log lines."""
    return datetime.datetime.now().strftime("%H:%M:%S")


class _Canvas(QFrame):
    """
    Central canvas. Draws a filled square representing the local cursor position.

    In Step 1 the dot position is derived from pyautogui.position() scaled to
    the canvas widget dimensions. In later steps StateController drives it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(QSize(400, 300))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: #1a1a2e;")
        self._dot_x: float = 0.5
        self._dot_y: float = 0.5

    def set_normalized_position(self, nx: float, ny: float) -> None:
        """
        Update the dot to normalized position (0.0-1.0). Triggers a repaint.

        nx, ny are fractions of the local logical screen, not of this widget.
        """
        self._dot_x = max(0.0, min(1.0, nx))
        self._dot_y = max(0.0, min(1.0, ny))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # Draw grid lines at 25% intervals so the canvas feels spatial.
        pen = QPen(QColor("#2a2a4e"))
        pen.setWidth(1)
        painter.setPen(pen)
        for i in range(1, 4):
            x = int(w * i / 4)
            y = int(h * i / 4)
            painter.drawLine(x, 0, x, h)
            painter.drawLine(0, y, w, y)

        # Draw the cursor dot.
        dot_x = int(self._dot_x * w) - _DOT_SIZE // 2
        dot_y = int(self._dot_y * h) - _DOT_SIZE // 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e94560"))
        painter.drawRect(dot_x, dot_y, _DOT_SIZE, _DOT_SIZE)

        painter.end()


class _StatusBar(QWidget):
    """Top status bar. Shows switch-state mode, cursor coordinates, and FPS."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        self.setStyleSheet(
            "background-color: #16213e; color: #e0e0e0; font-family: monospace; font-size: 13px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._label = QLabel("Mode: IDLE  |  Cursor: (0, 0)  |  FPS: --")
        self._label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._label)

    def update_status(self, mode: str, x: int, y: int, fps: str = "--") -> None:
        self._label.setText(f"Mode: {mode}  |  Cursor: ({x}, {y})  |  FPS: {fps}")


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
        # Keep the newest line visible.
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
        is_busy = state in (
            SwitchState.LISTENING,
            SwitchState.CONNECTING,
            SwitchState.HANDSHAKING,
        )

        self._addr_field.setEnabled(is_idle)
        self._listen_btn.setEnabled(is_idle)
        self._connect_btn.setEnabled(is_idle)
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
        elif state == SwitchState.CONNECTED:
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


class MainWindow(QMainWindow):
    """Root application window. Owns the connection panel, status bar, canvas, and log panel."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Slickshift -- Cursor Bridge Test")
        self.setMinimumSize(QSize(700, 580))
        self.resize(960, 700)
        self.setStyleSheet("background-color: #0f0f1a;")

        # --- State and transport ---
        self._state_ctrl = StateController()
        self._transport = TcpTransport()
        self._peer_info: dict | None = None  # last received hello payload from peer

        # Wire transport signals to main-thread handlers.
        self._transport.register_connected_callback(self._on_transport_connected)
        self._transport.register_disconnected_callback(self._on_transport_disconnected)
        self._transport.register_message_callback(self._on_message_received)

        # Wire state changes to the connection panel and status bar.
        self._state_ctrl.register_state_change_callback(self._on_state_changed)

        # --- Widgets ---
        self._conn_panel = _ConnectionPanel()
        self._status_bar = _StatusBar()
        self._canvas = _Canvas()
        self._log_panel = _LogPanel()

        # Wire buttons.
        self._conn_panel.listen_btn.clicked.connect(self._on_listen_clicked)
        self._conn_panel.connect_btn.clicked.connect(self._on_connect_clicked)
        self._conn_panel.disconnect_btn.clicked.connect(self._on_disconnect_clicked)

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
        root_layout.addWidget(self._status_bar)
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

        # --- Cursor polling timer (Step 1 behavior, kept through Step 3) ---
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
    # Cursor polling (Step 1 behavior retained through Step 3)
    # ------------------------------------------------------------------

    def _poll_cursor(self) -> None:
        x, y = pyautogui.position()
        nx = x / self._screen_w
        ny = y / self._screen_h
        self._canvas.set_normalized_position(nx, ny)
        mode = self._state_ctrl.state.value.upper()
        self._status_bar.update_status(mode, x, y)

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_listen_clicked(self) -> None:
        self._peer_info = None
        self._state_ctrl.begin_listening()
        self._conn_panel.on_state_changed(SwitchState.LISTENING)
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
        self._append_log(f"Connecting to {host}:{port}")
        self._transport.connect_to_peer(host, port)

    def _on_disconnect_clicked(self) -> None:
        self._transport.close()
        self._state_ctrl.force_release()
        self._peer_info = None
        self._conn_panel.on_state_changed(SwitchState.IDLE)
        self._append_log("Disconnected by user")

    # ------------------------------------------------------------------
    # Transport event handlers (called on Qt main thread via signals)
    # ------------------------------------------------------------------

    def _on_transport_connected(self) -> None:
        """TCP link is up. Transition to HANDSHAKING and send hello."""
        self._state_ctrl.connection_established()
        self._conn_panel.on_state_changed(SwitchState.HANDSHAKING)
        self._append_log("Sending hello")
        self._transport.send_hello()

    def _on_transport_disconnected(self, reason: str) -> None:
        """Connection dropped or timed out."""
        self._state_ctrl.connection_lost(reason)
        self._peer_info = None
        self._conn_panel.on_state_changed(SwitchState.IDLE, peer_info=None)
        self._append_log(f"Connection lost: {reason}")

    def _on_message_received(self, message: dict) -> None:
        """Dispatch incoming messages by type."""
        msg_type = message.get("type", "")

        if msg_type == "hello":
            self._handle_hello(message)
        else:
            # Unknown message types are logged but not fatal. Step 4+ adds more.
            logger.debug("Unknown message type: %r", msg_type)

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
        self._append_log(
            f"Received hello from peer: {name} ({plat})  {lw}x{lh}  dpi={dpi}"
        )
        self._append_log("Connection established")

    # ------------------------------------------------------------------
    # State change handler
    # ------------------------------------------------------------------

    def _on_state_changed(self, state: SwitchState) -> None:
        """Fired by StateController on every transition. Updates status bar."""
        # The connection panel is updated directly in the button handlers and
        # transport callbacks, where peer_info context is available. The state
        # controller callback handles the status bar only.
        mode = state.value.upper()
        x, y = pyautogui.position()
        self._status_bar.update_status(mode, x, y)

    # ------------------------------------------------------------------
    # Log panel
    # ------------------------------------------------------------------

    def _append_log(self, text: str) -> None:
        """Write a timestamped line to the log panel."""
        self._log_panel.append_line(f"[{_ts()}] {text}")

    def append_log(self, text: str) -> None:
        """Public interface for other components to write to the log panel."""
        self._append_log(text)
