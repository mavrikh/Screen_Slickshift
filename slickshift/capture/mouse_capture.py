"""
Phase 1 capture implementation. Uses passive `pyautogui` polling. Will be replaced by `CGEventTap` (macOS) and `RawInput` (Windows) in Phase 2 for true exclusive capture.

Polls the local cursor position at ~60 Hz and delivers normalized delta messages
to a caller-supplied enqueue function. Edge detection is NOT performed here.
Edge detection is the responsibility of the caller (the UI layer), which feeds
cursor positions to an EdgeDetector instance on every tick.

DPI handling:
- pyautogui.size() and pyautogui.position() are internally consistent on each
  platform. No dpi_scale correction is needed at the math layer.
- Normalized deltas represent a fraction of the sender's virtual-desktop canvas
  (the bounding box that spans all local monitors).
- The receiver scales back up using its own virtual-desktop bounding box.
- On a single-monitor setup the virtual-desktop bounding box equals the primary
  monitor dimensions, so behavior is identical to the previous implementation.

Multi-monitor normalization:
- pyautogui.position() returns virtual-desktop coordinates which can be negative
  (when a monitor is placed to the left or above the primary) or wider/taller
  than the primary monitor alone.
- Deltas are normalized as fractions of the virtual-desktop width/height so they
  span 0.0-1.0 across the full multi-monitor canvas.
- The virtual-desktop bounding box is derived from the MonitorInfo list provided
  at construction time and computed by compute_virtual_desktop_bbox().
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from collections.abc import Callable
from typing import Any

import pyautogui

from slickshift import config
from slickshift.transport.topology import MonitorInfo

_IS_MACOS: bool = platform.system() == "Darwin"

# Architecture invariant: pyautogui.FAILSAFE must always be False in Slickshift.
# The cursor legitimately reaches corners in a KVM. The dead-man switch (heartbeat)
# and the force-release hotkey replace the failsafe. See config.py and commit 523ee03.
pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)

# Type alias for the enqueue function supplied by the transport layer.
# Signature: enqueue_fn(message: dict[str, Any]) -> bool
# Returns True if the message was accepted, False if dropped due to backpressure.
DeltaCallback = Callable[[dict[str, Any]], bool]


def compute_virtual_desktop_bbox(
    monitors: list[MonitorInfo],
) -> tuple[int, int, int, int]:
    """
    Compute the bounding box of the full virtual desktop from a list of monitors.

    Returns (vd_x, vd_y, vd_w, vd_h) where:
      vd_x -- left edge of the virtual desktop (minimum monitor x, often 0)
      vd_y -- top edge of the virtual desktop (minimum monitor y, often 0)
      vd_w -- total width of the virtual desktop (max(x + width) - min(x))
      vd_h -- total height of the virtual desktop (max(y + height) - min(y))

    On a single-monitor setup with the primary at (0, 0) this degenerates to
    (0, 0, screen_w, screen_h), which is identical to the legacy behavior.

    If monitors is empty, falls back to pyautogui.size() so the capture loop
    never divides by zero. The caller is responsible for ensuring monitors is
    non-empty whenever possible.
    """
    if not monitors:
        logger.warning(
            "compute_virtual_desktop_bbox: empty monitor list -- "
            "falling back to pyautogui.size()"
        )
        w, h = pyautogui.size()
        return (0, 0, int(w), int(h))

    min_x = min(m.x for m in monitors)
    min_y = min(m.y for m in monitors)
    max_x = max(m.x + m.width for m in monitors)
    max_y = max(m.y + m.height for m in monitors)
    vd_w = max_x - min_x
    vd_h = max_y - min_y

    # Guard against degenerate cases (e.g. all monitors report width/height == 0).
    if vd_w <= 0:
        logger.warning(
            "compute_virtual_desktop_bbox: computed vd_w=%d -- clamping to 1", vd_w
        )
        vd_w = 1
    if vd_h <= 0:
        logger.warning(
            "compute_virtual_desktop_bbox: computed vd_h=%d -- clamping to 1", vd_h
        )
        vd_h = 1

    logger.debug(
        "Virtual desktop bbox: origin=(%d,%d) size=%dx%d (from %d monitor(s))",
        min_x, min_y, vd_w, vd_h, len(monitors),
    )
    return (min_x, min_y, vd_w, vd_h)


class MouseCapture:
    """
    Polls the local cursor position at ~60 Hz and delivers normalized delta
    messages to a caller-supplied enqueue function.

    This class is responsible only for delta delivery. Edge detection (dwell
    timer, cooldown, edge-band geometry) is handled externally by an
    EdgeDetector instance. The UI wires the two together: it feeds cursor
    positions from each poll tick to EdgeDetector.tick() on every iteration.

    Lifecycle:
        monitors = enumerate_local_monitors()
        capture = MouseCapture(monitors)
        capture.start(enqueue_fn)        # starts background polling thread
        ...
        capture.set_paused(True)         # suppress delta delivery (TRANSITIONING)
        capture.stop()                   # joins the thread

    The enqueue_fn is called from the polling thread and must be thread-safe.

    monitors: list[MonitorInfo] from enumerate_local_monitors(). Used to compute
        the virtual-desktop bounding box for normalization. On a single-monitor
        setup this is equivalent to the legacy screen_w/screen_h behavior.

    pyautogui is internally consistent -- position() and the monitor geometry are
    in the same logical-pixel unit on each platform -- so no dpi_scale
    transformation is needed here.
    """

    def __init__(self, monitors: list[MonitorInfo]) -> None:
        self._vd_x, self._vd_y, self._vd_w, self._vd_h = compute_virtual_desktop_bbox(monitors)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._seq: int = 0

        # Paused flag: when True, deltas are not enqueued (TRANSITIONING state).
        # The poll loop still runs so the caller can drive EdgeDetector.tick()
        # from the position values returned via the tick callback.
        self._paused: bool = False
        self._paused_lock = threading.Lock()

        logger.info(
            "MouseCapture initialized. virtual desktop: origin=(%d,%d) size=%dx%d",
            self._vd_x,
            self._vd_y,
            self._vd_w,
            self._vd_h,
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

        Normalization: ndx = dx_px / vd_w, ndy = dy_px / vd_h.
        vd_w and vd_h are the virtual-desktop bounding box dimensions derived from
        the local monitor list. On a single-monitor setup vd_w == screen_w and
        vd_h == screen_h, so behavior is identical to the legacy implementation.
        Both numerator and denominator are in pyautogui's native logical-pixel unit
        on this platform, so the fraction is correct regardless of DPI mode.
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
                    ndx: float = dx_px / self._vd_w
                    ndy: float = dy_px / self._vd_h
                    logger.debug(
                        "Delta: raw=(%d,%d) vd=(%dx%d) n=(%.4f,%.4f)",
                        dx_px, dy_px, self._vd_w, self._vd_h, ndx, ndy,
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


def make_mouse_capture(monitors: list[MonitorInfo]) -> MouseCapture:
    """
    Build the platform-appropriate MouseCapture for this OS.

    Returns MacMouseCapture (CGEventTap-based, supports cursor suppression)
    on macOS. Returns the polling MouseCapture on every other platform.

    Callers should always go through this factory rather than instantiating
    MouseCapture directly. That keeps the OS choice in one place; future
    Windows (RawInput) and Linux (libinput) implementations slot in here
    without touching any caller.
    """
    if _IS_MACOS:
        from slickshift.capture.mac_event_tap import MacMouseCapture
        return MacMouseCapture(monitors)
    return MouseCapture(monitors)
