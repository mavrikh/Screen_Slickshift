from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional


class HandoffEdge(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"


class HandoffMode(str, Enum):
    IDLE = "idle"
    ARMED = "armed"
    PENDING_HANDOFF = "pending_handoff"
    ACTIVE_REMOTE = "active_remote"
    RETURNING = "returning"


OPPOSITE_EDGE = {
    HandoffEdge.LEFT: HandoffEdge.RIGHT,
    HandoffEdge.RIGHT: HandoffEdge.LEFT,
    HandoffEdge.TOP: HandoffEdge.BOTTOM,
    HandoffEdge.BOTTOM: HandoffEdge.TOP,
}


@dataclass(frozen=True)
class HandoffTarget:
    target_id: str
    name: str
    host: str
    port: int

    def public_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
        }


@dataclass(frozen=True)
class ScreenRect:
    x: int
    y: int
    width: int
    height: int

    @property
    def left(self) -> int:
        return self.x

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def top(self) -> int:
        return self.y

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def is_valid(self) -> bool:
        return self.width > 0 and self.height > 0

    def public_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class LayoutScreen:
    screen_id: str
    device_id: str
    name: str
    rect: ScreenRect
    primary: bool = False
    edge_enabled: bool = True

    def is_valid(self) -> bool:
        return bool(self.screen_id) and bool(self.device_id) and self.rect.is_valid()

    def public_dict(self) -> dict:
        return {
            "screen_id": self.screen_id,
            "device_id": self.device_id,
            "name": self.name,
            "rect": self.rect.public_dict(),
            "primary": self.primary,
            "edge_enabled": self.edge_enabled,
        }


@dataclass(frozen=True)
class HandoffRoute:
    from_screen_id: str
    to_screen_id: str
    from_device_id: str
    to_device_id: str
    exit_edge: HandoffEdge
    enter_edge: HandoffEdge
    overlap_px: int

    def public_dict(self) -> dict:
        return {
            "from_screen_id": self.from_screen_id,
            "to_screen_id": self.to_screen_id,
            "from_device_id": self.from_device_id,
            "to_device_id": self.to_device_id,
            "exit_edge": self.exit_edge.value,
            "enter_edge": self.enter_edge.value,
            "overlap_px": self.overlap_px,
        }


@dataclass(frozen=True)
class HandoffLayout:
    screens: tuple[LayoutScreen, ...] = ()
    snap_tolerance_px: int = 24
    min_overlap_px: int = 80

    def valid_screens(self) -> tuple[LayoutScreen, ...]:
        return tuple(screen for screen in self.screens if screen.is_valid())

    def public_dict(self) -> dict:
        return {
            "screens": [screen.public_dict() for screen in self.valid_screens()],
            "snap_tolerance_px": self.snap_tolerance_px,
            "min_overlap_px": self.min_overlap_px,
            "overlaps": [overlap.public_dict() for overlap in find_layout_overlaps(self)],
            "routes": [route.public_dict() for route in derive_handoff_routes(self)],
        }


@dataclass(frozen=True)
class HandoffConfig:
    enabled: bool = False
    target_id: Optional[str] = None
    edge: Optional[HandoffEdge] = None
    dwell_ms: int = 400

    def is_ready(self) -> bool:
        return self.enabled and bool(self.target_id) and self.edge is not None

    def public_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "target_id": self.target_id,
            "edge": self.edge.value if self.edge else None,
            "dwell_ms": self.dwell_ms,
            "ready": self.is_ready(),
        }


@dataclass(frozen=True)
class LayoutOverlap:
    first_screen_id: str
    second_screen_id: str
    overlap_width: int
    overlap_height: int

    @property
    def area(self) -> int:
        return self.overlap_width * self.overlap_height

    def public_dict(self) -> dict:
        return {
            "first_screen_id": self.first_screen_id,
            "second_screen_id": self.second_screen_id,
            "overlap_width": self.overlap_width,
            "overlap_height": self.overlap_height,
            "area": self.area,
        }


@dataclass(frozen=True)
class HandoffState:
    mode: HandoffMode = HandoffMode.IDLE
    active_target_id: Optional[str] = None
    active_edge: Optional[HandoffEdge] = None
    notice: str = "Handoff is idle."

    def public_dict(self) -> dict:
        return {
            "mode": self.mode.value,
            "active_target_id": self.active_target_id,
            "active_edge": self.active_edge.value if self.active_edge else None,
            "notice": self.notice,
        }


def normalize_edge(value: object) -> Optional[HandoffEdge]:
    if isinstance(value, HandoffEdge):
        return value
    if not isinstance(value, str):
        return None
    try:
        return HandoffEdge(value.strip().lower())
    except ValueError:
        return None


