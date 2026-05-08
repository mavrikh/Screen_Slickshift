# Linux / SteamOS Native Options

This document records the current Phase 8 research decision. It is not an implementation plan for a native Linux agent yet.

## Current Decision

Do not build a native Linux/SteamOS input agent yet.

When the project reaches Linux receiver work, the safest first implementation target should be the desktop portal RemoteDesktop path on Wayland-capable desktops. That keeps input control user-mediated and closer to the platform security model.

Keep the current Steam Deck path browser-based until native Linux behavior has a clear test machine, permission story, and emergency-stop design.

## Why

Linux desktop input is split across Wayland, X11, kernel input devices, and desktop-specific policy. A single Linux input strategy will not behave equally across all sessions.

Wayland intentionally limits global input capture and injection. That is a good security boundary, and this project should not treat it as something to casually bypass.

## Options Reviewed

### XDG Desktop Portal RemoteDesktop

Primary future candidate for Wayland receiver input.

The portal API exposes remote desktop sessions and device selection for keyboard, pointer, and touchscreen control. The implementation normally involves user consent through the desktop environment.

Pros:

- Fits Wayland's permission model better than low-level device injection.
- Can request pointer, keyboard, and touchscreen device types.
- Better match for explicit, reversible control.
- Avoids requiring this project to invent a root/admin bypass.

Cons and unknowns:

- Desktop support can vary by compositor and portal backend.
- User prompts may make always-on edge handoff awkward.
- Needs real testing on KDE Plasma and SteamOS before coding against it.
- Clipboard and screen-cast integration exist in the broader portal family, but Screen Slickshift should keep screen capture out of scope unless explicitly requested later.

References:

- XDG Desktop Portal RemoteDesktop: https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html
- libportal overview: https://libportal.org/libportal.html

### uinput / libevdev

Possible advanced receiver path, not the default.

The Linux kernel `uinput` interface lets a userspace process create a virtual input device. Kernel docs recommend libevdev as a less error-prone wrapper for new software.

Pros:

- Can work below X11/Wayland in some setups.
- Useful for virtual mouse/keyboard receiver behavior.

Cons and unknowns:

- Usually needs access to `/dev/uinput`, group membership, udev rules, or elevated setup.
- Easy to create a dangerous always-on input injector if the app gets the permission model wrong.
- More system-specific than the browser and portal paths.
- Should require explicit local setup documentation and visible emergency stop behavior.

References:

- Linux kernel uinput docs: https://kernel.org/doc/html/latest/input/uinput.html
- Linux input userspace API: https://kernel.org/doc/html/latest/input/input_uapi.html

### X11 XTEST

Possible legacy fallback only for user-selected X11 sessions.

The XTEST extension can synthesize a limited set of keyboard, pointer, button, and motion events on X11.

Pros:

- Simple conceptually for X11-only sessions.
- Similar to what many automation tools historically use.

Cons and unknowns:

- X11-only; not suitable as the Linux default.
- Does not solve Wayland or SteamOS Game Mode.
- Should be treated as compatibility mode, not the primary future path.

Reference:

- XTEST extension protocol: https://x.org/releases/X11R7.7/doc/xextproto/xtest.html

### Browser Gamepad API

Potential Steam Deck sender-side enhancement, not a native receiver path.

The browser UI may later use controller/gamepad input to make the Steam Deck experience better. That would help the Deck act as a sender/control surface, but it does not provide native OS-level input injection on the target machine.

Pros:

- Fits the current browser-first Steam Deck path.
- Avoids native Linux permissions for sender-only controls.

Cons and unknowns:

- Browser support and controller mappings need device testing.
- Does not replace a Linux receiver implementation.

## Security Requirements For Any Linux Agent

- No root/admin assumption by default.
- No hidden background input capture.
- No arbitrary shell commands from remote clients.
- No screen capture, OCR, video, or remote desktop preview unless explicitly added later.
- The UI must show whether input receiving is enabled.
- Emergency stop must be visible and locally reachable.
- Clipboard and file transfer must remain separate permissions.
- Any uinput setup must be opt-in and documented as a system-level permission.

## Recommended Future Sequence

1. Keep Steam Deck support browser-based.
2. Test whether the portal RemoteDesktop path can receive pointer events on the target Linux desktop.
3. If portal behavior is usable, build a small receiver prototype around portal sessions.
4. Only after that, evaluate uinput as an explicit advanced fallback.
5. Treat XTEST as an X11 compatibility fallback only.

## Current Project Status

- No native Linux or SteamOS agent exists.
- No portal, uinput, libevdev, or XTEST code exists in the repo.
- Steam Deck support currently means using the browser UI.
