# Screen Slickshift — Native Settings UI Handoff

This document is the implementation brief for the **future native desktop app** (replaces the browser MVP). It is optimised for AI-assisted frontend work in Claude Code: structure, behaviour, and intent — no production code.

The reference prototype lives at `Screen Slickshift Settings.html` (React + JSX, single window, dark vaporwave palette).

---

## 1. Page Purpose

Screen Slickshift is a **local-LAN input-sharing utility**. The native shell is a single dark settings window that lets a user:

- See which trusted devices are online and which is currently being controlled.
- Pair a new device on the same LAN via a 6-digit PIN.
- Arrange screens in a schematic canvas so cursor handoff "just works" at edges.
- Tune mouse/keyboard, clipboard, and security behaviour without leaving the window.
- Trigger Emergency Stop from anywhere in the app.

Tone: **calm, practical, native settings app.** Not a remote-desktop product. No live screen previews, no marketing chrome.

---

## 2. Layout Structure

Single fixed-aspect window (~1180×760, resizable in production) with three regions:

```
┌─────────────────────────────────────────────────────────────┐
│ Titlebar  [● ● ●]   Screen Slickshift    Connected · LAN …  │
├──────────────┬──────────────────────────────────────────────┤
│              │                                              │
│  Sidebar     │   Main panel                                 │
│  (fixed      │   (scrollable, padded)                       │
│   width)     │                                              │
│              │                                              │
│  …           │                                              │
│  LAN pill    │                                              │
│  Emergency   │                                              │
│  Stop        │                                              │
└──────────────┴──────────────────────────────────────────────┘
```

- **Titlebar** spans full width, hosts traffic-light controls and a connection status indicator (right-aligned dot + LAN summary).
- **Sidebar** (≈248 px comfortable / ≈212 px compact) is always visible; navigation is a flat list — no nested disclosure.
- **Main panel** is the only scroll container in the body. The sidebar never scrolls.

### Sidebar contents (top → bottom)

1. Brand mark + product name + build label.
2. Group label `GENERAL` → **Overview**, **Devices**, **Settings**.
3. Group label `TRUST & ACTIVITY` → **Security**, **Logs**.
4. Spacer (`margin-top: auto`).
5. Persistent **"Local LAN only"** pill (live indicator).
6. Emergency Stop button.

---

## 3. Component Hierarchy

```
App
├── WindowChrome
│   ├── TrafficLights
│   ├── WindowTitle
│   └── ConnectionStatus
├── Sidebar
│   ├── BrandMark
│   ├── NavGroup (×2)
│   │   └── NavItem (×N)            // icon + label + optional badge
│   ├── LanPill
│   └── EmergencyStopButton
└── Main (router by active section)
    ├── OverviewScreen
    │   ├── DevicesPanel (compact)
    │   │   ├── DeviceRow             // self
    │   │   ├── DeviceRow (×N)        // paired
    │   │   └── DiscoveredRow (×N)
    │   └── ScreenEdgesCanvasPanel
    │       ├── EdgesCanvas
    │       │   ├── ScreenTile (draggable, ×N)
    │       │   └── EdgeArrow (×N)
    ├── DevicesScreen                 // full list with inline perm pills
    │   ├── DevicesPanel
    │   │   └── DeviceRow (with PermPill ×3 + Unpair)
    │   ├── DiscoveredPanel
    │   └── PairModal
    │       ├── MethodPicker
    │       ├── EnterCodeStep
    │       │   ├── PinInput (6 cells)
    │       │   └── PermissionList
    │       ├── ShowCodeStep (with Countdown)
    │       ├── ConnectingStep (Spinner)
    │       └── SuccessStep
    ├── SettingsScreen                // single scroll, grouped headers
    │   ├── MouseAndKeyboardGroup
    │   ├── ScreenEdgesGroup          // behaviour only — layout is on Overview
    │   └── ClipboardGroup
    ├── SecurityScreen
    │   ├── ThisDevicePanel
    │   └── ActiveSessionsPanel
    └── LogsScreen
        └── LogRow (×N)
```

`PairModal` and the Emergency Stop banner are siblings of the section router (overlays). The banner appears at the top of `Main` when lockout is active and is independent of the active section.

---

## 4. Responsive Behavior

The native window is **fixed-min-size**, not fluid. Define two density tokens and one min size, then let the OS handle resize.

