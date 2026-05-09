from __future__ import annotations

import logging
import sys
import time

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

    pyautogui.moveRel(int(dx), int(dy), duration=0)


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

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0
    _pyautogui_backend = pyautogui
    return _pyautogui_backend
