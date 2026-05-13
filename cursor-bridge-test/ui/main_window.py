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
import time
import pyautogui

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
    """

    _COLOR_DEFAULT = "#e0e0e0"
    _COLOR_TRANSITIONING = "#f08040"

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
    ) -> None:
        parts = [f"Mode: {mode}", f"Cursor: ({x}, {y})", f"Role: {role}"]
        if extra:
            parts.append(extra)
        self._label.setText("  |  ".join(parts))
        color = self._COLOR_TRANSITIONING if transitioning else self._COLOR_DEFAULT
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
        elif state == SwitchState.TRANSITIONING:
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(False)
            self._inject_chk.setEnabled(False)
            self._set_role("Handing off...", self._COLOR_TRANSITIONING)
        else:
            # IDLE, LISTENING, CONNECTING, HANDSHAKING -- all active buttons off.
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

        # Start the pynput listener. It runs for the full window lifetime and is
        # gated off until set_active(True) is called on CAPTURING entry.
        self._event_capture.start()

        logger.info(
            "MainWindow initialized. Screen (pyautogui): %dx%d  dpi_scale=%.2f. Polling at %dms.",
            self._screen_w, self._screen_h, self._dpi_scale, config.CANVAS_REFRESH_MS,
        )

        # --- QSettings: restore last peer address on startup (Ask 3) ---
        _last_addr: str = self._settings.value("last_peer_address", "", type=str)
        if _last_addr:
            self._conn_panel._addr_field.setText(_last_addr)
            logger.info("Restored last peer address from settings: %s", _last_addr)

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
        self._status_bar.update_status(mode, x, y, role=role, extra=extra, transitioning=transitioning)

    def _role_label_for_state(self, state: SwitchState) -> str:
        dpi_str = f"{self._dpi_scale:.2f}"
        if state == SwitchState.CAPTURING:
            return f"Sender | DPI: {dpi_str}"
        if state == SwitchState.TRANSITIONING:
            return "Handing off..."
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
        self._peer_info = None
        self._state_ctrl.begin_connecting()
        self._conn_panel.on_state_changed(SwitchState.CONNECTING)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTING)
        self._append_log(f"Connecting to {host}:{port}")
        self._transport.connect_to_peer(host, port)

    def _on_disconnect_clicked(self) -> None:
        self._dead_man_timer.stop()
        self._handoff_ack_timer.stop()  # Step 6: cancel any in-flight handoff
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._canvas.set_local_dimmed(False)
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
        """
        self._dead_man_timer.stop()
        # Cancel any in-flight handoff ack timer -- the peer is gone.
        self._handoff_ack_timer.stop()
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._canvas.set_local_dimmed(False)
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
        elif msg_type == "handoff_request":
            self._handle_handoff_request(message)
        elif msg_type == "handoff_ack":
            self._handle_handoff_ack()
        elif msg_type == "click":
            self._handle_click_message(message)
        elif msg_type == "scroll":
            self._handle_scroll_message(message)
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
        pw, ph = payload.get("screen_physical", [0, 0])
        dpi = payload.get("dpi_scale", 1.0)

        self._state_ctrl.handshake_complete()
        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=payload)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log(
            f"Received hello from peer: {name} ({plat})"
        )
        # Step 7: log the peer's full display configuration so Master can confirm
        # what pyautogui is reporting on each machine side by side.
        self._append_log(
            f"Peer screen: logical {lw}x{lh}, physical {pw}x{ph}, dpi={dpi}"
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
        self._canvas.reset_remote_position()

        # Update panels.
        self._conn_panel.on_state_changed(SwitchState.RECEIVING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.RECEIVING)
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
        """
        if self._state_ctrl.state != SwitchState.RECEIVING or not self._injection_enabled:
            return
        button: str = message.get("button", "left")
        pressed: bool = bool(message.get("pressed", True))
        logger.debug("Injecting click: button=%s pressed=%s", button, pressed)
        self._injector.click(button, pressed)

    def _handle_scroll_message(self, message: dict) -> None:
        """
        Inject a scroll event received from the sender.

        Only acts if RECEIVING and injection is enabled.
        """
        if self._state_ctrl.state != SwitchState.RECEIVING or not self._injection_enabled:
            return
        dx: int = int(message.get("dx", 0))
        dy: int = int(message.get("dy", 0))
        logger.debug("Injecting scroll: dx=%d dy=%d", dx, dy)
        self._injector.scroll(dx, dy)

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

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """
        Stop the pynput listener thread cleanly before the window closes.

        pynput's Listener.stop() + join() must be called explicitly; the daemon
        thread alone is not a safe cleanup path on all platforms.
        """
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
        """
        mode = state.value.upper()
        x, y = pyautogui.position()
        role = self._role_label_for_state(state)
        transitioning = (state == SwitchState.TRANSITIONING)
        self._status_bar.update_status(mode, x, y, role=role, transitioning=transitioning)
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
    # Log panel
    # ------------------------------------------------------------------

    def _append_log(self, text: str) -> None:
        """Write a timestamped line to the log panel."""
        self._log_panel.append_line(f"[{_ts()}] {text}")

    def append_log(self, text: str) -> None:
        """Public interface for other components to write to the log panel."""
        self._append_log(text)
