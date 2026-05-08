# Screen Slickshift

Screen Slickshift is a local-only LAN input sharing prototype.

The current app is a FastAPI server with a browser control UI that is usable from a Steam Deck or another device on the same LAN. The most complete control target is still Windows, but the code is structured so backend tests and server startup can work on macOS and Windows. It is not a full KVM, remote desktop, screen sharing tool, or finished native cross-platform agent.

The repository also contains an experimental macOS receiver and a small sender test tool for protocol experiments.

## What Exists

- `app/`: FastAPI backend and local control helpers.
- `static/`: Browser UI served by the FastAPI app.
- `agents/`: Experimental macOS receiver and protocol sender test tool.
- `config/macros.json`: Local allow-list of macro commands.
- `tests/`: Pytest coverage for protocol parsing, pairing/session primitives, device identity, startup logging, and pairing APIs.
- `docs/`: Project docs for protocol, security, design, agents, roadmap, and current state.
- `run.ps1`: Windows PowerShell helper that creates a virtual environment, installs requirements, and starts Uvicorn.
- `run.py`: Cross-platform server runner for an already prepared Python environment.

Runtime-created local files are ignored by git:

- `config/pairing_token.txt`
- `config/trusted_devices.json`
- `config/device_identity.json`
- `config/receive_dir.txt`
- `logs/server.log`

## Architecture

The working browser-control MVP is a single FastAPI process:

- `app/main.py` defines API routes, WebSocket routes, static file serving, CORS configuration, startup logging, and emergency lockout behavior.
- `app/security.py` checks the shared pairing token and reusable token validation helpers.
- `app/websocket.py` receives touchpad events over `/ws/touchpad`.
- `app/protocol.py` parses protocol v1 JSON input events: `mouse_move`, `mouse_button`, `scroll`, and `ping`. It accepts both flat messages and v1 `payload` envelopes, plus legacy `move` and `click` aliases.
- `app/input_control.py` lazily loads `pyautogui` for relative mouse movement, clicks, scrolling, and text typing.
- `app/clipboard.py` lazily loads `pyperclip` for clipboard get/set.
- `app/commands.py` runs only locally configured macro command arrays from `config/macros.json` and hides macros whose `platforms` list does not match the current OS.
- `app/files.py` saves uploaded files into the configured receive folder with sanitized, unique filenames and a configurable size limit.
- `app/pairing.py` contains short pairing-code, trusted-device, and temporary-session primitives.
- `app/device_identity.py` creates a local random device identity.
- `app/state.py` contains the in-memory emergency lockout flag.

The static UI in `static/index.html`, `static/app.js`, and `static/style.css` stores the token in browser local storage, calls the token-protected HTTP APIs, and opens the touchpad WebSocket. The WebSocket sends the token as the first message instead of putting it in the URL.

Trusted-device/session code is present in backend APIs and tests. Session-authenticated touchpad WebSocket clients can use session credentials with `mouse` permission, while the current browser UI still uses the global pairing token as the local-owner/admin path.

## Setup

Install Python 3.9 or newer.

On Windows, open PowerShell in the repository root and run:

```powershell
.\run.ps1
```

The script creates `.venv` if needed, installs `requirements.txt`, and starts:

```text
http://0.0.0.0:8765
```

For local-only PC testing:

```powershell
.\run.ps1 -HostAddress 127.0.0.1 -Port 8765
```

Manual setup is also possible:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --no-access-log
```

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python run.py --host 127.0.0.1 --port 8765
```

Use `--host 0.0.0.0` only when you intentionally want LAN access.

On a Steam Deck or another LAN device, open:

```text
http://YOUR_WINDOWS_PC_IP:8765
```

Enter the pairing token printed in the server console. The token is intentionally printed to the local console and not written to logs.

## Implemented Browser-Control Features

