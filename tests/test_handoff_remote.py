from urllib.error import HTTPError
from urllib.request import Request

import pytest

from app.handoff_remote import (
    RemoteStatus,
    RemoteTarget,
    arm_remote_detector,
    disarm_remote_detector,
    fetch_remote_screen_info,
    get_remote_detector_state,
    normalize_host,
    normalize_path,
    normalize_port,
    remote_token_is_valid,
    should_connect,
)


def test_remote_target_normalizes_values() -> None:
    target = RemoteTarget.from_values("http://192.168.1.25:8765", "8765", "ws/touchpad")

    assert target.host == "192.168.1.25"
    assert target.port == 8765
    assert target.path == "/ws/touchpad"
    assert target.status_url == "http://192.168.1.25:8765/api/status"
    assert target.auth_check_url == "http://192.168.1.25:8765/api/auth/check"
    assert target.input_status_url == "http://192.168.1.25:8765/api/input/status?check_backend=true"
    assert target.websocket_url == "ws://192.168.1.25:8765/ws/touchpad"


@pytest.mark.parametrize("host", ["", "192.168.1.4/path", "bad?host", "bad#host"])
def test_normalize_host_rejects_unsafe_values(host: str) -> None:
    with pytest.raises(ValueError):
        normalize_host(host)


@pytest.mark.parametrize("port", [0, 65536, "not-a-port"])
def test_normalize_port_rejects_invalid_values(port) -> None:
    with pytest.raises(ValueError):
        normalize_port(port)


def test_normalize_path_rejects_unknown_paths() -> None:
    with pytest.raises(ValueError):
        normalize_path("/ws/admin")


def test_should_connect_requires_reachable_enabled_mouse_receiver() -> None:
    assert should_connect(
        RemoteStatus(
            reachable=True,
            input_events=("mouse_move", "mouse_button", "scroll", "ping"),
        )
    )
    assert not should_connect(RemoteStatus(reachable=False))
    assert not should_connect(RemoteStatus(reachable=True, disabled=True))
    assert not should_connect(RemoteStatus(reachable=True, input_events=("mouse_move", "ping")))


def test_remote_token_is_valid_uses_auth_check(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr("app.handoff_remote.urlopen", fake_urlopen)

    assert remote_token_is_valid(RemoteTarget.from_values("192.168.1.25", 8765), "secret-token")
    request, timeout = calls[0]
    assert isinstance(request, Request)
    assert request.full_url == "http://192.168.1.25:8765/api/auth/check"
    assert request.headers["X-pairing-token"] == "secret-token"
    assert timeout == 2.0


def test_remote_token_is_valid_rejects_http_error(monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr("app.handoff_remote.urlopen", fake_urlopen)

    assert not remote_token_is_valid(RemoteTarget.from_values("192.168.1.25", 8765), "wrong-token")


# ---------------------------------------------------------------------------
# arm_remote_detector
# ---------------------------------------------------------------------------

def test_arm_remote_detector_posts_to_arm_endpoint(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def read(self):
            return b'{"state": "armed"}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr("app.handoff_remote.urlopen", fake_urlopen)

    target = RemoteTarget.from_values("192.168.1.10", 8765)
    result = arm_remote_detector(target, "token", "right", dwell_ms=500)

    assert result == {"state": "armed"}
    assert len(calls) == 1
    req = calls[0]
    assert req.full_url == "http://192.168.1.10:8765/api/handoff/arm"
    assert req.headers["X-pairing-token"] == "token"
    import json as _json
    body = _json.loads(req.data.decode())
    assert body["edge"] == "right"
    assert body["dwell_ms"] == 500


def test_arm_remote_detector_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.handoff_remote.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("refused")),
    )

    with pytest.raises(RuntimeError):
        arm_remote_detector(RemoteTarget.from_values("192.168.1.10", 8765), "token", "left")


# ---------------------------------------------------------------------------
# get_remote_detector_state
# ---------------------------------------------------------------------------

def test_get_remote_detector_state_calls_detector_state_endpoint(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def read(self):
            return b'{"state": "pending", "config": null}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr("app.handoff_remote.urlopen", fake_urlopen)

    target = RemoteTarget.from_values("192.168.1.10", 8765)
    result = get_remote_detector_state(target, "token")

    assert result == {"state": "pending", "config": None}
    assert calls[0].full_url == "http://192.168.1.10:8765/api/handoff/detector/state"
    assert calls[0].headers["X-pairing-token"] == "token"


def test_get_remote_detector_state_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.handoff_remote.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("refused")),
    )

    with pytest.raises(RuntimeError):
        get_remote_detector_state(RemoteTarget.from_values("192.168.1.10", 8765), "token")


# ---------------------------------------------------------------------------
# disarm_remote_detector
# ---------------------------------------------------------------------------

def test_disarm_remote_detector_posts_to_disarm_endpoint(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def read(self):
            return b'{"state": "idle"}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr("app.handoff_remote.urlopen", fake_urlopen)

    target = RemoteTarget.from_values("192.168.1.10", 8765)
    result = disarm_remote_detector(target, "token")

    assert result == {"state": "idle"}
    assert calls[0].full_url == "http://192.168.1.10:8765/api/handoff/disarm"
    assert calls[0].headers["X-pairing-token"] == "token"


def test_disarm_remote_detector_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.handoff_remote.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("refused")),
    )

    with pytest.raises(RuntimeError):
        disarm_remote_detector(RemoteTarget.from_values("192.168.1.10", 8765), "token")


# ---------------------------------------------------------------------------
# fetch_remote_screen_info
# ---------------------------------------------------------------------------

def test_fetch_remote_screen_info_calls_screen_info_endpoint(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def read(self):
            return b'{"width": 1920, "height": 1080, "cursor_x": 960, "cursor_y": 540, "error": ""}'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        calls.append(request)
        return FakeResponse()

    monkeypatch.setattr("app.handoff_remote.urlopen", fake_urlopen)

    target = RemoteTarget.from_values("192.168.1.10", 8765)
    result = fetch_remote_screen_info(target, "token")

    assert result["width"] == 1920
    assert result["height"] == 1080
    assert calls[0].full_url == "http://192.168.1.10:8765/api/screen/info"
    assert calls[0].headers["X-pairing-token"] == "token"


def test_fetch_remote_screen_info_raises_on_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.handoff_remote.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(OSError("refused")),
    )

    with pytest.raises(RuntimeError):
        fetch_remote_screen_info(RemoteTarget.from_values("192.168.1.10", 8765), "token")
