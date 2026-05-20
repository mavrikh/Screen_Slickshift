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
- Step A (this commit): install the tap, run a CFRunLoop in a daemon thread,
  honor set_paused() by returning None when paused. No delta delivery yet.
- Step B: emit normalized deltas through delta_callback to the send path.
- Step C: drive EdgeDetector via apply_delta() from the tap callback.

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

        # Anchor point used by the warp-on-move suppression path. When paused,
        # the tap callback warps the cursor back to this CGPoint on every
        # mouse-motion event, which makes the cursor visually frozen even
        # though the physical mouse keeps moving and we keep getting deltas.
        # Captured at set_paused(True) under _paused_lock.
        self._anchor_point: Any = None

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

        Always restores cursor visibility and clears the warp anchor so a
        crash mid-pause cannot leave the system cursor hidden.
        """
        self._stop_event.set()

        # Restore cursor visibility unconditionally. If we hid it during a
        # pause, this pairs the hide/show ref count. If we did not hide it,
        # this would over-decrement, so guard with the flag.
        if self._cursor_hidden:
            Quartz.CGDisplayShowCursor(0)
            self._cursor_hidden = False
        with self._paused_lock:
            self._anchor_point = None

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
        Pause or resume capture.

        Suppression mechanism: warp-on-move. While paused, the tap callback
        warps the cursor back to the anchor position on every mouse-motion
        event. The cursor visually freezes; the physical mouse keeps moving
        and we keep getting deltas. The cursor is also hidden during pause
        so the user does not see it teleporting on each warp event.

        Why not CGAssociateMouseAndMouseCursorPosition: that API only works
        when called from the Cocoa main thread (NSApplication.run). A plain
        Python script does not have one. Warp-on-move is the FPS-game
        pattern and works from any thread because CGWarpMouseCursorPosition
        and CGDisplayHideCursor are thread-agnostic.
        """
        with self._paused_lock:
            self._paused = paused
            if paused:
                # Capture current cursor position via a synthetic empty event.
                # Avoids depending on pyautogui's coordinate translation in
                # the suppression hot path.
                probe = Quartz.CGEventCreate(None)
                self._anchor_point = Quartz.CGEventGetLocation(probe)
            else:
                self._anchor_point = None

        if paused and not self._cursor_hidden:
            Quartz.CGDisplayHideCursor(0)
            self._cursor_hidden = True
        elif not paused and self._cursor_hidden:
            Quartz.CGDisplayShowCursor(0)
            self._cursor_hidden = False

        logger.info(
            "set_paused(%s) -- anchor=%s, cursor_hidden=%s",
            paused,
            self._anchor_point,
            self._cursor_hidden,
        )

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
        # Disable the default 250ms post-warp event suppression. Without this,
        # CGWarpMouseCursorPosition (used by the warp-on-move freeze in the
        # callback) would silence delta events for a quarter second after each
        # warp, which would make the captured motion stutter horribly.
        Quartz.CGSetLocalEventsSuppressionInterval(0.0)

        # Use Quartz.CGEventMaskBit() rather than manual (1 << X) shifts. The
        # event-type constants are not always in the low bits of the mask word
        # in some pyobjc bridge versions; CGEventMaskBit is the canonical helper.
        event_mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventMouseMoved)
            | Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDragged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDragged)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDragged)
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
        move. That is the suppression mechanism the polling loop cannot do.

        Step A: cursor suppression via warp-on-move. While paused, the
        callback warps the cursor back to the anchor point on every motion
        event so the cursor visually freezes. Step B will add delta delivery
        to delta_callback gated by self._paused.

        Disabled-event handling: macOS may disable the tap if the callback
        runs too long. The tap delivers special event types
        kCGEventTapDisabledByTimeout / kCGEventTapDisabledByUserInput in
        those cases. We re-enable immediately, otherwise the tap stays dead.
        """
        capture = self
        paused_lock = self._paused_lock
        tap_disabled_by_timeout = Quartz.kCGEventTapDisabledByTimeout
        tap_disabled_by_user_input = Quartz.kCGEventTapDisabledByUserInput

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

            # Read pause state and anchor once under the lock so a transition
            # between paused and active cannot leave us with an inconsistent pair
            # (e.g. paused=True but anchor=None).
            with paused_lock:
                currently_paused = capture._paused
                anchor = capture._anchor_point

            # Warp-on-move suppression: while paused (cursor logically "on peer"),
            # slam the cursor back to the anchor on every motion event so it
            # stays visually frozen on the sender's display. The event is still
            # returned so the run loop stays healthy.
            if currently_paused and anchor is not None:
                Quartz.CGWarpMouseCursorPosition(anchor)

            # Step B -- delta delivery.
            # Only emit when NOT paused. Quartz delivers hardware-reported pixel
            # deltas via kCGMouseEventDeltaX / kCGMouseEventDeltaY. These are
            # integer-valued doubles but represent raw device counts before any
            # OS acceleration curve is applied, which is exactly what we want
            # for a KVM: the physical distance the mouse moved, not where macOS
            # decided to move the cursor.
            #
            # Normalization follows the same formula as the polling loop:
            #   ndx = dx_px / vd_w,  ndy = dy_px / vd_h
            # so the receiver can scale back up using its own virtual-desktop
            # dimensions without knowing the sender's screen resolution.
            if not currently_paused:
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

            return event

        return _callback
