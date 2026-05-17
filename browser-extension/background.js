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

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

connect();
