from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config, main
from app.edge_detector import DetectorConfig, DetectorState, EdgeDetector
from app.handoff import HandoffEdge
from app.handoff_remote import RemoteHandoffBridge
from app.main import app
from app.pairing import PairingCodeBook, PairingSessionBook


# ---------------------------------------------------------------------------
# Fake detector — avoids starting real asyncio polling tasks in route tests
# ---------------------------------------------------------------------------

class FakeEdgeDetector:
    def __init__(self, initial_state: str = "idle") -> None:
        self._state = initial_state
        self._config: dict | None = None
        self.armed_configs: list[DetectorConfig] = []
        self.disarm_count = 0

    def state(self) -> DetectorState:
        return DetectorState(self._state)

    def public_dict(self) -> dict:
        return {"state": self._state, "config": self._config}

    async def arm(self, config: DetectorConfig) -> None:
        self._state = "armed"
        self._config = config.public_dict()
        self.armed_configs.append(config)

    async def disarm(self) -> None:
        self._state = "idle"
        self._config = None
        self.disarm_count += 1


# ---------------------------------------------------------------------------
# /api/handoff/arm
# ---------------------------------------------------------------------------

def test_handoff_arm_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).post(
        "/api/handoff/arm",
        json={"edge": "right"},
    )

    assert response.status_code == 401


