from app.handoff import (
    HandoffConfig,
    HandoffEdge,
    HandoffLayout,
    HandoffMode,
    HandoffState,
    HandoffTarget,
    LayoutOverlap,
    LayoutScreen,
    ScreenRect,
    arm_handoff,
    begin_pending_handoff,
    configure_handoff,
    confirm_handoff,
    derive_handoff_routes,
    disarm_handoff,
    finish_returning,
    find_layout_overlaps,
    make_handoff_layout,
    make_layout_screen,
    normalize_edge,
    normalize_screen_rect,
    snap_layout_screens,
    place_screen_without_overlap,
    snap_screen_rect,
    stop_remote_control,
)


def test_handoff_target_public_dict_exposes_no_secret() -> None:
    target = HandoffTarget(
        target_id="macbook-1",
        name="MacBook",
        host="192.168.1.20",
        port=8770,
    )

    assert target.public_dict() == {
        "target_id": "macbook-1",
        "name": "MacBook",
        "host": "192.168.1.20",
        "port": 8770,
    }


def test_default_config_is_disabled_and_not_ready() -> None:
    config = HandoffConfig()

    assert config.enabled is False
    assert config.is_ready() is False
    assert config.public_dict() == {
        "enabled": False,
        "target_id": None,
        "edge": None,
        "dwell_ms": 400,
        "ready": False,
    }


def test_configure_handoff_normalizes_edge_and_target() -> None:
    config = configure_handoff(
        enabled=True,
        target_id=" macbook-1 ",
        edge="LEFT",
        dwell_ms=750,
    )

    assert config.enabled is True
    assert config.target_id == "macbook-1"
    assert config.edge == HandoffEdge.LEFT
    assert config.dwell_ms == 750
    assert config.is_ready() is True


def test_configure_handoff_ignores_invalid_edge_and_dwell() -> None:
    config = configure_handoff(
        enabled=True,
        target_id="macbook-1",
        edge="diagonal",
        dwell_ms=1,
    )

    assert config.edge is None
    assert config.dwell_ms == 400
    assert config.is_ready() is False


def test_normalize_edge_accepts_enum_or_string() -> None:
    assert normalize_edge(HandoffEdge.RIGHT) == HandoffEdge.RIGHT
    assert normalize_edge(" right ") == HandoffEdge.RIGHT
    assert normalize_edge("missing") is None
    assert normalize_edge(None) is None


def test_screen_rect_edges_and_public_dict() -> None:
    rect = ScreenRect(x=10, y=20, width=300, height=200)

    assert rect.left == 10
    assert rect.right == 310
    assert rect.top == 20
    assert rect.bottom == 220
    assert rect.is_valid() is True
    assert rect.public_dict() == {
        "x": 10,
        "y": 20,
        "width": 300,
        "height": 200,
    }


def test_normalize_screen_rect_accepts_dict_and_rejects_invalid_values() -> None:
    assert normalize_screen_rect({"x": "10", "y": 20, "width": 300, "height": 200}) == ScreenRect(
        x=10,
        y=20,
        width=300,
        height=200,
    )
    assert normalize_screen_rect({"x": 0, "y": 0, "width": 0, "height": 200}) is None
    assert normalize_screen_rect({"x": "nope", "y": 0, "width": 300, "height": 200}) is None
    assert normalize_screen_rect(None) is None


def test_make_layout_screen_cleans_values() -> None:
    screen = make_layout_screen(
        screen_id=" desktop-main ",
        device_id=" desktop ",
        name=" Desktop ",
        rect={"x": 0, "y": 0, "width": 1920, "height": 1080},
        primary=True,
    )

    assert screen == LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
        primary=True,
    )


def test_make_layout_screen_rejects_missing_identity_or_rect() -> None:
    assert make_layout_screen(
        screen_id="",
        device_id="desktop",
        name="Desktop",
        rect={"x": 0, "y": 0, "width": 1920, "height": 1080},
    ) is None
    assert make_layout_screen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect={"x": 0, "y": 0, "width": -1, "height": 1080},
    ) is None


def test_layout_screen_public_dict() -> None:
    screen = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
        primary=True,
    )

    assert screen.public_dict() == {
        "screen_id": "desktop-main",
        "device_id": "desktop",
        "name": "Desktop",
        "rect": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "primary": True,
        "edge_enabled": True,
    }


