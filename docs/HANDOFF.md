# LLM Handoff Notes

Use this file when moving between GPT/Codex, Ollama, another local LLM, or a plain terminal workflow. Keep it short and update it after meaningful project changes.

## First Context To Read

Read these files in this order:

1. `AGENTS.md`
2. `docs/current_state.md`
3. `docs/ROADMAP.md`
4. `docs/UI_HANDOFFNEW.md` (native app UI baseline — the forward-looking design spec)
5. `docs/SECURITY.md`
6. `docs/PROTOCOL.md`
7. `docs/LINUX_STEAMOS.md`
8. `docs/EDGE_HANDOFF.md`

Then inspect the code rather than relying on chat history.

## Current Working Summary

Screen Slickshift is a local-only LAN input sharing project.

Current working shape:

- FastAPI server in `app/`.
- Browser UI in `static/`.
- The current development target is Mac-Windows remote mouse first, with both machines able to run the main app as a host/receiver.
- Windows verification is now expected to happen on a Windows machine with Python available.
- macOS can run backend tests and server startup from `.venv`.
- Experimental macOS receiver exists in `agents/macos_receiver.py`.
- macOS receiver event dispatch is covered by helper-level tests that avoid real OS input.
- macOS receiver exposes `/api/permissions` with non-secret permission guidance: Accessibility required for mouse control, Screen Recording not implemented, and emergency stop paths.
- macOS receiver `/ws/input` accepts owner-token auth, compatibility query-token auth, and `session_auth` with `mouse` permission.
- Pairing/trusted-device/session code exists and is tested.
- Browser UI now surfaces local device identity, trusted devices, active sessions, pairing-code generation, trust review, permission toggles, and session revocation.
- Browser UI still uses the global pairing token as the local-owner/admin path.
- Session-authenticated touchpad WebSocket input exists for guest/trusted clients and enforces `mouse` permission.
- Session-authenticated text and clipboard routes exist and enforce `keyboard`, `clipboard_read`, and `clipboard_write`.
- Session-authenticated upload exists and enforces `file_receive`.
- Session-authenticated macro execution exists and enforces `macros`; macros are still local allow-list entries only.
- Uploads have a default 50 MB size limit.
- LLM integration added: Local LLM support via OpenAI-compatible API (LM Studio). Configurable endpoint, centralized in `app/llm.py`. API route `/api/generate-text` for text generation.
- Browser WebSocket auth now sends the token as the first message instead of putting it in the URL. Project run helpers disable Uvicorn access logs.
- Phase 8 Linux/SteamOS research is documented in `docs/LINUX_STEAMOS.md`; no native Linux agent exists yet.
- Phase 9 edge-handoff planning is documented in `docs/EDGE_HANDOFF.md`; pure config/state and monitor layout geometry code exists in `app/handoff.py`, and `/handoff` provides a browser simulation page.
- The first app-integrated remote mouse bridge exists in `app/handoff_remote.py` and `/api/handoff/remote/*`. It connects to another running Screen Slickshift receiver and sends only mouse/ping protocol events.
- `/handoff` now has remote host/IP, port, token fields, and a manual remote touchpad. This is the current Mac-Windows test path.
- `/api/input/status` reports local receiver input state and can check whether the desktop input backend loads. Remote handoff start uses it when the target supports it.
- OS-level edge detector exists in `app/edge_detector.py` (60 Hz polling, dwell timer, armed/pending states).
- `/handoff` arms the detector on Arm button press, polls state at 100 ms, auto-fires goActive when pending.
- Return edge detection: sender arms receiver's detector for the opposite edge; polls receiver state every 200 ms; auto-returns on dwell.
- Dwell progress bar in the State panel fills green over 400 ms while cursor is in the edge zone.
- Layout tiles auto-update with real screen dimensions on connect; tile sizes normalized to ≤20% size difference.
- Drag-through behavior: tiles move freely during drag; on release, cursor position over blocker determines snap side.
- Mac-to-Mac remote mouse physically verified (in addition to Mac-Windows and Windows-Mac).
- v0.0004 adds temporary mouse diagnostics on the Devices page: local nudge, connected-remote nudge, and pywebview capture status.
- pywebview cursor capture now starts directly from a screen-center anchor and reports capture stats (`move_events`, `warp_count`, pending events) instead of first warping to the pywebview window center.
- v0.0005 adds a safe live Logs view backed by `/api/activity/mouse`; each machine can see sent/received mouse counters and recent connection/control events without recording secrets, clipboard contents, or keystroke text.
- v0.0006 disables PyAutoGUI's corner fail-safe for the receiver input backend because normal KVM movement can hit screen corners; Screen Slickshift's emergency lockout remains the supported stop path.
- Keyboard forwarding is active on both the `/handoff` active_remote overlay and the `/` main page touchpad session. Global OS-level keyboard capture does not exist.
- No global input capture, native packaging, or TLS exists yet.
- Native desktop app UI is designed but not yet built. The spec lives in `docs/UI_HANDOFFNEW.md`. Reference prototype files (React/JSX + CSS) are in `docs/UIFILES/`. The browser MVP in `static/` remains the working product.

