"""
Slickshift mainline -- switch state machine.

Single-user KVM model: the machine that clicks Start Mirroring is the session
master and remains the input source for the entire session. The peer is the
slave and never sources input. Edge crossings flip a "cursor side" flag in
MainWindow without swapping master/slave roles.

State semantics:
    CAPTURING -- this machine is the master. Mouse/keyboard motion sourced here.
    RECEIVING -- this machine is the slave. Injects events from the master.

Edge crossings briefly use TRANSITIONING as an ack-pending window. The state
returns to its pre-transition value on ack or on timeout (no role swap).

State diagram:

    IDLE ──listen()──> LISTENING ──inbound connection──> HANDSHAKING
    IDLE ──connect()──> CONNECTING ──TCP connected──> HANDSHAKING
    HANDSHAKING ──hello received──> CONNECTED
    CONNECTED ──start_mirroring()──> CAPTURING    (this machine is master)
    CONNECTED ──mirror_start_received()──> RECEIVING    (this machine is slave)
    CAPTURING ──master_begin_handoff()──> TRANSITIONING
    RECEIVING ──slave_begin_handoff()──> TRANSITIONING
    TRANSITIONING ──handoff_ack_received()──> CAPTURING or RECEIVING (whichever entered TRANSITIONING)
    TRANSITIONING ──handoff_ack_timeout()──> CAPTURING or RECEIVING (rollback to pre-transition state)
    CAPTURING ──stop_mirroring()──> CONNECTED
    RECEIVING ──mirror_stop_received()──> CONNECTED
    CAPTURING/RECEIVING ──remote drop──> RECONNECTING
    RECONNECTING ──reconnect succeeded──> CONNECTED
    RECONNECTING ──max attempts / user cancel──> IDLE
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
    # Ready for capture/injection to be wired in.
    CONNECTED = "connected"

    # This machine is the session master. Sources all mouse/keyboard input.
    # MouseCapture runs continuously here; exclusive mode toggles based on which
    # side of the edge the logical cursor is currently on.
    CAPTURING = "capturing"

    # This machine is the session slave. Injects deltas/clicks/keys from the
    # master. Never sources input. Never becomes master mid-session.
    RECEIVING = "receiving"

    # Briefly indeterminate during edge-crossing negotiation. ACK not yet
    # received. On ack or timeout, returns to whichever role state (CAPTURING
    # or RECEIVING) entered TRANSITIONING.
    TRANSITIONING = "transitioning"

    # Attempting automatic reconnect after a remote-initiated drop.
    # Not entered on user-initiated disconnects.
    RECONNECTING = "reconnecting"


class StateController:
    """Manages KVM switch state and guards all transitions."""

    def __init__(self) -> None:
        self._state: SwitchState = SwitchState.IDLE
        self._on_state_change: Callable[[SwitchState], None] | None = None
        # Tracks which role state entered TRANSITIONING so we can return to it
        # on ack or timeout. CAPTURING for master-initiated cursor cross,
        # RECEIVING for slave-initiated return.
        self._pre_transition_state: SwitchState | None = None

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
    # Connection and handshake transitions
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
        self._pre_transition_state = None
        self._transition(SwitchState.IDLE)

    def force_release(self) -> None:
        """
        Immediately return to IDLE regardless of current state or network condition.

        This is the failsafe path. It fires on the release hotkey and must never
        block on network I/O. The transport layer closes the socket separately.
        """
        logger.info("Force release triggered -- returning to IDLE")
        self._pre_transition_state = None
        self._transition(SwitchState.IDLE)

    # ------------------------------------------------------------------
    # One-way delta mirroring transitions
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
    # Edge-based handoff transitions
    # ------------------------------------------------------------------

    def master_begin_handoff(self) -> None:
        """
        Master's edge dwell fired: cursor crossing from this machine to the slave.

        Transitions CAPTURING -> TRANSITIONING. Caller stamps the pre-transition
        state so handoff_ack_received() / handoff_ack_timeout() can roll back.
        Only valid from CAPTURING.
        """
        if self._state != SwitchState.CAPTURING:
            logger.warning(
                "master_begin_handoff() called in state %s -- ignored", self._state.value
            )
            return
        self._pre_transition_state = SwitchState.CAPTURING
        self._transition(SwitchState.TRANSITIONING)

    def slave_begin_handoff(self) -> None:
        """
        Slave's edge dwell fired: cursor returning from this machine to the master.

        Transitions RECEIVING -> TRANSITIONING. Caller stamps the pre-transition
        state for rollback. Only valid from RECEIVING.
        """
        if self._state != SwitchState.RECEIVING:
            logger.warning(
                "slave_begin_handoff() called in state %s -- ignored", self._state.value
            )
            return
        self._pre_transition_state = SwitchState.RECEIVING
        self._transition(SwitchState.TRANSITIONING)

    def handoff_ack_received(self) -> None:
        """
        Peer acked the cursor crossing. Return to the pre-transition role state.

        Master rolls back to CAPTURING (and stays the master with cursor now on
        slave's side). Slave rolls back to RECEIVING (cursor now on master's
        side). Roles do not swap.
        """
        if self._state != SwitchState.TRANSITIONING:
            logger.warning(
                "handoff_ack_received() called in state %s -- ignored", self._state.value
            )
            return
        target = self._pre_transition_state or SwitchState.CAPTURING
        self._pre_transition_state = None
        self._transition(target)

    def handoff_ack_timeout(self) -> None:
        """
        No handoff_ack arrived within HANDOFF_ACK_TIMEOUT_S.

        Same target as handoff_ack_received() -- we roll back to the role state
        we came from. Cursor side is not flipped by the caller on timeout.
        """
        if self._state != SwitchState.TRANSITIONING:
            logger.warning(
                "handoff_ack_timeout() called in state %s -- ignored", self._state.value
            )
            return
        target = self._pre_transition_state or SwitchState.CAPTURING
        self._pre_transition_state = None
        logger.warning("Handoff ACK timeout -- rolling back to %s", target.value.upper())
        self._transition(target)

    def heartbeat_timeout(self) -> None:
        """
        Called by the dead-man timer when no heartbeat has arrived within the window.

        Forces return to IDLE. Fires without any user action required.
        Dead-man switch: architecture invariant #4 in Slickshift CLAUDE.md.
        """
        logger.info("Heartbeat timeout -- dead-man switch fired, returning to IDLE")
        self._transition(SwitchState.IDLE)

    # ------------------------------------------------------------------
    # Auto-reconnect transitions
    # ------------------------------------------------------------------

    def begin_reconnect(self, prev_role: SwitchState) -> None:
        """
        Enter RECONNECTING after a remote-initiated drop.

        prev_role is the state at time of drop (CAPTURING or RECEIVING) so the
        reconnect loop can restore the role on success. Only valid from CAPTURING
        or RECEIVING (including TRANSITIONING, which collapses to CAPTURING).

        Called by MainWindow when _user_initiated_disconnect is False.
        """
        logger.info(
            "Remote-initiated drop from %s -- entering RECONNECTING", prev_role.value.upper()
        )
        self._transition(SwitchState.RECONNECTING)

    def reconnect_succeeded(self) -> None:
        """
        TCP link restored and handshake completed. Transition RECONNECTING -> CONNECTED.

        Role restoration (CAPTURING or RECEIVING) happens in MainWindow after this
        transition fires, matching the normal connect path.

        Only valid from RECONNECTING state.
        """
        if self._state != SwitchState.RECONNECTING:
            logger.warning(
                "reconnect_succeeded() called in state %s -- ignored", self._state.value
            )
            return
        self._transition(SwitchState.CONNECTED)

    def reconnect_failed_finally(self) -> None:
        """
        All reconnect attempts exhausted. Return to IDLE.

        Only valid from RECONNECTING state.
        """
        if self._state != SwitchState.RECONNECTING:
            logger.warning(
                "reconnect_failed_finally() called in state %s -- ignored", self._state.value
            )
            return
        logger.info("Reconnect exhausted -- returning to IDLE")
        self._transition(SwitchState.IDLE)

    def reconnect_canceled(self) -> None:
        """
        User canceled the reconnect loop (Disconnect button or force-release hotkey).

        Only valid from RECONNECTING state.
        """
        if self._state != SwitchState.RECONNECTING:
            logger.warning(
                "reconnect_canceled() called in state %s -- ignored", self._state.value
            )
            return
        logger.info("Reconnect canceled by user -- returning to IDLE")
        self._transition(SwitchState.IDLE)
