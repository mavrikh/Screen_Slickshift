"""
macOS exclusive-mode mouse capture via CGEventTap at kCGHIDEventTap scope.

Why this exists:
- The polling MouseCapture (mouse_capture.py) reads cursor position via
  pyautogui.position() but cannot stop the OS from moving the local cursor.
  Once the user crosses the screen edge, the cursor keeps drifting on the
  sender's display while it should be visually "on the peer".
- A tap installed at kCGHIDEventTap intercepts mouse events before the OS
  delivers them to any process or to the system cursor compositor. Returning
  None from the callback drops the event entirely -- the local cursor does
  not move. Returning the event passes it through unchanged.

Phase 2 build order:
- Step A: install the tap, run a CFRunLoop in a daemon thread, honor
  set_paused() by returning None when paused. No delta delivery yet.
- Step B: emit normalized deltas through delta_callback to the send path.
- Step C: exclusive capture for motion. When paused, the callback returns
  None instead of returning the event unchanged, so the OS drops the
  motion event entirely and the local cursor never moves. The earlier
  warp-on-move attempt triggered macOS's shake-to-locate cursor and never
  actually froze anything; the None-return approach prevents motion from
  reaching the cursor in the first place.
- Step D (this commit): extend exclusive capture to click and scroll
  events. The same tap now intercepts kCGEventLeftMouseDown/Up,
  kCGEventRightMouseDown/Up, kCGEventOtherMouseDown/Up, and
  kCGEventScrollWheel. Click and scroll messages are forwarded to the
  send path via delta_callback (same wire format as the existing pynput
  path: {"type":"click","button","pressed"} and {"type":"scroll","dx","dy"}).
  When paused, the callback returns None for these too, so they no longer
  leak through to local apps at the physical HID device position. pynput's
  mouse listener becomes redundant on macOS and is no-op'd in EventCapture.

Permissions:
- Accessibility (System Settings > Privacy and Security > Accessibility)
  for the process running python.
- Input Monitoring (same path > Input Monitoring) for the same process.
- Both must be granted before kCGHIDEventTap will install. CGEventTapIsEnabled()
  returns False immediately after creation when either is missing.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

import Quartz

from slickshift.capture.mouse_capture import (
    DeltaCallback,
    MouseCapture,
    compute_virtual_desktop_bbox,
)
from slickshift.transport.topology import MonitorInfo

logger = logging.getLogger(__name__)


class MacMouseCapture(MouseCapture):
    """
    macOS implementation of MouseCapture using a CGEventTap.

    Public interface matches MouseCapture exactly: start(), stop(), set_paused().
    Subclassing the polling base lets us reuse the virtual-desktop bbox math and
    the seq counter; the polling _poll_loop is never started because start() is
    overridden.

    Threading model:
      - start() launches a daemon thread that owns a CFRunLoop and the tap.
      - The tap callback fires on that same daemon thread for every mouse event.
      - set_paused() flips a flag read by the tap callback under a lock.
      - stop() calls CFRunLoopStop() to break out of CFRunLoopRun(), disables
        the tap, and joins the thread.
    """

    def __init__(self, monitors: list[MonitorInfo]) -> None:
        super().__init__(monitors)
        self._tap_ref: Any = None
        self._run_loop_source: Any = None
        self._run_loop_ref: Any = None
        self._tap_thread: threading.Thread | None = None
        self._tap_ready = threading.Event()
        self._tap_install_ok: bool = False

        # Retain the callback closure on the instance so it isn't garbage-collected
        # after CGEventTapCreate returns. Pyobjc does not strongly retain the
        # Python callable passed to the tap; without this attribute the C run
        # loop ends up with a dangling reference and the callback silently
        # never fires (events pass through, cursor moves normally).
        self._tap_callback_ref: Any = None

        # Diagnostic counter for the tap callback. Used only by Step A's
        # smoke test to confirm the callback is actually being invoked.
        # Will be removed or downgraded to DEBUG once delta delivery lands.
        self._callback_invocation_count: int = 0

        # Tracks whether CGDisplayHideCursor has been called without a
        # paired CGDisplayShowCursor. The hide/show calls are ref-counted by
        # the OS, so this flag prevents leaving the system cursor permanently
        # hidden if stop() runs while paused.
        self._cursor_hidden: bool = False

    # ------------------------------------------------------------------
    # Lifecycle (overrides MouseCapture)
    # ------------------------------------------------------------------

    def start(self, delta_callback: DeltaCallback) -> None:
        """
        Install the CGEventTap and start the CFRunLoop in a daemon thread.

        Blocks up to 2 seconds waiting for the tap to be created and enabled.
        Logs a clear error if installation fails (typically Accessibility or
        Input Monitoring permission missing). Does not raise.

        Step A scope: delta_callback is accepted but not invoked. Step B will
        wire delta delivery.
        """
        self._stop_event.clear()
        with self._paused_lock:
            self._paused = False
        self._seq = 0
        self._tap_ready.clear()
        self._tap_install_ok = False

        self._tap_thread = threading.Thread(
            target=self._tap_loop,
            args=(delta_callback,),
            daemon=True,
            name="mac-event-tap",
        )
        self._tap_thread.start()

        if not self._tap_ready.wait(timeout=2.0):
            logger.error(
                "MacMouseCapture: tap thread did not signal ready within 2s. "
                "Tap installation may have failed silently."
            )
            return

        if not self._tap_install_ok:
            logger.error(
                "MacMouseCapture: tap installation failed. Confirm Accessibility "
                "and Input Monitoring are both granted to the process running "
                "python (System Settings > Privacy and Security)."
            )
            return

        logger.info("MacMouseCapture started (CGEventTap installed and enabled)")

    def stop(self) -> None:
        """Stop the tap thread and release the CFRunLoop. Idempotent.

        Always restores cursor visibility so a crash mid-pause cannot leave
        the system cursor hidden.
        """
        self._stop_event.set()

        # Restore cursor visibility unconditionally. If we hid it during a
        # pause, this pairs the hide/show ref count. If we did not hide it,
        # this would over-decrement, so guard with the flag.
        if self._cursor_hidden:
            Quartz.CGDisplayShowCursor(0)
            self._cursor_hidden = False

        if self._tap_ref is not None:
            Quartz.CGEventTapEnable(self._tap_ref, False)
        if self._run_loop_ref is not None:
            Quartz.CFRunLoopStop(self._run_loop_ref)

        if self._tap_thread is not None:
            self._tap_thread.join(timeout=1.0)
            self._tap_thread = None

        self._tap_ref = None
        self._run_loop_source = None
        self._run_loop_ref = None
        self._tap_install_ok = False
        logger.info("MacMouseCapture stopped")

    def set_paused(self, paused: bool) -> None:
        """
        Pause or resume forwarding. Two use cases:
          - Brief silence during a TRANSITIONING ack window so handoff
            handshakes do not dribble stale deltas.
          - Persistent "do not forward" while the master holds the cursor on
            its own screen.

        Effect on tap callback: while paused, motion / click / scroll
        deltas are NOT emitted to the peer. Local delivery is independent
        and governed by _exclusive -- so paused alone keeps events landing
        on the master's OS (cursor moves and clicks fire locally). Combine
        with _exclusive=True to also drop locally (legacy handoff silence).
        """
        with self._paused_lock:
            self._paused = paused
        self._update_cursor_visibility()
        logger.info(
            "set_paused(%s) -- cursor_hidden=%s", paused, self._cursor_hidden
        )

    def set_exclusive(self, exclusive: bool) -> None:
        """
        Engage or release exclusive mode on macOS.

        While exclusive: the tap callback continues to emit motion, click,
        and scroll messages to the peer (so the peer cursor moves and the
        peer machine sees the clicks), but returns None to drop the same
        events locally so the sender's apps never see them. The on-screen
        sender cursor is hidden. This is what is needed for "control the
        other machine without anything happening on the controller."
        """
        with self._paused_lock:
            self._exclusive = exclusive
        self._update_cursor_visibility()
        logger.info(
            "set_exclusive(%s) -- cursor_hidden=%s", exclusive, self._cursor_hidden
        )

    def _update_cursor_visibility(self) -> None:
        """
        Hide the cursor only while _exclusive is true. _paused means "do not
        emit deltas to the peer" -- it does not imply the cursor should hide.
        The master uses paused-without-exclusive to keep the local cursor
        visible while the cursor lives on the master's screen.

        CGDisplayHideCursor/ShowCursor are ref-counted by the OS; the
        _cursor_hidden flag guards against over-decrementing the count.
        """
        should_hide = self._exclusive
        if should_hide and not self._cursor_hidden:
            Quartz.CGDisplayHideCursor(0)
            self._cursor_hidden = True
        elif not should_hide and self._cursor_hidden:
            Quartz.CGDisplayShowCursor(0)
            self._cursor_hidden = False

    # ------------------------------------------------------------------
    # Background tap loop
    # ------------------------------------------------------------------

    def _tap_loop(self, delta_callback: DeltaCallback) -> None:
        """
        Background-thread entry point. Owns the CFRunLoop and the tap.

        Sequence:
          1. Build the event mask (mouse moved + dragged variants).
          2. Create the tap at kCGHIDEventTap.
          3. Enable the tap and verify CGEventTapIsEnabled() returns True.
          4. Add the tap as a CFRunLoop source on this thread's run loop.
          5. Signal _tap_ready and run CFRunLoopRun() until stop() breaks it.
          6. On exit: log + clean up are handled by stop() in the caller thread.
        """
        # Use Quartz.CGEventMaskBit() rather than manual (1 << X) shifts. The
        # event-type constants are not always in the low bits of the mask word
        # in some pyobjc bridge versions; CGEventMaskBit is the canonical helper.
        event_mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDragged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDragged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseUp)
            | Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
        )

        # Retain the callback on self before passing to pyobjc so the GC can't
        # collect it out from under the run loop. See _tap_callback_ref docstring.
        self._tap_callback_ref = self._make_tap_callback(delta_callback)

        # Install order matches Apple's canonical CGEventTap example:
        #   Create -> CreateRunLoopSource -> AddSource -> Enable -> RunLoopRun
        # Earlier draft enabled before adding the source, which works on some
        # macOS versions but is not the documented order.
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            event_mask,
            self._tap_callback_ref,
            None,
        )

        if tap is None:
            logger.error(
                "CGEventTapCreate returned None. The process likely lacks "
                "Accessibility or Input Monitoring permission. "
                "See module docstring for grant instructions."
            )
            self._tap_install_ok = False
            self._tap_ready.set()
            return

        self._tap_ref = tap
        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._run_loop_ref = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(
            self._run_loop_ref,
            self._run_loop_source,
            Quartz.kCFRunLoopCommonModes,
        )
        Quartz.CGEventTapEnable(tap, True)

        if not Quartz.CGEventTapIsEnabled(tap):
            logger.error(
                "CGEventTapIsEnabled returned False after enable. "
                "Permissions are likely missing. Tap will not fire."
            )
            self._tap_install_ok = False
            self._tap_ready.set()
            return

        logger.info(
            "CGEventTap installed and enabled. mask=0x%x scope=kCGHIDEventTap",
            event_mask,
        )
        self._tap_install_ok = True
        self._tap_ready.set()

        Quartz.CFRunLoopRun()

        logger.info("CGEventTap CFRunLoop exited")

    def _make_tap_callback(
        self, delta_callback: DeltaCallback
    ) -> Callable[[Any, Any, Any, Any], Any]:
        """
        Build the tap callback closure.

        The callback signature is fixed by Quartz:
            (proxy, event_type, event, refcon) -> CGEvent | None

        Returning the original event passes it through to the OS. Returning
        None drops the event before delivery -- the local cursor does not
        move and no application sees it.

        Event handling by branch:
        - Motion (kCGEventMouseMoved + the three drag variants): when paused,
          drop. When not paused, extract pixel deltas, normalize to vd
          fractions, and forward as {"type":"delta", ...}.
        - Click (left/right/middle Down/Up): when paused, drop. When not
          paused, map the event type to a button name + pressed state and
          forward as {"type":"click", "button", "pressed"}.
        - Scroll (kCGEventScrollWheel): when paused, drop. When not paused,
          read kCGScrollWheelEventDeltaAxis1/2 and forward as
          {"type":"scroll", "dx", "dy"}.

        The suppression-when-paused semantics is what makes exclusive mode
        work: clicks and scrolls no longer leak to local apps at the HID
        device position, and the cursor cannot reappear from interactive
        feedback. Returns None for everything captured when paused so the
        OS drops the entire event upstream of any other tap.

        Disabled-event handling: macOS may disable the tap if the callback
        runs too long. The tap delivers special event types
        kCGEventTapDisabledByTimeout / kCGEventTapDisabledByUserInput in
        those cases. We re-enable immediately, otherwise the tap stays dead.
        """
        capture = self
        paused_lock = self._paused_lock
        tap_disabled_by_timeout = Quartz.kCGEventTapDisabledByTimeout
        tap_disabled_by_user_input = Quartz.kCGEventTapDisabledByUserInput

        # Pre-compute event-type lookup tables and constants so the hot path
        # avoids repeated attribute access on the Quartz module.
        motion_types = (
            Quartz.kCGEventMouseMoved,
            Quartz.kCGEventLeftMouseDragged,
            Quartz.kCGEventRightMouseDragged,
            Quartz.kCGEventOtherMouseDragged,
        )
        # Left and right buttons map directly. "Other" buttons (middle, thumb)
        # are dispatched in a separate branch that reads the button-number
        # field to disambiguate.
        click_map = {
            Quartz.kCGEventLeftMouseDown: ("left", True),
            Quartz.kCGEventLeftMouseUp: ("left", False),
            Quartz.kCGEventRightMouseDown: ("right", True),
            Quartz.kCGEventRightMouseUp: ("right", False),
        }
        other_down = Quartz.kCGEventOtherMouseDown
        other_up = Quartz.kCGEventOtherMouseUp
        scroll_wheel = Quartz.kCGEventScrollWheel

        def _callback(proxy: Any, event_type: Any, event: Any, refcon: Any) -> Any:
            capture._callback_invocation_count += 1
            count = capture._callback_invocation_count
            if count == 1:
                logger.info("tap callback invoked for the first time (event_type=%s)", event_type)
            elif count % 500 == 0:
                logger.debug("tap callback invocation count=%d", count)

            if event_type in (tap_disabled_by_timeout, tap_disabled_by_user_input):
                logger.warning(
                    "Tap disabled by OS (event_type=%s). Re-enabling.", event_type
                )
                if capture._tap_ref is not None:
                    Quartz.CGEventTapEnable(capture._tap_ref, True)
                return event

            with paused_lock:
                currently_paused = capture._paused
                currently_exclusive = capture._exclusive

            # Two orthogonal gates:
            # - emit_to_peer: forward via delta_callback. Gated by NOT paused.
            #   Exclusive mode keeps forwarding enabled (that's the point --
            #   the peer still needs the events). Paused suppresses emit so
            #   handoff transitions don't dribble stale deltas, and so the
            #   master can hold the cursor on its own screen without forwarding.
            # - drop_locally: return None to drop the event before any local
            #   app sees it. Only exclusive triggers the drop; paused alone
            #   keeps motion landing on the local OS so the master cursor
            #   moves normally while paused-but-visible.
            emit_to_peer = not currently_paused
            drop_locally = currently_exclusive

            # MOTION events. Quartz delivers hardware-reported pixel deltas
            # via kCGMouseEventDeltaX / kCGMouseEventDeltaY -- raw device
            # counts before the OS acceleration curve.
            if event_type in motion_types:
                if emit_to_peer:
                    dx_px = Quartz.CGEventGetDoubleValueField(
                        event, Quartz.kCGMouseEventDeltaX
                    )
                    dy_px = Quartz.CGEventGetDoubleValueField(
                        event, Quartz.kCGMouseEventDeltaY
                    )
                    if dx_px != 0.0 or dy_px != 0.0:
                        ndx: float = dx_px / capture._vd_w
                        ndy: float = dy_px / capture._vd_h
                        logger.debug(
                            "Tap delta: raw=(%.1f,%.1f) vd=(%dx%d) n=(%.4f,%.4f)",
                            dx_px, dy_px, capture._vd_w, capture._vd_h, ndx, ndy,
                        )
                        capture._seq += 1
                        delta: dict = {
                            "type": "delta",
                            "ndx": ndx,
                            "ndy": ndy,
                            "seq": capture._seq,
                        }
                        accepted = delta_callback(delta)
                        if not accepted:
                            logger.debug(
                                "Tap delta seq=%d dropped (send queue full)", capture._seq
                            )
                if drop_locally:
                    return None
                return event

            # CLICK events for left and right buttons.
            click_info = click_map.get(event_type)
            if click_info is not None:
                if emit_to_peer:
                    button, pressed = click_info
                    logger.debug("Tap click: button=%s pressed=%s", button, pressed)
                    delta_callback(
                        {"type": "click", "button": button, "pressed": pressed}
                    )
                if drop_locally:
                    return None
                return event

            # CLICK events for "other" buttons. Read the button number to
            # disambiguate -- 2 is the middle button by convention. Higher
            # numbers (thumb buttons etc.) are not forwarded.
            if event_type == other_down or event_type == other_up:
                if emit_to_peer:
                    button_num = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGMouseEventButtonNumber
                    )
                    if button_num == 2:
                        pressed = event_type == other_down
                        logger.debug("Tap click: button=middle pressed=%s", pressed)
                        delta_callback(
                            {"type": "click", "button": "middle", "pressed": pressed}
                        )
                if drop_locally:
                    return None
                return event

            # SCROLL events. Axis1 is vertical (positive = up), Axis2 is
            # horizontal (positive = right) per Apple's Quartz docs.
            if event_type == scroll_wheel:
                if emit_to_peer:
                    dy_scroll = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGScrollWheelEventDeltaAxis1
                    )
                    dx_scroll = Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGScrollWheelEventDeltaAxis2
                    )
                    if dx_scroll != 0 or dy_scroll != 0:
                        logger.debug("Tap scroll: dx=%d dy=%d", dx_scroll, dy_scroll)
                        delta_callback(
                            {"type": "scroll", "dx": int(dx_scroll), "dy": int(dy_scroll)}
                        )
                if drop_locally:
                    return None
                return event

            # Unknown event type slipped through the mask -- pass through unchanged.
            return event

        return _callback
