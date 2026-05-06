# Screen Slickshift Project Notes

Treat the repository as the source of truth. Do not assume previous chats or undocumented features exist.

Screen Slickshift is a local-only LAN input sharing project. The current working product is a FastAPI server with a browser UI for local network control. Windows is still the most complete control target, but backend code and tests should remain usable on macOS and Windows. The repository also includes experimental protocol, pairing, trusted-device, and macOS receiver code.

## Hard Constraints

- No cloud services.
- No required accounts.
- No telemetry.
- No arbitrary shell commands from remote clients.
- No screen recording, screenshots, remote desktop previews, OCR, or video capture unless the user explicitly asks for that feature later.
- Keep security and emergency stop behavior visible.
- Prefer small understandable modules over large abstractions.
- Preserve the working Windows browser-control MVP unless the task explicitly changes it.
- Keep imports and tests OS-portable; desktop input and clipboard backends should be loaded only when actions need them.
- Keep protocol messages compatible unless intentionally migrating.
- Avoid admin/root assumptions unless the user explicitly accepts them.
- Do not log tokens, pairing codes, shared secrets, session tokens, clipboard contents, full keystrokes, or file contents.
- Use `apply_patch` for manual edits.

## Current Structure

- `app/main.py`: FastAPI app, API routes, WebSocket route, static UI serving, CORS, startup logging, lockout handling.
- `app/config.py`: Project paths, directory creation, pairing token creation, macro loading.
- `app/security.py`: Shared token checks for HTTP and WebSocket access.
- `app/input_control.py`: `pyautogui` mouse, scroll, and text injection.
- `app/websocket.py`: Touchpad WebSocket handler.
- `app/protocol.py`: JSON input event parser.
- `app/commands.py`: Local allow-listed macro loading and execution.
- `app/clipboard.py`: Clipboard get/set through `pyperclip`.
- `app/files.py`: Upload saving with filename sanitization and size limits.
- `app/pairing.py`: Pairing codes, trusted devices, session credentials, permissions.
- `app/device_identity.py`: Local random device identity storage.
- `app/state.py`: In-memory emergency lockout state.
- `static/`: Browser UI assets.
- `agents/`: Experimental macOS receiver and sender test tool.
- `tests/`: Backend tests.
- `docs/current_state.md`: Current repository snapshot.
- `docs/roadmap.md`: Grounded roadmap.
- `docs/HANDOFF.md`: Short continuity notes for switching between Codex, local LLMs, or terminal workflows.

## Current Working Features

- Token-protected browser UI.
- Touchpad movement over WebSocket.
- Mouse clicks and scrolling.
- Text sending.
- Clipboard get/set.
- Approved local macros.
- File upload to `uploads/` with a default 50 MB limit.
- Emergency lockout for active control actions.
- Backend pairing/trusted-device/session APIs and tests.
- Experimental macOS receiver and sender test scripts.
- Cross-platform `run.py` server runner for prepared Python environments.

## Important Current Limits

- The browser-control UI still uses the global pairing token rather than session-scoped trusted-device permissions.
- Pairing/session APIs exist, but they are not the primary authorization path for the browser UI.
- The macOS receiver is experimental and manual.
- Desktop input and clipboard behavior depends on OS support and permissions.
- There is no native Windows sender, native Linux/SteamOS agent, edge handoff, keyboard event capture, TLS, discovery, packaging, or screen capture.
- Session-authenticated upload now requires `file_receive`; macros remain owner-token-only.

## Documentation To Keep Current

- `README.md`
- `docs/current_state.md`
- `docs/roadmap.md`
- `docs/HANDOFF.md`
- `docs/PROTOCOL.md`
- `docs/SECURITY.md`
- `docs/DESIGN.md`
- `docs/AGENTS.md`

When updating docs, distinguish clearly between implemented behavior, tested backend scaffolding, and future intent.

For Ollama/local LLM fallback, start with `docs/HANDOFF.md`. Keep it current enough that a model with no chat history can resume safely.
