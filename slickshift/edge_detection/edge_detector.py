"""
Slickshift mainline -- screen-edge band detection and dwell timer.

Extracted from DeltaCapture._poll_loop in the cursor-bridge-test scaffold.
The test app mixed edge detection logic into the capture polling loop. The
mainline separates the two concerns so they can be developed and tested
independently.

This module owns:
  - _in_edge_band_for_monitor(): geometric test for whether a position is in
    the edge band of a specific monitor.
  - _compute_perp_for_monitor(): normalized perpendicular coordinate at a given
    edge for a specific monitor.
  - EdgeDetector: the stateful dwell timer and cooldown guard.

DeltaCapture (in capture/mouse_capture.py, currently a stub) will call
EdgeDetector.tick() on each poll iteration, passing the current cursor position.
When the dwell threshold is met, EdgeDetector fires the registered callback.

Multi-monitor support (Task #6):
  EdgeDetector now accepts a list[MonitorInfo] at construction. The arrangement
  (which edges of which monitors are "exit edges" toward the peer) is configured
  via set_exit_edges(). The convenience method set_peer_edge() builds the default
  single-edge arrangement applied to monitor[0] only, preserving backward compat
  for single-monitor setups and all pre-Task-7 callers.

  Task #7 (screen placement UI) will supply real arrangements via set_exit_edges()
  once the user has configured which local edges face the peer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from slickshift import config
from slickshift.transport.topology import MonitorInfo

logger = logging.getLogger(__name__)

# Valid edge names (canonical strings used throughout the codebase).
VALID_EDGES: frozenset[str] = frozenset({"right", "left", "top", "bottom"})

# Type alias for the edge-dwell callback.
# Arguments: sender_edge (str), perp (float 0.0-1.0).
EdgeDwellCallback = Callable[[str, float], None]


def _in_edge_band_for_monitor(
    edge: str,
    x: int,
    y: int,
    monitor: MonitorInfo,
) -> bool:
    """
    Return True if (x, y) is within EDGE_BAND_PX of the named edge of monitor.

    All coordinates are in virtual-desktop logical pixels. The monitor's own
    position and size are used for the band geometry, so each monitor in a
    multi-monitor setup is tested independently against its own boundaries.

    "left"   -- x < monitor.x + EDGE_BAND_PX
    "right"  -- x >= monitor.x + monitor.width - EDGE_BAND_PX
    "top"    -- y < monitor.y + EDGE_BAND_PX
    "bottom" -- y >= monitor.y + monitor.height - EDGE_BAND_PX
    """
    band = config.EDGE_BAND_PX
    if edge == "left":
        return x < monitor.x + band
    if edge == "right":
        return x >= monitor.x + monitor.width - band
    if edge == "top":
        return y < monitor.y + band
    if edge == "bottom":
        return y >= monitor.y + monitor.height - band
    return False


def _compute_perp_for_monitor(
    edge: str,
    x: int,
    y: int,
    monitor: MonitorInfo,
) -> float:
    """
    Compute the normalized perpendicular coordinate at a given edge of monitor.

    For left/right edges the perpendicular axis is Y:
        return (y - monitor.y) / monitor.height
    For top/bottom edges the perpendicular axis is X:
        return (x - monitor.x) / monitor.width

    Clamped to [0.0, 1.0]. The result is a position along that edge normalized
    to the monitor's own extent, not the full virtual desktop.
    """
    if edge in ("left", "right"):
        if monitor.height <= 0:
            return 0.0
        return max(0.0, min(1.0, (y - monitor.y) / monitor.height))
    if monitor.width <= 0:
        return 0.0
    return max(0.0, min(1.0, (x - monitor.x) / monitor.width))


def _monitor_contains(x: int, y: int, monitor: MonitorInfo) -> bool:
    """
    Return True if (x, y) lies within the bounds of monitor.

    Uses half-open intervals [x, x+w) and [y, y+h) matching the virtual-desktop
    coordinate convention (a point exactly on the right/bottom boundary belongs
    to the next monitor if one exists, not this one).
    """
    return (
        monitor.x <= x < monitor.x + monitor.width
        and monitor.y <= y < monitor.y + monitor.height
    )


class EdgeDetector:
    """
    Stateful screen-edge dwell detector with per-monitor geometry support.

    On each poll tick, the caller passes the current cursor position to tick().
    If the cursor has remained within EDGE_BAND_PX of a configured exit edge
    for EDGE_DWELL_S seconds continuously, the registered on_edge_dwell callback
    fires with (edge, perp) arguments.

    The detector includes a cooldown guard: call set_cooldown() after every
    handoff landing to suppress re-trigger for HANDOFF_COOLDOWN_S. This prevents
    the newly warped cursor from immediately re-triggering at the entry edge.

    Multi-monitor arrangement:
        An "arrangement" maps monitor index (into the monitors list) to the set
        of that monitor's edges that are exit edges toward the peer. Task #7 will
        supply real arrangements via set_exit_edges() once the screen placement UI
        is built. Until then, set_peer_edge() builds a default single-edge
        arrangement on monitor[0] for backward compatibility.

    Thread safety: tick() and set_cooldown() are expected to be called from
    the same background polling thread. Paused state is passed in per-call to
    tick() rather than stored on the instance, so no internal lock is needed.

    Lifecycle:
        monitors = enumerate_local_monitors()
        ed = EdgeDetector(monitors)
        ed.set_peer_edge("right")           # single-edge convenience (default)
        ed.register_callback(my_fn)
        # on each poll tick, from the capture thread:
        ed.tick(cur_x, cur_y, paused=False)
        # on handoff landing:
        ed.set_cooldown()

    Task #7 usage (once screen placement UI is ready):
        ed.set_exit_edges({0: {"right"}, 1: {"right"}})
        # monitor 0 and monitor 1 both exit at the right edge
    """

    def __init__(self, monitors: list[MonitorInfo]) -> None:
        # Store monitors. Fallback: if empty, synthesize a 1920x1080 primary
        # at (0,0) so the detector never fails on zero-monitor edge cases.
        if monitors:
            self._monitors: list[MonitorInfo] = list(monitors)
        else:
            logger.warning(
                "EdgeDetector: received empty monitor list -- "
                "synthesizing a 1920x1080 primary at (0,0)"
            )
            self._monitors = [
                MonitorInfo(x=0, y=0, width=1920, height=1080, dpi_scale=1.0, is_primary=True)
            ]

        # Default arrangement: the canonical single peer edge applied to monitor[0].
        # This is what set_peer_edge() builds and is equivalent to the pre-Task-6
        # single-monitor EdgeDetector behavior.
        self._peer_edge: str = "right"
        self._arrangement: dict[int, set[str]] = {0: {"right"}}

        self._callback: EdgeDwellCallback | None = None

        # Monotonic timestamp when cursor entered the current edge band.
        # None when the cursor is not in the band.
        self._dwell_start: float | None = None

        # The (monitor_index, edge) pair that started the current dwell.
        # Resets alongside _dwell_start whenever the cursor leaves the band.
        self._dwell_monitor_idx: int | None = None
        self._dwell_edge: str | None = None

        # Edge detection is suppressed until this monotonic timestamp.
        self._cooldown_until: float = 0.0

        primary = next((m for m in self._monitors if m.is_primary), self._monitors[0])
        logger.info(
            "EdgeDetector initialized. %d monitor(s). Primary: %dx%d at (%d,%d)",
            len(self._monitors),
            primary.width,
            primary.height,
            primary.x,
            primary.y,
        )

    # ------------------------------------------------------------------
    # Arrangement configuration
    # ------------------------------------------------------------------

    def set_peer_edge(self, edge: str) -> None:
        """
        Convenience method: configure a single exit edge on monitor[0].

        Builds the arrangement {0: {edge}} and stores the edge string for
        logging. Preserved for backward compatibility with all pre-Task-7
        callers. Single-monitor setups see identical behavior to before this
        refactor.

        Must be one of "right", "left", "top", "bottom". Invalid values are
        ignored (prior edge is kept).
        """
        if edge not in VALID_EDGES:
            logger.warning("set_peer_edge: invalid edge %r -- keeping %r", edge, self._peer_edge)
            return
        self._peer_edge = edge
        self._arrangement = {0: {edge}}
        # Reset dwell so a stale band from the old edge doesn't carry over.
        self._dwell_start = None
        self._dwell_monitor_idx = None
        self._dwell_edge = None
        logger.info("EdgeDetector peer edge set to: %s (arrangement: {0: {%r}})", edge, edge)

    def set_exit_edges(self, arrangement: dict[int, set[str]]) -> None:
        """
        Configure the full multi-monitor exit-edge arrangement.

        arrangement maps monitor index (into the list passed at construction)
        to a set of exit edge strings for that monitor. Only indices present in
        the dict are checked; monitors not in the dict have no exit edges.

        Example:
            # Monitor 0 exits right; monitor 1 exits right and bottom.
            ed.set_exit_edges({0: {"right"}, 1: {"right", "bottom"}})

        Invalid edge strings in any set are silently skipped.
        Invalid monitor indices (out of range for self._monitors) are silently
        skipped (they may correspond to monitors that have been disconnected).

        Calling this resets the current dwell timer so a stale band does not
        carry over to the new arrangement.

        This API is the integration point for Task #7 (screen placement UI).
        Until Task #7 lands, callers should use set_peer_edge() which builds
        the correct default arrangement automatically.
        """
        cleaned: dict[int, set[str]] = {}
        for idx, edges in arrangement.items():
            if idx < 0 or idx >= len(self._monitors):
                logger.debug(
                    "set_exit_edges: monitor index %d out of range (%d monitors) -- skipped",
                    idx,
                    len(self._monitors),
                )
                continue
            valid = {e for e in edges if e in VALID_EDGES}
            if valid:
                cleaned[idx] = valid
        self._arrangement = cleaned
        # Reset dwell.
        self._dwell_start = None
        self._dwell_monitor_idx = None
        self._dwell_edge = None
        logger.info("EdgeDetector exit-edge arrangement updated: %s", self._arrangement)

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

    # ------------------------------------------------------------------
    # Per-tick detection
    # ------------------------------------------------------------------

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

        Multi-monitor behavior:
          1. Determine which monitor contains (cur_x, cur_y).
          2. Check whether that monitor has any exit edges in the arrangement.
          3. For each exit edge of that monitor, check if the cursor is in the
             edge band.
          4. If in a band: start or advance the dwell timer.
          5. If dwell threshold met: fire the callback with (edge, perp) where
             perp is normalized to that monitor's own extent.

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
                self._dwell_monitor_idx = None
                self._dwell_edge = None
                logger.debug(
                    "EdgeDetector dwell reset (paused=%s cooldown=%s)", paused, in_cooldown
                )
            return

        # Find the monitor that contains the cursor.
        containing_monitor_idx: int | None = None
        for idx, monitor in enumerate(self._monitors):
            if _monitor_contains(cur_x, cur_y, monitor):
                containing_monitor_idx = idx
                break

        # If no monitor contains the cursor (e.g. cursor is in a gap between monitors
        # on some OS setups), fall back to monitor 0 to avoid dropping edge detection
        # entirely. This is rare but not impossible on unusual display configurations.
        if containing_monitor_idx is None:
            containing_monitor_idx = 0
            logger.debug(
                "EdgeDetector: cursor (%d,%d) not in any monitor -- defaulting to monitor 0",
                cur_x,
                cur_y,
            )

        monitor = self._monitors[containing_monitor_idx]

        # Get the exit edges for this monitor from the arrangement.
        exit_edges: set[str] = self._arrangement.get(containing_monitor_idx, set())

        # Find the first active exit edge the cursor is in the band for.
        active_edge: str | None = None
        for edge in exit_edges:
            if _in_edge_band_for_monitor(edge, cur_x, cur_y, monitor):
                active_edge = edge
                break

        logger.debug(
            "EdgeDetector tick: monitor=%d pos=(%d,%d) exit_edges=%s active_edge=%s dwell_start=%s",
            containing_monitor_idx,
            cur_x,
            cur_y,
            exit_edges,
            active_edge,
            self._dwell_start,
        )

        if active_edge is not None:
            # Cursor is in an exit band.
            if self._dwell_start is None:
                # Entering the band for the first time.
                self._dwell_start = now
                self._dwell_monitor_idx = containing_monitor_idx
                self._dwell_edge = active_edge
                logger.debug(
                    "EdgeDetector entered %s edge band on monitor %d at pos=(%d,%d)",
                    active_edge,
                    containing_monitor_idx,
                    cur_x,
                    cur_y,
                )
            elif (
                containing_monitor_idx == self._dwell_monitor_idx
                and active_edge == self._dwell_edge
                and now - self._dwell_start >= config.EDGE_DWELL_S
            ):
                # Dwell threshold met.
                perp = _compute_perp_for_monitor(active_edge, cur_x, cur_y, monitor)
                logger.info(
                    "EdgeDetector dwell complete: monitor=%d edge=%s perp=%.3f pos=(%d,%d)",
                    containing_monitor_idx,
                    active_edge,
                    perp,
                    cur_x,
                    cur_y,
                )
                self._dwell_start = None
                self._dwell_monitor_idx = None
                self._dwell_edge = None
                if self._callback is not None:
                    self._callback(active_edge, perp)
            elif (
                containing_monitor_idx != self._dwell_monitor_idx
                or active_edge != self._dwell_edge
            ):
                # Cursor moved to a different monitor or edge -- restart the dwell.
                self._dwell_start = now
                self._dwell_monitor_idx = containing_monitor_idx
                self._dwell_edge = active_edge
                logger.debug(
                    "EdgeDetector dwell restarted: monitor=%d edge=%s",
                    containing_monitor_idx,
                    active_edge,
                )
        else:
            # Cursor is not in any exit band.
            if self._dwell_start is not None:
                logger.debug(
                    "EdgeDetector left edge band (was monitor=%d edge=%s) -- dwell timer reset",
                    self._dwell_monitor_idx,
                    self._dwell_edge,
                )
                self._dwell_start = None
                self._dwell_monitor_idx = None
                self._dwell_edge = None
