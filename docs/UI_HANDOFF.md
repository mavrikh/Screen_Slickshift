# Screen Slickshift — UI Build Handoff

This document gives a self-contained brief for building or extending the browser UI. No other files are needed to understand the context.

---

## Project at a Glance

Screen Slickshift is a local-LAN input-sharing app. A FastAPI server runs on each machine. The browser UI is **plain HTML + vanilla JS + a single CSS file** — no build step, no framework. The server serves static files and exposes a REST/WebSocket API protected by a pairing token (`X-Pairing-Token` header).

There are three pages:
- `/` — main touchpad/control page (`static/index.html`, `static/app.js`)
- `/file-transfer` — file transfer page (`static/file_transfer.html`)
- `/handoff` — monitor layout, remote mouse, discovery/pairing (`static/handoff.html`, `static/handoff.js`)

---

## File Map

```
static/
  style.css          ← shared styles for all pages
  index.html         ← main touchpad page
  app.js             ← main page JS
  file_transfer.html ← file transfer page (no separate JS)
  handoff.html       ← layout + remote + discovery UI
  handoff.js         ← all handoff page JS (~1800 lines)
```

---

## CSS Design System

### Variables (`:root`)
```css
--bg: #111418          /* page background */
--panel: #1d232a       /* card/panel background */
--panel-strong: #27313b /* raised element inside a panel */
--text: #f4f7fb        /* primary text */
--muted: #a9b4c0       /* secondary text, labels */
--accent: #22c55e      /* primary action (green) */
--accent-strong: #16a34a
--danger: #ef4444      /* destructive actions (red) */
--border: #3a4652      /* all borders */
```

### Layout Classes
```css
.shell                 /* page wrapper, centers content, max-width varies */
.handoff-shell         /* 1220px shell for /handoff */
.topbar                /* flex header row with h1 + actions */
.topbar-actions        /* right side of topbar */
.grid                  /* 2-column card grid (main page) */
.handoff-grid          /* layout canvas + control panel (2-col) */
.panel                 /* card with bg/border/radius/padding */
.panel-header          /* flex row: h2 + action buttons */
.auth-panel            /* token entry card */
```

### Component Classes
```css
/* Buttons */
button                 /* full-width, green, 44px min-height */
button.secondary       /* panel-strong bg, bordered */
button.danger          /* red, auto-width */
button.compact-button  /* auto-width, 34px height, 0.86rem */
button.text-danger     /* transparent bg, red text, bordered */

/* Forms */
input, select          /* dark bg, full-width, 12px padding */
label                  /* muted color */

/* Rows */
.click-row             /* flex row of buttons (gap 10px) */
.button-pair           /* flex row of compact buttons */
.auth-row              /* flex row: input + button */

/* State display */
.state-list            /* list of key/value rows */
.state-item            /* flex row: span (label) + strong (value) */
.empty-state           /* placeholder text in lists */

/* Status / notices */
.handoff-status-box    /* muted bordered box, min-height 44px */
.pill                  /* inline badge; add .enabled or .warning */

/* Handoff-specific */
.layout-canvas         /* grid-pattern canvas, overflow hidden */
.layout-world          /* absolutely positioned, transformed */
.layout-screen         /* draggable tile; add .local or .target */
.remote-control-block  /* section with border-top separator */
.remote-mouse-pad      /* touchpad area, crosshair cursor */
.dwell-progress-item   /* container for dwell progress bar */
.dwell-progress-track  /* bar track */
.dwell-progress-fill   /* bar fill, animated width */
.active-handoff-layer  /* fixed fullscreen overlay */
.active-handoff-bar    /* floating bar inside the overlay */
.discovery-block       /* discovery section */
.discovery-header      /* flex row: h2 + toggle button */
.discovery-list        /* flex-col list of device cards */
.discovery-device      /* device row: info + action buttons */
.discovery-device-info /* name + host stacked */
.modal-overlay         /* fixed fullscreen dimmed backdrop */
.modal                 /* centered dialog card */
.pair-code-display     /* large monospace PIN display */
.pair-countdown-row    /* flex row: label + countdown */
.permission-row        /* flex row of checkbox labels */
.permission-label      /* flex row: checkbox + label */
.muted-text            /* block, muted color, 0.8rem */
.route-item            /* route row with 3-col grid */
```

### Typography Scale
```
h1: 1.45rem
h2: 1rem (margin-bottom: 12px)
h3: 0.86rem
body: inherit (Segoe UI / system)
button.compact-button: 0.86rem
.remote-stop-hint: 0.86rem
```

---

## HTML Patterns

### Panel with header + content
```html
<div class="panel">
  <div class="panel-header">
    <h2>Section Title</h2>
    <button class="secondary compact-button" type="button">Action</button>
  </div>
  <!-- content -->
</div>
```

