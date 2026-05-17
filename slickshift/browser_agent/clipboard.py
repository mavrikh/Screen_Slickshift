"""
slickshift.browser_agent.clipboard -- host-side clipboard routing for B3.

Reads the OS clipboard, decides whether the content is a URL or plain text,
and dispatches the matching browser-agent command:
    URL   -> open_url(url)       -- the extension opens a new tab
    text  -> paste_text(text)    -- the extension inserts into the active input

Public surface
--------------
    push_clipboard_to_browser(browser_agent) -> dict
        Call this from the Qt main thread (or from a background thread -- it
        is thread-safe). Raises on clipboard read failure or when no extension
        is connected. Returns the raw result dict from the browser_agent reply.

URL detection heuristic
-----------------------
    "Looks like a URL" means the string starts with one of the three plain-text
    URL schemes (http://, https://, ftp://) or matches a bare-domain pattern
    (e.g. "example.com/path"). We use the conservative plain-scheme prefix
    check as the primary gate because clipboard content that starts with
    "http" is almost always a URL; bare-domain matching is best-effort and
    disabled by default to reduce false positives on code snippets.

    The heuristic lives in _looks_like_url() and can be expanded without
    touching the public API.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from slickshift.browser_agent.server import BrowserAgentServer


# Schemes that open_url accepts (must match background.js validation).
_URL_SCHEME_RE = re.compile(r"^(https?|ftp)://", re.IGNORECASE)


def _looks_like_url(text: str) -> bool:
    """
    Return True when text should be treated as a URL and routed to open_url.

    Conservative: only fires on explicit http/https/ftp scheme prefixes so
    that code snippets, domain names in prose, and partial paths are routed
    to paste_text instead of accidentally opening tabs.
    """
    stripped = text.strip()
    return bool(_URL_SCHEME_RE.match(stripped))


def push_clipboard_to_browser(browser_agent: "BrowserAgentServer") -> dict:
    """
    Read the host OS clipboard and dispatch the appropriate browser command.

    Steps:
      1. Read the clipboard text via pyperclip.
      2. Sniff: URL -> send open_url; anything else -> send paste_text.
      3. Return the browser_agent reply dict.

    Raises:
        RuntimeError:  if no browser extension is connected.
        RuntimeError:  if pyperclip cannot read the clipboard.
        TimeoutError:  if the extension does not reply within COMMAND_TIMEOUT_S.
        RuntimeError:  if the extension returns an error payload.
    """
    try:
        import pyperclip  # imported lazily so the module loads without pyperclip on path
    except ImportError as exc:
        raise RuntimeError(
            "pyperclip is required for clipboard push. "
            "Run: pip install pyperclip"
        ) from exc

    try:
        content: str = pyperclip.paste()
    except Exception as exc:
        raise RuntimeError(f"Failed to read OS clipboard: {exc}") from exc

    if not content:
        raise RuntimeError("Clipboard is empty -- nothing to push.")

    if _looks_like_url(content):
        return browser_agent.send_command("open_url", url=content.strip())
    else:
        return browser_agent.send_command("paste_text", text=content)
