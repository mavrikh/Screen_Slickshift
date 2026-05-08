from urllib.error import HTTPError
from urllib.request import Request

import pytest

from app.handoff_remote import (
    RemoteStatus,
    RemoteTarget,
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
