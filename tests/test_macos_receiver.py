from agents import macos_receiver
from app.pairing import PairingSessionBook
from app.protocol import parse_event
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect


class FakePyAutoGUI:
    def __init__(self) -> None:
        self.moves = []
        self.clicks = []
        self.scrolls = []

    def moveRel(self, dx: int, dy: int, duration: int = 0) -> None:
        self.moves.append((dx, dy, duration))

    def click(self, button: str) -> None:
        self.clicks.append(button)

    def scroll(self, amount: int) -> None:
        self.scrolls.append(amount)


def test_receiver_status_payload_keeps_screen_capture_out_of_scope() -> None:
    macos_receiver.state.disabled = False

    payload = macos_receiver.receiver_status_payload()

    assert payload["ok"] is True
    assert payload["role"] == "receiver"
    assert payload["disabled"] is False
    assert payload["input_allowed"] is True
    assert payload["screen_capture"] is False
    assert payload["protocol"]["input_events"] == ["mouse_move", "mouse_button", "scroll", "keyboard", "ping"]
    assert payload["capabilities"]["receive_input"] is True
    assert payload["capabilities"]["screen_capture"] is False
    assert payload["auth"]["websocket"]["preferred"] == "first-message"
    assert "session-auth" in payload["auth"]["websocket"]["supported"]
    assert payload["auth"]["http"]["preferred"] == "x-pairing-token-header"
    assert payload["auth"]["token_exposed"] is False


def test_receiver_index_payload_marks_screen_capture_not_supported() -> None:
    macos_receiver.state.disabled = False

    payload = macos_receiver.receiver_index_payload()

    assert payload["role"] == "experimental_receiver"
    assert payload["screen_capture"] == "not_supported"
    assert payload["input_allowed"] is True
    assert payload["capabilities"]["requires_screen_recording_permission"] is False
    assert payload["auth"]["websocket"]["supported"] == ["first-message", "session-auth", "query-token"]
    assert payload["auth"]["http"]["supported"] == ["x-pairing-token-header", "query-token"]


def test_receiver_capabilities_are_explicit_about_scope() -> None:
    capabilities = macos_receiver.receiver_capabilities()

    assert capabilities == {
        "receive_input": True,
        "mouse": True,
        "keyboard": False,
        "clipboard": False,
        "file_transfer": False,
        "screen_capture": False,
        "requires_accessibility_permission": True,
        "requires_screen_recording_permission": False,
    }


def test_receiver_auth_description_advertises_modes_without_token() -> None:
    macos_receiver.state.token = "token-123"

    assert macos_receiver.receiver_auth_description() == {
        "websocket": {
            "preferred": "first-message",
            "supported": ["first-message", "session-auth", "query-token"],
        },
        "http": {
            "preferred": "x-pairing-token-header",
            "supported": ["x-pairing-token-header", "query-token"],
        },
        "token_exposed": False,
    }


def test_receiver_permissions_payload_explains_macos_scope() -> None:
    payload = macos_receiver.receiver_permissions_payload()

    assert payload["ok"] is True
    assert payload["platform"] == "macos"
    assert payload["permissions"]["accessibility"] == {
        "required": True,
        "purpose": "mouse control",
        "macos_path": "System Settings -> Privacy & Security -> Accessibility",
        "when_needed": "before receiving mouse input",
    }
    assert payload["permissions"]["screen_recording"] == {
        "required": False,
        "purpose": "not implemented",
        "macos_path": "not needed",
        "when_needed": "never in the current prototype",
    }
    assert "Ctrl+C" in payload["emergency_stop"][0]
    assert "screen corner" in payload["emergency_stop"][1]


def test_permissions_endpoint_returns_permission_guidance() -> None:
    response = TestClient(macos_receiver.app).get("/api/permissions")

    assert response.status_code == 200
    assert response.json() == macos_receiver.receiver_permissions_payload()


def test_lockout_payload_reports_input_allowed() -> None:
    macos_receiver.state.disabled = True

    payload = macos_receiver.lockout_payload()

    assert payload == {
        "ok": True,
        "disabled": True,
        "input_allowed": False,
    }