def test_make_handoff_layout_filters_invalid_screens_and_cleans_thresholds() -> None:
    valid = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    invalid = LayoutScreen(
        screen_id="broken",
        device_id="desktop",
        name="Broken",
        rect=ScreenRect(x=0, y=0, width=0, height=1080),
    )

    layout = make_handoff_layout([valid, invalid], snap_tolerance_px=-1, min_overlap_px=0)

    assert layout.screens == (valid,)
    assert layout.snap_tolerance_px == 24
    assert layout.min_overlap_px == 80


def test_derive_handoff_routes_for_horizontal_neighbors() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    macbook = LayoutScreen(
        screen_id="macbook-main",
        device_id="macbook",
        name="MacBook",
        rect=ScreenRect(x=1920, y=0, width=1440, height=900),
    )
    layout = HandoffLayout(screens=(desktop, macbook))

    routes = derive_handoff_routes(layout)

    assert [route.public_dict() for route in routes] == [
        {
            "from_screen_id": "desktop-main",
            "to_screen_id": "macbook-main",
            "from_device_id": "desktop",
            "to_device_id": "macbook",
            "exit_edge": "right",
            "enter_edge": "left",
            "overlap_px": 900,
        },
        {
            "from_screen_id": "macbook-main",
            "to_screen_id": "desktop-main",
            "from_device_id": "macbook",
            "to_device_id": "desktop",
            "exit_edge": "left",
            "enter_edge": "right",
            "overlap_px": 900,
        },
    ]


def test_derive_handoff_routes_for_vertical_neighbors() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    deck = LayoutScreen(
        screen_id="deck-main",
        device_id="steam-deck",
        name="Steam Deck",
        rect=ScreenRect(x=200, y=1080, width=1280, height=800),
    )
    layout = HandoffLayout(screens=(desktop, deck))

    routes = derive_handoff_routes(layout)

    assert routes[0].exit_edge == HandoffEdge.BOTTOM
    assert routes[0].enter_edge == HandoffEdge.TOP
    assert routes[0].overlap_px == 1280
    assert routes[1].exit_edge == HandoffEdge.TOP
    assert routes[1].enter_edge == HandoffEdge.BOTTOM


def test_derive_handoff_routes_honors_snap_tolerance() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    macbook = LayoutScreen(
        screen_id="macbook-main",
        device_id="macbook",
        name="MacBook",
        rect=ScreenRect(x=1930, y=0, width=1440, height=900),
    )

    assert len(derive_handoff_routes(HandoffLayout(screens=(desktop, macbook), snap_tolerance_px=24))) == 2
    assert derive_handoff_routes(HandoffLayout(screens=(desktop, macbook), snap_tolerance_px=4)) == ()


def test_derive_handoff_routes_requires_minimum_overlap() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    tiny_overlap = LayoutScreen(
        screen_id="macbook-main",
        device_id="macbook",
        name="MacBook",
        rect=ScreenRect(x=1920, y=1030, width=1440, height=900),
    )
    layout = HandoffLayout(screens=(desktop, tiny_overlap), min_overlap_px=80)

    assert derive_handoff_routes(layout) == ()


def test_derive_handoff_routes_ignores_screens_on_same_device() -> None:
    primary = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop Main",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    secondary = LayoutScreen(
        screen_id="desktop-side",
        device_id="desktop",
        name="Desktop Side",
        rect=ScreenRect(x=1920, y=0, width=1920, height=1080),
    )
    layout = HandoffLayout(screens=(primary, secondary))

    assert derive_handoff_routes(layout) == ()


def test_derive_handoff_routes_ignores_edge_disabled_screens() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    disabled = LayoutScreen(
        screen_id="macbook-main",
        device_id="macbook",
        name="MacBook",
        rect=ScreenRect(x=1920, y=0, width=1440, height=900),
        edge_enabled=False,
    )
    layout = HandoffLayout(screens=(desktop, disabled))

    assert derive_handoff_routes(layout) == ()
    assert layout.public_dict()["screens"][1]["edge_enabled"] is False


def test_handoff_layout_public_dict_includes_routes() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    macbook = LayoutScreen(
        screen_id="macbook-main",
        device_id="macbook",
        name="MacBook",
        rect=ScreenRect(x=1920, y=0, width=1440, height=900),
    )
    layout = HandoffLayout(screens=(desktop, macbook))

    payload = layout.public_dict()

    assert payload["screens"][0]["screen_id"] == "desktop-main"
    assert payload["routes"][0]["exit_edge"] == "right"
    assert payload["routes"][0]["to_device_id"] == "macbook"
    assert payload["overlaps"] == []


