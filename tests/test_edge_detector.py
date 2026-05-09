from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.edge_detector import (
    DetectorConfig,
    DetectorState,
    EdgeDetector,
    cursor_at_edge,
    make_detector_config,
)
from app.handoff import HandoffEdge


# ---------------------------------------------------------------------------
# cursor_at_edge — pure geometry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("x,y,expected", [
    (0, 500, True),   # exactly on left border
    (5, 500, True),   # within zone
    (6, 500, False),  # just outside zone
    (960, 540, False),  # center
])
def test_cursor_at_left_edge(x, y, expected) -> None:
    assert cursor_at_edge(x, y, 1920, 1080, HandoffEdge.LEFT, zone_px=5) is expected


@pytest.mark.parametrize("x,y,expected", [
    (1919, 500, True),   # exactly on right border
    (1914, 500, True),   # within zone (1919 - 5 = 1914)
    (1913, 500, False),  # just outside zone
])
def test_cursor_at_right_edge(x, y, expected) -> None:
    assert cursor_at_edge(x, y, 1920, 1080, HandoffEdge.RIGHT, zone_px=5) is expected


@pytest.mark.parametrize("x,y,expected", [
    (500, 0, True),
    (500, 5, True),
    (500, 6, False),
])
def test_cursor_at_top_edge(x, y, expected) -> None:
    assert cursor_at_edge(x, y, 1920, 1080, HandoffEdge.TOP, zone_px=5) is expected


@pytest.mark.parametrize("x,y,expected", [
    (500, 1079, True),
    (500, 1074, True),   # 1079 - 5 = 1074
    (500, 1073, False),
])
def test_cursor_at_bottom_edge(x, y, expected) -> None:
    assert cursor_at_edge(x, y, 1920, 1080, HandoffEdge.BOTTOM, zone_px=5) is expected


def test_cursor_at_edge_custom_zone() -> None:
    assert cursor_at_edge(10, 500, 1920, 1080, HandoffEdge.LEFT, zone_px=10) is True
    assert cursor_at_edge(10, 500, 1920, 1080, HandoffEdge.LEFT, zone_px=5) is False


# ---------------------------------------------------------------------------
# make_detector_config — input validation
# ---------------------------------------------------------------------------

def test_make_detector_config_valid() -> None:
    config = make_detector_config(edge="right", dwell_ms=500, zone_px=8)
    assert config is not None
    assert config.edge == HandoffEdge.RIGHT
    assert config.dwell_ms == 500
    assert config.zone_px == 8


def test_make_detector_config_invalid_edge_returns_none() -> None:
    assert make_detector_config(edge="diagonal") is None
    assert make_detector_config(edge=None) is None


def test_make_detector_config_clamps_dwell() -> None:
    config = make_detector_config(edge="left", dwell_ms=9999)
    assert config is not None
    assert config.dwell_ms == 400

    config = make_detector_config(edge="left", dwell_ms=50)
    assert config is not None
    assert config.dwell_ms == 400


def test_make_detector_config_clamps_zone() -> None:
    config = make_detector_config(edge="left", zone_px=0)
    assert config is not None
    assert config.zone_px == 5

    config = make_detector_config(edge="left", zone_px=100)
    assert config is not None
    assert config.zone_px == 5


def test_detector_config_public_dict() -> None:
    config = DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=400, zone_px=5)
    d = config.public_dict()
    assert d["edge"] == "left"
    assert d["dwell_ms"] == 400
    assert d["zone_px"] == 5
    assert d["screen_index"] == 0


def test_detector_config_public_dict_includes_screen_rect_when_set() -> None:
    config = DetectorConfig(
        edge=HandoffEdge.RIGHT, dwell_ms=400, zone_px=5,
        screen_index=1, screen_x=2560, screen_y=0, screen_width=1920, screen_height=1080,
    )
    d = config.public_dict()
    assert d["screen_index"] == 1
    assert d["screen_x"] == 2560
    assert d["screen_width"] == 1920


def test_detector_config_public_dict_omits_screen_rect_when_zero() -> None:
    config = DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=400, zone_px=5)
    d = config.public_dict()
    assert "screen_x" not in d
    assert "screen_width" not in d


# ---------------------------------------------------------------------------
# EdgeDetector state transitions
# ---------------------------------------------------------------------------

def test_detector_starts_idle() -> None:
    detector = EdgeDetector()
    assert detector.state() == DetectorState.IDLE


def test_detector_public_dict_idle() -> None:
    detector = EdgeDetector()
    d = detector.public_dict()
    assert d["state"] == "idle"
    assert d["config"] is None
    assert d["dwell_progress"] is None


