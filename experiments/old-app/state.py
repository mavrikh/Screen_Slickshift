from __future__ import annotations

from threading import Lock


class LockoutState:
    def __init__(self) -> None:
        self._disabled = False
        self._lock = Lock()

    def is_disabled(self) -> bool:
        with self._lock:
            return self._disabled

    def set_disabled(self, value: bool) -> bool:
        with self._lock:
            self._disabled = value
            return self._disabled


lockout_state = LockoutState()


class TrustedConnectionsState:
    def __init__(self) -> None:
        self._enabled = True
        self._lock = Lock()

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, value: bool) -> bool:
        with self._lock:
            self._enabled = value
            return self._enabled


trusted_connections_state = TrustedConnectionsState()
