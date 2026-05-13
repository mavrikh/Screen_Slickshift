"""
cursor-bridge-test -- switch state machine.

Exactly one machine owns input at any time. The state machine enforces this
invariant using acknowledge-based transitions: the host does not enter RECEIVING
state until the peer ACKs that it has entered CAPTURING state. On timeout, the
host rolls back to IDLE. This prevents double-cursor chaos and cursor-trap failure
modes described in brainstorm doc Section 3J.
"""

from __future__ import annotations

import enum
from collections.abc import Callable


class SwitchState(enum.Enum):
    """All valid states of the KVM switch state machine."""

    # This machine owns input. Local cursor is active. No forwarding.
    IDLE = "idle"

    # This machine is the active host. Local cursor is visible and tracked.
    # Mouse deltas are being captured and forwarded to the peer.
    CAPTURING = "capturing"

    # This machine is the secondary. Local cursor is hidden. Receiving and
    # injecting deltas from the peer.
    RECEIVING = "receiving"

    # Briefly indeterminate during handoff negotiation. ACK not yet received.
    TRANSITIONING = "transitioning"


class StateController:
    """Manages KVM switch state and guards all transitions."""

    def __init__(self) -> None:
        self._state: SwitchState = SwitchState.IDLE
        self._on_state_change: Callable[[SwitchState], None] | None = None

    @property
    def state(self) -> SwitchState:
        return self._state

    def register_state_change_callback(self, callback: Callable[[SwitchState], None]) -> None:
        """Register a callback that fires on every state transition."""
        self._on_state_change = callback

    def request_handoff_to_peer(self) -> None:
        """
        Begin handoff: this machine wishes to give control to the peer.

        Transitions to TRANSITIONING and sends a handoff request via transport.
        Rolls back to CAPTURING on ACK timeout.
        """
        raise NotImplementedError

    def confirm_handoff_received(self) -> None:
        """
        Called when this machine receives a handoff request from the peer.

        Transitions to RECEIVING and sends ACK back to peer.
        """
        raise NotImplementedError

    def force_release(self) -> None:
        """
        Immediately return to IDLE regardless of current state or network condition.

        This is the failsafe path. It fires on the release hotkey and must never
        block on network I/O.
        """
        raise NotImplementedError

    def peer_ack_received(self) -> None:
        """
        Called when the ACK arrives confirming the peer is now in RECEIVING state.

        Transitions this machine from TRANSITIONING to CAPTURING.
        """
        raise NotImplementedError

    def heartbeat_timeout(self) -> None:
        """
        Called by the dead-man timer when no heartbeat has arrived within the window.

        Forces return to IDLE. Fires without any user action required.
        """
        raise NotImplementedError
