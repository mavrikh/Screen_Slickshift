# LLM Handoff Notes

Use this file when moving between GPT/Codex, Ollama, another local LLM, or a plain terminal workflow. Keep it short and update it after meaningful project changes.

## First Context To Read

Read these files in this order:

1. `AGENTS.md`
2. `docs/current_state.md`
3. `docs/roadmap.md`
4. `docs/SECURITY.md`
5. `docs/PROTOCOL.md`

Then inspect the code rather than relying on chat history.

## Current Working Summary

Screen Slickshift is a local-only LAN input sharing project.

Current working shape:

- FastAPI server in `app/`.
- Browser UI in `static/`.
- The current development target is mac-to-mac first.
- Windows remains the most complete legacy browser-control target, but Windows verification is deferred until a Windows machine with Python is available.
- macOS can run backend tests and server startup from `.venv`.
- Experimental macOS receiver exists in `agents/macos_receiver.py`.
- Pairing/trusted-device/session code exists and is tested.
- Browser UI now surfaces local device identity, trusted devices, active sessions, pairing-code generation, trust review, permission toggles, and session revocation.
- Browser UI still uses the global pairing token as the local-owner/admin path.
- Session-authenticated touchpad WebSocket input exists for guest/trusted clients and enforces `mouse` permission.
- Session-authenticated text and clipboard routes exist and enforce `keyboard`, `clipboard_read`, and `clipboard_write`.
- Session-authenticated upload exists and enforces `file_receive`.
- Uploads have a default 50 MB size limit.
- LLM integration added: Local LLM support via OpenAI-compatible API (LM Studio). Configurable endpoint, centralized in `app/llm.py`. API route `/api/generate-text` for text generation.
- Browser WebSocket auth now sends the token as the first message instead of putting it in the URL. Project run helpers disable Uvicorn access logs.

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

## Verification Commands

Use:

```bash
source .venv/bin/activate
python -m pytest
```

Expected as of 2026-05-06:

```text
109 passed, 2 warnings
```

The two warnings are FastAPI `on_event` deprecation warnings. They are known and intentionally deferred.

Phase 1 status:

- Done for macOS-first development.
- Windows verification deferred.
- Phase 2 Security panel implementation is complete for the current browser UI.
- Phase 3 session-scoped permissions enforcement is complete.

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

- Session-scoped authorization for macros.
- UI display/preflight for the upload size limit.
- TLS/local certificates.
- Removing old WebSocket query-token compatibility.

Safest next security step:

1. Keep the global pairing token as local-owner/admin access.
2. Decide whether macros stay owner-token-only or require a new explicit permission.
3. Decide whether the UI should show the upload limit before file selection.
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
- `docs/roadmap.md` for next work.
