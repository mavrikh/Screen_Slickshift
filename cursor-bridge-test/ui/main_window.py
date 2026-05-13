"""
cursor-bridge-test -- PyQt6 main window.

Five regions:
  1. Connection panel (top): peer address field, Listen/Connect/Disconnect buttons,
     connection state indicator with peer info. Added in Step 3.
  2. Mirroring panel (below connection): Start/Stop Mirroring toggle, role display,
     Inject checkbox (receiver only), and Peer Layout dropdown (Step 6).
  3. Status bar: current switch-state, cursor coordinates, FPS, role, delta stats.
  4. Canvas (center): red square represents the local cursor position (always).
     In RECEIVING state a second green square mirrors the incoming sender deltas.
     In TRANSITIONING state the red square dims to grey to signal "leaving".
  5. Log panel (bottom, collapsible): last N lines from the event log.

Step 4 wires the mirroring button to DeltaCapture and the transport send queue.
Step 5 adds OS-level injection on the receiver side via MouseInjector, an Inject
checkbox (defaults off), macOS Accessibility permission probe, Esc force-release,
and status bar distinction between canvas-only and injecting receiver roles.
Step 6 adds:
  - Peer Layout dropdown: "Peer is to the: Right/Left/Top/Bottom" (CONNECTED/IDLE only).
  - Edge-band detection (EDGE_BAND_PX=2) with EDGE_DWELL_S=0.25 s dwell timer.
  - handoff_request / handoff_ack acknowledge-based handoff protocol.
  - Entry-edge cursor warp with normalized perpendicular position.
  - HANDOFF_ACK_TIMEOUT_S=1.0 s rollback if peer does not ack in time.
  - HANDOFF_COOLDOWN_S=0.5 s cooldown prevents immediate re-trigger after warp.
  - TRANSITIONING visual feedback: orange status bar role + dimmed grey canvas square.
  - Disconnect during TRANSITIONING returns cleanly to IDLE with no stuck state.
Step 7 adds:
  - Sender-side DPI scale read from QScreen.devicePixelRatio() at startup.
  - Status bar shows active DPI scale for Sender and Receiver (injecting) roles
    (informational only -- DPI is no longer load-bearing in the math).
  - Hello receipt logs peer's logical, physical, and DPI values in a single line.
Step 9 simplification:
  - pyautogui.size() and pyautogui.position() are internally consistent on each
    platform. Normalized deltas use pyautogui.size() directly on both sides.
  - Dropped effective_screen_w/h multiplications and Ctrl+Shift+D DPI toggle.
  - position_in_physical_pixels heuristic retained for diagnostic logging only.
"""

from __future__ import annotations

import collections
import datetime
import logging
import sys
import threading
import time
import pyautogui
from pynput import keyboard as _pynput_keyboard

from PyQt6.QtCore import QObject, QSettings, Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
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
from capture.event_capture import EventCapture
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


class _MainSignals(QObject):
    """
    Qt signal carrier for MainWindow's capture-thread callbacks.

    Lives on the main thread so signals cross the thread boundary safely via
    Qt's queued connection mechanism. All callbacks from non-Qt threads must go
    through these signals -- never call Qt widgets directly from a non-Qt thread.

    Matches the _TransportSignals pattern in transport/socket_io.py.
    """

    # Emitted by the capture thread when edge dwell completes.
    # Args: sender_edge (str), perp (float).
    edge_dwell_fired = pyqtSignal(str, float)

    # Emitted by the pynput listener thread when a mouse button event fires.
    # Args: button_name (str: "left"|"right"|"middle"), pressed (bool).
    click_fired = pyqtSignal(str, bool)

    # Emitted by the pynput listener thread when a scroll wheel event fires.
    # Args: dx (int), dy (int).
    scroll_fired = pyqtSignal(int, int)

    # Emitted by the pynput keyboard listener thread when the system-wide
    # Ctrl+Alt+Shift+Esc combo is detected. Step 8.
    force_release_pressed = pyqtSignal()

    # Emitted by the pynput keyboard listener thread when a key event fires
    # and keyboard forwarding is active. Step 9.
    # Args: key_name (str -- pyautogui name), pressed (bool).
    key_event_fired = pyqtSignal(str, bool)


