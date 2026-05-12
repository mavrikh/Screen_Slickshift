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
        log_level="info",
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
        self._cursor_hidden = False
        self._hotkey_last_fired = 0.0
        self._cap_stats = {
            "running": False,
            "anchor": None,
            "events_queued": 0,
            "move_events": 0,
            "button_events": 0,
            "hotkey_events": 0,
            "warp_count": 0,
            "last_move": None,
            "last_error": "",
        }

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
        """Return the local owner token. Retries for up to 2 s in case the
        server hasn't written the token file yet on first start."""
        import time as _t
        for _ in range(20):
            try:
                from app.config import TOKEN_FILE
                tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
            except Exception:
                pass
            _t.sleep(0.1)
        return ""

    def _dispatch_main_darwin(self, func) -> None:
        """Run func() on the macOS main thread via GCD dispatch_sync.
        AppKit calls must happen on the main thread or the process crashes."""
        try:
            from Foundation import NSThread  # type: ignore[import]
            if NSThread.isMainThread():
                func()
                return
        except Exception:
            pass
        try:
            import ctypes
            lib = ctypes.CDLL("/usr/lib/system/libdispatch.dylib")
            lib.dispatch_get_main_queue.restype = ctypes.c_void_p
            lib.dispatch_sync_f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            FUNC_T = ctypes.CFUNCTYPE(None, ctypes.c_void_p)
            cb = FUNC_T(lambda _: func())
            lib.dispatch_sync_f(lib.dispatch_get_main_queue(), None, cb)
        except Exception:
            try:
                func()
            except Exception:
                pass

    def _hide_cursor(self) -> None:
        if self._cursor_hidden:
            return
        import sys
        if sys.platform == "darwin":
            try:
                from Quartz import CGDisplayHideCursor, CGMainDisplayID  # type: ignore[import]
                CGDisplayHideCursor(CGMainDisplayID())
                self._cursor_hidden = True
            except Exception:
                pass
        elif sys.platform == "win32":
            try:
                import ctypes
                # ShowCursor uses a reference counter; decrement until hidden.
                while ctypes.windll.user32.ShowCursor(False) >= 0:
                    pass
                self._cursor_hidden = True
            except Exception:
                pass

    def _hotkey_active(self) -> bool:
        """Returns True if Shift + ` is currently held (any focus state)."""
        import sys
        try:
            if sys.platform == "win32":
                import ctypes
                shift = bool(ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000)
                backtick = bool(ctypes.windll.user32.GetAsyncKeyState(0xC0) & 0x8000)
                return shift and backtick
            if sys.platform == "darwin":
                from Quartz import CGEventSourceFlagsState, CGEventSourceKeyState, kCGEventSourceStateCombinedSessionState  # type: ignore[import]
                shift = bool(CGEventSourceFlagsState(kCGEventSourceStateCombinedSessionState) & 0x20000)
                backtick = bool(CGEventSourceKeyState(kCGEventSourceStateCombinedSessionState, 50))
                return shift and backtick
        except Exception:
            pass
        return False

    def start_hotkey_listener(self) -> None:
        """Start a background thread that fires window.slickshiftHotkey() in the
        webview whenever Shift+` is pressed while the capture loop is NOT active."""
        import threading
        threading.Thread(target=self._hotkey_poll_thread, daemon=True).start()

    def _hotkey_poll_thread(self) -> None:
        import time
        prev = False
        while True:
            try:
                cur = self._hotkey_active()
                now = time.monotonic()
                if (cur and not prev and not self._cap_running
                        and self._win is not None
                        and now - self._hotkey_last_fired > 1.0):
                    self._hotkey_last_fired = now
                    self._win.evaluate_js("window.slickshiftHotkey && window.slickshiftHotkey()")
                prev = cur
            except Exception:
                pass
            time.sleep(0.05)

    def _show_cursor(self) -> None:
        if not self._cursor_hidden:
            return
        import sys
        if sys.platform == "darwin":
            try:
                from Quartz import CGDisplayShowCursor, CGMainDisplayID  # type: ignore[import]
                CGDisplayShowCursor(CGMainDisplayID())
                self._cursor_hidden = False
            except Exception:
                pass
        elif sys.platform == "win32":
            try:
                import ctypes
                # Restore cursor — increment counter until visible (>= 0).
                while ctypes.windll.user32.ShowCursor(True) < 0:
                    pass
                self._cursor_hidden = False
            except Exception:
                pass

    def start_cursor_capture(
        self,
        center_x: int,
        center_y: int,
        remote_host: str = None,
        remote_port: int = None,
    ) -> dict:
        """Start an OS-level cursor capture loop.
        Polls cursor position at ~120 Hz, warps back to the screen center,
        and queues mouse_move / mouse_button events for get_cursor_events().

        remote_host / remote_port: if provided and fix_movement_scale is enabled,
        the loop fetches the remote screen dimensions once on startup and scales
        all dx/dy values by remote/local resolution so movement feels 1:1."""
        import threading
        import sys
        import time as _time

        self._stop_capture()
        self._cap_events = []
        self._cap_running = True
        import threading as _t
        self._cap_lock = _t.Lock()
        self._cap_stats = {
            "running": True,
            "anchor": None,
            "events_queued": 0,
            "move_events": 0,
            "button_events": 0,
            "hotkey_events": 0,
            "warp_count": 0,
            "last_move": None,
            "last_error": "",
        }

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
            from app.cursor_fixes import CURSOR_FIX_FLAGS

            try:
                import pyautogui
            except Exception:
                return
            # Disable FAILSAFE so the warp loop doesn't raise when the cursor
            # briefly passes near (0, 0).
            pyautogui.FAILSAFE = False

            # Bug 3 fix: use the DPI-aware warp_cursor_to_center() so the anchor
            # is established in the same coordinate space that pyautogui.position()
            # reads from. On Mac Retina, pyautogui.moveTo(sw//2, sh//2) uses
            # physical pixels while position() returns logical coords, so the
            # computed anchor lands in the wrong space. warp_cursor_to_center()
            # already handles this correctly; reading back the actual position
            # after the warp then gives a self-calibrated anchor with no mismatch.
            if CURSOR_FIX_FLAGS.get("fix_capture_anchor"):
                from app.input_control import warp_cursor_to_center
                warp_cursor_to_center()
            else:
                sw, sh = pyautogui.size()
                pyautogui.moveTo(sw // 2, sh // 2, duration=0)

            # Read back the actual post-warp position instead of trusting the
            # computed center. On Windows with DPI scaling, pyautogui.size() and
            # pyautogui.position() can use different coordinate spaces, making a
            # computed anchor wrong. Using the real position self-calibrates.
            _time.sleep(0.020)
            _actual = pyautogui.position()
            acx, acy = _actual.x, _actual.y
            with self._cap_lock:
                self._cap_stats["anchor"] = {"x": int(acx), "y": int(acy)}
                self._cap_stats["warp_count"] += 1

            # Bug 1 fix: fetch remote screen info and compute a scale factor so
            # that dx/dy are normalized to the remote machine's resolution.
            # The remote endpoint now returns LOGICAL dimensions, so remote/local
            # is a logical/logical ratio (correct regardless of DPI).
            scale_x = 1.0
            scale_y = 1.0
            if (CURSOR_FIX_FLAGS.get("fix_movement_scale")
                    and remote_host is not None
                    and remote_port is not None):
                try:
                    import json as _json
                    import urllib.request as _ur
                    # Normalize local dimensions to logical. On Windows with
                    # DPI awareness pyautogui.size() returns physical pixels;
                    # divide by the DPI scale to get logical. On Mac pyautogui
                    # already returns logical, so no adjustment is needed.
                    local_size = pyautogui.size()
                    local_w_raw = local_size.width or 1
                    local_h_raw = local_size.height or 1
                    if sys.platform == "win32" and CURSOR_FIX_FLAGS.get("fix_windows_dpi"):
                        try:
                            import ctypes as _ct2
                            _dpi = _ct2.windll.shcore.GetScaleFactorForDevice(0) / 100.0
                            local_w = int(local_w_raw / _dpi) or 1
                            local_h = int(local_h_raw / _dpi) or 1
                        except Exception:
                            local_w, local_h = local_w_raw, local_h_raw
                    else:
                        local_w, local_h = local_w_raw, local_h_raw
                    url = f"http://{remote_host}:{remote_port}/api/screen-info"
                    with _ur.urlopen(url, timeout=3) as _resp:
                        remote_info = _json.loads(_resp.read().decode("utf-8"))
                    remote_w = remote_info.get("width") or local_w
                    remote_h = remote_info.get("height") or local_h
                    scale_x = remote_w / local_w
                    scale_y = remote_h / local_h
                except Exception:
                    scale_x = 1.0
                    scale_y = 1.0

            # Normalize captured deltas to logical pixels before applying scale.
            # On Windows with DPI awareness pyautogui.position() returns physical
            # pixels — divide by the DPI scale. On Mac pyautogui already returns
            # logical pixels, so local_dpi stays 1.0.
            local_dpi = 1.0
            if sys.platform == "win32" and CURSOR_FIX_FLAGS.get("fix_windows_dpi"):
                try:
                    import ctypes as _ct
                    local_dpi = _ct.windll.shcore.GetScaleFactorForDevice(0) / 100.0
                except Exception:
                    pass

            sensitivity = float(CURSOR_FIX_FLAGS.get("cursor_sensitivity", 1.0))
            sensitivity = max(0.1, min(3.0, sensitivity))

            prev_l = prev_r = prev_hotkey = False
            while self._cap_running:
                try:
                    pos = pyautogui.position()
                    dx, dy = pos.x - acx, pos.y - acy
                    l_dn, r_dn = _buttons()
                    hotkey = self._hotkey_active()
                    evs = []
                    if dx or dy:
                        dx_logical = dx / local_dpi if local_dpi > 1.0 else dx
                        dy_logical = dy / local_dpi if local_dpi > 1.0 else dy
                        sdx = int(dx_logical * scale_x * sensitivity)
                        sdy = int(dy_logical * scale_y * sensitivity)
                        evs.append({"type": "mouse_move", "dx": sdx, "dy": sdy})
                        pyautogui.moveTo(acx, acy, duration=0)
                        self._cap_stats["warp_count"] += 1
                        self._cap_stats["move_events"] += 1
                        self._cap_stats["last_move"] = {"dx": sdx, "dy": sdy}
                    if l_dn != prev_l:
                        evs.append({"type": "mouse_button", "button": "left", "down": l_dn})
                        self._cap_stats["button_events"] += 1
                        prev_l = l_dn
                    if r_dn != prev_r:
                        evs.append({"type": "mouse_button", "button": "right", "down": r_dn})
                        self._cap_stats["button_events"] += 1
                        prev_r = r_dn
                    if hotkey and not prev_hotkey:
                        evs.append({"type": "hotkey_switch"})
                        self._cap_stats["hotkey_events"] += 1
                        self._hotkey_last_fired = _time.monotonic()
                    prev_hotkey = hotkey
                    if evs:
                        with self._cap_lock:
                            self._cap_events.extend(evs)
                            self._cap_stats["events_queued"] += len(evs)
                except Exception as exc:
                    with self._cap_lock:
                        self._cap_stats["last_error"] = str(exc)
                _time.sleep(0.008)

        self._hide_cursor()
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
            t.join(timeout=0.25)
        try:
            with self._cap_lock:
                self._cap_stats["running"] = False
        except Exception:
            self._cap_stats["running"] = False
        self._show_cursor()

    def get_cursor_events(self) -> list:
        """Return and clear all queued cursor/button events since the last call."""
        try:
            with self._cap_lock:
                evs = list(self._cap_events)
                self._cap_events.clear()
                return evs
        except Exception:
            return []

    def get_cursor_capture_status(self) -> dict:
        try:
            with self._cap_lock:
                stats = dict(self._cap_stats)
                stats["pending_events"] = len(self._cap_events)
                return stats
        except Exception:
            stats = dict(self._cap_stats)
            stats["pending_events"] = len(self._cap_events) if self._cap_events else 0
            return stats

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
    api.start_hotkey_listener()
    import os, pathlib
    _data_dir = pathlib.Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "ScreenSlickshift" / "webview"
    # debug=True enables F12 / right-click Inspect in the WebView2 window.
    # Remove before shipping.
    webview.start(storage_path=str(_data_dir), debug=True)


if __name__ == "__main__":
    main()
