"""
Slickshift mainline -- mouse position delta capture (STUB).

Production implementation is CGEventTap on macOS and RawInput on Windows.
pyautogui polling is NOT used here.

Why this is a stub:

The cursor-bridge-test scaffold used pyautogui.position() at ~60 Hz as a passive
poll. That approach cannot suppress local cursor delivery during forwarding: while
the sender's cursor is being tracked and forwarded, it still drives the sender's
own apps. For production Slickshift this is unacceptable.

The correct capture path per platform:
  - macOS: CGEventTap at kCGHIDEventTap scope via the Quartz bindings in
    PyObjC. Fires before the OS delivers the event to any application.
    Requires Accessibility permission (AXIsProcessTrustedWithOptions).
  - Windows: RawInput with the RIDEV_NOLEGACY flag via ctypes. Receives raw
    device input before OS cursor processing, allowing the application to
    suppress normal cursor movement.

Until one of those is implemented, this file defines the interface only.
The rest of the mainline (EdgeDetector, TcpTransport, StateController,
MouseInjector, EventCapture) is fully wired and functional. Only this
module is missing a real body.

When implementing: replace the NotImplementedError stubs below with a real
polling or event-tap class. The DeltaCallback type alias and the MouseCapture
interface (start, stop, set_paused) must be preserved so callers do not change.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Type alias for the enqueue function supplied by the transport layer.
# Signature: enqueue_fn(message: dict[str, Any]) -> bool
# Returns True if the message was accepted, False if dropped due to backpressure.
DeltaCallback = Callable[[dict[str, Any]], bool]


class MouseCapture:
    """
    Mouse position delta capture -- interface stub.

    Production implementations:
      - macOS: CGEventTap at kCGHIDEventTap scope (PyObjC / Quartz bindings)
      - Windows: RawInput with RIDEV_NOLEGACY (ctypes)

    All three methods raise NotImplementedError until a platform implementation
    replaces this file. The EdgeDetector in slickshift.edge_detection is called
    from inside the production polling/event-tap loop -- its tick() method
    should be invoked on every captured position update.
    """

    def start(self, delta_callback: DeltaCallback) -> None:
        """
        Start capturing cursor position deltas and delivering them to delta_callback.

        delta_callback is called from the capture thread with each delta message dict.
        It must be thread-safe. On macOS, start() must be called from the main thread
        (CGEventTap installation requires it).
        """
        raise NotImplementedError(
            "MouseCapture.start() is not implemented. "
            "Production capture requires CGEventTap (macOS) or RawInput (Windows)."
        )

    def stop(self) -> None:
        """
        Stop capture and release any OS-level tap or device handle.

        Safe to call from any thread. Idempotent.
        """
        raise NotImplementedError(
            "MouseCapture.stop() is not implemented. "
            "Production capture requires CGEventTap (macOS) or RawInput (Windows)."
        )

    def set_paused(self, paused: bool) -> None:
        """
        Pause or resume delta delivery without stopping the underlying capture.

        When paused=True, captured deltas are not forwarded to delta_callback,
        but the OS tap or device handle remains active. Used during TRANSITIONING
        so no stale deltas reach the peer after handoff fires.

        Edge detection (via EdgeDetector.tick()) continues while paused so the
        rollback path can still track cursor state.
        """
        raise NotImplementedError(
            "MouseCapture.set_paused() is not implemented. "
            "Production capture requires CGEventTap (macOS) or RawInput (Windows)."
        )