class _Canvas(QFrame):
    """
    Central canvas. Draws two squares:
    - Red square: local cursor position (always visible unless dimmed).
    - Green square: mirrors the sender's incoming deltas (RECEIVING state only).

    In RECEIVING state the green square starts at canvas center and accumulates
    normalized deltas from the peer. It does NOT represent any OS cursor position --
    it is a visual confirmation that delta math is working before injection is added
    in Step 5.

    In TRANSITIONING state (Step 6) the local square dims to grey to signal that
    the local cursor is leaving. The green square is not shown in TRANSITIONING.
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

        # When True, paint the local square as dim grey (TRANSITIONING feedback).
        self._local_dimmed: bool = False

    def set_local_position(self, nx: float, ny: float) -> None:
        """Update the red (local) square. Triggers a repaint."""
        self._local_x = max(0.0, min(1.0, nx))
        self._local_y = max(0.0, min(1.0, ny))
        self.update()

    # Keep the legacy name for compatibility with existing callers.
    def set_normalized_position(self, nx: float, ny: float) -> None:
        self.set_local_position(nx, ny)

    def set_local_dimmed(self, dimmed: bool) -> None:
        """
        Dim or restore the local (red) square.

        Call with dimmed=True when entering TRANSITIONING so the user sees
        that the local cursor is about to hand off. Call with dimmed=False on
        any other state transition.
        """
        self._local_dimmed = dimmed
        self.update()

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

        # Local cursor square: red normally, grey when TRANSITIONING.
        lx = int(self._local_x * w) - _DOT_SIZE // 2
        ly = int(self._local_y * h) - _DOT_SIZE // 2
        painter.setPen(Qt.PenStyle.NoPen)
        local_color = QColor("#555555") if self._local_dimmed else QColor("#e94560")
        painter.setBrush(local_color)
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

    Step 6: Role text is coloured orange (#f08040) in TRANSITIONING state to give
    a clear visual cue that a handoff is in flight and awaiting ACK.
    Step 9: Appends "| KBD" in red (#ff6060) when keyboard forwarding is live.
    """

    _COLOR_DEFAULT = "#e0e0e0"
    _COLOR_TRANSITIONING = "#f08040"
    _COLOR_RECONNECTING = "#c08040"
    _COLOR_KBD_ACTIVE = "#ff6060"

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
        transitioning: bool = False,
        reconnecting: bool = False,
        kbd_active: bool = False,
    ) -> None:
        parts = [f"Mode: {mode}", f"Cursor: ({x}, {y})", f"Role: {role}"]
        if extra:
            parts.append(extra)
        base_text = "  |  ".join(parts)
        if kbd_active:
            # Step 9: append KBD indicator in a separate span so only that word
            # is styled red while the rest of the label keeps its default colour.
            self._label.setText(
                f'{base_text}  |  <span style="color: {self._COLOR_KBD_ACTIVE};">KBD</span>'
            )
            self._label.setTextFormat(Qt.TextFormat.RichText)
        else:
            self._label.setText(base_text)
            self._label.setTextFormat(Qt.TextFormat.PlainText)
        if transitioning:
            color = self._COLOR_TRANSITIONING
        elif reconnecting:
            color = self._COLOR_RECONNECTING
        else:
            color = self._COLOR_DEFAULT
        self._label.setStyleSheet(f"color: {color};")


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
        elif state in (
            SwitchState.CONNECTED,
            SwitchState.CAPTURING,
            SwitchState.RECEIVING,
            SwitchState.TRANSITIONING,
            SwitchState.RECONNECTING,
        ):
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

    def address_text(self) -> str:
        """Return the raw text currently in the peer address field."""
        return self._addr_field.text().strip()

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
    Mirroring panel (Steps 4, 5, 6).

    Contains:
    - Start Mirroring button: only enabled in CONNECTED state.
    - Stop Mirroring button: only enabled in CAPTURING state.
    - Inject checkbox (Step 5): receiver only, defaults unchecked.
    - Peer Layout dropdown (Step 6): "Peer is to the: Right/Left/Top/Bottom".
      Enabled only in CONNECTED or IDLE states. Disabled during CAPTURING,
      RECEIVING, and TRANSITIONING to prevent mid-handoff layout changes.
    - Role label: displays the current role.

    The panel does not hold business logic. Signals are connected in MainWindow.
    """

    _COLOR_IDLE = "#888888"
    _COLOR_SENDER = "#f0c040"
    _COLOR_RECEIVER = "#40a0ff"
    _COLOR_RECEIVER_INJECTING = "#40ffc0"
    _COLOR_TRANSITIONING = "#f08040"

    # Edge options in display order. The value is the config key passed to DeltaCapture.
    _EDGE_OPTIONS: list[tuple[str, str]] = [
        ("Right", "right"),
        ("Left", "left"),
        ("Top", "top"),
        ("Bottom", "bottom"),
    ]

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

        # Step 5: Inject checkbox. Defaults to checked so injection starts
        # immediately when the machine first enters RECEIVING (assuming the
        # macOS Accessibility probe passes). The checkbox is disabled (greyed)
        # in all states except RECEIVING, so the visual "checked" state is
        # inert until the machine receives a mirror_start message.
        self._inject_chk = QCheckBox("Inject")
        self._inject_chk.setChecked(True)
        self._inject_chk.setEnabled(False)
        self._inject_chk.setStyleSheet(
            "QCheckBox { color: #b0b0b0; font-family: monospace; font-size: 12px; }"
            "QCheckBox:disabled { color: #555555; }"
        )
        layout.addWidget(self._inject_chk)

        # Step 9: Forward Keyboard checkbox. Defaults to unchecked. Only
        # enabled in CAPTURING state so the sender explicitly opts in.
        # Tooltip explains the dual-fire behaviour.
        self._kbd_fwd_chk = QCheckBox("Forward Keyboard")
        self._kbd_fwd_chk.setChecked(False)
        self._kbd_fwd_chk.setEnabled(False)
        self._kbd_fwd_chk.setToolTip(
            "Forwards every keystroke to the peer. "
            "Sender's local apps also receive keystrokes. Use with caution."
        )
        self._kbd_fwd_chk.setStyleSheet(
            "QCheckBox { color: #b0b0b0; font-family: monospace; font-size: 12px; }"
            "QCheckBox:disabled { color: #555555; }"
        )
        layout.addWidget(self._kbd_fwd_chk)

        # Step 6: Peer Layout dropdown.
        peer_label = QLabel("Peer is to the:")
        peer_label.setStyleSheet("color: #b0b0b0; font-family: monospace; font-size: 12px;")
        layout.addWidget(peer_label)

        self._peer_edge_combo = QComboBox()
        for display, _ in self._EDGE_OPTIONS:
            self._peer_edge_combo.addItem(display)
        self._peer_edge_combo.setCurrentIndex(0)  # default: Right
        self._peer_edge_combo.setStyleSheet(
            "QComboBox { background-color: #16213e; color: #e0e0e0; "
            "font-family: monospace; font-size: 12px; border: 1px solid #3a3a6e; "
            "padding: 3px 6px; } "
            "QComboBox:disabled { color: #555555; border-color: #2a2a4e; } "
            "QComboBox::drop-down { border: none; } "
            "QComboBox QAbstractItemView { background-color: #16213e; color: #e0e0e0; "
            "selection-background-color: #1e2f5e; font-family: monospace; font-size: 12px; }"
        )
        # Dropdown is editable only when idle or connected; disable during active states.
        self._peer_edge_combo.setEnabled(True)
        layout.addWidget(self._peer_edge_combo)

        layout.addStretch()

        self._role_label = QLabel("Role: Idle")
        self._role_label.setStyleSheet(
            f"color: {self._COLOR_IDLE}; font-family: monospace; font-size: 12px;"
        )
        layout.addWidget(self._role_label)

    def on_state_changed(self, state: SwitchState) -> None:
        """Update button states, role label, and dropdown enable to match the new switch state."""
        # Dropdown is editable only when nothing active is happening.
        dropdown_enabled = state in (SwitchState.CONNECTED, SwitchState.IDLE)
        self._peer_edge_combo.setEnabled(dropdown_enabled)

        if state == SwitchState.CONNECTED:
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(False)
            self._kbd_fwd_chk.setEnabled(False)
            self._set_role("Idle", self._COLOR_IDLE)
        elif state == SwitchState.CAPTURING:
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
            self._inject_chk.setEnabled(False)
            # Forward Keyboard is opt-in; only interactive while CAPTURING.
            self._kbd_fwd_chk.setEnabled(True)
            self._set_role("Sender", self._COLOR_SENDER)
        elif state == SwitchState.RECEIVING:
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(True)
            self._kbd_fwd_chk.setEnabled(False)
            self._set_role("Receiver", self._COLOR_RECEIVER)
        elif state == SwitchState.TRANSITIONING:
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(False)
            self._kbd_fwd_chk.setEnabled(False)
            self._set_role("Handing off...", self._COLOR_TRANSITIONING)
        elif state == SwitchState.RECONNECTING:
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(False)
            self._kbd_fwd_chk.setEnabled(False)
            self._set_role("Reconnecting...", self._COLOR_TRANSITIONING)
        else:
            # IDLE, LISTENING, CONNECTING, HANDSHAKING -- all active buttons off.
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(False)
            self._kbd_fwd_chk.setEnabled(False)
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

    def selected_edge(self) -> str:
        """Return the currently selected edge key (e.g. 'right', 'left', 'top', 'bottom')."""
        idx = self._peer_edge_combo.currentIndex()
        if 0 <= idx < len(self._EDGE_OPTIONS):
            return self._EDGE_OPTIONS[idx][1]
        return "right"

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

    @property
    def kbd_fwd_chk(self) -> QCheckBox:
        return self._kbd_fwd_chk

    @property
    def peer_edge_combo(self) -> QComboBox:
        return self._peer_edge_combo


class MainWindow(QMainWindow):
    """Root application window. Owns all panels, state, transport, and capture."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Slickshift -- Cursor Bridge Test")
        self.setMinimumSize(QSize(700, 620))
        self.resize(960, 740)
        self.setStyleSheet("background-color: #0f0f1a;")

        # --- QSettings: cross-platform persistent key/value store ---
        # Used to remember the last peer address across launches.
        # macOS: ~/Library/Preferences/com.Slickshift.CursorBridgeTest.plist
        # Windows: registry under HKCU\Software\Slickshift\CursorBridgeTest
        # Linux:   ~/.config/Slickshift/CursorBridgeTest.ini
        self._settings = QSettings("Slickshift", "CursorBridgeTest")

        # --- DPI scale for this machine (Step 7) ---
        # Read from Qt's primary screen so it matches what the hello payload reports.
        # Stored here so both DeltaCapture and move_relative use the same value.
        _primary_screen = QApplication.primaryScreen()
        self._dpi_scale: float = (
            _primary_screen.devicePixelRatio() if _primary_screen is not None else 1.0
        )

        # --- Empirical DPI mode detection (replaces platform heuristic) ---
        # Gather display diagnostics from both Qt and pyautogui at startup so the
        # comparison is grounded in actual reported values, not sys.platform.
        #
        # Qt reports logical pixels (device-independent points). pyautogui.size()
        # may report either logical or physical pixels depending on the OS DPI
        # awareness mode. If pyautogui is running in physical-pixel mode its size
        # will be larger than Qt's logical size by approximately dpi_scale.
        #
        # Diagnostic values (logged below for Master to paste back):
        _qt_size = _primary_screen.size() if _primary_screen is not None else None
        _qt_geom_size = _primary_screen.geometry().size() if _primary_screen is not None else None
        _qt_logical_w: int = _qt_size.width() if _qt_size is not None else 0
        _qt_logical_h: int = _qt_size.height() if _qt_size is not None else 0
        _qt_geom_w: int = _qt_geom_size.width() if _qt_geom_size is not None else 0
        _qt_geom_h: int = _qt_geom_size.height() if _qt_geom_size is not None else 0
        _pyautogui_size = pyautogui.size()
        _pyautogui_w: int = _pyautogui_size[0]
        _pyautogui_h: int = _pyautogui_size[1]
        _pyautogui_pos = pyautogui.position()

        # Empirical flag: if pyautogui reports a wider screen than Qt's logical
        # width by more than 10%, pyautogui is operating in physical-pixel mode.
        # The 10% tolerance absorbs minor rounding differences.
        _position_in_physical_pixels_empirical: bool = (
            _pyautogui_w > _qt_logical_w * 1.1
        )
        self._position_in_physical_pixels: bool = _position_in_physical_pixels_empirical

        logger.info(
            "Startup display info: qt_size=%dx%d qt_geom=%dx%d "
            "qt_pixel_ratio=%.2f pyautogui_size=%dx%d pyautogui_pos=(%d,%d)",
            _qt_logical_w, _qt_logical_h,
            _qt_geom_w, _qt_geom_h,
            self._dpi_scale,
            _pyautogui_w, _pyautogui_h,
            _pyautogui_pos[0], _pyautogui_pos[1],
        )
        if self._position_in_physical_pixels:
            logger.info(
                "position_in_physical_pixels=True "
                "(pyautogui_w=%d > qt_logical_w=%d * 1.1)",
                _pyautogui_w, _qt_logical_w,
            )
        else:
            logger.info(
                "position_in_physical_pixels=False "
                "(pyautogui_w=%d <= qt_logical_w=%d * 1.1)",
                _pyautogui_w, _qt_logical_w,
            )

        # --- State, transport, capture, and injection ---
        self._state_ctrl = StateController()
        self._transport = TcpTransport()
        # Reuse the pyautogui size already captured above for the diagnostic log.
        # This avoids a second OS call and guarantees the values are identical
        # to what the startup diagnostic line reported.
        self._screen_w: int = _pyautogui_w
        self._screen_h: int = _pyautogui_h
        self._capture = DeltaCapture(
            self._screen_w,
            self._screen_h,
        )
        self._injector = MouseInjector()
        self._peer_info: dict | None = None  # last received hello payload from peer

        # Signal bridge: capture-thread -> Qt main thread.
        # Must be created on the main thread (here in __init__) so Qt assigns it
        # to the main thread's event loop. Matches the _TransportSignals pattern.
        self._main_signals = _MainSignals()
        self._main_signals.edge_dwell_fired.connect(self._on_edge_dwell_fired)
        self._main_signals.click_fired.connect(self._on_click_fired)
        self._main_signals.scroll_fired.connect(self._on_scroll_fired)

        # Event capture: pynput-based button and scroll event listener.
        # Runs for the lifetime of the window; the _active gate is toggled with
        # CAPTURING state. Constructed here so start() can be called below.
        self._event_capture = EventCapture(
            on_click=self._on_event_capture_click,
            on_scroll=self._on_event_capture_scroll,
        )

        # Whether OS cursor injection is currently active on this machine.
        # Defaults to True to match the Inject checkbox default-on state.
        # The checkbox is disabled until RECEIVING state, so this flag is inert
        # until the probe runs on first RECEIVING entry.
        self._injection_enabled: bool = True

        # Step 6: QTimer that fires after HANDOFF_ACK_TIMEOUT_S if no handoff_ack
        # arrives from the peer. On timeout, rolls back TRANSITIONING -> CAPTURING.
        # Single-shot; started when we enter TRANSITIONING, cancelled on ack or rollback.
        self._handoff_ack_timer = QTimer(self)
        self._handoff_ack_timer.setSingleShot(True)
        self._handoff_ack_timer.setInterval(int(config.HANDOFF_ACK_TIMEOUT_S * 1000))
        self._handoff_ack_timer.timeout.connect(self._on_handoff_ack_timeout)

        # Step 8: user-initiated disconnect flag.
        # Set to True when the user explicitly clicks Disconnect or presses the
        # force-release hotkey. Reset to False on each Connect/Listen attempt.
        # When False and the transport fires a disconnected signal, the reconnect
        # path is entered instead of going straight to IDLE.
        self._user_initiated_disconnect: bool = False

        # Step 8: flag set while a reconnect attempt is in progress and the
        # handshake is completing. Tells _handle_hello to restore the prior role
        # instead of staying at CONNECTED.
        self._reconnect_restoring_role: bool = False

        # Step 8: reconnect state.
        # _reconnect_prev_role: the SwitchState at time of drop (CAPTURING or RECEIVING).
        # _reconnect_attempt: current attempt number (1-based).
        # _reconnect_delay_s: next delay in seconds (grows with backoff).
        # _reconnect_host/_reconnect_port: saved peer address from QSettings at drop time.
        self._reconnect_prev_role: SwitchState = SwitchState.IDLE
        self._reconnect_attempt: int = 0
        self._reconnect_delay_s: float = config.RECONNECT_INITIAL_DELAY_S
        self._reconnect_host: str = ""
        self._reconnect_port: int = config.DEFAULT_PORT

        # Step 8: reconnect timer. Single-shot; restarted on each failed attempt.
        # Runs on the Qt main thread so all state transitions are thread-safe.
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._on_reconnect_attempt)

        # Step 8: idle timeout in RECEIVING state.
        # Fires every 1 s while in RECEIVING. Compares against _last_delta_received_time.
        # Transitions RECEIVING -> CONNECTED if no delta/click/scroll arrives in
        # IDLE_TIMEOUT_S seconds.
        self._last_delta_received_time: float = 0.0
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(1000)  # 1 s tick
        self._idle_timer.timeout.connect(self._check_idle_timeout)

        # Step 8: system-wide force-release hotkey via pynput keyboard listener.
        # Tracks which modifier/special keys are currently held. The combo fires when
        # Ctrl+Alt+Shift+Esc are simultaneously pressed.
        # On macOS this requires Accessibility permission (same grant as the mouse
        # listener). Without it the listener starts but silently receives no events;
        # the in-window Esc handler remains a fallback in that case.
        self._kb_pressed_keys: set[_pynput_keyboard.Key | _pynput_keyboard.KeyCode] = set()
        self._kb_pressed_keys_lock = threading.Lock()
        self._kb_listener: _pynput_keyboard.Listener | None = None

        # Step 9: keyboard forwarding state.
        # True only when the checkbox is checked AND state is CAPTURING.
        # Toggled by _on_kbd_fwd_toggled and cleared on CAPTURING exit.
        self._keyboard_forwarding_active: bool = False

        # Translation map: pynput Key enum -> pyautogui key name string.
        # Used by _translate_pynput_key(). Keys not in this map and not
        # KeyCode-with-char are logged at DEBUG and skipped.
        self._PYNPUT_KEY_MAP: dict[_pynput_keyboard.Key, str] = {
            _pynput_keyboard.Key.shift: "shift",
            _pynput_keyboard.Key.shift_l: "shift",
            _pynput_keyboard.Key.shift_r: "shiftright",
            _pynput_keyboard.Key.ctrl: "ctrl",
            _pynput_keyboard.Key.ctrl_l: "ctrl",
            _pynput_keyboard.Key.ctrl_r: "ctrlright",
            _pynput_keyboard.Key.alt: "alt",
            _pynput_keyboard.Key.alt_l: "alt",
            _pynput_keyboard.Key.alt_r: "altright",
            _pynput_keyboard.Key.cmd: "command",
            _pynput_keyboard.Key.cmd_r: "cmdright",
            _pynput_keyboard.Key.enter: "enter",
            _pynput_keyboard.Key.esc: "esc",
            _pynput_keyboard.Key.space: "space",
            _pynput_keyboard.Key.tab: "tab",
            _pynput_keyboard.Key.backspace: "backspace",
            _pynput_keyboard.Key.delete: "delete",
            _pynput_keyboard.Key.up: "up",
            _pynput_keyboard.Key.down: "down",
            _pynput_keyboard.Key.left: "left",
            _pynput_keyboard.Key.right: "right",
            _pynput_keyboard.Key.f1: "f1",
            _pynput_keyboard.Key.f2: "f2",
            _pynput_keyboard.Key.f3: "f3",
            _pynput_keyboard.Key.f4: "f4",
            _pynput_keyboard.Key.f5: "f5",
            _pynput_keyboard.Key.f6: "f6",
            _pynput_keyboard.Key.f7: "f7",
            _pynput_keyboard.Key.f8: "f8",
            _pynput_keyboard.Key.f9: "f9",
            _pynput_keyboard.Key.f10: "f10",
            _pynput_keyboard.Key.f11: "f11",
            _pynput_keyboard.Key.f12: "f12",
            _pynput_keyboard.Key.home: "home",
            _pynput_keyboard.Key.end: "end",
            _pynput_keyboard.Key.page_up: "pageup",
            _pynput_keyboard.Key.page_down: "pagedown",
            _pynput_keyboard.Key.insert: "insert",
            _pynput_keyboard.Key.caps_lock: "capslock",
            _pynput_keyboard.Key.num_lock: "numlock",
        }

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

        # --- DPI diagnostic label (read-only, set once at startup) ---
        _diag_text: str = (
            f"Display: qt={_qt_logical_w}x{_qt_logical_h}"
            f"  pa={_pyautogui_w}x{_pyautogui_h}"
            f"  dpi={self._dpi_scale:.2f}"
            f"  phys_pos={self._position_in_physical_pixels}"
        )
        self._dpi_diag_label = QLabel(_diag_text)
        self._dpi_diag_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        _diag_font = QFont()
        _diag_font.setStyleHint(QFont.StyleHint.Monospace)
        _diag_font.setFamily("Menlo")
        _diag_font.setPointSize(11)
        self._dpi_diag_label.setFont(_diag_font)
        self._dpi_diag_label.setStyleSheet(
            "color: #909090; background-color: transparent; padding: 2px 8px;"
        )

        # Wire connection buttons.
        self._conn_panel.listen_btn.clicked.connect(self._on_listen_clicked)
        self._conn_panel.connect_btn.clicked.connect(self._on_connect_clicked)
        self._conn_panel.disconnect_btn.clicked.connect(self._on_disconnect_clicked)

        # Wire mirroring buttons.
        self._mirror_panel.start_btn.clicked.connect(self._on_start_mirroring_clicked)
        self._mirror_panel.stop_btn.clicked.connect(self._on_stop_mirroring_clicked)

        # Wire Inject checkbox (Step 5).
        self._mirror_panel.inject_chk.stateChanged.connect(self._on_inject_toggled)

        # Wire Forward Keyboard checkbox (Step 9).
        self._mirror_panel.kbd_fwd_chk.stateChanged.connect(self._on_kbd_fwd_toggled)

        # Step 6: Wire peer-edge dropdown. When the user changes it we update
        # DeltaCapture immediately so edge detection uses the new edge on the next tick.
        self._mirror_panel.peer_edge_combo.currentIndexChanged.connect(
            self._on_peer_edge_changed
        )

        # Wire edge-dwell callback from DeltaCapture to the Qt main thread.
        # _schedule_handoff_fire is called on the capture background thread; it emits
        # _main_signals.edge_dwell_fired which is delivered to _on_edge_dwell_fired on
        # the main thread via Qt's queued connection (see _MainSignals above).
        self._capture.set_edge_dwell_callback(self._schedule_handoff_fire)

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
        root_layout.addWidget(self._dpi_diag_label)
        root_layout.addWidget(self._mirror_panel)
        root_layout.addWidget(self._status_bar)
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

        # --- Cursor polling timer ---
        # Used for the local red-square canvas update and status bar coordinate display.
        # Also drives the sender delta dispatch when in CAPTURING state (the capture
        # module runs its own thread; this timer is only for the UI).
        # Note: self._screen_w, self._screen_h, and self._dpi_scale are set above
        # alongside DeltaCapture construction so all three share the same values.
        pyautogui.FAILSAFE = config.PYAUTOGUI_FAILSAFE

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(config.CANVAS_REFRESH_MS)
        self._poll_timer.timeout.connect(self._poll_cursor)
        self._poll_timer.start()

        # Dead-man monitor: independent of the heartbeat sender thread.
        # Ticks every 500 ms on the Qt main thread so it fires even when the
        # sender thread is blocked on send(). Primary detection path for
        # dead-man invariant #4. Started/stopped on connection state transitions.
        self._dead_man_timer = QTimer(self)
        self._dead_man_timer.setInterval(500)
        self._dead_man_timer.timeout.connect(self._check_dead_man)

        # Wire force-release hotkey signal (Step 8).
        self._main_signals.force_release_pressed.connect(self._on_system_force_release)

        # Wire keyboard forwarding signal (Step 9).
        self._main_signals.key_event_fired.connect(self._on_key_event_fired)

        # Start the pynput mouse listener. It runs for the full window lifetime and is
        # gated off until set_active(True) is called on CAPTURING entry.
        self._event_capture.start()

        # Start the system-wide keyboard listener for Ctrl+Alt+Shift+Esc (Step 8).
        self._start_kb_listener()

        logger.info(
            "MainWindow initialized. Screen (pyautogui): %dx%d  dpi_scale=%.2f. Polling at %dms.",
            self._screen_w, self._screen_h, self._dpi_scale, config.CANVAS_REFRESH_MS,
        )

        # --- QSettings: restore last peer address on startup (Ask 3) ---
        _last_addr: str = self._settings.value("last_peer_address", "", type=str)
        if _last_addr:
            self._conn_panel._addr_field.setText(_last_addr)
            logger.info("Restored last peer address from settings: %s", _last_addr)

        # --- QSettings: restore keyboard forwarding checkbox preference (Step 9) ---
        # The checkbox is disabled at startup (not CAPTURING), so restoring its
        # checked state here only sets the visual preference; the flag stays False
        # until the user enters CAPTURING and the checkbox is enabled.
        _kbd_fwd_saved: bool = self._settings.value(
            "keyboard_forwarding_enabled",
            config.KEYBOARD_FORWARDING_DEFAULT,
            type=bool,
        )
        self._mirror_panel.kbd_fwd_chk.blockSignals(True)
        self._mirror_panel.kbd_fwd_chk.setChecked(_kbd_fwd_saved)
        self._mirror_panel.kbd_fwd_chk.blockSignals(False)
        if _kbd_fwd_saved:
            logger.info("Restored keyboard forwarding preference: enabled (checkbox pre-checked)")

    # ------------------------------------------------------------------
    # Cursor polling (local red square + status bar coordinates)
    # ------------------------------------------------------------------

    def _poll_cursor(self) -> None:
        x, y = pyautogui.position()
        # Normalize against pyautogui.size() -- position() and size() are in the
        # same unit on each platform, so nx/ny correctly represent 0.0-1.0 across
        # the full screen range without any dpi_scale correction.
        nx = x / self._screen_w
        ny = y / self._screen_h
        self._canvas.set_local_position(nx, ny)

        state = self._state_ctrl.state
        mode = state.value.upper()
        role = self._role_label_for_state(state)
        extra = self._extra_status_for_state(state)
        transitioning = (state == SwitchState.TRANSITIONING)
        reconnecting = (state == SwitchState.RECONNECTING)
        self._status_bar.update_status(
            mode, x, y, role=role, extra=extra,
            transitioning=transitioning, reconnecting=reconnecting,
            kbd_active=self._keyboard_forwarding_active,
        )

    def _role_label_for_state(self, state: SwitchState) -> str:
        dpi_str = f"{self._dpi_scale:.2f}"
        if state == SwitchState.CAPTURING:
            return f"Sender | DPI: {dpi_str}"
        if state == SwitchState.TRANSITIONING:
            return "Handing off..."
        if state == SwitchState.RECONNECTING:
            attempt = self._reconnect_attempt
            return f"Reconnecting ({attempt}/{config.RECONNECT_MAX_ATTEMPTS})..."
        if state == SwitchState.RECEIVING and self._injection_enabled:
            return f"Receiver (injecting) | DPI: {dpi_str}"
        if state == SwitchState.RECEIVING:
            return "Receiver"
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
        self._user_initiated_disconnect = False
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
        # Persist the address for next launch (silent, no UI prompt).
        self._settings.setValue("last_peer_address", self._conn_panel.address_text())
        self._user_initiated_disconnect = False
        self._peer_info = None
        self._state_ctrl.begin_connecting()
        self._conn_panel.on_state_changed(SwitchState.CONNECTING)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTING)
        self._append_log(f"Connecting to {host}:{port}")
        self._transport.connect_to_peer(host, port)

    def _on_disconnect_clicked(self) -> None:
        self._user_initiated_disconnect = True
        # Cancel any in-flight reconnect loop (Step 8).
        self._reconnect_timer.stop()
        self._idle_timer.stop()
        self._dead_man_timer.stop()
        self._handoff_ack_timer.stop()  # Step 6: cancel any in-flight handoff
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._canvas.set_local_dimmed(False)
        self._canvas.hide_remote()
        # Notify peer before closing so it treats the drop as user-initiated and
        # skips its reconnect path. Best-effort: if the send fails (peer already
        # gone), log and proceed. Matches the force_release notification pattern.
        if self._state_ctrl.state not in (SwitchState.IDLE, SwitchState.RECONNECTING):
            try:
                self._transport.send({"type": "user_disconnect"})
            except Exception:
                logger.info("user_disconnect notification failed (peer already gone) -- proceeding with close")
        self._transport.close()
        if self._state_ctrl.state == SwitchState.RECONNECTING:
            self._state_ctrl.reconnect_canceled()
        else:
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
        Step 6: configure the capture module with the selected peer edge before starting.
        Step 7: sync the current DPI correction flag into DeltaCapture before starting.
        """
        edge = self._mirror_panel.selected_edge()
        self._capture.set_peer_edge(edge)
        self._state_ctrl.start_mirroring()
        self._transport.send({"type": "mirror_start"})
        self._capture.start(self._transport.enqueue_delta)
        self._event_capture.set_active(True)
        self._conn_panel.on_state_changed(SwitchState.CAPTURING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CAPTURING)
        self._append_log(f"Mirroring started -- this machine is now the Sender (peer to the {edge})")

    def _on_peer_edge_changed(self, index: int) -> None:  # noqa: ARG002
        """
        Peer-edge dropdown selection changed. Update DeltaCapture immediately.

        The dropdown is disabled during CAPTURING/RECEIVING/TRANSITIONING so this
        handler only fires when the state is CONNECTED or IDLE, which is safe.
        """
        edge = self._mirror_panel.selected_edge()
        self._capture.set_peer_edge(edge)
        logger.info("Peer edge updated to: %s", edge)

    def _on_stop_mirroring_clicked(self) -> None:
        """Stop capturing and notify peer to exit RECEIVING."""
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._state_ctrl.stop_mirroring()
        self._transport.send({"type": "mirror_stop"})
        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log("Mirroring stopped")

    def _stop_capture_if_running(self) -> None:
        """
        Stop DeltaCapture if it is currently polling.

        Also covers TRANSITIONING: the capture thread is still alive (paused)
        during a handoff-in-flight. If the connection dies mid-handoff, we must
        stop it cleanly to avoid leaving a zombie thread.
        """
        state = self._state_ctrl.state
        if state in (SwitchState.CAPTURING, SwitchState.TRANSITIONING):
            self._capture.set_paused(False)  # unpause before stop so the thread exits cleanly
            self._capture.stop()

    # ------------------------------------------------------------------
    # Transport event handlers (called on Qt main thread via signals)
    # ------------------------------------------------------------------

    def _on_transport_connected(self) -> None:
        """TCP link is up. Transition to HANDSHAKING and send hello."""
        self._state_ctrl.connection_established()
        self._conn_panel.on_state_changed(SwitchState.HANDSHAKING)
        self._mirror_panel.on_state_changed(SwitchState.HANDSHAKING)
        self._dead_man_timer.start()
        self._append_log("Sending hello")
        self._transport.send_hello()

    def _on_transport_disconnected(self, reason: str) -> None:
        """
        Connection dropped or timed out.

        Step 6: if we were mid-handoff (TRANSITIONING), cancel the ack timer and
        restore the canvas before transitioning to IDLE. No stuck state remains.

        Step 8: if the drop was NOT user-initiated and we were in an active mirroring
        role (CAPTURING, RECEIVING, TRANSITIONING), enter the auto-reconnect path
        instead of going directly to IDLE.
        """
        self._dead_man_timer.stop()
        self._handoff_ack_timer.stop()
        self._idle_timer.stop()
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._canvas.set_local_dimmed(False)
        self._canvas.hide_remote()

        prev_state = self._state_ctrl.state

        # Determine whether to attempt auto-reconnect.
        # Active mirroring states where reconnect makes sense.
        _reconnect_eligible: frozenset[SwitchState] = frozenset({
            SwitchState.CAPTURING,
            SwitchState.RECEIVING,
            SwitchState.TRANSITIONING,
        })
        should_reconnect = (
            not self._user_initiated_disconnect
            and prev_state in _reconnect_eligible
        )

        if should_reconnect:
            # Save the role at time of drop. TRANSITIONING collapses to CAPTURING
            # because the handoff did not complete.
            if prev_state == SwitchState.TRANSITIONING:
                self._reconnect_prev_role = SwitchState.CAPTURING
            else:
                self._reconnect_prev_role = prev_state

            # Read the last known peer address from QSettings.
            saved_addr: str = self._settings.value("last_peer_address", "", type=str)
            if saved_addr and ":" in saved_addr:
                parts = saved_addr.rsplit(":", 1)
                self._reconnect_host = parts[0].strip()
                try:
                    self._reconnect_port = int(parts[1].strip())
                except ValueError:
                    self._reconnect_port = config.DEFAULT_PORT
            elif saved_addr:
                self._reconnect_host = saved_addr
                self._reconnect_port = config.DEFAULT_PORT
            else:
                # No saved address -- cannot reconnect. Fall through to IDLE.
                should_reconnect = False

        if should_reconnect:
            # Initialize backoff state.
            self._reconnect_attempt = 0
            self._reconnect_delay_s = config.RECONNECT_INITIAL_DELAY_S

            # Use begin_reconnect to transition to RECONNECTING (not IDLE).
            self._state_ctrl.begin_reconnect(self._reconnect_prev_role)

            self._conn_panel.on_state_changed(SwitchState.RECONNECTING, peer_info=None)
            self._mirror_panel.on_state_changed(SwitchState.RECONNECTING)

            self._append_log(
                f"Connection lost ({reason}). "
                f"Attempting reconnect in {self._reconnect_delay_s:.1f}s..."
            )
            logger.info(
                "Connection lost (%s) -- scheduling reconnect in %.1fs",
                reason,
                self._reconnect_delay_s,
            )
            self._reconnect_timer.setInterval(int(self._reconnect_delay_s * 1000))
            self._reconnect_timer.start()
        else:
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
        elif msg_type == "handoff_request":
            self._handle_handoff_request(message)
        elif msg_type == "handoff_ack":
            self._handle_handoff_ack()
        elif msg_type == "click":
            self._handle_click_message(message)
        elif msg_type == "scroll":
            self._handle_scroll_message(message)
        elif msg_type == "key":
            self._handle_key_message(message)
        elif msg_type == "force_release":
            # Peer force-released. If we are CAPTURING, return to CONNECTED cleanly.
            logger.info("Peer sent force_release -- returning to CONNECTED")
            self._handle_peer_force_release()
        elif msg_type == "user_disconnect":
            # Peer issued a clean user-initiated disconnect. Set the flag so the
            # imminent transport-closed callback goes to IDLE instead of reconnecting.
            # Do NOT tear down here -- the peer will close the socket and
            # _on_transport_disconnected will fire with the flag already set.
            logger.info("Peer issued user disconnect -- closing cleanly without reconnect")
            self._user_initiated_disconnect = True
        elif msg_type == "idle_timeout_release":
            # Informational from peer. Log only; no state change required on this side.
            logger.info("Peer sent idle_timeout_release (informational)")
        else:
            logger.debug("Unknown message type: %r", msg_type)

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    def _handle_hello(self, payload: dict) -> None:
        """
        Process the peer's hello payload. Transition to CONNECTED and display
        peer info in the connection panel and log.

        Step 8: if _reconnect_restoring_role is set, this is a successful reconnect.
        Restore the prior role after transitioning to CONNECTED.
        """
        self._peer_info = payload
        name = payload.get("machine_name", "unknown")
        plat = payload.get("platform", "?")
        lw, lh = payload.get("screen_logical", [0, 0])
        pw, ph = payload.get("screen_physical", [0, 0])
        dpi = payload.get("dpi_scale", 1.0)

        restoring = self._reconnect_restoring_role
        self._reconnect_restoring_role = False

        if restoring:
            # Reconnect succeeded: RECONNECTING -> CONNECTED via state controller.
            self._state_ctrl.reconnect_succeeded()
        else:
            self._state_ctrl.handshake_complete()

        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=payload)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log(
            f"Received hello from peer: {name} ({plat})"
        )
        # Step 7: log the peer's full display configuration.
        self._append_log(
            f"Peer screen: logical {lw}x{lh}, physical {pw}x{ph}, dpi={dpi}"
        )

        if restoring:
            self._append_log(
                f"Reconnect succeeded. Restoring role: {self._reconnect_prev_role.value.upper()}"
            )
            logger.info(
                "Reconnect succeeded -- restoring role %s",
                self._reconnect_prev_role.value.upper(),
            )
            # Wire the dead-man timer back (it was started by _on_transport_connected
            # via connection_established -> HANDSHAKING -> dead_man_timer.start()).
            # Restore role.
            if self._reconnect_prev_role == SwitchState.CAPTURING:
                # Re-send mirror_start to peer so it enters RECEIVING, then go CAPTURING.
                self._transport.send({"type": "mirror_start"})
                edge = self._mirror_panel.selected_edge()
                self._capture.set_peer_edge(edge)
                self._state_ctrl.start_mirroring()
                self._capture.start(self._transport.enqueue_delta)
                self._event_capture.set_active(True)
                self._conn_panel.on_state_changed(SwitchState.CAPTURING, peer_info=self._peer_info)
                self._mirror_panel.on_state_changed(SwitchState.CAPTURING)
                self._append_log("Role restored: this machine is now the Sender again")
            else:
                # Was RECEIVING: peer will re-send mirror_start when it restores its
                # CAPTURING role. No action needed here; we wait for mirror_start.
                self._append_log(
                    "Role was RECEIVING -- waiting for peer to re-send mirror_start"
                )
        else:
            self._append_log("Connection established")

    def _handle_mirror_start(self) -> None:
        """
        Peer is starting to send deltas. Enter RECEIVING state.
        Reset receiver delta counters and the canvas green square.
        Step 8: reset idle timer on RECEIVING entry.
        """
        self._state_ctrl.mirror_start_received()
        self._deltas_received = 0
        self._delta_timestamps.clear()
        self._last_sample_log_time = time.monotonic()
        self._last_delta_received_time = time.monotonic()
        self._canvas.reset_remote_position()
        self._conn_panel.on_state_changed(SwitchState.RECEIVING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.RECEIVING)
        self._idle_timer.start()
        self._append_log("Peer started mirroring -- this machine is now the Receiver")

    def _handle_mirror_stop(self) -> None:
        """Peer stopped sending deltas. Return to CONNECTED state."""
        self._idle_timer.stop()
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
        self._last_delta_received_time = time.monotonic()
        self._last_ndx = ndx
        self._last_ndy = ndy
        self._last_seq = seq

        # Apply to the canvas green square (always).
        self._canvas.apply_remote_delta(ndx, ndy)

        # OS cursor injection (only when RECEIVING and Inject is checked).
        if self._state_ctrl.state == SwitchState.RECEIVING and self._injection_enabled:
            self._injector.move_relative(
                ndx, ndy,
                self._screen_w, self._screen_h,
            )

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
    # Step 6: edge-based handoff handlers
    # ------------------------------------------------------------------

    def _schedule_handoff_fire(self, sender_edge: str, perp: float) -> None:
        """
        Called from the DeltaCapture background thread when edge dwell completes.

        Bridges the capture thread to the Qt main thread via pyqtSignal (queued connection).
        """
        logger.info(
            "Edge dwell callback received on capture thread: edge=%s perp=%.3f",
            sender_edge,
            perp,
        )
        self._main_signals.edge_dwell_fired.emit(sender_edge, perp)

    def _on_edge_dwell_fired(self, sender_edge: str, perp: float) -> None:
        """
        Main thread: edge dwell threshold met on this machine (the current sender).

        Guard: only fire if we are still in CAPTURING state. The dwell callback
        can arrive slightly after a state change (e.g., user clicked Stop Mirroring
        just before dwell completed). If state changed, discard silently.
        """
        if self._state_ctrl.state != SwitchState.CAPTURING:
            logger.debug(
                "Edge dwell callback arrived in state %s -- discarded",
                self._state_ctrl.state.value,
            )
            return

        logger.info("Handoff fire: edge=%s perp=%.3f", sender_edge, perp)
        self._append_log(f"Handoff triggered: cursor at {sender_edge} edge, perp={perp:.3f}")

        # Transition to TRANSITIONING and pause delta delivery.
        # Gate off click/scroll forwarding while awaiting handoff ack.
        self._event_capture.set_active(False)
        self._state_ctrl.begin_handoff()
        self._capture.set_paused(True)

        # Send handoff_request to peer.
        self._transport.send({
            "type": "handoff_request",
            "perp": perp,
            "sender_edge": sender_edge,
        })

        # Update panels.
        self._conn_panel.on_state_changed(SwitchState.TRANSITIONING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.TRANSITIONING)

        # Start the ack timeout timer. If no handoff_ack arrives within
        # HANDOFF_ACK_TIMEOUT_S, _on_handoff_ack_timeout rolls back to CAPTURING.
        self._handoff_ack_timer.start()

    def _on_handoff_ack_timeout(self) -> None:
        """
        Handoff ACK did not arrive within HANDOFF_ACK_TIMEOUT_S.

        Roll back TRANSITIONING -> CAPTURING. Log the event and resume delta delivery.
        """
        if self._state_ctrl.state != SwitchState.TRANSITIONING:
            return  # already resolved via ack or disconnect
        logger.warning(
            "Handoff ACK timeout (%.1fs) -- rolling back to CAPTURING",
            config.HANDOFF_ACK_TIMEOUT_S,
        )
        self._append_log(
            f"Handoff ACK timeout after {config.HANDOFF_ACK_TIMEOUT_S:.1f}s -- resuming capture"
        )
        self._state_ctrl.handoff_ack_timeout()
        self._capture.set_paused(False)
        self._event_capture.set_active(True)
        self._conn_panel.on_state_changed(SwitchState.CAPTURING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CAPTURING)

    def _handle_handoff_request(self, payload: dict) -> None:
        """
        Peer (the current sender) wants to hand off control to us.

        We are in RECEIVING state. Steps:
        1. Compute entry edge (opposite of sender_edge).
        2. Warp OS cursor (if injection enabled) or only move canvas.
        3. Send handoff_ack.
        4. Transition RECEIVING -> CAPTURING (we are now the sender).
        5. Set cooldown on DeltaCapture to suppress immediate re-trigger.
        6. Start capture loop.
        """
        if self._state_ctrl.state != SwitchState.RECEIVING:
            logger.debug(
                "handoff_request arrived in state %s -- ignored",
                self._state_ctrl.state.value,
            )
            return

        # Leaving RECEIVING -- stop the idle timer (Step 8).
        self._idle_timer.stop()

        perp: float = float(payload.get("perp", 0.5))
        sender_edge: str = payload.get("sender_edge", "right")

        # Compute entry edge: opposite of sender_edge.
        _opposite: dict[str, str] = {
            "right": "left",
            "left": "right",
            "top": "bottom",
            "bottom": "top",
        }
        entry_edge = _opposite.get(sender_edge, "left")

        # Compute absolute cursor position on the entry edge.
        # perp is the normalized coordinate perpendicular to the edge.
        # For left/right entry: x = 1px (left) or screen_w-1 (right), y = perp * screen_h.
        # For top/bottom entry: y = 1px (top) or screen_h-1 (bottom), x = perp * screen_w.
        if entry_edge == "left":
            nx = 1.0 / self._screen_w
            ny = perp
        elif entry_edge == "right":
            nx = (self._screen_w - 1) / self._screen_w
            ny = perp
        elif entry_edge == "top":
            nx = perp
            ny = 1.0 / self._screen_h
        else:  # bottom
            nx = perp
            ny = (self._screen_h - 1) / self._screen_h

        logger.info(
            "Handoff received from peer at edge=%s perp=%.3f -- entry edge=%s nx=%.3f ny=%.3f",
            sender_edge, perp, entry_edge, nx, ny,
        )
        self._append_log(
            f"Handoff received from peer at edge={sender_edge} perp={perp:.3f}"
        )

        # Warp cursor to entry position.
        if self._injection_enabled:
            self._injector.move_absolute(
                nx, ny,
                self._screen_w, self._screen_h,
            )

        # Always reset the canvas green square to the entry position so the
        # visual representation matches the handoff landing point.
        # reset_remote_position() sets center; we override with a forced delta from
        # center to the actual entry point.
        self._canvas.reset_remote_position()
        # apply_remote_delta accumulates from 0.5, so delta = target - 0.5.
        self._canvas.apply_remote_delta(nx - 0.5, ny - 0.5)

        # Send ack back to the original sender.
        self._transport.send({"type": "handoff_ack"})

        # Transition this machine to CAPTURING (new sender).
        self._state_ctrl.handoff_request_received()

        # Set cooldown on the capture module before starting the loop.
        self._capture.set_cooldown()
        edge = self._mirror_panel.selected_edge()
        self._capture.set_peer_edge(edge)
        self._capture.set_paused(False)
        self._capture.start(self._transport.enqueue_delta)
        self._event_capture.set_active(True)

        # Hide the remote green square (we are now the sender, not receiver).
        self._canvas.hide_remote()

        # Update panels.
        self._conn_panel.on_state_changed(SwitchState.CAPTURING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CAPTURING)
        self._append_log("Role switched: this machine is now the Sender")

    def _handle_handoff_ack(self) -> None:
        """
        Peer confirmed it received our handoff_request and is now the sender.

        This machine transitions TRANSITIONING -> RECEIVING. The capture loop
        was already paused; now stop it fully and enter receive-only mode.
        """
        if self._state_ctrl.state != SwitchState.TRANSITIONING:
            logger.debug(
                "handoff_ack arrived in state %s -- ignored",
                self._state_ctrl.state.value,
            )
            return

        # Cancel the ack timeout -- we got the ack in time.
        self._handoff_ack_timer.stop()

        # Stop the capture thread (was paused; now fully stop it).
        self._capture.set_paused(False)
        self._capture.stop()

        # Transition to RECEIVING.
        self._state_ctrl.handoff_ack_received()

        # Reset the remote canvas square so it starts from center for incoming deltas.
        self._deltas_received = 0
        self._delta_timestamps.clear()
        self._last_sample_log_time = time.monotonic()
        self._last_delta_received_time = time.monotonic()
        self._canvas.reset_remote_position()

        # Update panels.
        self._conn_panel.on_state_changed(SwitchState.RECEIVING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.RECEIVING)
        self._idle_timer.start()
        self._append_log("Handoff complete -- this machine is now the Receiver")
        logger.info("Handoff complete -- transitioned to RECEIVING")

    # ------------------------------------------------------------------
    # EventCapture pynput thread callbacks (cross-thread signal emitters)
    # ------------------------------------------------------------------

    def _on_event_capture_click(self, button_name: str, pressed: bool) -> None:
        """
        Called from the pynput listener thread when a button event fires.

        Emits click_fired signal which is queued to the Qt main thread.
        Must not touch Qt widgets directly.
        """
        self._main_signals.click_fired.emit(button_name, pressed)

    def _on_event_capture_scroll(self, dx: int, dy: int) -> None:
        """
        Called from the pynput listener thread when a scroll event fires.

        Emits scroll_fired signal which is queued to the Qt main thread.
        Must not touch Qt widgets directly.
        """
        self._main_signals.scroll_fired.emit(dx, dy)

    # ------------------------------------------------------------------
    # Click and scroll sender handlers (Qt main thread)
    # ------------------------------------------------------------------

    def _on_click_fired(self, button: str, pressed: bool) -> None:
        """
        Main thread: a mouse button event was captured on this machine (sender).

        Gate: only forward if still in CAPTURING state. The signal may arrive
        slightly after a state transition; discard silently if so.
        """
        if self._state_ctrl.state != SwitchState.CAPTURING:
            return
        logger.debug("Sending click: button=%s pressed=%s", button, pressed)
        self._transport.send({"type": "click", "button": button, "pressed": pressed})

    def _on_scroll_fired(self, dx: int, dy: int) -> None:
        """
        Main thread: a scroll wheel event was captured on this machine (sender).

        Gate: only forward if still in CAPTURING state.
        """
        if self._state_ctrl.state != SwitchState.CAPTURING:
            return
        logger.debug("Sending scroll: dx=%d dy=%d", dx, dy)
        self._transport.send({"type": "scroll", "dx": dx, "dy": dy})

    # ------------------------------------------------------------------
    # Click and scroll receiver handlers (Qt main thread)
    # ------------------------------------------------------------------

    def _handle_click_message(self, message: dict) -> None:
        """
        Inject a click event received from the sender.

        Only acts if RECEIVING and injection is enabled -- same gate as delta injection.
        Step 8: reset the idle timeout on every received click.
        """
        if self._state_ctrl.state != SwitchState.RECEIVING or not self._injection_enabled:
            return
        self._last_delta_received_time = time.monotonic()
        button: str = message.get("button", "left")
        pressed: bool = bool(message.get("pressed", True))
        logger.debug("Injecting click: button=%s pressed=%s", button, pressed)
        self._injector.click(button, pressed)

    def _handle_scroll_message(self, message: dict) -> None:
        """
        Inject a scroll event received from the sender.

        Only acts if RECEIVING and injection is enabled.
        Step 8: reset the idle timeout on every received scroll.
        """
        if self._state_ctrl.state != SwitchState.RECEIVING or not self._injection_enabled:
            return
        self._last_delta_received_time = time.monotonic()
        dx: int = int(message.get("dx", 0))
        dy: int = int(message.get("dy", 0))
        logger.debug("Injecting scroll: dx=%d dy=%d", dx, dy)
        self._injector.scroll(dx, dy)

    def _handle_key_message(self, message: dict) -> None:
        """
        Inject a keyboard event received from the sender.

        Only acts if RECEIVING and injection is enabled -- same gate as
        click/scroll injection. Step 9.
        """
        if self._state_ctrl.state != SwitchState.RECEIVING or not self._injection_enabled:
            return
        key_name: str = message.get("key", "")
        pressed: bool = bool(message.get("pressed", True))
        if not key_name:
            logger.debug("Received key message with empty key -- skipped")
            return
        logger.debug("Injecting key: key=%r pressed=%s", key_name, pressed)
        self._injector.key(key_name, pressed)

    # ------------------------------------------------------------------
    # Step 9: keyboard forwarding toggle and key event handler
    # ------------------------------------------------------------------

    def _on_kbd_fwd_toggled(self, state: int) -> None:
        """
        Called when the Forward Keyboard checkbox changes state.

        Only meaningful while CAPTURING. If toggled in any other state (which
        should not happen because the checkbox is disabled), it is ignored.
        Persists the preference to QSettings so it survives restarts.
        """
        checked: bool = state != 0
        # Persist the preference regardless of current state.
        self._settings.setValue("keyboard_forwarding_enabled", checked)

        if self._state_ctrl.state != SwitchState.CAPTURING:
            # Checkbox is disabled outside CAPTURING; this path is a safety net.
            return

        self._keyboard_forwarding_active = checked
        if checked:
            self._append_log(
                "Keyboard forwarding ON. Every keystroke fires on both this machine "
                "and the peer. Press Ctrl+Alt+Shift+Esc to force-release."
            )
            logger.info("Keyboard forwarding enabled")
        else:
            self._append_log("Keyboard forwarding OFF.")
            logger.info("Keyboard forwarding disabled")

    def _translate_pynput_key(
        self,
        key: _pynput_keyboard.Key | _pynput_keyboard.KeyCode,
    ) -> str | None:
        """
        Translate a pynput key object to a pyautogui key name string.

        Returns the string on success, or None if the key cannot be translated
        (caller should log at DEBUG and skip forwarding rather than raise).
        """
        if isinstance(key, _pynput_keyboard.Key):
            name = self._PYNPUT_KEY_MAP.get(key)
            if name is None:
                logger.debug("KB forward: no mapping for pynput Key %r -- skipped", key)
            return name
        # KeyCode: use .char if available (ordinary printable characters).
        if isinstance(key, _pynput_keyboard.KeyCode) and key.char is not None:
            return key.char
        logger.debug("KB forward: cannot translate KeyCode %r -- skipped", key)
        return None

    def _on_key_event_fired(self, key_name: str, pressed: bool) -> None:
        """
        Qt main thread: a keyboard event was captured and forwarding is active.

        Gate: only forward if still in CAPTURING state. The signal may arrive
        slightly after a state transition; discard silently if so.
        """
        if self._state_ctrl.state != SwitchState.CAPTURING:
            return
        logger.debug("Sending key: key=%r pressed=%s", key_name, pressed)
        self._transport.send({"type": "key", "key": key_name, "pressed": pressed})

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
            "Canvas mirroring continues. "
            f"For a full disconnect use the system-wide hotkey ({config.HANDOFF_HOTKEY_RELEASE})."
        )
        logger.info("Injection force-released via Esc")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """
        Stop pynput listener threads cleanly before the window closes.

        pynput's Listener.stop() + join() must be called explicitly; the daemon
        thread alone is not a safe cleanup path on all platforms.
        Step 8: also stop the keyboard listener and cancel any in-flight reconnect.
        """
        self._reconnect_timer.stop()
        self._idle_timer.stop()
        self._stop_kb_listener()
        self._event_capture.stop()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        """
        Intercept hotkeys while the window has focus.

        Esc: force-release OS injection (RECEIVING + injection enabled only).
        All other keys pass through to the default handler.
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
        """
        Fired by StateController on every transition. Updates status bar and canvas dim.

        Step 6: dim the canvas red square in TRANSITIONING; restore in all other states.
        Step 8: pass reconnecting flag to status bar for amber color.
        Step 9: pass kbd_active flag; also clear keyboard forwarding when leaving CAPTURING.
        """
        mode = state.value.upper()
        x, y = pyautogui.position()
        role = self._role_label_for_state(state)
        transitioning = (state == SwitchState.TRANSITIONING)
        reconnecting = (state == SwitchState.RECONNECTING)

        # Clear keyboard forwarding when leaving CAPTURING.
        if state != SwitchState.CAPTURING and self._keyboard_forwarding_active:
            self._keyboard_forwarding_active = False
            logger.info("Keyboard forwarding deactivated (state left CAPTURING)")

        self._status_bar.update_status(
            mode, x, y, role=role,
            transitioning=transitioning, reconnecting=reconnecting,
            kbd_active=self._keyboard_forwarding_active,
        )
        self._canvas.set_local_dimmed(transitioning)

    # ------------------------------------------------------------------
    # Dead-man monitor (Qt main thread, 500 ms tick)
    # ------------------------------------------------------------------

    # States where the heartbeat is active and the monitor should run.
    _DEAD_MAN_ACTIVE_STATES: frozenset[SwitchState] = frozenset({
        SwitchState.CONNECTED,
        SwitchState.CAPTURING,
        SwitchState.RECEIVING,
        SwitchState.HANDSHAKING,
    })

    def _check_dead_man(self) -> None:
        """
        Called every 500 ms by _dead_man_timer on the Qt main thread.

        Reads seconds_since_last_pong() from the transport -- a single float
        read that is safe under the GIL without a lock. If the elapsed time
        exceeds HEARTBEAT_TIMEOUT_S, fire the disconnect path immediately.
        This fires even when the heartbeat sender thread is blocked on send().
        """
        elapsed: float = self._transport.seconds_since_last_pong()
        if elapsed > config.HEARTBEAT_TIMEOUT_S:
            logger.error(
                "Connection lost: heartbeat timeout (monitor, no pong for %.1fs)",
                elapsed,
            )
            self._transport.handle_disconnect(
                f"heartbeat timeout (monitor, no pong for {elapsed:.1f}s)"
            )

    # ------------------------------------------------------------------
    # Step 8: system-wide force-release hotkey (pynput keyboard listener)
    # ------------------------------------------------------------------

    def _start_kb_listener(self) -> None:
        """
        Start the pynput keyboard listener for the system-wide force-release hotkey.

        Monitors Ctrl+Alt+Shift+Esc globally. When detected, emits
        force_release_pressed signal which is delivered to _on_system_force_release
        on the Qt main thread.

        On macOS this requires Accessibility permission (same grant as the mouse
        listener). Without it the listener starts but silently receives no events.
        The in-window Esc handler is the fallback in that case.
        """
        if self._kb_listener is not None:
            return
        self._kb_listener = _pynput_keyboard.Listener(
            on_press=self._kb_on_press,
            on_release=self._kb_on_release,
        )
        self._kb_listener.start()
        logger.info(
            "System-wide keyboard listener started (%s to force-release)",
            config.HANDOFF_HOTKEY_RELEASE,
        )

    def _stop_kb_listener(self) -> None:
        """Stop the keyboard listener and join its thread. Idempotent."""
        if self._kb_listener is None:
            return
        self._kb_listener.stop()
        self._kb_listener.join()
        self._kb_listener = None
        logger.info("System-wide keyboard listener stopped")

    # The four keys that form the force-release combo.
    _HOTKEY_MODIFIERS: frozenset[_pynput_keyboard.Key] = frozenset({
        _pynput_keyboard.Key.ctrl,
        _pynput_keyboard.Key.ctrl_l,
        _pynput_keyboard.Key.ctrl_r,
        _pynput_keyboard.Key.alt,
        _pynput_keyboard.Key.alt_l,
        _pynput_keyboard.Key.alt_r,
        _pynput_keyboard.Key.shift,
        _pynput_keyboard.Key.shift_l,
        _pynput_keyboard.Key.shift_r,
    })

    def _kb_combo_active(self) -> bool:
        """
        Return True when Ctrl, Alt, Shift, and Esc are all currently pressed.

        Checks against the set of pressed keys maintained by _kb_on_press and
        _kb_on_release. Called on the pynput listener thread.
        """
        # Check for at least one variant of each required modifier.
        ctrl_variants = {
            _pynput_keyboard.Key.ctrl,
            _pynput_keyboard.Key.ctrl_l,
            _pynput_keyboard.Key.ctrl_r,
        }
        alt_variants = {
            _pynput_keyboard.Key.alt,
            _pynput_keyboard.Key.alt_l,
            _pynput_keyboard.Key.alt_r,
        }
        shift_variants = {
            _pynput_keyboard.Key.shift,
            _pynput_keyboard.Key.shift_l,
            _pynput_keyboard.Key.shift_r,
        }
        with self._kb_pressed_keys_lock:
            pressed = self._kb_pressed_keys
            has_ctrl = bool(pressed & ctrl_variants)
            has_alt = bool(pressed & alt_variants)
            has_shift = bool(pressed & shift_variants)
            has_esc = _pynput_keyboard.Key.esc in pressed
        return has_ctrl and has_alt and has_shift and has_esc

    def _kb_on_press(
        self,
        key: _pynput_keyboard.Key | _pynput_keyboard.KeyCode | None,
    ) -> None:
        """
        Called on the pynput listener thread for every key press.

        Force-release detection is unconditional (Step 8). Keyboard forwarding
        (Step 9) is layered on top: if _keyboard_forwarding_active is True,
        translate and emit the key -- EXCEPT when the full force-release combo
        is active (Ctrl+Alt+Shift+Esc all pressed), to avoid leaking that combo
        to the peer.
        """
        if key is None:
            return
        with self._kb_pressed_keys_lock:
            self._kb_pressed_keys.add(key)
        logger.debug("KB listener: press %r", key)
        if self._kb_combo_active():
            logger.info("Force-release combo detected on keyboard listener thread")
            self._main_signals.force_release_pressed.emit()
            # Do not forward keys while the force-release combo is fully active.
            return
        # Step 9: forward keystroke if keyboard forwarding is active.
        if self._keyboard_forwarding_active:
            key_name = self._translate_pynput_key(key)
            if key_name is not None:
                self._main_signals.key_event_fired.emit(key_name, True)

    def _kb_on_release(
        self,
        key: _pynput_keyboard.Key | _pynput_keyboard.KeyCode | None,
    ) -> None:
        """
        Called on the pynput listener thread for every key release.

        Step 9: forward key-release events when keyboard forwarding is active.
        The force-release combo suppression on press means those modifier
        release events can be forwarded safely (the combo is already broken by
        the time any of the four keys release).
        """
        if key is None:
            return
        with self._kb_pressed_keys_lock:
            self._kb_pressed_keys.discard(key)
        # Step 9: forward key-release if keyboard forwarding is active.
        if self._keyboard_forwarding_active:
            key_name = self._translate_pynput_key(key)
            if key_name is not None:
                self._main_signals.key_event_fired.emit(key_name, False)

    def _on_system_force_release(self) -> None:
        """
        Qt main thread: system-wide Ctrl+Alt+Shift+Esc was pressed.

        Regardless of current state, transition to IDLE and tear down the
        connection. Sends a force_release notification to the peer before closing
        the transport so the peer can also return cleanly.

        Defensively checks isVisible() in case the signal fires during startup
        before the window is shown.
        """
        if not self.isVisible():
            return

        logger.info("Force-released via system-wide hotkey (Ctrl+Alt+Shift+Esc)")
        self._append_log(
            "Force-released via system-wide hotkey (Ctrl+Alt+Shift+Esc)"
        )

        # Mark as user-initiated so the disconnected signal does not trigger reconnect.
        self._user_initiated_disconnect = True

        # Cancel any in-flight reconnect or idle timer.
        self._reconnect_timer.stop()
        self._idle_timer.stop()

        # Notify peer before closing (best effort -- fire and forget).
        if self._state_ctrl.state not in (SwitchState.IDLE, SwitchState.RECONNECTING):
            try:
                self._transport.send({"type": "force_release"})
            except Exception:
                pass  # connection may already be dead; notification is best-effort

        # Tear down.
        self._dead_man_timer.stop()
        self._handoff_ack_timer.stop()
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._canvas.set_local_dimmed(False)
        self._canvas.hide_remote()
        self._transport.close()

        if self._state_ctrl.state == SwitchState.RECONNECTING:
            self._state_ctrl.reconnect_canceled()
        else:
            self._state_ctrl.force_release()

        self._peer_info = None
        self._conn_panel.on_state_changed(SwitchState.IDLE, peer_info=None)
        self._mirror_panel.on_state_changed(SwitchState.IDLE)

    def _handle_peer_force_release(self) -> None:
        """
        Peer sent a force_release notification. If this machine is CAPTURING,
        return to CONNECTED cleanly (peer is no longer receiving).

        Set _user_initiated_disconnect so that if the peer also closes the
        socket immediately after force_release, the transport-closed callback
        on this side treats it as user-initiated and skips the reconnect path.
        Force-release is user-initiated from the perspective of the network --
        neither side should try to reconnect after a force-release.
        """
        self._user_initiated_disconnect = True
        state = self._state_ctrl.state
        if state == SwitchState.CAPTURING:
            self._event_capture.set_active(False)
            self._stop_capture_if_running()
            self._state_ctrl.stop_mirroring()
            self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
            self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
            self._append_log("Peer force-released -- returned to CONNECTED")
        elif state == SwitchState.RECEIVING:
            self._idle_timer.stop()
            self._canvas.hide_remote()
            self._state_ctrl.mirror_stop_received()
            self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
            self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
            self._append_log("Peer force-released -- returned to CONNECTED")

    # ------------------------------------------------------------------
    # Step 8: auto-reconnect timer callback
    # ------------------------------------------------------------------

    def _on_reconnect_attempt(self) -> None:
        """
        Qt main thread: reconnect timer fired. Attempt to reconnect to the peer.

        On success the transport will emit connected -> handshake -> CONNECTED,
        at which point _on_reconnect_connected restores the prior role.
        On failure the disconnected signal fires again and we schedule the next attempt.
        """
        if self._state_ctrl.state != SwitchState.RECONNECTING:
            return

        self._reconnect_attempt += 1
        self._append_log(
            f"Reconnect attempt {self._reconnect_attempt}/{config.RECONNECT_MAX_ATTEMPTS}..."
        )
        logger.info(
            "Reconnect attempt %d/%d to %s:%d",
            self._reconnect_attempt,
            config.RECONNECT_MAX_ATTEMPTS,
            self._reconnect_host,
            self._reconnect_port,
        )

        # The transport must be freshly constructed for each attempt because
        # the old socket objects are in a closed state after _handle_disconnect.
        self._transport = TcpTransport()
        self._transport.register_connected_callback(self._on_transport_connected)
        self._transport.register_disconnected_callback(self._on_reconnect_disconnected)
        self._transport.register_message_callback(self._on_message_received)

        # Signal _handle_hello to restore the prior role after handshake completes.
        self._reconnect_restoring_role = True

        self._transport.connect_to_peer(self._reconnect_host, self._reconnect_port)

    def _on_reconnect_disconnected(self, reason: str) -> None:
        """
        Qt main thread: a reconnect attempt failed (connect or handshake error).

        Schedule the next attempt with backoff, or give up after max attempts.
        """
        if self._state_ctrl.state != SwitchState.RECONNECTING:
            return
        self._reconnect_restoring_role = False

        logger.info(
            "Reconnect attempt %d/%d failed: %s",
            self._reconnect_attempt,
            config.RECONNECT_MAX_ATTEMPTS,
            reason,
        )
        self._append_log(
            f"Reconnect attempt {self._reconnect_attempt}/{config.RECONNECT_MAX_ATTEMPTS} "
            f"failed: {reason}"
        )

        if self._reconnect_attempt >= config.RECONNECT_MAX_ATTEMPTS:
            logger.info(
                "Reconnect exhausted after %d attempts -- returning to IDLE",
                config.RECONNECT_MAX_ATTEMPTS,
            )
            self._append_log(
                f"Reconnect exhausted after {config.RECONNECT_MAX_ATTEMPTS} attempts. "
                "Returning to IDLE."
            )
            self._state_ctrl.reconnect_failed_finally()
            self._peer_info = None
            self._conn_panel.on_state_changed(SwitchState.IDLE, peer_info=None)
            self._mirror_panel.on_state_changed(SwitchState.IDLE)
            return

        # Compute next delay with exponential backoff, capped at max.
        self._reconnect_delay_s = min(
            self._reconnect_delay_s * config.RECONNECT_BACKOFF_FACTOR,
            config.RECONNECT_MAX_DELAY_S,
        )
        self._append_log(
            f"Reconnecting (attempt {self._reconnect_attempt + 1}/"
            f"{config.RECONNECT_MAX_ATTEMPTS}) in {self._reconnect_delay_s:.1f}s..."
        )
        logger.info("Next reconnect in %.1fs", self._reconnect_delay_s)
        self._reconnect_timer.setInterval(int(self._reconnect_delay_s * 1000))
        self._reconnect_timer.start()

    # ------------------------------------------------------------------
    # Step 8: idle timeout in RECEIVING state
    # ------------------------------------------------------------------

    def _check_idle_timeout(self) -> None:
        """
        Called every 1 s by _idle_timer when in RECEIVING state.

        If no delta, click, or scroll has arrived in IDLE_TIMEOUT_S seconds,
        transition RECEIVING -> CONNECTED and inform the peer.
        """
        if self._state_ctrl.state != SwitchState.RECEIVING:
            self._idle_timer.stop()
            return

        elapsed = time.monotonic() - self._last_delta_received_time
        if elapsed >= config.IDLE_TIMEOUT_S:
            logger.info(
                "Idle timeout (%.1fs without input). Returning control to local machine.",
                config.IDLE_TIMEOUT_S,
            )
            self._append_log(
                f"Idle timeout ({config.IDLE_TIMEOUT_S:.1f}s without input). "
                "Returning control to local machine."
            )
            self._idle_timer.stop()

            # Inform peer (best effort -- peer may or may not act on this).
            try:
                self._transport.send({"type": "idle_timeout_release"})
            except Exception:
                pass

            # Transition RECEIVING -> CONNECTED.
            self._canvas.hide_remote()
            self._state_ctrl.mirror_stop_received()
            self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
            self._mirror_panel.on_state_changed(SwitchState.CONNECTED)

    # ------------------------------------------------------------------
    # Log panel
    # ------------------------------------------------------------------

    def _append_log(self, text: str) -> None:
        """Write a timestamped line to the log panel."""
        self._log_panel.append_line(f"[{_ts()}] {text}")

    def append_log(self, text: str) -> None:
        """Public interface for other components to write to the log panel."""
        self._append_log(text)
