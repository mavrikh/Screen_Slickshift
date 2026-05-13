"""
Slickshift mainline -- screen-edge band detection and dwell timer.

Extracted from DeltaCapture._poll_loop in the cursor-bridge-test scaffold.
The test app mixed edge detection logic into the capture polling loop. The
mainline separates the two concerns so they can be developed and tested
independently.

This module owns:
  - _in_edge_band(): geometric test for whether a position is in the edge band.
  - _compute_perp(): normalized perpendicular coordinate at a given edge.
  - EdgeDetector: the stateful dwell timer and cooldown guard.

DeltaCapture (in capture/mouse_capture.py, currently a stub) will call
EdgeDetector.tick() on each poll iteration, passing the current cursor position.
When the dwell threshold is met, EdgeDetector fires the registered callback.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from slickshift import config

logger = logging.getLogger(__name__)

# Valid edge names (canonical strings used throughout the codebase).
VALID_EDGES: frozenset[str] = frozenset({"right", "left", "top", "bottom"})

# Type alias for the edge-dwell callback.
# Arguments: sender_edge (str), perp (float 0.0-1.0).
EdgeDwellCallback = Callable[[str, float], None]


def _in_edge_band(edge: str, x: int, y: int, screen_w: int, screen_h: int) -> bool:
    """
    Return True if (x, y) is within EDGE_BAND_PX of the named edge boundary.

    "left"   -- x < EDGE_BAND_PX
    "right"  -- x >= screen_w - EDGE_BAND_PX
    "top"    -- y < EDGE_BAND_PX
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


class EdgeDetector:
    """
    Stateful screen-edge dwell detector.

    On each poll tick, the caller passes the current cursor position to tick().
    If the cursor has remained within EDGE_BAND_PX of the configured peer edge
    for EDGE_DWELL_S seconds continuously, the registered on_edge_dwell callback
    fires with (edge, perp) arguments.

    The detector includes a cooldown guard: call set_cooldown() after every
    handoff landing to suppress re-trigger for HANDOFF_COOLDOWN_S. This prevents
    the newly warped cursor from immediately re-triggering at the entry edge.

    Thread safety: tick() and set_cooldown() are expected to be called from
    the same background polling thread. Paused state is passed in per-call to
    tick() rather than stored on the instance, so no internal lock is needed.

    Lifecycle:
        ed = EdgeDetector(screen_w, screen_h)
        ed.set_peer_edge("right")
        ed.register_callback(my_fn)
        # on each poll tick, from the capture thread:
        ed.tick(cur_x, cur_y, paused=False)
        # on handoff landing:
        ed.set_cooldown()
    """

    def __init__(self, screen_w: int, screen_h: int) -> None:
        self._screen_w: int = screen_w
        self._screen_h: int = screen_h
        self._peer_edge: str = "right"
        self._callback: EdgeDwellCallback | None = None

        # Monotonic timestamp when cursor entered the current edge band.
        # None when the cursor is not in the band.
        self._dwell_start: float | None = None

        # Edge detection is suppressed until this monotonic timestamp.
        self._cooldown_until: float = 0.0

        logger.info("EdgeDetector initialized. screen: %dx%d", screen_w, screen_h)

    def set_peer_edge(self, edge: str) -> None:
        """
        Set which screen edge the peer is located at.

        Must be one of "right", "left", "top", "bottom". Changes take effect on
        the next tick() call; no restart required.
        """
        if edge not in VALID_EDGES:
            logger.warning("set_peer_edge: invalid edge %r -- keeping %r", edge, self._peer_edge)
            return
        self._peer_edge = edge
        logger.info("EdgeDetector peer edge set to: %s", edge)

    def register_callback(self, callback: EdgeDwellCallback) -> None:
        """Register the function to call when an edge dwell completes."""
        self._callback = callback

    def set_cooldown(self) -> None:
        """
        Set the handoff cooldown to now + HANDOFF_COOLDOWN_S.

        Call this every time a handoff lands (state transitions into CAPTURING
        via handoff_request_received) to suppress immediate re-trigger.
        """
        self._cooldown_until = time.monotonic() + config.HANDOFF_COOLDOWN_S
        logger.info("EdgeDetector cooldown set for %.2fs", config.HANDOFF_COOLDOWN_S)

    def tick(self, cur_x: int, cur_y: int, paused: bool) -> None:
        """
        Advance the dwell timer for the current cursor position.

        Called from the capture polling thread on every tick, regardless of
        whether delta delivery is paused. Edge detection is always driven by
        real cursor position so the rollback path can still poll state during
        TRANSITIONING.

        paused=True means the capture loop has suppressed delta delivery
        (TRANSITIONING state). Edge detection is also suppressed while paused
        so the dwell timer does not advance during the handoff window.

        When the dwell threshold is met, the registered callback fires with
        (edge, perp) and the dwell timer resets so the callback does not
        fire again immediately.
        """
        now = time.monotonic()
        in_cooldown = now < self._cooldown_until

        if paused or in_cooldown:
            # Reset the dwell timer so it does not accumulate across suppressed ticks.
            if self._dwell_start is not None:
                self._dwell_start = None
                logger.debug(
                    "EdgeDetector dwell reset (paused=%s cooldown=%s)", paused, in_cooldown
                )
            return

        edge = self._peer_edge
        in_band = _in_edge_band(edge, cur_x, cur_y, self._screen_w, self._screen_h)

        logger.debug(
            "EdgeDetector tick: edge=%s pos=(%d,%d) in_band=%s dwell_start=%s",
            edge, cur_x, cur_y, in_band, self._dwell_start,
        )

        if in_band:
            if self._dwell_start is None:
                self._dwell_start = now
                logger.debug("EdgeDetector entered %s edge band at pos=(%d,%d)", edge, cur_x, cur_y)
            elif now - self._dwell_start >= config.EDGE_DWELL_S:
                perp = _compute_perp(edge, cur_x, cur_y, self._screen_w, self._screen_h)
                logger.info(
                    "EdgeDetector dwell complete: edge=%s perp=%.3f pos=(%d,%d)",
                    edge, perp, cur_x, cur_y,
                )
                self._dwell_start = None  # reset so callback does not fire again immediately
                if self._callback is not None:
                    self._callback(edge, perp)
        else:
            if self._dwell_start is not None:
                logger.debug("EdgeDetector left %s edge band -- dwell timer reset", edge)
                self._dwell_start = None