def test_lockout_endpoint_updates_disabled_state() -> None:
    macos_receiver.state.token = "token-123"
    macos_receiver.state.disabled = False

    response = TestClient(macos_receiver.app).post("/api/lockout?token=token-123&disabled=true")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "disabled": True,
        "input_allowed": False,
    }
    assert macos_receiver.state.disabled is True


def test_lockout_endpoint_accepts_header_token() -> None:
    macos_receiver.state.token = "token-123"
    macos_receiver.state.disabled = False

    response = TestClient(macos_receiver.app).post(
        "/api/lockout?disabled=true",
        headers={"X-Pairing-Token": "token-123"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "disabled": True,
        "input_allowed": False,
    }
    assert macos_receiver.state.disabled is True


def test_lockout_endpoint_prefers_header_token_over_query_token() -> None:
    macos_receiver.state.token = "token-123"
    macos_receiver.state.disabled = False

    response = TestClient(macos_receiver.app).post(
        "/api/lockout?token=wrong&disabled=true",
        headers={"X-Pairing-Token": "token-123"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert macos_receiver.state.disabled is True


def test_lockout_endpoint_rejects_invalid_token() -> None:
    macos_receiver.state.token = "token-123"
    macos_receiver.state.disabled = False

    response = TestClient(macos_receiver.app).post("/api/lockout?token=wrong&disabled=true")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "error": "invalid token"}
    assert macos_receiver.state.disabled is False


def test_receiver_token_validation() -> None:
    macos_receiver.state.token = "token-123"

    assert macos_receiver.receiver_token_is_valid("token-123") is True
    assert macos_receiver.receiver_token_is_valid("wrong") is False
    assert macos_receiver.receiver_token_is_valid("") is False
    assert macos_receiver.receiver_token_is_valid(None) is False


def test_input_websocket_accepts_query_token() -> None:
    macos_receiver.state.token = "token-123"
    macos_receiver.state.disabled = False

    with TestClient(macos_receiver.app).websocket_connect("/ws/input?token=token-123") as websocket:
        websocket.send_json({"type": "ping"})


def test_input_websocket_accepts_first_message_auth() -> None:
    macos_receiver.state.token = "token-123"
    macos_receiver.state.disabled = False

    with TestClient(macos_receiver.app).websocket_connect("/ws/input") as websocket:
        websocket.send_json({"type": "auth", "token": "token-123"})
        websocket.send_json({"type": "ping"})


def test_input_websocket_accepts_session_auth_with_mouse_permission(monkeypatch) -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": True},
    )
    backend = FakePyAutoGUI()
    monkeypatch.setattr(macos_receiver, "pairing_session_book", session_book)
    monkeypatch.setattr(macos_receiver, "_pyautogui_backend", backend)

    with TestClient(macos_receiver.app).websocket_connect("/ws/input") as websocket:
        websocket.send_json(
            {
                "type": "session_auth",
                "session_id": grant.session.session_id,
                "session_token": grant.session_token,
            }
        )
        websocket.send_json({"type": "mouse_move", "dx": 4, "dy": -2})

    assert backend.moves == [(4, -2, 0)]
    updated_session = session_book.verify_session(grant.session.session_id, grant.session_token)
    assert updated_session is not None
    assert updated_session.last_active_at >= grant.session.last_active_at


def test_input_websocket_accepts_v1_session_auth_envelope(monkeypatch) -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": True},
    )
    backend = FakePyAutoGUI()
    monkeypatch.setattr(macos_receiver, "pairing_session_book", session_book)
    monkeypatch.setattr(macos_receiver, "_pyautogui_backend", backend)

    with TestClient(macos_receiver.app).websocket_connect("/ws/input") as websocket:
        websocket.send_json(
            {
                "version": 1,
                "payload": {
                    "type": "session_auth",
                    "session_id": grant.session.session_id,
                    "session_token": grant.session_token,
                },
            }
        )
        websocket.send_json({"version": 1, "payload": {"type": "mouse_move", "dx": 3, "dy": 2}})

    assert backend.moves == [(3, 2, 0)]


def test_input_websocket_rejects_session_without_mouse_permission(monkeypatch) -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": False},
    )
    monkeypatch.setattr(macos_receiver, "pairing_session_book", session_book)

    with TestClient(macos_receiver.app).websocket_connect("/ws/input") as websocket:
        websocket.send_json(
            {
                "type": "session_auth",
                "session_id": grant.session.session_id,
                "session_token": grant.session_token,
            }
        )
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()


