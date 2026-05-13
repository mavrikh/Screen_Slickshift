"""
cursor-bridge-test -- input capture (Step 4: polling delta loop).

Uses pyautogui.position() at ~60 Hz to compute normalized cursor deltas and
deliver them to a bounded outbound queue. The queue is consumed by the transport
layer's sender thread.

Design decisions vs. the brainstorm doc (Section 3A):
- pyautogui.position() is a passive poll, not a CGEventTap event callback.
  This means local cursor delivery is NOT suppressed while CAPTURING -- that is
  Step 6 (handoff) + Step 7 (DPI normalization). Passive polling is correct for
  Step 4, which is delta-send-only.
- The poll interval matches CANVAS_REFRESH_MS (16 ms, ~60 Hz). Step 4 does not
  require a tighter loop.
- Normalization: raw pixel deltas are divided by the logical screen dimensions
  reported by pyautogui.size(). The receiver scales up using its own screen
  dimensions from the hello payload. This is the hybrid model from brainstorm
  doc Section 4 Step 3.
- No-op suppression: if (dx, dy) == (0, 0), no delta message is enqueued. This
  prevents the send queue from filling when the cursor is stationary.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

import pyautogui

import config

logger = logging.getLogger(__name__)

# Type alias for the enqueue function supplied by the transport layer.
# Accepts a delta dict; returns True if the message was queued, False if dropped.
EnqueueFn = Callable[[dict], bool]


class DeltaCapture:
    """
    Polls the local cursor position at ~60 Hz and enqueues normalized delta
    messages for transmission by the transport layer.

    Lifecycle:
        capture = DeltaCapture()
        capture.start(enqueue_fn)   # starts background polling thread
        ...
        capture.stop()              # joins the thread

    The enqueue_fn is called from the polling thread. It must be thread-safe.
    The caller (TcpTransport) supplies a bounded queue with drop-oldest semantics.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._seq: int = 0
        self._screen_w, self._screen_h = pyautogui.size()
        logger.info(
            "DeltaCapture initialized. Logical screen: %dx%d",
            self._screen_w,
            self._screen_h,
        )

    def start(self, enqueue_fn: EnqueueFn) -> None:
        """
        Start the polling loop in a background daemon thread.

        enqueue_fn is called with each delta dict. It returns True if the
        message was accepted, False if it was dropped due to backpressure.
        """
        self._stop_event.clear()
        self._seq = 0
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(enqueue_fn,),
            daemon=True,
            name="delta-capture",
        )
        self._thread.start()
        logger.info("DeltaCapture started")

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("DeltaCapture stopped")

    def _poll_loop(self, enqueue_fn: EnqueueFn) -> None:
        """
        Background thread: poll pyautogui.position() every CANVAS_REFRESH_MS.

        Computes (dx_px, dy_px), skips no-ops, normalizes, and calls enqueue_fn.
        """
        interval_s: float = config.CANVAS_REFRESH_MS / 1000.0
        prev_x, prev_y = pyautogui.position()

        while not self._stop_event.is_set():
            time.sleep(interval_s)

            cur_x, cur_y = pyautogui.position()
            dx_px = cur_x - prev_x
            dy_px = cur_y - prev_y

            if dx_px == 0 and dy_px == 0:
                # Cursor did not move -- do not send a no-op delta.
                continue

            prev_x, prev_y = cur_x, cur_y

            ndx: float = dx_px / self._screen_w
            ndy: float = dy_px / self._screen_h
            self._seq += 1

            delta: dict = {
                "type": "delta",
                "ndx": ndx,
                "ndy": ndy,
                "seq": self._seq,
            }

            accepted = enqueue_fn(delta)
            if not accepted:
                logger.debug("Delta seq=%d dropped (send queue full)", self._seq)
