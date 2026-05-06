from app.protocol import parse_event


def test_parse_mouse_move() -> None:
    event = parse_event({"type": "mouse_move", "dx": "12.5", "dy": -4})

    assert event.type == "mouse_move"
    assert event.dx == 12.5
    assert event.dy == -4


def test_parse_legacy_move_alias() -> None:
    event = parse_event({"type": "move", "dx": 3, "dy": 2})

    assert event.type == "mouse_move"
    assert event.raw_type == "move"


def test_parse_mouse_button() -> None:
    event = parse_event({"type": "mouse_button", "button": "right", "down": True})

    assert event.type == "mouse_button"
    assert event.button == "right"
    assert event.down is True


def test_parse_legacy_click_alias() -> None:
    event = parse_event({"type": "click", "button": "middle"})

    assert event.type == "mouse_button"
    assert event.button == "middle"
    assert event.down is True


def test_parse_scroll() -> None:
    event = parse_event({"type": "scroll", "amount": "40"})

    assert event.type == "scroll"
    assert event.amount == 40


def test_unknown_button_falls_back_to_left() -> None:
    event = parse_event({"type": "mouse_button", "button": "side"})

    assert event.button == "left"
