"""
cursor-bridge-test -- input capture stub.

IMPLEMENTATION NOTE (Known Unknown #2 from Slickshift CLAUDE.md):
pyautogui's position() is a passive poll, not an event tap. It reads cursor
state on demand but does NOT intercept or suppress events. For a KVM that must
prevent the host cursor from acting locally during handoff, a real system-wide
event tap is required.

On macOS, the correct mechanism is CGEventTap at kCGHIDEventTap scope, which
fires before the OS delivers the event to any application. This requires
Accessibility permission (AXIsProcessTrustedWithOptions).

pyautogui polling is used in Step 1 only to get the canvas dot tracking.
Replace with a real CGEventTap before Step 6 (handoff at screen edge), because
passive polling cannot suppress local cursor delivery.

Step 1 uses pyautogui for the canvas dot position read. The capture callback
registered here is not yet called by anything -- that wiring happens in Step 4.
"""

from __future__ import annotations

from collections.abc import Callable


# Type alias for the delta callback: (dx, dy) in normalized units (0.0-1.0).
DeltaCallback = Callable[[float, float], None]


class MouseCapture:
    """System-wide mouse input capture for the host machine."""

    def start(self, on_delta: DeltaCallback) -> None:
        """
        Install system-wide capture and begin delivering normalized deltas.

        on_delta is called for every captured movement event with (ndx, ndy)
        where each value is dx/logical_width or dy/logical_height respectively.

        Does NOT return until stop() is called from another thread.
        """
        raise NotImplementedError

    def stop(self) -> None:
        """Uninstall the capture hook and stop delivering events."""
        raise NotImplementedError

    def register_edge_callback(self, on_edge: Callable[[str], None]) -> None:
        """
        Register a callback that fires when the cursor dwells at a screen edge.

        on_edge receives a direction string: "left", "right", "top", "bottom".
        Dwell threshold is configured in config.EDGE_DWELL_S.
        """
        raise NotImplementedError
