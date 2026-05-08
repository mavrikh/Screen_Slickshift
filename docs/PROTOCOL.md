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

The implemented WebSocket input parser currently supports protocol version 1.
Flat v1 messages are still accepted:

```json
{
  "version": 1,
  "type": "message_type"
}
```

The parser also accepts the v1 payload envelope:

```json
{
  "version": 1,
  "payload": {
    "type": "message_type"
  }
}
```

Unsupported protocol versions fail closed. Future native-agent messages may add
fields such as `device_id`, `session_id`, and `time`, but those fields are not
required by the current mouse-input subset.

## Current MVP Compatibility

The current browser UI now sends the protocol v1 input names:

- `mouse_move`
- `mouse_button`
- `scroll`
- `ping`

The Windows server still accepts the older MVP aliases during the transition:

- `move` maps to `mouse_move`.
- `click` maps to `mouse_button`.

The current browser UI authenticates `/ws/touchpad` by opening the WebSocket and
sending the pairing token as the first message, so the token is not placed in
the WebSocket URL:

```json
{
  "type": "auth",
  "token": "pairing-token"
}
```

The backend still accepts the older query-token WebSocket form for compatibility
for now, but new clients should avoid putting credentials in URLs.
The experimental macOS receiver accepts the same first-message owner-token auth
shape on `/ws/input`, while keeping its older query-token form for compatibility
with simple test tools.
It also accepts `session_auth` with a valid session id/token that has `mouse`
permission. Session-authenticated receiver input rechecks `mouse` permission
for each non-ping event and refreshes session activity only after accepted mouse
input.
For `/api/lockout`, the receiver accepts `X-Pairing-Token` and keeps the older
query-token form for compatibility. New clients should prefer the header.
The receiver also exposes non-secret macOS guidance at `GET /api/permissions`,
including that Accessibility is required for mouse control and Screen Recording
is not needed or implemented in the current prototype.
Its status and index payloads include an `auth` self-description:

```json
{
  "websocket": {
    "preferred": "first-message",
    "supported": ["first-message", "query-token"]
  },
  "http": {
    "preferred": "x-pairing-token-header",
    "supported": ["x-pairing-token-header", "query-token"]
  },
  "token_exposed": false
}
```

Receivers expose the currently implemented protocol subset through their status
responses. The main FastAPI server reports this at `GET /api/status`, and the
experimental macOS receiver reports it at `GET /api/status`:

```json
{
  "protocol": {
    "version": 1,
    "input_events": ["mouse_move", "mouse_button", "scroll", "ping"],
    "legacy_aliases": ["move", "click"],
    "envelope": "v1-payload",
    "limits": {
      "max_mouse_delta": 5000,
      "max_scroll_amount": 1000
    }
  }
}
```

The experimental macOS receiver also reports `input_allowed`, which is the
inverse of `disabled`, on status, index, and lockout responses.

It also reports a prototype `capabilities` object on status and index responses:

```json
{
  "receive_input": true,
  "mouse": true,
  "keyboard": false,
  "clipboard": false,
  "file_transfer": false,
  "screen_capture": false,
  "requires_accessibility_permission": true,
  "requires_screen_recording_permission": false
}
```

This is receiver self-description only. It is not a trusted permission grant.

Session-authenticated clients can authenticate the touchpad WebSocket with the
same flat or v1 payload-envelope shape:

```json
{
  "type": "session_auth",
  "session_id": "session-id",
  "session_token": "shown-once-session-token"
}
```

```json
{
  "version": 1,
  "payload": {
    "type": "session_auth",
    "session_id": "session-id",
    "session_token": "shown-once-session-token"
  }
}
```

After `session_auth`, `mouse_move`, `mouse_button`, and `scroll` require the
session's `mouse` permission. Accepted mouse actions update `last_active_at`.
Pings and rejected actions do not keep the session alive.

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
  "file_receive": false,
  "macros": false
}
```

Session-authenticated input, clipboard, file, or macro routes should verify both
the temporary session credential and the specific permission required for the
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

Implemented over the WebSocket input path:

- `mouse_move`
- `mouse_button`
- `scroll`
- `ping`
- Legacy aliases: `move`, `click`

Draft-only in this document:

- `key`
- `text` over WebSocket
- Clipboard messages over WebSocket

Text, clipboard, file transfer, and macro actions currently use HTTP API routes
rather than the WebSocket input protocol.

The parser clamps per-message mouse movement to +/-5000 and scroll amounts to
+/-1000. Non-finite mouse deltas such as `NaN` or `Infinity` are treated as `0`.

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

The current HTTP session route for text input is:

```text
POST /api/session/send-text
```

```json
{
  "session_id": "session-id",
  "session_token": "shown-once-session-token",
  "text": "hello"
}
```

This requires the `keyboard` permission and updates `last_active_at` only after
accepted text input. Typed text should not be logged.

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

The current HTTP session routes for clipboard are:

```text
POST /api/session/clipboard/read
POST /api/session/clipboard/write
```

Read request:

```json
{
  "session_id": "session-id",
  "session_token": "shown-once-session-token"
}
```

Write request:

```json
{
  "session_id": "session-id",
  "session_token": "shown-once-session-token",
  "text": "hello"
}
```

Clipboard read requires `clipboard_read`. Clipboard write requires
`clipboard_write`. Clipboard contents should not be logged.

## File Receive

The current HTTP session route for receiving files is:

```text
POST /api/session/upload
```

This is a multipart form request with these fields:

```text
session_id=session-id
session_token=shown-once-session-token
file=@local-file
```

Session uploads require `file_receive`. Accepted uploads update
`last_active_at` only after the file is saved. Upload filenames are sanitized,
saved under the receiver's configured receive folder, and limited to 50 MB by
default. The current default receive folder is the user's Downloads folder. File
contents should not be logged or auto-opened.

## Macros

The current HTTP session route for approved local macros is:

```text
POST /api/session/macro
```

```json
{
  "session_id": "session-id",
  "session_token": "shown-once-session-token",
  "id": "macro-id"
}
```

Session macros require `macros`. Accepted macro actions update
`last_active_at` only after the local allow-listed macro runs. Remote clients
cannot provide arbitrary command strings; they can only request macro ids already
defined on the receiver.

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