- **Min window**: 980×640. Below that, sidebar is allowed to collapse to icon-only (40 px).
- **Comfortable density**: row 36 px, panel padding 24 px, sidebar 248 px.
- **Compact density**: row 30 px, panel padding 16 px, sidebar 212 px.
- **Overview grid** (`Devices ⟷ ScreenEdges`) is `grid-template-columns: 1fr 1fr` until window width < 980 px, then it stacks into single column.
- **PinInput cells** stay 44–48 px regardless of density (target accessibility minimum).
- **Pair modal** is centred, max width 460 px, never expands.
- Long device names truncate with `text-overflow: ellipsis`; the row never wraps.

---

## 5. User Interaction Flow

### 5.1 First-run / pairing flow
1. User clicks **Add device** (Overview header or Devices header) **OR** clicks **Pair** on a discovered row.
2. Modal opens.
   - From the header → `MethodPicker` (Enter a code / Show a code).
   - From a discovered row → skip directly to `EnterCodeStep` with that device pre-targeted.
3. `EnterCodeStep`: user types 6 digits. Each digit auto-advances focus. Backspace on empty cell jumps back. When all 6 are filled, transition fires automatically (~200 ms debounce).
4. `ShowCodeStep`: a 6-digit code is rendered with a 60 s countdown bar. When the remote enters it (or countdown hits 0 in the prototype), advance to `ConnectingStep`.
5. `ConnectingStep`: spinner with "Establishing trust…". Keep visible at least 1.4 s for perceived continuity.
6. `SuccessStep`: green check, "Paired with X". Single primary `Done` button closes modal and pushes the new device onto the trusted list, removing it from `discovered`.

### 5.2 Daily use
- User opens the app → lands on **Overview**. Sees who is online and current screen layout at a glance.
- Drags a screen tile to rearrange. Tiles soft-snap (within 10 px) to the edges of neighbouring tiles. When edges actually touch (within 4 px) an `EdgeArrow` renders to indicate that handoff direction.
- Toggles per-device permissions (Clipboard / Files / Macros) inline on the **Devices** tab. Changes are applied optimistically; failure surfaces inline.
- Opens **Settings** to tune tracking speed, scroll, panic hotkey, clipboard sharing.
- Opens **Security** to revoke a session or trigger lockout.
- Opens **Logs** to audit pairing attempts and emergency events.

### 5.3 Cross-section navigation
- Sidebar click is the only navigation primitive. No tabs, no breadcrumbs.
- Internal "see also" hints (e.g. *"Per-device permissions live on the Devices tab"*) are static text, not links — keeps a single source of navigation truth.

---

## 6. States and Transitions

### App-level state
| State | Source | UI effect |
|---|---|---|
| `activeSection` | sidebar click / external event | Routes the main panel. Persisted via tweak so reload restores last view. |
| `lockout` | Emergency Stop / Security toggle | Shows red banner at top of main, switches the sidebar button to "Lockout active — release". |
| `accent` (theme) | tweaks panel | Rewrites `--accent` / `--accent-2` / soft / glow CSS vars. |
| `density` | tweaks panel | Sets `data-density` on `<html>`. |

### Pair modal state machine
```
idle → method ──► enter ──► connecting ──► success ──► (close)
              └► show  ──► connecting ──► success ──► (close)
```
- `enter` and `show` both branch to `connecting`.
- Modal can be cancelled from `method` / `enter` / `show` — closing during `connecting` or `success` is allowed but should not retroactively un-pair.

### Device row state
| State | Indicator |
|---|---|
| Self | `tag.you` "This Device" pill, accent-tinted icon |
| Online (idle) | green status dot + "Online" tag |
| Controlling | live cyan tag with pulsing dot |
| Being controlled | violet variant of the live tag |
| Paired (offline) | muted tag with "Paired · 2 min ago" |

### Transitions
- Modal: `200 ms cubic-bezier(.2,.9,.3,1.1)` rise + fade.
- Status pulse: 1.6–1.8 s ease-in-out infinite, reduced motion: stop animation, keep colour.
- Section change: instant. No slide / fade — settings apps don't animate route changes.
- Toggle thumb: 160 ms ease.

---

## 7. Button Behavior

| Variant | Use | Style cue |
|---|---|---|
| `btn-primary` | One per screen, the affirmative action ("Add device", "Done") | Accent gradient, glow shadow |
| `btn-secondary` | Neutral but distinct ("Pair", "Sync now") | Solid panel-2 fill, bordered |
| `btn-ghost` | Tertiary inline ("Rename", "Unpair", "Configure…") | Transparent, border only |
| `btn-danger` | Destructive ("Revoke all sessions") | Red tint, red border |
| `estop` | Emergency only | Red gradient when active; outlined when idle |