def test_find_layout_overlaps_reports_overlapping_screens() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    macbook = LayoutScreen(
        screen_id="macbook-main",
        device_id="macbook",
        name="MacBook",
        rect=ScreenRect(x=1800, y=900, width=1440, height=900),
    )
    layout = HandoffLayout(screens=(desktop, macbook))

    assert find_layout_overlaps(layout) == (
        LayoutOverlap(
            first_screen_id="desktop-main",
            second_screen_id="macbook-main",
            overlap_width=120,
            overlap_height=180,
        ),
    )
    assert find_layout_overlaps(layout)[0].public_dict() == {
        "first_screen_id": "desktop-main",
        "second_screen_id": "macbook-main",
        "overlap_width": 120,
        "overlap_height": 180,
        "area": 21600,
    }


def test_find_layout_overlaps_allows_touching_edges() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    macbook = LayoutScreen(
        screen_id="macbook-main",
        device_id="macbook",
        name="MacBook",
        rect=ScreenRect(x=1920, y=0, width=1440, height=900),
    )
    layout = HandoffLayout(screens=(desktop, macbook))

    assert find_layout_overlaps(layout) == ()


def test_snap_screen_rect_locks_horizontal_edge_when_close() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    moving = ScreenRect(x=1932, y=80, width=1440, height=900)

    snapped = snap_screen_rect(
        "macbook-main",
        moving,
        (desktop,),
        snap_tolerance_px=24,
        min_overlap_px=80,
    )

    assert snapped == ScreenRect(x=1920, y=80, width=1440, height=900)


def test_snap_screen_rect_locks_vertical_edge_when_close() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    moving = ScreenRect(x=200, y=1092, width=1280, height=800)

    snapped = snap_screen_rect(
        "deck-main",
        moving,
        (desktop,),
        snap_tolerance_px=24,
        min_overlap_px=80,
    )

    assert snapped == ScreenRect(x=200, y=1080, width=1280, height=800)


def test_snap_screen_rect_ignores_far_or_low_overlap_screens() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    far = ScreenRect(x=2000, y=80, width=1440, height=900)
    low_overlap = ScreenRect(x=1930, y=1040, width=1440, height=900)

    assert snap_screen_rect("macbook-main", far, (desktop,), snap_tolerance_px=24) == far
    assert snap_screen_rect("macbook-main", low_overlap, (desktop,), snap_tolerance_px=24) == low_overlap


def test_snap_screen_rect_can_force_nearest_side_on_release() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    moving = ScreenRect(x=2600, y=1300, width=1440, height=900)

    snapped = snap_screen_rect(
        "macbook-main",
        moving,
        (desktop,),
        snap_tolerance_px=10000,
        min_overlap_px=80,
    )

    assert snapped == ScreenRect(x=1920, y=1000, width=1440, height=900)


def test_place_screen_without_overlap_prefers_right_side() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    rect = ScreenRect(x=100, y=100, width=400, height=300)

    placed = place_screen_without_overlap(rect, (desktop,))

    assert placed == ScreenRect(x=1920, y=0, width=400, height=300)


def test_place_screen_without_overlap_skips_occupied_right_side() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    right = LayoutScreen(
        screen_id="right-main",
        device_id="right",
        name="Right",
        rect=ScreenRect(x=1920, y=0, width=400, height=300),
    )
    rect = ScreenRect(x=100, y=100, width=400, height=300)

    placed = place_screen_without_overlap(rect, (desktop, right))

    assert placed != right.rect
    assert find_layout_overlaps(
        HandoffLayout(
            screens=(
                desktop,
                right,
                LayoutScreen(
                    screen_id="new-main",
                    device_id="new",
                    name="New",
                    rect=placed,
                ),
            )
        )
    ) == ()


def test_snap_layout_screens_pulls_freeform_screens_to_edges() -> None:
    desktop = LayoutScreen(
        screen_id="desktop-main",
        device_id="desktop",
        name="Desktop",
        rect=ScreenRect(x=0, y=0, width=1920, height=1080),
    )
    macbook = LayoutScreen(
        screen_id="macbook-main",
        device_id="macbook",
        name="MacBook",
        rect=ScreenRect(x=3000, y=1200, width=1440, height=900),
    )
    deck = LayoutScreen(
        screen_id="deck-main",
        device_id="deck",
        name="Deck",
        rect=ScreenRect(x=-1600, y=-900, width=1280, height=800),
    )

    snapped = snap_layout_screens((desktop, macbook, deck))

    assert snapped[0] == desktop
    assert snapped[1].rect == ScreenRect(x=1920, y=1000, width=1440, height=900)
    assert find_layout_overlaps(HandoffLayout(screens=snapped)) == ()
    assert len(derive_handoff_routes(HandoffLayout(screens=snapped))) >= 2


