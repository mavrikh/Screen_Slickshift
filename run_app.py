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
        self._cap_running = False
        self._cap_thread = None
        self._cap_events: list = []
        self._cap_lock = None

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

    def start_cursor_capture(self, center_x: int, center_y: int) -> dict:
        """Start an OS-level cursor capture loop.
        Polls cursor position at ~120 Hz, warps back to (center_x, center_y),
        and queues mouse_move / mouse_button events for get_cursor_events()."""
        import threading
        import sys
        import time as _time

        self._stop_capture()
        cx, cy = int(center_x), int(center_y)
        self._cap_events = []
        self._cap_running = True
        import threading as _t
        self._cap_lock = _t.Lock()

        def _buttons():
            try:
                if sys.platform == "darwin":
                    from AppKit import NSEvent  # type: ignore[import]
                    b = NSEvent.pressedMouseButtons()
                    return bool(b & 1), bool(b & 2)
                if sys.platform == "win32":
                    import ctypes
                    l = bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
                    r = bool(ctypes.windll.user32.GetAsyncKeyState(0x02) & 0x8000)
                    return l, r
            except Exception:
                pass
            return False, False

        def _run():
            try:
                import pyautogui
            except Exception:
                return
            # Use pyautogui's own coordinate space for the anchor point so there
            # is no DPI / coordinate-system mismatch with pyautogui.position().
            # Disable FAILSAFE so the warp loop doesn't raise when the cursor
            # briefly passes near (0, 0).
            pyautogui.FAILSAFE = False
            sw, sh = pyautogui.size()
            acx, acy = sw // 2, sh // 2
            pyautogui.moveTo(acx, acy, duration=0)
            prev_l = prev_r = False
            while self._cap_running:
                try:
                    pos = pyautogui.position()
                    dx, dy = pos.x - acx, pos.y - acy
                    l_dn, r_dn = _buttons()
                    evs = []
                    if dx or dy:
                        evs.append({"type": "mouse_move", "dx": int(dx), "dy": int(dy)})
                        pyautogui.moveTo(acx, acy, duration=0)
                    if l_dn != prev_l:
                        evs.append({"type": "mouse_button", "button": "left", "down": l_dn})
                        prev_l = l_dn
                    if r_dn != prev_r:
                        evs.append({"type": "mouse_button", "button": "right", "down": r_dn})
                        prev_r = r_dn
                    if evs:
                        with self._cap_lock:
                            self._cap_events.extend(evs)
                except Exception:
                    pass
                _time.sleep(0.008)

        self._cap_thread = threading.Thread(target=_run, daemon=True)
        self._cap_thread.start()
        return {"ok": True}

    def stop_cursor_capture(self) -> None:
        self._stop_capture()

    def _stop_capture(self) -> None:
        self._cap_running = False
        t = self._cap_thread
        self._cap_thread = None
        if t is not None and t.is_alive():
            t.join(timeout=0.15)

    def get_cursor_events(self) -> list:
        """Return and clear all queued cursor/button events since the last call."""
        try:
            with self._cap_lock:
                evs = list(self._cap_events)
                self._cap_events.clear()
                return evs
        except Exception:
            return []

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
        js_api=api,
    )
    webview.start()


if __name__ == "__main__":
    main()
