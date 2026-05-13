from __future__ import annotations

import logging

from app.state import lockout_state


logger = logging.getLogger(__name__)
_pyperclip_backend = None


def set_clipboard_text(text: str) -> None:
    if lockout_state.is_disabled():
        raise RuntimeError("Clipboard control is disabled by emergency lockout.")
    pyperclip = _pyperclip()
    pyperclip.copy(text)


def get_clipboard_text() -> str:
    if lockout_state.is_disabled():
        raise RuntimeError("Clipboard control is disabled by emergency lockout.")
    pyperclip = _pyperclip()
    return pyperclip.paste()


def _pyperclip():
    global _pyperclip_backend
    if _pyperclip_backend is not None:
        return _pyperclip_backend

    try:
        import pyperclip
    except Exception as exc:
        logger.exception("Clipboard backend is unavailable.")
        raise RuntimeError("Clipboard backend is unavailable. Install pyperclip for clipboard support.") from exc

    _pyperclip_backend = pyperclip
    return _pyperclip_backend
