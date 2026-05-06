# Local Control Protocol Draft

This document sketches the local message format for future cross-device control.

The protocol should be:

- Local LAN only.
- Simple JSON at first.
- WebSocket-friendly.
- Easy to log and inspect.
- Strict about permissions.
- Versioned before multiple native agents rely on it.

## Transport

Initial transport:

```text
WebSocket over HTTP on the local network.
```

Later options:

- TLS with local certificates.
- mDNS discovery.
- A separate control channel and input-event channel.

## Common Message Fields

Every message should eventually include:

```json
{
  "version": 1,
  "type": "message_type",
  "device_id": "device-id",
  "session_id": "session-id",
  "time": 1760000000.0
}
```

The current MVP can stay simpler until the first two-agent prototype exists.

## Current MVP Compatibility

The current browser UI now sends the protocol v1 input names:

- `mouse_move`
- `mouse_button`
- `scroll`
- `ping`

The Windows server still accepts the older MVP aliases during the transition:

- `move` maps to `mouse_move`.
- `click` maps to `mouse_button`.

## Device Hello

Each native agent should create a stable local device identity on first launch.
The id should be random and project-specific, not a hardware or account id.

```json
{
  "type": "device_hello",
  "device_id": "windows-desktop-1",
  "name": "Windows Desktop",
  "os": "windows",
  "capabilities": ["send_input", "receive_input", "clipboard_text"]
}
```

## Pairing Request

```json
{
  "type": "pair_request",
  "device_id": "windows-desktop-1",
  "name": "Windows Desktop",
  "pairing_code": "123456"
}
```

## Trusted Pairing Draft

The first native-agent pairing flow should use a one-time 6-digit code shown on
the receiving computer.

Temporary receiver state:

```json
{
  "type": "pairing_code",
  "code": "123456",
  "expires_in": 60,
  "guest": false
}
```

Sender request:

```json
{
  "type": "pair_request",
  "device_id": "windows-desktop-1",
  "name": "Windows Desktop",
  "pairing_code": "123456",
  "remember_device": true
}
```

Receiver response:

```json
{
  "type": "pair_accept",
  "device_id": "macbook-1",
  "name": "MacBook",
  "trusted": true,
  "permissions": {
    "mouse": true,
    "keyboard": false,
    "clipboard_read": false,
    "clipboard_write": false,
    "file_receive": false
  }
}
```

Pairing acceptance should also create a temporary session grant for the current
connection. The session token is returned once, should be stored only in memory
by the receiver, and should expire automatically.

```json
{
  "session": {
    "session_id": "session-id",
    "device_id": "windows-desktop-1",
    "guest": false,
    "permissions": {
      "mouse": true,
      "keyboard": false,
      "clipboard_read": false,
      "clipboard_write": false,
      "file_receive": false
    },
    "expires_at": 1760000000.0,
    "idle_timeout_seconds": 600,
    "last_active_at": 1760000000.0
  },
  "session_token": "shown-once-session-token"
}
```

The receiver should expose active sessions to the local authenticated user
without showing token hashes or session tokens. The local user should be able to
revoke one session or all sessions.

Supported idle timeout choices are 60, 180, 300, 600, 1200, and 1800 seconds.
The default is 600 seconds. A session's `last_active_at` should be updated only
after an accepted action that required session permission. Pings and rejected
actions should not keep a session alive.

Guest sessions should use the same short-code shape with `remember_device` set
to `false`. Guest codes and session credentials should be discarded when the
session ends or expires.

Trusted reconnects should use the saved device id plus the saved shared secret
from the original pairing. A successful reconnect should update `last_seen_at`
for the trusted device and return the device's current permissions. The exact
wire shape is still deferred until the first native sender/receiver flow is
ready.

Permission updates should be constrained to known permission names. The initial
trusted-device permission set is:

```json
{
  "mouse": true,
  "keyboard": false,
  "clipboard_read": false,
  "clipboard_write": false,
  "file_receive": false
}
```

Session-authenticated input, clipboard, or file routes should verify both the
temporary session credential and the specific permission required for the
requested action. Unknown permission names should fail closed.

Changing trusted-device permissions or removing a trusted device should revoke
that device's active temporary sessions. The device can reconnect only by
creating a new valid session.

Pairing codes, saved secrets, clipboard contents, and typed text should not be
logged.

Invalid pairing-code attempts should be rate-limited. After 5 failed attempts,
the receiver should invalidate active pairing codes and require the local user
to generate a new code.

## Input Events

Relative mouse movement:

```json
{
  "type": "mouse_move",
  "dx": 12,
  "dy": -4
}
```

Mouse button:

```json
{
  "type": "mouse_button",
  "button": "left",
  "down": true
}
```

Scroll:

```json
{
  "type": "scroll",
  "amount": -12
}
```

Keyboard:

```json
{
  "type": "key",
  "key": "A",
  "down": true,
  "modifiers": ["ctrl"]
}
```

Text input:

```json
{
  "type": "text",
  "text": "hello"
}
```

## Clipboard

Clipboard should be permissioned separately from mouse/keyboard control.

Manual clipboard set:

```json
{
  "type": "clipboard_set",
  "format": "text/plain",
  "text": "hello"
}
```

Manual clipboard request:

```json
{
  "type": "clipboard_get",
  "format": "text/plain"
}
```

## Emergency Stop

```json
{
  "type": "emergency_stop",
  "reason": "user_pressed_button"
}
```

All agents should treat this as high priority.
Emergency stop should revoke temporary pairing sessions. Re-enabling local
control should not silently restore those sessions.

For trusted-device sessions in the final native app, emergency stop should place
the stopped trusted device into a local review-required state on the receiver.
The trusted device should not be allowed to reconnect until the receiving
computer's local user decides whether to keep or remove trust.
If trust is kept, the review-required state is cleared. If trust is removed, the
trusted-device record is deleted.

## Explicitly Out Of Scope For The Initial Protocol

These features should not be added until the user explicitly chooses them:

- Screen recording.
- Screenshots.
- Remote desktop video.
- Audio streaming.
- Cloud relay.
- Arbitrary shell command execution.

The project may support screen-related features later, but they must be opt-in and clearly visible.
