# DESIGN.md — Screen Slickshift Browser UI Design Reference

> **This document covers the current browser-based MVP** (`static/`, served from the FastAPI server).
> For the **future native desktop app**, the authoritative design spec is **`docs/UI_HANDOFFNEW.md`**.
> Reference prototype files for the native UI (React/JSX + CSS) live in `docs/UIFILES/`.
> New native-app UI work should follow UI_HANDOFFNEW.md, not this document.

**Audience:** This document is structured context for Claude Design (or any lightweight design-focused LLM). It is not a developer reference. It describes what the product is, how users interact with it, what every screen contains, how it should look and behave, and what to avoid. Read it top to bottom before generating any mockup or component for the browser MVP.

---

## 1. Product Overview

Screen Slickshift is a local-LAN input-sharing utility. It lets one person use the mouse and keyboard of one computer to control another computer on the same network, by physically moving the cursor past the edge of the screen or manually triggering a handoff. It is not a remote desktop product. No screen is ever captured or transmitted.

The product runs as a local web server (Python/FastAPI). The UI is a browser-based control panel served from that server. There is no cloud component. All communication is device-to-device on the local network.

The current implementation has three pages:
- `/` — primary touchpad and security control panel
- `/handoff` — edge detection, layout simulation, and remote connection management
- `/file-transfer` — file drop and transfer history (out of scope for current design work)

---

## 2. User Goals

Primary user:
- Technical hobbyist or power user with two or more computers on the same desk or in the same room.
- Wants to share one keyboard and mouse across machines without a physical KVM switch.
- Cares about privacy: does not want screen contents transmitted, does not want cloud services.
- Comfortable running a local server but does not want to manage config files.

Goals in priority order:
1. Quickly connect to another machine and start controlling it.
2. Set up automatic edge handoff so cursor movement is seamless.
3. Trust a device so pairing doesn't require a token every time.
4. Know at a glance whether the current machine is controlling, being controlled, or idle.
5. Stop remote control instantly if something goes wrong.

Non-goals for users:
- Streaming video or audio.
- File syncing (file transfer exists as a separate utility, not a core workflow).
- Managing multiple simultaneous active sessions.

---

## 3. Primary Workflows

### 3.1 Manual Remote Control (Touchpad Mode)

1. User opens `/` on the controlling machine.
2. Enters the pairing token shown in the server console.
3. The touchpad area activates. Dragging moves the remote cursor.
4. User can left-click, right-click, middle-click, scroll, and send text.
5. User can tap "Capture Cursor" to lock pointer and control mouse with physical movement.
6. Stop by pressing Escape or clicking Stop.

### 3.2 Edge Handoff (Automatic)

1. User opens `/handoff` on the controlling machine.
2. Connects to server via pairing token.
3. Navigates to the Remote tab, enters the target machine's host/IP and remote token.
4. Connects to the remote machine.
5. Arms the handoff: selects target screen and edge in the State tab, clicks Arm.
6. Moves the physical cursor to the armed edge on the local machine.
7. After dwell delay, the handoff fires. Remote machine receives input.
8. Moving back to the return edge transfers control back.
9. Disarm to stop.

### 3.3 Discovery and Pairing (New Device)

1. User opens `/handoff`, navigates to Discovery tab.
2. Clicks "Start Advertising" so this device is visible.
3. On the target machine, same tab shows the advertising device.
4. Clicks the device card to initiate pairing.
5. PIN is displayed on the target machine. User enters it in the pair modal.
6. If "Remember this device" is checked, credentials are saved to localStorage.
7. Subsequent connections use trusted reconnect (no PIN required).

### 3.4 Emergency Stop

At any point, the Stop button in the top bar ends all active sessions and input control immediately. It is always visible and always reachable.

---

## 4. Core UI Screens

### 4.1 Main Page (`/`)

**Purpose:** Direct touchpad control of a connected machine. Also the home page users land on before connecting.

