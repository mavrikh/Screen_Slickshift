"""
cursor-bridge-test -- pynput-based mouse button and scroll event capture.

Runs a pynput.mouse.Listener in a background thread. Fires registered callbacks
when button press/release or scroll events occur.

This module is independent of the polling-based DeltaCapture (motion). Both run
concurrently on the sender side. pynput and pyautogui coexist fine; on macOS
both require Accessibility permission (already granted alongside pyautogui).

Event gating: set_active(False) suppresses callback delivery without stopping the
listener thread. This gate must be flipped to True only while in CAPTURING state so
clicks and scrolls are not forwarded during idle periods.

Thread safety: on_click and on_scroll are called from the pynput listener thread.
Callers must supply callbacks that are thread-safe (e.g., emit a pyqtSignal rather
than touching Qt widgets directly).

Known limitation: pynput does not suppress the event from reaching local
applications (exclusive capture mode is deferred per roadmap). A click forwarded
to the receiver will also fire on the sender machine's own apps.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from pynput import mouse as _pynput_mouse

logger = logging.getLogger(__name__)

# Type aliases for the callbacks.
# ClickCallback(button_name: str, pressed: bool) -- called on button down/up.
# ScrollCallback(dx: int, dy: int)               -- called on scroll wheel event.
ClickCallback = Callable[[str, bool], None]
ScrollCallback = Callable[[int, int], None]

# Map pynput button objects to their canonical name strings.
_BUTTON_NAMES: dict[_pynput_mouse.Button, str] = {
    _pynput_mouse.Button.left: "left",
    _pynput_mouse.Button.right: "right",
    _pynput_mouse.Button.middle: "middle",
}


class EventCapture:
    """
    Cross-platform mouse button and scroll event capture via pynput.

    Lifecycle:
        ec = EventCapture(on_click=..., on_scroll=...)
        ec.start()           # starts listener thread
        ec.set_active(True)  # enable event forwarding when entering CAPTURING
        ...
        ec.set_active(False) # gate off when leaving CAPTURING
        ...
        ec.stop()            # join listener thread on window close

    The listener thread runs for the lifetime of the application once start() is
    called. Only the _active gate determines whether events reach the callbacks.
    """

    def __init__(
        self,
        on_click: ClickCallback,
        on_scroll: ScrollCallback,
    ) -> None:
        self._on_click = on_click
        self._on_scroll = on_scroll
        self._active: bool = False
        self._listener: _pynput_mouse.Listener | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        Start the pynput listener in its own background thread.

        Events are delivered to _pynput_on_click and _pynput_on_scroll, which
        check the _active gate before calling the user callbacks. The listener
        starts gated off (inactive) -- call set_active(True) to open the gate.
        """
        if self._listener is not None:
            logger.warning("EventCapture.start() called while listener already running -- ignored")
            return

        self._listener = _pynput_mouse.Listener(
            on_click=self._pynput_on_click,
            on_scroll=self._pynput_on_scroll,
            on_move=None,  # motion is handled by DeltaCapture's polling loop
        )
        self._listener.start()
        logger.info("EventCapture started (gated off until set_active(True))")

    def stop(self) -> None:
        """
        Stop the pynput listener and join its thread.

        Call this on MainWindow close. Idempotent if already stopped.
        pynput's Listener.stop() signals the thread; join() waits for clean exit.
        """
        if self._listener is None:
            return
        self._listener.stop()
        self._listener.join()
        self._listener = None
        logger.info("EventCapture stopped")

    def set_active(self, active: bool) -> None:
        """
        Open or close the event-forwarding gate.

        When active=False, incoming pynput events are discarded before reaching
        the registered callbacks. When active=True, events are forwarded.

        Call set_active(True) when entering CAPTURING state.
        Call set_active(False) when leaving CAPTURING (any direction).
        """
        self._active = active
        logger.debug("EventCapture active=%s", active)

    # ------------------------------------------------------------------
    # pynput callbacks (called on the listener thread)
    # ------------------------------------------------------------------

    def _pynput_on_click(
        self,
        x: int,
        y: int,
        button: _pynput_mouse.Button,
        pressed: bool,
    ) -> None:
        """
        Fires on every mouse button press and release.

        x, y are the current cursor coordinates (not forwarded -- position is
        handled by DeltaCapture). button is a pynput Button enum; we convert it
        to a canonical string ("left", "right", "middle"). Unknown buttons are
        logged at DEBUG and dropped.
        """
        if not self._active:
            return

        button_name = _BUTTON_NAMES.get(button)
        if button_name is None:
            logger.debug("EventCapture: unknown button %r -- dropped", button)
            return

        logger.debug(
            "EventCapture click: button=%s pressed=%s pos=(%d,%d)",
            button_name, pressed, x, y,
        )
        self._on_click(button_name, pressed)

    def _pynput_on_scroll(
        self,
        x: int,
        y: int,
        dx: int,
        dy: int,
    ) -> None:
        """
        Fires on every scroll wheel event.

        dx is horizontal scroll delta (positive = right), dy is vertical scroll
        delta (positive = up on most platforms). Values are integer pynput units;
        passed through directly to the callback.
        """
        if not self._active:
            return

        logger.debug(
            "EventCapture scroll: dx=%d dy=%d pos=(%d,%d)",
            dx, dy, x, y,
        )
        self._on_scroll(dx, dy)