- Token-protected status/auth checks.
- Browser touchpad over WebSocket.
- Relative mouse movement.
- Left, right, and middle click.
- Scroll forwarding with a UI speed slider.
- Pointer capture support in browsers that allow pointer lock.
- Text sending through `pyautogui.write`.
- Clipboard read/write through `pyperclip`.
- Local allow-listed macros from `config/macros.json`; the default checked-in macros are Windows-only.
- File uploads into a configurable receive folder that defaults to the user's Downloads folder, with a default 50 MB limit.
- Upload panel displays the configured upload limit and blocks oversized files before upload.
- Upload panel displays and can update the receive folder path.
- Upload panel shows recent received and rejected transfers from the current server run.
- Upload panel can clear recent transfer records for the current server run.
- Dedicated `/file-transfer` browser page for focused file-transfer use.
- File-transfer page shows trusted recipient readiness with explicit blocked reasons.
- File-transfer page can update trusted-device `file_receive` permission.
- Emergency lockout that blocks input, clipboard, file upload, and macro actions.
- Security panel showing local device identity, trusted devices, active pairing sessions, and pairing-code generation.
- Trusted-device removal, trust-review actions, single-session revoke, and revoke-all sessions from the browser UI.
- Trusted-device permission toggles for the existing backend permission model.
- Session-authenticated touchpad WebSocket mode that enforces `mouse` permission.
- Session-authenticated text and clipboard routes enforcing `keyboard`, `clipboard_read`, and `clipboard_write`.
- Session-authenticated upload route enforcing `file_receive`.
- Session-authenticated macro route enforcing `macros`; macros still come only from the local allow-list.
- Rotating server log file at `logs/server.log`.

## Implemented Pairing/Trust APIs

Backend code and tests currently cover:

- Random local device identity saved in `config/device_identity.json`.
- Trusted-device storage in `config/trusted_devices.json`.
- One-time 6-digit pairing codes.
- Guest pairing sessions.
- Trusted pairing sessions with returned shared secrets.
- Session token hashing.
- Session expiration and idle timeout validation.
- Permission records for `mouse`, `keyboard`, `clipboard_read`, `clipboard_write`, `file_receive`, and `macros`.
- Trusted-device removal and permission updates revoking active sessions.
- Emergency lockout revoking active pairing sessions and marking trusted devices for review.
- Session-authenticated touchpad WebSocket input with `mouse` permission checks and `last_active_at` updates only after accepted mouse actions.
- Session-authenticated text input and clipboard read/write with separate permission checks.
- Session-authenticated upload with `file_receive` permission checks.
- Session-authenticated macro execution with `macros` permission checks.

The Security panel uses these APIs for local-owner management. Session-authenticated clients can use existing session credentials for mouse input, text input, clipboard read/write, upload, and approved macros.

## Experimental macOS Tools

`agents/macos_receiver.py` runs a small FastAPI receiver with:

- `GET /api/status`
- `POST /api/lockout`
- `ws://HOST:8770/ws/input?token=TOKEN`

It accepts the current flat `mouse_move`, `mouse_button`, `scroll`, and `ping` protocol messages and injects mouse input through `pyautogui`.

`agents/send_test_input.py` sends simple one-shot test actions to a receiver:

- `wiggle`
- `click`
- `right-click`
- `scroll`

Add `--envelope` to send protocol v1 payload envelopes instead of flat input messages.

`agents/manual_sender.py` starts an interactive manual sender prototype:

```bash
python -m agents.manual_sender --host MAC_IP --port 8770 --token TOKEN
```

It prints an internal-testing notice when it starts. This tool is not the final user-facing sender and does not perform global input capture.

To target the main app server instead of the experimental receiver:

```bash
python -m agents.manual_sender --host SERVER_IP --port 8765 --token TOKEN --path /ws/touchpad --auth-mode first-message
```

It requires explicit `start` before mouse input commands are sent. Supported commands are `move DX DY`, `click [left|right|middle]`, `scroll AMOUNT`, `wait SECONDS`, `ping`, `help`, `stop`, and `quit`.
Before opening the WebSocket it reads the receiver's `/api/status` endpoint and reports whether the receiver is reachable, disabled, and which protocol subset it advertises. If the receiver reports emergency-disabled, the sender stops before connecting unless `--allow-disabled-receiver` is used for diagnostics. If the receiver advertises protocol events but is missing mouse movement, mouse button, scroll, or ping support, the sender also stops before connecting. Use `--skip-status-check` only when testing a receiver that does not expose that endpoint.

