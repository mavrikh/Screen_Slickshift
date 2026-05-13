"""
cursor-bridge-test -- input capture (Steps 4, 6, and 7).

Step 4: pyautogui.position() at ~60 Hz computes normalized cursor deltas and
delivers them to a bounded outbound queue consumed by the transport sender thread.

Step 6 additions:
- Edge band detection: on each poll tick, if the cursor is within EDGE_BAND_PX
  of the configured peer edge and the caller is in CAPTURING state, a dwell timer
  starts. If the cursor stays in the band for EDGE_DWELL_S seconds without leaving,
  the on_edge_dwell callback fires. The perpendicular coordinate at that moment is
  passed as a normalized value 0.0-1.0.
- Cooldown guard: the caller sets _handoff_cooldown_until (via set_cooldown()) so
  edge detection is suppressed for HANDOFF_COOLDOWN_S after every handoff landing.
  This prevents immediate re-trigger when the newly warped cursor starts at the edge.
- The poll loop pauses delta delivery while is_paused is True (set by the main
  window during TRANSITIONING). Pausing does NOT stop the thread -- edge detection
  keeps running so the rollback path can still poll cursor state.

DPI handling (simplified in Step 9):
- pyautogui.size() and pyautogui.position() are internally consistent on each
  platform. No dpi_scale correction is applied at the math layer.
- Normalized deltas represent a fraction of the sender's pyautogui canvas.
- The receiver scales back up using its own pyautogui.size(). The
  fraction-of-screen invariant naturally handles cross-machine DPI differences
  without any explicit dpi_scale arithmetic.
- The position_in_physical_pixels heuristic is computed and logged in MainWindow
  for diagnostic visibility but nothing in this module branches on it.

Design decisions vs. brainstorm doc (Section 3A):
- pyautogui.position() is a passive poll, not a CGEventTap event callback.
  Local cursor delivery is NOT suppressed while CAPTURING. That is Step 7+.
- Normalization: raw pixel deltas are divided by pyautogui.size() on the sender
  side and multiplied by pyautogui.size() on the receiver side.
- No-op suppression: (dx, dy) == (0, 0) skips the queue enqueue but edge-band
  checks still run on every tick so the dwell timer advances correctly.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import pyautogui

import config

logger = logging.getLogger(__name__)

# Type alias for the enqueue function supplied by the transport layer.
EnqueueFn = Callable[[dict], bool]

# Type alias for the edge-dwell callback.
# Arguments: sender_edge (str), perp (float 0.0-1.0).
EdgeDwellCallback = Callable[[str, float], None]

# Valid edge names.
VALID_EDGES: frozenset[str] = frozenset({"right", "left", "top", "bottom"})


def _compute_perp(edge: str, x: int, y: int, screen_w: int, screen_h: int) -> float:
    """
    Compute the normalized perpendicular coordinate at a given edge.

    For left/right edges the perpendicular axis is Y: return y / screen_h.
    For top/bottom edges the perpendicular axis is X: return x / screen_w.
    screen_w and screen_h are from pyautogui.size() on this platform.
    Clamped to [0.0, 1.0].
    """
    if edge in ("left", "right"):
        return max(0.0, min(1.0, y / screen_h))
    return max(0.0, min(1.0, x / screen_w))


def _in_edge_band(edge: str, x: int, y: int, screen_w: int, screen_h: int) -> bool:
    """
    Return True if (x, y) is within EDGE_BAND_PX of the named edge boundary.

    "left"   -- x <= EDGE_BAND_PX - 1
    "right"  -- x >= screen_w - EDGE_BAND_PX
    "top"    -- y <= EDGE_BAND_PX - 1
    "bottom" -- y >= screen_h - EDGE_BAND_PX

    screen_w and screen_h are from pyautogui.size() on this platform.
    """
    band = config.EDGE_BAND_PX
    if edge == "left":
        return x < band
    if edge == "right":
        return x >= screen_w - band
    if edge == "top":
        return y < band
    if edge == "bottom":
        return y >= screen_h - band
    return False


class DeltaCapture:
    """
    Polls the local cursor position at ~60 Hz, enqueues normalized delta
    messages for transmission, and fires an edge-dwell callback when the
    cursor holds at the configured peer edge long enough.

    Lifecycle:
        capture = DeltaCapture(screen_w, screen_h)
        capture.set_peer_edge("right")          # configure before start
        capture.set_edge_dwell_callback(fn)
        capture.start(enqueue_fn)               # starts background polling thread
        ...
        capture.set_paused(True)                # suppress delta enqueue (TRANSITIONING)
        capture.set_cooldown()                  # suppress edge detection after handoff
        capture.stop()                          # joins the thread

    The enqueue_fn is called from the polling thread and must be thread-safe.
    The on_edge_dwell callback fires from the polling thread; route to the Qt
    main thread via signal if it touches Qt widgets.

    screen_w, screen_h: pyautogui.size() on the caller's platform. pyautogui is
        internally consistent -- size() and position() are in the same unit on
        each platform -- so no dpi_scale transformation is needed here.
        Passed in so DeltaCapture does not need to import PyQt6 to query QScreen.
    """

    def __init__(
        self,
        screen_w: int,
        screen_h: int,
    ) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._seq: int = 0
        self._screen_w: int = screen_w
        self._screen_h: int = screen_h

        # Edge configuration. Default "right" matches the dropdown default.
        self._peer_edge: str = "right"

        # Callback to fire when the dwell threshold is met.
        self._edge_dwell_callback: EdgeDwellCallback | None = None

        # Paused flag: when True, deltas are not enqueued (TRANSITIONING state).
        # Edge checks still run; only queue delivery stops.
        self._paused: bool = False
        self._paused_lock = threading.Lock()

        # Cooldown: edge detection is suppressed until this monotonic timestamp.
        # Set to time.monotonic() + HANDOFF_COOLDOWN_S after every handoff landing.
        self._handoff_cooldown_until: float = 0.0
        self._cooldown_lock = threading.Lock()

        logger.info(
            "DeltaCapture initialized. screen: %dx%d",
            self._screen_w,
            self._screen_h,
        )

    # ------------------------------------------------------------------
    # Configuration setters (call before or during operation)
    # ------------------------------------------------------------------

    def set_peer_edge(self, edge: str) -> None:
        """
        Set which screen edge the peer is located at.

        Must be one of "right", "left", "top", "bottom". Changes take effect on
        the next poll tick; no restart required.
        """
        if edge not in VALID_EDGES:
            logger.warning("set_peer_edge: invalid edge %r -- keeping %r", edge, self._peer_edge)
            return
        self._peer_edge = edge
        logger.info("Peer edge set to: %s", edge)

    def set_edge_dwell_callback(self, callback: EdgeDwellCallback) -> None:
        """Register the function to call when an edge dwell completes."""
        self._edge_dwell_callback = callback

    def set_paused(self, paused: bool) -> None:
        """
        Pause or resume delta delivery.

        When paused=True, the poll loop skips enqueue_fn calls but continues
        polling and running edge-band checks. Used during TRANSITIONING so no
        stale deltas reach the peer after handoff fires.
        """
        with self._paused_lock:
            self._paused = paused
        logger.debug("DeltaCapture paused=%s", paused)

    def set_cooldown(self) -> None:
        """
        Set the handoff cooldown to now + HANDOFF_COOLDOWN_S.

        Call this every time a handoff lands (state transitions into CAPTURING
        via handoff_request_received) to suppress immediate re-trigger.
        """
        until = time.monotonic() + config.HANDOFF_COOLDOWN_S
        with self._cooldown_lock:
            self._handoff_cooldown_until = until
        logger.info(
            "Handoff cooldown set for %.2fs", config.HANDOFF_COOLDOWN_S
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, enqueue_fn: EnqueueFn) -> None:
        """
        Start the polling loop in a background daemon thread.

        enqueue_fn is called with each delta dict when not paused. It returns
        True if accepted, False if dropped due to backpressure.
        """
        self._stop_event.clear()
        with self._paused_lock:
            self._paused = False
        self._seq = 0
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(enqueue_fn,),
            daemon=True,
            name="delta-capture",
        )
        self._thread.start()
        logger.info("DeltaCapture started (peer_edge=%s)", self._peer_edge)

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("DeltaCapture stopped")

    # ------------------------------------------------------------------
    # Background poll loop
    # ------------------------------------------------------------------

    def _poll_loop(self, enqueue_fn: EnqueueFn) -> None:
        """
        Background thread: poll pyautogui.position() every CANVAS_REFRESH_MS.

        Each tick:
        1. Compute (dx_px, dy_px). If non-zero and not paused, normalize and enqueue.
        2. Run edge-band check if not in cooldown.
        3. Advance or reset dwell timer based on whether cursor is in the band.
        4. If dwell threshold met, fire the edge_dwell_callback.

        Normalization: ndx = dx_px / screen_w, ndy = dy_px / screen_h.
        Both numerator and denominator are in pyautogui's native unit on this
        platform, so the fraction is correct regardless of DPI mode.
        """
        import config as _cfg  # re-import inside thread for clarity on constants

        interval_s: float = _cfg.CANVAS_REFRESH_MS / 1000.0
        prev_x, prev_y = pyautogui.position()

        # Dwell tracking.
        dwell_start: float | None = None  # monotonic time when cursor entered the band

        while not self._stop_event.is_set():
            time.sleep(interval_s)

            cur_x, cur_y = pyautogui.position()
            dx_px = cur_x - prev_x
            dy_px = cur_y - prev_y

            # --- Delta delivery ---
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
                    delta: dict = {
                        "type": "delta",
                        "ndx": ndx,
                        "ndy": ndy,
                        "seq": self._seq,
                    }
                    accepted = enqueue_fn(delta)
                    if not accepted:
                        logger.debug("Delta seq=%d dropped (send queue full)", self._seq)

            # --- Edge-band detection (Step 6) ---
            now = time.monotonic()

            # Suppress edge checks while paused (TRANSITIONING) or within cooldown.
            with self._cooldown_lock:
                in_cooldown = now < self._handoff_cooldown_until
            with self._paused_lock:
                currently_paused = self._paused

            if currently_paused or in_cooldown:
                # Reset the dwell timer so it doesn't accumulate across suppressed ticks.
                if dwell_start is not None:
                    dwell_start = None
                    logger.debug("Edge dwell reset (paused=%s cooldown=%s)", currently_paused, in_cooldown)
                continue

            edge = self._peer_edge
            in_band = _in_edge_band(edge, cur_x, cur_y, self._screen_w, self._screen_h)

            logger.debug(
                "Edge check: edge=%s pos=(%d,%d) in_band=%s dwell_start=%s",
                edge, cur_x, cur_y, in_band, dwell_start,
            )

            if in_band:
                if dwell_start is None:
                    dwell_start = now
                    logger.debug("Entered %s edge band at pos=(%d,%d)", edge, cur_x, cur_y)
                elif now - dwell_start >= _cfg.EDGE_DWELL_S:
                    # Dwell threshold met -- fire handoff.
                    perp = _compute_perp(edge, cur_x, cur_y, self._screen_w, self._screen_h)
                    logger.info(
                        "Edge dwell complete: edge=%s perp=%.3f pos=(%d,%d)",
                        edge, perp, cur_x, cur_y,
                    )
                    dwell_start = None  # reset so we don't fire again immediately
                    if self._edge_dwell_callback is not None:
                        self._edge_dwell_callback(edge, perp)
            else:
                if dwell_start is not None:
                    logger.debug("Left %s edge band -- dwell timer reset", edge)
                    dwell_start = None