def test_input_websocket_ping_does_not_keep_session_active(monkeypatch) -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": True},
    )
    monkeypatch.setattr(macos_receiver, "pairing_session_book", session_book)

    with TestClient(macos_receiver.app).websocket_connect("/ws/input") as websocket:
        websocket.send_json(
            {
                "type": "session_auth",
                "session_id": grant.session.session_id,
                "session_token": grant.session_token,
            }
        )
        websocket.send_json({"type": "ping"})

    updated_session = session_book.verify_session(grant.session.session_id, grant.session_token)
    assert updated_session is not None
    assert updated_session.last_active_at == grant.session.last_active_at


def test_input_websocket_rejects_invalid_first_message_auth() -> None:
    macos_receiver.state.token = "token-123"
    macos_receiver.state.disabled = False

    with TestClient(macos_receiver.app).websocket_connect("/ws/input") as websocket:
        websocket.send_json({"type": "auth", "token": "wrong"})
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()


def test_input_websocket_rejects_unsupported_auth_version() -> None:
    macos_receiver.state.token = "token-123"
    macos_receiver.state.disabled = False

    with TestClient(macos_receiver.app).websocket_connect("/ws/input") as websocket:
        websocket.send_json({"version": 2, "payload": {"type": "auth", "token": "token-123"}})
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()


def test_startup_banner_mentions_accessibility_and_screen_recording_scope() -> None:
    banner = macos_receiver.startup_banner("0.0.0.0", 8770, "token-123")

    assert "Experimental macOS Receiver" in banner
    assert "Listening on: http://0.0.0.0:8770" in banner
    assert "Pairing token: token-123" in banner
    assert "Accessibility permission may be required for mouse control." in banner
    assert "Screen Recording: not needed and not implemented." in banner


def test_startup_banner_mentions_emergency_stop_paths() -> None:
    banner = macos_receiver.startup_banner("127.0.0.1", 8770, "token-123")

    assert "Emergency stop: press Ctrl+C" in banner
    assert "screen corner" in banner


def test_apply_receiver_event_moves_mouse_when_enabled() -> None:
    macos_receiver.state.disabled = False
    backend = FakePyAutoGUI()

    handled = macos_receiver.apply_receiver_event(
        parse_event({"type": "mouse_move", "dx": 4.8, "dy": -2.2}),
        pyautogui_backend=backend,
    )

    assert handled is True
    assert backend.moves == [(4, -2, 0)]


def test_apply_receiver_event_clicks_when_enabled() -> None:
    macos_receiver.state.disabled = False
    backend = FakePyAutoGUI()

    handled = macos_receiver.apply_receiver_event(
        parse_event({"type": "mouse_button", "button": "right", "down": True}),
        pyautogui_backend=backend,
    )

    assert handled is True
    assert backend.clicks == ["right"]


def test_apply_receiver_event_scrolls_when_enabled() -> None:
    macos_receiver.state.disabled = False
    backend = FakePyAutoGUI()

    handled = macos_receiver.apply_receiver_event(
        parse_event({"type": "scroll", "amount": -20}),
        pyautogui_backend=backend,
    )

    assert handled is True
    assert backend.scrolls == [-20]


def test_apply_receiver_event_blocks_input_when_disabled() -> None:
    macos_receiver.state.disabled = True
    backend = FakePyAutoGUI()

    handled = macos_receiver.apply_receiver_event(
        parse_event({"type": "mouse_move", "dx": 4, "dy": -2}),
        pyautogui_backend=backend,
    )

    assert handled is False
    assert backend.moves == []


def test_apply_receiver_event_allows_ping_when_disabled() -> None:
    macos_receiver.state.disabled = True
    backend = FakePyAutoGUI()

    handled = macos_receiver.apply_receiver_event(
        parse_event({"type": "ping"}),
        pyautogui_backend=backend,
    )

    assert handled is True
    assert backend.moves == []


def test_apply_receiver_event_ignores_unknown_event() -> None:
    macos_receiver.state.disabled = False
    backend = FakePyAutoGUI()

    handled = macos_receiver.apply_receiver_event(
        parse_event({"type": "screen_capture"}),
        pyautogui_backend=backend,
    )

    assert handled is False
    assert backend.moves == []
