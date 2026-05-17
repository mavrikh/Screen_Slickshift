/**
 * Slickshift Browser Agent -- background service worker.
 *
 * Connects to the Slickshift desktop host over a loopback WebSocket
 * (ws://127.0.0.1:8765). Reconnects with exponential backoff when
 * the host is not running or drops the connection.
 *
 * Port 8765 was chosen to match the existing run_app.py HTTP server
 * port already in the Slickshift codebase. Change SLICKSHIFT_PORT if
 * you run the host on a different port.
 *
 * Message format (both directions): JSON objects.
 *   Host -> Extension: { id: <string>, command: <string>, ...params }
 *   Extension -> Host: { id: <string>, result: <any> }
 *                  or: { id: <string>, error: <string> }
 *
 * The `id` field is an opaque correlation token set by the host. The
 * extension echoes it back so the host can match replies to requests.
 */

const SLICKSHIFT_HOST = "127.0.0.1";
const SLICKSHIFT_PORT = 8765;
const SLICKSHIFT_WS_URL = `ws://${SLICKSHIFT_HOST}:${SLICKSHIFT_PORT}/browser-agent`;

// Backoff config: starts at 1 s, doubles each attempt, caps at 5 s.
// Low cap because the host is on loopback -- there is no network cost to
// retrying quickly, and a short cap means restarting the host does not
// require a manual extension reload.
const RECONNECT_INITIAL_MS = 1000;
const RECONNECT_MAX_MS = 5000;
const RECONNECT_FACTOR = 2;

let _socket = null;
let _reconnectDelay = RECONNECT_INITIAL_MS;
let _reconnectTimer = null;

// ---------------------------------------------------------------------------
// Connection lifecycle
// ---------------------------------------------------------------------------

function connect() {
  if (_socket && (_socket.readyState === WebSocket.OPEN ||
                  _socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  console.log(`[Slickshift] Connecting to ${SLICKSHIFT_WS_URL}`);
  _socket = new WebSocket(SLICKSHIFT_WS_URL);

  _socket.onopen = () => {
    console.log("[Slickshift] WebSocket connected.");
    _reconnectDelay = RECONNECT_INITIAL_MS;
  };

  _socket.onmessage = (event) => {
    handleMessage(event.data);
  };

  _socket.onerror = (err) => {
    // onerror fires before onclose; just log -- onclose handles reconnect.
    console.warn("[Slickshift] WebSocket error:", err);
  };

  _socket.onclose = (event) => {
    console.log(
      `[Slickshift] WebSocket closed (code=${event.code}). ` +
      `Reconnecting in ${_reconnectDelay} ms.`
    );
    _socket = null;
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  if (_reconnectTimer !== null) return;
  _reconnectTimer = setTimeout(() => {
    _reconnectTimer = null;
    connect();
    _reconnectDelay = Math.min(_reconnectDelay * RECONNECT_FACTOR, RECONNECT_MAX_MS);
  }, _reconnectDelay);
}

function sendToHost(payload) {
  if (_socket && _socket.readyState === WebSocket.OPEN) {
    _socket.send(JSON.stringify(payload));
  } else {
    console.warn("[Slickshift] Cannot send -- socket not open:", payload);
  }
}

// ---------------------------------------------------------------------------
// Command dispatch
// ---------------------------------------------------------------------------

async function handleMessage(raw) {
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch (e) {
    console.error("[Slickshift] Received non-JSON message:", raw);
    return;
  }

  const { id, command } = msg;
  if (!command) {
    console.warn("[Slickshift] Message missing 'command' field:", msg);
    return;
  }

  console.log(`[Slickshift] Command received: ${command} (id=${id})`);

  try {
    const result = await dispatch(command, msg);
    sendToHost({ id, result });
  } catch (err) {
    console.error(`[Slickshift] Error handling command '${command}':`, err);
    sendToHost({ id, error: String(err) });
  }
}

async function dispatch(command, msg) {
  switch (command) {
    case "list_tabs":
      return await handleListTabs();

    case "switch_to_tab":
      return await handleSwitchToTab(msg.tabId);

    case "get_cookies":
      return await handleGetCookies(msg.domain);

    case "wait_for_selector":
      return await handleWaitForSelector(msg.tabId, msg.selector, msg.timeoutMs);

    default:
      throw new Error(`Unknown command: ${command}`);
  }
}

// ---------------------------------------------------------------------------
// Command handlers
// ---------------------------------------------------------------------------

/**
 * list_tabs -- return all open tabs across all windows.
 * Result: [ { tabId, title, url }, ... ]
 */
async function handleListTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.map((tab) => ({
    tabId: tab.id,
    title: tab.title ?? "",
    url: tab.url ?? "",
  }));
}

/**
 * switch_to_tab -- activate a specific tab and focus its window.
 *
 * Two-step because chrome.tabs.update only makes the tab active within its
 * window; if that window is behind another window it stays invisible until
 * we also call chrome.windows.update to focus it. The tab query gives us
 * the windowId without a second round-trip.
 *
 * Result: { tabId, windowId, focused: true } on success.
 */
async function handleSwitchToTab(tabId) {
  if (typeof tabId !== "number") {
    throw new Error(`switch_to_tab: tabId must be a number, got ${typeof tabId}`);
  }

  // Activate the tab within its window.
  const updatedTab = await chrome.tabs.update(tabId, { active: true });

  // Focus the window that owns the tab so it comes to the foreground.
  await chrome.windows.update(updatedTab.windowId, { focused: true });

  return { tabId: updatedTab.id, windowId: updatedTab.windowId, focused: true };
}

/**
 * wait_for_selector -- watch a tab's DOM for a CSS selector to appear.
 *
 * Injects an async function into the target tab via chrome.scripting.executeScript.
 * The injected function uses a MutationObserver to detect when the selector
 * matches anything in the document, then resolves. It also sets a timeout
 * fallback so it always returns rather than hanging.
 *
 * The background handler wraps that with its own outer timeout so a hung tab
 * (e.g. navigated away, frozen) does not block the command indefinitely.
 *
 * Requires: "scripting" permission in manifest.json.
 *
 * Result (matched):   { matched: true,  timeMs: <elapsed ms> }
 * Result (timed out): { matched: false, timedOut: true }
 *
 * @param {number} tabId      - Tab to observe.
 * @param {string} selector   - CSS selector to wait for.
 * @param {number} timeoutMs  - Maximum wait time in milliseconds.
 */
async function handleWaitForSelector(tabId, selector, timeoutMs) {
  if (typeof tabId !== "number") {
    throw new Error(`wait_for_selector: tabId must be a number, got ${typeof tabId}`);
  }
  if (typeof selector !== "string" || selector.trim() === "") {
    throw new Error("wait_for_selector: selector must be a non-empty string");
  }
  if (typeof timeoutMs !== "number" || timeoutMs <= 0) {
    throw new Error("wait_for_selector: timeoutMs must be a positive number");
  }

  // We pass timeoutMs and selector to the injected function via args[].
  // The injected function is serialized, so it cannot close over outer variables --
  // everything it needs must come through the args array.
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [selector, timeoutMs],
    func: (sel, timeout) => {
      // Runs inside the page. Returns a Promise -- Chrome awaits it before
      // returning the result to executeScript.
      return new Promise((resolve) => {
        const start = Date.now();

        // If the element is already present, resolve immediately.
        if (document.querySelector(sel)) {
          resolve({ matched: true, timeMs: 0 });
          return;
        }

        const timer = setTimeout(() => {
          observer.disconnect();
          resolve({ matched: false, timedOut: true });
        }, timeout);

        const observer = new MutationObserver(() => {
          if (document.querySelector(sel)) {
            clearTimeout(timer);
            observer.disconnect();
            resolve({ matched: true, timeMs: Date.now() - start });
          }
        });

        // subtree + childList catches most dynamic content additions.
        // attributes catches cases where a hidden element becomes visible.
        observer.observe(document.documentElement, {
          subtree: true,
          childList: true,
          attributes: true,
        });
      });
    },
  });

  // executeScript returns an array of per-frame results. We only injected into
  // the main frame, so take the first result's value.
  if (!results || results.length === 0) {
    throw new Error("wait_for_selector: executeScript returned no results");
  }
  return results[0].result;
}