### State row
```html
<div class="state-item">
  <span>Label</span>
  <strong id="someTextEl">value</strong>
</div>
```

### Button row
```html
<div class="click-row">
  <button id="primaryBtn" type="button">Primary</button>
  <button id="secondaryBtn" class="secondary" type="button">Secondary</button>
</div>
```

### Modal overlay
```html
<div id="myModal" class="modal-overlay" hidden>
  <div class="modal">
    <h2>Title</h2>
    <p>Body text</p>
    <div class="click-row">
      <button id="confirmBtn" type="button">Confirm</button>
      <button id="cancelBtn" class="secondary" type="button">Cancel</button>
    </div>
  </div>
</div>
```
Show/hide with `element.hidden = true/false`.

### Section separator block
```html
<div class="remote-control-block">
  <h2>Section Title</h2>
  <!-- grid content -->
</div>
```
This class adds `margin-top`, `padding-top`, and a top border separator.

---

## JS Patterns (`handoff.js`)

### State object
All page state lives in `handoffState`. Mutate fields directly, then call a render function.
```javascript
handoffState.remoteConnected = true;
renderState(); // or renderAll()
```

### API calls
```javascript
// GET
const data = await api("/api/some/endpoint");

// POST with JSON
const data = await api("/api/some/endpoint", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ key: value }),
});
```
`api()` automatically adds `X-Pairing-Token` from `handoffState.token`. Throws `Error` on non-2xx (message comes from `data.detail`). Wrap calls in `try/catch`.

### Rendering
- `renderAll()` — rerenders everything (targets, canvas, routes, state panel)
- `renderState()` — rerenders just the state/status items and button disabled states
- `renderCanvas()` — rerenders just the layout tile canvas
- `setNotice(msg)` — updates both the status box and topbar status text

### Auth headers (for reference)
```javascript
function authHeaders() {
  return { "X-Pairing-Token": handoffState.token };
}
```

### localStorage keys in use
```
wdcToken                          ← owner token
slickshiftHandoffRemoteHost
slickshiftHandoffRemotePort
slickshiftHandoffRemoteToken
slickshiftLayout_v1               ← layout JSON (screens, viewport, etc.)
slickshiftLocalDeviceId           ← unused currently
slickshiftTrusted_${device_id}    ← {shared_secret} for trusted reconnect
```

---

## API Reference (UI-Relevant Endpoints)

All endpoints require `X-Pairing-Token: <token>` header unless marked **[no auth]**.

### Auth & Identity
| Method | Path | Response |
|---|---|---|
| GET | `/api/auth/check` | `{ok: true}` |
| GET | `/api/device` | `{device: {device_id, name, platform}}` |
| GET | `/api/status` | `{app, disabled, auth, max_upload_bytes, protocol}` |

### Screen Info
| Method | Path | Notes |
|---|---|---|
| GET | `/api/screen/info` | `{width, height, cursor_x, cursor_y, error}` |
| GET | `/api/screen/monitors` | `{monitors: [{index, x, y, width, height, primary, name}]}` |

### Edge Detector (local)
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/handoff/arm` | `{edge, dwell_ms, zone_px, screen_index}` | edge: left/right/top/bottom |
| POST | `/api/handoff/disarm` | — | |
| GET | `/api/handoff/detector/state` | — | `{state, config, dwell_progress}` state: idle/armed/pending |

### Remote Bridge
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/handoff/remote/start` | `{host, port, token, path}` | owner-token connection |
| POST | `/api/handoff/remote/start-session` | `{host, port, path, session_id, session_token}` | discovery/paired connection |
| POST | `/api/handoff/remote/stop` | — | |
| GET | `/api/handoff/remote/status` | — | `{connected, target}` |
| POST | `/api/handoff/remote/event` | `{type, dx, dy, button, down, amount}` | types: mouse_move, mouse_button, scroll, ping |
| POST | `/api/handoff/remote/arm-return` | `{return_edge, dwell_ms}` | arms edge on remote |
| GET | `/api/handoff/remote/return-state` | — | `{state, dwell_progress}` |
| POST | `/api/handoff/remote/disarm-return` | — | |
| GET | `/api/handoff/remote/screen-info` | — | `{width, height, error}` |

### Layout Preview
| Method | Path | Body |
|---|---|---|
| POST | `/api/handoff/layout/preview` | `{screens: [{screen_id, device_id, name, rect, primary, edge_enabled}], snap_tolerance_px, min_overlap_px}` |

Response: `{routes: [{from_screen_id, to_screen_id, exit_edge, enter_edge, to_device_id, overlap_px}], overlaps: [...]}`