**Layout:**
- Top bar: product name, connection status pill, navigation links (Handoff), Stop button.
- Auth panel (shown when disconnected): password input + Connect button.
- Two-column grid of panels (collapses to single column below 760px):
  - Touchpad panel (spans 2 rows, left): Large drag area. Capture Cursor button. Scroll speed slider. Left/Right/Middle click buttons.
  - Text panel (right, row 1): Textarea + "Send Text" button.
  - Macros panel (right, row 2): Dynamic list of named macro buttons.
  - Clipboard panel: Get from PC / Send to PC buttons.
  - Security panel (full width): Local device info, pairing code display with Trusted/Guest toggle, trusted devices list, active sessions list, Revoke All.
  - Activity log panel (full width): Scrolling event log (no raw keystrokes, no clipboard content).

**States:**
- Disconnected: Auth panel shown, touchpad disabled, status pill shows "Disconnected."
- Connecting: Button in loading state.
- Connected: Auth panel hidden, touchpad active, status pill shows "Connected."

### 4.2 Handoff Page (`/handoff`)

**Purpose:** Configure and manage cursor edge handoff between machines. Most complex screen.

**Layout:**
- Top bar: title "Handoff", status text (not a pill — plain text showing current handoff state), Main link, Stop button.
- Auth panel: same pattern as main page.
- Three-panel grid:
  - **Layout panel (left, tall):** Visual canvas showing schematic screen tiles arranged in their physical positions. Zoom In/Out/Reset controls. Freeform toggle. Reset Layout button. Tiles are draggable in freeform mode.
  - **Control panel (right, tall):** Tab bar with three tabs: State, Remote, Discovery.
  - **Routes panel (bottom, full width):** Preview of configured handoff routes (source → edge → target). Refresh/Preview button.

**Control panel — State tab:**
- State rows: Selected Monitor, Mode, Target, Edge, Local Screen.
- Dwell progress bar (hidden when not dwelling).
- Target dropdown (list of known screens).
- Edge dropdown (Left/Right/Top/Bottom).
- Arm / Disarm buttons.
- Add Machine / Add Monitor buttons.
- Toggle Monitor Edges button.
- Simulate Edge / Confirm buttons.
- Notice box at bottom: current handoff status message.

**Control panel — Remote tab:**
- Host/IP input.
- Port input (default 8765).
- Remote token input.
- Connect Remote / Disconnect buttons.
- Remote status row (shows "disconnected" / "connected").
- Arm Handoff button (disabled until connected) + Routes button.
- Route hint text.
- Go Active button (disabled until connected).
- Remote touchpad (small pad for cursor control without edge handoff).
- Wiggle / Left Click / Right Click buttons.

**Control panel — Discovery tab:**
- Start/Stop Advertising toggle button.
- Device list: cards for each discovered device, showing name and IP.
- Empty state: "Start advertising to find nearby devices."

**Active handoff overlay:**
- Full-screen dark overlay that appears when "Go Active" is triggered.
- Floating bar at top: status text, Capture Cursor, Left Click, Right Click, Stop buttons.
- Dragging the overlay moves the remote cursor.
- Keyboard is forwarded.
- Escape key stops the session.

**Modals:**
- Pair modal: appears when initiating pairing with a discovered device. Shows device name, PIN input, expiry countdown, Mouse/Keyboard permission checkboxes, Remember checkbox.
- Pending pair modal: appears when another device is requesting to pair with this machine. Shows the PIN the user needs to read aloud or relay to the requester. Large monospace PIN display.

### 4.3 File Transfer Page (`/file-transfer`)

Out of scope for current design phase. Exists but is not a priority.

---

## 5. Component Hierarchy

