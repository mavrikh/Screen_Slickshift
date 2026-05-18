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
sends back. Tab titles, URLs, and cookie values appear in that log. Do not
run this extension if you do not want that data hitting a local log file.

The `get_cookies` command reads cookies from the user's own browser and sends
them over loopback (127.0.0.1) to the Slickshift desktop app running on the
same machine. No data is transmitted to any remote server.

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
backoff: starts at 1 second, doubles each attempt, caps at 5 seconds.
This means it is safe to start the host before or after the browser --
the connection will establish within one backoff cycle either way, and
restarting the host does not require a manual extension reload.

Manifest V3 service workers are killed after about 30 seconds of
inactivity, which would otherwise silently drop the WebSocket and
prevent reconnect (the onclose reconnect timer dies with the worker).
The extension uses a `chrome.alarms` keep-alive that fires every 24
seconds and calls `connect()` (idempotent: no-ops when the socket is
already open, reconnects when it is not). This keeps the worker alive
and self-heals the connection.

## Supported commands (prototype)

| Command | Params | Description |
|---|---|---|
| `list_tabs` | (none) | Returns `[{tabId, title, url}, ...]` for all open tabs across all windows. |
| `switch_to_tab` | `tabId: number` | Activates the given tab and focuses its window. Returns `{tabId, windowId, focused: true}`. |
| `get_cookies` | `domain: string` | Returns all cookies for the given domain (and subdomains). Requires `cookies` permission and `<all_urls>` host permission. |
| `wait_for_selector` | `tabId: number`, `selector: string`, `timeoutMs: number` | Waits for a CSS selector to appear in the tab's DOM. Returns `{matched: true, timeMs: N}` or `{matched: false, timedOut: true}`. Requires `scripting` permission. |
| `tab_cycle` | `direction: "next" \| "prev"` | Activates the next or previous tab in the focused window, wrapping at either end. Returns `{tabId, windowId, focused, direction, fromIndex, toIndex}`. |
| `open_url` | `url: string` | Opens the URL in a new tab. Validates scheme is http/https/ftp. Returns `{tabId}`. |
| `paste_text` | `text: string` | Inserts text into the active tab's currently focused input (input, textarea, or contenteditable). Returns `{pasted: true, elementType}` or `{pasted: false, reason}`. Requires `scripting` permission. |
| `click_selector` | `tabId: number`, `selector: string` | Experimental: finds the first element matching the selector and calls `element.click()`. Synthetic click (`isTrusted: false`); works for `<a href>` navigation and most simple click handlers, fails on widgets that check `isTrusted`. Returns `{clicked: true, tagName, href?}` or `{clicked: false, reason}`. Requires `scripting` permission. |

## Message format

Host to extension (examples):

```json
{ "id": "abc123", "command": "list_tabs" }
{ "id": "abc124", "command": "switch_to_tab", "tabId": 42 }
{ "id": "abc125", "command": "get_cookies", "domain": "example.com" }
{ "id": "abc126", "command": "wait_for_selector", "tabId": 42, "selector": "#submit-btn", "timeoutMs": 5000 }
```

Extension to host (success):
```json
{ "id": "<opaque string>", "result": <command-specific value> }
```

Extension to host (error):
```json
{ "id": "<opaque string>", "error": "Error message here" }
```

The `id` field is set by the host and echoed back by the extension.
The host uses it to correlate responses to requests.

## Permissions

| Permission | Reason |
|---|---|
| `tabs` | Enumerate tabs and query tab metadata for `list_tabs` and `switch_to_tab`. |
| `activeTab` | Reserved for future commands that need short-lived access to the currently active tab. |
| `cookies` | Read cookie store for `get_cookies`. |
| `scripting` | Inject content functions for `wait_for_selector`. |
| `<all_urls>` (host) | Required by the `cookies` API when the target domain is not known at install time. |

## Files

```
browser-extension/
  manifest.json   -- Manifest V3 extension descriptor
  background.js   -- Service worker: WebSocket client, command dispatch
  README.md       -- This file
```
