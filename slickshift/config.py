"""
Slickshift mainline -- global configuration constants.

Change values here to tune behavior without touching module code.
Port 51847 is in the ephemeral/user range and is not assigned by IANA.
"""

import sys

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

# Width of the edge band in pixels. Cursor must be within this many pixels of
# the screen boundary (0 or screen_dim - 1) to enter the dwell timer.
EDGE_BAND_PX: int = 2

# Time in seconds the cursor must remain in the edge band before handoff fires.
EDGE_DWELL_S: float = 0.25

# Time in seconds to wait for handoff_ack from peer before rolling back to CAPTURING.
HANDOFF_ACK_TIMEOUT_S: float = 1.0

# Cooldown in seconds after transitioning into CAPTURING via handoff. Edge
# detection is suppressed during this window to prevent immediate re-trigger
# when the newly warped cursor lands at the entry edge.
HANDOFF_COOLDOWN_S: float = 0.5

# Logging
LOG_LEVEL: str = "DEBUG"

# UI refresh rate for the canvas dot (milliseconds between redraws).
CANVAS_REFRESH_MS: int = 16  # ~60 fps

# Maximum lines kept in the log panel before the oldest are dropped.
LOG_PANEL_MAX_LINES: int = 50

# pyautogui FailSafe is intentionally disabled in all versions.
# The cursor legitimately reaches corners in a KVM. See commit 523ee03.
PYAUTOGUI_FAILSAFE: bool = False

# Maximum number of outbound delta messages queued in the sender's send queue.
# When the queue is full the oldest entry is dropped and DELTAS_DROPPED increments.
DELTA_QUEUE_MAX: int = 32

# Window length (seconds) over which the receiver computes the incoming delta rate.
DELTA_RATE_WINDOW_S: float = 0.5

# How often (seconds) the receiver logs a sample of the most recent delta.
DELTA_SAMPLE_LOG_INTERVAL_S: float = 1.0

# Auto-reconnect on remote-initiated drop.
RECONNECT_INITIAL_DELAY_S: float = 1.0
RECONNECT_BACKOFF_FACTOR: float = 2.0
RECONNECT_MAX_DELAY_S: float = 30.0
RECONNECT_MAX_ATTEMPTS: int = 5

# Idle timeout when receiving with no input.
IDLE_TIMEOUT_S: float = 60.0

# Keyboard forwarding. Off by default; opt-in only.
# Sender's local keystrokes still fire on local apps -- this forwards them
# additionally to the peer. Use with explicit caution.
KEYBOARD_FORWARDING_DEFAULT: bool = False

# Outgoing scroll multiplier. Applied at capture time before sending scroll
# events to the peer. Mac trackpad senders benefit from 3-5x because pynput
# delivers fewer events per gesture than a hi-res mouse wheel. Tunable at
# runtime via the Mirror panel slider; persisted per machine via QSettings.
SCROLL_MULTIPLIER_DEFAULT: int = 4 if sys.platform == "darwin" else 1
SCROLL_MULTIPLIER_MIN: int = 1
SCROLL_MULTIPLIER_MAX: int = 10
