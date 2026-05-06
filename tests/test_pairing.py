import time

from app.pairing import (
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    PairingCodeBook,
    PairingSessionBook,
    TrustedDeviceStore,
)


def test_pairing_code_is_six_digits() -> None:
    code_book = PairingCodeBook()

    pairing_code = code_book.create_code()

    assert pairing_code.code.isdigit()
    assert len(pairing_code.code) == 6
    assert pairing_code.guest is False
    assert pairing_code.expires_at - time.time() <= 60


def test_pairing_code_is_single_use() -> None:
    code_book = PairingCodeBook()
    pairing_code = code_book.create_code()

    assert code_book.consume_code(pairing_code.code) == pairing_code
    assert code_book.consume_code(pairing_code.code) is None


def test_expired_pairing_code_is_rejected() -> None:
    code_book = PairingCodeBook(ttl_seconds=-1)
    pairing_code = code_book.create_code()

    assert code_book.consume_code(pairing_code.code) is None


def test_guest_pairing_code_marks_temporary_session() -> None:
    code_book = PairingCodeBook()

    pairing_code = code_book.create_code(guest=True)

    assert pairing_code.guest is True


def test_pairing_code_book_rate_limits_failed_attempts() -> None:
    code_book = PairingCodeBook(
        max_failed_attempts=2,
    )

    first = code_book.consume_code_result("111111")
    second = code_book.consume_code_result("222222")
    third = code_book.consume_code_result("333333")

    assert first.rate_limited is False
    assert second.rate_limited is True
    assert second.regenerate_required is True
    assert third.rate_limited is True
    assert third.regenerate_required is True


def test_pairing_code_book_resets_failed_attempts_after_success() -> None:
    code_book = PairingCodeBook(
        max_failed_attempts=2,
    )
    pairing_code = code_book.create_code()

    failed = code_book.consume_code_result("111111")
    success = code_book.consume_code_result(pairing_code.code)
    after_success_failure = code_book.consume_code_result("222222")

    assert failed.rate_limited is False
    assert success.pairing_code == pairing_code
    assert after_success_failure.rate_limited is False


def test_pairing_code_book_requires_new_code_after_too_many_failures() -> None:
    code_book = PairingCodeBook(max_failed_attempts=2)
    pairing_code = code_book.create_code()

    code_book.consume_code_result("111111")
    limited = code_book.consume_code_result("222222")
    original_code_after_limit = code_book.consume_code_result(pairing_code.code)

    new_pairing_code = code_book.create_code()
    new_code_result = code_book.consume_code_result(new_pairing_code.code)

    assert limited.regenerate_required is True
    assert original_code_after_limit.pairing_code is None
    assert original_code_after_limit.regenerate_required is True
    assert new_code_result.pairing_code == new_pairing_code