@pytest.mark.anyio
async def test_detector_public_dict_reports_dwell_progress_while_at_edge(monkeypatch) -> None:
    _patch_pyautogui_at_left_edge(monkeypatch)
    detector = EdgeDetector()
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=400, zone_px=5))
    await asyncio.sleep(0.1)
    d = detector.public_dict()
    assert d["state"] == "armed"
    assert d["dwell_progress"] is not None
    assert 0.0 < d["dwell_progress"] <= 1.0
    await detector.disarm()


@pytest.mark.anyio
async def test_detector_public_dict_dwell_progress_is_none_when_not_at_edge(monkeypatch) -> None:
    _patch_pyautogui_never_at_edge(monkeypatch)
    detector = EdgeDetector()
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=400, zone_px=5))
    await asyncio.sleep(0.05)
    d = detector.public_dict()
    assert d["dwell_progress"] is None
    await detector.disarm()


@pytest.mark.anyio
async def test_disarm_clears_dwell_progress(monkeypatch) -> None:
    _patch_pyautogui_at_left_edge(monkeypatch)
    detector = EdgeDetector()
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=400, zone_px=5))
    await asyncio.sleep(0.1)
    assert detector.public_dict()["dwell_progress"] is not None
    await detector.disarm()
    assert detector.public_dict()["dwell_progress"] is None


@pytest.mark.anyio
async def test_arm_sets_armed_state(monkeypatch) -> None:
    _patch_pyautogui_never_at_edge(monkeypatch)
    detector = EdgeDetector()
    config = DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=5000)
    await detector.arm(config)
    assert detector.state() == DetectorState.ARMED
    await detector.disarm()


@pytest.mark.anyio
async def test_disarm_resets_to_idle(monkeypatch) -> None:
    _patch_pyautogui_never_at_edge(monkeypatch)
    detector = EdgeDetector()
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=5000))
    await detector.disarm()
    assert detector.state() == DetectorState.IDLE
    assert detector.public_dict()["config"] is None


@pytest.mark.anyio
async def test_disarm_from_idle_is_safe() -> None:
    detector = EdgeDetector()
    await detector.disarm()
    assert detector.state() == DetectorState.IDLE


@pytest.mark.anyio
async def test_arm_twice_cancels_previous_task(monkeypatch) -> None:
    _patch_pyautogui_never_at_edge(monkeypatch)
    detector = EdgeDetector()
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=5000))
    first_task = detector._task
    await detector.arm(DetectorConfig(edge=HandoffEdge.RIGHT, dwell_ms=5000))
    assert first_task is not detector._task
    assert first_task.done()
    await detector.disarm()


# ---------------------------------------------------------------------------
# EdgeDetector dwell detection
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_detector_transitions_to_pending_after_dwell(monkeypatch) -> None:
    _patch_pyautogui_at_left_edge(monkeypatch)
    detector = EdgeDetector()
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=50, zone_px=5))
    await asyncio.sleep(0.25)
    assert detector.state() == DetectorState.PENDING


@pytest.mark.anyio
async def test_detector_stays_armed_when_cursor_not_at_edge(monkeypatch) -> None:
    _patch_pyautogui_never_at_edge(monkeypatch)
    detector = EdgeDetector()
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=50, zone_px=5))
    await asyncio.sleep(0.15)
    assert detector.state() == DetectorState.ARMED
    await detector.disarm()


@pytest.mark.anyio
async def test_detector_resets_dwell_when_cursor_leaves_edge(monkeypatch) -> None:
    call_count = {"n": 0}

    def position():
        call_count["n"] += 1
        # First batch: at edge; middle: away; final: back at edge
        if call_count["n"] <= 4:
            return SimpleNamespace(x=0, y=500)
        if call_count["n"] <= 8:
            return SimpleNamespace(x=500, y=500)
        return SimpleNamespace(x=0, y=500)

    fake_pg = SimpleNamespace(
        position=position,
        size=lambda: SimpleNamespace(width=1920, height=1080),
    )
    monkeypatch.setattr("app.edge_detector._get_pyautogui", lambda: fake_pg)

    detector = EdgeDetector()
    # Dwell of 50ms; poll at 16ms. First 4 calls (~64ms) at edge then leave.
    # After leaving, dwell resets. Then returns and dwells again → PENDING.
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=50, zone_px=5))
    await asyncio.sleep(0.5)
    assert detector.state() == DetectorState.PENDING


@pytest.mark.anyio
async def test_detector_sets_idle_when_backend_unavailable(monkeypatch) -> None:
    def raise_runtime():
        raise RuntimeError("no desktop")

    monkeypatch.setattr("app.edge_detector._get_pyautogui", raise_runtime)
    detector = EdgeDetector()
    await detector.arm(DetectorConfig(edge=HandoffEdge.LEFT, dwell_ms=400))
    await asyncio.sleep(0.1)
    assert detector.state() == DetectorState.IDLE


