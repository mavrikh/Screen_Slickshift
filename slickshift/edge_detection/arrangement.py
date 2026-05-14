"""
Slickshift -- arrangement inversion logic and wire-format helpers.

An "arrangement" maps local monitor index (int) to the set of that monitor's
edges that are exit edges toward the peer (set[str]).  When machine A commits
an arrangement, it must send the geometric inverse to machine B so that B's
exit edges face back toward A consistently.

Single-monitor inversion table:

    local side  ->  peer side
    {0: {"right"}}          ->  {0: {"left"}}
    {0: {"left"}}           ->  {0: {"right"}}
    {0: {"top"}}            ->  {0: {"bottom"}}
    {0: {"bottom"}}         ->  {0: {"top"}}
    {0: {"right", "top"}}   ->  {0: {"left", "bottom"}}
    {0: {"right", "bottom"}} -> {0: {"left", "top"}}
    {0: {"left", "top"}}    ->  {0: {"right", "bottom"}}
    {0: {"left", "bottom"}} ->  {0: {"right", "top"}}

Multi-monitor inversion (unlocked mode):

    When the user has unlocked peer monitors and positioned them independently,
    the arrangement on machine A is {local_idx: {edges...}} for each local monitor
    that has an exit edge facing a peer monitor.  The inversion applies the
    opposite edge to each local_idx entry and maps the result to the same index
    on the peer's monitor list.  This is correct when the peer's monitor indices
    correspond to the same relative positions -- which is the natural case when
    both machines have monitors with identical layout indices.

    When the arrangement spans more local monitor indices than the peer has
    monitors (index out of range), those entries are dropped and a WARNING is
    logged.  The caller should use the returned arrangement as-is.

    Locked-mode multi-monitor (arrangement spans more than one index but no
    explicit per-monitor positioning was done) applies the same per-edge
    opposite, identical to the unlocked path.

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

    For each (monitor_idx, edges) pair in the arrangement, the inverted
    arrangement maps monitor_idx to the set of opposite edges:
        right -> left,  left -> right,  top -> bottom,  bottom -> top.

    The monitor index in the returned dict is the same as in the input. The
    caller is responsible for applying the result to the peer's monitor list.
    This works correctly when both machines number their monitors from 0 in the
    same relative order (which is the common case for the unlocked multi-monitor
    path introduced in Task #10).

    When an arrangement index is out of range for the peer's monitor list (i.e.,
    index >= peer_monitor_count), the entry is dropped and a WARNING is logged.
    This handles the case where the peer has fewer monitors than local.

    When all indices are dropped (no valid entries remain), the function falls
    back to {0: inverse_edges_of_first_entry} and logs a WARNING so the caller
    is informed that a best-effort result was used.

    Parameters
    ----------
    arrangement:
        Local arrangement dict, e.g. {0: {"right"}} or
        {0: {"right", "top"}, 1: {"right"}}.
    peer_monitor_count:
        Number of monitors on the peer machine. Indices >= this value are
        dropped.

    Returns
    -------
    dict[int, set[str]]
        Inverted arrangement suitable for passing to
        EdgeDetector.set_exit_edges() on the peer machine.
    """
    inverted: dict[int, set[str]] = {}
    dropped: list[int] = []

    for monitor_idx, edges in arrangement.items():
        if monitor_idx >= peer_monitor_count:
            dropped.append(monitor_idx)
            continue

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

    if dropped:
        logger.warning(
            "invert_arrangement: arrangement indices %s are out of range for "
            "peer (peer_monitor_count=%d) -- dropped from inverted result",
            dropped,
            peer_monitor_count,
        )

    if not inverted and arrangement:
        # Best-effort fallback: apply inverse of the first valid entry to index 0.
        first_edges = next(iter(arrangement.values()))
        fb_edges: set[str] = {
            _OPPOSITE_EDGE[e] for e in first_edges if e in _OPPOSITE_EDGE
        }
        if not fb_edges:
            fb_edges = {"left"}  # absolute last resort
        inverted = {0: fb_edges}
        logger.warning(
            "invert_arrangement: all entries dropped -- falling back to {0: %s}",
            fb_edges,
        )

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