def test_trusted_device_store_saves_hash_not_plain_secret(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")

    credential = store.trust_device("macbook-1", "MacBook")
    saved_text = store.path.read_text(encoding="utf-8")

    assert credential.shared_secret not in saved_text
    assert store.verify_secret("macbook-1", credential.shared_secret) is True
    assert store.verify_secret("macbook-1", "wrong-secret") is False


def test_trusted_device_permissions_default_to_safe_values(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")

    credential = store.trust_device("macbook-1", "MacBook", {"keyboard": True, "file_receive": True})

    permissions = credential.device.permissions
    assert permissions["mouse"] is True
    assert permissions["keyboard"] is True
    assert permissions["clipboard_read"] is False
    assert permissions["clipboard_write"] is False
    assert permissions["file_receive"] is True


def test_trusted_device_public_dict_excludes_secret_hash(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")

    credential = store.trust_device("macbook-1", "MacBook")

    assert "secret_hash" not in credential.device.public_dict()


def test_trusted_device_store_loads_existing_devices(tmp_path) -> None:
    path = tmp_path / "trusted_devices.json"
    store = TrustedDeviceStore(path)
    credential = store.trust_device("macbook-1", "MacBook")

    loaded_store = TrustedDeviceStore(path)
    loaded_device = loaded_store.get_device("macbook-1")

    assert loaded_device is not None
    assert loaded_device.name == "MacBook"
    assert loaded_store.verify_secret("macbook-1", credential.shared_secret) is True


def test_verify_and_mark_seen_updates_last_seen(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")
    credential = store.trust_device("macbook-1", "MacBook")

    verified_device = store.verify_and_mark_seen("macbook-1", credential.shared_secret)

    assert verified_device is not None
    assert verified_device.device_id == "macbook-1"
    assert verified_device.last_seen_at is not None

    loaded_device = store.get_device("macbook-1")
    assert loaded_device is not None
    assert loaded_device.last_seen_at == verified_device.last_seen_at


def test_verify_and_mark_seen_rejects_wrong_secret_without_updating(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")
    store.trust_device("macbook-1", "MacBook")

    verified_device = store.verify_and_mark_seen("macbook-1", "wrong-secret")

    assert verified_device is None
    loaded_device = store.get_device("macbook-1")
    assert loaded_device is not None
    assert loaded_device.last_seen_at is None


def test_verify_and_mark_seen_rejects_unknown_device(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")

    assert store.verify_and_mark_seen("missing-device", "secret") is None


def test_mark_review_required_blocks_trusted_reconnect(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")
    credential = store.trust_device("macbook-1", "MacBook")

    marked_count = store.mark_review_required({"macbook-1"})

    assert marked_count == 1
    device = store.get_device("macbook-1")
    assert device is not None
    assert device.review_required is True
    assert store.verify_and_mark_seen("macbook-1", credential.shared_secret) is None


def test_mark_review_required_ignores_unknown_devices(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")

    assert store.mark_review_required({"missing-device"}) == 0


def test_resolve_review_can_keep_trusted_device(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")
    credential = store.trust_device("macbook-1", "MacBook")
    store.mark_review_required({"macbook-1"})

    trusted_device = store.resolve_review("macbook-1", keep_trust=True)

    assert trusted_device is not None
    assert trusted_device.review_required is False
    assert store.verify_and_mark_seen("macbook-1", credential.shared_secret) is not None


def test_resolve_review_can_remove_trusted_device(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")
    store.trust_device("macbook-1", "MacBook")
    store.mark_review_required({"macbook-1"})

    trusted_device = store.resolve_review("macbook-1", keep_trust=False)

    assert trusted_device is None
    assert store.get_device("macbook-1") is None


def test_update_permissions_changes_allowed_values(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")
    store.trust_device("macbook-1", "MacBook")

    updated_device = store.update_permissions(
        "macbook-1",
        {"keyboard": True, "clipboard_read": True},
    )

    assert updated_device is not None
    assert updated_device.permissions["mouse"] is True
    assert updated_device.permissions["keyboard"] is True
    assert updated_device.permissions["clipboard_read"] is True
    assert updated_device.permissions["clipboard_write"] is False


def test_update_permissions_ignores_unknown_values(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")
    store.trust_device("macbook-1", "MacBook")

    updated_device = store.update_permissions(
        "macbook-1",
        {"screen_capture": True, "file_receive": True},
    )

    assert updated_device is not None
    assert "screen_capture" not in updated_device.permissions
    assert updated_device.permissions["file_receive"] is True


def test_update_permissions_rejects_unknown_device(tmp_path) -> None:
    store = TrustedDeviceStore(tmp_path / "trusted_devices.json")

    assert store.update_permissions("missing-device", {"keyboard": True}) is None


def test_trusted_device_store_discards_expired_codes() -> None:
    code_book = PairingCodeBook(ttl_seconds=-1)
    pairing_code = code_book.create_code()

    time.sleep(0.001)
    code_book.discard_expired()

    assert code_book.consume_code(pairing_code.code) is None


def test_pairing_session_book_creates_verifiable_session() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)

    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"keyboard": True},
    )

    verified_session = session_book.verify_session(
        grant.session.session_id,
        grant.session_token,
    )
    assert verified_session == grant.session
    assert verified_session.permissions["keyboard"] is True
    assert verified_session.permissions["clipboard_read"] is False
    assert verified_session.idle_timeout_seconds == DEFAULT_IDLE_TIMEOUT_SECONDS
    assert verified_session.last_active_at > 0


def test_pairing_session_book_accepts_allowed_idle_timeout() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)

    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        idle_timeout_seconds=60,
    )

    assert grant.session.idle_timeout_seconds == 60


def test_pairing_session_book_falls_back_for_invalid_idle_timeout() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)

    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        idle_timeout_seconds=999,
    )

    assert grant.session.idle_timeout_seconds == DEFAULT_IDLE_TIMEOUT_SECONDS


def test_pairing_session_book_does_not_store_plain_token() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)

    grant = session_book.create_session("macbook-1", guest=False)

    assert grant.session_token
    assert grant.session.token_hash != grant.session_token
    assert "token_hash" not in grant.session.public_dict()


def test_pairing_session_book_rejects_wrong_token() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session("macbook-1", guest=False)

    assert session_book.verify_session(grant.session.session_id, "wrong-token") is None


def test_pairing_session_book_rejects_expired_session() -> None:
    session_book = PairingSessionBook(ttl_seconds=-1)
    grant = session_book.create_session("macbook-1", guest=False)

    assert session_book.verify_session(grant.session.session_id, grant.session_token) is None


def test_pairing_session_book_rejects_idle_session() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        idle_timeout_seconds=60,
    )
    stale_session = grant.session.__class__(
        session_id=grant.session.session_id,
        device_id=grant.session.device_id,
        guest=grant.session.guest,
        permissions=grant.session.permissions,
        token_hash=grant.session.token_hash,
        expires_at=grant.session.expires_at,
        idle_timeout_seconds=grant.session.idle_timeout_seconds,
        last_active_at=time.time() - 61,
    )
    session_book._sessions[grant.session.session_id] = stale_session

    assert session_book.verify_session(grant.session.session_id, grant.session_token) is None


def test_pairing_session_book_marks_session_active() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session("macbook-1", guest=False)
    original_last_active_at = grant.session.last_active_at
    time.sleep(0.001)

    updated_session = session_book.mark_session_active(
        grant.session.session_id,
        grant.session_token,
    )

    assert updated_session is not None
    assert updated_session.last_active_at > original_last_active_at


def test_pairing_session_book_does_not_mark_wrong_token_active() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session("macbook-1", guest=False)

    assert session_book.mark_session_active(grant.session.session_id, "wrong-token") is None


def test_pairing_session_book_verifies_allowed_permission() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"mouse": True, "keyboard": True},
    )

    session = session_book.verify_session_permission(
        grant.session.session_id,
        grant.session_token,
        "keyboard",
    )

    assert session == grant.session


def test_pairing_session_book_rejects_denied_permission() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session("macbook-1", guest=False)

    assert session_book.verify_session_permission(
        grant.session.session_id,
        grant.session_token,
        "keyboard",
    ) is None


def test_pairing_session_book_rejects_unknown_permission() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session("macbook-1", guest=False)

    assert session_book.verify_session_permission(
        grant.session.session_id,
        grant.session_token,
        "screen_capture",
    ) is None


def test_pairing_session_book_rejects_permission_with_wrong_token() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"keyboard": True},
    )

    assert session_book.verify_session_permission(
        grant.session.session_id,
        "wrong-token",
        "keyboard",
    ) is None


