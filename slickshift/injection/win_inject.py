"""
Slickshift Windows mouse injection via ctypes SendInput.

Replaces the pyautogui path on Windows with a direct Win32 SendInput call.
This eliminates pyautogui's overhead and gives us raw MOUSEEVENTF_MOVE
relative deltas with no intermediate cursor positioning API.

Coordinate contract (mirrors the pyautogui injector exactly):
  move_relative(ndx, ndy, canvas_w, canvas_h)
      ndx/ndy are normalized fractions of the *sender's* virtual-desktop canvas.
      This module multiplies them by canvas_w/canvas_h to recover pixel deltas
      and passes those directly to SendInput as MOUSEEVENTF_MOVE (relative).
      That matches the pyautogui path: pyautogui.moveRel(dx_px, dy_px).

  move_absolute(nx, ny, screen_w, screen_h)
      nx/ny are 0.0-1.0 fractions of the *receiver's* virtual desktop.
      SendInput MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE expects coordinates
      scaled to the [0, 65535] range across the primary monitor.  We convert
      via SetCursorPos instead, which accepts pixel coordinates directly and
      works correctly on multi-monitor setups without the 65535-normalization
      quirk. This matches pyautogui.moveTo() semantics.

Reference:
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input
  https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput

Import safety:
  This module raises ImportError at the top if the platform is not Windows.
  It is only ever imported by the make_mouse_injector() factory inside
  mouse_inject.py, which guards the import behind a platform check.
  Non-Windows Python processes never load this file.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import platform

if platform.system() != "Windows":
    raise ImportError(
        "slickshift.injection.win_inject is Windows-only. "
        "Use make_mouse_injector() from mouse_inject.py to get the correct "
        "injector for this platform."
    )

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 constants
# (https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput)
# ---------------------------------------------------------------------------

INPUT_MOUSE: int = 0  # dwType field value for a mouse event

MOUSEEVENTF_MOVE: int = 0x0001       # relative cursor movement
MOUSEEVENTF_LEFTDOWN: int = 0x0002
MOUSEEVENTF_LEFTUP: int = 0x0004
MOUSEEVENTF_RIGHTDOWN: int = 0x0008
MOUSEEVENTF_RIGHTUP: int = 0x0010
MOUSEEVENTF_MIDDLEDOWN: int = 0x0020
MOUSEEVENTF_MIDDLEUP: int = 0x0040
MOUSEEVENTF_WHEEL: int = 0x0800      # vertical scroll; mouseData = delta * 120
MOUSEEVENTF_HWHEEL: int = 0x01000   # horizontal scroll; mouseData = delta * 120

# One "detent" of scroll wheel movement per WHEEL_DELTA unit.
# https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-mousewheel
WHEEL_DELTA: int = 120

# ---------------------------------------------------------------------------
# Structs
# ---------------------------------------------------------------------------
# ULONG_PTR is pointer-sized on both 32-bit and 64-bit Windows.
# ctypes.c_size_t is the correct ctypes alias: DWORD on 32-bit, ULONGLONG on
# 64-bit. Using it here avoids struct padding errors on 64-bit processes.
# https://learn.microsoft.com/en-us/windows/win32/winprog/windows-data-types
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    """
    MOUSEINPUT struct.
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-mouseinput
    """
    _fields_ = [
        ("dx",          ctypes.wintypes.LONG),
        ("dy",          ctypes.wintypes.LONG),
        ("mouseData",   ctypes.wintypes.DWORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    """Union for the INPUT struct's payload field."""
    _fields_ = [
        ("mi", MOUSEINPUT),
        # ki (KEYBDINPUT) and hi (HARDWAREINPUT) are omitted; we only send
        # mouse events from this module.
    ]


class INPUT(ctypes.Structure):
    """
    INPUT struct.
    https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input
    """
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


# ---------------------------------------------------------------------------
# Low-level send helper
# ---------------------------------------------------------------------------

def _send(flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0) -> None:
    """
    Build and dispatch a single INPUT record via SendInput.

    flags:      MOUSEEVENTF_* bitmask.
    dx, dy:     Pixel deltas for MOUSEEVENTF_MOVE.  Zero for button/scroll events.
    mouse_data: WHEEL_DELTA multiple for scroll events.  Zero otherwise.
    """
    inp = INPUT(type=INPUT_MOUSE)
    inp._input.mi.dwFlags = flags
    inp._input.mi.dx = dx
    inp._input.mi.dy = dy
    inp._input.mi.mouseData = mouse_data
    inp._input.mi.time = 0          # let the system timestamp the event
    inp._input.mi.dwExtraInfo = 0

    result = ctypes.windll.user32.SendInput(
        1,                          # number of INPUT records
        ctypes.byref(inp),
        ctypes.sizeof(INPUT),
    )
    if result != 1:
        err = ctypes.get_last_error()
        logger.warning("SendInput returned %d (expected 1); GetLastError=%d", result, err)


# ---------------------------------------------------------------------------
# Public injector class
# ---------------------------------------------------------------------------

