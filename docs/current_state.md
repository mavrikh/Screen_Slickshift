# Current State

Last reviewed from repository contents on 2026-05-06.

This document describes what is present in the codebase. It does not claim features that are only future intent.

For switching between GPT/Codex, Ollama, another local LLM, or a plain terminal workflow, use `docs/HANDOFF.md` as the quick continuity file.

Phase 1 stabilization is complete for the current macOS-first development baseline. Windows verification is intentionally deferred until a Windows machine with Python is available.

## 1. App Architecture

Screen Slickshift currently has three main parts:

- A FastAPI backend in `app/`.
- A static browser UI in `static/`.
- Experimental native-agent scripts in `agents/`.

The working MVP is the FastAPI backend plus static UI:

- `app/main.py` creates the FastAPI app, serves `/`, mounts `/static`, exposes HTTP APIs, exposes `/ws/touchpad`, configures CORS, prints the pairing token on startup, and writes rotating logs.
- `static/app.js` stores the token in browser local storage, calls the token-protected APIs, opens the touchpad WebSocket, and sends input events.
- `app/input_control.py`, `app/clipboard.py`, `app/commands.py`, and `app/files.py` do the local side effects. Desktop input and clipboard libraries are loaded lazily so backend imports and tests are not tied to one desktop OS.
- `app/protocol.py` parses the small JSON input-event format.
- `app/pairing.py` contains a newer pairing/trusted-device/session model used by backend APIs and tests.

The backend trusted-device/session model now protects session-authenticated touchpad WebSocket input for mouse actions. The current browser UI still uses the global pairing token as the local-owner/admin path.

## 2. How To Run It

On Windows:

```powershell
.\run.ps1
```

This creates `.venv` if needed, installs `requirements.txt`, and starts:

```text
http://0.0.0.0:8765
```

For local-only testing:

```powershell
.\run.ps1 -HostAddress 127.0.0.1 -Port 8765
```

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py --host 127.0.0.1 --port 8765
```

Manual run from an environment with dependencies installed:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8765
```

Tests:

```bash
python -m pytest
```

Expected on the current macOS baseline:

```text
109 passed, 2 warnings
```

The warnings are FastAPI `on_event` deprecation warnings and are intentionally deferred.

Experimental macOS receiver:

```bash
python -m agents.macos_receiver --host 0.0.0.0 --port 8770
```

Experimental sender test:

```bash
python -m agents.send_test_input --host MAC_IP --port 8770 --token TOKEN --action wiggle
```

## 3. Features Implemented

Working browser-control MVP:

- Serve a browser UI.
- Pairing-token entry and token check.
- Touchpad WebSocket connection.
- Relative mouse movement.
- Left, right, and middle click.
- Scroll forwarding.
- Browser pointer capture support.
- Scroll speed slider.
- Text sending to the controlled PC.
- Clipboard read and write.
- Macro list loaded from `config/macros.json`.
- Platform-specific macros can be hidden by a `platforms` field.
- Running approved macro command arrays.
- File upload to `uploads/`.
- Emergency stop and re-enable.
- Security panel with local device identity, pairing-code generation, trusted devices, and active pairing sessions.
- UI actions for trusted-device removal, trust review keep/remove, single-session revoke, and revoke-all sessions.
- Trusted-device permission toggles backed by the existing permission update API.
- Session-authenticated touchpad WebSocket mode enforcing `mouse` permission for movement, clicking, and scrolling.
- Session-authenticated text route enforcing `keyboard` permission.
- Session-authenticated clipboard read/write routes enforcing `clipboard_read` and `clipboard_write`.
- Session-authenticated upload route enforcing `file_receive` permission.
- Session-authenticated macro route enforcing `macros` permission.
- Default 50 MB upload size limit.
- Activity log in the browser UI.
- Rotating server logs.
- LLM text generation via `/api/generate-text` route, using local OpenAI-compatible API (LM Studio).

Backend pairing/trust code implemented and tested:

- Local random device identity.
- Trusted-device persistence.
- Shared-secret hashing.
- 6-digit one-time pairing codes.
- Guest sessions.
- Trusted sessions.
- Session token hashing.
- Session TTL and idle timeout checks.
- Permission records for mouse, keyboard, clipboard read, clipboard write, and file receive.
- Session listing and revocation APIs.
- Trusted-device removal.
- Trusted-device permission updates.
- Trust review state after emergency lockout.