Rules:
- `type="button"` on every `<button>`. Never accidentally submit a form.
- Icon + label uses 6 px gap, 14 px icon at 13 px text.
- Disabled state lowers opacity to 0.5 and disables hover transform.
- Hover shifts `translateY(-1px)` only on `btn-primary`; others change background only.

---

## 8. Connection States

Surfaced in three places consistently:

1. **Titlebar status** (always visible): green dot + "Connected · LAN 192.168.1.0/24" or red dot + "Offline".
2. **Sidebar LAN pill**: persistent green when LAN reachable, switches to amber "Network unreachable" when local interface is down.
3. **Per-device row**: dot + tag (see §6).

State machine for a single device:
```
discovered → pairing → trusted-offline ⇄ trusted-online
                              │
                              ├─ → controlling
                              ├─ → controlled
                              └─ → review-required (after lockout)
```

`review-required` state must show a yellow `tag.warn` and disable input forwarding until the user clicks **Approve** in the row.

---

## 9. Error States

All errors surface near the action that caused them — not via toasts in the corner.

| Failure | Surface |
|---|---|
| Invalid pairing code | Inline red text under PIN row + cells reset with shake animation |
| Code expired | `ShowCodeStep` swaps countdown for "Code expired — generate new" with a `btn-secondary` to regenerate |
| Connection dropped | Device row tag flips to muted "Offline · retrying", row gains a single retry button on hover |
| Permission denied (e.g. Accessibility on macOS) | Settings row inlines a yellow note + "Open Settings…" button |
| Discovery failed | Discovered panel collapses to one line + retry chip |
| Lockout blocking action | Banner persists; the failed control disables and shows tooltip "Blocked by Emergency Stop" |

No fullscreen error screens. The shell never goes blank.

---

## 10. Accessibility Considerations

- **Keyboard navigation**: every interactive must be reachable via Tab. Sidebar nav uses arrow keys (Up/Down) within the group, Enter to activate.
- **Focus ring**: 3 px `var(--accent-soft)` outline outside the element; never remove.
- **Pair modal traps focus** while open; Escape closes (except during `connecting`/`success`).
- **PIN cells** declare `aria-label="Digit N of 6"`. Paste of full 6-digit code into any cell distributes across all six.
- **Toggles** are real `<button role="switch" aria-checked>`, not divs in production.
- **Targets**: 44 px minimum for any tap target (PIN cells, Emergency Stop, primary buttons). Inline perm pills are 28 px — acceptable for desktop pointer; on touch builds (Steam Deck), increase to 36 px.
- **Contrast**: text/background ≥ 4.5:1. Muted text uses `--text-dim` (≈7:1 on `--panel`), never `--muted` for any sentence the user must read.
- **Reduced motion**: respect `prefers-reduced-motion`. Disable pulse, spinner becomes a static dotted ring, modal rise becomes instant.
- **Live regions**: connection state changes announce via an `aria-live="polite"` element near the titlebar.
- **Emergency Stop** must be reachable with a single, documented global hotkey, even if focus is trapped in a modal.

---

## 11. Visual Style Guidance

**Direction**: dark vaporwave settings — deep indigo base, cyan/violet accent gradient, minimal saturation outside the accent. Calm, not neon.

### Palette (CSS custom properties)
```
--bg-0           #08071a    page outside window
--bg-1           #0d0b22    window base
--panel          #15132e    cards
--panel-2        #1c1937    raised inside cards
--panel-3        #25214a    deepest raised (slider track, seg control)
--text           #eef0ff
--text-dim       #b9b4dd    secondary copy
--muted          #7e7aab    metadata, labels
--border         rgba(140,120,220,0.16)
--border-strong  rgba(160,140,240,0.28)
--accent         #22d3ee    cyan (default)
--accent-2       #c084fc    violet (gradient pair)
--accent-soft    rgba(34,211,238,0.18)
--accent-glow    rgba(34,211,238,0.35)
--ok             #4ade80
--warn           #fbbf24
--danger         #fb7185
```

The accent palette is **swappable** at runtime (Cyan / Magenta / Mint / Amber). All accent-derived colours are computed from `--accent` so swapping one variable cascades.

### Typography
- UI: **Inter** 400 / 500 / 600 / 700.
- Mono (PINs, IDs, hotkeys, log timestamps): **JetBrains Mono** 500 / 600.
- Scale: H1 24 / H2 13 / body 13 / meta 12 / micro 11 / labels 10 uppercase.