```
App Shell
├── TopBar
│   ├── Title + StatusPill (or status text)
│   ├── NavLinks
│   └── StopButton
├── AuthGate (conditional — hidden when connected)
│   ├── Label
│   ├── TokenInput
│   └── ConnectButton
└── PageContent
    ├── [Main Page]
    │   ├── TouchpadPanel
    │   │   ├── DragArea
    │   │   ├── CaptureButton
    │   │   ├── ScrollSlider
    │   │   └── ClickButtons (L/R/M)
    │   ├── TextPanel
    │   ├── MacrosPanel
    │   ├── ClipboardPanel
    │   ├── SecurityPanel
    │   │   ├── LocalDeviceInfo
    │   │   ├── PairingCodeDisplay
    │   │   ├── TrustedDeviceList → DeviceCard[]
    │   │   └── SessionList
    │   └── ActivityLog
    └── [Handoff Page]
        ├── LayoutPanel
        │   ├── CanvasControls
        │   └── LayoutCanvas → ScreenTile[]
        ├── ControlPanel
        │   ├── TabBar → TabButton[]
        │   ├── StateTab
        │   │   ├── StateRow[] (mode, target, edge, etc.)
        │   │   ├── DwellProgressBar
        │   │   ├── TargetSelect + EdgeSelect
        │   │   └── ActionButtons
        │   ├── RemoteTab
        │   │   ├── ConnectionForm
        │   │   ├── StateRow (remote status)
        │   │   ├── RemoteTouchpad
        │   │   └── ActionButtons
        │   └── DiscoveryTab
        │       ├── AdvertiseButton
        │       └── DeviceList → DeviceCard[]
        ├── RoutesPanel → RouteRow[]
        ├── ActiveHandoffLayer (overlay)
        └── Modals
            ├── PairModal
            └── PendingPairModal
```

---

## 6. Layout Descriptions

### Overall shell
Max content width: 1180px (main page), 1220px (handoff page). Horizontally centered. 18px padding. Dark page background.

### Top bar
Horizontal flex. Space between left group (title + status) and right group (nav + stop). 16px gap. Sticky or static (not fixed — no scroll issues).

### Auth panel
Card-style panel. Label above. Single row: password input (flex-grow) + button. 16px margin below.

### Main page grid
Two columns, repeating `1fr 1fr`, 16px gap. Touchpad panel spans 2 rows via `grid-row: span 2`.

### Handoff grid
Two columns: `1.5fr 0.8fr`. Layout panel left. Control panel right. Routes panel below, full width (grid-column: 1 / -1).

### Panels
8px border-radius. 14px internal padding. 1px border. No drop shadow.

### Control panel tabs
Tab bar is a horizontal strip of text buttons flush against the top of the panel, separated from content by a 1px bottom border. Active tab has a colored underline (2px, accent color). Tab panel content is below the tab bar. Only one tab panel is visible at a time.

### Layout canvas
Fixed minimum height (360px). Background grid pattern (subtle dots or lines). Screen tiles positioned absolutely within a "world" div that is translated and scaled for pan/zoom.

### Routes panel
Flat list. Each route is a single row: source screen name, arrow glyph, edge name, arrow glyph, target screen name.

---

## 7. Interaction Behaviors

### Touchpad (main page)
- Pointer down on the pad area begins tracking.
- Mouse movement sends delta events to the server at ~80Hz (throttled to 12ms intervals).
- Pointer up ends tracking.
- No visual cursor on the touchpad — the interaction is felt through the remote machine's cursor.
- Scroll events map to the server's scroll input.
- Capture Cursor: requests pointer lock. Movement continues via `mousemove.movementX/Y`. Escape releases pointer lock.

### Active remote layer (handoff page)
- Full-screen overlay intercepts all input.
- Mouse drag sends movement events.
- Pointer lock optional via Capture Cursor button.
- Keyboard events forwarded: printable characters, special keys (F1-F12, arrows, delete, etc.), modifier combinations.
- Pointer down sends `mouse_button down: true`. Pointer up sends `mouse_button down: false`. This enables drag on the remote machine.
- Right-click sends right button down/up.
- Escape key stops the session.

### Layout canvas drag
- Tiles draggable in freeform mode. Constrained to logical grid positions in normal mode.
- Pan: drag empty canvas area.
- Zoom: scroll wheel or zoom buttons.

### Edge dwell
- When cursor hits the armed edge, a progress bar fills over the dwell delay (configurable, default ~300ms).
- If cursor leaves the edge before dwell completes, progress bar resets.
- Progress bar is shown inline in the State tab, not as a global overlay.