### Discovery
| Method | Path | Body | Auth |
|---|---|---|---|
| POST | `/api/discovery/advertise` | — | token |
| POST | `/api/discovery/stop` | — | token |
| GET | `/api/discovery/browse` | — | token → `{advertising, devices: [{name, device_id, host, port, trusted}]}` |
| POST | `/api/discovery/request-pair` | `{device_id, name}` | **none** |
| GET | `/api/discovery/pending-request` | — | token → `{pending, code, requester_name, seconds_remaining}` |
| POST | `/api/discovery/dismiss-request` | — | token |
| POST | `/api/discovery/pair` | `{code, device_id, name, permissions, remember_device, idle_timeout_seconds}` | **none** |
| POST | `/api/pairing/trusted-reconnect` | `{device_id, shared_secret, idle_timeout_seconds}` | **none** |

### Discovery Proxy (CORS workaround — call A's server to reach B)
| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/discovery/remote/request-pair` | `{host, port}` | server injects local identity |
| POST | `/api/discovery/remote/pair` | `{host, port, code, permissions, remember_device}` | → `{ok, trusted, session_token, session: {session_id}, shared_secret?}` |
| POST | `/api/discovery/remote/reconnect` | `{host, port, shared_secret}` | → `{ok, session_token, session: {session_id}}` |

### Session-Authenticated Remote Ops (B's server, called via proxy)
| Method | Path | Body |
|---|---|---|
| POST | `/api/session/screen/info` | `{session_id, session_token}` |
| POST | `/api/session/handoff/arm` | `{session_id, session_token, edge, dwell_ms, zone_px, screen_index}` |
| POST | `/api/session/handoff/disarm` | `{session_id, session_token}` |
| POST | `/api/session/handoff/detector/state` | `{session_id, session_token}` |

### Trusted Devices & Sessions
| Method | Path |
|---|---|
| GET | `/api/trusted-devices` → `{devices: [{device_id, name, permissions, last_seen_at, review_required}]}` |
| DELETE | `/api/trusted-devices/{device_id}` |
| PATCH | `/api/trusted-devices/{device_id}/permissions` body: `{permissions: {mouse, keyboard, ...}}` |
| GET | `/api/pairing-sessions` → `{sessions: [{session_id, device_id, guest, permissions, created_at}]}` |
| DELETE | `/api/pairing-sessions/{session_id}` |
| DELETE | `/api/pairing-sessions` — revoke all |

---

## Current `/handoff` Page Structure

### `handoff.html` Skeleton
```
<body>
  <main class="shell handoff-shell">
    <header class="topbar">               ← title + status text + Stop button
    <section class="auth-panel">          ← token input + Connect button
    <section class="handoff-grid">
      <div class="panel handoff-layout-panel">  ← left: zoom/pan/reset buttons + canvas
      <div class="panel handoff-control-panel"> ← right: control column (see below)
      <div class="panel handoff-routes-panel">  ← full-width bottom: route list
  </main>

  <div id="activeHandoffLayer">           ← fixed fullscreen overlay when active
  <div id="pairModal" class="modal-overlay">    ← initiating-pair modal
  <div id="pendingPairModal" class="modal-overlay"> ← incoming-pair modal
  <script src="/static/handoff.js">
</body>
```

### Control Panel (`.handoff-control-panel`) — current content in order
```
h2 "State"
.state-list
  → Selected Monitor
  → Mode
  → Target
  → Edge
  → [dwell progress bar]
  → Local Screen

label "Target" + <select id="targetSelect">
label "Edge" + <select id="edgeSelect">
.click-row → Arm + Disarm buttons
.click-row → Add Machine + Add Monitor buttons
Toggle Monitor Edges button
.click-row → Simulate Edge + Confirm buttons

.remote-control-block
  h2 "Remote Mouse"
  label + input#remoteHostInput
  label + input#remotePortInput
  label + input#remoteTokenInput
  .click-row → Connect Remote + Disconnect
  .state-item → Remote status
  button#goActiveButton
  div#remoteMousePad (touchpad)
  .remote-stop-hint
  .click-row → Wiggle + Left Click + Right Click

.discovery-block
  .discovery-header → h2 "Discovery" + toggle button
  div#discoveryList

