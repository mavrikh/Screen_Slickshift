# Security And Privacy Principles

This project controls real computers, so security is a core feature.

The default posture should be:

```text
Local, explicit, permissioned, visible, reversible.
```

## Hard Rules

- No cloud services.
- No accounts required.
- No telemetry.
- No arbitrary shell commands from remote clients.
- No screen recording or screenshots unless the user explicitly enables that later.
- No remote desktop video unless the user explicitly enables that later.
- No hidden background control.
- No admin/root requirement unless a future platform feature truly needs it.

## Privacy Rule For Screens

The app should avoid capturing another system's screen by default.

Screen recording, screenshots, preview thumbnails, OCR, and remote desktop video are all sensitive. They should not be implemented or enabled unless the user explicitly says that feature is wanted.

If screen capture is ever added later, it should have:

- A separate permission toggle.
- A visible active indicator.
- Clear logs.
- A quick disable button.
- No capture during pairing by default.

## Pairing

Devices should not accept control from random LAN clients.

Preferred model:

- First launch creates a local device identity.
- Pairing requires a short code or token shown on the receiving device.
- Paired devices are stored in a trusted-device allow-list.
- Removing a trusted device should be easy.

The local device identity should be a random project-specific id saved on disk.
It should not be derived from a MAC address, serial number, cloud account, or
other hardware/account identifier.

### Trusted Devices And Guest Sessions

Screen Slickshift should support two pairing paths.

Trusted-device pairing is for computers the user expects to reconnect later. The
receiver shows a short one-time code, such as a 6-digit PIN. The sender enters
that code, both sides create a shared credential, and each computer saves the
other device as trusted. Future connections can use the saved credential instead
of asking for the PIN again.

Guest pairing is for temporary access. The receiver still shows a short one-time
code, but the session is not saved as a trusted device. When the session ends or
expires, the code and temporary credential are discarded.

Pairing should create a temporary session credential for the current connection.
That session credential is separate from a saved trusted-device secret. Session
credentials should be short-lived, kept in memory by the receiver, and removed
when the session ends or expires.

The local user should be able to view active pairing sessions and revoke one
session or all sessions without deleting trusted-device records.

Temporary sessions should have an idle timeout. Supported timeout choices are
1, 3, 5, 10, 20, and 30 minutes, with 10 minutes as the default. The session
should expire if no accepted control action occurs before the timeout. Network
pings or rejected actions should not keep a session alive.

Pairing codes should be:

- Generated locally.
- Valid for 60 seconds by default.
- Single-use.
- Shown only to the local user.
- Excluded from logs.

Pairing-code guesses should be rate-limited. After 5 failed attempts, active
pairing codes should be invalidated and the receiving computer should require a
new code. Guessed codes should not be logged.

Saved device records should include only what is needed to reconnect:

- Device id.
- Device name.
- Secret verifier or hash, not the plain setup code.
- Per-device permissions.
- Created and last-seen timestamps.

Clipboard and file-transfer permissions should default to off for new trusted
devices unless the user explicitly enables them.

## Permissions

Permissions should be per device where possible.

Possible permissions:

- Mouse control.
- Keyboard control.
- Clipboard read.
- Clipboard write.
- File receive.
- File send.
- Macro execution.
- Screen capture, disabled by default and not part of the early product.

Clipboard and files should not be bundled invisibly into mouse/keyboard control.

Trusted-device permissions should be editable after pairing. Unknown permission
names should be ignored or rejected rather than stored, so a client cannot
invent sensitive capabilities such as screen capture by sending new fields.
Future session-authenticated routes should verify both the temporary session
credential and the specific permission required for the action. Unknown
permission names should fail closed.
Changing permissions for a trusted device should revoke that device's active
temporary sessions so old permissions cannot continue running.

Removing a trusted device should also revoke that device's active temporary
sessions.

## Network Exposure

The app should bind to local interfaces intentionally.

Good defaults:

- Show the address the app is listening on.
- Prefer private LAN.
- Warn when listening on all interfaces.
- Do not expose to the internet.
- Avoid UPnP/port forwarding.

## Logs

Logs should be useful without being invasive.

Log:

- Pairing attempts.
- Accepted/rejected devices.
- Connection start/stop.
- Macro execution.
- Emergency stop.
- Errors.

Avoid logging:

- Full keystroke content.
- Clipboard contents.
- File contents.
- Password/token values.

## Emergency Stop

Every control surface should have an obvious emergency stop.

Emergency stop should:

- Stop receiving input.
- Stop sending input.
- Revoke temporary pairing sessions.
- Leave a clear visible state.
- Be reversible by the local user.

Re-enabling control should not silently restore revoked sessions. A remote
device should need a fresh valid session before controlling input again.

Future native agents should support a configurable local panic hotkey on the
receiving computer. Triggering that hotkey should enter emergency stop and
revoke temporary sessions. The app should log only that the hotkey triggered
emergency stop, not surrounding keystrokes.

For trusted-device connections in the final native app, emergency stop should
also require a local trust review on the receiving computer. The receiving
computer should show a local prompt asking whether the stopped trusted device
should still be allowed to connect in the future. That trusted device should not
be allowed to reconnect until the local user addresses the prompt. If the local
user chooses not to allow future connections, the trusted-device record should be
removed.

The backend should represent this as a review-required state on the trusted
device. While review is required, saved-credential reconnects for that device
should be rejected.

## macOS Notes

macOS input control will require user-granted permissions.

Likely permissions:

- Accessibility for injecting input.
- Input Monitoring for capturing global input later.

The app should explain why each permission is needed and avoid requesting permissions before the feature is used.

## Linux / SteamOS Notes

Wayland intentionally restricts global input capture and injection.

The project should not bypass platform security casually. Any Linux native agent should be researched carefully and should explain what permissions or system services it needs.

## File Transfer

File transfer should remain optional.

If enabled:

- Save to a dedicated folder.
- Sanitize filenames.
- Avoid auto-opening files.
- Consider per-device permission.
- Consider size limits.

## Macros

Macros must stay allow-listed.

Allowed:

- Commands defined locally in config.
- Fixed command arrays.
- `shell=False` or equivalent no-shell execution.

Not allowed:

- Browser-supplied command strings.
- Remote arbitrary shell execution.
- User-provided text interpolated into commands.
