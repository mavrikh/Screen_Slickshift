# Slickshift Browser Agent -- Chrome Extension

A Manifest V3 Chrome extension that acts as a local agent for the
Slickshift desktop host. The extension and host communicate over a
loopback WebSocket -- no traffic leaves the machine.

## Port choice

Default port: **8765**. This matches the port `run_app.py` already uses
for the Slickshift HTTP server, keeping both services on the same port
number family. If you change the port, update `SLICKSHIFT_PORT` in
`background.js` and `BROWSER_AGENT_PORT` in `slickshift/browser_agent/server.py`
to match.

## Privacy note

The host logs every command it receives and every response the extension
sends back. Tab titles and URLs appear in that log. Do not run this
extension if you do not want your open tabs hitting a local log file.

## How to load (unpacked, developer mode)

1. Open Chrome and go to `chrome://extensions`.
2. Enable "Developer mode" (toggle in the top-right corner).
3. Click "Load unpacked".
4. Select this folder (`browser-extension/`).
5. The extension loads. It will start connecting to `ws://127.0.0.1:8765/browser-agent` immediately.
6. Start the Slickshift host (`python -m slickshift.main`). The extension
   reconnects automatically once the host is running.

## Reconnect behavior

The background service worker attempts to reconnect with exponential
backoff: starts at 1 second, doubles each attempt, caps at 30 seconds.
This means it is safe to start the host before or after the browser --
the connection will establish within one backoff cycle either way.

## Supported commands (prototype)

| Command | Description |
|---|---|
| `list_tabs` | Returns `[{tabId, title, url}, ...]` for all open tabs. |

## Message format

Host to extension:
```json
{ "id": "<opaque string>", "command": "list_tabs" }
```

Extension to host (success):
```json
{ "id": "<opaque string>", "result": [ ... ] }
```

Extension to host (error):
```json
{ "id": "<opaque string>", "error": "Error message here" }
```

The `id` field is set by the host and echoed back by the extension.
The host uses it to correlate responses to requests.

## Files

```
browser-extension/
  manifest.json   -- Manifest V3 extension descriptor
  background.js   -- Service worker: WebSocket client, command dispatch
  README.md       -- This file
```
