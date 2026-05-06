from __future__ import annotations

import logging
import time

import pyautogui

from app.state import lockout_state


logger = logging.getLogger(__name__)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0

_last_move_at = 0.0
_min_move_interval = 0.004


def _ensure_enabled() -> None:
    if lockout_state.is_disabled():
        raise RuntimeError("Input control is disabled by emergency lockout.")


def move_mouse(dx: float, dy: float) -> None:
    global _last_move_at
    _ensure_enabled()

    now = time.monotonic()
    if now - _last_move_at < _min_move_interval:
        return
    _last_move_at = now

    pyautogui.moveRel(int(dx), int(dy), duration=0)


def click_mouse(button: str = "left") -> None:
    _ensure_enabled()
    if button not in {"left", "right", "middle"}:
        button = "left"
    pyautogui.click(button=button)


def scroll_mouse(amount: int) -> None:
    _ensure_enabled()
    pyautogui.scroll(int(amount))


def send_text_to_pc(text: str) -> None:
    _ensure_enabled()
    if not text:
        return
    pyautogui.write(text, interval=0.001)
