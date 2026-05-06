# Roadmap

This roadmap is grounded in the current repository state. It separates implemented behavior from cautious next steps.

## Current Baseline

Implemented today:

- FastAPI server that can be started from a prepared Python environment on macOS or Windows.
- Steam Deck-friendly browser UI.
- Token-protected browser control.
- Mouse/touchpad movement.
- Mouse clicks.
- Scroll forwarding.
- Text sending.
- Clipboard get/set.
- Approved local macros.
- File uploads to `uploads/`.
- Emergency lockout.
- Protocol parser for mouse movement, mouse button, scroll, and ping.
- Backend device identity, pairing-code, trusted-device, permission, and session primitives.
- Experimental macOS receiver and sender test scripts.
- Lazy loading for desktop input and clipboard backends.
- Platform filtering for configured macros.
- Security panel for trusted devices, sessions, pairing codes, trust review, and permission toggles.
- Session-authenticated touchpad WebSocket input with `mouse` permission enforcement.
- Session-authenticated text, clipboard, and upload routes with permission enforcement.
- Default 50 MB upload size limit.

Not implemented today:

- Native Windows sender.
- Native Linux/SteamOS agent.
- Packaged macOS app.
- Edge-of-screen handoff.
- Global keyboard/mouse capture.
- Session-scoped authorization for macros.
- TLS/local certificates.
- mDNS discovery.
- Upload size limit display in the UI.
- Screen capture or remote desktop preview.

## Development Principles

- Keep the Windows browser-control MVP working.
- Preserve protocol compatibility unless deliberately migrating.
- Keep security behavior visible.
- Prefer small, testable changes.
- Keep clipboard and file transfer separately permissioned.
- Do not add screen capture, screenshots, OCR, or video features without an explicit future request.
- Avoid admin/root assumptions unless a platform feature genuinely needs them and the user accepts that tradeoff.

## Phase 1: Document And Stabilize Current State

Status: done for the current macOS-first development baseline.

Windows verification is deferred until a Windows machine with Python is available. That is acceptable for now because the immediate target is mac-to-mac development, while the Windows browser-control MVP remains preserved in code and docs.

Goals:

- Keep `README.md`, `AGENTS.md`, `docs/current_state.md`, and this roadmap accurate.
- Make clear which pairing/session pieces are active versus scaffolded.
- Keep the existing tests passing.
- Keep Python setup and server startup usable on macOS.
- Keep Windows setup documented and avoid breaking the existing Windows path.

Done when:

- A new contributor can run the server and tests from the docs on macOS.
- The docs do not imply unimplemented native-agent behavior exists.
- Deferred work is explicitly marked rather than treated as unknown.

## Phase 2: Surface Pairing And Session State In The UI

Status: in progress.

Goal: make the existing backend trust/session model visible before using it for more control.

Possible work:

- Show the local device identity. Done.
- Show trusted devices. Done.
- Show active pairing sessions. Done.
- Generate trusted and guest pairing codes. Done.
- Allow revoking one session. Done.
- Allow revoking all sessions. Done.
- Show `review_required` state for trusted devices. Done.
- Add trust-review actions for keep/remove. Done.
- Add trusted-device permission toggles. Done.
- Browser-test the panel on the current macOS baseline.

Done when:

- The browser UI can inspect and manage the backend trust/session state already present in code.
- Session and trusted-device secrets remain hidden from UI responses and logs.
- Permission enforcement remains deferred until state management is stable.

## Phase 3: Enforce Session-Scoped Permissions For Remote Control

Status: in progress.

Goal: use the existing session and permission model for real control actions.

Possible work:

- Keep `X-Pairing-Token` as local-owner/admin authorization. Done.
- Add a separate session credential path for guest/trusted remote clients. Done for touchpad WebSocket.
- Require `mouse` permission for touchpad movement, clicks, and scroll. Done for session-authenticated touchpad WebSocket.
- Require `keyboard` permission for text or future keyboard input. Done for session-authenticated text send route.
- Require `clipboard_read` and `clipboard_write` for clipboard actions. Done for session-authenticated clipboard read/write routes.
- Require `file_receive` for uploads. Done for session-authenticated upload route.
- Update `last_active_at` only after accepted permissioned actions. Done for touchpad WebSocket mouse actions, session text, session clipboard, and session upload.
- Ensure pings and rejected actions do not keep sessions alive. Done for touchpad WebSocket session auth.

Done when:

- A guest/trusted client can be limited to specific permissions.
- Emergency lockout revokes sessions and re-enable does not silently restore them.
- Existing browser-control behavior is preserved for the local-owner token flow.

## Phase 4: Tighten File Transfer

Goal: keep file transfer optional and bounded.

Possible work:

- Add a configurable upload size limit.
- Report rejected uploads clearly. Done at the API level; UI preflight/display remains.
- Keep uploads in the dedicated `uploads/` directory.
- Avoid auto-opening uploaded files.
- Consider per-device file receive permission once session auth is active. Done for session-authenticated upload route.

Done when:

- Upload behavior is permissioned and has a clear maximum size.

## Phase 5: Improve Protocol Without Breaking Existing Clients

Goal: evolve the protocol from simple mouse messages toward cross-device input sharing.

Possible work:

- Version messages.
- Define session-authenticated message envelopes.
- Add keyboard event parsing only when a sender/receiver path needs it.
- Keep legacy `move` and `click` aliases until there is an intentional migration.
- Document which message types are implemented versus draft-only.

Done when:

- Browser UI, Windows server, and experimental receiver can share the same implemented protocol subset.

## Phase 6: Build A Manual Windows Sender Prototype

Goal: prove a native sender path without edge handoff.

Possible work:

- Manual start/stop control mode.
- Select one receiver.
- Send relative mouse movement.
- Send basic mouse buttons.
- Send basic keyboard events only if a safe capture method is chosen.
- Include a local panic hotkey if global capture is introduced.

Done when:

- A Windows sender can manually control a paired receiver over the LAN without screen capture or cloud services.

## Phase 7: Continue macOS Receiver Prototype

Goal: move from the experimental script toward a safer receiver.

Possible work:

- Reuse the session/permission model.
- Keep Accessibility permission explanations clear.
- Keep Screen Recording out of scope.
- Add clearer emergency stop behavior.
- Add tests around receiver protocol handling where practical.

Done when:

- A paired sender can control macOS mouse input through an explicit, permissioned session.

## Phase 8: Research Linux / SteamOS Native Options

Goal: decide what is realistic on Linux and SteamOS before writing native code.

Known context:

- The current Steam Deck path is browser-based.
- Wayland restricts global input capture and injection.
- No native Linux/SteamOS agent exists in the repo.

Possible research areas:

- Browser Gamepad API for Steam Deck controls.
- KDE/Wayland portals.
- `uinput` where appropriate.
- X11 support only when the user chooses an X11 session.

Done when:

- The repo has a documented, security-conscious implementation choice for Linux/SteamOS.

## Phase 9: Edge Handoff

Goal: support cursor handoff only after manual sender/receiver control is reliable.

Possible work:

- Configure screen arrangement.
- Detect controller cursor at a screen edge.
- Hand off relative input to a target device.
- Provide a hotkey/manual escape path.
- Keep emergency stop always reachable.

Done when:

- Handoff can be tested reliably without trapping the user or hiding control state.

## Phase 10: Packaging Decision

Goal: decide how this should become a regular app after the behavior is proven.

Options to evaluate later:

- Keep Python and package it.
- Tauri plus Rust/native helpers.
- Electron plus native helpers.

Done when:

- The protocol and first cross-device prototype are stable enough that packaging tradeoffs are meaningful.
