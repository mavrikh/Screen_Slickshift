"""
Slickshift -- arrangement inversion logic and wire-format helpers.

An "arrangement" maps local monitor index (int) to the set of that monitor's
edges that are exit edges toward the peer (set[str]).  When machine A commits
an arrangement, it must send the geometric inverse to machine B so that B's
exit edges face back toward A consistently.

Single-monitor inversion table (the only case handled precisely here):

    local side  ->  peer side
    {0: {"right"}}          ->  {0: {"left"}}
    {0: {"left"}}           ->  {0: {"right"}}
    {0: {"top"}}            ->  {0: {"bottom"}}
    {0: {"bottom"}}         ->  {0: {"top"}}
    {0: {"right", "top"}}   ->  {0: {"left", "bottom"}}
    {0: {"right", "bottom"}} -> {0: {"left", "top"}}
    {0: {"left", "top"}}    ->  {0: {"right", "bottom"}}
    {0: {"left", "bottom"}} ->  {0: {"right", "top"}}

Multi-monitor inversion (peer_monitor_count > 1 OR arrangement spans more than
one monitor index) is deferred to Task #9.  For now the simple per-edge
opposite is applied to each monitor index independently, a warning is logged,
and the caller proceeds.  The result is good enough to unblock testing on
single-monitor setups.

Wire format helpers:

    arrangement_to_wire(arrangement)   -> dict[str, list[str]]
    wire_to_arrangement(wire)          -> dict[int, set[str]]

The wire format uses JSON-safe types: string keys (str(monitor_index)) and
lists of edge strings instead of sets.  Conversion happens only at the
network boundary.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Opposite edge mapping used by invert_arrangement().
_OPPOSITE_EDGE: dict[str, str] = {
    "right": "left",
    "left": "right",
    "top": "bottom",
    "bottom": "top",
}


def invert_arrangement(
    arrangement: dict[int, set[str]],
    peer_monitor_count: int,
) -> dict[int, set[str]]:
    """
    Compute the geometric inverse of a local arrangement for the peer machine.

    For a single-monitor setup (one monitor index in arrangement AND
    peer_monitor_count == 1) this applies the simple opposite-edge table:
        right <-> left,  top <-> bottom.

    For multi-monitor cases (arrangement spans more than one index, or
    peer_monitor_count > 1) the same per-edge opposite is applied to each
    index independently.  A WARNING is logged so Task #9 can pick up real
    cases that need proper cross-monitor inversion.

    The returned arrangement uses the same monitor index space as the input.
    That is, monitor 0 in the input maps to monitor 0 in the output.  The
    caller is responsible for applying the result to the peer's own monitor
    list (which may number differently -- Task #9 handles remapping).

    Parameters
    ----------
    arrangement:
        Local arrangement dict, e.g. {0: {"right"}} or {0: {"right", "top"}}.
    peer_monitor_count:
        Number of monitors on the peer machine.  Used only to decide whether
        to emit the multi-monitor warning.

    Returns
    -------
    dict[int, set[str]]
        Inverted arrangement suitable for passing to
        EdgeDetector.set_exit_edges() on the peer machine.
    """
    local_monitor_count = len(arrangement)
    multi_monitor = local_monitor_count > 1 or peer_monitor_count > 1

    if multi_monitor:
        logger.warning(
            "invert_arrangement: multi-monitor case detected "
            "(local_monitors_in_arrangement=%d, peer_monitor_count=%d). "
            "Applying simple per-edge inversion -- Task #9 will handle "
            "proper cross-monitor remapping.",
            local_monitor_count,
            peer_monitor_count,
        )

    inverted: dict[int, set[str]] = {}
    for monitor_idx, edges in arrangement.items():
        inv_edges: set[str] = set()
        for edge in edges:
            opposite = _OPPOSITE_EDGE.get(edge)
            if opposite is not None:
                inv_edges.add(opposite)
            else:
                logger.warning(
                    "invert_arrangement: unknown edge %r for monitor %d -- skipped",
                    edge,
                    monitor_idx,
                )
        if inv_edges:
            inverted[monitor_idx] = inv_edges

    logger.debug(
        "invert_arrangement: %s -> %s (peer_monitor_count=%d)",
        arrangement,
        inverted,
        peer_monitor_count,
    )
    return inverted


# ---------------------------------------------------------------------------
# Wire-format helpers
# ---------------------------------------------------------------------------


def arrangement_to_wire(arrangement: dict[int, set[str]]) -> dict[str, list[str]]:
    """
    Serialize an arrangement to a JSON-safe wire dict.

    Converts int keys to str and set values to sorted lists so the output is
    stable and JSON-serializable.

    Example:
        {0: {"right", "top"}}  ->  {"0": ["right", "top"]}
    """
    return {str(k): sorted(v) for k, v in arrangement.items()}


def wire_to_arrangement(wire: dict[str, list[str]]) -> dict[int, set[str]]:
    """
    Deserialize a wire dict back to an arrangement.

    Converts str keys to int and list values to sets.  Invalid or
    non-integer keys are silently skipped.

    Example:
        {"0": ["right", "top"]}  ->  {0: {"right", "top"}}
    """
    result: dict[int, set[str]] = {}
    for k, v in wire.items():
        try:
            idx = int(k)
        except (ValueError, TypeError):
            logger.warning(
                "wire_to_arrangement: non-integer key %r -- skipped", k
            )
            continue
        result[idx] = set(v)
    return result