### Surface treatment
- Window: 16 px radius, soft 30 px shadow + 1 px subtle violet outline.
- Panels: 12 px radius, 1 px border, no inner shadow.
- Background of stage: layered radial gradients (violet top-left, cyan bottom-right) + faint 40 px grid masked to vignette. The window itself sits cleanly on top.

---

## 12. Spacing / Layout Logic

Token-based spacing — do not invent ad-hoc values.

| Token | Value | Use |
|---|---|---|
| `--row-h` | 36 / 30 px | sidebar item, dev row min height |
| `--pad`   | 24 / 16 px | main panel horizontal/vertical padding |
| panel inner padding | 14 px vertical, 18 px horizontal | every `.set-row`, `.dev-row`, `.log-row`, `.panel-h` |
| panel gap (Overview) | 16 px | between left and right column panels |
| `panel + panel` | 16 px margin-top | vertical stack |
| section group spacing | 28 px | between `settings-group` headers |
| modal | 22 px horizontal, 18 px vertical padding |
| button height | 34 px primary/secondary, 28 px compact ghost |

Grid usage:
- Sidebar: flex column, `gap: 2px`, `margin-top: auto` on the foot block.
- Main: regular vertical stack of panels.
- Overview: CSS Grid 1fr 1fr.
- Permission matrix: Grid `1fr repeat(N, 80px)` for row alignment.

Never use absolute positioning except for: traffic lights inset, edge arrows on the canvas, the modal overlay.

---

## 13. Reusable Component Recommendations

These are the components every screen pulls from — implement once, instrument for variants.

- **`Panel`** — container with optional `Panel.Header` (title + sub) and bordered children. Supports a "footer" slot for the edge canvas variant.
- **`SetRow`** — `{ name, description, control }` two-column row. The vast majority of settings rows are this.
- **`Toggle`** — switch with full and "mini" sizes (mini used inside `PermPill`).
- **`Slider`** — range with an inline mono value chip on the right.
- **`Segmented`** — 2–3 option control (e.g. Relative / Absolute pointer mode).
- **`Tag`** — pill for status. Variants: `live`, `you`, `warn`, neutral.
- **`Kbd`** — keycap-style chip for hotkeys.
- **`PinInput`** — 6-cell numeric, auto-advance, paste-aware.
- **`Spinner`** — accent-on-violet conic ring.
- **`Modal`** — overlay + card with `ModalHeader` / `ModalBody` / `ModalFooter` slots.
- **`DeviceRow`** — composite. Slots for `actions` so Overview (compact) and Devices (with perm pills) reuse the same shell.
- **`EdgesCanvas`** — drag/snap canvas accepting an array of tiles; emits arrangement on change.

Avoid one-off components — if it appears once, write it inline; if it appears twice, hoist it.

---

## 14. Suggested Frontend Component Breakdown

For Claude Code, organise the codebase like this:

```
src/
  app/
    App.tsx                 // root, routing, theme provider, lockout banner
    sidebar/
      Sidebar.tsx
      NavItem.tsx
      LanPill.tsx
      EmergencyStopButton.tsx
    chrome/
      WindowChrome.tsx
      TrafficLights.tsx
      ConnectionStatus.tsx
  screens/
    Overview/
      OverviewScreen.tsx
      DevicesPanel.tsx
      ScreenEdgesPanel.tsx
    Devices/
      DevicesScreen.tsx
      DeviceRow.tsx
      PermPill.tsx
      DiscoveredRow.tsx
      PairModal/
        PairModal.tsx
        MethodPicker.tsx
        EnterCodeStep.tsx
        ShowCodeStep.tsx
        ConnectingStep.tsx
        SuccessStep.tsx
        PinInput.tsx
        PermissionList.tsx
    Settings/
      SettingsScreen.tsx
      MouseAndKeyboardGroup.tsx
      ScreenEdgesGroup.tsx
      ClipboardGroup.tsx
    Security/
      SecurityScreen.tsx
      ThisDevicePanel.tsx
      ActiveSessionsPanel.tsx
    Logs/
      LogsScreen.tsx
      LogRow.tsx
  ui/
    Panel.tsx
    SetRow.tsx
    Button.tsx
    Toggle.tsx
    Slider.tsx
    Segmented.tsx
    Tag.tsx
    Kbd.tsx
    Modal.tsx
    Spinner.tsx
    EdgesCanvas/
      EdgesCanvas.tsx
      ScreenTile.tsx
      EdgeArrow.tsx
  state/
    useDevices.ts            // paired + discovered + actions
    usePairing.ts            // pair modal state machine
    useLayout.ts             // monitor arrangement, snap logic
    useLockout.ts
    useTheme.ts              // accent + density
    useConnection.ts         // titlebar/LAN status
  api/
    client.ts                // fetch wrapper, X-Pairing-Token header
    devices.ts
    pairing.ts
    discovery.ts
    handoff.ts
    sessions.ts
  styles/
    tokens.css               // all CSS custom properties
    base.css
```

