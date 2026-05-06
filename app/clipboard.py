from __future__ import annotations

import pyperclip

from app.state import lockout_state


def set_clipboard_text(text: str) -> None:
    if lockout_state.is_disabled():
        raise RuntimeError("Clipboard control is disabled by emergency lockout.")
    pyperclip.copy(text)


def get_clipboard_text() -> str:
    if lockout_state.is_disabled():
        raise RuntimeError("Clipboard control is disabled by emergency lockout.")
    return pyperclip.paste()
