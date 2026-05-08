# Edge Handoff Plan

This document starts Phase 9. It describes the intended behavior before any global input capture or edge detection code is added.

## Current Decision

Do not implement automatic edge handoff yet.

Use the manual sender and experimental receiver as the test bed until the handoff state machine, panic behavior, and visible control state are clear.

The pure configuration/state model now exists in `app/handoff.py`. It does not capture input, inspect screen edges, or send network traffic.

The first monitor layout model also exists in `app/handoff.py`. It represents draggable screen rectangles and derives possible handoff routes from adjacent screen edges. This is still only geometry; it does not read real monitor positions from the OS.

The first browser simulation surface exists at `/handoff`. It lets the user drag prototype screen rectangles, preview derived routes, arm/disarm the state machine, simulate an edge crossing, confirm handoff, and stop.

The same `/handoff` page now includes a manual remote mouse test panel. It can connect to another running Screen Slickshift app by host/IP, port, and remote token, then send movement, click, and scroll events through `/api/handoff/remote/*`. This is a manual proof path, not automatic edge detection.

The route preview can arm a derived route directly. This keeps the manual
handoff flow tied to the visible screen layout instead of requiring the target
and edge controls to be set separately.

Confirming a pending handoff now requires an active remote connection. When it
succeeds, the browser focuses the remote touchpad so the active remote surface
is visually and interactively clear.

Current Mac-Windows verification path:

1. Run the main app on both machines with `python run.py --host 0.0.0.0 --port 8765`.
2. Open `/handoff` on the sender machine.
3. Enter the sender machine's local token at the top of the page.
4. In Remote Mouse, enter the target machine LAN IP, port `8765`, and the target machine token.
5. Click Connect Remote.
6. Click Wiggle or drag inside Remote touchpad.

Expected result: the target machine mouse moves. This has been physically verified from Mac to Windows and Windows to Mac. Mac to Mac still needs physical verification.

The remote bridge now validates the target token over HTTP before opening the
remote WebSocket. When the target supports `/api/input/status`, the bridge also
checks that the target input backend is allowed and loadable before connecting.

## Goal

Let one computer intentionally hand mouse control to another paired device when the pointer crosses a configured screen edge.

This is not remote desktop. There should be no screen capture, screenshots, OCR, or video preview.

## Non-Goals

- No hidden global input capture.
- No keyboard capture until mouse handoff is reliable.
- No automatic discovery requirement in the first version.
- No cloud relay.
- No arbitrary shell commands.
- No screen preview.

## Required Concepts

### Local Device

The device physically receiving the user's mouse/keyboard input.

### Target Device

The paired device that may receive remote input after handoff.

### Edge Zone

A configured screen edge that can trigger handoff. Examples:

- left edge to MacBook
- right edge to desktop
- top edge to Steam Deck browser session

### Screen Layout

The final UI should include a layout tab where the local user can drag device
screens into their physical arrangement. The current model represents each
screen as a rectangle:

- `screen_id`
- `device_id`
- display name
- `x`, `y`, `width`, `height`
- primary flag

When rectangles touch with enough overlap, the model derives possible handoff
routes. For example, if a MacBook rectangle touches the right side of a Desktop
rectangle, the Desktop right edge can hand off into the MacBook left edge.

Screens that belong to the same device do not create remote handoff routes.
Screens should not overlap. The current model can report overlaps, and the
prototype `/handoff` drag UI blocks moves that would place one screen on top of
another.

Screen tiles should snap edge-to-edge when dragged near each other. The
prototype `/handoff` UI currently snaps nearby sides together while still
blocking overlap.

By default, releasing a dragged screen forces it to snap to the nearest valid
screen side. Turning Freeform mode off also snaps all screens inward to nearest
valid sides. The prototype Freeform mode disables forced snapping while still
allowing route preview to search over a larger distance for the nearest screen
on a side.

Individual monitor tiles can be excluded from edge routing. This keeps the
monitor visible in the arrangement while preventing it from becoming a handoff
edge.

The prototype can add multiple monitors to a device and can add machines up to a
current prototype cap of five devices in one layout.
Add Machine and Add Monitor must never create an overlapping tile. The prototype
places new screens on an available side of an existing screen and falls back to a
small search for free space if the preferred side is occupied.

The layout surface should support navigation independent of the screen tiles.
The prototype supports zoom in/out, mouse-wheel zoom, reset view, and dragging
empty layout space to pan around. Reset View fits the viewport around the
centerpoint and bounds of all screens instead of just returning to 100%.