def test_pairing_session_book_rejects_permission_for_expired_session() -> None:
    session_book = PairingSessionBook(ttl_seconds=-1)
    grant = session_book.create_session(
        "macbook-1",
        guest=False,
        permissions={"keyboard": True},
    )

    assert session_book.verify_session_permission(
        grant.session.session_id,
        grant.session_token,
        "keyboard",
    ) is None


def test_pairing_session_book_removes_session() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session("macbook-1", guest=False)

    assert session_book.remove_session(grant.session.session_id) is True
    assert session_book.verify_session(grant.session.session_id, grant.session_token) is None


def test_pairing_session_book_removes_sessions_for_device() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    first = session_book.create_session("macbook-1", guest=False)
    second = session_book.create_session("macbook-1", guest=False)
    other = session_book.create_session("guest-laptop", guest=True)

    removed_count = session_book.remove_sessions_for_device("macbook-1")

    assert removed_count == 2
    assert session_book.verify_session(first.session.session_id, first.session_token) is None
    assert session_book.verify_session(second.session.session_id, second.session_token) is None
    assert session_book.verify_session(other.session.session_id, other.session_token)


def test_pairing_session_book_lists_sessions_without_tokens() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    grant = session_book.create_session("macbook-1", guest=False)

    sessions = session_book.list_sessions()

    assert sessions == [grant.session]
    assert "token_hash" not in sessions[0].public_dict()


def test_pairing_session_book_list_discards_expired_sessions() -> None:
    session_book = PairingSessionBook(ttl_seconds=-1)
    session_book.create_session("macbook-1", guest=False)

    assert session_book.list_sessions() == []


def test_pairing_session_book_removes_all_sessions() -> None:
    session_book = PairingSessionBook(ttl_seconds=300)
    first = session_book.create_session("macbook-1", guest=False)
    second = session_book.create_session("guest-laptop", guest=True)

    removed_count = session_book.remove_all_sessions()

    assert removed_count == 2
    assert session_book.verify_session(first.session.session_id, first.session_token) is None
    assert session_book.verify_session(second.session.session_id, second.session_token) is None
