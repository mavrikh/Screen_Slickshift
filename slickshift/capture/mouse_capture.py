"""
Phase 1 capture implementation. Uses passive `pyautogui` polling. Will be replaced by `CGEventTap` (macOS) and `RawInput` (Windows) in Phase 2 for true exclusive capture.

Polls the local cursor position at ~60 Hz and delivers normalized delta messages
to a caller-supplied enqueue function. Edge detection is NOT performed here.
Edge detection is the responsibility of the caller (the UI layer), which feeds
cursor positions to an EdgeDetector instance on every tick.

DPI handling:
- pyautogui.size() and pyautogui.position() are internally consistent on each
  platform. No dpi_scale correction is needed at the math layer.
- Normalized deltas represent a fraction of the sender's pyautogui canvas.
- The receiver scales back up using its own pyautogui.size(). The
  fraction-of-screen invariant naturally handles cross-machine DPI differences
  without any explicit dpi_scale arithmetic.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import pyautogui

from slickshift import config

# Architecture invariant: pyautogui.FAILSAFE must always be False in Slickshift.
# The cursor legitimately reaches corners in a KVM. The dead-man switch (heartbeat)
# and the force-release hotkey replace the failsafe. See config.py and commit 523ee03.
pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)

# Type alias for the enqueue function supplied by the transport layer.
# Signature: enqueue_fn(message: dict[str, Any]) -> bool
# Returns True if the message was accepted, False if dropped due to backpressure.
DeltaCallback = Callable[[dict[str, Any]], bool]


class MouseCapture:
    """
    Polls the local cursor position at ~60 Hz and delivers normalized delta
    messages to a caller-supplied enqueue function.

    This class is responsible only for delta delivery. Edge detection (dwell
    timer, cooldown, edge-band geometry) is handled externally by an
    EdgeDetector instance. The UI wires the two together: it feeds cursor
    positions from each poll tick to EdgeDetector.tick() on every iteration.

    Lifecycle:
        capture = MouseCapture(screen_w, screen_h)
        capture.start(enqueue_fn)        # starts background polling thread
        ...
        capture.set_paused(True)         # suppress delta delivery (TRANSITIONING)
        capture.stop()                   # joins the thread

    The enqueue_fn is called from the polling thread and must be thread-safe.

    screen_w, screen_h: pyautogui.size() on the caller's platform. pyautogui is
        internally consistent -- size() and position() are in the same unit on
        each platform -- so no dpi_scale transformation is needed here.
        Passed in so MouseCapture does not need to import PyQt6 to query QScreen.
    """

    def __init__(self, screen_w: int, screen_h: int) -> None:
        self._screen_w: int = screen_w
        self._screen_h: int = screen_h
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._seq: int = 0

        # Paused flag: when True, deltas are not enqueued (TRANSITIONING state).
        # The poll loop still runs so the caller can drive EdgeDetector.tick()
        # from the position values returned via the tick callback.
        self._paused: bool = False
        self._paused_lock = threading.Lock()

        logger.info(
            "MouseCapture initialized. screen: %dx%d",
            self._screen_w,
            self._screen_h,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, delta_callback: DeltaCallback) -> None:
        """
        Start the polling loop in a background daemon thread.

        delta_callback is called from the polling thread with each delta dict
        when not paused. It must be thread-safe. Returns True if the message
        was accepted, False if dropped due to backpressure.
        """
        self._stop_event.clear()
        with self._paused_lock:
            self._paused = False
        self._seq = 0
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(delta_callback,),
            daemon=True,
            name="mouse-capture",
        )
        self._thread.start()
        logger.info("MouseCapture started")

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it to exit. Idempotent."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("MouseCapture stopped")

    def set_paused(self, paused: bool) -> None:
        """
        Pause or resume delta delivery without stopping the underlying capture.

        When paused=True, captured deltas are not forwarded to delta_callback,
        but the polling thread remains active. Used during TRANSITIONING so no
        stale deltas reach the peer after handoff fires.

        Edge detection (via EdgeDetector.tick()) continues while paused so the
        rollback path can still track cursor state. The UI is responsible for
        calling EdgeDetector.tick() with the paused flag set accordingly.
        """
        with self._paused_lock:
            self._paused = paused
        logger.debug("MouseCapture paused=%s", paused)

    # ------------------------------------------------------------------
    # Background poll loop
    # ------------------------------------------------------------------

    def _poll_loop(self, delta_callback: DeltaCallback) -> None:
        """
        Background thread: poll pyautogui.position() every CANVAS_REFRESH_MS.

        Each tick:
        1. Compute (dx_px, dy_px). If non-zero and not paused, normalize and deliver.
        2. (dx, dy) == (0, 0) skips delivery but the loop still runs so the
           caller's EdgeDetector.tick() continues to advance on every iteration.

        Normalization: ndx = dx_px / screen_w, ndy = dy_px / screen_h.
        Both numerator and denominator are in pyautogui's native unit on this
        platform, so the fraction is correct regardless of DPI mode.
        """
        interval_s: float = config.CANVAS_REFRESH_MS / 1000.0
        prev_x, prev_y = pyautogui.position()

        while not self._stop_event.is_set():
            time.sleep(interval_s)

            cur_x, cur_y = pyautogui.position()
            dx_px = cur_x - prev_x
            dy_px = cur_y - prev_y

            if dx_px != 0 or dy_px != 0:
                prev_x, prev_y = cur_x, cur_y
                with self._paused_lock:
                    currently_paused = self._paused
                if not currently_paused:
                    ndx: float = dx_px / self._screen_w
                    ndy: float = dy_px / self._screen_h
                    logger.debug(
                        "Delta: raw=(%d,%d) screen=(%dx%d) n=(%.4f,%.4f)",
                        dx_px, dy_px, self._screen_w, self._screen_h, ndx, ndy,
                    )
                    self._seq += 1
                    delta: dict[str, Any] = {
                        "type": "delta",
                        "ndx": ndx,
                        "ndy": ndy,
                        "seq": self._seq,
                    }
                    accepted = delta_callback(delta)
                    if not accepted:
                        logger.debug("Delta seq=%d dropped (send queue full)", self._seq)
