"""Desktop launcher — starts the local server then opens a pywebview window.

Usage:
    python run_app.py

The FastAPI server runs in a daemon thread; closing the window exits the
process and takes the server with it.
"""
from __future__ import annotations

import sys
import threading
import time
import urllib.request
import urllib.error

_SERVER_HOST = "0.0.0.0"   # listen on all interfaces so LAN devices can connect
_UI_HOST = "127.0.0.1"    # webview connects via localhost
_PORT = 8765
_URL = f"http://{_UI_HOST}:{_PORT}"


def _start_server() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Missing dependencies. Run: pip install -r requirements.txt") from exc

    uvicorn.run(
        "app.main:app",
        host=_SERVER_HOST,
        port=_PORT,
        access_log=False,
        log_level="warning",
    )


def _wait_for_server(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{_URL}/api/status", timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


class _AppAPI:
    """Python functions exposed to the webview JavaScript context as window.pywebview.api.*"""

    def __init__(self) -> None:
        self._win = None  # set to the pywebview Window after creation

    def warp_to_center(self) -> dict:
        """Warp the OS cursor to the centre of the pywebview window.
        Handles AppKit vs pyautogui coordinate differences on macOS.
        Returns CSS reference coords (content-area relative) for delta tracking."""
        try:
            import pyautogui
            import sys
            if sys.platform == "darwin":
                from AppKit import NSScreen, NSApp  # type: ignore[import]
                screen = NSScreen.mainScreen()
                screen_h_logical = screen.frame().size.height
                dpr = float(screen.backingScaleFactor())
                win = NSApp.mainWindow() or NSApp.keyWindow()
                if win is None:
                    visible = [w for w in NSApp.windows() if w.isVisible()]
                    win = visible[0] if visible else None
                if win is None:
                    raise RuntimeError("no window")
                frame = win.frame()
                content_h = win.contentView().frame().size.height
                # AppKit origin is bottom-left; centre in AppKit logical coords:
                cx_logical = frame.origin.x + frame.size.width / 2
                cy_appkit   = frame.origin.y + frame.size.height / 2
                # Convert to pyautogui (top-left origin):
                phys_x = int(cx_logical * dpr)
                phys_y = int((screen_h_logical - cy_appkit) * dpr)
                pyautogui.moveTo(phys_x, phys_y, duration=0)
                return {
                    "ok": True,
                    "phys_x": phys_x, "phys_y": phys_y,
                    "css_ref_x": frame.size.width / 2,
                    "css_ref_y": content_h / 2,
                }
            else:
                # Windows / Linux: window.screenX/Y are already top-left CSS coords
                if self._win is not None:
                    dpr = 1  # Windows pyautogui uses logical coords on most setups
                    cx = self._win.x + self._win.width // 2
                    cy = self._win.y + self._win.height // 2
                    pyautogui.moveTo(cx, cy, duration=0)
                    return {
                        "ok": True,
                        "phys_x": cx, "phys_y": cy,
                        "css_ref_x": self._win.width / 2,
                        "css_ref_y": self._win.height / 2,
                    }
                raise RuntimeError("no window ref")
        except Exception as exc:
            try:
                import pyautogui
                sw, sh = pyautogui.size()
                pyautogui.moveTo(sw // 2, sh // 2, duration=0)
            except Exception:
                pass
            return {"ok": False, "error": str(exc)}

    def cursor_warp_to_physical(self, x: int, y: int) -> None:
        """Fire-and-forget cursor warp to pre-computed physical coordinates."""
        try:
            import pyautogui
            pyautogui.moveTo(int(x), int(y), duration=0)
        except Exception:
            pass

    def get_owner_token(self) -> str:
        """Return the local owner token so the webview can authenticate without
        the user having to read the terminal."""
        try:
            from app.config import TOKEN_FILE
            return TOKEN_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def minimize(self) -> None:
        try:
            if self._win is not None:
                self._win.minimize()
        except Exception:
            pass

    def restore(self) -> None:
        try:
            if self._win is not None:
                self._win.restore()
        except Exception:
            pass

    def close_window(self) -> None:
        try:
            if self._win is not None:
                self._win.destroy()
        except Exception:
            pass


def main() -> None:
    if sys.version_info < (3, 9):
        raise SystemExit("Screen Slickshift requires Python 3.9 or newer.")

    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "pywebview is not installed. Run: pip install pywebview"
        ) from exc

    threading.Thread(target=_start_server, daemon=True).start()

    if not _wait_for_server():
        raise SystemExit("Server did not start within 15 seconds.")

    api = _AppAPI()
    api._win = webview.create_window(
        "Screen Slickshift",
        _URL,
        width=1220,
        height=800,
        min_size=(900, 620),
        resizable=True,
        frameless=True,
        easy_drag=False,   # we handle drag via -webkit-app-region in the HTML
        js_api=api,
    )
    webview.start()


if __name__ == "__main__":
    main()
