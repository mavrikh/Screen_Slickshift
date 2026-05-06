# Experimental Agents

This folder starts the move from a Steam Deck browser controller toward native cross-device control.

These agents are prototypes. They are intentionally small and manual.

## What Exists Now

```text
agents/
  macos_receiver.py
  send_test_input.py
```

### `macos_receiver.py`

Runs a small receiver server on macOS.

It accepts protocol messages over:

```text
ws://MAC_IP:8770/ws/input?token=TOKEN
```

Supported messages:

- `mouse_move`
- `mouse_button`
- `scroll`
- `ping`

It uses `pyautogui` to inject local mouse input on the Mac.

It does not capture the Mac screen.

### `send_test_input.py`

Sends a tiny test action to a receiver.

It can:

- Wiggle the mouse.
- Left click.
- Right click.
- Scroll.

It is not a global input capture tool. That is deliberate. We want to prove receiver behavior before adding global hooks.

## macOS Permissions

macOS may require Accessibility permission before `pyautogui` can control the mouse.

If prompted, grant permission only to the terminal or Python app you are using for this test.

Expected path:

```text
System Settings -> Privacy & Security -> Accessibility
```

Screen Recording should not be needed. Do not grant Screen Recording for this prototype.

## Safe Test Flow

On the Mac receiver:

```bash
cd /path/to/Screen_Slickshift
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m agents.macos_receiver --host 0.0.0.0 --port 8770
```

The receiver prints a pairing token.

On the sender machine:

```powershell
cd C:\path\to\Screen_Slickshift
.\.venv\Scripts\Activate.ps1
python -m agents.send_test_input --host MAC_IP --port 8770 --token TOKEN --action wiggle
```

Other test actions:

```powershell
python -m agents.send_test_input --host MAC_IP --token TOKEN --action click
python -m agents.send_test_input --host MAC_IP --token TOKEN --action right-click
python -m agents.send_test_input --host MAC_IP --token TOKEN --action scroll --scroll -40
```

## Finding The Mac IP

On macOS:

```bash
ipconfig getifaddr en0
```

If using Ethernet, try:

```bash
ipconfig getifaddr en1
```

You can also check:

```text
System Settings -> Wi-Fi -> Details -> IP Address
```

## Current Security Model

This prototype uses a temporary pairing token printed at startup.

Important constraints:

- Local LAN only.
- No cloud service.
- No screen capture.
- No arbitrary shell commands.
- No file transfer.
- No clipboard access yet.

The receiver should be stopped when not testing.

## Known Limitations

- This is not edge handoff yet.
- This is not a polished desktop app.
- This does not capture global input on Windows or macOS.
- This does not persist trusted devices.
- This does not use TLS yet.
- This only tests the receiver protocol path.

## Why This Step Matters

This proves the future architecture in the smallest possible way:

```text
sender -> local network -> receiver -> OS input injection
```

Once this works reliably, the next step can be a real Windows sender that captures mouse/keyboard events manually while a control mode is active.