# ---------------------------------------------------------------------------
# cursor_at_edge — multi-monitor screen offset
# ---------------------------------------------------------------------------

def test_cursor_at_edge_secondary_monitor_left() -> None:
    # Secondary monitor at x=2560
    assert cursor_at_edge(2560, 500, 1920, 1080, HandoffEdge.LEFT, zone_px=5, screen_x=2560, screen_y=0) is True
    assert cursor_at_edge(2565, 500, 1920, 1080, HandoffEdge.LEFT, zone_px=5, screen_x=2560, screen_y=0) is True
    assert cursor_at_edge(2566, 500, 1920, 1080, HandoffEdge.LEFT, zone_px=5, screen_x=2560, screen_y=0) is False


def test_cursor_at_edge_secondary_monitor_right() -> None:
    assert cursor_at_edge(4479, 500, 1920, 1080, HandoffEdge.RIGHT, zone_px=5, screen_x=2560, screen_y=0) is True
    assert cursor_at_edge(4474, 500, 1920, 1080, HandoffEdge.RIGHT, zone_px=5, screen_x=2560, screen_y=0) is True
    assert cursor_at_edge(4473, 500, 1920, 1080, HandoffEdge.RIGHT, zone_px=5, screen_x=2560, screen_y=0) is False


def test_cursor_not_on_secondary_monitor_returns_false() -> None:
    # Cursor at (500, 500) is on primary, not on secondary at x=2560
    assert cursor_at_edge(500, 500, 1920, 1080, HandoffEdge.LEFT, zone_px=5, screen_x=2560, screen_y=0) is False


def test_cursor_on_primary_right_edge_does_not_trigger_secondary_left() -> None:
    # Cursor at primary right edge (x=1919) should not trigger secondary monitor left edge (x=2560)
    assert cursor_at_edge(1919, 500, 1920, 1080, HandoffEdge.LEFT, zone_px=5, screen_x=2560, screen_y=0) is False


def test_cursor_above_secondary_monitor_returns_false() -> None:
    # Secondary monitor at y=100; cursor at y=50 is above it
    assert cursor_at_edge(2560, 50, 1920, 900, HandoffEdge.TOP, zone_px=5, screen_x=2560, screen_y=100) is False


def test_cursor_at_top_of_secondary_monitor() -> None:
    assert cursor_at_edge(2800, 100, 1920, 900, HandoffEdge.TOP, zone_px=5, screen_x=2560, screen_y=100) is True
    assert cursor_at_edge(2800, 105, 1920, 900, HandoffEdge.TOP, zone_px=5, screen_x=2560, screen_y=100) is True
    assert cursor_at_edge(2800, 106, 1920, 900, HandoffEdge.TOP, zone_px=5, screen_x=2560, screen_y=100) is False


# ---------------------------------------------------------------------------
# make_detector_config — screen_index handling
# ---------------------------------------------------------------------------

def test_make_detector_config_accepts_screen_index(monkeypatch) -> None:
    fake_monitors = [
        {"index": 0, "x": 0, "y": 0, "width": 2560, "height": 1600, "primary": True, "name": "Built-in"},
        {"index": 1, "x": 2560, "y": 0, "width": 1920, "height": 1080, "primary": False, "name": "External"},
    ]
    monkeypatch.setattr("app.edge_detector.get_monitors", lambda: fake_monitors)
    config = make_detector_config(edge="right", screen_index=1)
    assert config is not None
    assert config.screen_index == 1
    assert config.screen_x == 2560
    assert config.screen_width == 1920
    assert config.screen_height == 1080


def test_make_detector_config_clamps_out_of_range_index(monkeypatch) -> None:
    fake_monitors = [
        {"index": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True, "name": "Primary"},
    ]
    monkeypatch.setattr("app.edge_detector.get_monitors", lambda: fake_monitors)
    config = make_detector_config(edge="left", screen_index=99)
    assert config is not None
    assert config.screen_index == 0
    assert config.screen_width == 1920


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_pyautogui_never_at_edge(monkeypatch) -> None:
    fake_pg = SimpleNamespace(
        position=lambda: SimpleNamespace(x=500, y=500),
        size=lambda: SimpleNamespace(width=1920, height=1080),
    )
    monkeypatch.setattr("app.edge_detector._get_pyautogui", lambda: fake_pg)


def _patch_pyautogui_at_left_edge(monkeypatch) -> None:
    fake_pg = SimpleNamespace(
        position=lambda: SimpleNamespace(x=0, y=500),
        size=lambda: SimpleNamespace(width=1920, height=1080),
    )
    monkeypatch.setattr("app.edge_detector._get_pyautogui", lambda: fake_pg)
