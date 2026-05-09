from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.handoff import HandoffEdge, normalize_edge


logger = logging.getLogger(__name__)


class DetectorState(str, Enum):
    IDLE = "idle"
    ARMED = "armed"
    PENDING = "pending"


@dataclass(frozen=True)
class DetectorConfig:
    edge: HandoffEdge
    dwell_ms: int = 400
    zone_px: int = 5
    screen_index: int = 0
    screen_x: int = 0
    screen_y: int = 0
    screen_width: int = 0   # 0 = fetch from pyautogui at runtime (backward compat)
    screen_height: int = 0

    def public_dict(self) -> dict:
        d: dict[str, Any] = {
            "edge": self.edge.value,
            "dwell_ms": self.dwell_ms,
            "zone_px": self.zone_px,
            "screen_index": self.screen_index,
        }
        if self.screen_width > 0:
            d["screen_x"] = self.screen_x
            d["screen_y"] = self.screen_y
            d["screen_width"] = self.screen_width
            d["screen_height"] = self.screen_height
        return d


def get_monitors() -> list[dict[str, Any]]:
    """Return list of {index, x, y, width, height, primary, name} for all displays."""
    try:
        if sys.platform == "darwin":
            return _get_monitors_macos()
        if sys.platform == "win32":
            return _get_monitors_windows()
    except Exception as exc:
        logger.debug("Monitor enumeration failed: %s", exc)
    try:
        import pyautogui
        size = pyautogui.size()
        return [{"index": 0, "x": 0, "y": 0, "width": size.width, "height": size.height, "primary": True, "name": "Primary Display"}]
    except Exception:
        return [{"index": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True, "name": "Display"}]


def _get_monitors_macos() -> list[dict[str, Any]]:
    from AppKit import NSScreen  # type: ignore[import]
    monitors: list[dict[str, Any]] = []
    main_screen = NSScreen.mainScreen()
    main_h = float(main_screen.frame().size.height)
    for i, screen in enumerate(NSScreen.screens()):
        f = screen.frame()
        x = int(f.origin.x)
        y = int(main_h - f.origin.y - f.size.height)
        w = int(f.size.width)
        h = int(f.size.height)
        try:
            name = str(screen.localizedName())
        except AttributeError:
            name = "Built-in Display" if screen == main_screen else f"Display {i + 1}"
        monitors.append({"index": i, "x": x, "y": y, "width": w, "height": h, "primary": screen == main_screen, "name": name})
    return monitors


def _get_monitors_windows() -> list[dict[str, Any]]:
    import ctypes
    import ctypes.wintypes
    monitors: list[dict[str, Any]] = []

    def _cb(hMonitor, hdcMonitor, lprcMonitor, dwData):
        r = lprcMonitor.contents
        monitors.append({
            "index": len(monitors),
            "x": int(r.left), "y": int(r.top),
            "width": int(r.right - r.left), "height": int(r.bottom - r.top),
            "primary": r.left == 0 and r.top == 0,
            "name": f"Display {len(monitors) + 1}",
        })
        return True

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(ctypes.wintypes.RECT), ctypes.c_double,
    )
    ctypes.windll.user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    return monitors


def make_detector_config(
    *,
    edge: object,
    dwell_ms: int = 400,
    zone_px: int = 5,
    screen_index: int = 0,
) -> Optional[DetectorConfig]:
    clean_edge = normalize_edge(edge)
    if clean_edge is None:
        return None
    clean_dwell = dwell_ms if isinstance(dwell_ms, int) and 0 <= dwell_ms <= 5000 else 400
    clean_zone = zone_px if isinstance(zone_px, int) and 1 <= zone_px <= 50 else 5
    clean_index = screen_index if isinstance(screen_index, int) and screen_index >= 0 else 0

    monitors = get_monitors()
    if clean_index >= len(monitors):
        clean_index = 0
    m = monitors[clean_index] if monitors else None

    if m is not None:
        return DetectorConfig(
            edge=clean_edge,
            dwell_ms=clean_dwell,
            zone_px=clean_zone,
            screen_index=clean_index,
            screen_x=m["x"],
            screen_y=m["y"],
            screen_width=m["width"],
            screen_height=m["height"],
        )
    return DetectorConfig(edge=clean_edge, dwell_ms=clean_dwell, zone_px=clean_zone)