def normalize_screen_rect(value: object) -> Optional[ScreenRect]:
    if isinstance(value, ScreenRect):
        return value if value.is_valid() else None
    if not isinstance(value, dict):
        return None
    try:
        rect = ScreenRect(
            x=int(value.get("x", 0)),
            y=int(value.get("y", 0)),
            width=int(value.get("width", 0)),
            height=int(value.get("height", 0)),
        )
    except (TypeError, ValueError):
        return None
    return rect if rect.is_valid() else None


def make_layout_screen(
    *,
    screen_id: str,
    device_id: str,
    name: str,
    rect: object,
    primary: bool = False,
    edge_enabled: bool = True,
) -> Optional[LayoutScreen]:
    clean_rect = normalize_screen_rect(rect)
    clean_screen_id = screen_id.strip() if isinstance(screen_id, str) else ""
    clean_device_id = device_id.strip() if isinstance(device_id, str) else ""
    clean_name = name.strip() if isinstance(name, str) and name.strip() else clean_device_id
    if clean_rect is None or not clean_screen_id or not clean_device_id:
        return None
    return LayoutScreen(
        screen_id=clean_screen_id,
        device_id=clean_device_id,
        name=clean_name,
        rect=clean_rect,
        primary=bool(primary),
        edge_enabled=bool(edge_enabled),
    )


def make_handoff_layout(
    screens: list[LayoutScreen],
    *,
    snap_tolerance_px: int = 24,
    min_overlap_px: int = 80,
) -> HandoffLayout:
    clean_snap_tolerance = (
        snap_tolerance_px
        if isinstance(snap_tolerance_px, int) and 0 <= snap_tolerance_px <= 200
        else 24
    )
    clean_min_overlap = min_overlap_px if isinstance(min_overlap_px, int) and min_overlap_px > 0 else 80
    return HandoffLayout(
        screens=tuple(screen for screen in screens if screen.is_valid()),
        snap_tolerance_px=clean_snap_tolerance,
        min_overlap_px=clean_min_overlap,
    )


def derive_handoff_routes(layout: HandoffLayout) -> tuple[HandoffRoute, ...]:
    routes: list[HandoffRoute] = []
    screens = tuple(screen for screen in layout.valid_screens() if screen.edge_enabled)
    for source in screens:
        for target in screens:
            if source.screen_id == target.screen_id:
                continue
            if source.device_id == target.device_id:
                continue
            route = _derive_route(source, target, layout.snap_tolerance_px, layout.min_overlap_px)
            if route is not None:
                routes.append(route)
    return tuple(routes)


def snap_screen_rect(
    screen_id: str,
    rect: ScreenRect,
    screens: tuple[LayoutScreen, ...],
    snap_tolerance_px: int = 24,
    min_overlap_px: int = 80,
) -> ScreenRect:
    if not rect.is_valid():
        return rect

    candidates: list[tuple[int, ScreenRect]] = []
    for screen in screens:
        if screen.screen_id == screen_id or not screen.is_valid():
            continue

        horizontal_y = _clamp(rect.y, screen.rect.top - rect.height + min_overlap_px, screen.rect.bottom - min_overlap_px)
        vertical_x = _clamp(rect.x, screen.rect.left - rect.width + min_overlap_px, screen.rect.right - min_overlap_px)

        right_snap = ScreenRect(
            x=screen.rect.left - rect.width,
            y=horizontal_y,
            width=rect.width,
            height=rect.height,
        )
        _add_snap_candidate(candidates, rect, right_snap, screen.rect, HandoffEdge.RIGHT, snap_tolerance_px, min_overlap_px)

        left_snap = ScreenRect(
            x=screen.rect.right,
            y=horizontal_y,
            width=rect.width,
            height=rect.height,
        )
        _add_snap_candidate(candidates, rect, left_snap, screen.rect, HandoffEdge.LEFT, snap_tolerance_px, min_overlap_px)

        bottom_snap = ScreenRect(
            x=vertical_x,
            y=screen.rect.top - rect.height,
            width=rect.width,
            height=rect.height,
        )
        _add_snap_candidate(candidates, rect, bottom_snap, screen.rect, HandoffEdge.BOTTOM, snap_tolerance_px, min_overlap_px)

        top_snap = ScreenRect(
            x=vertical_x,
            y=screen.rect.bottom,
            width=rect.width,
            height=rect.height,
        )
        _add_snap_candidate(candidates, rect, top_snap, screen.rect, HandoffEdge.TOP, snap_tolerance_px, min_overlap_px)

    if not candidates:
        return rect
    return sorted(candidates, key=lambda item: item[0])[0][1]


