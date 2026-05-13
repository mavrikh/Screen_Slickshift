"""
cursor-bridge-test -- switch state machine.

Exactly one machine owns input at any time. The state machine enforces this
invariant using acknowledge-based transitions: the host does not enter RECEIVING
state until the peer ACKs that it has entered CAPTURING state. On timeout, the
host rolls back to IDLE. This prevents double-cursor chaos and cursor-trap failure
modes described in brainstorm doc Section 3J.

State diagram (Step 6 -- edge-based handoff added):

    IDLE ──listen()──> LISTENING ──inbound connection──> HANDSHAKING
    IDLE ──connect()──> CONNECTING ──TCP connected──> HANDSHAKING
    HANDSHAKING ──hello received──> CONNECTED
    CONNECTED ──start_mirroring()──> CAPTURING
    CONNECTED ──mirror_start_received()──> RECEIVING
    CAPTURING ──edge dwell fires──> TRANSITIONING
    TRANSITIONING ──handoff_ack received──> RECEIVING
    TRANSITIONING ──ack timeout / disconnect──> CAPTURING (rollback) / IDLE
    RECEIVING ──handoff_request received──> CAPTURING (new sender, via handoff)
    CAPTURING ──stop_mirroring()──> CONNECTED
    RECEIVING ──mirror_stop_received()──> CONNECTED
    any active ──force_release() / disconnect──> IDLE
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

    # This machine is the active sender. Local cursor is visible and tracked.
    # Mouse deltas are being captured and forwarded to the peer via the send queue.
    # Reachable in Step 4 via start_mirroring().
    CAPTURING = "capturing"

    # This machine is the secondary receiver. Incoming deltas are logged and
    # used to mirror the sender's cursor motion on the in-app canvas square.
    # OS cursor is NOT moved yet (Step 5). Reachable in Step 4 via mirror_start_received().
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
    # Step 4 transitions -- one-way delta mirroring
    # ------------------------------------------------------------------

    def start_mirroring(self) -> None:
        """
        User clicked Start Mirroring on this machine while CONNECTED.

        Transitions to CAPTURING. The caller is responsible for sending
        {"type": "mirror_start"} to the peer so it enters RECEIVING.
        Only valid from CONNECTED state.
        """
        if self._state != SwitchState.CONNECTED:
            logger.warning(
                "start_mirroring() called in state %s -- ignored", self._state.value
            )
            return
        self._transition(SwitchState.CAPTURING)

    def stop_mirroring(self) -> None:
        """
        User clicked Stop Mirroring on this machine while CAPTURING.

        Transitions back to CONNECTED. The caller is responsible for sending
        {"type": "mirror_stop"} to the peer so it exits RECEIVING.
        Only valid from CAPTURING state.
        """
        if self._state != SwitchState.CAPTURING:
            logger.warning(
                "stop_mirroring() called in state %s -- ignored", self._state.value
            )
            return
        self._transition(SwitchState.CONNECTED)

    def mirror_start_received(self) -> None:
        """
        Peer sent {"type": "mirror_start"}. This machine becomes the receiver.

        Transitions to RECEIVING. Only valid from CONNECTED state.
        """
        if self._state != SwitchState.CONNECTED:
            logger.warning(
                "mirror_start_received() in state %s -- ignored", self._state.value
            )
            return
        self._transition(SwitchState.RECEIVING)

    def mirror_stop_received(self) -> None:
        """
        Peer sent {"type": "mirror_stop"}. Mirroring session is over.

        Transitions back to CONNECTED. Only valid from RECEIVING state.
        """
        if self._state != SwitchState.RECEIVING:
            logger.warning(
                "mirror_stop_received() in state %s -- ignored", self._state.value
            )
            return
        self._transition(SwitchState.CONNECTED)

    # ------------------------------------------------------------------
    # Step 6 transitions -- edge-based handoff
    # ------------------------------------------------------------------

    def begin_handoff(self) -> None:
        """
        Edge dwell fired: this machine (currently CAPTURING) wants to hand off
        control to the peer.

        Transitions CAPTURING -> TRANSITIONING. The caller is responsible for:
          1. Sending {"type": "handoff_request", "perp": ..., "sender_edge": ...}
          2. Starting the HANDOFF_ACK_TIMEOUT_S timer.
          3. Pausing the capture loop (no more deltas while TRANSITIONING).

        Only valid from CAPTURING state.
        """
        if self._state != SwitchState.CAPTURING:
            logger.warning(
                "begin_handoff() called in state %s -- ignored", self._state.value
            )
            return
        self._transition(SwitchState.TRANSITIONING)

    def handoff_ack_received(self) -> None:
        """
        Peer sent handoff_ack confirming it has entered CAPTURING (new sender).

        Transitions this machine TRANSITIONING -> RECEIVING. The original sender
        now becomes the receiver. The caller cancels the ack-timeout timer.

        Only valid from TRANSITIONING state.
        """
        if self._state != SwitchState.TRANSITIONING:
            logger.warning(
                "handoff_ack_received() called in state %s -- ignored", self._state.value
            )
            return
        self._transition(SwitchState.RECEIVING)

    def handoff_ack_timeout(self) -> None:
        """
        No handoff_ack arrived within HANDOFF_ACK_TIMEOUT_S.

        Rolls back TRANSITIONING -> CAPTURING so the user can continue moving
        the cursor locally. Logs at WARNING; this indicates a network hiccup or
        a slow peer.

        Only valid from TRANSITIONING state.
        """
        if self._state != SwitchState.TRANSITIONING:
            logger.warning(
                "handoff_ack_timeout() called in state %s -- ignored", self._state.value
            )
            return
        logger.warning("Handoff ACK timeout -- rolling back to CAPTURING")
        self._transition(SwitchState.CAPTURING)

    def handoff_request_received(self) -> None:
        """
        Peer (currently CAPTURING) sent a handoff_request to give control to us.

        Transitions this machine RECEIVING -> CAPTURING (we become the new sender).
        The caller is responsible for:
          1. Warping the OS cursor to the entry-edge position derived from the
             handoff_request payload.
          2. Sending {"type": "handoff_ack"} back to the peer.
          3. Starting the capture loop so deltas flow from this machine.
          4. Setting _handoff_cooldown_until so edge detection is suppressed.

        Only valid from RECEIVING state.
        """
        if self._state != SwitchState.RECEIVING:
            logger.warning(
                "handoff_request_received() called in state %s -- ignored",
                self._state.value,
            )
            return
        self._transition(SwitchState.CAPTURING)

    def heartbeat_timeout(self) -> None:
        """
        Called by the dead-man timer when no heartbeat has arrived within the window.

        Forces return to IDLE. Fires without any user action required.
        Dead-man switch: architecture invariant #4 in Slickshift CLAUDE.md.
        """
        logger.info("Heartbeat timeout -- dead-man switch fired, returning to IDLE")
        self._transition(SwitchState.IDLE)