class WindowsMouseInjector:
    """
    Drop-in replacement for MouseInjector that uses ctypes SendInput on Windows.

    Public interface is identical to MouseInjector in mouse_inject.py.  The
    caller (main_window.py) holds a reference typed as the result of
    make_mouse_injector() and calls the same methods regardless of OS.
    """

    def __init__(self) -> None:
        # Windows does not require a permission probe.  Mirror the MacOS injector's
        # _injection_verified flag so callers that call verify_permission_probe()
        # (e.g. during Inject checkbox toggle) work without modification.
        self._injection_verified: bool = True

    # ------------------------------------------------------------------
    # Permission probe (no-op on Windows; kept for interface compatibility)
    # ------------------------------------------------------------------

    def verify_permission_probe(self) -> bool:
        """
        Windows does not gate injection behind a user-visible permission dialog.
        SendInput works immediately in any process that is not a UIPI-blocked
        low-integrity process. Return True unconditionally.

        Interface mirrors MouseInjector.verify_permission_probe() so the caller
        in main_window.py can treat both injectors identically.
        """
        return True

    def reset_verification(self) -> None:
        """No-op on Windows. Kept for interface compatibility."""

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
        Inject a relative cursor movement via MOUSEEVENTF_MOVE.

        ndx, ndy are normalized deltas (fractions of the sender's virtual-desktop
        canvas width and height). Multiply by canvas_w/canvas_h to recover the
        original pixel delta -- identical to what the pyautogui path does before
        calling pyautogui.moveRel(dx_px, dy_px, duration=0).

        SendInput MOUSEEVENTF_MOVE without MOUSEEVENTF_ABSOLUTE is a raw pixel
        delta in the desktop coordinate system, which is exactly what we want.
        """
        dx_px = int(ndx * canvas_w)
        dy_px = int(ndy * canvas_h)
        logger.debug(
            "inject move_relative: ndx=%.4f ndy=%.4f canvas=%dx%d -> dx=%d dy=%d",
            ndx, ndy, canvas_w, canvas_h, dx_px, dy_px,
        )
        _send(MOUSEEVENTF_MOVE, dx=dx_px, dy=dy_px)

    def move_absolute(
        self,
        nx: float,
        ny: float,
        screen_w: int,
        screen_h: int,
    ) -> None:
        """
        Warp the cursor to a normalized absolute position using SetCursorPos.

        nx, ny are in range 0.0-1.0 relative to the receiver's virtual desktop.
        screen_w, screen_h are the receiver's virtual desktop pixel dimensions
        (passed in by the caller -- same contract as the pyautogui injector).

        We use SetCursorPos rather than MOUSEEVENTF_ABSOLUTE because
        MOUSEEVENTF_ABSOLUTE normalizes to [0, 65535] over the primary monitor
        only, which breaks on multi-monitor setups where the virtual desktop is
        wider or taller than the primary. SetCursorPos accepts raw virtual-
        desktop pixel coordinates and handles multi-monitor correctly.
        """
        x_px = int(nx * screen_w)
        y_px = int(ny * screen_h)
        logger.debug("inject move_absolute: nx=%.4f ny=%.4f -> x=%d y=%d", nx, ny, x_px, y_px)
        ctypes.windll.user32.SetCursorPos(x_px, y_px)

    # ------------------------------------------------------------------
    # Clicks
    # ------------------------------------------------------------------

    def click(self, button: str, pressed: bool) -> None:
        """
        Inject a synthetic mouse button down or up.

        button must be one of "left", "right", "middle".
        pressed=True injects a button-down; pressed=False injects button-up.

        Maps identically to pyautogui.mouseDown(button=button) /
        pyautogui.mouseUp(button=button) semantics.
        """
        logger.debug("inject click: button=%s pressed=%s", button, pressed)

        flag_map: dict[str, tuple[int, int]] = {
            "left":   (MOUSEEVENTF_LEFTDOWN,   MOUSEEVENTF_LEFTUP),
            "right":  (MOUSEEVENTF_RIGHTDOWN,  MOUSEEVENTF_RIGHTUP),
            "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
        }
        pair = flag_map.get(button)
        if pair is None:
            logger.warning("inject click: unknown button %r -- ignoring", button)
            return

        down_flag, up_flag = pair
        _send(down_flag if pressed else up_flag)

    # ------------------------------------------------------------------
    # Scroll
    # ------------------------------------------------------------------

    def scroll(self, dx: int, dy: int) -> None:
        """
        Inject scroll wheel movement.

        dy is vertical scroll (positive = up, matching pynput convention and
        the pyautogui injector's contract). Maps to MOUSEEVENTF_WHEEL with
        mouseData = dy * WHEEL_DELTA (positive scrolls up per WM_MOUSEWHEEL).

        dx is horizontal scroll. Maps to MOUSEEVENTF_HWHEEL with
        mouseData = dx * WHEEL_DELTA (positive scrolls right per WM_MOUSEHWHEEL).

        Both match the integer-unit convention from pynput and the pyautogui
        path: no unit translation needed.
        """
        logger.debug("inject scroll: dx=%d dy=%d", dx, dy)
        if dy != 0:
            _send(MOUSEEVENTF_WHEEL, mouse_data=ctypes.wintypes.DWORD(dy * WHEEL_DELTA).value)
        if dx != 0:
            _send(MOUSEEVENTF_HWHEEL, mouse_data=ctypes.wintypes.DWORD(dx * WHEEL_DELTA).value)

    # ------------------------------------------------------------------
    # Keyboard (delegated to pyautogui -- out of scope for this module)
    # ------------------------------------------------------------------

    def key(self, key_name: str, pressed: bool) -> None:
        """
        Inject a synthetic keyboard event.

        Keyboard injection is not in scope for the ctypes refactor; pyautogui
        continues to handle it on Windows for now. This module imports pyautogui
        lazily to keep the ctypes path independent from pyautogui at module load.
        """
        logger.debug("inject key: key_name=%r pressed=%s", key_name, pressed)
        import pyautogui  # noqa: PLC0415 -- intentionally deferred
        if pressed:
            pyautogui.keyDown(key_name)
        else:
            pyautogui.keyUp(key_name)