### Discovery
- Polling starts when the Discovery tab is active and advertising is on.
- Device cards appear as devices are found.
- Clicking a device card opens the pair modal.
- 4-second grace period before "no devices" empty state appears.

### Modals
- Click outside the modal or press Escape to dismiss (except PIN entry — must explicitly cancel or submit).
- Countdown timer shown in pair modal. When it expires, the modal closes automatically and an error is shown.

---

## 8. Connection States

### Local server connection (token auth)

| State | Status indicator | Touchpad | Auth panel |
|---|---|---|---|
| Disconnected | "Disconnected" (muted text or pill) | Disabled | Shown |
| Connected | "Connected" (green pill or text) | Enabled | Hidden |

### Remote machine connection (handoff page)

| State | Remote status row | Go Active button | Arm Handoff button |
|---|---|---|---|
| Disconnected | "disconnected" | Disabled | Disabled |
| Connecting | "connecting…" | Disabled | Disabled |
| Connected | "connected" (green) | Enabled | Enabled |

### Handoff mode (State tab)

| Mode | Mode row text | Behavior |
|---|---|---|
| idle | "idle" | No monitoring |
| armed | "armed" | Polling cursor position |
| dwelling | "dwelling" | Dwell progress bar fills |
| active | "active" | Remote machine has control |
| active_remote | "active_remote" | This machine is sending input to remote |

### Discovery

| State | Discovery tab |
|---|---|
| Not advertising | "Start Advertising" button |
| Advertising, searching | Searching indicator, grace period before empty state |
| Advertising, devices found | DeviceCard list |
| Pairing in progress | PairModal shown |
| Paired | Modal dismisses, device may appear in trusted list |

---

## 9. Error States

- **Wrong token:** Show inline error below the token input. Do not clear the input.
- **Connection refused (remote):** Show error in the remote status row. Keep inputs populated.
- **Pairing PIN expired:** PairModal closes, show a brief error message, allow retry.
- **Pairing PIN wrong:** Inline error in PairModal. Do not close modal.
- **Handoff arm failed:** Show error in the notice box at bottom of control panel.
- **WebSocket disconnected mid-session:** Update status indicator immediately. If active remote, stop active layer and show notice.
- **Screen info unavailable:** Fall back to defaults; do not crash. Show "unknown" in state rows.
- **No monitors found:** Show single default monitor. Do not block usage.

Error messages should be plain English. Never expose raw exception text, stack traces, or internal API paths in the UI.

---

## 10. Accessibility

- All interactive elements must have visible focus states (3px offset outline, `rgba(56,189,248,0.45)` as reference color).
- All buttons have `type="button"` to prevent accidental form submission.
- Touch targets: minimum 44px height on primary actions. Compact buttons may be 34px where space is constrained.
- Tab order: follows visual reading order top-to-bottom, left-to-right.
- The layout canvas and touchpad are not keyboard-navigable by design — they are pointer-based controls. They have ARIA labels describing their purpose.
- Modals use `role="dialog"` pattern. Focus is trapped inside modal while open.
- Status rows use `<span>` for label and `<strong>` for value. Screen reader reads both.
- Hidden content uses the `hidden` HTML attribute. CSS must include `[hidden] { display: none !important; }` to prevent class-based display overrides from showing hidden content.
- Color is never the only indicator of state. Status text accompanies all colored indicators.

---

## 11. Steam Deck and Handheld Considerations

The app is intended to run in a browser, including Steam Deck's built-in browser in Desktop Mode.

- Touch targets must be at minimum 44px. Prefer 48-52px for primary actions on small screens.
- Avoid hover-only state disclosure — all states must be visible on touch.
- The remote touchpad is the primary control surface on a touch device. It should be large and centered.
- Keyboard forwarding should work with the on-screen keyboard (keys sent as typed characters).
- Pointer lock is not available in all mobile/handheld environments — fall back gracefully to touch movement.
- The layout canvas pan/zoom should support pinch-to-zoom on touch when possible.
- Responsive breakpoint at 760px collapses grid to single column, which works on Steam Deck's ~1280px width but may reflow on portrait phone.
- No feature requires a right mouse button — alternatives always available via buttons.