def snap_layout_screens(
    screens: tuple[LayoutScreen, ...],
    snap_tolerance_px: int = 10000,
    min_overlap_px: int = 80,
) -> tuple[LayoutScreen, ...]:
    valid_screens = tuple(screen for screen in screens if screen.is_valid())
    if len(valid_screens) <= 1:
        return valid_screens

    placed: list[LayoutScreen] = [valid_screens[0]]
    for screen in valid_screens[1:]:
        snapped_rect = snap_screen_rect(
            screen.screen_id,
            screen.rect,
            tuple(placed),
            snap_tolerance_px=snap_tolerance_px,
            min_overlap_px=min_overlap_px,
        )
        if any(_rects_overlap(snapped_rect, placed_screen.rect) for placed_screen in placed):
            snapped_rect = place_screen_without_overlap(snapped_rect, tuple(placed))
        placed.append(replace(screen, rect=snapped_rect))
    return tuple(placed)


def place_screen_without_overlap(
    rect: ScreenRect,
    screens: tuple[LayoutScreen, ...],
    gap_px: int = 0,
) -> ScreenRect:
    if not rect.is_valid() or not screens:
        return rect

    candidates: list[tuple[int, ScreenRect]] = []
    for screen in screens:
        if not screen.is_valid():
            continue
        candidates.extend(
            [
                (
                    0,
                    ScreenRect(
                        x=screen.rect.right + gap_px,
                        y=screen.rect.y,
                        width=rect.width,
                        height=rect.height,
                    ),
                ),
                (
                    1,
                    ScreenRect(
                        x=screen.rect.left - rect.width - gap_px,
                        y=screen.rect.y,
                        width=rect.width,
                        height=rect.height,
                    ),
                ),
                (
                    2,
                    ScreenRect(
                        x=screen.rect.x,
                        y=screen.rect.bottom + gap_px,
                        width=rect.width,
                        height=rect.height,
                    ),
                ),
                (
                    3,
                    ScreenRect(
                        x=screen.rect.x,
                        y=screen.rect.top - rect.height - gap_px,
                        width=rect.width,
                        height=rect.height,
                    ),
                ),
            ]
        )

    for _, candidate in sorted(
        candidates,
        key=lambda item: (item[0], abs(item[1].x - rect.x) + abs(item[1].y - rect.y)),
    ):
        if not any(_rects_overlap(candidate, screen.rect) for screen in screens if screen.is_valid()):
            return candidate

    step_x = rect.width + gap_px
    step_y = rect.height + gap_px
    for row in range(0, 8):
        for column in range(0, 8):
            candidate = ScreenRect(
                x=rect.x + column * step_x,
                y=rect.y + row * step_y,
                width=rect.width,
                height=rect.height,
            )
            if not any(_rects_overlap(candidate, screen.rect) for screen in screens if screen.is_valid()):
                return candidate

    return rect


def find_layout_overlaps(layout: HandoffLayout) -> tuple[LayoutOverlap, ...]:
    overlaps: list[LayoutOverlap] = []
    screens = layout.valid_screens()
    for index, first in enumerate(screens):
        for second in screens[index + 1 :]:
            overlap = _screen_overlap(first, second)
            if overlap is not None:
                overlaps.append(overlap)
    return tuple(overlaps)


def _add_snap_candidate(
    candidates: list[tuple[int, ScreenRect]],
    original: ScreenRect,
    snapped: ScreenRect,
    target: ScreenRect,
    exit_edge: HandoffEdge,
    snap_tolerance_px: int,
    min_overlap_px: int,
) -> None:
    if exit_edge in (HandoffEdge.LEFT, HandoffEdge.RIGHT):
        overlap = _range_overlap(snapped.top, snapped.bottom, target.top, target.bottom)
    else:
        overlap = _range_overlap(snapped.left, snapped.right, target.left, target.right)

    distance = abs(original.x - snapped.x) + abs(original.y - snapped.y)
    if distance <= snap_tolerance_px and overlap >= min_overlap_px:
        candidates.append((distance, snapped))


def _derive_route(
    source: LayoutScreen,
    target: LayoutScreen,
    snap_tolerance_px: int,
    min_overlap_px: int,
) -> Optional[HandoffRoute]:
    horizontal_overlap = _range_overlap(source.rect.top, source.rect.bottom, target.rect.top, target.rect.bottom)
    vertical_overlap = _range_overlap(source.rect.left, source.rect.right, target.rect.left, target.rect.right)

    if abs(source.rect.right - target.rect.left) <= snap_tolerance_px and horizontal_overlap >= min_overlap_px:
        return _route(source, target, HandoffEdge.RIGHT, horizontal_overlap)
    if abs(source.rect.left - target.rect.right) <= snap_tolerance_px and horizontal_overlap >= min_overlap_px:
        return _route(source, target, HandoffEdge.LEFT, horizontal_overlap)
    if abs(source.rect.bottom - target.rect.top) <= snap_tolerance_px and vertical_overlap >= min_overlap_px:
        return _route(source, target, HandoffEdge.BOTTOM, vertical_overlap)
    if abs(source.rect.top - target.rect.bottom) <= snap_tolerance_px and vertical_overlap >= min_overlap_px:
        return _route(source, target, HandoffEdge.TOP, vertical_overlap)
    return None


