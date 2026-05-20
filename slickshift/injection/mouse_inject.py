"""
Slickshift mainline -- OS-level mouse injection via pyautogui.

pyautogui wraps platform APIs for cursor movement:
  - macOS: CGEventCreate + CGEventPost (requires Accessibility permission)
  - Windows: SendInput with MOUSEEVENTF_MOVE / MOUSEEVENTF_ABSOLUTE

Accessibility permission on macOS is NOT prompted by pyautogui itself; the OS
prompts on the first call that would actually move the cursor. If permission is
denied, pyautogui silently does nothing. MouseInjector detects this via a
before/after position probe and surfaces a clear log message rather than
silently losing events. See verify_permission_probe() in this module.

pyautogui's failsafe (corner abort) is disabled globally because the cursor
legitimately reaches screen corners in a KVM workflow. The dead-man switch
(heartbeat) and the force-release hotkey replace it. See config.py.

DPI handling:
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
# all four corners on the secondary machine. The dead-man heartbeat and the
# force-release hotkey serve as the safety net instead.
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
                "Grant in System Settings > Privacy and Security > Accessibility, "
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
        canvas_w: int,
        canvas_h: int,
    ) -> None:
        """
        Inject a relative cursor movement.

        ndx, ndy are normalized deltas (fractions of the sender's virtual-desktop
        canvas -- its full multi-monitor bounding box width and height). The
        receiver multiplies by the sender's canvas dimensions to recover the
        original pixel delta and injects it as an OS-level relative move.

        canvas_w, canvas_h: the sender's virtual-desktop bounding box dimensions
            (peer_vd_w, peer_vd_h). On old peers that did not send monitor
            topology these fall back to the peer's screen_w/screen_h scalars from
            the hello payload.

        Single-monitor invariant: when both peers have a single monitor at (0,0)
        with matching dimensions, canvas_w == screen_w and canvas_h == screen_h,
        so behavior is identical to the legacy implementation.
        """
        dx_px = int(ndx * canvas_w)
        dy_px = int(ndy * canvas_h)
        logger.debug(
            "inject move_relative: ndx=%.4f ndy=%.4f canvas=%dx%d -> dx=%d dy=%d",
            ndx, ndy, canvas_w, canvas_h, dx_px, dy_px,
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

        Used at handoff entry to anchor the secondary cursor at the
        corresponding edge position. nx, ny are in range 0.0-1.0 relative to
        the receiver's pyautogui canvas.

        screen_w, screen_h must be from pyautogui.size() on this machine.
        pyautogui.moveTo receives coordinates in its own native unit directly.
        """
        x_px = int(nx * screen_w)
        y_px = int(ny * screen_h)
        logger.debug("inject move_absolute: nx=%.4f ny=%.4f -> x=%d y=%d", nx, ny, x_px, y_px)
        pyautogui.moveTo(x_px, y_px, duration=0)

    def click(self, button: str, pressed: bool) -> None:
        """
        Inject a synthetic mouse button down or up at the current cursor position.

        button must be one of "left", "right", "middle" -- the canonical strings
        produced by EventCapture and expected by pyautogui.mouseDown/mouseUp.
        pressed=True injects a button-down event; pressed=False injects button-up.
        """
        logger.debug("inject click: button=%s pressed=%s", button, pressed)
        if pressed:
            pyautogui.mouseDown(button=button)
        else:
            pyautogui.mouseUp(button=button)

    def scroll(self, dx: int, dy: int) -> None:
        """
        Inject scroll wheel movement.

        dy is vertical scroll (positive = up, matches pynput convention).
        dx is horizontal scroll.
        pyautogui scroll units are platform-specific (roughly one click/line
        per integer unit). pynput delivers integer deltas; we pass them through
        directly. If scroll speed feels wrong during testing, a scaling factor
        can be added here later.
        """
        logger.debug("inject scroll: dx=%d dy=%d", dx, dy)
        if dy != 0:
            pyautogui.scroll(dy)
        if dx != 0:
            pyautogui.hscroll(dx)

    def key(self, key_name: str, pressed: bool) -> None:
        """
        Inject a synthetic keyboard event at the OS level.

        key_name must be a string accepted by pyautogui (e.g. 'a', 'shift',
        'enter', 'f1'). pyautogui handles the platform-specific keycode
        translation internally (e.g. 'command' -> Cmd on macOS).

        pressed=True fires a key-down event; pressed=False fires key-up.
        Both events must be forwarded so modifier state is reconstructed
        correctly on the receiver (e.g. Shift held while typing a letter).
        """
        logger.debug("inject key: key_name=%r pressed=%s", key_name, pressed)
        if pressed:
            pyautogui.keyDown(key_name)
        else:
            pyautogui.keyUp(key_name)


def make_mouse_injector() -> MouseInjector:
    """
    Build the platform-appropriate mouse injector for this OS.

    Returns WindowsMouseInjector (ctypes SendInput-based) on Windows.
    Returns MouseInjector (pyautogui-based) on every other platform.

    Callers should always go through this factory rather than instantiating
    MouseInjector directly. That keeps the OS choice in one place; future
    Linux (uinput) implementations slot in here without touching any caller.

    The Windows import is deferred inside the platform check so that
    win_inject.py (which raises ImportError on non-Windows at module level)
    is never loaded on macOS or Linux.
    """
    if platform.system() == "Windows":
        from slickshift.injection.win_inject import WindowsMouseInjector  # noqa: PLC0415
        return WindowsMouseInjector()
    return MouseInjector()