---

## 12. Visual Style

### Color palette (exact values from current implementation)

| Token | Value | Use |
|---|---|---|
| `--bg` | `#111418` | Page background |
| `--panel` | `#1d232a` | Card and panel background |
| `--panel-strong` | `#27313b` | Raised elements inside panels (secondary buttons, inputs, state rows) |
| `--text` | `#f4f7fb` | Primary text |
| `--muted` | `#a9b4c0` | Labels, secondary text, hints |
| `--accent` | `#22c55e` | Primary action buttons, active state indicators, selected tile border |
| `--accent-strong` | `#16a34a` | Hover state for accent elements |
| `--danger` | `#ef4444` | Stop button, destructive actions only |
| `--border` | `#3a4652` | All borders |

No other colors should be introduced without a clear purpose. Do not add purple, orange, or gradient fills.

### Typography

- Font family: `"Segoe UI", system-ui, sans-serif` — uses native system font on each OS.
- `h1`: 1.45rem, normal weight used in top bar product name.
- `h2`: 1rem, used for panel section titles.
- `h3`: 0.86rem, no margin.
- Body text: 0.9rem–0.95rem. Labels slightly smaller.
- Monospace (PIN display, activity log): system monospace stack.
- Bold (`<strong>`) for state values in status rows.

### Shape language

- Border radius: 8px everywhere. No pill shapes on panels or large cards.
- Pill shape (border-radius: 999px) is reserved for small status badges/labels.
- No shadows. Elevation is communicated through background color difference only.
- 1px borders throughout. No thick borders except focus rings.
- Buttons: full-width by default for primary actions. Auto-width for secondary and compact.
- Compact buttons: 34px minimum height, 0.86rem font, reduced padding.

### Layout spacing

- Panel padding: 14px.
- Gap between panels: 16px.
- Gap between form elements: 8px (label + input).
- Button row gap: 8px.
- State row internal padding: 10px 12px.
- Auth panel margin below: 16px.

---

## 13. Suggested Animations

Keep animations minimal and functional. No decorative motion.

| Element | Animation | Duration | Notes |
|---|---|---|---|
| Dwell progress bar | Width transition | 80ms linear | Must feel smooth and immediate |
| Tab switch | Instant | — | No slide or fade — content replaces immediately |
| Modal appear | Opacity 0 → 1 | 120ms ease-out | Backdrop and modal fade in together |
| Modal dismiss | Opacity 1 → 0 | 80ms ease-in | Fast exit |
| Status pill color change | Instant | — | No color transition |
| Screen tile drag | Pointer-driven | — | No spring or snap animation |
| Discovery badge | Instant | — | Badge appears/disappears without animation |
| Active layer show/hide | Instant | — | Full-screen overlay should appear immediately, no fade |

Do not animate: button clicks, hover states, layout panel pan/zoom, state row value changes.

---

## 14. Responsive Layout Behavior

### Above 760px (desktop)

- Main page: 2-column grid, touchpad spans 2 rows.
- Handoff page: `1.5fr 0.8fr` two-column layout. Routes panel full width below.
- Top bar: horizontal with all elements visible.

### Below 760px (tablet and handheld)

- All grids collapse to single column.
- Touchpad panel stacks above text/macros/clipboard panels.
- Handoff page: layout panel above, control panel below, routes panel below that.
- Top bar: wraps or stacks — title on one row, controls on next.
- `button.danger` and `button.compact-button` become full width.
- Modal max-width uses full viewport width minus safe margins.

### No horizontal scroll

The layout must never produce horizontal scrollbar. Use `min-width: 0` on flex/grid children when needed.

---

## 15. Security Visibility Requirements

The UI must make trust state visible without requiring the user to navigate to a settings screen.

Required always-visible:
- Whether a remote session is currently active (top bar, status text or indicator).
- The Stop button — always in the top bar, always reachable without scrolling.

Required visible on request (one tap/click):
- Which device is connected and its identity.
- Current pairing token (obfuscated by default, reveal on click).
- List of trusted devices.
- Active sessions with their permissions.
- Whether keyboard forwarding is enabled for a session.

