from app.platform_support import platform_allows


def test_platform_allows_missing_platforms() -> None:
    assert platform_allows(None, current="macos") is True


def test_platform_allows_all_platforms() -> None:
    assert platform_allows(["all"], current="windows") is True


def test_platform_allows_matching_platform() -> None:
    assert platform_allows(["windows", "macos"], current="macos") is True


def test_platform_rejects_nonmatching_platform() -> None:
    assert platform_allows(["windows"], current="macos") is False