/**
 * get_cookies -- return all cookies the browser holds for a given domain.
 *
 * Uses chrome.cookies.getAll({ domain }) which matches the domain and all
 * subdomains. The result is the raw cookie array from the browser -- name,
 * value, domain, path, secure, httpOnly, etc. -- suitable for inspection or
 * export to the Slickshift host.
 *
 * Privacy note: this reads the user's own cookies on their own machine and
 * sends them over loopback to the Slickshift desktop app. No data leaves the
 * machine. The cookies permission + <all_urls> host permission are required
 * because the domain is not known at extension-install time.
 *
 * Requires: "cookies" permission + "<all_urls>" host_permission in manifest.json.
 * Result: [ { name, value, domain, path, secure, httpOnly, ... }, ... ]
 */
async function handleGetCookies(domain) {
  if (typeof domain !== "string" || domain.trim() === "") {
    throw new Error("get_cookies: domain must be a non-empty string");
  }

  const cookies = await chrome.cookies.getAll({ domain: domain.trim() });
  // Return the full cookie objects -- the host decides how much of each to use.
  return cookies;
}

// ---------------------------------------------------------------------------
// Service worker keep-alive
// ---------------------------------------------------------------------------

// MV3 kills idle service workers after ~30 s, which would silently drop the
// WebSocket and prevent reconnect (the onclose timer dies with the worker).
// chrome.alarms can wake a sleeping worker; we schedule one every 24 s and use
// the handler to call connect(), which is idempotent -- it no-ops when the
// socket is already open and reconnects when it is not.
const KEEPALIVE_ALARM = "slickshift-keepalive";
const KEEPALIVE_PERIOD_MIN = 0.4; // 24 seconds, comfortably under the ~30 s idle cap.

chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_PERIOD_MIN });

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === KEEPALIVE_ALARM) {
    connect();
  }
});

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

connect();
