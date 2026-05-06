# Screen Slickshift

A local-only Windows 11 companion server with a Steam Deck-friendly browser UI.

This is an MVP control panel, not a full KVM. It lets a Steam Deck browser send touchpad movement, clicks, text, approved macros, clipboard text, and file uploads to a Windows PC over your local LAN.

## Folder Guide

- `app/main.py`: FastAPI app, API routes, static UI serving, startup logging.
- `app/config.py`: Project paths, directory creation, pairing token creation, macro config loading.
- `app/security.py`: Shared-token checks for HTTP and WebSocket access.
- `app/input_control.py`: Windows mouse movement, clicking, scrolling, and text typing through `pyautogui`.
- `app/commands.py`: Loads and runs only pre-approved macros from `config/macros.json`.
- `app/clipboard.py`: Clipboard get/set helpers through `pyperclip`.
- `app/files.py`: Upload saving with filename sanitization and unique names.
- `app/websocket.py`: Live touchpad WebSocket event handler.
- `static/index.html`: Browser UI served to the Steam Deck.
- `static/app.js`: Browser-side token handling, API calls, touchpad events, upload logic.
- `static/style.css`: Steam Deck-friendly responsive styling.
- `config/macros.json`: Local allow-list of commands the browser may run.
- `uploads/`: Dedicated safe folder for uploaded files.
- `logs/`: Rotating server logs.
- `requirements.txt`: Python dependencies.
- `run.ps1`: Windows setup and run helper.

## Security Model

- Local LAN only. No cloud services, accounts, or telemetry.
- Browser requests require the pairing token printed in the Windows console on startup.
- The browser cannot send arbitrary shell commands.
- Macros are loaded only from `config/macros.json`.
- Commands run with `shell=False`.
- Upload filenames are sanitized and saved only under `uploads/`.
- CORS is closed by default because the UI is served from the same FastAPI origin.
- No admin privileges are required for the MVP.
- Emergency Stop disables input, clipboard, file upload, and macro actions until re-enabled.

Anyone on your LAN who has the token can control your PC. Keep the token private.

## Setup On Windows 11

Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/) or the Microsoft Store. During install, enable the option to add Python to PATH if you see it.

Open PowerShell in this folder:

```powershell
cd C:\Users\mavri\Documents\Codex\2026-05-05\you-are-gpt-codex-acting-as\screen_slickshift
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If `py` is not available, use:

```powershell
python -m venv .venv
```

If PowerShell blocks script activation, run this once for your user:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Run

For Steam Deck access over your LAN:

```powershell
.\run.ps1
```

By default, this starts:

```text
http://0.0.0.0:8765
```

`0.0.0.0` means the server listens on your PC's network interfaces. The Deck will use your PC's LAN IP address, not `0.0.0.0`.

For local-only PC testing:

```powershell
.\run.ps1 -HostAddress 127.0.0.1 -Port 8765
```

## Find Your Windows PC LAN IP

In PowerShell:

```powershell
ipconfig
```

Look for your active Wi-Fi or Ethernet adapter and copy the `IPv4 Address`, often something like:

```text
192.168.1.42
```

You can also use:

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -like "192.168.*" -or $_.IPAddress -like "10.*" -or $_.IPAddress -like "172.*"}
```

## Steam Deck URL

On the Steam Deck, open a browser to:

```text
http://YOUR_WINDOWS_PC_IP:8765
```

Example:

```text
http://192.168.1.42:8765
```

Enter the pairing token printed in the Windows PowerShell console.

## Test The MVP Features

1. Token access
   - Start the server.
   - Open the URL on the PC or Steam Deck.
   - Enter the printed pairing token and click Connect.
   - The activity log should say the touchpad connected.

2. Touchpad movement
   - Drag inside the Touchpad area.
   - The Windows mouse pointer should move.

3. Mouse clicks
   - Tap the touchpad area or press Left Click.
   - Press Right Click to open a context menu on Windows.

4. Send text
   - Open Notepad on Windows.
   - Click inside Notepad.
   - Type text in the web UI and press Send Text To PC.

5. Macros
   - Press Notepad, Calculator, or Lock PC.
   - These are loaded from `config/macros.json`.
   - Add new macros only by editing that file on the Windows PC.

6. Clipboard
   - Put text in the web UI Clipboard box and press Send To PC.
   - Paste on Windows with Ctrl+V.
   - Copy text on Windows, then press Get From PC.

7. File upload
   - Choose or drop a file in the File Drop panel.
   - Press Upload.
   - Confirm it appears in the `uploads/` folder.

8. Emergency Stop
   - Press Emergency Stop.
   - Input, clipboard, macro, and upload actions should be blocked.
   - Press Re-enable Control to resume.

## Editing Macros

Edit `config/macros.json`. Each macro must have an `id`, `label`, and `command` array:

```json
{
  "macros": [
    {
      "id": "open_notepad",
      "label": "Notepad",
      "description": "Open Windows Notepad",
      "command": ["notepad.exe"]
    },
    {
      "id": "open_calculator",
      "label": "Calculator",
      "description": "Open Calculator",
      "command": ["explorer.exe", "shell:AppsFolder\\Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"]
    }
  ]
}
```

Do not put user-provided text into `command`. Keep macros explicit and boring.

## Logs

Server logs are written to:

```text
logs/server.log
```

The console also prints startup information and errors.

## Troubleshooting

- Deck cannot open the page:
  - Make sure PC and Deck are on the same LAN.
  - Confirm the Windows IP with `ipconfig`.
  - Check Windows Defender Firewall. Allow Python on private networks if prompted.
  - Try opening `http://127.0.0.1:8765` on the Windows PC first.

- Token rejected:
  - Use the token printed by the current server run.
  - The saved token is in `config/pairing_token.txt`.
  - Delete that file and restart to generate a new token.

- Mouse does not move:
  - Make sure the server is running on the Windows desktop session you want to control.
  - Some elevated/admin windows may ignore non-admin input from a normal process.
  - Move the mouse to a screen corner to trigger `pyautogui` failsafe if needed.

- Text typing is weird:
  - `pyautogui.write` is simple keyboard simulation and works best with plain ASCII text.
  - For rich Unicode text, use the clipboard feature instead.

- Upload fails:
  - Check that the `uploads/` folder exists and is writable.
  - Try a small file first.

- Macro does not run:
  - Check `config/macros.json` for valid JSON.
  - Use a full executable path if Windows cannot find the program.
  - Check `logs/server.log` for the error.

## Next Practical Improvements

- Add HTTPS with a local certificate.
- Add QR-code pairing.
- Add per-session token rotation.
- Add keyboard shortcut buttons using pre-approved actions.
- Add drag sensitivity controls.
- Add download browsing for the upload folder.