### Armed State

Handoff should only happen when the sender is armed. If the sender is disarmed, edge crossing does nothing.

### Active Handoff

The sender is currently routing relative mouse movement, clicks, and scroll to the target device.

## Proposed State Machine

```text
idle
  -> armed
  -> pending_handoff
  -> active_remote
  -> returning
  -> idle
```

### idle

No remote input is being sent.

Allowed actions:

- arm handoff
- select target
- change edge configuration

### armed

The app watches for a configured edge crossing.

Allowed actions:

- disarm
- switch target
- enter `pending_handoff` after a deliberate edge gesture

### pending_handoff

Short confirmation window after the edge gesture.

Purpose:

- Avoid accidental handoff from grazing a screen edge.
- Give the UI a chance to show the target before input leaves the local machine.

Possible confirmation rules:

- hold at the edge for a short dwell time
- push beyond the edge twice
- require a modifier key later, only if keyboard capture is explicitly added

### active_remote

Mouse movement, click, and scroll events are sent to the target.

Required visible controls:

- current target name
- emergency stop
- return/disconnect button
- input allowed/blocked status from receiver

### returning

Remote input stops and local control is restored.

Return triggers:

- local panic/stop
- target disconnect
- receiver emergency lockout
- session expires
- user moves back across a configured return edge, once implemented

## Minimum Safe Behavior

Before automatic handoff exists, the project should support:

- Manual target selection.
- Manual start/stop.
- Receiver status preflight.
- Receiver emergency-disabled refusal.
- Visible active target.
- Local stop that does not depend on the receiver.

The current `agents/manual_sender.py` covers part of this in a CLI form. The main app now also covers it through `app/handoff_remote.py` and the `/handoff` browser page's manual remote touchpad.

## Panic / Stop Requirements

The first automatic sender must have at least two stop paths:

- A local UI stop button.
- A local keyboard or mouse panic path once global capture exists.

The receiver must keep its own emergency lockout. Sender stop and receiver lockout are separate protections.

## First Implementation Slice Later

When coding starts, keep the first slice small:

1. Add a handoff configuration model for target id and edge. Done in `app/handoff.py`.
2. Add a disabled-by-default sender state object. Done in `app/handoff.py`.
3. Add a monitor layout geometry model for future drag-arrange UI. Done in `app/handoff.py`.
4. Add a browser simulation surface for the layout and state machine. Started at `/handoff`.
5. Reuse manual sender connection/status checks. Started in `app/handoff_remote.py`.
6. Add tests for state transitions and layout route derivation without capturing real input. Started in `tests/test_handoff.py`.
7. Only then consider OS-specific pointer-edge detection.

## Open Questions

- What exact UI should arm/disarm handoff?
- Should edge dwell or double-push be the first accidental-trigger guard?
- How should multi-monitor layouts be represented?
- How should target return edges work when screen sizes differ?
- Which OS gets the first real edge detector: macOS first, or Windows once a Windows test machine is available?

## Current Project Status

- Pure edge handoff config/state code exists in `app/handoff.py`.
- Pure monitor layout geometry exists in `app/handoff.py`.
- Prototype browser simulation surface exists at `/handoff`.
- Prototype screen dragging blocks overlapping monitor tiles.
- Prototype remote mouse bridge exists in `app/handoff_remote.py`.
- `/handoff` can connect to another receiver and manually send mouse movement/click/scroll through a remote touchpad.
- Target receiver diagnostics exist at `/api/input/status`, and remote handoff uses them when available.
- Derived routes in `/handoff` can arm the handoff state directly.
- Confirming a pending handoff requires a connected remote target and focuses the remote touchpad.
- Mac to Windows remote mouse movement has been physically verified with both machines running the main app.
- Windows to Mac remote mouse movement has been physically verified with both machines running the main app.
- Mac to Mac remote mouse movement is expected to use the same path but still needs physical verification.
- `/handoff` has a Stop button and an Escape key local stop path for the visible browser handoff flow.
- Automatic edge detection and global input capture are still not implemented.
- Prototype screen dragging snaps nearby sides edge-to-edge.
- Prototype screen release forces snapping to the nearest valid side unless Freeform mode is enabled.
- Prototype monitor tiles can be excluded from edge routing.
- Prototype layout supports multiple monitors per device and up to five devices.
- Prototype layout surface supports zooming and panning.
- No global input capture code exists.
- No pointer-edge detector exists.
- No handoff network sender loop exists.
- Manual sender and experimental receiver are the current proving tools.
- Keyboard capture remains deferred.
