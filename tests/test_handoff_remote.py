import pytest

from app.handoff_remote import RemoteStatus, RemoteTarget, normalize_host, normalize_path, normalize_port, should_connect


def test_remote_target_normalizes_values() -> None:
    target = RemoteTarget.from_values("http://192.168.1.25:8765", "8765", "ws/touchpad")

    assert target.host == "192.168.1.25"
    assert target.port == 8765
    assert target.path == "/ws/touchpad"
    assert target.status_url == "http://192.168.1.25:8765/api/status"
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
