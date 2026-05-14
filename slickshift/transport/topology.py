"""
Slickshift -- monitor topology data model and local enumeration.

MonitorInfo captures per-monitor geometry as seen by the virtual desktop
coordinate system (logical pixels). It is serialized to/from JSON dicts
when exchanged in the hello handshake so both peers know each other's
full monitor layout before coordinate math begins.

Placement in transport/ is intentional: topology data crosses the wire, so
its definition belongs next to the framing/serialization layer rather than
in UI or system helpers.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from PyQt6.QtGui import QGuiApplication, QScreen

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class MonitorInfo:
    """
    Geometry and identity of a single monitor as seen by the virtual desktop.

    All dimensions are in logical pixels (i.e. the coordinate space that
    pyautogui, Win32 MONITORINFO, and Qt's QScreen.geometry() all agree on
    after DPI virtualisation is applied).

    Fields:
        x           -- left edge of the monitor in virtual-desktop coordinates
        y           -- top edge of the monitor in virtual-desktop coordinates
        width       -- logical width in pixels
        height      -- logical height in pixels
        dpi_scale   -- physical-to-logical ratio (1.0 = non-HiDPI, 2.0 = Retina,
                       varies on Windows per-monitor DPI)
        is_primary  -- True on exactly one monitor in the list (the primary/main display)
    """

    x: int
    y: int
    width: int
    height: int
    dpi_scale: float
    is_primary: bool

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict suitable for inclusion in the hello payload."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitorInfo":
        """
        Reconstruct a MonitorInfo from a wire-format dict.

        Unknown keys in data are silently ignored so that future protocol
        additions do not break older receivers.
        """
        return cls(
            x=int(data["x"]),
            y=int(data["y"]),
            width=int(data["width"]),
            height=int(data["height"]),
            dpi_scale=float(data["dpi_scale"]),
            is_primary=bool(data["is_primary"]),
        )


def enumerate_local_monitors() -> list[MonitorInfo]:
    """
    Build a list of MonitorInfo objects describing every screen attached to
    this machine, using Qt's QGuiApplication.screens().

    Requires a QApplication (or QGuiApplication) to already be running.
    Returns an empty list and logs a warning if no application instance exists.

    The list is ordered with the primary monitor first, followed by secondary
    monitors in the order Qt reports them.

    QScreen.geometry() returns the monitor's position and size in logical
    pixels within the virtual desktop coordinate space. This is the same
    coordinate space used by pyautogui.position() and pyautogui.moveTo(),
    so no unit conversion is needed.
    """
    app: QGuiApplication | None = QGuiApplication.instance()  # type: ignore[assignment]
    if app is None:
        logger.warning(
            "enumerate_local_monitors() called before QGuiApplication exists; "
            "returning empty list"
        )
        return []

    primary: QScreen | None = QGuiApplication.primaryScreen()
    screens: list[QScreen] = QGuiApplication.screens()

    monitors: list[MonitorInfo] = []

    # Emit the primary screen first so index-0 is always the primary.
    if primary is not None:
        geom = primary.geometry()
        monitors.append(
            MonitorInfo(
                x=geom.x(),
                y=geom.y(),
                width=geom.width(),
                height=geom.height(),
                dpi_scale=round(primary.devicePixelRatio(), 4),
                is_primary=True,
            )
        )
        logger.debug(
            "Primary monitor: %dx%d at (%d, %d) dpi_scale=%.4f",
            geom.width(),
            geom.height(),
            geom.x(),
            geom.y(),
            primary.devicePixelRatio(),
        )

    for screen in screens:
        if screen is primary:
            continue  # already added above
        geom = screen.geometry()
        monitors.append(
            MonitorInfo(
                x=geom.x(),
                y=geom.y(),
                width=geom.width(),
                height=geom.height(),
                dpi_scale=round(screen.devicePixelRatio(), 4),
                is_primary=False,
            )
        )
        logger.debug(
            "Secondary monitor: %dx%d at (%d, %d) dpi_scale=%.4f",
            geom.width(),
            geom.height(),
            geom.x(),
            geom.y(),
            screen.devicePixelRatio(),
        )

    logger.debug("enumerate_local_monitors(): found %d monitor(s)", len(monitors))
    return monitors
