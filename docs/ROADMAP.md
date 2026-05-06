# Screen Slickshift Roadmap

This project started as a Steam Deck browser control panel for a Windows PC. The long-term goal is a local-only, cross-platform input sharing app for Windows, macOS, and Linux/SteamOS.

The project should move slowly, with each phase producing something testable.

## Current MVP

The current app is a Windows FastAPI server with a Steam Deck-friendly browser UI.

It supports:

- Token-protected browser access.
- Pointer/touchpad movement.
- Mouse clicks.
- Scroll forwarding.
- Text sending.
- Clipboard get/set.
- Approved macros.
- File uploads to a safe folder.
- Emergency lockout.

This remains useful as the first controller surface, but it is not enough for full cross-platform mouse sharing.

## Long-Term Goal

Run an agent on each device so one device can control another over the local network.

Target platforms:

- Windows.
- macOS.
- Linux, especially SteamOS/Wayland where practical.

Desired behavior:

- Pair devices on the local network.
- Share mouse and keyboard control.
- Eventually move control by crossing the edge of a screen.
- Optionally sync clipboard.
- Keep file transfer optional and permission-controlled.
- Avoid cloud services, accounts, telemetry, and remote servers.

## Phase 1: Polish Current Browser Controller

Improve the existing Windows server and Steam Deck browser UI.

Possible next features:

- Mouse sensitivity slider.
- Scroll direction toggle.
- Keyboard shortcut panel.
- Saved browser preferences.
- Better connection status.
- Better emergency stop state.
- Gamepad API experiments for Steam Deck controls.

## Phase 2: Shared Protocol

Define the local message format used by agents and browser clients.

The protocol should support:

- Device hello/status.
- Pairing.
- Mouse move.
- Mouse button down/up.
- Scroll.
- Key down/up.
- Text input.
- Clipboard get/set.
- Emergency stop.
- Permission negotiation.

The protocol should stay simple and readable at first.

## Phase 3: Windows Sender Agent

Build a Windows agent that can send input events to another paired device.

First version:

- Manual start/stop.
- Select one receiver.
- Send relative mouse movement.
- Send basic mouse buttons.
- Send basic keyboard events.
- Include a panic hotkey or emergency stop.

No edge-of-screen handoff yet.

The panic hotkey should be configurable later, but the first implementation can
use a conservative default while the safety behavior is proven.

## Phase 4: macOS Receiver Agent

Build a macOS receiver that accepts paired input events and injects them into macOS.

Expected macOS permissions:

- Accessibility permission for input injection.
- Input Monitoring may be needed later for capture.
- Local Network permission may appear depending on packaging.

First milestone:

```text
Windows controls macOS mouse and keyboard over LAN.
```

Current experimental scaffold:

- `agents/macos_receiver.py`
- `agents/send_test_input.py`

This scaffold proves protocol input injection without global input capture, edge handoff, screen capture, clipboard sync, or file transfer.

## Phase 5: Pairing And Security

Before this becomes a regular tool, add a stronger pairing model.

Features:

- Device identity.
- Trusted-device allow-list.
- Pairing token or short code.
- Session tokens.
- Permission toggles per device.
- Clear logs.
- Visible control-enabled state.
- Emergency disable from either side where possible.

## Phase 6: Clipboard

Add clipboard sharing as a separate permission.

Preferred path:

- Text-only first.
- Manual sync first.
- Automatic sync only if the user enables it.
- Clear indication when clipboard sharing is active.

## Phase 7: Edge Handoff

After manual control works reliably:

- Configure screen arrangement.
- Detect when the controller cursor reaches an edge.
- Hand off relative input to the target device.
- Return control when moving back across the opposite edge or pressing a hotkey.

## Phase 8: Linux / SteamOS

SteamOS mostly means Wayland, which is intentionally restrictive for global input capture and injection.

Possible paths to investigate later:

- Keep browser mode for Steam Deck as the main controller.
- Use Gamepad API inside the browser.
- Explore KDE/Wayland portals.
- Explore `uinput` for virtual input where appropriate.
- Support X11 if the user chooses an X11 session.

Wayland support should be researched carefully before committing to a native Linux agent design.

## Future Exploration: Game Controllers And Android

After the core desktop app is working, investigate game controller sharing
between trusted devices.

Goals to keep in mind:

- Let a paired controller follow the trusted device relationship where possible.
- Avoid forcing the user to re-sync or re-pair the controller every time they
  switch target devices.
- Keep controller access permissioned separately from mouse and keyboard input.
- Make disconnect, emergency stop, and local control state visible.

Possible Android support should also be researched later. Treat it as a separate
platform effort, not an assumption in the first Windows/macOS/Linux prototype.

Open questions:

- Which platforms allow virtual game controller injection without elevated
  privileges or kernel drivers?
- Whether controller forwarding should use native APIs, virtual HID devices, or
  a different approach per platform.
- Whether Android is a controller source, receiver, or both.
- How Bluetooth pairing and local-network trusted-device pairing should relate
  without surprising the user.

## Phase 9: Desktop App Packaging

Once behavior is proven, decide whether to keep Python or move to a polished app stack.

Possible app stacks:

- Python prototype with packaging.
- Tauri plus Rust for a lighter native desktop app.
- Electron plus native helpers.

The decision should happen after the protocol and first cross-device prototype are proven.