State management: prefer **per-feature hooks** (returning `{state, actions}`) over a global store. Only `useTheme`, `useLockout`, and `useConnection` should be context-wide.

---

## 15. Notes for Cross-Platform Behavior

The same React tree is intended to ship on macOS and Windows.

- **Window chrome**: traffic lights are macOS-style in the prototype. On Windows, replace with min/max/close on the right; titlebar height becomes 32 px instead of 44 px. Wrap the whole thing in a `<WindowChrome platform="…" />` — content underneath is identical.
- **Hotkey display**: Mac shows ⌃⌥⌘. Windows shows `Ctrl + Alt + Win`. `Kbd` must accept a `platform` prop and render the correct glyphs.
- **System fonts fallback**: prefer Inter, but fall through to `system-ui`, `Segoe UI Variable` (Win), `SF Pro Text` (Mac).
- **Native menu integration**: the in-app sidebar is the only nav. Don't duplicate it in the OS menu bar; the native menu only owns app-level commands (Quit, Preferences shortcut, Help).
- **Permissions UX**:
  - macOS: surface Accessibility, Input Monitoring, and (future) Screen Recording prompts inline in Settings → Mouse & Keyboard rather than at install time.
  - Windows: surface UAC elevation request inline if pointer mode requires it.
- **File paths in Logs**: never show absolute home paths in logs view; use `~/…` (Mac) or `%USERPROFILE%\…` (Win) abstractions.

---

## 16. Steam Deck / Browser-Specific UX

The legacy browser UI (`/`, `/file-transfer`, `/handoff`) survives as the Steam Deck control surface until a native Linux receiver ships. Treat it as a **separate skin** of the same components, not a parallel implementation.

- **Touch targets**: every interactive grows to 44 px minimum on Deck. PIN cells already qualify; perm pills, log rows, and the sidebar grow.
- **Sidebar collapse**: at < 720 px the sidebar collapses to icons only. Tap-and-hold reveals labels.
- **No window chrome**: hide `WindowChrome` when running in browser context (`window.__SS_BROWSER__` env flag).
- **Pointer affordances**: Steam Deck users expect a touchpad area. Reuse the `EdgesCanvas` infrastructure to render an absolute positioned `RemoteMousePad` on a dedicated `/control` route — out of scope for this native shell, but its components must remain composable.
- **Pairing**: the Deck typically *initiates* pairing. Default `PairModal` to `MethodPicker` and pre-select **Enter a code** when the user agent looks like a Deck.
- **No Emergency Stop in browser**: lockout must be triggered from the host machine, not from a remote browser session. The browser shell shows the Emergency Stop button as visible-but-disabled with tooltip explaining where to do it.
- **Reduced visual effects**: drop the radial-gradient backdrop and grid mask in the browser shell — they hurt Deck GPU. Solid `--bg-1` is enough.
- **Keyboard via on-screen**: when a `PinInput` cell focuses on the Deck, programmatically request the on-screen keyboard via Steam's web keyboard URI; close it on the final cell.

---

## Quick implementation checklist

- [ ] Token CSS file matching §11.
- [ ] `WindowChrome` + `Sidebar` shell with section routing + persisted active section.
- [ ] `OverviewScreen` with 1fr 1fr grid, draggable `EdgesCanvas`, compact `DeviceRow`s.
- [ ] `DevicesScreen` with full `DeviceRow` (inline `PermPill ×3` + Unpair) and `PairModal` state machine.
- [ ] `SettingsScreen` as one scroll, three labelled groups; **no edge dwell delay control**.
- [ ] `SecurityScreen` with This Device + Active Sessions (no perm matrix — perms live on Devices).
- [ ] `LogsScreen`.
- [ ] Emergency Stop banner integrated at the top of `Main`.
- [ ] Theme tokens: accent palette swap, density toggle, both via tweak/settings persistence.
- [ ] Reduced-motion + keyboard nav + focus ring across the board.
