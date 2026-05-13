"""
cursor-bridge-test -- PyQt6 main window.

Three regions:
  1. Status bar (top): current mode, cursor coordinates, FPS.
  2. Canvas (center): a filled square representing the local cursor position.
     In Step 1 the dot tracks the real cursor via pyautogui polling.
     In later steps it is driven by the state machine and transport layer.
  3. Log panel (bottom, collapsible): last N lines from the event log.

No real input handling or transport wiring in Step 1. The window is a
visual skeleton: it opens, the dot tracks the cursor, the placeholder text
is correct, and the log panel scrolls.
"""

from __future__ import annotations

import logging
import pyautogui

from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QMainWindow,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import config

logger = logging.getLogger(__name__)

# Dot size in logical pixels for the canvas cursor indicator.
_DOT_SIZE: int = 14


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
    """Top status bar. Shows mode, cursor coordinates, and FPS."""

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
        self.setFixedHeight(140)
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


class MainWindow(QMainWindow):
    """Root application window. Owns the status bar, canvas, and log panel."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Slickshift -- Cursor Bridge Test")
        self.setMinimumSize(QSize(640, 520))
        self.resize(900, 620)
        self.setStyleSheet("background-color: #0f0f1a;")

        # --- Widgets ---
        self._status_bar = _StatusBar()
        self._canvas = _Canvas()
        self._log_panel = _LogPanel()

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
        root_layout.addWidget(self._status_bar)
        root_layout.addWidget(splitter)
        self.setCentralWidget(central)

        # --- Step 1 polling timer ---
        # Reads cursor position via pyautogui and updates the canvas dot and
        # status bar. Real input handling replaces this in Step 4.
        self._screen_w, self._screen_h = pyautogui.size()
        pyautogui.FAILSAFE = config.PYAUTOGUI_FAILSAFE

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(config.CANVAS_REFRESH_MS)
        self._poll_timer.timeout.connect(self._poll_cursor)
        self._poll_timer.start()

        logger.info("MainWindow initialized. Polling cursor at %dms interval.", config.CANVAS_REFRESH_MS)

    def _poll_cursor(self) -> None:
        x, y = pyautogui.position()
        nx = x / self._screen_w
        ny = y / self._screen_h
        self._canvas.set_normalized_position(nx, ny)
        self._status_bar.update_status("IDLE", x, y)

    def append_log(self, text: str) -> None:
        """Public interface for other components to write to the log panel."""
        self._log_panel.append_line(text)
