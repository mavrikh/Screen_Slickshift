"""
cursor-bridge-test -- switch state machine.

Exactly one machine owns input at any time. The state machine enforces this
invariant using acknowledge-based transitions: the host does not enter RECEIVING
state until the peer ACKs that it has entered CAPTURING state. On timeout, the
host rolls back to IDLE. This prevents double-cursor chaos and cursor-trap failure
modes described in brainstorm doc Section 3J.

State diagram for Step 3 (connection and handshake):

    IDLE ──listen()──> LISTENING ──inbound connection──> HANDSHAKING
    IDLE ──connect()──> CONNECTING ──TCP connected──> HANDSHAKING
    HANDSHAKING ──hello received──> CONNECTED
    CONNECTED ──close()──> IDLE
    any ──force_release()──> IDLE

CAPTURING and RECEIVING are defined but not yet reachable -- Step 4+.
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class SwitchState(enum.Enum):
    """All valid states of the KVM switch state machine."""

    # No connection. Local cursor is active. No forwarding.
    IDLE = "idle"

    # Bound and listening for an inbound TCP connection from the peer.
    LISTENING = "listening"

    # Attempting an outbound TCP connection to the peer.
    CONNECTING = "connecting"

    # TCP link is up. Waiting for both sides to complete the hello exchange.
    HANDSHAKING = "handshaking"

    # Hello exchange complete. Both machines know each other's display config.
    # Ready for Step 4+ (capture/injection) to be wired in.
    CONNECTED = "connected"

    # This machine is the active host. Local cursor is visible and tracked.
    # Mouse deltas are being captured and forwarded to the peer.
    # NOT YET REACHABLE -- wired in Step 4.
    CAPTURING = "capturing"

    # This machine is the secondary. Local cursor is hidden. Receiving and
    # injecting deltas from the peer.
    # NOT YET REACHABLE -- wired in Step 5.
    RECEIVING = "receiving"

    # Briefly indeterminate during handoff negotiation. ACK not yet received.
    # NOT YET REACHABLE -- wired in Step 6.
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

    def _transition(self, new_state: SwitchState) -> None:
        """Internal: update state and fire the registered callback."""
        old = self._state
        self._state = new_state
        logger.info("State: %s -> %s", old.value.upper(), new_state.value.upper())
        if self._on_state_change is not None:
            self._on_state_change(new_state)

    # ------------------------------------------------------------------
    # Step 3 transitions -- connection and handshake
    # ------------------------------------------------------------------

    def begin_listening(self) -> None:
        """
        Record that a TCP server has been bound and is waiting for a peer.

        Called by TcpTransport after start_server() succeeds.
        """
        self._transition(SwitchState.LISTENING)

    def begin_connecting(self) -> None:
        """
        Record that an outbound TCP connection attempt is in progress.

        Called by TcpTransport before connect_to_peer() starts.
        """
        self._transition(SwitchState.CONNECTING)

    def connection_established(self) -> None:
        """
        TCP link is up (either inbound accept or outbound connect succeeded).

        Transitions to HANDSHAKING. The hello exchange must complete before
        moving to CONNECTED -- this is the acknowledge-based discipline from
        the architecture invariants: do not call CONNECTED until the peer's
        hello arrives.
        """
        self._transition(SwitchState.HANDSHAKING)

    def handshake_complete(self) -> None:
        """
        Both sides have exchanged hello payloads. Connection is ready.

        Called after the local hello has been sent AND the peer's hello has
        been received and stored.
        """
        self._transition(SwitchState.CONNECTED)

    def connection_lost(self, reason: str = "connection closed") -> None:
        """
        TCP link dropped or hello exchange failed. Returns to IDLE.

        reason is logged at INFO level for the log panel.
        """
        logger.info("Connection lost: %s", reason)
        self._transition(SwitchState.IDLE)

    def force_release(self) -> None:
        """
        Immediately return to IDLE regardless of current state or network condition.

        This is the failsafe path. It fires on the release hotkey and must never
        block on network I/O. The transport layer closes the socket separately.
        """
        logger.info("Force release triggered -- returning to IDLE")
        self._transition(SwitchState.IDLE)

    # ------------------------------------------------------------------
    # Step 4+ transitions -- not yet reachable
    # ------------------------------------------------------------------

    def request_handoff_to_peer(self) -> None:
        """
        Begin handoff: this machine wishes to give control to the peer.

        Transitions to TRANSITIONING and sends a handoff request via transport.
        Rolls back to CAPTURING on ACK timeout.
        NOT YET IMPLEMENTED -- Step 6.
        """
        raise NotImplementedError

    def confirm_handoff_received(self) -> None:
        """
        Called when this machine receives a handoff request from the peer.

        Transitions to RECEIVING and sends ACK back to peer.
        NOT YET IMPLEMENTED -- Step 6.
        """
        raise NotImplementedError

    def peer_ack_received(self) -> None:
        """
        Called when the ACK arrives confirming the peer is now in RECEIVING state.

        Transitions this machine from TRANSITIONING to CAPTURING.
        NOT YET IMPLEMENTED -- Step 6.
        """
        raise NotImplementedError

    def heartbeat_timeout(self) -> None:
        """
        Called by the dead-man timer when no heartbeat has arrived within the window.

        Forces return to IDLE. Fires without any user action required.
        Dead-man switch: architecture invariant #4 in Slickshift CLAUDE.md.
        """
        logger.info("Heartbeat timeout -- dead-man switch fired, returning to IDLE")
        self._transition(SwitchState.IDLE)
