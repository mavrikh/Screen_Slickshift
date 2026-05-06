# Design Direction

This document captures the intended look and feel for the future desktop app.

The current browser control panel is an MVP surface. The final app should feel more like a calm native settings app for configuring local device control.

## Overall Feel

The app should feel:

- Native.
- Practical.
- Calm.
- Secure.
- Easy to understand.
- Dark-mode first.

It should borrow the clarity and spacing of modern system settings apps, especially the macOS mouse/settings style, without copying Apple branding or exact layouts.

The goal is not a flashy remote desktop product. The goal is a trustworthy local control utility.

## Visual Style

Preferred direction:

- Dark charcoal and graphite surfaces.
- Soft white primary text.
- Muted gray secondary text.
- Blue-green accent color for active states.
- Red only for emergency or destructive actions.
- Subtle borders.
- Quiet panels.
- Rounded corners, but not overly pill-shaped.
- Native-feeling controls: toggles, sliders, segmented controls, device cards.

Avoid:

- Marketing-style hero sections.
- Decorative gradients.
- Purple-heavy palettes.
- Oversized cards.
- Remote desktop video preview styling.
- Decorative blobs or ornamental backgrounds.

## Main App Structure

Future desktop app shell:

```text
Sidebar
  Devices
  Mouse & Keyboard
  Screen Edges
  Clipboard
  Security
  Logs

Main Panel
  Current section settings
```

The sidebar should make the app feel like a settings/control center, not a website.

## Important Screens

### Devices

Purpose:

- Show paired and available local devices.
- Pair a new device.
- Remove trusted devices.
- Show connection state.

Possible device states:

- Available.
- Paired.
- Connected.
- Controlling this device.
- Being controlled.
- Disabled.

### Mouse & Keyboard

Purpose:

- Configure input sharing behavior.
- Tune tracking and scrolling.
- Enable or disable keyboard sharing.

Possible controls:

- Tracking speed slider.
- Scroll speed slider.
- Natural scrolling toggle.
- Keyboard sharing toggle.
- Mouse button mapping.
- Pointer capture mode.
- Panic hotkey.

The panic hotkey should be configurable in the native app. It should be treated
as a local safety control, not as a remote command from another device.

### Screen Edges

Purpose:

- Configure how cursor handoff works between devices.

Possible controls:

- Screen arrangement diagram.
- Edge handoff toggle.
- Handoff delay.
- Return edge behavior.
- Manual hotkey fallback.

The screen arrangement should be simple and schematic. It should not show live screenshots of other devices by default.

### Clipboard

Purpose:

- Configure clipboard sharing as a separate permission.

Possible controls:

- Clipboard sharing toggle.
- Manual sync button.
- Text-only mode.
- Per-device permission.

Clipboard contents should not be shown in logs by default.

### Security

Purpose:

- Make trust and control state visible.

Possible controls:

- Trusted devices list.
- Pairing token/code.
- Revoke all sessions.
- Local LAN status.
- Permission toggles.
- Emergency Stop.

Security should be understandable without expert knowledge.

### Logs

Purpose:

- Show connection and control events without leaking sensitive content.

Should show:

- Pairing attempts.
- Device connected/disconnected.
- Control started/stopped.
- Emergency stop.
- Errors.

Should avoid:

- Full typed keystrokes.
- Clipboard contents.
- File contents.
- Tokens.

## Screen Privacy

The design should avoid implying remote desktop, screen recording, or screenshots.

Do not include:

- Live remote screen previews.
- Screenshot thumbnails.
- Camera/video feed elements.
- Screen recording controls.

If screen capture is ever added later, it must be:

- Explicitly enabled by the user.
- Clearly indicated while active.
- Controlled by a separate permission.
- Easy to disable quickly.

## Emergency Stop

Emergency Stop should be visually prominent but not constantly alarming.

Suggested behavior:

- Always reachable from the main app shell.
- Red action button.
- Clear disabled/control-paused state after activation.
- Local user can re-enable.
- Trusted-device emergency stops should show a local trust review prompt before
  that device can reconnect.

The trust review prompt should ask whether the stopped trusted device is still
allowed to connect in the future. The remote device should not be able to
dismiss or bypass this prompt.

## Product Language

Use plain language.

Prefer:

- "Mouse & Keyboard"
- "Screen Edges"
- "Trusted Devices"
- "Local LAN only"
- "Input sharing active"
- "Emergency Stop"

Avoid:

- Overly technical protocol terms in primary UI.
- Marketing slogans.
- Vague security claims.

## Mockup Notes

The first visual mockup established this direction:

- Dark settings-style window.
- Left sidebar.
- Main Mouse & Keyboard panel.
- Paired device tiles for Windows PC, MacBook, and Steam Deck.
- Simple display arrangement diagram with edge handoff arrows.
- Sliders and toggles for practical settings.
- Compact security status card.
- Prominent Emergency Stop button.

Treat that mockup as a design reference, not a strict final layout.
