"""
Windows exclusive-mode mouse capture via the WH_MOUSE_LL low-level hook.

Why this exists:
- The polling MouseCapture (mouse_capture.py) reads cursor position via
  pyautogui.position(). On Windows the OS clamps the cursor at the edges
  of the virtual desktop, so once a master's cursor reaches the edge,
  position-delta polling produces zero and no more motion forwards to the
  peer. That makes the polling backend unusable for "Windows as the KVM
  controller".
- WH_MOUSE_LL is a system-wide hook that fires for every hardware mouse
  event before the OS dispatches it to any window. Returning non-zero from
  the callback suppresses the event before any local app sees it. This is
  the Windows equivalent of the macOS CGEventTap at kCGHIDEventTap scope.

Re-warp trick (the way around the edge clamp):
- The hook callback receives the cursor position AFTER the OS clamp, so
  raw deltas are not directly available.
- Workaround: while exclusive mode is on, after each motion event compute
  the delta from the screen center, then immediately SetCursorPos back to
  center. The next event's position is again measured from center. The
  cursor effectively never moves locally during exclusive mode and the
  delta stream is continuous regardless of where the user's hand pushes.
- SetCursorPos triggers a synthetic motion event that fires the hook again
  with the LLMHF_INJECTED flag set. The callback recognises this and
  returns CallNextHookEx without re-warping, so there is no infinite loop.

Threading model:
- start() launches a daemon thread that installs the hook and runs a Win32
  message loop (GetMessageW). The OS dispatches hook callbacks via that
  thread's message queue, so the hook must be installed on the thread that
  pumps messages.
- The callback fires on the hook thread for every mouse event globally.
- set_paused() and set_exclusive() flip flags read by the callback under
  the paused-lock inherited from MouseCapture.
- stop() posts WM_QUIT to the hook thread, unhooks, and joins.

Public interface matches MouseCapture exactly: start, stop, set_paused,
set_exclusive. Subclassing the polling base lets us reuse the
virtual-desktop bbox math and the seq counter; the polling _poll_loop is
never started because start() is overridden.

No third-party dependencies. Uses ctypes against user32.dll only.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import threading
from typing import Any

from slickshift.capture.mouse_capture import (
    DeltaCallback,
    MouseCapture,
)
from slickshift.transport.topology import MonitorInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Win32 constants and structures
# ---------------------------------------------------------------------------

WH_MOUSE_LL: int = 14
HC_ACTION: int = 0

# Hook event types delivered via wParam.
WM_MOUSEMOVE: int = 0x0200
WM_LBUTTONDOWN: int = 0x0201
WM_LBUTTONUP: int = 0x0202
WM_RBUTTONDOWN: int = 0x0204
WM_RBUTTONUP: int = 0x0205
WM_MBUTTONDOWN: int = 0x0207
WM_MBUTTONUP: int = 0x0208
WM_MOUSEWHEEL: int = 0x020A
WM_MOUSEHWHEEL: int = 0x020E

# MSLLHOOKSTRUCT.flags bits.
LLMHF_INJECTED: int = 0x00000001
LLMHF_LOWER_IL_INJECTED: int = 0x00000002

# Window/thread message used to break out of the message loop.
WM_QUIT: int = 0x0012

# WHEEL_DELTA: one "click" of the scroll wheel. mouseData high word is in
# multiples of this; we divide to get integer scroll counts.
WHEEL_DELTA: int = 120


class _POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", _POINT),
        ("mouseData", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


# The hook procedure signature: LRESULT CALLBACK proc(int nCode, WPARAM wParam, LPARAM lParam).
# LRESULT and LPARAM are pointer-sized signed integers on the platform.
_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_long,  # LRESULT
    ctypes.c_int,   # nCode
    wt.WPARAM,
    wt.LPARAM,
)


# ---------------------------------------------------------------------------
# user32.dll bindings
# ---------------------------------------------------------------------------

# Resolve user32 at import time so a missing symbol fails loudly on import,
# not at first-event time.
try:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
except OSError as exc:
    # Importing this module on a non-Windows host should never happen because
    # the factory in mouse_capture.py is platform-gated, but if it does we
    # want a clear error rather than an obscure ctypes one later.
    raise ImportError(
        "win_mouse_hook can only be imported on Windows (failed to load user32/kernel32)"
    ) from exc

_user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,           # idHook
    _HOOKPROC,              # lpfn
    wt.HINSTANCE,           # hmod
    wt.DWORD,               # dwThreadId
]
_user32.SetWindowsHookExW.restype = wt.HHOOK

_user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
_user32.UnhookWindowsHookEx.restype = wt.BOOL

_user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
_user32.CallNextHookEx.restype = ctypes.c_long

_user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
_user32.GetMessageW.restype = wt.BOOL

_user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
_user32.TranslateMessage.restype = wt.BOOL

_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
_user32.DispatchMessageW.restype = ctypes.c_long

_user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
_user32.PostThreadMessageW.restype = wt.BOOL

_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wt.BOOL

_user32.ShowCursor.argtypes = [wt.BOOL]
_user32.ShowCursor.restype = ctypes.c_int

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wt.DWORD

_kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
_kernel32.GetModuleHandleW.restype = wt.HMODULE


# ---------------------------------------------------------------------------
# WinMouseHook
# ---------------------------------------------------------------------------

class WinMouseHook(MouseCapture):
    """
    Windows implementation of MouseCapture using a WH_MOUSE_LL hook.

    Lifecycle and threading match MacMouseCapture's pattern: a daemon thread
    owns the hook and the message pump. set_paused/set_exclusive are
    cross-thread; the callback reads them under the same _paused_lock that
    the base class uses, so behaviour matches the Mac backend's gating.

    Re-warp + suppression while exclusive:
      - The callback computes deltas from the screen center each event.
      - SetCursorPos(center) keeps the cursor anchored so no edge clamp ever
        truncates the delta stream.
      - Returning 1 from the callback suppresses the event before any local
        app sees it. The local cursor never moves while exclusive.

    Non-exclusive behaviour:
      - The callback still fires but does not re-warp or suppress.
      - Deltas are computed from the previous position.
      - paused=True still suppresses emit; paused=False emits.
    """

    def __init__(self, monitors: list[MonitorInfo]) -> None:
        super().__init__(monitors)

        # Hook plumbing. Keep references on the instance so Python's GC does
        # not free the callback closure while the OS holds a pointer to it.
        self._hook_handle: wt.HHOOK | None = None
        self._hook_thread: threading.Thread | None = None
        self._hook_thread_id: int = 0
        self._hook_proc_ref: Any = None  # WINFUNCTYPE-wrapped callback

        self._tap_ready = threading.Event()
        self._tap_install_ok: bool = False

        # Anchor point for the re-warp trick. Computed in the screen
        # coordinate space pyautogui/SetCursorPos use, which on Windows is
        # the virtual-desktop coordinate space (can be negative on multi-
        # monitor setups where a secondary monitor is to the left of primary).
        self._center_x: int = self._vd_x + self._vd_w // 2
        self._center_y: int = self._vd_y + self._vd_h // 2

        # Last hardware-event position. Updated on every non-injected motion
        # event while NOT exclusive. While exclusive the re-warp keeps the
        # cursor at center so deltas are always computed against center.
        self._last_x: int = self._center_x
        self._last_y: int = self._center_y

        # Cursor visibility tracking. ShowCursor is ref-counted by Windows,
        # so we only call it on edge transitions of self._cursor_hidden to
        # avoid leaking the counter and stranding the system cursor invisible.
        self._cursor_hidden: bool = False

        # Delta callback stored for the hook thread to read.
        self._delta_callback: DeltaCallback | None = None
        self._delta_callback_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle (overrides MouseCapture)
    # ------------------------------------------------------------------

    def start(self, delta_callback: DeltaCallback) -> None:
        """
        Install the WH_MOUSE_LL hook and run the message loop in a daemon
        thread. Blocks up to 2 seconds waiting for the hook to be installed.
        Logs a clear error if installation fails; does not raise.
        """
        self._stop_event.clear()
        with self._paused_lock:
            self._paused = False
        self._seq = 0
        self._tap_ready.clear()
        self._tap_install_ok = False
        with self._delta_callback_lock:
            self._delta_callback = delta_callback

        self._hook_thread = threading.Thread(
            target=self._hook_loop,
            daemon=True,
            name="win-mouse-hook",
        )
        self._hook_thread.start()

        if not self._tap_ready.wait(timeout=2.0):
            logger.error(
                "WinMouseHook: hook thread did not signal ready within 2s. "
                "Installation may have failed silently."
            )
            return

        if not self._tap_install_ok:
            err = ctypes.get_last_error()
            logger.error(
                "WinMouseHook: SetWindowsHookExW failed (GetLastError=%d). "
                "WH_MOUSE_LL hooks generally require no special privilege; "
                "if this persists, check group-policy or third-party security "
                "software that blocks low-level hooks.",
                err,
            )
            return

        logger.info("WinMouseHook started (WH_MOUSE_LL installed)")

    def stop(self) -> None:
        """Stop the hook thread and unhook. Always restores cursor visibility."""
        self._stop_event.set()

        if self._cursor_hidden:
            # Bring the cursor back if we hid it during an exclusive window.
            # ShowCursor(TRUE) increments the display counter and the OS will
            # render the cursor again once the counter is >= 0.
            _user32.ShowCursor(True)
            self._cursor_hidden = False

        # Wake the message loop. PostThreadMessage delivers WM_QUIT so
        # GetMessageW returns 0 and the loop exits.
        if self._hook_thread_id != 0:
            _user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)

        if self._hook_thread is not None:
            self._hook_thread.join(timeout=2.0)
            self._hook_thread = None

        self._hook_thread_id = 0
        self._hook_handle = None
        self._hook_proc_ref = None
        self._tap_install_ok = False
        with self._delta_callback_lock:
            self._delta_callback = None
        logger.info("WinMouseHook stopped")

    def set_exclusive(self, exclusive: bool) -> None:
        """
        Engage or release exclusive mode.

        On engage: hide the OS cursor and anchor _last_x/_last_y to center.
        On release: restore the OS cursor.

        The hook callback reads the flag under _paused_lock so the engage
        side-effects (cursor warp/hide) happen here on the calling thread
        while the callback's view of the flag flips atomically.
        """
        with self._paused_lock:
            was_exclusive = self._exclusive
            self._exclusive = exclusive

        if exclusive and not was_exclusive:
            # Entering exclusive: anchor at center so the first re-warp delta
            # is well-defined, and hide the cursor.
            _user32.SetCursorPos(self._center_x, self._center_y)
            self._last_x = self._center_x
            self._last_y = self._center_y
            if not self._cursor_hidden:
                # ShowCursor(FALSE) decrements the display counter; counter
                # < 0 hides the cursor. Track our hide so we never decrement
                # twice (which would require two paired TRUE calls to restore).
                _user32.ShowCursor(False)
                self._cursor_hidden = True
        elif not exclusive and was_exclusive:
            # Leaving exclusive: restore cursor visibility.
            if self._cursor_hidden:
                _user32.ShowCursor(True)
                self._cursor_hidden = False

        logger.debug("WinMouseHook exclusive=%s (was %s)", exclusive, was_exclusive)

    # ------------------------------------------------------------------
    # Hook thread
    # ------------------------------------------------------------------

    def _hook_loop(self) -> None:
        """
        Background thread entry point. Installs the WH_MOUSE_LL hook and runs
        a Win32 message pump until WM_QUIT arrives via stop().
        """
        self._hook_thread_id = _kernel32.GetCurrentThreadId()
        self._hook_proc_ref = _HOOKPROC(self._hook_proc)

        # hMod=GetModuleHandle(None) is the convention for global hooks
        # installed from a Python process. dwThreadId=0 means "all threads in
        # the desktop" which is what makes this a system-wide hook.
        hmod = _kernel32.GetModuleHandleW(None)
        self._hook_handle = _user32.SetWindowsHookExW(
            WH_MOUSE_LL,
            self._hook_proc_ref,
            hmod,
            0,
        )

        if not self._hook_handle:
            self._tap_install_ok = False
            self._tap_ready.set()
            return

        self._tap_install_ok = True
        self._tap_ready.set()
        logger.info("WH_MOUSE_LL installed, entering message loop")

        msg = wt.MSG()
        while True:
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0:
                # WM_QUIT received.
                break
            if ret == -1:
                logger.error("GetMessageW returned -1 (error=%d)", ctypes.get_last_error())
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))
            if self._stop_event.is_set():
                break

        if self._hook_handle:
            _user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None
        logger.info("WinMouseHook message loop exited")

    # ------------------------------------------------------------------
    # Hook callback
    # ------------------------------------------------------------------

    def _hook_proc(self, n_code: int, w_param: int, l_param: int) -> int:
        """
        Called by the OS on the hook thread for every mouse event.

        Returns:
          - CallNextHookEx(...) when the event should pass through to the
            rest of the hook chain and to local apps.
          - 1 (or any non-zero) to suppress the event.

        The Microsoft docs say nCode < 0 means "pass it on without
        processing". HC_ACTION (0) is the only nCode value WH_MOUSE_LL
        actually delivers in practice; we treat anything else as pass-through.
        """
        if n_code != HC_ACTION:
            return _user32.CallNextHookEx(self._hook_handle or 0, n_code, w_param, l_param)

        try:
            hook_struct = ctypes.cast(
                l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)
            ).contents
            injected = bool(hook_struct.flags & LLMHF_INJECTED)

            # Injected events come from synthetic sources -- pyautogui-style
            # injection, our own SetCursorPos rewarp, or anything else
            # generating events via SendInput/mouse_event. We do not want to
            # forward these (they would echo our own actions) or re-warp
            # from them (which would cause feedback). Pass through.
            if injected:
                return _user32.CallNextHookEx(self._hook_handle or 0, n_code, w_param, l_param)

            with self._paused_lock:
                currently_paused = self._paused
                currently_exclusive = self._exclusive

            emit_to_peer = not currently_paused
            drop_locally = currently_exclusive

            if w_param == WM_MOUSEMOVE:
                self._handle_motion(hook_struct, emit_to_peer, currently_exclusive)
            elif w_param in (
                WM_LBUTTONDOWN, WM_LBUTTONUP,
                WM_RBUTTONDOWN, WM_RBUTTONUP,
                WM_MBUTTONDOWN, WM_MBUTTONUP,
            ):
                if emit_to_peer:
                    self._handle_click(w_param)
            elif w_param == WM_MOUSEWHEEL:
                if emit_to_peer:
                    self._handle_scroll(hook_struct, horizontal=False)
            elif w_param == WM_MOUSEHWHEEL:
                if emit_to_peer:
                    self._handle_scroll(hook_struct, horizontal=True)

            if drop_locally:
                return 1
        except Exception:
            # Never let an exception propagate into Win32. Log and pass through.
            logger.exception("WinMouseHook callback raised")

        return _user32.CallNextHookEx(self._hook_handle or 0, n_code, w_param, l_param)

    def _handle_motion(
        self,
        hook_struct: _MSLLHOOKSTRUCT,
        emit_to_peer: bool,
        exclusive: bool,
    ) -> None:
        """Compute delta and (when exclusive) re-warp the cursor to center."""
        cur_x = hook_struct.pt.x
        cur_y = hook_struct.pt.y

        if exclusive:
            # Re-warp mode: delta is measured from center, then snap back.
            dx_px = cur_x - self._center_x
            dy_px = cur_y - self._center_y
            if dx_px != 0 or dy_px != 0:
                _user32.SetCursorPos(self._center_x, self._center_y)
            # After re-warp the cursor sits at center again. Update _last_*
            # so the first non-exclusive event after release reads a sane delta.
            self._last_x = self._center_x
            self._last_y = self._center_y
        else:
            # Non-exclusive: compute delta from prior hardware position.
            dx_px = cur_x - self._last_x
            dy_px = cur_y - self._last_y
            self._last_x = cur_x
            self._last_y = cur_y

        if not emit_to_peer:
            return
        if dx_px == 0 and dy_px == 0:
            return

        ndx: float = dx_px / self._vd_w
        ndy: float = dy_px / self._vd_h
        self._seq += 1
        delta: dict[str, Any] = {
            "type": "delta",
            "ndx": ndx,
            "ndy": ndy,
            "seq": self._seq,
        }
        with self._delta_callback_lock:
            cb = self._delta_callback
        if cb is not None:
            accepted = cb(delta)
            if not accepted:
                logger.debug("Hook delta seq=%d dropped (send queue full)", self._seq)

    def _handle_click(self, w_param: int) -> None:
        """Forward click events via the same wire format as the Mac tap."""
        if w_param == WM_LBUTTONDOWN:
            button, pressed = "left", True
        elif w_param == WM_LBUTTONUP:
            button, pressed = "left", False
        elif w_param == WM_RBUTTONDOWN:
            button, pressed = "right", True
        elif w_param == WM_RBUTTONUP:
            button, pressed = "right", False
        elif w_param == WM_MBUTTONDOWN:
            button, pressed = "middle", True
        elif w_param == WM_MBUTTONUP:
            button, pressed = "middle", False
        else:
            return

        msg = {"type": "click", "button": button, "pressed": pressed}
        with self._delta_callback_lock:
            cb = self._delta_callback
        if cb is not None:
            cb(msg)

    def _handle_scroll(self, hook_struct: _MSLLHOOKSTRUCT, *, horizontal: bool) -> None:
        """
        Forward scroll events. mouseData high word is a signed 16-bit scroll
        delta in multiples of WHEEL_DELTA (120). Convert to integer "clicks"
        so the wire format matches the Mac tap.
        """
        raw = ctypes.c_short((hook_struct.mouseData >> 16) & 0xFFFF).value
        units = raw // WHEEL_DELTA if raw else 0
        if units == 0:
            return
        if horizontal:
            dx, dy = units, 0
        else:
            dx, dy = 0, units
        msg = {"type": "scroll", "dx": dx, "dy": dy}
        with self._delta_callback_lock:
            cb = self._delta_callback
        if cb is not None:
            cb(msg)
