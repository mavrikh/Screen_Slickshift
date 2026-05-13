"""
cursor-bridge-test -- global configuration constants.

Change values here to tune behavior without touching module code.
Port 51847 is in the ephemeral/user range and is not assigned by IANA.
"""

# Network
DEFAULT_PORT: int = 51847
DEFAULT_HOST: str = "0.0.0.0"  # listen on all interfaces when acting as server

# Failsafe hotkey that forces immediate return to IDLE/host-owns regardless of
# network state. Must work even when the remote machine is unreachable.
HANDOFF_HOTKEY_RELEASE: str = "ctrl+alt+shift+esc"

# Heartbeat interval in seconds. Ping is sent every HEARTBEAT_INTERVAL_S.
# If no pong arrives within HEARTBEAT_TIMEOUT_S, the connection is marked dead.
# Dead-man switch: architecture invariant #4 in Slickshift CLAUDE.md.
HEARTBEAT_INTERVAL_S: float = 2.0
HEARTBEAT_TIMEOUT_S: float = 6.0

# Dwell time at screen edge before handoff fires (seconds).
EDGE_DWELL_S: float = 1.2

# Logging
LOG_LEVEL: str = "DEBUG"

# UI refresh rate for the canvas dot (milliseconds between redraws).
CANVAS_REFRESH_MS: int = 16  # ~60 fps

# Maximum lines kept in the log panel before the oldest are dropped.
LOG_PANEL_MAX_LINES: int = 50

# pyautogui FailSafe is intentionally disabled in all rebuilt versions.
# The cursor legitimately reaches corners in a KVM. See commit 523ee03.
PYAUTOGUI_FAILSAFE: bool = False