def _screen_overlap(first: LayoutScreen, second: LayoutScreen) -> Optional[LayoutOverlap]:
    overlap_width = _range_overlap(first.rect.left, first.rect.right, second.rect.left, second.rect.right)
    overlap_height = _range_overlap(first.rect.top, first.rect.bottom, second.rect.top, second.rect.bottom)
    if overlap_width <= 0 or overlap_height <= 0:
        return None
    return LayoutOverlap(
        first_screen_id=first.screen_id,
        second_screen_id=second.screen_id,
        overlap_width=overlap_width,
        overlap_height=overlap_height,
    )


def _rects_overlap(first: ScreenRect, second: ScreenRect) -> bool:
    return (
        first.left < second.right
        and first.right > second.left
        and first.top < second.bottom
        and first.bottom > second.top
    )


def _route(source: LayoutScreen, target: LayoutScreen, exit_edge: HandoffEdge, overlap_px: int) -> HandoffRoute:
    return HandoffRoute(
        from_screen_id=source.screen_id,
        to_screen_id=target.screen_id,
        from_device_id=source.device_id,
        to_device_id=target.device_id,
        exit_edge=exit_edge,
        enter_edge=OPPOSITE_EDGE[exit_edge],
        overlap_px=overlap_px,
    )


def _range_overlap(first_start: int, first_end: int, second_start: int, second_end: int) -> int:
    return max(0, min(first_end, second_end) - max(first_start, second_start))


def _clamp(value: int, minimum: int, maximum: int) -> int:
    if minimum > maximum:
        return value
    return min(max(value, minimum), maximum)


def configure_handoff(
    *,
    enabled: bool = False,
    target_id: Optional[str] = None,
    edge: object = None,
    dwell_ms: int = 400,
) -> HandoffConfig:
    clean_target_id = target_id.strip() if isinstance(target_id, str) and target_id.strip() else None
    clean_edge = normalize_edge(edge)
    clean_dwell_ms = dwell_ms if isinstance(dwell_ms, int) and 100 <= dwell_ms <= 5000 else 400
    return HandoffConfig(
        enabled=bool(enabled),
        target_id=clean_target_id,
        edge=clean_edge,
        dwell_ms=clean_dwell_ms,
    )


def arm_handoff(state: HandoffState, config: HandoffConfig) -> HandoffState:
    if state.mode == HandoffMode.ACTIVE_REMOTE:
        return replace(state, notice="Cannot arm while remote control is active.")
    if not config.is_ready():
        return replace(state, notice="Handoff needs an enabled target and edge before arming.")
    return HandoffState(
        mode=HandoffMode.ARMED,
        active_target_id=config.target_id,
        active_edge=config.edge,
        notice="Handoff armed.",
    )


def disarm_handoff(state: HandoffState) -> HandoffState:
    if state.mode == HandoffMode.ACTIVE_REMOTE:
        return replace(state, notice="Stop remote control before disarming.")
    return HandoffState(notice="Handoff disarmed.")


def begin_pending_handoff(state: HandoffState, crossed_edge: object) -> HandoffState:
    edge = normalize_edge(crossed_edge)
    if state.mode != HandoffMode.ARMED:
        return replace(state, notice="Handoff is not armed.")
    if edge is None or edge != state.active_edge:
        return replace(state, notice="Ignored non-configured edge.")
    return replace(state, mode=HandoffMode.PENDING_HANDOFF, notice="Handoff pending.")


def confirm_handoff(state: HandoffState) -> HandoffState:
    if state.mode != HandoffMode.PENDING_HANDOFF:
        return replace(state, notice="No pending handoff to confirm.")
    return replace(state, mode=HandoffMode.ACTIVE_REMOTE, notice="Remote control active.")


def stop_remote_control(state: HandoffState, reason: str = "Local stop requested.") -> HandoffState:
    if state.mode != HandoffMode.ACTIVE_REMOTE:
        return HandoffState(notice=reason)
    return replace(state, mode=HandoffMode.RETURNING, notice=reason)


def finish_returning(state: HandoffState) -> HandoffState:
    if state.mode != HandoffMode.RETURNING:
        return replace(state, notice="No return is in progress.")
    return HandoffState(notice="Local control restored.")
