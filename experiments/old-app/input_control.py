from __future__ import annotations

import logging
import sys
import time

from app.cursor_fixes import CURSOR_FIX_FLAGS
from app.state import lockout_state


logger = logging.getLogger(__name__)

_last_move_at = 0.0
_min_move_interval = 0.004
_pyautogui_backend = None


def _ensure_enabled() -> None:
    if lockout_state.is_disabled():
        raise RuntimeError("Input control is disabled by emergency lockout.")


def move_mouse(dx: float, dy: float) -> None:
    global _last_move_at
    _ensure_enabled()
    pyautogui = _pyautogui()

    now = time.monotonic()
    if now - _last_move_at < _min_move_interval:
        return
    _last_move_at = now

    try:
        pyautogui.moveRel(int(dx), int(dy), duration=0)
    except pyautogui.FailSafeException:
        logger.warning("pyautogui FailSafeException suppressed in move_mouse (corner trigger).")


def click_mouse(button: str = "left") -> None:
    _ensure_enabled()
    pyautogui = _pyautogui()
    if button not in {"left", "right", "middle"}:
        button = "left"
    pyautogui.click(button=button)


def mousedown_mouse(button: str = "left") -> None:
    _ensure_enabled()
    pyautogui = _pyautogui()
    if button not in {"left", "right", "middle"}:
        button = "left"
    pyautogui.mouseDown(button=button)


def mouseup_mouse(button: str = "left") -> None:
    _ensure_enabled()
    pyautogui = _pyautogui()
    if button not in {"left", "right", "middle"}:
        button = "left"
    pyautogui.mouseUp(button=button)


def scroll_mouse(amount: int) -> None:
    _ensure_enabled()
    pyautogui = _pyautogui()
    pyautogui.scroll(int(amount))


_SPECIAL_KEYS = frozenset({
    "enter", "tab", "backspace", "delete", "up", "down", "left", "right",
    "home", "end", "pageup", "pagedown", "insert", "escape", "esc", "space",
    "capslock", "numlock", "scrolllock",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
})


def press_key(
    key: str,
    ctrl: bool = False,
    alt: bool = False,
    shift: bool = False,
    meta: bool = False,
) -> None:
    _ensure_enabled()
    if not key:
        return
    pyautogui = _pyautogui()
    modifiers = []
    if ctrl:
        modifiers.append("ctrl")
    if alt:
        modifiers.append("alt")
    if shift:
        modifiers.append("shift")
    if meta:
        modifiers.append("command" if sys.platform == "darwin" else "win")
    pg_key = key.lower() if key.lower() in _SPECIAL_KEYS else key
    if modifiers:
        pyautogui.hotkey(*modifiers, pg_key)
    elif len(key) == 1:
        pyautogui.write(key)
    elif pg_key in _SPECIAL_KEYS:
        pyautogui.press(pg_key)


def warp_cursor_to_center() -> tuple[int, int]:
    _ensure_enabled()
    pyautogui = _pyautogui()
    w, h = pyautogui.size()
    x, y = w // 2, h // 2
    pyautogui.moveTo(x, y, duration=0)
    return x, y


def diagnostic_mouse_nudge(dx: float = 80, dy: float = 0) -> dict:
    """Move the local cursor once and report the observed before/after position."""
    _ensure_enabled()
    pyautogui = _pyautogui()
    before = pyautogui.position()
    pyautogui.moveRel(int(dx), int(dy), duration=0)
    after = pyautogui.position()
    return {
        "ok": True,
        "requested": {"dx": int(dx), "dy": int(dy)},
        "before": {"x": int(before.x), "y": int(before.y)},
        "after": {"x": int(after.x), "y": int(after.y)},
        "observed": {"dx": int(after.x - before.x), "dy": int(after.y - before.y)},
    }


def send_text_to_pc(text: str) -> None:
    _ensure_enabled()
    if not text:
        return
    pyautogui = _pyautogui()
    pyautogui.write(text, interval=0.001)


def input_control_status(check_backend: bool = False) -> dict:
    status = {
        "backend": "pyautogui",
        "available": None,
        "disabled": lockout_state.is_disabled(),
        "input_allowed": not lockout_state.is_disabled(),
        "platform": sys.platform,
        "accessibility_required": sys.platform == "darwin",
        "screen_recording_required": False,
        "pyautogui_failsafe": False,
        "emergency_stop": "Screen Slickshift emergency lockout",
        "error": "",
    }
    if not check_backend:
        return status

    try:
        _pyautogui()
    except RuntimeError as exc:
        status["available"] = False
        status["input_allowed"] = False
        status["error"] = str(exc)
        return status

    status["available"] = True
    return status


def _pyautogui():
    global _pyautogui_backend
    if _pyautogui_backend is not None:
        return _pyautogui_backend

    try:
        import pyautogui
    except Exception as exc:
        logger.exception("Desktop input backend is unavailable.")
        raise RuntimeError(
            "Desktop input backend is unavailable. Install pyautogui and run from a desktop session "
            "with the required OS permissions."
        ) from exc

    # PyAutoGUI's corner fail-safe conflicts with normal KVM use because a
    # remote cursor can legitimately move into any screen corner. Screen
    # Slickshift uses its explicit emergency lockout instead.
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0

    # Bug 2 fix: set per-monitor DPI awareness on Windows so that pyautogui
    # coord reads and writes operate in the same coordinate space.
    if sys.platform == "win32" and CURSOR_FIX_FLAGS.get("fix_windows_dpi"):
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()  # fallback for older Windows
            except Exception:
                pass

    _pyautogui_backend = pyautogui
    return _pyautogui_backend