Must never be visible in UI:
- Raw session tokens (do not display in logs or status rows).
- Pairing codes in the activity log.
- Clipboard content in the activity log.
- Keystroke content in the activity log.

The PIN displayed in the pending pair modal is intentionally large and readable — it needs to be read aloud or relayed to the requesting user.

The "Remember this device" checkbox defaults to checked. This is intentional — friction-free repeat connections are a core goal. But the checkbox must be clearly labeled and easily unchecked.

---

## 16. Persistent UI Constraints

These constraints come from the underlying architecture and must not be designed around:

1. **Single active session**: Only one remote machine can be actively controlled at a time. Do not design UI for simultaneous multi-session control.

2. **No screen preview**: Never show what the remote machine's screen looks like. No thumbnail, no live feed, no screenshot. This is a firm product constraint, not a missing feature.

3. **Local server only**: The server runs on the same machine as the browser. Do not design for cloud accounts, remote server configurations, or SaaS-style login flows.

4. **Token shown in server console**: The initial pairing token is printed to the terminal when the server starts. The UI cannot generate or regenerate this token — it is set server-side. The input field is for the user to enter the token they see in their terminal.

5. **No user accounts**: There are no usernames, passwords, or accounts. Pairing is device-to-device via PIN. Trusted device credentials are stored in the browser's localStorage.

6. **Physical topology**: The layout canvas shows a schematic of where screens are physically arranged relative to each other. It is not a pixel-accurate diagram. Tiles are symbolic, not proportional.

7. **Keyboard forwarding is session-level, not key-level**: You cannot choose which keys to forward per-keystroke. The permission is granted or denied per session.

---

## 17. Features Intentionally Not Included

Do not design UI for these features. They are not planned:

- Screen sharing or remote desktop video.
- Audio forwarding.
- Remote file browsing.
- Cloud sync or cloud relay.
- User accounts or email login.
- Chat or messaging between devices.
- Wake-on-LAN.
- Remote shutdown or reboot controls.
- Remote application launching.
- Screenshot capture from remote machine.
- Automatic reconnect to last session on page load.
- Multi-user simultaneous sessions.
- Browser extension or OS-level input capture.

---

## 18. Frontend Architecture

### Current implementation

Vanilla HTML, CSS, JavaScript. No build step. No framework. Three HTML files, one CSS file, three JS files. All served as static files from the Python server.

### State pattern

Each page has a central state object (e.g., `handoffState`). All mutations go through the state object first. Render functions read from the state object and update the DOM. No direct mutation of DOM from event handlers — event handlers update state, then call render.

### API call pattern

All API calls use a shared `apiCall(path, body)` helper that injects the auth token from state and returns parsed JSON. Errors are caught and displayed. No global fetch interceptors.

### Event pattern

WebSocket for real-time input (touchpad events, handoff events). HTTP polling for state changes (detector state, discovery devices, pending pair requests). No server-sent events currently.

### localStorage keys in use

- `wdcToken` — local server pairing token.
- `slickshiftHandoffRemoteHost`, `slickshiftHandoffRemotePort`, `slickshiftHandoffRemoteToken` — remembered remote connection fields.
- `slickshiftLayout_v1` — saved screen layout positions.
- `slickshiftTrusted_${device_id}` — trusted device credentials (session_id, session_token, device name).

### Module upgrade path (if refactoring)

If moving to ES Modules: each component becomes a `.js` module. Shared state via a `state.js` module exported and imported. No bundler needed — browsers support `type="module"` natively. This is the recommended next step if the JS files grow beyond ~2000 lines each.

---

## 19. Reusable Components

These 12 components appear multiple times across the product and should be designed as consistent, reusable units. In the current vanilla JS implementation they are patterns, not Web Components — but they should look identical wherever they appear.