The experimental receiver also exposes `GET /api/permissions` with non-secret macOS guidance: Accessibility is required for mouse control, Screen Recording is not implemented, and emergency stop paths are listed.
The receiver WebSocket accepts owner-token auth, legacy query-token auth, and `session_auth` when the session has `mouse` permission.

For repeatable tests, pass `--command` more than once:

```bash
python -m agents.manual_sender --host MAC_IP --port 8770 --token TOKEN --command start --command "move 40 0" --command "click left" --command stop
```

Use `--command-delay SECONDS` to pause after each scripted command.
For quick smoke tests, use `--preset wiggle`, `--preset click`, `--preset right-click`, `--preset middle-click`, or `--preset scroll`.

These tools do not implement global input capture, edge handoff, clipboard sync, file transfer, screen capture, persistence, TLS, packaging, or a native UI.

## Linux / SteamOS Direction

There is no native Linux or SteamOS agent yet. The current Steam Deck path remains the browser UI.

Phase 8 research is recorded in `docs/LINUX_STEAMOS.md`. The current direction is to test XDG Desktop Portal RemoteDesktop first for future Wayland receiver work, keep `uinput`/libevdev as an explicit advanced fallback, and treat XTEST as X11-only compatibility mode.

## Edge Handoff Direction

There is no pointer-edge detector, handoff sender loop, or global input capture code yet. Phase 9 planning is recorded in `docs/EDGE_HANDOFF.md`, and the pure config/state plus monitor layout geometry model lives in `app/handoff.py`. The prototype `/handoff` page lets the user drag simulated screens, snap sides edge-to-edge, use Freeform mode, fit/pan/zoom the layout view, disable individual monitors from edge routing, add multiple monitors/devices, preview derived routes, and exercise the handoff state machine without sending remote input.

## Security Notes

The current protections visible in code are:

- Local-only design; no cloud services, accounts, or telemetry.
- Shared owner pairing token required for browser HTTP APIs and `/ws/touchpad`.
- The owner token is currently a 6-digit prototype/testing convenience. Final device trust should use a short human-entered pairing code only to establish hidden long-lived secrets and temporary session credentials.
- Token is saved locally and printed to console, but startup logging avoids logging it.
- Project run helpers disable Uvicorn access logs so WebSocket URLs and request paths are not recorded by default.
- CORS is closed by default because the UI is served from the same origin.
- Remote clients cannot submit arbitrary shell commands.
- Macros are local allow-list entries and run with `shell=False`.
- Upload filenames are sanitized and path-stripped before saving.
- Receive folder changes require the owner token and are stored locally in `config/receive_dir.txt`.
- Emergency lockout is visible in the UI and checked by input, clipboard, upload, and macro helpers.
- Trusted-device shared secrets and session tokens are hashed at rest/in memory where applicable.
- Pairing code guesses are rate-limited in the pairing code book.
- Logs avoid token values, pairing codes, shared secrets, clipboard contents, file contents, and full typed text.

Known gaps:

- The browser-control route still grants broad control to anyone with the global owner token.
- The owner token is intentionally short for current testing and should not be treated as final security.
- No TLS/local certificate support is implemented.
- No mDNS discovery or firewall guidance is implemented in code.
- File uploads have a default 50 MB size limit, and the browser UI displays/preflights that limit.
- Legacy WebSocket query-token compatibility still exists for now, but the browser UI no longer uses it.
- Desktop input and clipboard actions require OS support, installed optional backends, and any permissions required by that OS.

## Tests

Run:

```bash
python -m pytest
```

The tests are focused on backend primitives and APIs. There are no browser automation tests or end-to-end input injection tests in the repository.

## Current Safest Next Step

The safest next development step is to continue Phase 5 protocol tightening without breaking the current browser-control MVP.

A conservative milestone would be:

1. Keep the existing token flow as local-owner/admin access.
2. Keep the Security panel as the owner/admin management surface.
3. Keep protocol v1 compatible with the browser UI and experimental macOS receiver.
4. Plan the later nearby trusted-device send flow.

See `docs/current_state.md` and `docs/ROADMAP.md` for the current source-of-truth snapshot.

For moving work between Codex, a local Ollama model, or a plain terminal workflow, start with `docs/HANDOFF.md`.
