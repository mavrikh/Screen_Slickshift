# Current State

Last reviewed from repository contents on 2026-05-08.

This document describes what is present in the codebase. It does not claim features that are only future intent.

For switching between GPT/Codex, Ollama, another local LLM, or a plain terminal workflow, use `docs/HANDOFF.md` as the quick continuity file.

Phase 1 stabilization is complete for the current baseline. Phase 9 has a bidirectional Mac-Windows remote-mouse test path with both machines running the same Python/FastAPI app.

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
- `app/handoff_remote.py` contains the first app-integrated outbound remote-mouse bridge for connecting one running Screen Slickshift app to another over `/ws/touchpad` or `/ws/input`.

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
419 passed, 2 warnings
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

Manual sender prototype:

```bash
python -m agents.manual_sender --host MAC_IP --port 8770 --token TOKEN
```

This tool prints an internal-testing notice when it starts. It is not product UX
and does not perform global input capture.

Manual sender against the main app server:

```bash
python -m agents.manual_sender --host SERVER_IP --port 8765 --token TOKEN --path /ws/touchpad --auth-mode first-message
```

The manual sender checks the receiver's `/api/status` endpoint before opening
the WebSocket unless `--skip-status-check` is used. It stops before connecting
when receiver status reports emergency-disabled unless `--allow-disabled-receiver`
is used for diagnostics. If a receiver advertises protocol events but omits
required mouse/ping events, the sender also stops before connecting.
Repeated `--command` arguments can run non-interactive manual tests. Scripted
tests can include `wait SECONDS` and `--command-delay`. The sender includes a
`help` command for its command list and smoke-test presets for wiggle, clicks,
and scroll.

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
- File upload to a configurable receive folder that defaults to the user's Downloads folder.
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
- Upload panel displays the configured upload limit and blocks oversized files before upload.
- Upload panel displays and can update the receive folder path.
- Upload panel includes inline status for upload progress, success, and rejection/error messages.
- Upload panel shows recent received and rejected transfers from the current server run.
- Upload panel can clear recent transfer records for the current server run.
- Dedicated `/file-transfer` browser page for focused file-transfer use.
- File-transfer page shows trusted recipient readiness with explicit blocked reasons.
- File-transfer page can update trusted-device `file_receive` permission.
- Activity log in the browser UI.
- Dedicated `/handoff` browser page for prototype monitor layout and remote mouse testing.
- Handoff layout page supports draggable monitor tiles, no-overlap placement, nearest-side snapping, Freeform mode, monitor edge-disable toggles, multiple monitors per device, up to five simulated devices, zoom, pan, and Reset View fit-to-layout.
- Handoff layout page can connect to a remote Screen Slickshift receiver by host/IP, port, and remote token, then send mouse movement, clicks, and scroll through a manual remote touchpad.
- Owner-token-protected remote handoff APIs: `/api/handoff/remote/status`, `/api/handoff/remote/start`, `/api/handoff/remote/event`, and `/api/handoff/remote/stop`.
- Owner-token-protected input status API: `/api/input/status`, with optional backend import check for receiver diagnostics.
- Go Active button on `/handoff` page that enters active_remote mode directly without the simulation arm/confirm steps.
- Active handoff layer auto-stops and exits the overlay when the remote connection is lost during active_remote mode.
- OS-level edge detector in `app/edge_detector.py`: polls `pyautogui.position()` at ~60 Hz when armed, fires after cursor dwells at the configured edge for 400 ms (configurable), transitions idle → armed → pending.
- Owner-token-protected edge detector APIs: `POST /api/handoff/arm`, `POST /api/handoff/disarm`, `GET /api/handoff/detector/state`.
- Owner-token-protected screen info API: `GET /api/screen/info` returns primary screen dimensions and current cursor position.
- `/handoff` Arm button and route Arm buttons start real cursor polling via the Python backend.
- `/handoff` polls detector state every 200 ms while armed; when pending fires and remote is connected, goes active automatically.
- `/handoff` shows real local screen dimensions in the State panel after connecting.
- Return edge: when active_remote mode starts, the sender arms the receiver's edge detector for the return edge (opposite of the exit edge, via `/api/handoff/remote/arm-return`). The sender polls `/api/handoff/remote/return-state` every 200ms. When the receiver's cursor dwells at the return edge for 400ms, the sender automatically stops and returns to local control. Stop and Escape also remain available.
- Layout tiles are automatically updated with real screen dimensions after connecting: local tile from `/api/screen/info`, target tile from `/api/handoff/remote/screen-info` via the remote bridge.
- Rotating server logs.
- LLM text generation via `/api/generate-text` route, using local OpenAI-compatible API (LM Studio).
- mDNS device discovery via `app/discovery.py` and `zeroconf`: `DiscoveryService` advertises and browses `_slickshift._tcp.local.` services, resolves device_id and screen host/port from TXT records.
- Discovery pairing flow: unauthenticated `POST /api/discovery/request-pair` creates a time-limited (45s) pending pair request with a server-generated 6-digit code; the local owner reads the code from `GET /api/discovery/pending-request` (owner-token required). The remote device submits the code via `POST /api/discovery/pair` to obtain a session token and optional shared secret for trusted reconnects.
- Trusted reconnect: `POST /api/pairing/trusted-reconnect` accepts device_id + shared_secret, issues a fresh session without a PIN.
- CORS proxy routes: `POST /api/discovery/remote/request-pair`, `/remote/pair`, `/remote/reconnect` forward discovery API calls from A's browser through A's server to B's server, injecting A's device identity automatically.
- Session-based remote bridge: `RemoteHandoffBridge.start_with_session()` connects to B's WebSocket using session credentials (`session_auth` message) instead of the owner token. Bridge stores both `_token` (owner) and `_session_id`/`_session_token` (session) and selects the available auth path automatically for all remote operations.
- Session-authenticated remote operations on B: `POST /api/session/screen/info`, `/api/session/handoff/arm` (requires `mouse` permission), `/api/session/handoff/disarm`, `/api/session/handoff/detector/state` — all accept session credentials in the POST body, enabling return detection and screen-info fetching for discovery-paired connections.
- Multi-monitor edge detection: `get_monitors()` in `app/edge_detector.py` enumerates all connected displays using `NSScreen` on macOS (with correct top-down coordinate conversion) and `EnumDisplayMonitors` on Windows. `DetectorConfig` stores the target monitor's full rect at arm time. `cursor_at_edge()` enforces a screen-bounds check before testing edges, preventing false positives when a cursor on a secondary monitor exceeds a primary screen's pixel range. `GET /api/screen/monitors` exposes the display list to the browser. `POST /api/handoff/arm` and `/api/session/handoff/arm` accept `screen_index` to target a specific monitor.
- `/handoff` fetches all local monitors on connect, tags layout tiles with `monitor_index`, passes `screen_index` when arming, and shows monitor names as tile subtitles when multiple displays are present. `Add Monitor` uses real monitor data to add unrepresented displays with correct dimensions.
- `/handoff` control panel reorganized into three tabs (State / Remote / Discovery). Discovery tab shows a badge when an incoming pair request is pending; Remote tab auto-activates after a discovery connection. Pending pair PIN modal has a real 1-second countdown independent of the discovery poll interval. Discovery list shows "Searching…" for 4 seconds after advertising starts.
- Layout tiles show real screen dimensions normalized so the largest display is at most 20% bigger than the smallest. Layout is persisted to `localStorage` and restored on page reload.
- `[hidden]` CSS guarantee: `[hidden] { display: none !important }` prevents author `display` values from overriding the HTML `hidden` attribute.

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
- The owner token is currently a 6-digit prototype/testing convenience. Final trust should use short human-entered pairing only to establish hidden shared secrets and temporary session credentials.
- `/ws/touchpad` requires either owner-token auth as the first message, a valid session credential as the first message, or legacy query-token auth.
- The pairing token is generated locally and saved in `config/pairing_token.txt`.
- Startup logs do not include the token; the console does print it for local use.
- Project run helpers disable Uvicorn access logs by default to avoid recording request paths or WebSocket URLs.
- CORS allow-list is empty by default.
- Macros come only from `config/macros.json`.
- Macro commands are arrays and run with `shell=False`.
- Upload filenames are path-stripped, sanitized, and made unique.
- Uploads have a default 50 MB size limit.
- Receive folder changes require the owner token and are stored locally in `config/receive_dir.txt`.
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
- The discovery pairing flow is PIN-based (6-digit code displayed on the receiver's screen). No additional security beyond possession of the code and being on the local network.
- Trusted-device shared secrets are stored in the browser's `localStorage` — suitable for LAN prototype, not for a hardened product.
- Trusted-device/session authorization is enforced for session-authenticated touchpad mouse actions, text, clipboard read/write, upload, macros, handoff arm/disarm, and screen info.
- The macOS receiver uses a temporary token but has no persistent trust model.
- Remote handoff start is owner-token protected, preflights the target `/api/status`, refuses emergency-disabled receivers, and only sends mouse/ping protocol events.
- Remote handoff start validates the target token through `/api/auth/check` before opening the remote WebSocket.
- Remote handoff start checks target `/api/input/status?check_backend=true` when available and refuses targets that report input is blocked.
- Remote handoff target tokens are not logged by the app and are not returned from API responses.
- Session-based remote connections (discovery/trusted-reconnect path) skip the input-status pre-check; per-event mouse authorization is enforced server-side instead.

## 5. Platform-Specific Code

Windows-oriented code:

- `run.ps1` is a Windows PowerShell helper.
- `config/macros.json` contains Windows commands: `notepad.exe`, `rundll32.exe user32.dll,LockWorkStation`, and a Windows Calculator shell target.
- `app/commands.py` uses Windows creation flags when `os.name == "nt"`.
- `app/input_control.py` is not Windows-only by code. It lazily loads `pyautogui` when input actions are used.
- The main app's `/ws/touchpad` receiver path runs on Windows as the remote mouse target when Python dependencies are installed and the app is running in a desktop session. Mac-to-Windows remote mouse has been physically verified.

macOS-specific or macOS-oriented code:

- `agents/macos_receiver.py` is explicitly an experimental macOS receiver.
- It lazily loads `pyautogui` and may require macOS Accessibility permission.
- Its startup banner says Accessibility may be required, Screen Recording is not needed, and emergency stop paths are Ctrl+C plus pyautogui screen-corner failsafe.
- Receiver status/index payloads and event-dispatch behavior have focused tests.
- Receiver `/api/permissions` reports Accessibility as required for mouse control, Screen Recording as not needed/not implemented, and the available emergency stop paths.
- Receiver status/index/lockout payloads report `input_allowed` alongside `disabled`.
- Receiver status/index payloads report prototype capabilities for mouse input, no keyboard, no clipboard, no file transfer, no screen capture, Accessibility required, and Screen Recording not required.
- Receiver `/ws/input` accepts first-message token auth and still accepts query-token auth for compatibility.
- Receiver `/ws/input` accepts `session_auth` with `mouse` permission and updates session activity only after accepted mouse input.
- Receiver `/api/lockout` accepts `X-Pairing-Token` and still accepts query-token auth for compatibility.
- Receiver status/index payloads advertise supported WebSocket and HTTP auth modes without exposing the token.
- The main app's `/ws/touchpad` receiver path runs on macOS as the remote mouse target when Accessibility permission is granted. Mac-to-Windows, Windows-to-Mac, and Mac-to-Mac remote mouse have all been physically verified.
- Receiver emergency-disabled state blocks mouse/click/scroll while allowing ping.
- `app/device_identity.py` maps `platform.system() == "Darwin"` to `macos`.

Linux/SteamOS-specific code:

- No native Linux or SteamOS agent code is present.
- Steam Deck support currently means using the browser UI.
- Phase 8 research is documented in `docs/LINUX_STEAMOS.md`.
- Future Linux receiver work should test XDG Desktop Portal RemoteDesktop first.
- `uinput`/libevdev is documented as an explicit advanced fallback, not the default.
- XTEST is documented as an X11-only compatibility fallback.

Edge handoff:

- Phase 9 planning is documented in `docs/EDGE_HANDOFF.md`.
- Pure edge handoff config/state code exists in `app/handoff.py`.
- Pure monitor layout geometry exists in `app/handoff.py` for draggable screen rectangles and adjacent-edge route derivation.
- Prototype browser handoff page exists at `/handoff` for drag-layout and state simulation.
- Owner-token-protected `POST /api/handoff/layout/preview` returns sanitized screens and derived routes.
- `/handoff` supports zoom controls, mouse-wheel zoom, fit-to-all Reset View, and panning by dragging empty layout space.
- `/handoff` Add Machine/Add Monitor place new tiles in a priority order (right → left → top → bottom of the reference screen) and never create overlapping tiles.
- `/handoff` drag tiles freely through each other; on release the cursor position over the blocker determines which side to snap to for horizontal overlaps, and vertical overlaps always snap back.
- Layout tiles are automatically updated with real screen dimensions on connect: local tile from `/api/screen/info`, remote tile from `/api/handoff/remote/screen-info`. Tile sizes are normalized so the largest screen is at most 20% bigger than the smallest.
- OS-level edge detector in `app/edge_detector.py` polls `pyautogui.position()` at ~60 Hz when armed. Transitions idle → armed → pending after cursor dwells at the configured edge for 400 ms.
- `/api/handoff/arm`, `/api/handoff/disarm`, `/api/handoff/detector/state`, and `/api/screen/info` are owner-token-protected endpoints that control and query the detector.
- `/handoff` Arm button and route Arm buttons start real cursor polling. The browser polls every 100 ms; when the detector reaches pending and remote is connected, `goActive()` fires automatically.
- A green dwell progress bar appears in the State panel while the cursor is in the edge zone, filling over 400 ms.
- Active remote mode opens a full-window overlay with pointer lock capture, movement forwarding, scroll, click buttons, and a Stop button. Escape also stops.
- Go Active button skips the simulation steps and enters active_remote directly when remote is connected.
- Active layer auto-stops if the remote connection is lost.
- Return edge: when going active, the sender arms the receiver's edge detector for the return edge (opposite of exit edge). The sender polls the receiver's detector state every 200 ms; when the receiver's cursor dwells at its return edge, the sender returns to local control automatically.
- Mac-to-Windows, Windows-to-Mac, and Mac-to-Mac remote mouse have all been physically verified.
- Devices page includes temporary mouse diagnostics for isolating the current mouse-control failures: move this cursor, move the connected remote cursor, and show pywebview capture status.
- In pywebview mode, active control starts the Python cursor capture loop directly at a screen-center anchor. The capture loop exposes diagnostic stats including move event count, warp count, pending events, anchor, and last error.
- Logs tab now polls `/api/activity/mouse` every second and shows safe in-memory sent/received counters plus recent connection/control events for this server run. It does not record tokens, shared secrets, clipboard contents, file contents, or keystroke text.
- No global input capture code exists (cursor position reading does not require Accessibility permission on macOS).
- Multi-monitor edge detection uses the primary screen only; multi-monitor support is deferred.
- Keyboard forwarding is implemented on both control surfaces: the `/handoff` active_remote overlay and the `/` main page touchpad session. Keystrokes are sent as `{type: "keyboard", key, ctrl, alt, shift, meta}` events over the existing WebSocket. Escape and modifier-only keys are not forwarded. Global OS-level keyboard capture remains out of scope.

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

- The protocol docs include future native-agent message types that are not fully implemented as WebSocket protocol actions. The implemented WebSocket subset is protocol v1 mouse input, ping, session auth, and legacy `move`/`click` aliases. Main and experimental receiver status responses advertise the implemented protocol subset and per-message numeric limits.
- The roadmap describes future native agents and edge handoff, but those are not implemented.
- Cross-platform server startup exists through `run.py`, but desktop input behavior still depends on `pyautogui` support and OS permissions.

## 7. Native App UI Design Artifacts

The browser MVP (`static/`) is the current working product. A native desktop app UI has been designed as the forward-looking replacement. The design is not yet implemented in the main app.

Design spec:
- **`docs/UI_HANDOFFNEW.md`** — the baseline implementation brief for the native settings window. Covers layout, component hierarchy, palette (dark vaporwave — deep indigo + cyan/violet accent), typography, spacing tokens, interaction flows, accessibility, and a full React component file breakdown. This is the authoritative design reference for all future native UI work.

Reference prototype files (all in `docs/UIFILES/`):
- `Screen Slickshift Settings.html` — self-contained React+JSX prototype rendered in a browser. Single window, dark vaporwave palette. Use this to visually verify the design intent.
- `styles.css` — complete CSS token set and component styles matching §11–12 of UI_HANDOFFNEW.md.
- `app.jsx` — root app shell, sidebar, routing, lockout banner.
- `devices.jsx` — Devices screen, DeviceRow, PermPill, PairModal state machine.
- `icons.jsx` — shared SVG icon set.
- `other-sections.jsx` — Overview, Settings, Security, and Logs screens.
- `tweaks-panel.jsx` — accent palette swap and density toggle panel.

Key design decisions captured in UI_HANDOFFNEW.md:
- Single fixed-aspect window (~1180×760) with sidebar navigation — no tabs or breadcrumbs.
- Emergency Stop always visible in the sidebar foot, reachable from any section.
- Pair modal is a 5-step state machine: method → enter/show code → connecting → success.
- Browser MVP (`/`, `/handoff`, `/file-transfer`) continues as the Steam Deck control surface — treated as a separate skin, not a parallel implementation.
- `docs/DESIGN.md` covers the existing browser MVP design language (green accent, system fonts, vanilla JS). For new native app work, follow UI_HANDOFFNEW.md instead.

## 8. Session Summary — 2026-05-09

This session built the live React UI (`static/app-ui/`) from the earlier prototype, wired it to the real APIs, and added several fixes and the edge-handoff feature.

### What was built / changed

| Item | File(s) | Notes |
|---|---|---|
| New React UI live at `/` | `static/app-ui/` | Old green UI preserved at `/classic` |
| Discovery toggles split | `app/discovery.py`, `app/main.py`, `devices.jsx` | Search (browse) and Allow connections (advertise) are now independent; new endpoints: `POST /api/discovery/browse/start`, `browse/stop`, `advertise/stop` |
| Refresh button removed | `devices.jsx` | Pointless — list already auto-polls |
| Saved-device reconnect fix | `devices.jsx` | `submitPin` now uses `remember_device:true` and stores `{shared_secret, host, port}` in `localStorage`; `handleConnect` uses stored host/port as fallback when device not visible in mDNS |
| "This Device" removed from Overview trusted list | `devices.jsx` | Was appearing as a spurious row in `OverviewSection` |
| Edge handoff wired in Overview | `devices.jsx` | Drag tiles to snap → toggle "Edge handoff" → cursor dwell at edge auto-connects; return edge on Screen B auto-disconnects |
| `OverviewEdges` shows all paired devices | `devices.jsx` | Previously only showed `status === "connected"` devices |

### Needs testing (Windows + Mac)

1. **Split toggles** — Turn on "Search" without "Allow connections": this machine should see others but not be visible. Turn on "Allow connections" without "Search": this machine should be visible but the list should stay empty. Verify the toggle states persist through the poll cycle.
2. **Saved-device reconnect** — Pair two devices. Close the app on one and reopen. On the other, click Connect without the first machine being in the mDNS discovered list — it should reconnect using stored host/port.
3. **Edge handoff** — In Overview, drag the remote tile to snap its left edge against the local tile's right edge. Toggle "Edge handoff" on. Move cursor to the right edge of the local screen and hold — expect control to transfer automatically. On the remote, push cursor back to its left edge and hold — expect return.
4. **PIN pairing end-to-end** — Fresh pair on Windows: Screen A clicks Connect on a discovered device, Screen B shows the code, Screen A enters it. Verify both sides end up in each other's trusted list and active control starts immediately.
5. **Allow connections toggle** — Turning "Allow connections" off while "Search" is on should keep the discovered list populated (browsing continues) but remove this machine from other devices' lists.

### Remaining gaps before packaging

1. **Settings persistence** — Mouse speed, scroll speed, natural scroll, keyboard toggle, pointer mode are all UI-local `useState`. Closing the Settings tab loses them. Fix: write a JSON blob to `localStorage` on change and read it on mount. No backend work needed.
2. **Logs section** — Currently shows a placeholder pointing to `/classic`. The backend writes rotating logs to `LOG_DIR/server.log`. A simple `GET /api/logs/tail` endpoint returning the last N lines would feed a live view. Or an SSE stream.
3. **Clipboard sync** — Settings panel has toggles but nothing behind them. Backend has `GET/POST /api/clipboard` and session variants. Needs a polling or push loop to sync between devices.
4. **Windows mDNS** — Not yet verified. If devices don't appear: `netsh advfirewall firewall add rule name="Slickshift mDNS" protocol=UDP dir=in localport=5353 action=allow`.
5. **Multi-monitor** — Edge detector has per-monitor support but not tested on multi-monitor hardware.

## 9. What Is Next (Path to Executables + Chrome Extension)

Priority order for completing the app before packaging:

1. **Settings persistence** (1–2h, frontend only) — localStorage JSON blob; no backend needed.
2. **Clipboard sync wiring** (2–3h) — Poll remote clipboard on a configurable interval; sync both directions.
3. **Logs tab live feed** (1–2h) — Add `GET /api/logs/tail?n=200` endpoint; auto-scroll log view in the UI.
4. **Chrome extension — browser handoff window** (separate effort, see Phase 11 in ROADMAP.md) — The extension sits in Chrome's sidebar or a popup and acts as a lightweight control surface: connects to the local Slickshift server via `localhost:8765`, shows discovered devices, and lets the user switch which machine gets keyboard/mouse from the browser. Key capabilities needed: `chrome.sidePanel` or popup UI, WebSocket to local server, pointer-lock-free mouse event forwarding via the existing `/api/handoff/remote/event` HTTP endpoint, keyboard forwarding.
5. **Native executable packaging** (Phase 10) — PyInstaller single-file bundle is the fastest path: `pyinstaller --onefile --add-data "static:static" run.py`. Target: double-click to start, browser opens automatically, no terminal visible. Mac `.app` and Windows `.exe`. Tauri or Electron deferred unless the Python bundle proves too large or slow.