| Component | Appears in | Description |
|---|---|---|
| `AuthGate` | All pages | Token input + Connect button. Hidden when connected. |
| `DeviceCard` | Discovery tab, Trusted Devices list | Device name, IP or subtitle, action button (Pair / Remove / Connect). |
| `StateRow` | State tab, Remote tab, Security panel | Label (left, muted) + value (right, bold). Grid layout. |
| `TabBar` | Handoff control panel | Tab buttons with active underline. Tab badge for notifications. |
| `TabPanel` | Handoff control panel | Content area shown/hidden by active tab. |
| `ModalDialog` | PairModal, PendingPairModal | Overlay + centered card. Title, body, optional input, action buttons. |
| `StatusPill` | Main page top bar | Small inline badge. Green for connected, muted for disconnected. |
| `NoticeBox` | Bottom of control panel | Full-width, color-neutral box for current status message. |
| `ProgressBar` | Dwell progress | Track + fill. Fill width driven by 0.0–1.0 value. Animated. |
| `ClickRow` | Throughout | Horizontal flex row of 2 buttons with 8px gap. |
| `PanelHeader` | All panels | `<h2>` title + optional right-aligned action buttons. |
| `EmptyState` | Discovery list, route list | Centered muted text when a list has no items. |

---

## 20. Guidance for Claude Design

**What this product is:**
A local input-sharing utility that looks and feels like a calm system settings app. Dark. Minimal. Practical. No marketing.

**What to prototype first:**
The Handoff page — State tab and Remote tab. This is the most complex and differentiated screen. It contains: the visual layout canvas with draggable tiles, the tab-based control panel, the state rows, the remote connection form, the active handoff overlay, and two modals. Getting this page right establishes the design language for everything else.

**The most important reusable components to define first:**
1. `AuthGate` — appears identically on all three pages.
2. `StateRow` — appears 8+ times on the handoff page alone.
3. `DeviceCard` — appears in Discovery tab and Trusted Devices panel.
4. `ModalDialog` — pair modal and pending pair modal share the same shell.
5. `TabBar` + `TabPanel` — the control panel tab system.

**Exact colors to use (do not invent new ones):**
```
Background:      #111418
Panel:           #1d232a
Panel raised:    #27313b
Text:            #f4f7fb
Muted text:      #a9b4c0
Accent (green):  #22c55e
Accent hover:    #16a34a
Danger (red):    #ef4444
Border:          #3a4652
```

**Typography:**
System font (`"Segoe UI", system-ui, sans-serif`). No custom fonts. No icon fonts — use text glyphs or Unicode arrows (→, ←, ↑, ↓) where needed.

**Shape:**
8px border-radius on everything. No shadows. Depth via background color difference only. 1px borders.

**Buttons:**
- Primary (green, full-width, 44px min height): Arm, Connect, Send, Go Active.
- Secondary (dark background, auto-width, bordered): Disarm, Disconnect, Zoom, Preview, Refresh.
- Danger (red, auto-width): Stop, Revoke.
- Compact (auto-width, 34px, smaller font): Zoom In, Zoom Out, Reset, Preview routes.

**What NOT to design:**
- Remote screen preview or thumbnail.
- Video/audio controls.
- Cloud/account/login screens.
- Decorative backgrounds, gradients, or blobs.
- Animations on state rows, buttons, or layout tiles.

**The Stop button:**
Always in the top-right of the top bar. Always red (`#ef4444`). Always labeled "Stop". Never hidden. Never disabled. Never moved to a submenu.

**The layout canvas:**
This is a schematic, not a map. Screen tiles are simple rounded rectangles with the screen name inside. Local machine's screen has a green border. Target machine's screen has a cyan/teal border. Edges with handoff configured show a small indicator (arrow or colored dot). The canvas has a subtle dot-grid background to convey it is a spatial workspace.

**The active remote overlay:**
A full-screen semi-transparent dark overlay (not fully opaque — user should see the text underneath for context). A floating bar anchored to the top with status text and control buttons. This is the most critical safety affordance — the user must always be able to see and reach Stop.

**Accessibility note for generated mockups:**
Focus rings must be visible in all mockup states. The pair PIN display should be at least 2.4rem, monospace, with letter-spacing. All status states should be readable without color.