def cursor_at_edge(
    cursor_x: int,
    cursor_y: int,
    screen_width: int,
    screen_height: int,
    edge: HandoffEdge,
    zone_px: int = 5,
    screen_x: int = 0,
    screen_y: int = 0,
) -> bool:
    # Ensure cursor is on this screen before checking edges.
    # This prevents false positives on multi-monitor setups where
    # a cursor on a secondary monitor can exceed primary screen bounds.
    if (
        screen_width > 0
        and screen_height > 0
        and not (
            screen_x <= cursor_x <= screen_x + screen_width
            and screen_y <= cursor_y <= screen_y + screen_height
        )
    ):
        return False
    if edge == HandoffEdge.LEFT:
        return cursor_x <= screen_x + zone_px
    if edge == HandoffEdge.RIGHT:
        return cursor_x >= screen_x + screen_width - 1 - zone_px
    if edge == HandoffEdge.TOP:
        return cursor_y <= screen_y + zone_px
    if edge == HandoffEdge.BOTTOM:
        return cursor_y >= screen_y + screen_height - 1 - zone_px
    return False


class EdgeDetector:
    POLL_INTERVAL = 0.016

    def __init__(self) -> None:
        self._state = DetectorState.IDLE
        self._config: Optional[DetectorConfig] = None
        self._task: Optional[asyncio.Task] = None
        self._dwell_start: Optional[float] = None

    def state(self) -> DetectorState:
        return self._state

    def public_dict(self) -> dict:
        progress: Optional[float] = None
        if (
            self._state == DetectorState.ARMED
            and self._dwell_start is not None
            and self._config is not None
        ):
            elapsed_ms = (time.monotonic() - self._dwell_start) * 1000
            progress = round(min(1.0, elapsed_ms / self._config.dwell_ms), 3)
        return {
            "state": self._state.value,
            "config": self._config.public_dict() if self._config else None,
            "dwell_progress": progress,
        }

    async def arm(self, config: DetectorConfig) -> None:
        await self._cancel_task()
        self._config = config
        self._state = DetectorState.ARMED
        self._dwell_start = None
        self._task = asyncio.create_task(self._run())

    async def disarm(self) -> None:
        await self._cancel_task()
        self._state = DetectorState.IDLE
        self._config = None
        self._dwell_start = None

    async def _run(self) -> None:
        config = self._config
        if config is None:
            return
        try:
            pyautogui = _get_pyautogui()
        except RuntimeError as exc:
            logger.warning("Edge detector: desktop backend unavailable: %s", exc)
            self._state = DetectorState.IDLE
            self._dwell_start = None
            return

        while True:
            await asyncio.sleep(self.POLL_INTERVAL)
            if self._state != DetectorState.ARMED:
                return
            try:
                pos = pyautogui.position()
                if config.screen_width > 0:
                    sw, sh = config.screen_width, config.screen_height
                    sx, sy = config.screen_x, config.screen_y
                else:
                    size = pyautogui.size()
                    sw, sh = size.width, size.height
                    sx, sy = 0, 0
            except Exception as exc:
                logger.debug("Edge detector: position/size read failed: %s", exc)
                continue

            at_edge = cursor_at_edge(pos.x, pos.y, sw, sh, config.edge, config.zone_px, sx, sy)

            if at_edge:
                if config.dwell_ms == 0:
                    self._state = DetectorState.PENDING
                    logger.info(
                        "Edge detector: instant trigger on %s edge (screen %d).",
                        config.edge.value,
                        config.screen_index,
                    )
                    return
                if self._dwell_start is None:
                    self._dwell_start = time.monotonic()
                elif (time.monotonic() - self._dwell_start) * 1000 >= config.dwell_ms:
                    self._state = DetectorState.PENDING
                    logger.info(
                        "Edge detector: dwell complete on %s edge (screen %d) after %dms.",
                        config.edge.value,
                        config.screen_index,
                        config.dwell_ms,
                    )
                    return
            else:
                self._dwell_start = None

    async def _cancel_task(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


def _get_pyautogui():
    try:
        import pyautogui
        return pyautogui
    except Exception as exc:
        raise RuntimeError("Desktop input backend unavailable.") from exc