def test_state_public_dict_uses_wire_values() -> None:
    state = HandoffState(
        mode=HandoffMode.ARMED,
        active_target_id="macbook-1",
        active_edge=HandoffEdge.LEFT,
        notice="ready",
    )

    assert state.public_dict() == {
        "mode": "armed",
        "active_target_id": "macbook-1",
        "active_edge": "left",
        "notice": "ready",
    }


def test_arm_requires_ready_config() -> None:
    state = arm_handoff(HandoffState(), HandoffConfig())

    assert state.mode == HandoffMode.IDLE
    assert state.notice == "Handoff needs an enabled target and edge before arming."


def test_arm_handoff_sets_target_and_edge() -> None:
    config = configure_handoff(enabled=True, target_id="macbook-1", edge="left")

    state = arm_handoff(HandoffState(), config)

    assert state.mode == HandoffMode.ARMED
    assert state.active_target_id == "macbook-1"
    assert state.active_edge == HandoffEdge.LEFT
    assert state.notice == "Handoff armed."


def test_disarm_resets_non_active_state() -> None:
    config = configure_handoff(enabled=True, target_id="macbook-1", edge="left")
    armed = arm_handoff(HandoffState(), config)

    state = disarm_handoff(armed)

    assert state == HandoffState(notice="Handoff disarmed.")


def test_disarm_does_not_stop_active_remote() -> None:
    active = HandoffState(
        mode=HandoffMode.ACTIVE_REMOTE,
        active_target_id="macbook-1",
        active_edge=HandoffEdge.LEFT,
    )

    state = disarm_handoff(active)

    assert state.mode == HandoffMode.ACTIVE_REMOTE
    assert state.notice == "Stop remote control before disarming."


def test_begin_pending_requires_armed_state() -> None:
    state = begin_pending_handoff(HandoffState(), "left")

    assert state.mode == HandoffMode.IDLE
    assert state.notice == "Handoff is not armed."


def test_begin_pending_requires_configured_edge() -> None:
    config = configure_handoff(enabled=True, target_id="macbook-1", edge="left")
    armed = arm_handoff(HandoffState(), config)

    state = begin_pending_handoff(armed, "right")

    assert state.mode == HandoffMode.ARMED
    assert state.notice == "Ignored non-configured edge."


def test_confirmed_handoff_flow_reaches_active_remote() -> None:
    config = configure_handoff(enabled=True, target_id="macbook-1", edge="left")
    armed = arm_handoff(HandoffState(), config)
    pending = begin_pending_handoff(armed, "left")
    active = confirm_handoff(pending)

    assert pending.mode == HandoffMode.PENDING_HANDOFF
    assert active.mode == HandoffMode.ACTIVE_REMOTE
    assert active.active_target_id == "macbook-1"
    assert active.active_edge == HandoffEdge.LEFT


def test_confirm_without_pending_is_ignored() -> None:
    config = configure_handoff(enabled=True, target_id="macbook-1", edge="left")
    armed = arm_handoff(HandoffState(), config)

    state = confirm_handoff(armed)

    assert state.mode == HandoffMode.ARMED
    assert state.notice == "No pending handoff to confirm."


def test_stop_remote_control_enters_returning() -> None:
    active = HandoffState(
        mode=HandoffMode.ACTIVE_REMOTE,
        active_target_id="macbook-1",
        active_edge=HandoffEdge.LEFT,
    )

    state = stop_remote_control(active, reason="panic")

    assert state.mode == HandoffMode.RETURNING
    assert state.active_target_id == "macbook-1"
    assert state.notice == "panic"


def test_stop_when_not_active_returns_idle() -> None:
    state = stop_remote_control(HandoffState(mode=HandoffMode.ARMED), reason="panic")

    assert state == HandoffState(notice="panic")


def test_finish_returning_restores_idle() -> None:
    returning = HandoffState(mode=HandoffMode.RETURNING, active_target_id="macbook-1")

    state = finish_returning(returning)

    assert state == HandoffState(notice="Local control restored.")


def test_finish_returning_ignores_other_states() -> None:
    state = finish_returning(HandoffState(mode=HandoffMode.ARMED))

    assert state.mode == HandoffMode.ARMED
    assert state.notice == "No return is in progress."
