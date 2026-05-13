# cursor-bridge-test

Standalone test application for validating cross-machine mouse cursor movement between Slickshift host machines. This app is NOT Slickshift's production UI. It is the development and diagnostic vehicle described in `documents/mouse-integration-restart-brainstorm.md`.

Its sole purpose is producing observable, loggable evidence that each component of the KVM input pipeline works before integration into the main codebase.

## Requirements

- Python 3.10 or later
- PyQt6 6.6 or later
- pyautogui 0.9.54 or later (provisional, see requirements.txt for notes)
- macOS Accessibility permission granted to the Python process (required for cursor injection in later steps)

## Running the app

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

On Windows, replace the activate line with `.venv\Scripts\activate`.

## Status

**Step 1 of 8 -- scaffolded.** See `documents/mouse-integration-restart-brainstorm.md` Section 7 for the full build sequence.

What works at Step 1:
- PyQt6 window opens with three regions: status bar (top), canvas (center), log panel (bottom).
- The canvas dot tracks the real local cursor position via pyautogui polling at approximately 60 fps.
- The status bar shows the current cursor coordinates in logical pixels.
- The log panel displays static placeholder text.

What does NOT work yet:
- Transport (TCP socket): skeleton only, raises NotImplementedError.
- Capture (system-wide event tap): skeleton only, raises NotImplementedError.
- Injection (synthetic input): skeleton only, raises NotImplementedError.
- State machine (handoff logic): skeleton only, raises NotImplementedError.

## Build sequence checkpoints

| Step | Description | Status |
|------|-------------|--------|
| 1 | Skeleton on Mac A -- window + dot tracking | Done |
| 2 | Same app on Mac B or Windows | Not started |
| 3 | TCP handshake and display config exchange | Not started |
| 4 | One-way delta mirroring (no injection) | Not started |
| 5 | One-way injection (shadow mode) | Not started |
| 6 | Two-way handoff at screen edge | Not started |
| 7 | DPI normalization verified on real hardware | Not started |
| 8 | Failsafes (dead-man switch, idle timeout) | Not started |
