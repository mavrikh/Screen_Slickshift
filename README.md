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
- `logs/server.log`
- `uploads/`

## Architecture

The working browser-control MVP is a single FastAPI process:

- `app/main.py` defines API routes, WebSocket routes, static file serving, CORS configuration, startup logging, and emergency lockout behavior.
- `app/security.py` checks the shared pairing token and reusable token validation helpers.
- `app/websocket.py` receives touchpad events over `/ws/touchpad`.
- `app/protocol.py` parses simple JSON input events: `mouse_move`, `mouse_button`, `scroll`, and `ping`. It also accepts legacy `move` and `click` aliases.
- `app/input_control.py` lazily loads `pyautogui` for relative mouse movement, clicks, scrolling, and text typing.
- `app/clipboard.py` lazily loads `pyperclip` for clipboard get/set.
- `app/commands.py` runs only locally configured macro command arrays from `config/macros.json` and hides macros whose `platforms` list does not match the current OS.
- `app/files.py` saves uploaded files into `uploads/` with sanitized, unique filenames and a configurable size limit.
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
- File uploads into `uploads/`, with a default 50 MB limit.
- Emergency lockout that blocks input, clipboard, file upload, and macro actions.
- Security panel showing local device identity, trusted devices, active pairing sessions, and pairing-code generation.
- Trusted-device removal, trust-review actions, single-session revoke, and revoke-all sessions from the browser UI.
- Trusted-device permission toggles for the existing backend permission model.
- Session-authenticated touchpad WebSocket mode that enforces `mouse` permission.
- Session-authenticated text and clipboard routes enforcing `keyboard`, `clipboard_read`, and `clipboard_write`.
- Session-authenticated upload route enforcing `file_receive`.
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
- Permission records for `mouse`, `keyboard`, `clipboard_read`, `clipboard_write`, and `file_receive`.
- Trusted-device removal and permission updates revoking active sessions.
- Emergency lockout revoking active pairing sessions and marking trusted devices for review.
- Session-authenticated touchpad WebSocket input with `mouse` permission checks and `last_active_at` updates only after accepted mouse actions.
- Session-authenticated text input and clipboard read/write with separate permission checks.
- Session-authenticated upload with `file_receive` permission checks.

The Security panel uses these APIs for local-owner management. Session-authenticated clients can use existing session credentials for mouse input, text input, clipboard read/write, and upload. Macro actions still use the local-owner token path.

## Experimental macOS Tools

`agents/macos_receiver.py` runs a small FastAPI receiver with:

- `GET /api/status`
- `POST /api/lockout`
- `ws://HOST:8770/ws/input?token=TOKEN`

It accepts `mouse_move`, `mouse_button`, `scroll`, and `ping` protocol messages and injects mouse input through `pyautogui`.

`agents/send_test_input.py` sends simple test actions to a receiver:

- `wiggle`
- `click`
- `right-click`
- `scroll`

These tools do not implement global input capture, edge handoff, clipboard sync, file transfer, screen capture, persistence, TLS, packaging, or a native UI.

## Security Notes

The current protections visible in code are:

- Local-only design; no cloud services, accounts, or telemetry.
- Shared pairing token required for browser HTTP APIs and `/ws/touchpad`.
- Token is saved locally and printed to console, but startup logging avoids logging it.
- Project run helpers disable Uvicorn access logs so WebSocket URLs and request paths are not recorded by default.
- CORS is closed by default because the UI is served from the same origin.
- Remote clients cannot submit arbitrary shell commands.
- Macros are local allow-list entries and run with `shell=False`.
- Upload filenames are sanitized and path-stripped before saving.
- Emergency lockout is visible in the UI and checked by input, clipboard, upload, and macro helpers.
- Trusted-device shared secrets and session tokens are hashed at rest/in memory where applicable.
- Pairing code guesses are rate-limited in the pairing code book.
- Logs avoid token values, pairing codes, shared secrets, clipboard contents, file contents, and full typed text.

Known gaps:

- The browser-control route still grants broad control to anyone with the global token.
- No TLS/local certificate support is implemented.
- No mDNS discovery or firewall guidance is implemented in code.
- File uploads have a default 50 MB size limit. The UI does not yet display that limit before selecting a file.
- Legacy WebSocket query-token compatibility still exists for now, but the browser UI no longer uses it.
- The trusted-device/session model is not yet used to authorize macro actions.
- Desktop input and clipboard actions require OS support, installed optional backends, and any permissions required by that OS.

## Tests

Run:

```bash
python -m pytest
```

The tests are focused on backend primitives and APIs. There are no browser automation tests or end-to-end input injection tests in the repository.

## Current Safest Next Step

The safest next development step is to continue wiring the existing pairing/session permission model into real control paths without breaking the current browser-control MVP.

A conservative milestone would be:

1. Keep the existing token flow as local-owner/admin access.
2. Keep the Security panel as the owner/admin management surface.
3. Decide whether macros should remain owner-token-only or get a separate explicit permission.
4. Keep emergency lockout revocation behavior visible and tested.

See `docs/current_state.md` and `docs/roadmap.md` for the current source-of-truth snapshot.

For moving work between Codex, a local Ollama model, or a plain terminal workflow, start with `docs/HANDOFF.md`.