Experimental agent features:

- macOS receiver accepts mouse movement, button, scroll, and ping messages over WebSocket.
- Sender test tool can wiggle, click, right-click, or scroll against a receiver.

## 4. Security Protections Present

Present in the working MVP:

- No cloud service, accounts, or telemetry in the codebase.
- HTTP control APIs require `X-Pairing-Token`.
- `/ws/touchpad` requires either owner-token auth as the first message, a valid session credential as the first message, or legacy query-token auth.
- The pairing token is generated locally and saved in `config/pairing_token.txt`.
- Startup logs do not include the token; the console does print it for local use.
- Project run helpers disable Uvicorn access logs by default to avoid recording request paths or WebSocket URLs.
- CORS allow-list is empty by default.
- Macros come only from `config/macros.json`.
- Macro commands are arrays and run with `shell=False`.
- Upload filenames are path-stripped, sanitized, and made unique.
- Uploads have a default 50 MB size limit.
- Emergency lockout is checked before input, clipboard, macro, and upload actions.
- `pyautogui.FAILSAFE` is enabled.

Present in pairing/trust code:

- Device identity is random and local.
- Trusted-device shared secrets are hashed before storage.
- Session tokens are hashed in memory.
- Public device/session responses omit secret hashes and session tokens.
- Pairing codes are single-use and expire.
- Failed pairing-code attempts are rate-limited.
- Permission updates ignore unknown permission names.
- Removing a trusted device revokes its sessions.
- Changing permissions revokes that device's sessions.
- Emergency lockout revokes active sessions and can mark trusted devices for review.

Known security gaps or unclear areas:

- Browser control still depends on possession of the global token.
- Legacy WebSocket query-token compatibility still exists in the backend, but the browser UI now sends the token as the first WebSocket message.
- No TLS or local certificate support is implemented.
- No network discovery security model is implemented.
- The UI does not yet show the upload size limit before file selection.
- Trusted-device/session authorization is enforced for session-authenticated touchpad mouse actions, text, clipboard read/write, upload, and macros.
- The macOS receiver uses a temporary token but has no persistent trust model.

## 5. Platform-Specific Code

Windows-oriented code:

- `run.ps1` is a Windows PowerShell helper.
- `config/macros.json` contains Windows commands: `notepad.exe`, `rundll32.exe user32.dll,LockWorkStation`, and a Windows Calculator shell target.
- `app/commands.py` uses Windows creation flags when `os.name == "nt"`.
- `app/input_control.py` is not Windows-only by code. It lazily loads `pyautogui` when input actions are used.

macOS-specific or macOS-oriented code:

- `agents/macos_receiver.py` is explicitly an experimental macOS receiver.
- It lazily loads `pyautogui` and may require macOS Accessibility permission.
- `app/device_identity.py` maps `platform.system() == "Darwin"` to `macos`.

Linux/SteamOS-specific code:

- No native Linux or SteamOS agent code is present.
- Steam Deck support currently means using the browser UI.

## 6. Incomplete Or Experimental Files

Clearly experimental:

- `agents/macos_receiver.py`
- `agents/send_test_input.py`
- `docs/AGENTS.md`
- `docs/PROTOCOL.md`
- `docs/DESIGN.md`

Implemented but not fully integrated into the active browser-control path:

- `app/pairing.py`
- Pairing/trusted-device/session routes in `app/main.py`
- Tests for pairing/session/trusted-device behavior

Unclear or incomplete:

- The protocol docs include future native-agent message types that are not fully implemented as WebSocket protocol actions.
- The roadmap describes future native agents and edge handoff, but those are not implemented.
- Cross-platform server startup exists through `run.py`, but desktop input behavior still depends on `pyautogui` support and OS permissions.

## 7. Next Safest Development Step

The next safest step is to finish Phase 3 by deciding how macros should relate to the session permission model.

A small, testable sequence:

1. Keep the existing pairing token as local-owner/admin access.
2. Decide whether macros should remain owner-token-only or get a separate explicit permission.
3. Keep uploads bounded and owner-visible.
4. Keep updating `last_active_at` only after accepted permissioned actions.
