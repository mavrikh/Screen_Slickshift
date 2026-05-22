"""
Slickshift -- PyQt6 main window.

Five regions:
  1. Connection panel (top): peer address field, Listen/Connect/Disconnect buttons,
     connection state indicator with peer info.
  2. Mirroring panel (below connection): Start/Stop Mirroring toggle, role display,
     Inject checkbox (receiver only), and Peer Layout dropdown.
  3. Status bar: current switch-state, cursor coordinates, FPS, role, delta stats.
  4. Canvas (center): red square represents the local cursor position (always).
     In RECEIVING state a second green square mirrors the incoming sender deltas.
     In TRANSITIONING state the red square dims to grey to signal "leaving".
  5. Log panel (bottom, collapsible): last N lines from the event log.

EdgeDetector wiring:
  MouseCapture produces only delta callbacks. EdgeDetector (in
  slickshift.edge_detection.edge_detector) owns edge-band detection and the dwell
  timer. The UI is the integration point:
    - _edge_detector is instantiated in __init__ alongside _capture.
    - The edge-dwell callback is registered on _edge_detector (not on _capture).
    - _poll_cursor feeds cur_x, cur_y to _edge_detector.tick() on every tick.
    - _handle_handoff_request calls _edge_detector.set_cooldown() after every
      handoff landing.
    - _on_peer_edge_changed calls _edge_detector.set_peer_edge() when the user
      changes the peer-edge dropdown.
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

from PyQt6.QtCore import (
    QObject,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    QSize,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from slickshift import config
from slickshift.capture.event_capture import EventCapture
from slickshift.capture.mouse_capture import MouseCapture, make_mouse_capture
from slickshift.edge_detection.arrangement import (
    arrangement_to_wire,
    invert_arrangement,
    wire_to_arrangement,
)
from slickshift.edge_detection.edge_detector import EdgeDetector
from slickshift.injection.mouse_inject import make_mouse_injector
from slickshift.state_machine.controller import StateController, SwitchState
from slickshift.transport.socket_io import TcpTransport
from slickshift.capture.mouse_capture import compute_virtual_desktop_bbox
from slickshift.transport.topology import MonitorInfo, enumerate_local_monitors

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

    # Emitted when an arrangement_update message arrives from the peer.
    # Args: wire_arrangement (dict -- JSON-safe wire format).
    arrangement_update_received = pyqtSignal(object)

    # Emitted by the pynput keyboard listener thread when the system-wide
    # Ctrl+Alt+Shift+Esc combo is detected.
    force_release_pressed = pyqtSignal()

    # Emitted by the pynput keyboard listener thread when a key event fires
    # and keyboard forwarding is active.
    # Args: key_name (str -- pyautogui name), pressed (bool).
    key_event_fired = pyqtSignal(str, bool)

    # Emitted by the pynput keyboard listener thread when a browser hotkey is
    # detected (B1 feature). The action string is one of:
    #   "list_tabs"  -- Ctrl+Alt+Shift+L
    #   "tab_next"   -- Ctrl+Alt+Shift+]
    #   "tab_prev"   -- Ctrl+Alt+Shift+[
    # Args: action (str).
    browser_hotkey_pressed = pyqtSignal(str)

    # Emitted by the pynput keyboard listener thread when the B3 clipboard-push
    # hotkey (Ctrl+Alt+Shift+V) is detected.
    clipboard_push_pressed = pyqtSignal()


class _Canvas(QFrame):
    """
    Central canvas. Draws two squares:
    - Red square: local cursor position (always visible unless dimmed).
    - Green square: mirrors the sender's incoming deltas (RECEIVING state only).

    In RECEIVING state the green square starts at canvas center and accumulates
    normalized deltas from the peer. It does NOT represent any OS cursor position --
    it is a visual confirmation that delta math is working before injection is added.

    In TRANSITIONING state the local square dims to grey to signal that
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

        Clamps to [0.0, 1.0] so the square never leaves the canvas.
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

    Role text is coloured orange (#f08040) in TRANSITIONING state to give
    a clear visual cue that a handoff is in flight and awaiting ACK.
    Appends "| KBD" in red (#ff6060) when keyboard forwarding is live.
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
    Connection panel.

    Contains:
    - Peer address field: "<ip>:<port>" or just "<ip>" (port defaults to DEFAULT_PORT)
    - Listen button: start a TCP server and wait for inbound connection
    - Connect button: initiate outbound connection to the entered address
    - Disconnect button: close any active connection
    - Status indicator: current connection state plus peer info when connected
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
    Mirroring panel.

    Contains:
    - Start Mirroring button: only enabled in CONNECTED state.
    - Stop Mirroring button: only enabled in CAPTURING state.
    - Inject checkbox: receiver only, defaults unchecked.
    - Forward Keyboard checkbox: sender only, defaults unchecked.
    - Peer Layout dropdown: "Peer is to the: Right/Left/Top/Bottom".
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

    # Edge options in display order. The value is the config key passed to EdgeDetector.
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

        # DEBUG: cursor-freeze validation toggle.
        # Spins up a self-contained MacMouseCapture (independent of the
        # state machine, no peer required) and pauses it so the None-return
        # suppression + cursor hide can be observed on a single machine.
        # REMOVE this button (and its handler in MainWindow) once full
        # handoff validation is complete.
        self._debug_pause_btn = QPushButton("DEBUG: Freeze Cursor")
        self._debug_pause_btn.setStyleSheet(btn_style)
        self._debug_pause_btn.setCheckable(True)
        self._debug_pause_btn.setToolTip(
            "Self-contained Phase 2 freeze test. Toggle on to hide and freeze "
            "the cursor; toggle off to restore. No peer connection needed."
        )
        layout.addWidget(self._debug_pause_btn)

        # Inject checkbox. Defaults to checked so injection starts immediately
        # when the machine first enters RECEIVING (assuming the macOS Accessibility
        # probe passes). Disabled in all states except RECEIVING.
        # Also gated on arrangement_committed -- tooltip explains when blocked.
        self._inject_chk = QCheckBox("Inject")
        self._inject_chk.setChecked(True)
        self._inject_chk.setEnabled(False)
        self._inject_chk.setToolTip(
            "Configure and commit a screen arrangement before enabling injection."
        )
        self._inject_chk.setStyleSheet(
            "QCheckBox { color: #b0b0b0; font-family: monospace; font-size: 12px; }"
            "QCheckBox:disabled { color: #555555; }"
        )
        layout.addWidget(self._inject_chk)

        # Forward Keyboard checkbox. Defaults to unchecked. Only enabled in
        # CAPTURING state so the sender explicitly opts in.
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

        # Peer Layout dropdown.
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
        self._peer_edge_combo.setEnabled(True)
        layout.addWidget(self._peer_edge_combo)

        scroll_label = QLabel("Scroll:")
        scroll_label.setStyleSheet("color: #b0b0b0; font-family: monospace; font-size: 12px;")
        layout.addWidget(scroll_label)

        self._scroll_slider = QSlider(Qt.Orientation.Horizontal)
        self._scroll_slider.setMinimum(config.SCROLL_MULTIPLIER_MIN)
        self._scroll_slider.setMaximum(config.SCROLL_MULTIPLIER_MAX)
        self._scroll_slider.setValue(config.SCROLL_MULTIPLIER_DEFAULT)
        self._scroll_slider.setFixedWidth(90)
        self._scroll_slider.setToolTip(
            "Multiplier applied to outgoing scroll events. "
            "Increase if trackpad scrolling on this machine feels slow on the peer."
        )
        self._scroll_slider.setStyleSheet(
            "QSlider::groove:horizontal { background: #16213e; height: 4px; border: 1px solid #3a3a6e; } "
            "QSlider::handle:horizontal { background: #e0e0e0; width: 10px; margin: -4px 0; border-radius: 2px; } "
            "QSlider::sub-page:horizontal { background: #40a0ff; }"
        )
        layout.addWidget(self._scroll_slider)

        self._scroll_value_label = QLabel(f"{config.SCROLL_MULTIPLIER_DEFAULT}x")
        self._scroll_value_label.setStyleSheet(
            "color: #e0e0e0; font-family: monospace; font-size: 12px; min-width: 24px;"
        )
        layout.addWidget(self._scroll_value_label)

        self._scroll_slider.valueChanged.connect(
            lambda v: self._scroll_value_label.setText(f"{v}x")
        )

        layout.addStretch()

        self._role_label = QLabel("Role: Idle")
        self._role_label.setStyleSheet(
            f"color: {self._COLOR_IDLE}; font-family: monospace; font-size: 12px;"
        )
        layout.addWidget(self._role_label)

    def on_state_changed(
        self,
        state: SwitchState,
        arrangement_committed: bool = True,
    ) -> None:
        """
        Update button states, role label, and dropdown enable to match the new switch state.

        arrangement_committed: when False, the Inject checkbox is disabled even in
        RECEIVING state. Set False until the user has committed a screen arrangement.
        Defaults to True so all pre-Task-7 call sites continue to work unchanged.
        """
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
            # Gate Inject on arrangement_committed. The arrangement panel
            # auto-commits from the dropdown default, so for normal single-monitor
            # use this resolves to True before RECEIVING is reached.
            self._inject_chk.setEnabled(arrangement_committed)
            if arrangement_committed:
                self._inject_chk.setToolTip("")
            else:
                self._inject_chk.setToolTip(
                    "Configure and commit a screen arrangement before enabling injection."
                )
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

    def set_dropdown_edge(self, edge: str) -> None:
        """
        Programmatically set the peer-edge dropdown to match the given edge string.

        Blocks the dropdown's currentIndexChanged signal while updating so that
        setting the dropdown from code does not re-trigger the handler and create
        a feedback loop.

        edge must be one of "right", "left", "top", "bottom". If the edge does not
        match any option (e.g. a corner snap was committed), keeps the current value.
        """
        for idx, (_, key) in enumerate(self._EDGE_OPTIONS):
            if key == edge:
                self._peer_edge_combo.blockSignals(True)
                self._peer_edge_combo.setCurrentIndex(idx)
                self._peer_edge_combo.blockSignals(False)
                return

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
    def debug_pause_btn(self) -> QPushButton:
        """DEBUG: Phase 2 cursor-freeze validation toggle. Remove with the button."""
        return self._debug_pause_btn

    @property
    def inject_chk(self) -> QCheckBox:
        return self._inject_chk

    @property
    def kbd_fwd_chk(self) -> QCheckBox:
        return self._kbd_fwd_chk

    @property
    def peer_edge_combo(self) -> QComboBox:
        return self._peer_edge_combo

    @property
    def scroll_slider(self) -> QSlider:
        return self._scroll_slider


class _DraggablePeerItem(QGraphicsItem):
    """
    A single peer-monitor rectangle that can be dragged independently.

    Used when "Unlock Same PC Monitors" is enabled. Each peer monitor gets its
    own _DraggablePeerItem so the user can position them individually to form
    L-shaped or other non-rectangular layouts.

    Colors:
        Peer rectangle: amber #f0c040
        Snap-edge highlight: green #40ffc0
    """

    _PEER_COLOR = QColor("#f0c040")
    _PEER_BORDER = QColor("#c09020")
    _HIGHLIGHT_COLOR = QColor("#40ffc0")

    def __init__(
        self,
        rect: QRectF,
        monitor_index: int,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        self._rect: QRectF = rect
        self.monitor_index: int = monitor_index
        self._highlights: list[tuple[QRectF, str]] = []
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_highlights(self, highlights: list[tuple[QRectF, str]]) -> None:
        """Set edges to highlight. highlights is in item-local coordinates."""
        self._highlights = highlights
        self.update()

    def scene_rect(self) -> QRectF:
        """Return the item rectangle in scene coordinates."""
        p = self.pos()
        return QRectF(p.x() + self._rect.x(), p.y() + self._rect.y(),
                      self._rect.width(), self._rect.height())

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        pad = 6.0
        return self._rect.adjusted(-pad, -pad, pad, pad)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # type: ignore[override]
        painter.setBrush(self._PEER_COLOR)
        pen = QPen(self._PEER_BORDER)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawRect(self._rect)

        if self._highlights:
            hpen = QPen(self._HIGHLIGHT_COLOR)
            hpen.setWidth(3)
            painter.setPen(hpen)
            for local_rect, edge in self._highlights:
                if edge == "right":
                    x = local_rect.x() + local_rect.width()
                    painter.drawLine(
                        QPointF(x, local_rect.y()),
                        QPointF(x, local_rect.y() + local_rect.height()),
                    )
                elif edge == "left":
                    x = local_rect.x()
                    painter.drawLine(
                        QPointF(x, local_rect.y()),
                        QPointF(x, local_rect.y() + local_rect.height()),
                    )
                elif edge == "top":
                    y = local_rect.y()
                    painter.drawLine(
                        QPointF(local_rect.x(), y),
                        QPointF(local_rect.x() + local_rect.width(), y),
                    )
                elif edge == "bottom":
                    y = local_rect.y() + local_rect.height()
                    painter.drawLine(
                        QPointF(local_rect.x(), y),
                        QPointF(local_rect.x() + local_rect.width(), y),
                    )


class _DraggablePeerGroup(QGraphicsItem):
    """
    A group of peer-monitor rectangles that move together as a single unit.

    Renders all peer monitors scaled to the scene coordinate space. The user
    drags this item to position the peer machine relative to the local monitors.
    When released, the parent scene's snap logic fires.

    Colors:
        Peer rectangles: amber #f0c040
        Snap-edge highlight: green #40ffc0
    """

    _PEER_COLOR = QColor("#f0c040")
    _PEER_BORDER = QColor("#c09020")
    _HIGHLIGHT_COLOR = QColor("#40ffc0")

    def __init__(
        self,
        peer_rects_scene: list[QRectF],
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(parent)
        # peer_rects_scene: rectangles in scene coordinates (already scaled)
        self._rects: list[QRectF] = peer_rects_scene
        # Which edges of which local rectangles to highlight.
        # List of (local_rect: QRectF, edge: str) pairs.
        self._highlights: list[tuple[QRectF, str]] = []
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_highlights(self, highlights: list[tuple[QRectF, str]]) -> None:
        """
        Set which local monitor edges to draw a highlight on.

        highlights is a list of (local_rect_in_scene_coords, edge_str) pairs.
        The local_rect must be in SCENE coordinates (not accounting for this item's
        own position -- the caller converts before calling).
        """
        self._highlights = highlights
        self.update()

    def bounding_rect_of_rects(self) -> QRectF:
        """Return the union bounding box of all peer rectangles."""
        if not self._rects:
            return QRectF(0, 0, 1, 1)
        x0 = min(r.x() for r in self._rects)
        y0 = min(r.y() for r in self._rects)
        x1 = max(r.x() + r.width() for r in self._rects)
        y1 = max(r.y() + r.height() for r in self._rects)
        return QRectF(x0, y0, x1 - x0, y1 - y0)

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        # Expand by highlight line width so it is not clipped.
        br = self.bounding_rect_of_rects()
        pad = 6.0
        return br.adjusted(-pad, -pad, pad, pad)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # type: ignore[override]
        # Draw peer rectangles.
        for rect in self._rects:
            painter.setBrush(self._PEER_COLOR)
            pen = QPen(self._PEER_BORDER)
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawRect(rect)

        # Draw snap-edge highlights on local rects.
        # The highlight is drawn in the item's local coordinate system; the caller
        # passes local-rect positions that are already offset to this item's frame.
        if self._highlights:
            hpen = QPen(self._HIGHLIGHT_COLOR)
            hpen.setWidth(3)
            painter.setPen(hpen)
            for local_rect, edge in self._highlights:
                if edge == "right":
                    x = local_rect.x() + local_rect.width()
                    painter.drawLine(
                        QPointF(x, local_rect.y()),
                        QPointF(x, local_rect.y() + local_rect.height()),
                    )
                elif edge == "left":
                    x = local_rect.x()
                    painter.drawLine(
                        QPointF(x, local_rect.y()),
                        QPointF(x, local_rect.y() + local_rect.height()),
                    )
                elif edge == "top":
                    y = local_rect.y()
                    painter.drawLine(
                        QPointF(local_rect.x(), y),
                        QPointF(local_rect.x() + local_rect.width(), y),
                    )
                elif edge == "bottom":
                    y = local_rect.y() + local_rect.height()
                    painter.drawLine(
                        QPointF(local_rect.x(), y),
                        QPointF(local_rect.x() + local_rect.width(), y),
                    )


# Snap position constants. These describe where the peer group sits relative
# to the local monitor cluster.
_SNAP_RIGHT = "right"
_SNAP_LEFT = "left"
_SNAP_TOP = "top"
_SNAP_BOTTOM = "bottom"
_SNAP_TOP_RIGHT = "top_right"
_SNAP_TOP_LEFT = "top_left"
_SNAP_BOTTOM_RIGHT = "bottom_right"
_SNAP_BOTTOM_LEFT = "bottom_left"

# Map from snap position to the set of exit edges it implies on monitor 0.
# For multi-monitor local setups the panel derives per-monitor edges in
# _compute_arrangement(), but single-monitor uses this table directly.
_SNAP_TO_EDGES: dict[str, set[str]] = {
    _SNAP_RIGHT: {"right"},
    _SNAP_LEFT: {"left"},
    _SNAP_TOP: {"top"},
    _SNAP_BOTTOM: {"bottom"},
    _SNAP_TOP_RIGHT: {"right", "top"},
    _SNAP_TOP_LEFT: {"left", "top"},
    _SNAP_BOTTOM_RIGHT: {"right", "bottom"},
    _SNAP_BOTTOM_LEFT: {"left", "bottom"},
}

# Map from snap position to the dominant dropdown-compatible edge.
# Corner positions collapse to the horizontal axis (right/left takes priority).
_SNAP_TO_DROPDOWN_EDGE: dict[str, str] = {
    _SNAP_RIGHT: "right",
    _SNAP_LEFT: "left",
    _SNAP_TOP: "top",
    _SNAP_BOTTOM: "bottom",
    _SNAP_TOP_RIGHT: "right",
    _SNAP_TOP_LEFT: "left",
    _SNAP_BOTTOM_RIGHT: "right",
    _SNAP_BOTTOM_LEFT: "left",
}


class _ScreenArrangementPanel(QGroupBox):
    """
    Screen Arrangement panel.

    Visualizes both machines' monitors as scaled rectangles. Local monitors are
    shown in blue-gray and are not draggable. Peer monitors are amber and move as
    a unit when dragged. When the peer group is within SNAP_THRESHOLD scene pixels
    of any snap position, it snaps there automatically.

    On "Commit Arrangement", derives per-monitor exit edges and stores them. The
    caller uses arrangement_committed() to check whether a commit has happened
    and calls committed_arrangement() to retrieve the dict for EdgeDetector.

    Signals:
        arrangement_changed(dict): emitted when a new arrangement is committed.
            The dict is arrangement: dict[int, set[str]] suitable for
            EdgeDetector.set_exit_edges().
    """

    arrangement_changed = pyqtSignal(object)  # emits dict[int, set[str]]
    # Emitted when the "Unlock Same PC Monitors" toggle changes. Arg: new checked state.
    monitors_unlock_changed = pyqtSignal(bool)

    # Scene layout constants (in scene pixels).
    _LOCAL_COLOR = QColor("#40a0ff")
    _LOCAL_BORDER = QColor("#206080")
    _SCENE_MARGIN = 20.0
    _LOCAL_SCENE_W = 160.0  # scene width for the local group bounding box
    _LOCAL_SCENE_H = 120.0  # scene height for the local group bounding box
    _SNAP_THRESHOLD = 30.0  # scene pixels -- how close before snapping

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Screen Arrangement", parent)
        self.setStyleSheet(
            "QGroupBox { color: #e0e0e0; font-family: monospace; font-size: 12px; "
            "border: 1px solid #2a2a4e; margin-top: 6px; padding-top: 4px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 14, 8, 8)
        outer.setSpacing(6)

        # Hint label.
        self._hint_label = QLabel(
            "Drag the amber (peer) block to match the physical screen layout. "
            "Click Commit when done."
        )
        self._hint_label.setStyleSheet(
            "color: #909090; font-family: monospace; font-size: 11px; border: none;"
        )
        self._hint_label.setWordWrap(True)
        outer.addWidget(self._hint_label)

        # Graphics view.
        self._scene = QGraphicsScene()
        self._scene.setBackgroundBrush(QColor("#1a1a2e"))

        self._view = QGraphicsView(self._scene)
        self._view.setFixedHeight(220)
        self._view.setStyleSheet(
            "QGraphicsView { background-color: #1a1a2e; border: 1px solid #3a3a6e; }"
        )
        self._view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer.addWidget(self._view)

        # Toggle row: "Unlock Same PC Monitors" checkbox.
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)

        self._unlock_chk = QCheckBox("Unlock Same PC Monitors")
        self._unlock_chk.setChecked(False)
        # Feature temporarily disabled. Re-enable once the arrangement wire
        # format is extended to carry spatial pairing (which peer monitor is
        # adjacent to which local monitor). Until then, multi-monitor splits
        # cannot be inverted correctly on the receiver. Locked group-drag mode
        # works for "peer on one side" layouts even with multiple monitors.
        self._unlock_chk.setEnabled(False)
        self._unlock_chk.setToolTip(
            "Temporarily disabled. Re-enables when the cross-machine arrangement "
            "protocol is extended to carry per-monitor spatial pairing."
        )
        self._unlock_chk.setStyleSheet(
            "QCheckBox { color: #b0b0b0; font-family: monospace; font-size: 12px; border: none; }"
            "QCheckBox:disabled { color: #555555; }"
        )
        self._unlock_chk.stateChanged.connect(self._on_unlock_toggled)
        toggle_row.addWidget(self._unlock_chk)
        toggle_row.addStretch()
        outer.addLayout(toggle_row)

        # Bottom row: status label + commit button.
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        self._status_label = QLabel("Arrangement: not set")
        self._status_label.setStyleSheet(
            "color: #b0b0b0; font-family: monospace; font-size: 11px; border: none;"
        )
        bottom_row.addWidget(self._status_label)
        bottom_row.addStretch()

        btn_style = (
            "QPushButton { background-color: #16213e; color: #e0e0e0; "
            "font-family: monospace; font-size: 12px; border: 1px solid #3a3a6e; "
            "padding: 4px 12px; } "
            "QPushButton:hover { background-color: #1e2f5e; } "
            "QPushButton:disabled { color: #555555; border-color: #2a2a4e; }"
        )
        self._commit_btn = QPushButton("Commit Arrangement")
        self._commit_btn.setStyleSheet(btn_style)
        self._commit_btn.setEnabled(False)
        self._commit_btn.clicked.connect(self._on_commit_clicked)
        bottom_row.addWidget(self._commit_btn)

        outer.addLayout(bottom_row)

        # Peer-set banner: shown briefly after a remote arrangement_update is
        # applied.  Starts hidden; auto-dismissed after 3 s via QTimer.
        self._peer_banner = QLabel("Arrangement set by peer")
        self._peer_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._peer_banner.setStyleSheet(
            "color: #40ffc0; background-color: #0f2020; font-family: monospace; "
            "font-size: 11px; border: 1px solid #20a070; padding: 3px 8px; border-radius: 3px;"
        )
        self._peer_banner.hide()
        outer.addWidget(self._peer_banner)

        self._peer_banner_timer = QTimer(self)
        self._peer_banner_timer.setSingleShot(True)
        self._peer_banner_timer.setInterval(3000)
        self._peer_banner_timer.timeout.connect(self._peer_banner.hide)

        # Internal state.
        self._local_monitors: list[MonitorInfo] = []
        self._peer_monitors: list[MonitorInfo] = []

        # Scene-space rectangles for local monitors (not items, just geometry).
        self._local_scene_rects: list[QRectF] = []

        # Snap position -> scene offset for the peer group's top-left corner.
        self._snap_offsets: dict[str, QPointF] = {}

        # Current snap position (may be None before first snap).
        self._current_snap: str | None = None

        # Whether a commit has happened in this session.
        self._arrangement_committed: bool = False

        # Last committed arrangement dict.
        self._last_arrangement: dict[int, set[str]] = {}

        # Draggable peer group item (locked mode: single group).
        self._peer_item: _DraggablePeerGroup | None = None

        # Independent peer-monitor items (unlocked mode: one item per peer monitor).
        self._peer_items_unlocked: list[_DraggablePeerItem] = []

        # Whether "Unlock Same PC Monitors" is active.
        self._monitors_unlocked: bool = False

        # Whether the panel is locked (CAPTURING/RECEIVING/TRANSITIONING).
        self._locked: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def arrangement_committed(self) -> bool:
        """Return True if the user has committed an arrangement at least once."""
        return self._arrangement_committed

    def committed_arrangement(self) -> dict[int, set[str]]:
        """Return the last committed arrangement dict."""
        return self._last_arrangement

    def monitors_unlocked(self) -> bool:
        """Return True if the per-monitor unlock toggle is currently checked."""
        return self._monitors_unlocked

    def set_monitors_unlocked(self, unlocked: bool) -> None:
        """
        Programmatically set the unlock toggle state without triggering a rebuild.

        Used at startup to restore persisted toggle preference before monitors are
        populated. The rebuild happens later when set_monitors() is called, so
        calling this before set_monitors() is safe.
        """
        self._monitors_unlocked = unlocked
        self._unlock_chk.blockSignals(True)
        self._unlock_chk.setChecked(unlocked)
        self._unlock_chk.blockSignals(False)

    def set_monitors(
        self,
        local_monitors: list[MonitorInfo],
        peer_monitors: list[MonitorInfo] | None,
    ) -> None:
        """
        Populate the panel with monitor geometries and rebuild the scene.

        local_monitors: this machine's monitor list.
        peer_monitors: peer machine's monitor list, or None (fallback to one 1x1 rect).

        Call this whenever a new connection is established (peer monitors arrive
        in the hello payload). Re-calling clears any prior arrangement.
        """
        self._local_monitors = local_monitors
        if peer_monitors:
            self._peer_monitors = peer_monitors
        else:
            # Fallback: synthesize a single generic peer monitor.
            # Use a size proportional to local[0] if available, else 1920x1080.
            if local_monitors:
                pw = local_monitors[0].width
                ph = local_monitors[0].height
            else:
                pw, ph = 1920, 1080
            self._peer_monitors = [
                MonitorInfo(x=0, y=0, width=pw, height=ph, dpi_scale=1.0, is_primary=True)
            ]
        self._arrangement_committed = False
        self._last_arrangement = {}
        self._current_snap = None
        self._rebuild_scene()

    def apply_dropdown_edge(self, edge: str) -> None:
        """
        Synchronize the panel to a dropdown selection.

        In locked (group-drag) mode: snaps the peer group to the cardinal
        position matching edge and auto-commits the arrangement. Called by
        MainWindow when the dropdown changes (and also at startup/connect to
        seed the default).

        In unlocked (per-monitor) mode: the dropdown does not control placement
        of individual monitors, so this call is a no-op. The user must manually
        position each peer monitor and click Commit.
        """
        if self._monitors_unlocked:
            return
        cardinal_map: dict[str, str] = {
            "right": _SNAP_RIGHT,
            "left": _SNAP_LEFT,
            "top": _SNAP_TOP,
            "bottom": _SNAP_BOTTOM,
        }
        snap_pos = cardinal_map.get(edge)
        if snap_pos is None:
            return
        self._apply_snap(snap_pos)
        self._do_commit()

    def on_state_changed(self, state: SwitchState) -> None:
        """
        Update panel visibility and locked state to match the switch state.

        Hidden in IDLE. Visible-and-editable in CONNECTED. Visible-but-locked
        in CAPTURING/RECEIVING/TRANSITIONING/RECONNECTING.
        """
        if state == SwitchState.IDLE:
            self.hide()
        elif state == SwitchState.CONNECTED:
            self.show()
            self._set_locked(False)
        else:
            # CAPTURING, RECEIVING, TRANSITIONING, RECONNECTING, LISTENING,
            # CONNECTING, HANDSHAKING -- show but lock.
            self.show()
            self._set_locked(True)

    def apply_remote_arrangement(self, arrangement: dict[int, set[str]]) -> None:
        """
        Programmatically apply an arrangement received from the peer.

        Moves the peer block to the snap position matching the arrangement,
        updates the snap state and status label, and marks the arrangement as
        committed.  The Commit button does NOT fire -- the remote arrangement
        is already committed by definition.

        Shows a 3-second "Arrangement set by peer" banner so the user knows
        the panel was updated remotely.

        Parameters
        ----------
        arrangement:
            Inverted arrangement computed by the caller (already suitable for
            this machine's EdgeDetector -- the caller has applied
            invert_arrangement() before calling here).
        """
        # Collect all edges in the arrangement to find the snap position.
        all_edges: set[str] = set()
        for edge_set in arrangement.values():
            all_edges.update(edge_set)

        # Map the edge set to the closest named snap position.
        # Corner positions match when both relevant edges are present.
        snap_pos: str | None = None
        has_right = "right" in all_edges
        has_left = "left" in all_edges
        has_top = "top" in all_edges
        has_bottom = "bottom" in all_edges

        if has_right and has_top:
            snap_pos = _SNAP_TOP_RIGHT
        elif has_right and has_bottom:
            snap_pos = _SNAP_BOTTOM_RIGHT
        elif has_left and has_top:
            snap_pos = _SNAP_TOP_LEFT
        elif has_left and has_bottom:
            snap_pos = _SNAP_BOTTOM_LEFT
        elif has_right:
            snap_pos = _SNAP_RIGHT
        elif has_left:
            snap_pos = _SNAP_LEFT
        elif has_top:
            snap_pos = _SNAP_TOP
        elif has_bottom:
            snap_pos = _SNAP_BOTTOM

        if snap_pos is not None and self._peer_item is not None:
            self._apply_snap(snap_pos)

        self._last_arrangement = arrangement
        self._arrangement_committed = True
        if self._monitors_unlocked:
            n = len(arrangement)
            self._status_label.setText(
                f"Arrangement: {n} monitor(s) set by peer (committed)"
            )
        elif self._current_snap is not None:
            self._update_status_label(self._current_snap)

        # Show the peer-set banner for 3 seconds.
        self._peer_banner.show()
        self._peer_banner_timer.start()

    # ------------------------------------------------------------------
    # Private: scene building
    # ------------------------------------------------------------------

    def _scale_factor(self) -> float:
        """
        Compute a scale factor so the local monitor group fits within _LOCAL_SCENE_W
        x _LOCAL_SCENE_H scene pixels. All monitors (local and peer) use this same
        factor so their relative sizes are preserved.
        """
        if not self._local_monitors:
            return 1.0
        # Compute the local virtual-desktop bounding box.
        lx0 = min(m.x for m in self._local_monitors)
        ly0 = min(m.y for m in self._local_monitors)
        lx1 = max(m.x + m.width for m in self._local_monitors)
        ly1 = max(m.y + m.height for m in self._local_monitors)
        local_w = lx0 + (lx1 - lx0) - lx0  # == lx1 - lx0
        local_h = ly0 + (ly1 - ly0) - ly0  # == ly1 - ly0
        # Protect against zero.
        local_w = max(local_w, 1)
        local_h = max(local_h, 1)
        sx = self._LOCAL_SCENE_W / local_w
        sy = self._LOCAL_SCENE_H / local_h
        return min(sx, sy)

    def _monitor_to_scene_rect(self, monitor: MonitorInfo, scale: float) -> QRectF:
        """Convert a monitor's virtual-desktop rect to a scene-space QRectF."""
        return QRectF(
            monitor.x * scale,
            monitor.y * scale,
            max(monitor.width * scale, 4.0),
            max(monitor.height * scale, 4.0),
        )

    def _rebuild_scene(self) -> None:
        """Clear and repopulate the scene from the current monitor lists."""
        self._scene.clear()
        self._peer_item = None
        self._peer_items_unlocked = []
        self._local_scene_rects = []
        self._snap_offsets = {}

        if not self._local_monitors:
            return

        scale = self._scale_factor()

        # --- Local monitors (static, not draggable) ---
        # Normalize so the local group's top-left is at (margin, margin).
        lx0 = min(m.x for m in self._local_monitors)
        ly0 = min(m.y for m in self._local_monitors)

        for mon in self._local_monitors:
            rx = (mon.x - lx0) * scale + self._SCENE_MARGIN
            ry = (mon.y - ly0) * scale + self._SCENE_MARGIN
            rw = max(mon.width * scale, 4.0)
            rh = max(mon.height * scale, 4.0)
            rect = QRectF(rx, ry, rw, rh)
            self._local_scene_rects.append(rect)
            item = QGraphicsRectItem(rect)
            item.setBrush(self._LOCAL_COLOR)
            item.setPen(QPen(self._LOCAL_BORDER, 1))
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            self._scene.addItem(item)
            # Label: show resolution.
            label = self._scene.addText(f"{mon.width}x{mon.height}")
            label.setDefaultTextColor(QColor("#1a1a2e"))
            font = QFont("monospace", 7)
            label.setFont(font)
            label.setPos(rx + 4, ry + 4)

        # Compute the bounding box of the local group in scene coords.
        lbx0 = min(r.x() for r in self._local_scene_rects)
        lby0 = min(r.y() for r in self._local_scene_rects)
        lbx1 = max(r.x() + r.width() for r in self._local_scene_rects)
        lby1 = max(r.y() + r.height() for r in self._local_scene_rects)

        # Connect mouseRelease via scene event filter approach.
        self._view.viewport().installEventFilter(self)

        if self._monitors_unlocked:
            self._rebuild_scene_unlocked(scale, lbx0, lby0, lbx1, lby1)
        else:
            self._rebuild_scene_locked(scale, lbx0, lby0, lbx1, lby1)

    def _rebuild_scene_locked(
        self,
        scale: float,
        lbx0: float,
        lby0: float,
        lbx1: float,
        lby1: float,
    ) -> None:
        """
        Rebuild the scene in locked (group-drag) mode.

        All peer monitors move as a single unit. Snap positions are the same 8
        positions relative to the local monitor cluster as in the original Task #7
        design.
        """
        # --- Peer monitors (draggable group) ---
        px0 = min(m.x for m in self._peer_monitors)
        py0 = min(m.y for m in self._peer_monitors)
        peer_rects_local: list[QRectF] = []
        for mon in self._peer_monitors:
            prx = (mon.x - px0) * scale
            pry = (mon.y - py0) * scale
            prw = max(mon.width * scale, 4.0)
            prh = max(mon.height * scale, 4.0)
            peer_rects_local.append(QRectF(prx, pry, prw, prh))

        self._peer_item = _DraggablePeerGroup(peer_rects_local)
        self._scene.addItem(self._peer_item)

        # Peer group bounding box dimensions.
        pgw = (
            max(r.x() + r.width() for r in peer_rects_local)
            - min(r.x() for r in peer_rects_local)
        )
        pgh = (
            max(r.y() + r.height() for r in peer_rects_local)
            - min(r.y() for r in peer_rects_local)
        )

        # Snap-position top-left corners for the peer group.
        self._snap_offsets = {
            _SNAP_RIGHT: QPointF(lbx1, lby0 + (lby1 - lby0) / 2 - pgh / 2),
            _SNAP_LEFT: QPointF(lbx0 - pgw, lby0 + (lby1 - lby0) / 2 - pgh / 2),
            _SNAP_TOP: QPointF(lbx0 + (lbx1 - lbx0) / 2 - pgw / 2, lby0 - pgh),
            _SNAP_BOTTOM: QPointF(lbx0 + (lbx1 - lbx0) / 2 - pgw / 2, lby1),
            _SNAP_TOP_RIGHT: QPointF(lbx1, lby0 - pgh),
            _SNAP_TOP_LEFT: QPointF(lbx0 - pgw, lby0 - pgh),
            _SNAP_BOTTOM_RIGHT: QPointF(lbx1, lby1),
            _SNAP_BOTTOM_LEFT: QPointF(lbx0 - pgw, lby1),
        }

        # Place at default snap position.
        default_snap = _SNAP_RIGHT
        if self._current_snap is not None and self._current_snap in self._snap_offsets:
            default_snap = self._current_snap
        self._apply_snap(default_snap)

        # Set scene rect to include everything with margin.
        all_rects = self._local_scene_rects + [
            QRectF(
                self._peer_item.pos().x() + r.x(),
                self._peer_item.pos().y() + r.y(),
                r.width(),
                r.height(),
            )
            for r in peer_rects_local
        ]
        sx0 = min(r.x() for r in all_rects) - self._SCENE_MARGIN
        sy0 = min(r.y() for r in all_rects) - self._SCENE_MARGIN
        sx1 = max(r.x() + r.width() for r in all_rects) + self._SCENE_MARGIN
        sy1 = max(r.y() + r.height() for r in all_rects) + self._SCENE_MARGIN
        self._scene.setSceneRect(QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0))
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        self._commit_btn.setEnabled(not self._locked)

    def _rebuild_scene_unlocked(
        self,
        scale: float,
        lbx0: float,
        lby0: float,
        lbx1: float,
        lby1: float,
    ) -> None:
        """
        Rebuild the scene in unlocked (per-monitor independent drag) mode.

        Each peer monitor gets its own _DraggablePeerItem. Items are placed in a
        vertical stack to the right of the local monitor cluster by default so
        they are visible and do not overlap on first render.
        """
        lw = lbx1 - lbx0
        lh = lby1 - lby0

        y_cursor = lby0
        for idx, mon in enumerate(self._peer_monitors):
            prw = max(mon.width * scale, 4.0)
            prh = max(mon.height * scale, 4.0)
            # item-local rect always starts at (0,0).
            item_rect = QRectF(0.0, 0.0, prw, prh)
            peer_item = _DraggablePeerItem(item_rect, idx)
            if self._locked:
                peer_item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            self._scene.addItem(peer_item)
            # Position: stack to the right of local monitors.
            peer_item.setPos(QPointF(lbx1, y_cursor))
            y_cursor += prh + 4.0
            self._peer_items_unlocked.append(peer_item)
            # Label the item with its monitor index.
            label = self._scene.addText(f"P{idx}")
            label.setDefaultTextColor(QColor("#1a1a2e"))
            font = QFont("monospace", 7)
            label.setFont(font)
            label.setPos(peer_item.pos().x() + 2, peer_item.pos().y() + 2)

        # Scene rect: include local + all peer items with margin.
        all_scene_rects: list[QRectF] = list(self._local_scene_rects)
        for item in self._peer_items_unlocked:
            all_scene_rects.append(item.scene_rect())

        sx0 = min(r.x() for r in all_scene_rects) - self._SCENE_MARGIN
        sy0 = min(r.y() for r in all_scene_rects) - self._SCENE_MARGIN
        sx1 = max(r.x() + r.width() for r in all_scene_rects) + self._SCENE_MARGIN
        sy1 = max(r.y() + r.height() for r in all_scene_rects) + self._SCENE_MARGIN
        # Add extra margin to give dragging room.
        sx1 += lw
        sy1 += lh
        self._scene.setSceneRect(QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0))
        self._view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

        self._commit_btn.setEnabled(not self._locked)

    def _apply_snap(self, snap_pos: str) -> None:
        """Move the peer group item to the named snap position and update highlights."""
        if self._peer_item is None:
            return
        offset = self._snap_offsets.get(snap_pos)
        if offset is None:
            return
        self._peer_item.setPos(offset)
        self._current_snap = snap_pos
        self._update_highlights(snap_pos)
        self._update_status_label(snap_pos)

    def _update_highlights(self, snap_pos: str) -> None:
        """Draw green highlights on the local monitor edges that touch the peer."""
        if self._peer_item is None:
            return
        edges = _SNAP_TO_EDGES.get(snap_pos, set())
        # Build highlight list: for each local monitor rect, for each touching edge.
        # Convert local scene rects to item-local coordinates (subtract item.pos()).
        item_pos = self._peer_item.pos()
        highlights: list[tuple[QRectF, str]] = []
        for scene_rect in self._local_scene_rects:
            local_rect = QRectF(
                scene_rect.x() - item_pos.x(),
                scene_rect.y() - item_pos.y(),
                scene_rect.width(),
                scene_rect.height(),
            )
            for edge in edges:
                highlights.append((local_rect, edge))
        self._peer_item.set_highlights(highlights)

    def _update_status_label(self, snap_pos: str) -> None:
        """Update the status label to describe the current snap position."""
        readable = {
            _SNAP_RIGHT: "Peer is to the right",
            _SNAP_LEFT: "Peer is to the left",
            _SNAP_TOP: "Peer is above",
            _SNAP_BOTTOM: "Peer is below",
            _SNAP_TOP_RIGHT: "Peer is top-right",
            _SNAP_TOP_LEFT: "Peer is top-left",
            _SNAP_BOTTOM_RIGHT: "Peer is bottom-right",
            _SNAP_BOTTOM_LEFT: "Peer is bottom-left",
        }
        text = readable.get(snap_pos, snap_pos)
        committed_note = " (committed)" if self._arrangement_committed else ""
        self._status_label.setText(f"Arrangement: {text}{committed_note}")

    # ------------------------------------------------------------------
    # Private: event filter for drag-end snapping
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        """
        Intercept mouse-release on the scene viewport.

        In locked (group-drag) mode: find the nearest snap position for the
        peer group and snap to it.

        In unlocked (per-monitor) mode: snap each peer item independently to
        the nearest edge of any local monitor or other peer monitor. Updates
        highlights on all items after snapping.
        """
        from PyQt6.QtCore import QEvent
        if (
            obj is self._view.viewport()
            and event.type() == QEvent.Type.MouseButtonRelease
            and not self._locked
        ):
            if self._monitors_unlocked:
                self._snap_unlocked_items()
            elif self._peer_item is not None:
                self._snap_to_nearest()
        return super().eventFilter(obj, event)

    def _snap_unlocked_items(self) -> None:
        """
        Snap each independent peer-monitor item to the nearest qualifying edge.

        For each peer item, candidate snap targets are:
          1. Edges of every local monitor rectangle (in scene coords).
          2. Edges of every other peer monitor item (in scene coords).

        A "snap" aligns the item so it abuts the target edge with zero gap.
        Only the closest edge within _SNAP_THRESHOLD is used. Candidates use
        center-to-center proximity on the dominant axis to avoid bizarre
        snapping when the item is far away on the perpendicular axis.

        After all items have been repositioned, highlights are refreshed.
        """
        if not self._peer_items_unlocked:
            return

        for item in self._peer_items_unlocked:
            item_sr = item.scene_rect()
            iw = item_sr.width()
            ih = item_sr.height()
            icx = item_sr.x() + iw / 2
            icy = item_sr.y() + ih / 2

            best_pos: QPointF | None = None
            best_dist: float = self._SNAP_THRESHOLD

            # --- Candidates from local monitor rects ---
            for local_rect in self._local_scene_rects:
                lx = local_rect.x()
                ly = local_rect.y()
                lw = local_rect.width()
                lh = local_rect.height()

                candidates: list[tuple[float, float]] = [
                    # right edge of local -> item's left attaches
                    (lx + lw, ly + lh / 2 - ih / 2),
                    # left edge of local -> item's right attaches
                    (lx - iw, ly + lh / 2 - ih / 2),
                    # bottom edge of local -> item's top attaches
                    (lx + lw / 2 - iw / 2, ly + lh),
                    # top edge of local -> item's bottom attaches
                    (lx + lw / 2 - iw / 2, ly - ih),
                ]
                for sx, sy in candidates:
                    ccx = sx + iw / 2
                    ccy = sy + ih / 2
                    dist = ((icx - ccx) ** 2 + (icy - ccy) ** 2) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                        best_pos = QPointF(sx, sy)

            # --- Candidates from other peer items ---
            for other in self._peer_items_unlocked:
                if other is item:
                    continue
                other_sr = other.scene_rect()
                ox = other_sr.x()
                oy = other_sr.y()
                ow = other_sr.width()
                oh = other_sr.height()

                candidates_peer: list[tuple[float, float]] = [
                    (ox + ow, oy + oh / 2 - ih / 2),
                    (ox - iw, oy + oh / 2 - ih / 2),
                    (ox + ow / 2 - iw / 2, oy + oh),
                    (ox + ow / 2 - iw / 2, oy - ih),
                ]
                for sx, sy in candidates_peer:
                    ccx = sx + iw / 2
                    ccy = sy + ih / 2
                    dist = ((icx - ccx) ** 2 + (icy - ccy) ** 2) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                        best_pos = QPointF(sx, sy)

            if best_pos is not None:
                item.setPos(best_pos)

        # Refresh highlights after all items are snapped.
        self._update_highlights_unlocked()

    def _update_highlights_unlocked(self) -> None:
        """
        Refresh edge highlights on all peer items in unlocked mode.

        For each peer item, find every local monitor edge it is touching (within
        2 scene pixels of abutting). Collect highlights as (local_rect_in_item_coords,
        edge) pairs and pass to the item.
        """
        touch_px = 2.0
        for item in self._peer_items_unlocked:
            item_sr = item.scene_rect()
            item_pos = item.pos()
            highlights: list[tuple[QRectF, str]] = []

            for local_rect in self._local_scene_rects:
                # Convert local_rect to item-local coordinates.
                local_in_item = QRectF(
                    local_rect.x() - item_pos.x(),
                    local_rect.y() - item_pos.y(),
                    local_rect.width(),
                    local_rect.height(),
                )
                lx = local_rect.x()
                ly = local_rect.y()
                lw = local_rect.width()
                lh = local_rect.height()
                ix = item_sr.x()
                iy = item_sr.y()
                iw = item_sr.width()
                ih = item_sr.height()

                # right edge of local touches left edge of item
                if abs((lx + lw) - ix) < touch_px:
                    highlights.append((local_in_item, "right"))
                # left edge of local touches right edge of item
                if abs(lx - (ix + iw)) < touch_px:
                    highlights.append((local_in_item, "left"))
                # bottom edge of local touches top edge of item
                if abs((ly + lh) - iy) < touch_px:
                    highlights.append((local_in_item, "bottom"))
                # top edge of local touches bottom edge of item
                if abs(ly - (iy + ih)) < touch_px:
                    highlights.append((local_in_item, "top"))

            item.set_highlights(highlights)

    def _snap_to_nearest(self) -> None:
        """Snap the peer group to the nearest snap position, if within threshold."""
        if self._peer_item is None:
            return
        current_pos = self._peer_item.pos()
        best_snap: str | None = None
        best_dist: float = self._SNAP_THRESHOLD

        for snap_pos, target_pos in self._snap_offsets.items():
            dx = current_pos.x() - target_pos.x()
            dy = current_pos.y() - target_pos.y()
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_snap = snap_pos

        if best_snap is not None:
            self._apply_snap(best_snap)
        else:
            # Not near any snap -- return to previous snap or default.
            fallback = self._current_snap or _SNAP_RIGHT
            self._apply_snap(fallback)

    # ------------------------------------------------------------------
    # Private: commit logic
    # ------------------------------------------------------------------

    def _on_commit_clicked(self) -> None:
        """Commit the current snap arrangement."""
        if self._monitors_unlocked:
            self._do_commit_unlocked()
        elif self._current_snap is not None:
            self._do_commit()

    def _do_commit(self) -> None:
        """Derive the arrangement from the current snap position and emit it."""
        if self._current_snap is None:
            return
        arrangement = self._compute_arrangement(self._current_snap)
        self._last_arrangement = arrangement
        self._arrangement_committed = True
        self._update_status_label(self._current_snap)
        self.arrangement_changed.emit(arrangement)

    def _do_commit_unlocked(self) -> None:
        """
        Derive and emit the per-monitor arrangement from independently positioned items.

        Calls _compute_arrangement_unlocked() to derive {local_idx: {edges}} from
        geometric proximity of peer items to local monitor rects.
        """
        if not self._peer_items_unlocked:
            return
        arrangement = self._compute_arrangement_unlocked()
        self._last_arrangement = arrangement
        self._arrangement_committed = True
        self._status_label.setText(
            f"Arrangement: {len(self._peer_items_unlocked)} peer monitor(s) placed (committed)"
        )
        self.arrangement_changed.emit(arrangement)

    def _compute_arrangement(self, snap_pos: str) -> dict[int, set[str]]:
        """
        Derive a per-monitor exit-edge arrangement from snap_pos.

        For single-monitor local setups this maps directly from _SNAP_TO_EDGES.

        For multi-monitor local setups: determine which local monitors are
        adjacent to the peer group for each relevant edge direction, and assign
        those exit edges to those monitor indices. Simple implementation: all
        local monitors that are on the outer boundary in that direction get the
        edge, regardless of whether peer rects overlap them specifically.
        This is sufficient for the current use cases (one or two local monitors).
        """
        edges = _SNAP_TO_EDGES.get(snap_pos, set())
        if not edges:
            return {}

        if len(self._local_monitors) <= 1:
            return {0: set(edges)}

        # Multi-monitor: assign exit edges to monitors on the relevant boundary.
        result: dict[int, set[str]] = {}
        for edge in edges:
            # Find the extreme boundary value for this edge.
            if edge == "right":
                extreme = max(m.x + m.width for m in self._local_monitors)
                for idx, mon in enumerate(self._local_monitors):
                    if mon.x + mon.width == extreme:
                        result.setdefault(idx, set()).add(edge)
            elif edge == "left":
                extreme = min(m.x for m in self._local_monitors)
                for idx, mon in enumerate(self._local_monitors):
                    if mon.x == extreme:
                        result.setdefault(idx, set()).add(edge)
            elif edge == "top":
                extreme = min(m.y for m in self._local_monitors)
                for idx, mon in enumerate(self._local_monitors):
                    if mon.y == extreme:
                        result.setdefault(idx, set()).add(edge)
            elif edge == "bottom":
                extreme = max(m.y + m.height for m in self._local_monitors)
                for idx, mon in enumerate(self._local_monitors):
                    if mon.y + mon.height == extreme:
                        result.setdefault(idx, set()).add(edge)
        return result

    def _compute_arrangement_unlocked(self) -> dict[int, set[str]]:
        """
        Derive a per-local-monitor exit-edge arrangement from independently placed items.

        For each peer monitor item, determine which local monitor(s) it is touching
        and on which edge(s). A touch is detected when the peer item's bounding box
        abuts a local monitor's edge within _SNAP_THRESHOLD scene pixels.

        The returned dict maps local_monitor_index -> set of exit edges that face
        the peer on that monitor.

        If a peer item does not touch any local monitor, a WARNING is logged and
        that item is skipped. The arrangement is still emitted with whatever valid
        entries exist (no gate on layout sanity per task spec).

        If no items touch any local monitor (fully disconnected layout), falls back
        to {0: {"right"}} and logs a WARNING.

        Edge determination per peer item and local monitor:
          - item's left abuts local's right  -> local has exit edge "right"
          - item's right abuts local's left  -> local has exit edge "left"
          - item's top abuts local's bottom  -> local has exit edge "bottom"
          - item's bottom abuts local's top  -> local has exit edge "top"
        """
        touch_px = self._SNAP_THRESHOLD
        result: dict[int, set[str]] = {}
        any_touch = False

        for item in self._peer_items_unlocked:
            item_sr = item.scene_rect()
            ix = item_sr.x()
            iy = item_sr.y()
            iw = item_sr.width()
            ih = item_sr.height()
            item_touched = False

            for local_idx, local_rect in enumerate(self._local_scene_rects):
                lx = local_rect.x()
                ly = local_rect.y()
                lw = local_rect.width()
                lh = local_rect.height()

                # Horizontal adjacency: item's left abuts local's right (exit "right").
                if abs(ix - (lx + lw)) < touch_px:
                    result.setdefault(local_idx, set()).add("right")
                    item_touched = True
                    any_touch = True
                # item's right abuts local's left (exit "left").
                if abs((ix + iw) - lx) < touch_px:
                    result.setdefault(local_idx, set()).add("left")
                    item_touched = True
                    any_touch = True
                # Vertical adjacency: item's top abuts local's bottom (exit "bottom").
                if abs(iy - (ly + lh)) < touch_px:
                    result.setdefault(local_idx, set()).add("bottom")
                    item_touched = True
                    any_touch = True
                # item's bottom abuts local's top (exit "top").
                if abs((iy + ih) - ly) < touch_px:
                    result.setdefault(local_idx, set()).add("top")
                    item_touched = True
                    any_touch = True

            if not item_touched:
                logger.warning(
                    "_compute_arrangement_unlocked: peer monitor %d does not touch "
                    "any local monitor -- skipped in arrangement. "
                    "The resulting layout may be disconnected.",
                    item.monitor_index,
                )

        if not any_touch:
            logger.warning(
                "_compute_arrangement_unlocked: no peer items touch any local monitor. "
                "Falling back to {0: {'right'}} so EdgeDetector has a valid arrangement."
            )
            return {0: {"right"}}

        return result

    def _set_locked(self, locked: bool) -> None:
        """Lock or unlock the panel (disable drag and commit during active mirroring)."""
        self._locked = locked
        has_item = (
            self._peer_item is not None
            or bool(self._peer_items_unlocked)
        )
        self._commit_btn.setEnabled(not locked and has_item)
        # Unlock checkbox is temporarily force-disabled (see __init__).
        self._unlock_chk.setEnabled(False)
        if self._peer_item is not None:
            self._peer_item.setFlag(
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not locked
            )
        for item in self._peer_items_unlocked:
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not locked)

    def _on_unlock_toggled(self, state: int) -> None:
        """
        Called when the "Unlock Same PC Monitors" checkbox changes.

        Switches between group-drag (locked) mode and per-monitor independent-drag
        (unlocked) mode. Rebuilds the scene to reflect the new mode. Resets the
        arrangement committed state so the user must re-commit after switching modes.
        """
        self._monitors_unlocked = state != 0
        # Reset committed state -- the arrangement derivation path changes.
        self._arrangement_committed = False
        self._last_arrangement = {}
        self._rebuild_scene()
        self.monitors_unlock_changed.emit(self._monitors_unlocked)
        logger.debug(
            "_ScreenArrangementPanel: unlock toggle -> monitors_unlocked=%s",
            self._monitors_unlocked,
        )


class _BrowserPanel(QGroupBox):
    """
    Browser extension control panel.

    Row 1: "List Tabs" button (quick action, existing) + connection status label.
    Row 2: Browser command composer -- a dropdown of command names, a params
           field (JSON object or simple key=value pairs), and a Send button.
           Results land in the same main log panel via the _browser_log signal.

    The panel is always visible. The lazy connection-count check on each button
    press is intentional -- no polling timer needed for a prototype.

    Phase A commands available in the composer:
        switch_to_tab   params: {"tabId": <int>}
        get_cookies     params: {"domain": "<string>"}
        wait_for_selector  params: {"tabId": <int>, "selector": "<css>", "timeoutMs": <int>}

    B3 commands added to the composer:
        open_url        params: {"url": "<string>"} -- opens URL in a new tab
        paste_text      params: {"text": "<string>"} -- inserts text into the active input
    """

    # Signal emitted with a human-readable result string for the log panel.
    # Declared at class level so Qt registers it correctly.
    result_ready = pyqtSignal(str)

    # Commands available in the composer dropdown, with placeholder param hints.
    _COMPOSER_COMMANDS: list[tuple[str, str]] = [
        ("switch_to_tab",      '{"tabId": 0}'),
        ("get_cookies",        '{"domain": "example.com"}'),
        ("wait_for_selector",  '{"tabId": 0, "selector": "#main", "timeoutMs": 5000}'),
        ("open_url",           '{"url": "https://example.com"}'),
        ("paste_text",         '{"text": "hello from Slickshift"}'),
        ("click_selector",     '{"tabId": 0, "selector": "a#video-title"}'),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Browser Extension", parent)
        self.setStyleSheet(
            "QGroupBox { color: #e0e0e0; font-family: monospace; font-size: 12px; "
            "border: 1px solid #2a2a4e; margin-top: 6px; padding-top: 4px; } "
            "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 12, 8, 8)
        outer.setSpacing(4)

        btn_style = (
            "QPushButton { background-color: #16213e; color: #e0e0e0; "
            "font-family: monospace; font-size: 12px; border: 1px solid #3a3a6e; "
            "padding: 4px 12px; } "
            "QPushButton:hover { background-color: #1e2f5e; } "
            "QPushButton:disabled { color: #555555; border-color: #2a2a4e; }"
        )

        # --- Row 1: quick-action button + status ---
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        self._list_tabs_btn = QPushButton("List Tabs")
        self._list_tabs_btn.setToolTip(
            "Send list_tabs to the connected Chrome extension and print results to the log."
        )
        self._list_tabs_btn.setStyleSheet(btn_style)
        row1.addWidget(self._list_tabs_btn)

        self._status_label = QLabel("No extension connected")
        self._status_label.setStyleSheet(
            "color: #888888; font-family: monospace; font-size: 12px;"
        )
        row1.addWidget(self._status_label)
        row1.addStretch()
        outer.addLayout(row1)

        # --- Row 2: browser command composer ---
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self._cmd_combo = QComboBox()
        self._cmd_combo.setStyleSheet(
            "QComboBox { background-color: #16213e; color: #e0e0e0; "
            "font-family: monospace; font-size: 12px; border: 1px solid #3a3a6e; "
            "padding: 2px 6px; } "
            "QComboBox::drop-down { border: none; } "
            "QComboBox QAbstractItemView { background-color: #16213e; color: #e0e0e0; "
            "selection-background-color: #1e2f5e; }"
        )
        for cmd_name, _ in self._COMPOSER_COMMANDS:
            self._cmd_combo.addItem(cmd_name)
        # Populate params hint when the selection changes.
        self._cmd_combo.currentIndexChanged.connect(self._on_cmd_selected)
        row2.addWidget(self._cmd_combo)

        self._params_field = QLineEdit()
        self._params_field.setPlaceholderText("params (JSON)")
        self._params_field.setStyleSheet(
            "QLineEdit { background-color: #16213e; color: #e0e0e0; "
            "font-family: monospace; font-size: 12px; border: 1px solid #3a3a6e; "
            "padding: 2px 6px; }"
        )
        # Pre-fill with the hint for the first item.
        if self._COMPOSER_COMMANDS:
            self._params_field.setText(self._COMPOSER_COMMANDS[0][1])
        row2.addWidget(self._params_field, stretch=1)

        self._send_btn = QPushButton("Send")
        self._send_btn.setStyleSheet(btn_style)
        self._send_btn.setToolTip("Send the selected browser command with the given params.")
        row2.addWidget(self._send_btn)
        outer.addLayout(row2)

    def _on_cmd_selected(self, index: int) -> None:
        """Fill the params field with the placeholder for the newly selected command."""
        if 0 <= index < len(self._COMPOSER_COMMANDS):
            self._params_field.setText(self._COMPOSER_COMMANDS[index][1])

    def set_connected(self, count: int) -> None:
        """Update the status label to reflect the current connection count."""
        if count == 0:
            self._status_label.setText("No extension connected")
            self._status_label.setStyleSheet(
                "color: #888888; font-family: monospace; font-size: 12px;"
            )
        elif count == 1:
            self._status_label.setText("Extension connected")
            self._status_label.setStyleSheet(
                "color: #40e040; font-family: monospace; font-size: 12px;"
            )
        else:
            self._status_label.setText(f"{count} extensions connected")
            self._status_label.setStyleSheet(
                "color: #40e040; font-family: monospace; font-size: 12px;"
            )

    @property
    def list_tabs_btn(self) -> QPushButton:
        return self._list_tabs_btn

    @property
    def send_btn(self) -> QPushButton:
        return self._send_btn

    def current_command(self) -> str:
        """Return the command name currently selected in the composer dropdown."""
        return self._cmd_combo.currentText()

    def current_params_text(self) -> str:
        """Return the raw text from the params field."""
        return self._params_field.text().strip()


class MainWindow(QMainWindow):
    """Root application window. Owns all panels, state, transport, capture, and edge detection."""

    # Signal used by the browser-agent background thread to push log lines to
    # the main thread safely (never call Qt widgets from a non-Qt thread).
    _browser_log = pyqtSignal(str)

    def __init__(self, browser_agent=None) -> None:
        super().__init__()
        self.setWindowTitle("Slickshift")
        self.setMinimumSize(QSize(700, 620))
        self.resize(960, 740)
        self.setStyleSheet("background-color: #0f0f1a;")

        # --- QSettings: cross-platform persistent key/value store ---
        # Used to remember the last peer address across launches.
        # macOS: ~/Library/Preferences/com.Slickshift.Slickshift.plist
        # Windows: registry under HKCU\Software\Slickshift\Slickshift
        # Linux:   ~/.config/Slickshift/Slickshift.ini
        self._settings = QSettings("Slickshift", "Slickshift")

        # --- DPI scale for this machine ---
        # Read from Qt's primary screen so it matches what the hello payload reports.
        _primary_screen = QApplication.primaryScreen()
        self._dpi_scale: float = (
            _primary_screen.devicePixelRatio() if _primary_screen is not None else 1.0
        )

        # --- Empirical DPI mode detection ---
        # Qt reports logical pixels. pyautogui.size() may report either logical or
        # physical pixels depending on the OS DPI awareness mode.
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

        # --- State, transport, capture, edge detection, and injection ---
        self._state_ctrl = StateController()
        self._transport = TcpTransport()
        # Reuse the pyautogui size already captured above for the diagnostic log.
        self._screen_w: int = _pyautogui_w
        self._screen_h: int = _pyautogui_h

        # Enumerate local monitors for multi-monitor coordinate math.
        # enumerate_local_monitors() requires a QGuiApplication to be running;
        # at this point in __init__ the QApplication is already up.
        self._local_monitors: list[MonitorInfo] = enumerate_local_monitors()

        # Single-monitor unit reconciliation. On DPI-aware Windows, Qt reports
        # logical pixels (e.g. 2560x1440) while pyautogui.position() returns
        # physical pixels (e.g. 3840x2160). Normalizing pyautogui coordinates
        # against a Qt-derived bbox produces nx/ny > 1.0 and edge bands at the
        # wrong screen position. Reconcile by replacing the single monitor's
        # dimensions with pyautogui's, so cursor positions and bbox share a
        # unit space. Task #7 will do a per-monitor DPI-aware reconciliation
        # for true multi-monitor support.
        if (
            len(self._local_monitors) == 1
            and (
                self._local_monitors[0].width != _pyautogui_w
                or self._local_monitors[0].height != _pyautogui_h
            )
        ):
            qt_mon = self._local_monitors[0]
            self._local_monitors[0] = MonitorInfo(
                x=0,
                y=0,
                width=_pyautogui_w,
                height=_pyautogui_h,
                dpi_scale=qt_mon.dpi_scale,
                is_primary=True,
            )
            logger.info(
                "Single-monitor unit reconciliation: Qt %dx%d at (%d,%d) -> "
                "pyautogui %dx%d at (0,0) to match cursor coordinate space",
                qt_mon.width, qt_mon.height, qt_mon.x, qt_mon.y,
                _pyautogui_w, _pyautogui_h,
            )

        self._local_vd_x: int
        self._local_vd_y: int
        self._local_vd_w: int
        self._local_vd_h: int
        (
            self._local_vd_x,
            self._local_vd_y,
            self._local_vd_w,
            self._local_vd_h,
        ) = compute_virtual_desktop_bbox(self._local_monitors)
        logger.info(
            "Local virtual desktop bbox: origin=(%d,%d) size=%dx%d (%d monitor(s))",
            self._local_vd_x,
            self._local_vd_y,
            self._local_vd_w,
            self._local_vd_h,
            len(self._local_monitors),
        )

        # MouseCapture: produces delta callbacks only. No edge logic inside.
        # Receives the local monitor list so it can normalize against the full
        # virtual-desktop bounding box rather than the primary monitor alone.
        # make_mouse_capture() returns MacMouseCapture (CGEventTap) on macOS
        # and the polling MouseCapture on Windows/Linux -- same interface either way.
        self._capture = make_mouse_capture(self._local_monitors)

        # EdgeDetector: owns edge-band detection, dwell timer, and cooldown guard.
        # The UI feeds cursor positions to it on every poll tick.
        # The edge-dwell callback is registered here (was on DeltaCapture before).
        # Receives the local monitor list so it can check per-monitor edge bands.
        self._edge_detector = EdgeDetector(self._local_monitors)
        self._edge_detector.set_peer_edge(self._mirror_panel_default_edge())
        self._edge_detector.register_callback(self._schedule_handoff_fire)

        self._injector = make_mouse_injector()
        self._peer_info: dict | None = None  # last received hello payload from peer
        # Parsed monitor list from the peer's most recent hello. None means the
        # peer did not send a monitors field (old peer or not yet connected).
        self._peer_monitors: list[MonitorInfo] | None = None

        # Peer identity string used as the QSettings key base for per-peer
        # arrangement persistence.  Set in _handle_hello() from machine_name,
        # falling back to the address text the user typed.  Reset to "" on
        # disconnect.  Empty string means no identity known yet (no save/load).
        self._peer_identity: str = ""

        # Signal bridge: capture-thread -> Qt main thread.
        # Must be created on the main thread (here in __init__) so Qt assigns it
        # to the main thread's event loop.
        self._main_signals = _MainSignals()
        self._main_signals.edge_dwell_fired.connect(self._on_edge_dwell_fired)
        self._main_signals.click_fired.connect(self._on_click_fired)
        self._main_signals.scroll_fired.connect(self._on_scroll_fired)
        self._main_signals.arrangement_update_received.connect(
            self._handle_arrangement_update
        )

        # Event capture: pynput-based button and scroll event listener.
        self._event_capture = EventCapture(
            on_click=self._on_event_capture_click,
            on_scroll=self._on_event_capture_scroll,
        )

        # Whether OS cursor injection is currently active on this machine.
        self._injection_enabled: bool = True

        # Tracks whether MouseCapture is currently paused. Maintained by the UI
        # whenever set_paused() is called on _capture, so _poll_cursor can pass
        # the correct paused flag to EdgeDetector.tick() without reaching into
        # MouseCapture internals. Also gates the edge detector when the logical
        # cursor is on the peer's side (no need to advance dwell on a parked
        # or locked local cursor).
        self._capture_paused: bool = False

        # Session-master flag. True on the machine that clicked Start Mirroring,
        # False on the peer (set when mirror_start arrives), None when not
        # mirroring. The master sources all input for the session; the slave
        # never sources. Roles do not swap on edge crossings.
        self._is_master: bool | None = None

        # Logical cursor side. True when the cursor logically lives on this
        # machine's screen, False when it is on the peer. Flipped by handoff
        # request/ack exchanges. Master toggles exclusive mode on this flag;
        # slave toggles its edge detector on this flag.
        self._cursor_on_local: bool = True

        # QTimer that fires after HANDOFF_ACK_TIMEOUT_S if no handoff_ack arrives.
        # Single-shot; started when we enter TRANSITIONING, cancelled on ack or rollback.
        self._handoff_ack_timer = QTimer(self)
        self._handoff_ack_timer.setSingleShot(True)
        self._handoff_ack_timer.setInterval(int(config.HANDOFF_ACK_TIMEOUT_S * 1000))
        self._handoff_ack_timer.timeout.connect(self._on_handoff_ack_timeout)

        # User-initiated disconnect flag.
        # Set to True when the user explicitly clicks Disconnect or presses the
        # force-release hotkey. Reset to False on each Connect/Listen attempt.
        # When False and the transport fires a disconnected signal, the reconnect
        # path is entered instead of going straight to IDLE.
        self._user_initiated_disconnect: bool = False

        # Flag set while a reconnect attempt is in progress and the handshake is
        # completing. Tells _handle_hello to restore the prior role.
        self._reconnect_restoring_role: bool = False

        # Reconnect state.
        self._reconnect_prev_role: SwitchState = SwitchState.IDLE
        self._reconnect_attempt: int = 0
        self._reconnect_delay_s: float = config.RECONNECT_INITIAL_DELAY_S
        self._reconnect_host: str = ""
        self._reconnect_port: int = config.DEFAULT_PORT

        # Reconnect timer. Single-shot; restarted on each failed attempt.
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._on_reconnect_attempt)

        # Idle timeout in RECEIVING state.
        # Fires every 1 s while in RECEIVING.
        self._last_delta_received_time: float = 0.0
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(1000)  # 1 s tick
        self._idle_timer.timeout.connect(self._check_idle_timeout)

        # System-wide force-release hotkey via pynput keyboard listener.
        self._kb_pressed_keys: set[_pynput_keyboard.Key | _pynput_keyboard.KeyCode] = set()
        self._kb_pressed_keys_lock = threading.Lock()
        self._kb_listener: _pynput_keyboard.Listener | None = None

        # Keyboard forwarding state.
        # True only when the checkbox is checked AND state is CAPTURING.
        self._keyboard_forwarding_active: bool = False

        # Translation map: pynput Key enum -> pyautogui key name string.
        _key_pairs: list[tuple[str, str]] = [
            ("shift", "shift"),
            ("shift_l", "shift"),
            ("shift_r", "shiftright"),
            ("ctrl", "ctrl"),
            ("ctrl_l", "ctrl"),
            ("ctrl_r", "ctrlright"),
            ("alt", "alt"),
            ("alt_l", "alt"),
            ("alt_r", "altright"),
            ("cmd", "command"),
            ("cmd_r", "cmdright"),
            ("enter", "enter"),
            ("esc", "esc"),
            ("space", "space"),
            ("tab", "tab"),
            ("backspace", "backspace"),
            ("delete", "delete"),
            ("up", "up"),
            ("down", "down"),
            ("left", "left"),
            ("right", "right"),
            ("f1", "f1"), ("f2", "f2"), ("f3", "f3"), ("f4", "f4"),
            ("f5", "f5"), ("f6", "f6"), ("f7", "f7"), ("f8", "f8"),
            ("f9", "f9"), ("f10", "f10"), ("f11", "f11"), ("f12", "f12"),
            ("home", "home"),
            ("end", "end"),
            ("page_up", "pageup"),
            ("page_down", "pagedown"),
            ("insert", "insert"),
            ("caps_lock", "capslock"),
            ("num_lock", "numlock"),
        ]
        self._PYNPUT_KEY_MAP: dict = {}
        for _attr, _target in _key_pairs:
            _key_obj = getattr(_pynput_keyboard.Key, _attr, None)
            if _key_obj is not None:
                self._PYNPUT_KEY_MAP[_key_obj] = _target

        # Wire transport signals to main-thread handlers.
        self._transport.register_connected_callback(self._on_transport_connected)
        self._transport.register_disconnected_callback(self._on_transport_disconnected)
        self._transport.register_message_callback(self._on_message_received)

        # Wire state changes to panels.
        self._state_ctrl.register_state_change_callback(self._on_state_changed)

        # --- Receiver delta rate tracking ---
        self._delta_timestamps: collections.deque[float] = collections.deque()
        self._deltas_received: int = 0
        self._last_ndx: float = 0.0
        self._last_ndy: float = 0.0
        self._last_seq: int = 0
        self._last_sample_log_time: float = 0.0

        # --- Browser agent reference (may be None if not started) ---
        self._browser_agent = browser_agent

        # --- Widgets ---
        self._conn_panel = _ConnectionPanel()
        self._mirror_panel = _MirrorPanel()
        self._arr_panel = _ScreenArrangementPanel()
        self._browser_panel = _BrowserPanel()
        self._status_bar = _StatusBar()
        self._canvas = _Canvas()
        self._log_panel = _LogPanel()

        # _arr_panel starts hidden; becomes visible on CONNECTED.
        self._arr_panel.hide()

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

        # DEBUG: cursor-freeze toggle + diagnostic position poller. Remove
        # with the button. The poller logs to the UI panel only when the
        # cursor moves, so Master can see at a glance whether the freeze
        # is actually holding without scrolling through EdgeDetector ticks.
        self._debug_capture: MouseCapture | None = None
        self._debug_freeze_last_pos: tuple[int, int] | None = None
        self._debug_freeze_check_timer = QTimer(self)
        self._debug_freeze_check_timer.setInterval(500)  # 2 Hz
        self._debug_freeze_check_timer.timeout.connect(self._debug_freeze_tick)
        self._mirror_panel.debug_pause_btn.toggled.connect(self._on_debug_pause_toggled)

        # Safety: auto-release DEBUG cursor-freeze if Slickshift loses
        # foreground focus. Without this, alt-tabbing away leaves the
        # CGEventTap still dropping events globally, so the cursor reappears
        # in the new app but motion remains suppressed -- effectively
        # locking the whole machine until the user finds Slickshift again
        # to un-toggle. Releasing on focus loss gives alt-tab as a natural
        # escape route.
        QApplication.instance().applicationStateChanged.connect(
            self._on_application_state_changed
        )

        # In-window toggle hotkey: Shift+Esc toggles the debug freeze
        # regardless of which control currently has Qt focus. This is the
        # safe alternative to the pynput Ctrl+Alt+Shift+Esc combo (which
        # collides with a destructive macOS session shortcut on at least
        # one MacBook configuration) and to Tab+Space (which relies on Qt
        # focus landing on the button -- not always reliable on macOS).
        # Works only while the Slickshift window has focus, so it can never
        # interfere with system-level shortcuts.
        self._debug_toggle_shortcut = QShortcut(QKeySequence("Shift+Esc"), self)
        self._debug_toggle_shortcut.activated.connect(
            self._mirror_panel.debug_pause_btn.toggle
        )

        # Wire Inject checkbox.
        self._mirror_panel.inject_chk.stateChanged.connect(self._on_inject_toggled)

        # Wire Forward Keyboard checkbox.
        self._mirror_panel.kbd_fwd_chk.stateChanged.connect(self._on_kbd_fwd_toggled)

        # Wire peer-edge dropdown. When the user changes it we update EdgeDetector
        # immediately so edge detection uses the new edge on the next tick.
        self._mirror_panel.peer_edge_combo.currentIndexChanged.connect(
            self._on_peer_edge_changed
        )

        # Wire arrangement panel. When the user commits, feed EdgeDetector and
        # refresh the Inject checkbox gate.
        self._arr_panel.arrangement_changed.connect(self._on_arrangement_committed)
        # Wire unlock toggle. Persist preference to QSettings when it changes.
        self._arr_panel.monitors_unlock_changed.connect(self._on_monitors_unlock_changed)

        self._mirror_panel.scroll_slider.valueChanged.connect(
            self._event_capture.set_scroll_multiplier
        )

        # EdgeDetector callback is already registered above on _edge_detector.
        # (In the test app this was: self._capture.set_edge_dwell_callback(...))
        # Now: self._edge_detector.register_callback(self._schedule_handoff_fire)
        # is called at construction time above.

        # Wire browser panel. The signal crosses from the worker thread to the
        # Qt main thread so the log panel widget is only touched from the main thread.
        self._browser_log.connect(self._log_panel.append_line)
        self._browser_panel.list_tabs_btn.clicked.connect(self._on_list_tabs_clicked)
        self._browser_panel.send_btn.clicked.connect(self._on_browser_send_clicked)

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
        root_layout.addWidget(self._arr_panel)
        root_layout.addWidget(self._browser_panel)
        root_layout.addWidget(self._status_bar)
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

        # --- Cursor polling timer ---
        # Drives the local red-square canvas update, status bar coordinate display,
        # and EdgeDetector tick on every interval.
        pyautogui.FAILSAFE = config.PYAUTOGUI_FAILSAFE

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(config.CANVAS_REFRESH_MS)
        self._poll_timer.timeout.connect(self._poll_cursor)
        self._poll_timer.start()

        # Dead-man monitor: independent of the heartbeat sender thread.
        # Ticks every 500 ms on the Qt main thread.
        self._dead_man_timer = QTimer(self)
        self._dead_man_timer.setInterval(500)
        self._dead_man_timer.timeout.connect(self._check_dead_man)

        # Wire force-release hotkey signal.
        self._main_signals.force_release_pressed.connect(self._on_system_force_release)

        # Wire keyboard forwarding signal.
        self._main_signals.key_event_fired.connect(self._on_key_event_fired)

        # Wire browser hotkey signal (B1).
        self._main_signals.browser_hotkey_pressed.connect(self._on_browser_hotkey)

        # Wire clipboard-push hotkey signal (B3).
        self._main_signals.clipboard_push_pressed.connect(self._on_clipboard_push)

        # Start the pynput mouse listener. Runs for the full window lifetime.
        self._event_capture.start()

        # Start the system-wide keyboard listener for Ctrl+Alt+Shift+Esc.
        self._start_kb_listener()

        logger.info(
            "MainWindow initialized. Screen (pyautogui): %dx%d  dpi_scale=%.2f. Polling at %dms.",
            self._screen_w, self._screen_h, self._dpi_scale, config.CANVAS_REFRESH_MS,
        )

        # --- QSettings: restore last peer address on startup ---
        _last_addr: str = self._settings.value("last_peer_address", "", type=str)
        if _last_addr:
            self._conn_panel._addr_field.setText(_last_addr)
            logger.info("Restored last peer address from settings: %s", _last_addr)

        # --- QSettings: restore keyboard forwarding checkbox preference ---
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

        _scroll_mult_saved: int = self._settings.value(
            "scroll_multiplier",
            config.SCROLL_MULTIPLIER_DEFAULT,
            type=int,
        )
        _scroll_mult_clamped = max(
            config.SCROLL_MULTIPLIER_MIN,
            min(config.SCROLL_MULTIPLIER_MAX, _scroll_mult_saved),
        )
        self._mirror_panel.scroll_slider.setValue(_scroll_mult_clamped)
        self._event_capture.set_scroll_multiplier(_scroll_mult_clamped)
        logger.info("Restored scroll multiplier: %dx", _scroll_mult_clamped)

        # --- QSettings: monitors_unlocked preference is preserved on disk but
        # the feature is temporarily disabled (see _ScreenArrangementPanel.__init__).
        # Force runtime state to locked regardless of saved value.
        self._arr_panel.set_monitors_unlocked(False)

    # ------------------------------------------------------------------
    # Helper: default edge from the mirror panel dropdown at construction time
    # ------------------------------------------------------------------

    def _mirror_panel_default_edge(self) -> str:
        """
        Return the default peer edge before the mirror panel widget is created.

        The mirror panel defaults to index 0 ("right"). This helper encodes that
        default so EdgeDetector is seeded consistently with the dropdown.
        """
        return "right"

    # ------------------------------------------------------------------
    # Helper: peer virtual-desktop canvas dimensions for injection math
    # ------------------------------------------------------------------

    def _peer_vd_dims(self) -> tuple[int, int]:
        """
        Return (peer_vd_w, peer_vd_h) -- the width and height of the peer's
        virtual-desktop bounding box. Used to denormalize incoming deltas back
        to pixel units so the injected movement matches what the sender measured.

        When the peer sent a "monitors" list (Task #5 hello payload), the full
        bounding box is computed from that list via compute_virtual_desktop_bbox().

        When _peer_monitors is None (old peer or not yet connected), falls back
        to the scalar screen_logical values from the hello payload, and then to
        the local screen dimensions as a last resort. This preserves the
        single-monitor behavior that existed before Task #6.
        """
        if self._peer_monitors is not None:
            _, _, vd_w, vd_h = compute_virtual_desktop_bbox(self._peer_monitors)
            return (vd_w, vd_h)

        # Legacy fallback: read scalar fields from the hello payload.
        if self._peer_info is not None:
            lw, lh = self._peer_info.get("screen_logical", [0, 0])
            if lw > 0 and lh > 0:
                return (int(lw), int(lh))

        # Last resort: local screen dimensions (single-machine testing).
        return (self._screen_w, self._screen_h)

    # ------------------------------------------------------------------
    # Per-peer arrangement persistence helpers
    # ------------------------------------------------------------------

    def _arrangement_settings_key(self) -> str | None:
        """
        Return the QSettings key for the current peer's arrangement, or None
        if no peer identity is known yet.

        Format: "arrangement/<peer-identity>" where peer-identity is the peer's
        machine_name (from hello payload), falling back to the address text.
        The key is sanitized to avoid path separators in the settings key.
        """
        if not self._peer_identity:
            return None
        safe_id = self._peer_identity.replace("/", "_").replace("\\", "_")
        return f"arrangement/{safe_id}"

    def _save_arrangement_for_peer(self, arrangement: dict[int, set[str]]) -> None:
        """
        Persist the given arrangement to QSettings keyed by the current peer identity.

        Skips silently when no peer identity is available (e.g. not yet
        connected or hello not yet received).
        """
        key = self._arrangement_settings_key()
        if key is None:
            return
        import json as _json
        wire = arrangement_to_wire(arrangement)
        self._settings.setValue(key, _json.dumps(wire))
        logger.debug("Saved arrangement for peer %r: %s", self._peer_identity, wire)

    def _load_arrangement_for_peer(self) -> dict[int, set[str]] | None:
        """
        Load a previously saved arrangement for the current peer from QSettings.

        Returns the arrangement dict if a saved value exists, or None if no
        saved arrangement is found for this peer identity.
        """
        key = self._arrangement_settings_key()
        if key is None:
            return None
        raw: str = self._settings.value(key, "", type=str)
        if not raw:
            return None
        import json as _json
        try:
            wire = _json.loads(raw)
            arrangement = wire_to_arrangement(wire)
            logger.debug(
                "Loaded arrangement for peer %r: %s", self._peer_identity, arrangement
            )
            return arrangement
        except Exception as exc:
            logger.warning(
                "Failed to load saved arrangement for peer %r: %s -- ignoring",
                self._peer_identity,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Cursor polling (local red square, status bar, and EdgeDetector tick)
    # ------------------------------------------------------------------

    def _poll_cursor(self) -> None:
        x, y = pyautogui.position()
        # Normalize against the virtual-desktop bounding box so the red square
        # spans the full multi-monitor canvas (0.0-1.0 across all monitors).
        # On a single-monitor setup vd_x==0, vd_y==0, vd_w==screen_w, vd_h==screen_h,
        # so this is identical to the previous screen_w/screen_h normalization.
        nx = (x - self._local_vd_x) / self._local_vd_w
        ny = (y - self._local_vd_y) / self._local_vd_h
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

        # Feed the EdgeDetector on every tick.
        # _capture_paused is True during TRANSITIONING so the dwell timer does not
        # advance while awaiting handoff ack.
        self._edge_detector.tick(x, y, paused=self._capture_paused)

    def _role_label_for_state(self, state: SwitchState) -> str:
        dpi_str = f"{self._dpi_scale:.2f}"
        if state == SwitchState.CAPTURING:
            cursor_side = "local" if self._cursor_on_local else "peer"
            return f"Controller (cursor on {cursor_side}) | DPI: {dpi_str}"
        if state == SwitchState.TRANSITIONING:
            return "Crossing edge..."
        if state == SwitchState.RECONNECTING:
            attempt = self._reconnect_attempt
            return f"Reconnecting ({attempt}/{config.RECONNECT_MAX_ATTEMPTS})..."
        if state == SwitchState.RECEIVING and self._injection_enabled:
            cursor_side = "local" if self._cursor_on_local else "controller"
            return f"Controlled (cursor on {cursor_side}) | DPI: {dpi_str}"
        if state == SwitchState.RECEIVING:
            return "Controlled"
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
        self._peer_monitors = None
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
        self._settings.setValue("last_peer_address", self._conn_panel.address_text())
        self._user_initiated_disconnect = False
        self._peer_info = None
        self._peer_monitors = None
        self._state_ctrl.begin_connecting()
        self._conn_panel.on_state_changed(SwitchState.CONNECTING)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTING)
        self._append_log(f"Connecting to {host}:{port}")
        self._transport.connect_to_peer(host, port)

    def _on_disconnect_clicked(self) -> None:
        self._settings.setValue("scroll_multiplier", self._mirror_panel.scroll_slider.value())
        self._user_initiated_disconnect = True
        self._reconnect_timer.stop()
        self._idle_timer.stop()
        self._dead_man_timer.stop()
        self._handoff_ack_timer.stop()
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._canvas.set_local_dimmed(False)
        self._canvas.hide_remote()
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
        self._reset_session_role_flags()
        self._peer_info = None
        self._peer_monitors = None
        self._peer_identity = ""
        self._conn_panel.on_state_changed(SwitchState.IDLE)
        self._mirror_panel.on_state_changed(SwitchState.IDLE)
        self._append_log("Disconnected by user")

    # ------------------------------------------------------------------
    # Mirroring button handlers
    # ------------------------------------------------------------------

    def _on_start_mirroring_clicked(self) -> None:
        """
        This machine becomes the session master. The cursor starts on the master's
        own screen; the user crosses it to the slave by walking into the peer
        edge. The slave only sees injected motion once the cursor has crossed.

        Master always runs MouseCapture for the full session. While the cursor
        is on the master (cursor_on_local=True) MouseCapture is paused so no
        deltas leave the box and the local cursor remains visible. When the
        cursor crosses to the slave the handoff handlers flip the paused and
        exclusive flags so deltas start flowing and the local cursor hides.
        """
        edge = self._mirror_panel.selected_edge()
        self._edge_detector.set_peer_edge(edge)
        self._state_ctrl.start_mirroring()
        self._is_master = True
        self._cursor_on_local = True
        self._transport.send({"type": "mirror_start"})
        self._capture.start(self._transport.enqueue_message)
        # Cursor starts on master: visible, no forwarding. set_paused stops
        # delta emission; set_exclusive(False) keeps the cursor visible and
        # lets motion through to the OS so the user can drive their local box.
        self._capture.set_paused(True)
        self._capture.set_exclusive(False)
        self._capture_paused = False  # edge detector active on master's cursor
        self._event_capture.set_active(True)
        # Re-arm keyboard forwarding from the checkbox. _on_kbd_fwd_toggled
        # only flips the active flag while in CAPTURING, so a box checked
        # before Start Mirroring (including a value restored from QSettings)
        # would otherwise show checked without forwarding actually being on.
        self._keyboard_forwarding_active = self._mirror_panel.kbd_fwd_chk.isChecked()
        self._append_log(
            f"[KBD diag] Start Mirroring: forwarding_active={self._keyboard_forwarding_active} "
            f"(checkbox isChecked={self._mirror_panel.kbd_fwd_chk.isChecked()})"
        )
        if self._keyboard_forwarding_active:
            logger.info("Keyboard forwarding re-armed from checkbox on Start Mirroring")
        self._conn_panel.on_state_changed(SwitchState.CAPTURING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CAPTURING)
        self._append_log(
            f"Mirroring started -- this machine is the controller (peer to the {edge}). "
            "Move the cursor to that edge to cross to the peer screen."
        )

    def _on_monitors_unlock_changed(self, unlocked: bool) -> None:
        """
        Called when the "Unlock Same PC Monitors" toggle changes in the arrangement panel.
        Persists the new preference to QSettings immediately.
        """
        self._settings.setValue("monitors_unlocked", unlocked)
        logger.info("monitors_unlocked preference saved: %s", unlocked)

    def _on_peer_edge_changed(self, index: int) -> None:  # noqa: ARG002
        """
        Peer-edge dropdown selection changed. Update EdgeDetector and sync the panel.

        The dropdown is disabled during CAPTURING/RECEIVING/TRANSITIONING so this
        handler only fires when the state is CONNECTED or IDLE.

        Calls apply_dropdown_edge() which snaps the panel to the cardinal position
        and auto-commits the arrangement, keeping the two controls synchronized.
        """
        edge = self._mirror_panel.selected_edge()
        self._edge_detector.set_peer_edge(edge)
        # Sync the arrangement panel. The panel's apply_dropdown_edge() internally
        # calls _do_commit() which emits arrangement_changed, which in turn calls
        # _on_arrangement_committed() and calls set_exit_edges(). set_exit_edges()
        # builds a {0: {edge}} arrangement matching set_peer_edge(), so the two
        # methods stay consistent.
        self._arr_panel.apply_dropdown_edge(edge)
        logger.info("Peer edge updated to: %s", edge)

    def _on_arrangement_committed(self, arrangement: dict) -> None:
        """
        Called when the arrangement panel emits a committed arrangement.

        Feeds the arrangement to EdgeDetector.set_exit_edges(). Syncs the
        peer-edge dropdown to the dominant edge derived from the arrangement.
        Re-evaluates whether the Inject checkbox should be enabled (Inject
        requires a committed arrangement).

        Sends an arrangement_update message to the peer so the peer can apply
        the geometric inverse automatically.  The send is wrapped in try/except
        because the peer may be mid-handoff or disconnecting; a failed send
        must not prevent the local commit from completing.
        """
        logger.info("Arrangement committed: %s", arrangement)
        self._edge_detector.set_exit_edges(arrangement)
        self._save_arrangement_for_peer(arrangement)
        self._append_log(f"Screen arrangement committed: {arrangement}")

        # Sync the dropdown to the dominant edge. Collect all edge strings from
        # the arrangement and pick the first one matching a dropdown option.
        # Priority: right > left > top > bottom (arbitrary but consistent).
        all_edges: set[str] = set()
        for edge_set in arrangement.values():
            all_edges.update(edge_set)
        _dropdown_priority = ["right", "left", "top", "bottom"]
        dominant_edge = next(
            (e for e in _dropdown_priority if e in all_edges),
            None,
        )
        if dominant_edge is not None:
            self._mirror_panel.set_dropdown_edge(dominant_edge)

        # Send the arrangement to the peer so the peer applies the inverse.
        # Only send when connected (transport is live); skip in IDLE/LISTENING/
        # CONNECTING/HANDSHAKING when no peer socket exists yet.
        _sendable_states = frozenset({
            SwitchState.CONNECTED,
            SwitchState.CAPTURING,
            SwitchState.RECEIVING,
            SwitchState.TRANSITIONING,
            SwitchState.RECONNECTING,
        })
        if self._state_ctrl.state in _sendable_states:
            wire = arrangement_to_wire(arrangement)
            try:
                self._transport.send({"type": "arrangement_update", "arrangement": wire})
                logger.info("Sent arrangement_update to peer: %s", wire)
            except Exception as exc:
                logger.warning(
                    "arrangement_update send failed (peer may be disconnecting): %s", exc
                )

        # Re-evaluate Inject gate: if we are in RECEIVING and the arrangement
        # is now committed, enable Inject.
        if self._state_ctrl.state == SwitchState.RECEIVING:
            self._mirror_panel.on_state_changed(
                SwitchState.RECEIVING,
                arrangement_committed=self._arr_panel.arrangement_committed(),
            )

    def _on_stop_mirroring_clicked(self) -> None:
        """Stop capturing and notify peer to exit RECEIVING."""
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._state_ctrl.stop_mirroring()
        self._reset_session_role_flags()
        self._transport.send({"type": "mirror_stop"})
        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log("Mirroring stopped")

    def _on_debug_pause_toggled(self, paused: bool) -> None:
        """
        DEBUG: Phase 2 Step A cursor-freeze validation.

        Toggle on: spin up a self-contained MacMouseCapture (independent of
        the state machine, no peer needed), start it, then pause it. The
        cursor should disappear and freeze in place even as the physical
        mouse keeps moving.

        Toggle off: unpause, stop, and tear down the debug capture. Cursor
        reappears and tracks normally again.

        Runs entirely on the GUI thread because Qt button signals are
        delivered on that thread. PyQt6 sets up NSApplication on import, so
        CGDisplayHideCursor takes effect (unlike from a bare CLI script).

        REMOVE this method, the button in _MirrorPanel, and the toggled
        signal wiring once Phase 2 Step 5 (full handoff validation) lands.
        """
        if paused:
            if self._debug_capture is None:
                self._debug_capture = make_mouse_capture(self._local_monitors)
                self._debug_capture.start(lambda _delta: True)
            self._debug_capture.set_exclusive(True)
            self._debug_freeze_last_pos = None
            self._debug_freeze_check_timer.start()
            self._append_log("DEBUG: cursor frozen and hidden (toggle off to restore)")
            logger.info("DEBUG cursor-freeze ENGAGED")
        else:
            self._debug_freeze_check_timer.stop()
            if self._debug_capture is not None:
                self._debug_capture.set_exclusive(False)
                self._debug_capture.stop()
                self._debug_capture = None
            final_x, final_y = pyautogui.position()
            self._append_log(f"DEBUG: cursor restored (final pos=({final_x},{final_y}))")
            logger.info("DEBUG cursor-freeze RELEASED")

    def _debug_freeze_tick(self) -> None:
        """
        DEBUG: 2 Hz poll of the cursor position while freeze is engaged.

        Logs to the UI panel only on the first tick (to confirm the poller
        is running) and again whenever the cursor position changes from the
        previous tick. If the cursor stays still, the panel stays quiet
        after the initial line -- that's the freeze working. If lines like
        "MOTION DETECTED" start appearing while you move the mouse, the
        freeze actually broke.
        """
        x, y = pyautogui.position()
        if self._debug_freeze_last_pos is None:
            self._append_log(f"DEBUG freeze polling: initial pos=({x},{y})")
        elif (x, y) != self._debug_freeze_last_pos:
            prev_x, prev_y = self._debug_freeze_last_pos
            self._append_log(
                f"DEBUG freeze MOTION DETECTED: ({prev_x},{prev_y}) -> ({x},{y})"
            )
        self._debug_freeze_last_pos = (x, y)

    def _on_application_state_changed(self, state: Qt.ApplicationState) -> None:
        """
        Auto-release DEBUG cursor-freeze when the app loses foreground.

        Triggered by QApplication.applicationStateChanged. If the user alt-tabs
        away while frozen, the CGEventTap would otherwise keep dropping motion
        events globally even though the cursor reappears in the new app -- the
        whole machine would feel locked. Auto-releasing on focus loss gives
        alt-tab as a natural escape route.

        Only the DEBUG freeze is released here. Real CAPTURING is not affected
        because losing focus during a live mirror session is a normal user
        action that should not tear down the connection.
        """
        if state == Qt.ApplicationState.ApplicationActive:
            return
        if self._debug_capture is not None:
            logger.info(
                "App lost foreground (state=%s) -- auto-releasing DEBUG cursor-freeze",
                state,
            )
            self._mirror_panel.debug_pause_btn.setChecked(False)

    def _stop_capture_if_running(self) -> None:
        """
        Stop MouseCapture if it is currently polling.

        Also covers TRANSITIONING: the capture thread is still alive during a
        cursor-cross-in-flight. If the connection dies mid-cross, we must stop
        it cleanly to avoid leaving a zombie thread.

        Master is the only side that runs MouseCapture, so this is effectively
        a master-side cleanup. The role-state guard happens to match because
        the master is in CAPTURING (or briefly TRANSITIONING) for the session.
        """
        state = self._state_ctrl.state
        if state in (SwitchState.CAPTURING, SwitchState.TRANSITIONING):
            self._capture.set_exclusive(False)  # release exclusive so cursor returns
            self._capture.set_paused(False)  # unpause before stop so the thread exits cleanly
            self._capture_paused = False
            self._capture.stop()

    def _reset_session_role_flags(self) -> None:
        """
        Clear the master/cursor-side flags. Call this on any path that ends
        the mirroring session (disconnect, force-release, stop mirroring).
        """
        self._is_master = None
        self._cursor_on_local = True

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

        If we were mid-handoff (TRANSITIONING), cancel the ack timer and restore
        the canvas before transitioning to IDLE. No stuck state remains.

        If the drop was NOT user-initiated and we were in an active mirroring role
        (CAPTURING, RECEIVING, TRANSITIONING), enter the auto-reconnect path
        instead of going directly to IDLE.
        """
        self._settings.setValue("scroll_multiplier", self._mirror_panel.scroll_slider.value())
        self._dead_man_timer.stop()
        self._handoff_ack_timer.stop()
        self._idle_timer.stop()
        self._event_capture.set_active(False)
        self._stop_capture_if_running()
        self._canvas.set_local_dimmed(False)
        self._canvas.hide_remote()

        prev_state = self._state_ctrl.state

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
            if prev_state == SwitchState.TRANSITIONING:
                self._reconnect_prev_role = SwitchState.CAPTURING
            else:
                self._reconnect_prev_role = prev_state

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
                should_reconnect = False

        if should_reconnect:
            self._reconnect_attempt = 0
            self._reconnect_delay_s = config.RECONNECT_INITIAL_DELAY_S
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
            self._reset_session_role_flags()
            self._peer_info = None
            self._peer_monitors = None
            self._peer_identity = ""
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
        elif msg_type == "arrangement_update":
            self._main_signals.arrangement_update_received.emit(
                message.get("arrangement", {})
            )
        elif msg_type == "force_release":
            logger.info("Peer sent force_release -- returning to CONNECTED")
            self._handle_peer_force_release()
        elif msg_type == "user_disconnect":
            logger.info("Peer issued user disconnect -- closing cleanly without reconnect")
            self._user_initiated_disconnect = True
        elif msg_type == "idle_timeout_release":
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

        If _reconnect_restoring_role is set, this is a successful reconnect.
        Restore the prior role after transitioning to CONNECTED.

        Task #5 (monitors field): if the peer sent a "monitors" list, deserialize
        it into self._peer_monitors. If absent (old peer), store None and log a
        debug note so future tasks can detect the backward-compat path.
        """
        self._peer_info = payload
        name = payload.get("machine_name", "unknown")
        plat = payload.get("platform", "?")
        lw, lh = payload.get("screen_logical", [0, 0])
        pw, ph = payload.get("screen_physical", [0, 0])
        dpi = payload.get("dpi_scale", 1.0)

        # Establish peer identity for QSettings persistence.
        # Prefer machine_name (stable hostname) from the hello payload.
        # Fall back to whatever the user typed in the address field.
        machine_name: str = payload.get("machine_name", "")
        self._peer_identity = machine_name if machine_name else self._conn_panel.address_text()

        # Parse multi-monitor topology if present (Task #5).
        raw_monitors: list[dict] | None = payload.get("monitors")
        if raw_monitors is not None:
            self._peer_monitors = [MonitorInfo.from_dict(m) for m in raw_monitors]
            logger.debug(
                "Peer sent monitor topology: %d monitor(s)", len(self._peer_monitors)
            )
        else:
            self._peer_monitors = None
            logger.debug(
                "Peer hello has no 'monitors' field (old peer or pre-Task-5 build); "
                "falling back to scalar screen_logical/screen_physical fields"
            )

        restoring = self._reconnect_restoring_role
        self._reconnect_restoring_role = False

        if restoring:
            self._state_ctrl.reconnect_succeeded()
        else:
            self._state_ctrl.handshake_complete()

        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=payload)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log(
            f"Received hello from peer: {name} ({plat})"
        )
        self._append_log(
            f"Peer screen: logical {lw}x{lh}, physical {pw}x{ph}, dpi={dpi}"
        )
        if self._peer_monitors is not None:
            self._append_log(
                f"Peer monitor count: {len(self._peer_monitors)}"
            )

        # Feed monitor topology into the arrangement panel.
        # On reconnect we do not reset the panel -- the prior arrangement stays
        # committed so the user does not have to re-configure after a drop.
        if not restoring:
            self._arr_panel.set_monitors(self._local_monitors, self._peer_monitors)
            # Seed the panel from the current dropdown value so the default
            # single-monitor path works without the user touching the panel.
            self._arr_panel.apply_dropdown_edge(self._mirror_panel.selected_edge())
            logger.info(
                "Arrangement panel populated: %d local, %d peer monitor(s)",
                len(self._local_monitors),
                len(self._peer_monitors) if self._peer_monitors else 0,
            )

            # Restore a persisted arrangement for this peer if one exists.
            # A restored arrangement counts as committed (no re-commit required).
            # We apply it WITHOUT sending an arrangement_update -- it is our local
            # view, not a new commit pushed to the peer.
            saved_arrangement = self._load_arrangement_for_peer()
            if saved_arrangement:
                self._edge_detector.set_exit_edges(saved_arrangement)
                self._arr_panel.apply_remote_arrangement(saved_arrangement)
                # Sync dropdown.
                all_saved_edges: set[str] = set()
                for edge_set in saved_arrangement.values():
                    all_saved_edges.update(edge_set)
                _dropdown_priority = ["right", "left", "top", "bottom"]
                dominant_saved = next(
                    (e for e in _dropdown_priority if e in all_saved_edges),
                    None,
                )
                if dominant_saved is not None:
                    self._mirror_panel.set_dropdown_edge(dominant_saved)
                # Hide the peer banner immediately -- this is a local restore,
                # not a remote push.
                self._arr_panel._peer_banner.hide()
                self._arr_panel._peer_banner_timer.stop()
                self._append_log(
                    f"Restored saved arrangement for peer {self._peer_identity!r}: "
                    f"{saved_arrangement}"
                )
                logger.info(
                    "Restored saved arrangement for peer %r: %s",
                    self._peer_identity,
                    saved_arrangement,
                )
            else:
                logger.debug(
                    "No saved arrangement for peer %r -- using dropdown default",
                    self._peer_identity,
                )

        if restoring:
            self._append_log(
                f"Reconnect succeeded. Restoring role: {self._reconnect_prev_role.value.upper()}"
            )
            logger.info(
                "Reconnect succeeded -- restoring role %s",
                self._reconnect_prev_role.value.upper(),
            )
            if self._reconnect_prev_role == SwitchState.CAPTURING:
                self._transport.send({"type": "mirror_start"})
                edge = self._mirror_panel.selected_edge()
                self._edge_detector.set_peer_edge(edge)
                self._state_ctrl.start_mirroring()
                self._is_master = True
                self._cursor_on_local = True
                self._capture.start(self._transport.enqueue_message)
                # Restore as cursor-on-master: visible, no forwarding. The user
                # crosses the edge again to re-engage exclusive mode.
                self._capture.set_paused(True)
                self._capture.set_exclusive(False)
                self._capture_paused = False
                self._event_capture.set_active(True)
                self._conn_panel.on_state_changed(SwitchState.CAPTURING, peer_info=self._peer_info)
                self._mirror_panel.on_state_changed(SwitchState.CAPTURING)
                self._append_log("Controller role restored after reconnect")
            else:
                self._append_log(
                    "Role was RECEIVING -- waiting for peer to re-send mirror_start"
                )
        else:
            self._append_log("Connection established")

    def _handle_mirror_start(self) -> None:
        """
        Peer has become the session master. This machine is the slave for the
        full session: it never sources input and never enters CAPTURING. The
        slave's edge detector stays gated off until the cursor logically arrives
        on this machine via a handoff_request.
        """
        self._state_ctrl.mirror_start_received()
        self._is_master = False
        self._cursor_on_local = False
        # Edge detector gated off until cursor crosses to us. _poll_cursor reads
        # this flag to suppress dwell advancement.
        self._capture_paused = True
        self._deltas_received = 0
        self._delta_timestamps.clear()
        self._last_sample_log_time = time.monotonic()
        self._last_delta_received_time = time.monotonic()
        self._canvas.reset_remote_position()
        self._conn_panel.on_state_changed(SwitchState.RECEIVING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(
            SwitchState.RECEIVING,
            arrangement_committed=self._arr_panel.arrangement_committed(),
        )
        self._idle_timer.start()
        self._append_log("Peer is mirroring -- this machine is the controlled side")

    def _handle_mirror_stop(self) -> None:
        """Peer stopped sending deltas. Return to CONNECTED state."""
        self._idle_timer.stop()
        self._state_ctrl.mirror_stop_received()
        self._reset_session_role_flags()
        self._canvas.hide_remote()
        self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
        self._append_log(
            f"Peer stopped mirroring. Total deltas received: {self._deltas_received}"
        )

    def _handle_arrangement_update(self, wire_arrangement: dict) -> None:
        """
        Process an arrangement_update message from the peer (Qt main thread).

        The peer has committed an arrangement on its side.  Steps:
        1. Deserialize the wire format.
        2. Compute the geometric inverse for this machine.
        3. Apply to EdgeDetector.
        4. Update the arrangement panel visually.
        5. Re-evaluate the Inject gate (a remote arrangement counts as committed).

        The signal carries the raw wire dict from _on_message_received so the
        JSON boundary conversion happens here rather than in the transport thread.
        """
        if not wire_arrangement:
            logger.warning("Received empty arrangement_update -- ignored")
            return

        peer_arrangement = wire_to_arrangement(wire_arrangement)
        peer_monitor_count = len(self._peer_monitors) if self._peer_monitors else 1
        local_arrangement = invert_arrangement(peer_arrangement, peer_monitor_count)

        self._edge_detector.set_exit_edges(local_arrangement)
        self._arr_panel.apply_remote_arrangement(local_arrangement)
        self._save_arrangement_for_peer(local_arrangement)

        # Sync the dropdown to the dominant edge of the inverted arrangement.
        all_edges: set[str] = set()
        for edge_set in local_arrangement.values():
            all_edges.update(edge_set)
        _dropdown_priority = ["right", "left", "top", "bottom"]
        dominant_edge = next(
            (e for e in _dropdown_priority if e in all_edges),
            None,
        )
        if dominant_edge is not None:
            self._mirror_panel.set_dropdown_edge(dominant_edge)

        self._append_log(
            f"Arrangement set by peer: applied inverse {local_arrangement}"
        )
        logger.info(
            "Applied arrangement_update from peer: arrangement=%s, inverted to=%s",
            peer_arrangement,
            local_arrangement,
        )

        # Re-evaluate Inject gate: remote arrangement counts as committed.
        if self._state_ctrl.state == SwitchState.RECEIVING:
            self._mirror_panel.on_state_changed(
                SwitchState.RECEIVING,
                arrangement_committed=self._arr_panel.arrangement_committed(),
            )

    def _handle_delta(self, message: dict) -> None:
        """
        Process an incoming cursor delta from the sender.

        Always updates the canvas green square. When in RECEIVING state and
        injection is enabled, also moves the OS cursor via MouseInjector.
        The receiver uses its own screen dimensions for pixel translation.
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

        self._canvas.apply_remote_delta(ndx, ndy)

        if (
            self._is_master is False
            and self._cursor_on_local
            and self._injection_enabled
        ):
            peer_vd_w, peer_vd_h = self._peer_vd_dims()
            self._injector.move_relative(
                ndx, ndy,
                peer_vd_w, peer_vd_h,
            )

        now = time.monotonic()
        if now - self._last_sample_log_time >= config.DELTA_SAMPLE_LOG_INTERVAL_S:
            self._last_sample_log_time = now
            rate = self._compute_delta_rate()
            self._append_log(
                f"Last delta: ndx={ndx:.4f} ndy={ndy:.4f} seq={seq}  "
                f"({self._deltas_received} total, {rate:.1f} Hz)"
            )

    # ------------------------------------------------------------------
    # Edge-based handoff handlers
    # ------------------------------------------------------------------

    def _schedule_handoff_fire(self, sender_edge: str, perp: float) -> None:
        """
        Called from the EdgeDetector via the poll timer thread (Qt main thread)
        when edge dwell completes.

        The EdgeDetector callback fires synchronously from tick(), which is called
        from _poll_cursor on the Qt main thread. The signal emit is therefore also
        on the main thread, but using the signal keeps the call path consistent
        with the original design and makes it easy to move tick() off-thread later.
        """
        logger.info(
            "Edge dwell callback received: edge=%s perp=%.3f",
            sender_edge,
            perp,
        )
        self._main_signals.edge_dwell_fired.emit(sender_edge, perp)

    def _on_edge_dwell_fired(self, sender_edge: str, perp: float) -> None:
        """
        Main thread: edge dwell threshold met on this machine. Initiates a
        cursor-side cross, not a role swap.

        Two valid callers:
          - Master while the cursor is on the master's screen (CAPTURING +
            _cursor_on_local). Cursor crossing TO the slave.
          - Slave while the cursor is on the slave's screen (RECEIVING +
            _cursor_on_local). Cursor crossing BACK to the master.

        Either way the protocol is identical: send a handoff_request, transition
        TRANSITIONING, wait for ack. The handoff_ack_received handler flips
        _cursor_on_local and adjusts capture flags.
        """
        if not self._cursor_on_local:
            logger.debug("Edge dwell fired while cursor is on peer -- discarded")
            return

        state = self._state_ctrl.state
        if state == SwitchState.CAPTURING and self._is_master:
            self._state_ctrl.master_begin_handoff()
        elif state == SwitchState.RECEIVING and self._is_master is False:
            self._state_ctrl.slave_begin_handoff()
        else:
            logger.debug(
                "Edge dwell fired in unexpected state=%s is_master=%s -- discarded",
                state.value, self._is_master,
            )
            return

        logger.info("Cursor cross initiated: edge=%s perp=%.3f", sender_edge, perp)
        self._append_log(
            f"Cursor crossing peer edge ({sender_edge}, perp={perp:.3f})"
        )

        # Silence the master's tap during ack-pending so no stray deltas leak.
        # Slave has no MouseCapture running so this is a no-op on the slave.
        if self._is_master:
            self._capture.set_paused(True)
        self._capture_paused = True

        self._transport.send({
            "type": "handoff_request",
            "perp": perp,
            "sender_edge": sender_edge,
        })

        self._conn_panel.on_state_changed(SwitchState.TRANSITIONING, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(SwitchState.TRANSITIONING)

        self._handoff_ack_timer.start()

    def _on_handoff_ack_timeout(self) -> None:
        """
        Handoff ACK did not arrive within HANDOFF_ACK_TIMEOUT_S. Roll back the
        state machine (TRANSITIONING -> CAPTURING or RECEIVING per pre-transition
        state) without flipping _cursor_on_local -- the cursor stayed put.
        """
        if self._state_ctrl.state != SwitchState.TRANSITIONING:
            return
        logger.warning(
            "Handoff ACK timeout (%.1fs) -- rolling back",
            config.HANDOFF_ACK_TIMEOUT_S,
        )
        self._append_log(
            f"Handoff ACK timeout after {config.HANDOFF_ACK_TIMEOUT_S:.1f}s -- cursor stays here"
        )
        self._state_ctrl.handoff_ack_timeout()
        rolled_back_to = self._state_ctrl.state

        # Master: unpause so the user can continue driving the local cursor.
        # Slave: no MouseCapture to unpause; just reset the edge-detector gate.
        if self._is_master:
            self._capture.set_paused(False)
        self._capture_paused = False  # edge detector active again (cursor still here)

        self._conn_panel.on_state_changed(rolled_back_to, peer_info=self._peer_info)
        self._mirror_panel.on_state_changed(rolled_back_to)

    def _handle_handoff_request(self, payload: dict) -> None:
        """
        Peer says: "I'm sending the cursor to you." Flip _cursor_on_local to
        True. Roles do NOT swap. Two paths:

        - Master receives this (cursor returning from slave): unhide local
          cursor, stop forwarding deltas, warp local cursor to entry edge.
        - Slave receives this (cursor arriving from master): warp local cursor
          to entry edge, activate slave's edge detector for the eventual return.

        Either way: warp, ack, log. Then the local edge detector is freshly
        gated on so the user can immediately walk the cursor back to the peer
        edge if they want.
        """
        state = self._state_ctrl.state
        if state not in (SwitchState.CAPTURING, SwitchState.RECEIVING):
            logger.debug(
                "handoff_request arrived in state %s -- ignored", state.value
            )
            return
        if self._is_master is None:
            logger.debug("handoff_request arrived before mirror_start -- ignored")
            return
        if self._cursor_on_local:
            logger.debug(
                "handoff_request arrived but cursor already on local side -- ignored"
            )
            return

        self._idle_timer.stop()

        perp: float = float(payload.get("perp", 0.5))
        sender_edge: str = payload.get("sender_edge", "right")

        _opposite: dict[str, str] = {
            "right": "left",
            "left": "right",
            "top": "bottom",
            "bottom": "top",
        }
        entry_edge = _opposite.get(sender_edge, "left")

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
            "Cursor arriving from peer: sender_edge=%s perp=%.3f entry=%s n=(%.3f,%.3f) is_master=%s",
            sender_edge, perp, entry_edge, nx, ny, self._is_master,
        )

        # Master path: pause first (stops emit), then drop exclusive (unhides
        # cursor + stops dropping local motion). Reversing the order would
        # briefly emit deltas with the cursor visible -- the slave would see
        # a stray pixel before we settle.
        if self._is_master:
            self._capture.set_paused(True)
            self._capture.set_exclusive(False)
            self._append_log(
                f"Cursor returned from peer at edge={sender_edge} perp={perp:.3f}"
            )
        else:
            self._append_log(
                f"Cursor arrived from controller at edge={sender_edge} perp={perp:.3f}"
            )

        if self._injection_enabled:
            self._injector.move_absolute(
                nx, ny,
                self._screen_w, self._screen_h,
            )

        self._canvas.reset_remote_position()
        self._canvas.apply_remote_delta(nx - 0.5, ny - 0.5)

        self._transport.send({"type": "handoff_ack"})

        # Flip cursor side AFTER sending the ack so any racing message sees the
        # transition reflected. State machine value does not change here -- the
        # role state (CAPTURING for master, RECEIVING for slave) is correct
        # because we never enter TRANSITIONING on the receiving side of a cross.
        self._cursor_on_local = True

        # Re-seed the edge detector so the freshly-warped cursor does not
        # immediately re-trigger at the entry edge.
        self._edge_detector.set_cooldown()
        edge = self._mirror_panel.selected_edge()
        self._edge_detector.set_peer_edge(edge)
        self._capture_paused = False  # edge detector active for return detection

        self._canvas.hide_remote()

    def _handle_handoff_ack(self) -> None:
        """
        Peer accepted our handoff_request. The cursor has now logically crossed
        to the peer. Flip _cursor_on_local to False and roll the state machine
        back to its pre-transition role (CAPTURING for master, RECEIVING for
        slave) -- roles do not swap.

        Master path: engage exclusive (hide local cursor, drop local motion,
        start emitting deltas to the slave).
        Slave path: gate the edge detector off (slave's cursor is parked) and
        reset delta counters for the upcoming controller-driven session.
        """
        if self._state_ctrl.state != SwitchState.TRANSITIONING:
            logger.debug(
                "handoff_ack arrived in state %s -- ignored",
                self._state_ctrl.state.value,
            )
            return

        self._handoff_ack_timer.stop()
        self._state_ctrl.handoff_ack_received()
        rolled_back_to = self._state_ctrl.state

        self._cursor_on_local = False

        if self._is_master:
            # Master takes over the peer's screen: emit deltas, hide local cursor.
            self._capture.set_exclusive(True)
            self._capture.set_paused(False)
            self._append_log("Cursor crossed to peer -- controller driving peer screen")
            logger.info("Cursor crossed to peer (master)")
        else:
            # Slave: cursor went home. Reset receive-side counters and park the
            # edge detector until the cursor returns.
            self._deltas_received = 0
            self._delta_timestamps.clear()
            self._last_sample_log_time = time.monotonic()
            self._last_delta_received_time = time.monotonic()
            self._canvas.reset_remote_position()
            self._append_log("Cursor returned to controller")
            logger.info("Cursor crossed to peer (slave-initiated return)")

        # Edge detector gated off: the cursor is no longer on this machine.
        self._capture_paused = True

        self._conn_panel.on_state_changed(rolled_back_to, peer_info=self._peer_info)
        if rolled_back_to == SwitchState.RECEIVING:
            self._mirror_panel.on_state_changed(
                rolled_back_to,
                arrangement_committed=self._arr_panel.arrangement_committed(),
            )
            self._idle_timer.start()
        else:
            self._mirror_panel.on_state_changed(rolled_back_to)

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
        Main thread: a mouse button event was captured on this machine.
        Only forward if we are the session master AND the cursor is currently
        on the peer's side. Clicks that land while the cursor is on the master
        are handled by the local OS and should never reach the slave.
        """
        if not self._is_master or self._cursor_on_local:
            return
        logger.debug("Sending click: button=%s pressed=%s", button, pressed)
        self._transport.enqueue_message({"type": "click", "button": button, "pressed": pressed})

    def _on_scroll_fired(self, dx: int, dy: int) -> None:
        """
        Main thread: a scroll wheel event was captured on this machine.
        Forward only if this machine is the master and the cursor is on the peer.
        """
        if not self._is_master or self._cursor_on_local:
            return
        logger.debug("Sending scroll: dx=%d dy=%d", dx, dy)
        self._transport.enqueue_message({"type": "scroll", "dx": dx, "dy": dy})

    # ------------------------------------------------------------------
    # Click and scroll receiver handlers (Qt main thread)
    # ------------------------------------------------------------------

    def _handle_click_message(self, message: dict) -> None:
        """
        Inject a click event received from the master. Only acts on the slave
        side while the cursor is logically on this machine and injection is
        enabled.
        """
        if self._is_master or not self._cursor_on_local or not self._injection_enabled:
            return
        self._last_delta_received_time = time.monotonic()
        button: str = message.get("button", "left")
        pressed: bool = bool(message.get("pressed", True))
        logger.debug("Injecting click: button=%s pressed=%s", button, pressed)
        self._injector.click(button, pressed)

    def _handle_scroll_message(self, message: dict) -> None:
        """
        Inject a scroll event received from the master. Slave-side, cursor-here,
        injection-enabled.
        """
        if self._is_master or not self._cursor_on_local or not self._injection_enabled:
            return
        self._last_delta_received_time = time.monotonic()
        dx: int = int(message.get("dx", 0))
        dy: int = int(message.get("dy", 0))
        logger.debug("Injecting scroll: dx=%d dy=%d", dx, dy)
        self._injector.scroll(dx, dy)

    def _handle_key_message(self, message: dict) -> None:
        """
        Inject a keyboard event received from the master. Slave-side,
        cursor-here, injection-enabled.
        """
        if self._is_master or not self._cursor_on_local or not self._injection_enabled:
            return
        key_name: str = message.get("key", "")
        pressed: bool = bool(message.get("pressed", True))
        if not key_name:
            logger.debug("Received key message with empty key -- skipped")
            return
        logger.debug("Injecting key: key=%r pressed=%s", key_name, pressed)
        self._injector.key(key_name, pressed)

    # ------------------------------------------------------------------
    # Keyboard forwarding toggle and key event handler
    # ------------------------------------------------------------------

    def _on_kbd_fwd_toggled(self, state: int) -> None:
        """
        Called when the Forward Keyboard checkbox changes state.

        Only meaningful while CAPTURING. Persists the preference to QSettings.
        """
        checked: bool = state != 0
        self._settings.setValue("keyboard_forwarding_enabled", checked)

        if self._state_ctrl.state != SwitchState.CAPTURING:
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
        if isinstance(key, _pynput_keyboard.KeyCode) and key.char is not None:
            return key.char
        logger.debug("KB forward: cannot translate KeyCode %r -- skipped", key)
        return None

    def _on_key_event_fired(self, key_name: str, pressed: bool) -> None:
        """
        Qt main thread: a keyboard event was captured and forwarding is active.
        Master-only path. Forwards only while the cursor is on the peer; keys
        the master types while the cursor is on the master must stay local.
        """
        if not self._is_master or self._cursor_on_local:
            if pressed:
                self._append_log(
                    f"[KBD diag] key={key_name!r} blocked "
                    f"(is_master={self._is_master}, cursor_on_local={self._cursor_on_local})"
                )
            return
        if pressed:
            self._append_log(f"[KBD diag] key={key_name!r} forwarded to peer")
        logger.debug("Sending key: key=%r pressed=%s", key_name, pressed)
        self._transport.enqueue_message({"type": "key", "key": key_name, "pressed": pressed})

    # ------------------------------------------------------------------
    # Inject toggle and force-release
    # ------------------------------------------------------------------

    def _on_inject_toggled(self, state: int) -> None:
        """
        Called when the Inject checkbox changes state.

        When enabling: run the macOS Accessibility probe first. If the probe
        fails, uncheck the box and leave injection disabled.
        When disabling: deactivate injection immediately.
        """
        checked: bool = state != 0
        if checked:
            if not self._injector.verify_permission_probe():
                self._append_log(
                    "macOS Accessibility permission missing. "
                    "Grant in System Settings > Privacy and Security > Accessibility, "
                    "then re-toggle Inject."
                )
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

        Canvas mirroring continues; only OS cursor movement stops.
        """
        if not self._injection_enabled:
            return
        self._injection_enabled = False
        self._injector.reset_verification()
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

    # ------------------------------------------------------------------
    # Browser extension commands
    # ------------------------------------------------------------------

    def _on_list_tabs_clicked(self) -> None:
        """
        Send list_tabs to the connected Chrome extension.

        Runs in a background thread so the Qt main thread is not blocked while
        waiting for the extension to reply. Results are delivered to the log
        panel via the _browser_log signal.
        """
        if self._browser_agent is None:
            self._append_log("[browser] Browser agent is not running.")
            return

        count = self._browser_agent.connection_count()
        self._browser_panel.set_connected(count)
        if count == 0:
            self._append_log("[browser] No extension connected. Load the extension in Chrome or Brave first.")
            return

        def _run() -> None:
            try:
                tabs = self._browser_agent.send_command("list_tabs")
                if not tabs:
                    self._browser_log.emit("[browser] list_tabs: no open tabs.")
                    return
                lines = [f"[browser] list_tabs: {len(tabs)} tab(s) found."]
                for t in tabs:
                    lines.append(
                        f"  [{t.get('tabId', '?')}] {t.get('title', '(no title)')}"
                        f" -- {t.get('url', '')}"
                    )
                self._browser_log.emit("\n".join(lines))
            except Exception as exc:
                self._browser_log.emit(f"[browser] list_tabs error: {exc}")

        threading.Thread(target=_run, name="browser-list-tabs", daemon=True).start()

    def _on_browser_send_clicked(self) -> None:
        """
        Send an arbitrary browser command from the composer UI to the extension.

        Parses the params field as JSON, unpacks the resulting dict as kwargs
        to send_command, and logs the raw result. Runs in a background thread
        so the Qt main thread is never blocked waiting for the extension.
        """
        import json as _json

        if self._browser_agent is None:
            self._append_log("[browser] Browser agent is not running.")
            return

        count = self._browser_agent.connection_count()
        self._browser_panel.set_connected(count)
        if count == 0:
            self._append_log("[browser] No extension connected.")
            return

        command = self._browser_panel.current_command()
        raw_params = self._browser_panel.current_params_text()

        # Parse params. Accept an empty string as {}.
        try:
            params: dict = _json.loads(raw_params) if raw_params else {}
        except _json.JSONDecodeError as exc:
            self._append_log(f"[browser] Bad params JSON: {exc}")
            return

        if not isinstance(params, dict):
            self._append_log("[browser] Params must be a JSON object, not an array or scalar.")
            return

        def _run() -> None:
            try:
                result = self._browser_agent.send_command(command, **params)
                self._browser_log.emit(f"[browser] {command} result: {_json.dumps(result, indent=2)}")
            except Exception as exc:
                self._browser_log.emit(f"[browser] {command} error: {exc}")

        threading.Thread(
            target=_run, name=f"browser-{command}", daemon=True
        ).start()

    def _on_browser_hotkey(self, action: str) -> None:
        """
        Qt main thread: a browser hotkey was pressed (B1).

        Dispatches the appropriate browser command in a background thread so the
        Qt main thread is never blocked waiting for the extension reply.

        action is one of: "list_tabs", "tab_next", "tab_prev".
        """
        if self._browser_agent is None:
            self._append_log("[browser] Browser agent is not running.")
            return

        count = self._browser_agent.connection_count()
        self._browser_panel.set_connected(count)
        if count == 0:
            self._append_log("[browser] No extension connected -- hotkey ignored.")
            return

        if action == "list_tabs":
            # Reuse the existing list-tabs runner directly.
            self._on_list_tabs_clicked()
            return

        if action in ("tab_next", "tab_prev"):
            direction = "next" if action == "tab_next" else "prev"
            self._append_log(f"[browser] tab_cycle {direction} (hotkey)")

            def _run() -> None:
                try:
                    result = self._browser_agent.send_command("tab_cycle", direction=direction)
                    from_i = result.get("fromIndex", "?")
                    to_i = result.get("toIndex", "?")
                    tab_id = result.get("tabId", "?")
                    self._browser_log.emit(
                        f"[browser] tab_cycle {direction}: "
                        f"tab {from_i} -> {to_i} (tabId={tab_id})"
                    )
                except Exception as exc:
                    self._browser_log.emit(f"[browser] tab_cycle {direction} error: {exc}")

            threading.Thread(target=_run, name=f"browser-tab-{direction}", daemon=True).start()
            return

        # Unrecognised action -- should not happen, but log it rather than silently swallow.
        self._append_log(f"[browser] Unknown browser hotkey action: {action!r}")

    def _on_clipboard_push(self) -> None:
        """
        Qt main thread: Ctrl+Alt+Shift+V was pressed (B3 clipboard-push hotkey).

        Reads the host OS clipboard via slickshift.browser_agent.clipboard and
        dispatches open_url (for URLs) or paste_text (for everything else) to
        the connected browser extension. Runs the blocking send_command call in
        a background thread so the Qt main thread is never stalled.
        """
        if self._browser_agent is None:
            self._append_log("[browser] Browser agent is not running.")
            return

        count = self._browser_agent.connection_count()
        self._browser_panel.set_connected(count)
        if count == 0:
            self._append_log("[browser] No extension connected -- clipboard push ignored.")
            return

        self._append_log("[browser] Clipboard push hotkey (Ctrl+Alt+Shift+V) detected.")

        def _run() -> None:
            from slickshift.browser_agent.clipboard import push_clipboard_to_browser
            try:
                result = push_clipboard_to_browser(self._browser_agent)
                # open_url returns {"tabId": N}; paste_text returns {"pasted": bool, ...}
                if isinstance(result, dict) and "tabId" in result:
                    self._browser_log.emit(
                        f"[browser] clipboard push: opened URL in new tab (tabId={result['tabId']})"
                    )
                elif isinstance(result, dict) and result.get("pasted") is False:
                    reason = result.get("reason", "unknown")
                    self._browser_log.emit(
                        f"[browser] clipboard push: text not pasted -- {reason}"
                    )
                else:
                    self._browser_log.emit(
                        f"[browser] clipboard push: text pasted into {result.get('elementType', '?')}"
                    )
            except Exception as exc:
                self._browser_log.emit(f"[browser] clipboard push error: {exc}")

        threading.Thread(target=_run, name="browser-clipboard-push", daemon=True).start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """
        Stop pynput listener threads cleanly before the window closes.
        Also stop the keyboard listener and cancel any in-flight reconnect.
        """
        self._settings.setValue("scroll_multiplier", self._mirror_panel.scroll_slider.value())
        self._settings.setValue("monitors_unlocked", self._arr_panel.monitors_unlocked())
        self._reconnect_timer.stop()
        self._idle_timer.stop()
        self._stop_kb_listener()
        self._event_capture.stop()
        if self._browser_agent is not None:
            self._browser_agent.stop()
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        """
        Intercept hotkeys while the window has focus.

        Esc: force-release OS injection (RECEIVING plus injection enabled only).
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

        Dims the canvas red square in TRANSITIONING; restores in all other states.
        Clears keyboard forwarding when leaving CAPTURING.
        """
        mode = state.value.upper()
        x, y = pyautogui.position()
        role = self._role_label_for_state(state)
        transitioning = (state == SwitchState.TRANSITIONING)
        reconnecting = (state == SwitchState.RECONNECTING)

        if state != SwitchState.CAPTURING and self._keyboard_forwarding_active:
            self._keyboard_forwarding_active = False
            logger.info("Keyboard forwarding deactivated (state left CAPTURING)")

        self._status_bar.update_status(
            mode, x, y, role=role,
            transitioning=transitioning, reconnecting=reconnecting,
            kbd_active=self._keyboard_forwarding_active,
        )
        self._canvas.set_local_dimmed(transitioning)
        self._arr_panel.on_state_changed(state)

    # ------------------------------------------------------------------
    # Dead-man monitor (Qt main thread, 500 ms tick)
    # ------------------------------------------------------------------

    _DEAD_MAN_ACTIVE_STATES: frozenset[SwitchState] = frozenset({
        SwitchState.CONNECTED,
        SwitchState.CAPTURING,
        SwitchState.RECEIVING,
        SwitchState.HANDSHAKING,
    })

    def _check_dead_man(self) -> None:
        """
        Called every 500 ms by _dead_man_timer on the Qt main thread.

        Reads seconds_since_last_pong() from the transport. If the elapsed time
        exceeds HEARTBEAT_TIMEOUT_S, fire the disconnect path immediately.
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
    # System-wide force-release hotkey (pynput keyboard listener)
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
        Called on the pynput listener thread.
        """
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

    def _kb_browser_hotkey_action(
        self,
        key: _pynput_keyboard.Key | _pynput_keyboard.KeyCode | None,
    ) -> str | None:
        """
        Return the browser action string if the current key + modifier state
        matches a B1 browser hotkey, otherwise None.

        The three bindings share the Ctrl+Alt+Shift chord and differ only in the
        trigger key -- 'l' for list_tabs, ']' for next tab, '[' for prev tab.
        Called on the pynput listener thread after the force-release check, so it
        only fires when the combo is NOT the force-release combo.
        """
        if not isinstance(key, _pynput_keyboard.KeyCode):
            # All three trigger keys are printable characters, not special keys.
            return None
        char = key.char
        if char is None:
            return None

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
        # Read the pressed-keys snapshot once under the lock.
        with self._kb_pressed_keys_lock:
            pressed = self._kb_pressed_keys
            has_ctrl = bool(pressed & ctrl_variants)
            has_alt = bool(pressed & alt_variants)
            has_shift = bool(pressed & shift_variants)

        if not (has_ctrl and has_alt and has_shift):
            return None

        # char.lower() normalises away the shift-modified character so the user
        # can press either case.  On most OSes Shift+[ sends '{'; on macOS it may
        # send the literal '[' depending on keyboard layout.  Checking both the raw
        # char and its lower form handles the common layouts without a keymap lookup.
        lower = char.lower()
        if lower == "l":
            return "list_tabs"
        if char in ("]", "}") or lower in ("]", "}"):
            return "tab_next"
        if char in ("[", "{") or lower in ("[", "{"):
            return "tab_prev"
        if lower == "v":
            return "clipboard_push"
        return None

    def _kb_on_press(
        self,
        key: _pynput_keyboard.Key | _pynput_keyboard.KeyCode | None,
    ) -> None:
        """
        Called on the pynput listener thread for every key press.

        Force-release detection is unconditional. Keyboard forwarding is layered
        on top: if _keyboard_forwarding_active is True, translate and emit the
        key -- EXCEPT when the full force-release combo is active, to avoid
        leaking that combo to the peer.
        """
        if key is None:
            return
        with self._kb_pressed_keys_lock:
            self._kb_pressed_keys.add(key)
        logger.debug("KB listener: press %r", key)
        if self._kb_combo_active():
            logger.info("Force-release combo detected on keyboard listener thread")
            self._main_signals.force_release_pressed.emit()
            return
        # Browser hotkeys (B1/B3): Ctrl+Alt+Shift+{L, ], [, V}. Checked after
        # force-release so the modifier chord never leaks into browser commands
        # when Esc is also held.
        browser_action = self._kb_browser_hotkey_action(key)
        if browser_action is not None:
            logger.info("Browser hotkey detected: %s", browser_action)
            # B3 clipboard-push gets its own signal so its handler can be kept
            # separate from the B1 tab-management handler.
            if browser_action == "clipboard_push":
                self._main_signals.clipboard_push_pressed.emit()
            else:
                self._main_signals.browser_hotkey_pressed.emit(browser_action)
            return
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
        Forward key-release events when keyboard forwarding is active.
        """
        if key is None:
            return
        with self._kb_pressed_keys_lock:
            self._kb_pressed_keys.discard(key)
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
        """
        if not self.isVisible():
            return

        logger.info("Force-released via system-wide hotkey (Ctrl+Alt+Shift+Esc)")
        self._append_log(
            "Force-released via system-wide hotkey (Ctrl+Alt+Shift+Esc)"
        )

        # If DEBUG freeze is engaged, release it first. Setting the toggle
        # button unchecked fires _on_debug_pause_toggled(False) which stops
        # the diagnostic timer, unpauses, stops, and tears down _debug_capture
        # -- and the cursor reappears immediately. Without this, the emergency
        # hotkey only released the main _capture and left the user stuck in
        # an invisible-cursor state with no escape but killing the process.
        if self._debug_capture is not None:
            logger.info("Force-release also clearing DEBUG cursor-freeze")
            self._mirror_panel.debug_pause_btn.setChecked(False)

        self._user_initiated_disconnect = True
        self._reconnect_timer.stop()
        self._idle_timer.stop()

        if self._state_ctrl.state not in (SwitchState.IDLE, SwitchState.RECONNECTING):
            try:
                self._transport.send({"type": "force_release"})
            except Exception:
                pass

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

        self._reset_session_role_flags()
        self._peer_info = None
        self._peer_monitors = None
        self._peer_identity = ""
        self._conn_panel.on_state_changed(SwitchState.IDLE, peer_info=None)
        self._mirror_panel.on_state_changed(SwitchState.IDLE)

    def _handle_peer_force_release(self) -> None:
        """
        Peer sent a force_release notification. Tear down mirroring on our side
        regardless of role and return to CONNECTED.

        Requirement #1 from the spec: if the slave force-closes, the master's
        cursor must auto-unlock immediately. The _stop_capture_if_running path
        below releases exclusive mode and stops the capture thread, which is
        what restores the master's local cursor.
        """
        self._user_initiated_disconnect = True
        state = self._state_ctrl.state
        if state == SwitchState.CAPTURING:
            self._event_capture.set_active(False)
            self._stop_capture_if_running()
            self._state_ctrl.stop_mirroring()
            self._reset_session_role_flags()
            self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
            self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
            self._append_log("Peer force-released -- returned to CONNECTED")
        elif state == SwitchState.RECEIVING:
            self._idle_timer.stop()
            self._canvas.hide_remote()
            self._state_ctrl.mirror_stop_received()
            self._reset_session_role_flags()
            self._conn_panel.on_state_changed(SwitchState.CONNECTED, peer_info=self._peer_info)
            self._mirror_panel.on_state_changed(SwitchState.CONNECTED)
            self._append_log("Peer force-released -- returned to CONNECTED")

    # ------------------------------------------------------------------
    # Auto-reconnect timer callback
    # ------------------------------------------------------------------

    def _on_reconnect_attempt(self) -> None:
        """
        Qt main thread: reconnect timer fired. Attempt to reconnect to the peer.
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

        self._transport = TcpTransport()
        self._transport.register_connected_callback(self._on_transport_connected)
        self._transport.register_disconnected_callback(self._on_reconnect_disconnected)
        self._transport.register_message_callback(self._on_message_received)

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
            self._peer_monitors = None
            self._peer_identity = ""
            self._conn_panel.on_state_changed(SwitchState.IDLE, peer_info=None)
            self._mirror_panel.on_state_changed(SwitchState.IDLE)
            return

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
    # Idle timeout in RECEIVING state
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

            try:
                self._transport.send({"type": "idle_timeout_release"})
            except Exception:
                pass

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