.handoff-status-box  ← notice/status text
```

### Key `handoffState` Fields
```javascript
{
  token: string,
  mode: "idle" | "armed" | "pending_handoff" | "active_remote",
  activeTargetId: string | null,
  activeEdge: "left"|"right"|"top"|"bottom" | null,
  screens: [{screen_id, device_id, name, rect:{x,y,width,height}, primary, edge_enabled, monitor_index?}],
  selectedScreenId: string,
  freeformScreens: boolean,
  routes: [{from_screen_id, to_screen_id, exit_edge, enter_edge, to_device_id, overlap_px}],
  remoteConnected: boolean,
  remoteTargetLabel: string,
  pointerLocked: boolean,
  dwellProgress: number | null,        // 0.0–1.0
  screenWidth/screenHeight: number | null,
  realDims: {[deviceId]: {width, height}},
  localMonitors: [{index, x, y, width, height, primary, name}],
  discoveryAdvertising: boolean,
  discoveredDevices: [{name, device_id, host, port}],
  discoveryPollTimer: number | null,
  pairingState: {step, target, expiresAt, countdownTimer},
  viewport: {x, y, scale},
}
```

---

## Patterns to Follow

### Adding a new section to the control panel
Add HTML inside `.handoff-control-panel` in `handoff.html`. Use `.remote-control-block` for a separated section with a title. Get the element at the top of `handoff.js` with `document.getElementById`. Update `renderState()` to reflect any state-driven changes.

### Adding a new modal
Add HTML before `<script>` with `class="modal-overlay"` and `hidden`. Toggle `element.hidden`. Confirm/cancel buttons call JS functions. Wrap API calls in try/catch, show errors in a `.modal-error` div inside the modal.

### Polling pattern
```javascript
function startXPolling() {
  stopXPolling();
  handoffState.xTimer = setInterval(pollX, 2000);
}
function stopXPolling() {
  if (handoffState.xTimer !== null) {
    clearInterval(handoffState.xTimer);
    handoffState.xTimer = null;
  }
}
async function pollX() {
  if (!handoffState.token) return;
  try {
    const data = await api("/api/some/endpoint");
    // update state, call renderState() or renderAll()
  } catch { /* ignore transient errors */ }
}
```

### Adding a new API route to the server
In `app/main.py`, add a Pydantic `BaseModel` for the request body, then:
```python
@app.post("/api/my/route", dependencies=[Depends(verify_token)])
async def my_route(payload: MyRequest) -> dict:
    ...
    return {"ok": True, "field": value}
```
For session-authenticated routes (no owner token), omit `dependencies=[Depends(verify_token)]` and verify session manually:
```python
if pairing_session_book.verify_session(payload.session_id, payload.session_token) is None:
    raise HTTPException(status_code=401, detail="Invalid or expired session.")
```

---

## Known Rough Edges / Areas for Improvement

### `/handoff` control panel is crowded
The right panel has 8+ distinct sections in a single scrollable column. Consider a tab bar (`State` / `Remote` / `Discovery`) to organize:
- **State tab**: selected monitor info, mode/target/edge/dwell, arm/disarm, add machine/monitor, simulate/confirm
- **Remote tab**: host/port/token fields, connect/disconnect, touchpad pad, Go Active
- **Discovery tab**: advertise toggle, device list, paired device management

### Discovery polling always shows "no devices" initially
When advertising starts, the first poll fires immediately but mDNS needs ~1–2s to resolve. Add a "searching…" state for the first few seconds after advertising starts.

### Pending pair modal countdown
The PIN countdown only updates every 2s (tied to the discovery poll interval). It should have its own 1s interval updating from the server's `seconds_remaining` value.

### No connection-lost indicator for discovery sessions
When a session-based remote connection drops, the bridge silently disconnects. A reconnect prompt (using stored `slickshiftTrusted_${device_id}` credential) would improve UX.

### No visual distinction between monitor tiles when multiple local monitors are shown
When the user has 2 real monitors, both local tiles look identical (same green border). A subtitle showing "Built-in Display" vs "External Display" — sourced from `monitor_index` and `handoffState.localMonitors` — would clarify which is which.

---

## Rules for AI Working on This UI

1. **No build step** — do not add npm, webpack, bundlers, or framework imports. Vanilla JS only.
2. **No new CSS files** — all styles go in `static/style.css`. Use existing CSS variables.
3. **No inline styles** except positional values that must be computed at runtime (e.g., `style="left:${x}px"`).
4. **`escapeHtml(value)`** — always use this when inserting user-controlled strings into innerHTML.
5. **State first, render second** — mutate `handoffState`, then call a render function. Do not mutate the DOM directly for state-driven content.
6. **`hidden` attribute** for show/hide — not `display:none` via JS, not CSS classes toggling visibility.
7. **`type="button"` on every `<button>`** unless it is a form submit. Prevents accidental form submission.
8. **Token check before API calls** — check `if (!handoffState.token) return;` at the top of async functions that call `api()`.
9. **Catch API errors** — wrap every `await api(...)` call in try/catch. Show errors via `setNotice(error.message)` or inline modal error text; do not throw to the top level.
10. **Test count is 419** — if adding backend routes, run `python -m pytest tests/ -q` in the venv and confirm all pass before reporting done.
