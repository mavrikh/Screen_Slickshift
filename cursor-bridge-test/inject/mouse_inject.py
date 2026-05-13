"""
cursor-bridge-test -- input injection stub.

On macOS, injection uses CGEventCreate + CGEventPost (kCGHIDEventTap scope).
Accessibility permission (AXIsProcessTrustedWithOptions) is required and must
be verified before any injection call is made. Never silently fall back to
injection-without-permission -- see architecture invariants in Slickshift CLAUDE.md.

On Windows, injection uses SendInput with MOUSEEVENTF_MOVE. For absolute
positioning, MOUSEEVENTF_ABSOLUTE requires coordinates scaled to 0-65535.
The injecting process must match the elevation level of the target window.

pyautogui's moveTo/moveRel wraps these APIs. Whether it is sufficient for the
full production use case or needs to be replaced with direct API calls is a
question for Step 5 diagnostics.
"""

from __future__ import annotations


class MouseInjector:
    """Synthetic mouse event injection on the secondary (receiving) machine."""

    def move_relative(self, ndx: float, ndy: float, logical_width: int, logical_height: int) -> None:
        """
        Inject a relative cursor movement.

        ndx, ndy are normalized deltas (fractions of the local logical canvas).
        Multiply by logical_width / logical_height to get pixel deltas before injecting.
        """
        raise NotImplementedError

    def move_absolute(self, nx: float, ny: float, logical_width: int, logical_height: int) -> None:
        """
        Warp the cursor to a normalized absolute position.

        Used at handoff entry to anchor the secondary cursor at the corresponding
        edge position. nx, ny are in range 0.0-1.0 relative to the logical canvas.
        """
        raise NotImplementedError

    def click(self, button: str = "left") -> None:
        """Inject a synthetic click at the current cursor position."""
        raise NotImplementedError