def test_handoff_arm_rejects_invalid_edge(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = FakeEdgeDetector()
    monkeypatch.setattr(main, "edge_detector", fake)

    response = TestClient(app).post(
        "/api/handoff/arm",
        headers={"X-Pairing-Token": token},
        json={"edge": "diagonal"},
    )

    assert response.status_code == 400
    assert len(fake.armed_configs) == 0


def test_handoff_arm_returns_armed_state(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = FakeEdgeDetector()
    monkeypatch.setattr(main, "edge_detector", fake)

    response = TestClient(app).post(
        "/api/handoff/arm",
        headers={"X-Pairing-Token": token},
        json={"edge": "right", "dwell_ms": 500, "zone_px": 8},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "armed"
    assert data["config"]["edge"] == "right"
    assert data["config"]["dwell_ms"] == 500
    assert data["config"]["zone_px"] == 8


def test_handoff_arm_defaults_dwell_and_zone(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = FakeEdgeDetector()
    monkeypatch.setattr(main, "edge_detector", fake)

    response = TestClient(app).post(
        "/api/handoff/arm",
        headers={"X-Pairing-Token": token},
        json={"edge": "left"},
    )

    assert response.status_code == 200
    assert len(fake.armed_configs) == 1
    assert fake.armed_configs[0].edge == HandoffEdge.LEFT
    assert fake.armed_configs[0].dwell_ms == 400
    assert fake.armed_configs[0].zone_px == 5


def test_handoff_arm_accepts_all_four_edges(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    for edge in ("left", "right", "top", "bottom"):
        fake = FakeEdgeDetector()
        monkeypatch.setattr(main, "edge_detector", fake)
        response = TestClient(app).post(
            "/api/handoff/arm",
            headers={"X-Pairing-Token": token},
            json={"edge": edge},
        )
        assert response.status_code == 200, f"expected 200 for edge={edge!r}"
        assert response.json()["config"]["edge"] == edge


# ---------------------------------------------------------------------------
# /api/handoff/disarm
# ---------------------------------------------------------------------------

def test_handoff_disarm_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).post("/api/handoff/disarm")

    assert response.status_code == 401


def test_handoff_disarm_returns_idle_state(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = FakeEdgeDetector(initial_state="armed")
    monkeypatch.setattr(main, "edge_detector", fake)

    response = TestClient(app).post(
        "/api/handoff/disarm",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "idle"
    assert fake.disarm_count == 1


def test_handoff_disarm_from_idle_is_idempotent(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = FakeEdgeDetector()
    monkeypatch.setattr(main, "edge_detector", fake)

    response = TestClient(app).post(
        "/api/handoff/disarm",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "idle"


# ---------------------------------------------------------------------------
# /api/handoff/detector/state
# ---------------------------------------------------------------------------

def test_handoff_detector_state_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/handoff/detector/state")

    assert response.status_code == 401


def test_handoff_detector_state_returns_idle_by_default(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    monkeypatch.setattr(main, "edge_detector", FakeEdgeDetector())

    response = TestClient(app).get(
        "/api/handoff/detector/state",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "idle"
    assert response.json()["config"] is None


def test_handoff_detector_state_reflects_armed(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = FakeEdgeDetector(initial_state="armed")
    fake._config = {"edge": "top", "dwell_ms": 400, "zone_px": 5}
    monkeypatch.setattr(main, "edge_detector", fake)

    response = TestClient(app).get(
        "/api/handoff/detector/state",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "armed"
    assert data["config"]["edge"] == "top"


def test_handoff_detector_state_reflects_pending(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    monkeypatch.setattr(main, "edge_detector", FakeEdgeDetector(initial_state="pending"))

    response = TestClient(app).get(
        "/api/handoff/detector/state",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "pending"


# ---------------------------------------------------------------------------
# /api/screen/info
# ---------------------------------------------------------------------------

def test_screen_info_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/screen/info")

    assert response.status_code == 401


def test_screen_info_returns_error_when_pyautogui_unavailable(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()

    def raise_import(*args, **kwargs):
        raise ImportError("no display")

    monkeypatch.setitem(__builtins__ if isinstance(__builtins__, dict) else vars(__builtins__), "import", None)

    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pyautogui":
            raise ImportError("no display")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    response = TestClient(app).get(
        "/api/screen/info",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["width"] is None
    assert data["height"] is None
    assert data["error"] != ""


def test_screen_info_returns_dimensions_when_available(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    from types import SimpleNamespace

    fake_pg = SimpleNamespace(
        size=lambda: SimpleNamespace(width=2560, height=1440),
        position=lambda: SimpleNamespace(x=100, y=200),
    )

    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "pyautogui":
            return fake_pg
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)

    response = TestClient(app).get(
        "/api/screen/info",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["width"] == 2560
    assert data["height"] == 1440
    assert data["cursor_x"] == 100
    assert data["cursor_y"] == 200
    assert data["error"] == ""


# ---------------------------------------------------------------------------
# Return-edge routes — /api/handoff/remote/arm-return etc.
# ---------------------------------------------------------------------------

class FakeReturnBridge:
    def __init__(self, *, arm_raises: bool = False, state_raises: bool = False) -> None:
        self.arm_raises = arm_raises
        self.state_raises = state_raises
        self.armed_edge: str | None = None
        self.armed_dwell: int | None = None
        self.disarm_count = 0
        self._return_state = "armed"

    async def arm_return_detector(self, return_edge: str, dwell_ms: int = 400) -> dict:
        if self.arm_raises:
            raise RuntimeError("remote unreachable")
        self.armed_edge = return_edge
        self.armed_dwell = dwell_ms
        return {"state": self._return_state}

    async def get_return_state(self) -> dict:
        if self.state_raises:
            raise RuntimeError("remote unreachable")
        return {"state": self._return_state, "config": None}

    async def disarm_return_detector(self) -> dict:
        self.disarm_count += 1
        return {"state": "idle"}

    # Unused bridge methods — keep interface compatible
    async def start(self, *a, **kw):  # type: ignore[override]
        pass

    async def send_event(self, *a, **kw):  # type: ignore[override]
        pass

    async def stop(self, *a, **kw):  # type: ignore[override]
        return {}

    def status(self) -> dict:
        return {"connected": False, "target": None}


def test_return_arm_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).post(
        "/api/handoff/remote/arm-return",
        json={"return_edge": "left"},
    )

    assert response.status_code == 401


def test_return_arm_calls_bridge(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeReturnBridge()
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).post(
        "/api/handoff/remote/arm-return",
        headers={"X-Pairing-Token": token},
        json={"return_edge": "left", "dwell_ms": 500},
    )

    assert response.status_code == 200
    assert bridge.armed_edge == "left"
    assert bridge.armed_dwell == 500


def test_return_arm_returns_409_when_not_connected(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeReturnBridge(arm_raises=True)
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).post(
        "/api/handoff/remote/arm-return",
        headers={"X-Pairing-Token": token},
        json={"return_edge": "left"},
    )

    assert response.status_code == 409


def test_return_state_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/handoff/remote/return-state")

    assert response.status_code == 401


def test_return_state_returns_detector_state(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeReturnBridge()
    bridge._return_state = "pending"
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).get(
        "/api/handoff/remote/return-state",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "pending"


def test_return_state_returns_409_when_not_connected(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeReturnBridge(state_raises=True)
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).get(
        "/api/handoff/remote/return-state",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 409


def test_return_disarm_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).post("/api/handoff/remote/disarm-return")

    assert response.status_code == 401


def test_return_disarm_calls_bridge(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    bridge = FakeReturnBridge()
    monkeypatch.setattr(main, "remote_handoff_bridge", bridge)

    response = TestClient(app).post(
        "/api/handoff/remote/disarm-return",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    assert bridge.disarm_count == 1


# ---------------------------------------------------------------------------
# /api/handoff/remote/screen-info
# ---------------------------------------------------------------------------

class FakeScreenInfoBridge(FakeReturnBridge):
    def __init__(self, *, raises: bool = False) -> None:
        super().__init__()
        self._raises = raises

    async def get_remote_screen_info(self) -> dict:
        if self._raises:
            raise RuntimeError("remote unreachable")
        return {"width": 1920, "height": 1080, "cursor_x": 0, "cursor_y": 0, "error": ""}


def test_remote_screen_info_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)

    response = TestClient(app).get("/api/handoff/remote/screen-info")

    assert response.status_code == 401


def test_remote_screen_info_returns_dimensions(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    monkeypatch.setattr(main, "remote_handoff_bridge", FakeScreenInfoBridge())

    response = TestClient(app).get(
        "/api/handoff/remote/screen-info",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["width"] == 1920
    assert data["height"] == 1080


def test_remote_screen_info_returns_409_when_not_connected(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    monkeypatch.setattr(main, "remote_handoff_bridge", FakeScreenInfoBridge(raises=True))

    response = TestClient(app).get(
        "/api/handoff/remote/screen-info",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# /api/screen/monitors
# ---------------------------------------------------------------------------

def test_screen_monitors_requires_token(tmp_path, monkeypatch) -> None:
    _use_temp_config(tmp_path, monkeypatch)
    response = TestClient(app).get("/api/screen/monitors")
    assert response.status_code == 401


def test_screen_monitors_returns_list(tmp_path, monkeypatch) -> None:
    from app import edge_detector as ed
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = [{"index": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "primary": True, "name": "Test Display"}]
    monkeypatch.setattr(ed, "get_monitors", lambda: fake)

    response = TestClient(app).get(
        "/api/screen/monitors",
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["monitors"], list)
    assert data["monitors"][0]["width"] == 1920
    assert data["monitors"][0]["name"] == "Test Display"


def test_screen_monitors_screen_index_passed_to_arm(tmp_path, monkeypatch) -> None:
    from app import edge_detector as ed
    _use_temp_config(tmp_path, monkeypatch)
    token = config.get_or_create_pairing_token()
    fake = [
        {"index": 0, "x": 0, "y": 0, "width": 2560, "height": 1600, "primary": True, "name": "Built-in"},
        {"index": 1, "x": 2560, "y": 0, "width": 1920, "height": 1080, "primary": False, "name": "External"},
    ]
    monkeypatch.setattr(ed, "get_monitors", lambda: fake)

    response = TestClient(app).post(
        "/api/handoff/arm",
        json={"edge": "right", "screen_index": 1},
        headers={"X-Pairing-Token": token},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["config"]["screen_index"] == 1
    assert data["config"]["screen_x"] == 2560
    assert data["config"]["screen_width"] == 1920


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _use_temp_config(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    monkeypatch.setattr(config, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config, "TOKEN_FILE", config_dir / "pairing_token.txt")
    monkeypatch.setattr(config, "TRUSTED_DEVICES_FILE", config_dir / "trusted_devices.json")
    monkeypatch.setattr(config, "DEVICE_IDENTITY_FILE", config_dir / "device_identity.json")
    monkeypatch.setattr(config, "RECEIVE_DIR_FILE", config_dir / "receive_dir.txt")
    monkeypatch.setattr(main, "pairing_code_book", PairingCodeBook())
    monkeypatch.setattr(main, "pairing_session_book", PairingSessionBook())
    main.lockout_state.set_disabled(False)
    main.transfer_history.clear()
