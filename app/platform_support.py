from __future__ import annotations

import platform
from collections.abc import Iterable
from typing import Optional


def current_os_key() -> str:
    system = platform.system().strip().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def platform_allows(platforms: object, current: Optional[str] = None) -> bool:
    if platforms is None:
        return True
    if isinstance(platforms, str):
        candidates: Iterable[object] = [platforms]
    elif isinstance(platforms, Iterable):
        candidates = platforms
    else:
        return False

    current_key = current or current_os_key()
    allowed = {str(item).strip().lower() for item in candidates if str(item).strip()}
    return "all" in allowed or current_key in allowed