Do not assume native cross-platform agents, edge handoff, TLS, discovery, screen capture, or trusted-device UI exist yet.

## Local Python Commands

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
python run.py --host 127.0.0.1 --port 8765
```

Windows:

```powershell
.\run.ps1
```

Windows local-only:

```powershell
.\run.ps1 -HostAddress 127.0.0.1 -Port 8765
```

Known local environment note from 2026-05-06:

- On this Mac, system Python is `/usr/bin/python3` at Python 3.9.6.
- Plain `python` is available after activating `.venv`.
- `requirements.txt` includes macOS Python 3.9 `pyobjc` pins so `pyautogui` dependencies install without building an incompatible yanked version.
- LM Studio running locally at http://localhost:1234/v1 with Qwen coding model.
- The owner/admin token is intentionally 6 digits for current prototype testing. Final pairing should use the short code only to establish hidden shared secrets and temporary session credentials.

## Verification Commands

Use:

```bash
source .venv/bin/activate
python -m pytest
```

Expected as of the edge detector integration:

```text
341 passed, 2 warnings
```

Focused remote handoff check:

```bash
source .venv/bin/activate
python -m pytest tests/test_handoff_remote.py tests/test_api_pairing_status.py -q
node --check static/handoff.js
```

Expected:

```text
97 passed, 2 warnings
```

The two warnings are FastAPI `on_event` deprecation warnings. They are known and intentionally deferred.

Phase 1 status:

- Done for macOS-first development.
- Windows verification deferred.
- Phase 2 Security panel implementation is complete for the current browser UI.
- Phase 3 session-scoped permissions enforcement is complete.
- Phase 9 remote mouse bridge has started. Mac-to-Windows and Windows-to-Mac remote mouse have been physically verified. Mac-to-Mac still needs physical verification.

For a syntax/import sanity check on macOS sandboxed environments:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/screen_slickshift_pycache python -m compileall app agents run.py
```

## Security Phase Status

The security phase was close to done but not fully integrated.

Implemented and tested:

- Local device identity.
- Trusted-device storage.
- Pairing codes.
- Guest/trusted sessions.
- Session token hashing.
- Permission records.
- Session revocation.
- Trust review state after emergency lockout.

Still not fully integrated:

- Nearby trusted-device send/broadcast flow.
- TLS/local certificates.
- Removing old WebSocket query-token compatibility.

Safest next security step:

1. Keep the global pairing token as local-owner/admin access for the current prototype.
2. Continue Phase 5 protocol tightening.
3. Design the nearby trusted-device send/broadcast flow.
4. Preserve local-owner token routes while session clients mature.

## Hard Rules For Any Assistant

- Do not add cloud services.
- Do not add accounts or telemetry.
- Do not add arbitrary remote shell commands.
- Do not add screen capture, screenshots, OCR, previews, or remote desktop video unless explicitly requested later.
- Do not log pairing tokens, pairing codes, shared secrets, session tokens, clipboard contents, full keystrokes, or file contents.
- Preserve the Windows browser-control MVP.
- Keep imports and tests OS-portable.
- Use `apply_patch` for manual edits when available.

## Periodic Update Checklist

Update this file after:

- A security milestone changes.
- The run/test commands change.
- A platform support assumption changes.
- A new major feature is actually implemented.
- Tests gain or lose expected warnings.
- A local environment workaround becomes obsolete.

Also update:

- `README.md` for user-facing setup and feature status.
- `AGENTS.md` for assistant/project constraints.
- `docs/current_state.md` for implemented reality.
- `docs/ROADMAP.md` for next work.
