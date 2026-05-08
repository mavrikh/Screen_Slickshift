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
- File uploads to a configurable receive folder that defaults to Downloads.
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
- Receive folder display/change in the UI.

Not implemented today:

- Native Windows sender.
- Native Linux/SteamOS agent.
- Packaged macOS app.
- Edge-of-screen handoff.
- Global keyboard/mouse capture.
- TLS/local certificates.
- mDNS discovery.
- Nearby trusted-device send/broadcast flow.
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

Status: completed.

Goal: use the existing session and permission model for real control actions.

Possible work:

- Keep `X-Pairing-Token` as local-owner/admin authorization. Done.
- Add a separate session credential path for guest/trusted remote clients. Done for touchpad WebSocket.
- Require `mouse` permission for touchpad movement, clicks, and scroll. Done for session-authenticated touchpad WebSocket.
- Require `keyboard` permission for text or future keyboard input. Done for session-authenticated text send route.
- Require `clipboard_read` and `clipboard_write` for clipboard actions. Done for session-authenticated clipboard read/write routes.
- Require `file_receive` for uploads. Done for session-authenticated upload route.
- Require `macros` for approved local macro execution. Done for session-authenticated macro route.
- Update `last_active_at` only after accepted permissioned actions. Done for touchpad WebSocket mouse actions, session text, session clipboard, session upload, and session macro.
- Ensure pings and rejected actions do not keep sessions alive. Done for touchpad WebSocket session auth.

Done when:

- A guest/trusted client can be limited to specific permissions.
- Emergency lockout revokes sessions and re-enable does not silently restore them.
- Existing browser-control behavior is preserved for the local-owner token flow.

Completed.

Note: the current owner/admin pairing token is intentionally short for prototype
testing. The final trust design should use a short human-entered code only to
establish hidden shared secrets and fresh temporary session credentials.

## Phase 4: Tighten File Transfer

Goal: keep file transfer optional and bounded.

Possible work:

- Add a configurable upload size limit.
- Report rejected uploads clearly. Done at the API level, with browser-side size preflight, and with inline File Drop status.
- Default received files to the user's Downloads folder. Done.
- Allow changing the receive folder. Done through the current browser UI.
- Show recent file-transfer results. Done as in-memory server-run history for received and rejected uploads displayed in the File Drop list.
- Clear recent file-transfer results. Done for current server-run history.
- Add a dedicated file-transfer screen/window. Done as the `/file-transfer` browser page.
- Show trusted recipient readiness. Done with `can_receive_files` and blocked-reason metadata.
- Allow recipient file-receive permission changes from the file-transfer page. Done.
- Avoid auto-opening uploaded files.
- Consider per-device file receive permission once session auth is active. Done for session-authenticated upload route.
- Later, design nearby trusted-device send/broadcast flow.

Done when:

- Upload behavior is permissioned, has a clear maximum size, and saves to an owner-visible configurable folder.

## Phase 5: Improve Protocol Without Breaking Existing Clients

Goal: evolve the protocol from simple mouse messages toward cross-device input sharing.

Possible work:

- Version messages. Done for protocol v1 parsing.
- Define session-authenticated message envelopes. Done for the current WebSocket auth parser.
- Add keyboard event parsing only when a sender/receiver path needs it.
- Keep legacy `move` and `click` aliases until there is an intentional migration.
- Document which message types are implemented versus draft-only. Done.

Done when:

- Browser UI, Windows server, and experimental receiver can share the same implemented protocol subset.

Current status:

- Flat v1 input messages still work.
- V1 `payload` envelopes are accepted for WebSocket auth and input events.
- Unsupported protocol versions fail closed.
- Main server and experimental macOS receiver status responses advertise the implemented protocol subset.
- `agents/send_test_input.py --envelope` can exercise the v1 payload-envelope shape.
- Protocol numeric parsing clamps extreme per-message mouse and scroll values.
- Keyboard WebSocket events remain deferred because no sender path currently needs them.

## Phase 6: Build A Manual Windows Sender Prototype

Goal: prove a native sender path without edge handoff.

Possible work:

- Manual start/stop control mode. Done in the portable CLI prototype.
- Select one receiver. Done through host, port, and token CLI arguments.
- Send relative mouse movement. Done through manual `move DX DY` commands.
- Send basic mouse buttons. Done through manual `click` commands.
- Send basic keyboard events only if a safe capture method is chosen.
- Include a local panic hotkey if global capture is introduced.

Done when:

- A Windows sender can manually control a paired receiver over the LAN without screen capture or cloud services.

Current status:

- `agents/manual_sender.py` is a portable manual CLI sender prototype.
- It is not global input capture and not a native Windows app.
- It prints an internal-testing notice when it starts.
- It deliberately starts stopped and requires `start` before sending mouse input.
- `stop`, `pause`, and `panic` stop sending input inside the CLI session.
- It checks receiver `/api/status` before connecting unless `--skip-status-check` is used.
- It refuses to connect when receiver status reports emergency-disabled unless `--allow-disabled-receiver` is used.
- It refuses to connect when advertised receiver protocol events are missing required mouse/ping support.
- It can target either the experimental `/ws/input` query-token receiver or the main app `/ws/touchpad` first-message auth path.
- It supports repeated `--command` arguments for non-interactive manual tests.
- Scripted tests can include `wait SECONDS` and `--command-delay`.
- It includes a `help` command for the manual sender command list.
- It includes smoke-test presets for wiggle, clicks, and scroll.

## Phase 7: Continue macOS Receiver Prototype

Goal: move from the experimental script toward a safer receiver.

Possible work:

- Reuse the session/permission model.
- Keep Accessibility permission explanations clear. Started in the receiver startup banner.
- Keep Screen Recording out of scope. Started in status/index payloads and startup banner.
- Add clearer emergency stop behavior. Started in the receiver startup banner.
- Add tests around receiver protocol handling where practical. Started with helper-level tests for status payloads, event dispatch, lockout blocking, and unknown events.

Done when:

- A paired sender can control macOS mouse input through an explicit, permissioned session.

Current status:

- `agents/macos_receiver.py` remains experimental and token-based.
- Receiver status and index payloads explicitly keep screen capture out of scope.
- Receiver startup banner states Accessibility may be required and Screen Recording is not needed.
- Receiver startup banner names Ctrl+C and pyautogui screen-corner failsafe as emergency stop paths.
- Receiver `/api/permissions` reports macOS permission guidance, including Accessibility required for mouse control and Screen Recording not implemented.
- Receiver status/index/lockout payloads report `input_allowed` alongside `disabled`.
- Receiver status/index payloads report prototype capabilities, including Accessibility required and Screen Recording not required.
- Receiver `/ws/input` accepts first-message token auth and keeps query-token auth for compatibility.
- Receiver `/ws/input` accepts `session_auth` with `mouse` permission and updates session activity only after accepted mouse input.
- Receiver `/api/lockout` accepts `X-Pairing-Token` and keeps query-token auth for compatibility.
- Receiver status/index payloads advertise supported WebSocket and HTTP auth modes without exposing the token.
- Receiver event dispatch is tested without invoking real OS mouse control.
- Emergency-disabled state blocks mouse/click/scroll events while allowing ping.

## Phase 8: Research Linux / SteamOS Native Options

Goal: decide what is realistic on Linux and SteamOS before writing native code.

Known context:

- The current Steam Deck path is browser-based.
- Wayland restricts global input capture and injection.
- No native Linux/SteamOS agent exists in the repo.

Possible research areas:

- Browser Gamepad API for Steam Deck controls. Documented as a sender-side enhancement only.
- KDE/Wayland portals. Documented as the preferred future Linux receiver path to test first.
- `uinput` where appropriate. Documented as an advanced opt-in fallback, not the default.
- X11 support only when the user chooses an X11 session. Documented as a compatibility fallback only.

Done when:

- The repo has a documented, security-conscious implementation choice for Linux/SteamOS.

Current status:

- Phase 8 research decision is recorded in `docs/LINUX_STEAMOS.md`.
- No native Linux or SteamOS agent exists.
- Keep Steam Deck support browser-based for now.
- When Linux receiver work starts, test XDG Desktop Portal RemoteDesktop first.
- Treat `uinput`/libevdev as an explicit advanced fallback that may need local system setup.
- Treat XTEST as X11-only compatibility mode.
- Do not implement Linux screen capture as part of this phase.

## Phase 9: Edge Handoff

Goal: support cursor handoff only after manual sender/receiver control is reliable.

Possible work:

- Configure screen arrangement.
- Detect controller cursor at a screen edge.
- Hand off relative input to a target device.
- Provide a hotkey/manual escape path.
- Keep emergency stop always reachable.
- Document the state machine before adding global capture. Started in `docs/EDGE_HANDOFF.md`.
- Add a pure handoff config/state model. Done in `app/handoff.py`.
- Add a pure monitor layout geometry model for the future drag-arrange tab. Done in `app/handoff.py`.
- Add a browser simulation surface for layout and state. Started at `/handoff`.
- Add state transition tests that do not capture real input. Started in `tests/test_handoff.py`.
- Add an app-integrated manual remote mouse bridge. Started in `app/handoff_remote.py` and `/api/handoff/remote/*`.
- Wire `/handoff` to remote mouse movement for Mac-Windows testing. Started with a manual remote touchpad.

Done when:

- Handoff can be tested reliably without trapping the user or hiding control state.

Current status:

- Phase 9 planning is started in `docs/EDGE_HANDOFF.md`.
- Pure edge handoff config/state code exists.
- Pure monitor layout geometry exists for draggable screen rectangles and adjacent-edge route derivation.
- Prototype browser handoff page exists at `/handoff` for drag-layout and state simulation.
- Prototype layout blocks overlapping monitor tiles and reports overlaps from the preview API.
- Prototype layout snaps nearby screen sides edge-to-edge and supports zooming/panning the layout view, with Reset View fitting all screens.
- Prototype layout forces nearest-side snapping on release unless Freeform mode is enabled, and turning Freeform off snaps all screens inward.
- Prototype layout supports disabling a monitor from edge routing.
- Prototype layout supports multiple monitors per device and up to five devices in the simulation.
- Prototype Add Machine/Add Monitor use non-overlap placement.
- Prototype remote handoff APIs can connect this app to another Screen Slickshift receiver and send mouse movement, click, scroll, or ping events.
- Prototype `/handoff` page has remote host/IP, port, and token fields plus a manual remote touchpad for real mouse testing against another running app.
- Mac-to-Windows and Windows-to-Mac remote mouse movement are physically verified with both machines running the main app.
- No global input capture code exists.
- No pointer-edge detector exists.
- No automatic handoff network sender loop exists.
- Manual remote touchpad and the internal CLI manual sender remain the proving tools.
- Keyboard capture remains deferred.
- The next implementation slice should move toward visible/manual handoff activation and return behavior before adding OS-level edge detection or global capture.

## Phase 10: Packaging Decision

Goal: decide how this should become a regular app after the behavior is proven.

Options to evaluate later:

- Keep Python and package it.
- Tauri plus Rust/native helpers.
- Electron plus native helpers.

Done when:

- The protocol and first cross-device prototype are stable enough that packaging tradeoffs are meaningful.
