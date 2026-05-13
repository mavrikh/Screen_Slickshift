"""
cursor-bridge-test -- OS-level mouse injection via pyautogui.

pyautogui wraps platform APIs for cursor movement:
  - macOS: CGEventCreate + CGEventPost (requires Accessibility permission)
  - Windows: SendInput with MOUSEEVENTF_MOVE / MOUSEEVENTF_ABSOLUTE

Accessibility permission on macOS is NOT prompted by pyautogui itself; the OS
prompts on the first call that would actually move the cursor. If permission is
denied, pyautogui silently does nothing. MouseInjector detects this via a
before/after position probe and surfaces a clear log message rather than
silently losing events. See _verify_permission_probe() in this module.

pyautogui's failsafe (corner abort) is disabled globally because the cursor
legitimately reaches screen corners in a KVM workflow. The dead-man-switch
(Step 3 heartbeat) and the Esc force-release (Step 5) replace it. See config.py.

DPI handling (simplified in Step 9):
- pyautogui.size() and pyautogui.position() are internally consistent on each
  platform. The receiver scales normalized deltas by its own pyautogui.size()
  directly. No dpi_scale multiplication is needed at the injection layer.
- The sender normalized against its own pyautogui canvas; the receiver multiplies
  back using its own. The fraction-of-screen invariant handles cross-machine DPI
  differences naturally.
"""

from __future__ import annotations

import logging
import platform

import pyautogui

# Disable pyautogui's built-in failsafe. In a KVM the cursor legitimately reaches
# all four corners on the secondary machine. The dead-man heartbeat (Step 3) and
# the Esc force-release (Step 5) serve as the safety net instead.
pyautogui.FAILSAFE = False
pyautogui.MINIMUM_DURATION = 0
pyautogui.PAUSE = 0

logger = logging.getLogger(__name__)

_IS_MACOS: bool = platform.system() == "Darwin"


class MouseInjector:
    """Synthetic mouse event injection on the secondary (receiving) machine."""

    def __init__(self) -> None:
        # Set to True after the macOS Accessibility probe passes (or on Windows,
        # immediately). Ensures the probe only runs once per session.
        self._injection_verified: bool = False

    # ------------------------------------------------------------------
    # Permission probe (macOS only)
    # ------------------------------------------------------------------

    def verify_permission_probe(self) -> bool:
        """
        Run a move-by-1 / move-by-minus-1 probe to confirm the OS will honor
        injection calls. On macOS, Accessibility permission must be granted or
        pyautogui silently does nothing.

        Returns True if injection appears to be working, False otherwise.
        Only runs once per session; subsequent calls return the cached result.
        """
        if self._injection_verified:
            return True

        if not _IS_MACOS:
            # Windows: SendInput does not require a permission prompt.
            self._injection_verified = True
            return True

        before = pyautogui.position()
        pyautogui.moveRel(1, 0, duration=0)
        pyautogui.moveRel(-1, 0, duration=0)
        after = pyautogui.position()

        if before == after:
            logger.warning(
                "macOS Accessibility permission missing. "
                "Grant in System Settings > Privacy & Security > Accessibility, "
                "then re-toggle Inject."
            )
            return False

        self._injection_verified = True
        logger.info("macOS Accessibility permission confirmed -- injection active")
        return True

    def reset_verification(self) -> None:
        """
        Clear the verified flag so the probe re-runs on the next injection call.
        Call this when the user re-enables the Inject checkbox after a denial.
        """
        self._injection_verified = False

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def move_relative(
        self,
        ndx: float,
        ndy: float,
        screen_w: int,
        screen_h: int,
    ) -> None:
        """
        Inject a relative cursor movement.

        ndx, ndy are normalized deltas (fractions of the sender's pyautogui
        canvas). The receiver multiplies by its own pyautogui.size() to recover
        a pixel count in its own native unit. pyautogui.moveRel then receives
        a value consistent with the unit it expects on this platform.

        screen_w, screen_h must be from pyautogui.size() on this machine.
        """
        dx_px = int(ndx * screen_w)
        dy_px = int(ndy * screen_h)
        logger.debug(
            "inject move_relative: ndx=%.4f ndy=%.4f -> dx=%d dy=%d",
            ndx, ndy, dx_px, dy_px,
        )
        pyautogui.moveRel(dx_px, dy_px, duration=0)

    def move_absolute(
        self,
        nx: float,
        ny: float,
        screen_w: int,
        screen_h: int,
    ) -> None:
        """
        Warp the cursor to a normalized absolute position.

        Used at handoff entry (Step 6) to anchor the secondary cursor at the
        corresponding edge position. nx, ny are in range 0.0-1.0 relative to
        the receiver's pyautogui canvas.

        screen_w, screen_h must be from pyautogui.size() on this machine.
        pyautogui.moveTo receives coordinates in its own native unit directly.
        """
        x_px = int(nx * screen_w)
        y_px = int(ny * screen_h)
        logger.debug("inject move_absolute: nx=%.4f ny=%.4f -> x=%d y=%d", nx, ny, x_px, y_px)
        pyautogui.moveTo(x_px, y_px, duration=0)

    def click(self, button: str = "left") -> None:
        """Inject a synthetic click at the current cursor position. Step 8+."""
        raise NotImplementedError
