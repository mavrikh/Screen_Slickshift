import json

from app.device_identity import DeviceIdentity, DeviceIdentityStore


def test_get_or_create_saves_stable_identity(tmp_path) -> None:
    path = tmp_path / "device_identity.json"
    store = DeviceIdentityStore(path)

    first = store.get_or_create()
    second = store.get_or_create()

    assert first == second
    assert first.device_id.startswith("screen-slickshift-")
    assert path.exists()


def test_load_reads_existing_identity(tmp_path) -> None:
    path = tmp_path / "device_identity.json"
    path.write_text(
        json.dumps(
            {
                "device_id": "screen-slickshift-test",
                "name": "Windows PC",
                "os": "windows",
                "created_at": 123.0,
            }
        ),
        encoding="utf-8",
    )

    identity = DeviceIdentityStore(path).load()

    assert identity == DeviceIdentity(
        device_id="screen-slickshift-test",
        name="Windows PC",
        os="windows",
        created_at=123.0,
    )


def test_load_rejects_invalid_identity(tmp_path) -> None:
    path = tmp_path / "device_identity.json"
    path.write_text(json.dumps({"device_id": "", "name": "Bad"}), encoding="utf-8")

    assert DeviceIdentityStore(path).load() is None


def test_identity_json_contains_no_secret_fields(tmp_path) -> None:
    path = tmp_path / "device_identity.json"
    identity = DeviceIdentityStore(path).get_or_create()

    saved_data = json.loads(path.read_text(encoding="utf-8"))

    assert saved_data == identity.to_dict()
    assert "token" not in saved_data
    assert "secret" not in saved_data
    assert "mac_address" not in saved_data
