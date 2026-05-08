const state = {
  token: localStorage.getItem("wdcToken") || "",
  socket: null,
  reconnectTimer: null,
  reconnectStableTimer: null,
  reconnectAttempts: 0,
  heartbeatTimer: null,
  manualSocketClose: false,
  pointerLocked: false,
  lastPointer: null,
  selectedFile: null,
  lockout: false,
  device: null,
  trustedDevices: [],
  pairingSessions: [],
  pairingCode: null,
  maxUploadBytes: null,
  receiveDir: "",
  transfers: [],
};

const tokenInput = document.getElementById("tokenInput");
const saveTokenButton = document.getElementById("saveTokenButton");
const connectionStatus = document.getElementById("connectionStatus");
const activityLog = document.getElementById("activityLog");
const touchpad = document.getElementById("touchpad");
const textToSend = document.getElementById("textToSend");
const sendTextButton = document.getElementById("sendTextButton");
const macroList = document.getElementById("macroList");
const clipboardText = document.getElementById("clipboardText");
const getClipboardButton = document.getElementById("getClipboardButton");
const setClipboardButton = document.getElementById("setClipboardButton");
const fileInput = document.getElementById("fileInput");
const uploadButton = document.getElementById("uploadButton");
const dropZone = document.getElementById("dropZone");
const uploadLimitText = document.getElementById("uploadLimitText");
const uploadStatus = document.getElementById("uploadStatus");
const receiveDirInput = document.getElementById("receiveDirInput");
const saveReceiveDirButton = document.getElementById("saveReceiveDirButton");
const transferList = document.getElementById("transferList");
const clearTransfersButton = document.getElementById("clearTransfersButton");
const lockoutButton = document.getElementById("lockoutButton");
const captureCursorButton = document.getElementById("captureCursorButton");
const scrollSpeed = document.getElementById("scrollSpeed");
const scrollSpeedValue = document.getElementById("scrollSpeedValue");
const refreshSecurityButton = document.getElementById("refreshSecurityButton");
const localDevice = document.getElementById("localDevice");
const trustedDevices = document.getElementById("trustedDevices");
const pairingSessions = document.getElementById("pairingSessions");
const trustedCodeButton = document.getElementById("trustedCodeButton");
const guestCodeButton = document.getElementById("guestCodeButton");
const pairingCodeDisplay = document.getElementById("pairingCodeDisplay");
const revokeAllSessionsButton = document.getElementById("revokeAllSessionsButton");

tokenInput.value = state.token;

function log(message) {
  const time = new Date().toLocaleTimeString();
  activityLog.textContent = `[${time}] ${message}\n${activityLog.textContent}`;
}

function setStatus(message) {
  connectionStatus.textContent = message;
}

function authHeaders() {
  return { "X-Pairing-Token": state.token };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...authHeaders(),
      ...(options.headers || {}),
    },
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed: ${response.status}`);
  }
  return data;
}

function requireToken() {
  if (!state.token) {
    log("Enter the pairing token first.");
    return false;
  }
  return true;
}

function shortId(value) {
  if (!value) return "unknown";
  if (value.length <= 14) return value;
  return `${value.slice(0, 8)}...${value.slice(-4)}`;
}

function formatTime(seconds) {
  if (!seconds) return "never";
  const date = new Date(seconds * 1000);
  return date.toLocaleString();
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "unknown size";
  const units = ["bytes", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  if (unitIndex === 0) return `${value} ${units[unitIndex]}`;
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unitIndex]}`;
}

function selectedFileIsTooLarge() {
  return (
    state.selectedFile &&
    Number.isFinite(state.maxUploadBytes) &&
    state.selectedFile.size > state.maxUploadBytes
  );
}

function renderUploadState() {
  const limitText = Number.isFinite(state.maxUploadBytes)
    ? `Upload limit: ${formatBytes(state.maxUploadBytes)}`
    : "Upload limit unavailable";
  const folderText = state.receiveDir ? `Receive folder: ${state.receiveDir}` : "Receive folder unavailable";
  const fileText = state.selectedFile
    ? `Selected: ${state.selectedFile.name} (${formatBytes(state.selectedFile.size)})`
    : "No file selected";
  const tooLarge = selectedFileIsTooLarge();

  uploadLimitText.textContent = tooLarge
    ? `${fileText}. Exceeds ${formatBytes(state.maxUploadBytes)} limit.`
    : `${limitText}. ${fileText}. ${folderText}.`;
  uploadLimitText.classList.toggle("warning", Boolean(tooLarge));
  uploadButton.disabled = Boolean(tooLarge);
  if (tooLarge) {
    setUploadStatus(`File exceeds the ${formatBytes(state.maxUploadBytes)} upload limit.`, "error");
  }
}

function setUploadStatus(message, type = "neutral") {
  uploadStatus.textContent = message;
  uploadStatus.classList.toggle("success", type === "success");
  uploadStatus.classList.toggle("error", type === "error");
}

function recordTransfer({ name, bytes, status, detail }) {
  state.transfers.unshift({
    name,
    bytes,
    status,
    detail,
    time: new Date(),
  });
  state.transfers = state.transfers.slice(0, 6);
  renderTransfers();
}

function setServerTransfers(transfers = []) {
  state.transfers = transfers.map((transfer) => ({
    name: transfer.filename,
    bytes: transfer.bytes,
    status: transfer.status || "received",
    detail: transfer.detail || transfer.path || transfer.source || "",
    time: new Date(transfer.created_at * 1000),
  }));
  renderTransfers();
}

function renderTransfers() {
  if (!state.transfers.length) {
    transferList.innerHTML = '<div class="empty-state">No recent transfers.</div>';
    clearTransfersButton.disabled = true;
    return;
  }

  clearTransfersButton.disabled = false;
  transferList.innerHTML = state.transfers
    .map((transfer) => {
      const statusClass = transfer.status === "received" ? "success" : "error";
      return `
        <div class="transfer-item">
          <div class="item-title">
            <span>${escapeHtml(transfer.name)}</span>
            <span class="pill ${statusClass === "success" ? "enabled" : "warning"}">${escapeHtml(transfer.status)}</span>
          </div>
          <div class="item-meta">
            <span>${escapeHtml(formatBytes(transfer.bytes))}</span>
            <span>${escapeHtml(transfer.time.toLocaleTimeString())}</span>
            <span>${escapeHtml(transfer.detail || "")}</span>
          </div>
        </div>
      `;
    })
    .join("");
}

function formatPermissions(permissions = {}) {
  const entries = Object.entries(permissions);
  if (!entries.length) return "";
  return entries
    .map(([key, enabled]) => {
      const label = key.replaceAll("_", " ");
      return `<span class="pill ${enabled ? "enabled" : ""}">${enabled ? "On" : "Off"}: ${label}</span>`;
    })
    .join("");
}

function renderPermissionToggles(device) {
  const permissions = device.permissions || {};
  return Object.entries(permissions)
    .map(([key, enabled]) => {
      const label = key.replaceAll("_", " ");
      return `
        <label class="permission-toggle">
          <input
            type="checkbox"
            data-action="permission-toggle"
            data-device-id="${escapeHtml(device.device_id)}"
            data-permission="${escapeHtml(key)}"
            ${enabled ? "checked" : ""}
          />
          <span>${escapeHtml(label)}</span>
        </label>
      `;
    })
    .join("");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderSecurityState() {
  renderLocalDevice();
  renderTrustedDevices();
  renderPairingSessions();
  renderPairingCode();
}

function renderLocalDevice() {
  if (!state.device) {
    localDevice.innerHTML = '<div class="empty-state">Connect with the pairing token to load this device.</div>';
    return;
  }

  localDevice.innerHTML = `
    <div class="state-item">
      <div class="item-title">
        <span>${escapeHtml(state.device.name)}</span>
        <span class="pill enabled">${escapeHtml(state.device.os)}</span>
      </div>
      <div class="item-meta">
        <span>ID: ${escapeHtml(shortId(state.device.device_id))}</span>
        <span>Created: ${escapeHtml(formatTime(state.device.created_at))}</span>
      </div>
    </div>
  `;
}

function renderTrustedDevices() {
  if (!state.trustedDevices.length) {
    trustedDevices.innerHTML = '<div class="empty-state">No trusted devices.</div>';
    return;
  }

  trustedDevices.innerHTML = state.trustedDevices
    .map(
      (device) => `
        <div class="state-item ${device.review_required ? "review-required" : ""}">
          <div class="item-title">
            <span>${escapeHtml(device.name)}</span>
            ${device.review_required ? '<span class="pill warning">Review needed</span>' : '<span class="pill enabled">Trusted</span>'}
          </div>
          <div class="item-meta">
            <span>ID: ${escapeHtml(shortId(device.device_id))}</span>
            <span>Created: ${escapeHtml(formatTime(device.created_at))}</span>
            <span>Last seen: ${escapeHtml(formatTime(device.last_seen_at))}</span>
          </div>
          <div class="permission-toggle-list">${renderPermissionToggles(device)}</div>
          <div class="item-actions">
            ${
              device.review_required
                ? `<button class="secondary compact-button" type="button" data-action="keep-trust" data-device-id="${escapeHtml(device.device_id)}">Keep Trust</button>
                   <button class="text-danger compact-button" type="button" data-action="remove-review-trust" data-device-id="${escapeHtml(device.device_id)}">Remove Trust</button>`
                : ""
            }
            <button class="text-danger compact-button" type="button" data-action="remove-device" data-device-id="${escapeHtml(device.device_id)}">Remove Device</button>
          </div>
        </div>
      `
    )
    .join("");
}

function renderPairingSessions() {
  if (!state.pairingSessions.length) {
    pairingSessions.innerHTML = '<div class="empty-state">No active sessions.</div>';
    return;
  }

  pairingSessions.innerHTML = state.pairingSessions
    .map(
      (session) => `
        <div class="state-item">
          <div class="item-title">
            <span>${escapeHtml(shortId(session.device_id))}</span>
            <span class="pill ${session.guest ? "" : "enabled"}">${session.guest ? "Guest" : "Trusted"}</span>
          </div>
          <div class="item-meta">
            <span>Session: ${escapeHtml(shortId(session.session_id))}</span>
            <span>Last active: ${escapeHtml(formatTime(session.last_active_at))}</span>
            <span>Expires: ${escapeHtml(formatTime(session.expires_at))}</span>
            <span>Idle timeout: ${escapeHtml(session.idle_timeout_seconds)} seconds</span>
          </div>
          <div class="permission-list">${formatPermissions(session.permissions)}</div>
          <div class="item-actions">
            <button class="text-danger compact-button" type="button" data-action="revoke-session" data-session-id="${escapeHtml(session.session_id)}">Revoke Session</button>
          </div>
        </div>
      `
    )
    .join("");
}

function renderPairingCode() {
  if (!state.pairingCode) {
    pairingCodeDisplay.textContent = "No active code";
    return;
  }

  pairingCodeDisplay.innerHTML = `
    <div class="pairing-code-value">${escapeHtml(state.pairingCode.code)}</div>
    <div class="item-meta">
      <span>${state.pairingCode.guest ? "Guest" : "Trusted"} code</span>
      <span>Expires: ${escapeHtml(formatTime(state.pairingCode.expires_at))}</span>
    </div>
  `;
}

async function loadSecurityState() {
  if (!requireToken()) return;
  const [deviceData, trustedData, sessionData] = await Promise.all([
    api("/api/device"),
    api("/api/trusted-devices"),
    api("/api/pairing-sessions"),
  ]);
  state.device = deviceData.device;
  state.trustedDevices = trustedData.devices || [];
  state.pairingSessions = sessionData.sessions || [];
  renderSecurityState();
}

async function loadStatus() {
  const response = await fetch("/api/status");
  const data = await response.json();
  state.lockout = Boolean(data.disabled);
  state.maxUploadBytes = data.max_upload_bytes;
  lockoutButton.textContent = state.lockout ? "Re-enable Control" : "Emergency Stop";
  renderUploadState();
}

async function loadFileTransferSettings() {
  if (!requireToken()) return;
  const [settingsData, transferData] = await Promise.all([
    api("/api/file-transfer/settings"),
    api("/api/file-transfer/transfers"),
  ]);
  state.maxUploadBytes = settingsData.max_upload_bytes;
  state.receiveDir = settingsData.receive_dir || "";
  receiveDirInput.value = state.receiveDir;
  setServerTransfers(transferData.transfers || []);
  renderUploadState();
}

async function refreshSecurityState(message = "") {
  try {
    await loadSecurityState();
    if (message) log(message);
  } catch (error) {
    log(error.message);
  }
}

function connectSocket() {
  if (!requireToken()) return;
  if (
    state.socket &&
    (state.socket.readyState === WebSocket.OPEN || state.socket.readyState === WebSocket.CONNECTING)
  ) {
    return;
  }

  clearTimeout(state.reconnectTimer);
  state.manualSocketClose = false;

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${protocol}://${window.location.host}/ws/touchpad`;
  state.socket = new WebSocket(url);

  state.socket.addEventListener("open", () => {
    sendSocket({ type: "auth", token: state.token });
    setStatus("Connected");
    log("Touchpad connected.");
    startHeartbeat();
    clearTimeout(state.reconnectStableTimer);
    state.reconnectStableTimer = setTimeout(() => {
      if (state.socket && state.socket.readyState === WebSocket.OPEN) {
        state.reconnectAttempts = 0;
      }
    }, 3000);
  });

  state.socket.addEventListener("close", () => {
    clearTimeout(state.reconnectStableTimer);
    stopHeartbeat();
    setStatus("Disconnected");
    if (!state.manualSocketClose && state.token) {
      if (state.reconnectAttempts >= 5) {
        log("Touchpad disconnected. Reconnect paused; press Save / Connect to try again.");
        return;
      }
      log("Touchpad disconnected. Reconnecting...");
      scheduleReconnect();
    } else {
      log("Touchpad disconnected.");
    }
  });

  state.socket.addEventListener("error", () => {
    setStatus("Connection error");
    log("Touchpad connection error.");
  });
}

function scheduleReconnect() {
  clearTimeout(state.reconnectTimer);
  const delay = Math.min(15000, 1200 * 2 ** state.reconnectAttempts);
  state.reconnectAttempts += 1;
  state.reconnectTimer = setTimeout(() => {
    state.socket = null;
    connectSocket();
  }, delay);
}

function startHeartbeat() {
  stopHeartbeat();
  state.heartbeatTimer = setInterval(() => {
    sendSocket({ type: "ping" });
  }, 15000);
}

function stopHeartbeat() {
  clearInterval(state.heartbeatTimer);
  state.heartbeatTimer = null;
}

function resetSocket() {
  clearTimeout(state.reconnectTimer);
  clearTimeout(state.reconnectStableTimer);
  state.reconnectAttempts = 0;
  state.manualSocketClose = true;
  stopHeartbeat();
  if (state.socket) {
    state.socket.close();
    state.socket = null;
  }
}

function sendSocket(message) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) return;
  state.socket.send(JSON.stringify(message));
}

function scrollAmount(event) {
  const direction = event.deltaY < 0 ? 1 : -1;
  return direction * Number(scrollSpeed.value || 6);
}

function updatePointerLockUi() {
  state.pointerLocked = document.pointerLockElement === touchpad;
  touchpad.classList.toggle("locked", state.pointerLocked);
  captureCursorButton.textContent = state.pointerLocked ? "Release Cursor" : "Capture Cursor";
  touchpad.querySelector("span").textContent = state.pointerLocked
    ? "Cursor captured. Press Esc to release."
    : "Drag to move mouse";
}

async function togglePointerLock() {
  if (document.pointerLockElement === touchpad) {
    document.exitPointerLock();
    return;
  }

  if (!touchpad.requestPointerLock) {
    log("This browser does not support cursor capture.");
    return;
  }

  try {
    await touchpad.requestPointerLock();
  } catch (error) {
    log(`Cursor capture failed: ${error.message}`);
  }
}

async function loadMacros() {
  if (!requireToken()) return;
  macroList.innerHTML = "";
  const data = await api("/api/macros");

  for (const macro of data.macros) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = macro.label;
    button.title = macro.description || macro.label;
    button.addEventListener("click", async () => {
      try {
        await api("/api/macro", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: macro.id }),
        });
        log(`Ran macro: ${macro.label}`);
        connectSocket();
      } catch (error) {
        log(error.message);
      }
    });
    macroList.appendChild(button);
  }
}

async function createPairingCode(guest) {
  if (!requireToken()) return;
  try {
    const data = await api("/api/pairing-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guest }),
    });
    state.pairingCode = data;
    renderPairingCode();
    log(`Generated ${guest ? "guest" : "trusted"} pairing code.`);
  } catch (error) {
    log(error.message);
  }
}

async function removeTrustedDevice(deviceId) {
  try {
    const data = await api(`/api/trusted-devices/${encodeURIComponent(deviceId)}`, {
      method: "DELETE",
    });
    await refreshSecurityState(`Removed trusted device. Revoked ${data.revoked_sessions} session(s).`);
  } catch (error) {
    log(error.message);
  }
}

async function resolveTrustReview(deviceId, keepTrust) {
  try {
    const data = await api(`/api/trusted-devices/${encodeURIComponent(deviceId)}/trust-review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keep_trust: keepTrust }),
    });
    await refreshSecurityState(
      data.kept
        ? `Kept trusted device. Revoked ${data.revoked_sessions} session(s).`
        : `Removed trusted device. Revoked ${data.revoked_sessions} session(s).`
    );
  } catch (error) {
    log(error.message);
  }
}

async function updateTrustedDevicePermission(deviceId, permission, enabled) {
  try {
    const data = await api(`/api/trusted-devices/${encodeURIComponent(deviceId)}/permissions`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permissions: { [permission]: enabled } }),
    });
    await refreshSecurityState(
      `Updated ${permission.replaceAll("_", " ")} permission. Revoked ${data.revoked_sessions} session(s).`
    );
  } catch (error) {
    log(error.message);
    await refreshSecurityState();
  }
}

async function revokePairingSession(sessionId) {
  try {
    await api(`/api/pairing-sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    await refreshSecurityState("Revoked pairing session.");
  } catch (error) {
    log(error.message);
  }
}

async function revokeAllPairingSessions() {
  try {
    const data = await api("/api/pairing-sessions", {
      method: "DELETE",
    });
    await refreshSecurityState(`Revoked ${data.removed} pairing session(s).`);
  } catch (error) {
    log(error.message);
  }
}

saveTokenButton.addEventListener("click", async () => {
  state.token = tokenInput.value.trim();
  localStorage.setItem("wdcToken", state.token);
  resetSocket();
  try {
    await api("/api/auth/check");
    log("Pairing token accepted.");
    await loadMacros();
    await loadFileTransferSettings();
    await loadSecurityState();
    connectSocket();
  } catch (error) {
    log(error.message);
  }
});

refreshSecurityButton.addEventListener("click", () => {
  refreshSecurityState("Security state refreshed.");
});

trustedCodeButton.addEventListener("click", () => {
  createPairingCode(false);
});

guestCodeButton.addEventListener("click", () => {
  createPairingCode(true);
});

revokeAllSessionsButton.addEventListener("click", () => {
  revokeAllPairingSessions();
});

clearTransfersButton.addEventListener("click", async () => {
  if (!requireToken()) return;
  try {
    const data = await api("/api/file-transfer/transfers", {
      method: "DELETE",
    });
    state.transfers = [];
    renderTransfers();
    const message = `Cleared ${data.removed} transfer record(s).`;
    setUploadStatus(message, "success");
    log(message);
  } catch (error) {
    setUploadStatus(error.message, "error");
    log(error.message);
  }
});

saveReceiveDirButton.addEventListener("click", async () => {
  if (!requireToken()) return;
  try {
    const data = await api("/api/file-transfer/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: receiveDirInput.value.trim() }),
    });
    state.receiveDir = data.receive_dir || "";
    state.maxUploadBytes = data.max_upload_bytes;
    receiveDirInput.value = state.receiveDir;
    renderUploadState();
    setUploadStatus("Updated receive folder.", "success");
    log("Updated receive folder.");
  } catch (error) {
    setUploadStatus(error.message, "error");
    log(error.message);
  }
});

trustedDevices.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const deviceId = button.dataset.deviceId;
  if (!deviceId) return;

  if (button.dataset.action === "remove-device") {
    removeTrustedDevice(deviceId);
  } else if (button.dataset.action === "keep-trust") {
    resolveTrustReview(deviceId, true);
  } else if (button.dataset.action === "remove-review-trust") {
    resolveTrustReview(deviceId, false);
  }
});

trustedDevices.addEventListener("change", (event) => {
  const input = event.target.closest("input[data-action='permission-toggle']");
  if (!input) return;
  const deviceId = input.dataset.deviceId;
  const permission = input.dataset.permission;
  if (!deviceId || !permission) return;
  updateTrustedDevicePermission(deviceId, permission, input.checked);
});

pairingSessions.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action='revoke-session']");
  if (!button || !button.dataset.sessionId) return;
  revokePairingSession(button.dataset.sessionId);
});

touchpad.addEventListener("pointerdown", (event) => {
  if (state.pointerLocked) return;
  touchpad.setPointerCapture(event.pointerId);
  state.lastPointer = { x: event.clientX, y: event.clientY };
});

touchpad.addEventListener("pointermove", (event) => {
  if (state.pointerLocked) return;
  if (!state.lastPointer) return;
  const dx = event.clientX - state.lastPointer.x;
  const dy = event.clientY - state.lastPointer.y;
  state.lastPointer = { x: event.clientX, y: event.clientY };
  sendSocket({ type: "mouse_move", dx: dx * 1.3, dy: dy * 1.3 });
});

touchpad.addEventListener("pointerup", () => {
  state.lastPointer = null;
});

touchpad.addEventListener("click", () => {
  sendSocket({ type: "mouse_button", button: "left", down: true });
});

touchpad.addEventListener("contextmenu", (event) => {
  event.preventDefault();
  sendSocket({ type: "mouse_button", button: "right", down: true });
});

touchpad.addEventListener("wheel", (event) => {
  event.preventDefault();
  sendSocket({ type: "scroll", amount: scrollAmount(event) });
});

document.addEventListener(
  "wheel",
  (event) => {
    if (!state.pointerLocked) return;
    event.preventDefault();
    sendSocket({ type: "scroll", amount: scrollAmount(event) });
  },
  { passive: false }
);

document.addEventListener("mousemove", (event) => {
  if (!state.pointerLocked) return;
  sendSocket({ type: "mouse_move", dx: event.movementX * 1.3, dy: event.movementY * 1.3 });
});

document.addEventListener("pointerlockchange", updatePointerLockUi);

document.addEventListener("pointerlockerror", () => {
  log("Cursor capture was blocked by the browser.");
  updatePointerLockUi();
});

captureCursorButton.addEventListener("click", togglePointerLock);

scrollSpeed.addEventListener("input", () => {
  scrollSpeedValue.textContent = `${scrollSpeed.value}x`;
});

document.getElementById("leftClickButton").addEventListener("click", () => {
  sendSocket({ type: "mouse_button", button: "left", down: true });
});

document.getElementById("rightClickButton").addEventListener("click", () => {
  sendSocket({ type: "mouse_button", button: "right", down: true });
});

document.getElementById("middleClickButton").addEventListener("click", () => {
  sendSocket({ type: "mouse_button", button: "middle", down: true });
});

sendTextButton.addEventListener("click", async () => {
  try {
    await api("/api/send-text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: textToSend.value }),
    });
    log("Sent text to PC.");
  } catch (error) {
    log(error.message);
  }
});

getClipboardButton.addEventListener("click", async () => {
  try {
    const data = await api("/api/clipboard");
    clipboardText.value = data.text || "";
    log("Fetched clipboard from PC.");
  } catch (error) {
    log(error.message);
  }
});

setClipboardButton.addEventListener("click", async () => {
  try {
    await api("/api/clipboard", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: clipboardText.value }),
    });
    log("Sent clipboard to PC.");
  } catch (error) {
    log(error.message);
  }
});

fileInput.addEventListener("change", () => {
  state.selectedFile = fileInput.files[0] || null;
  renderUploadState();
  if (state.selectedFile) log(`Selected ${state.selectedFile.name} (${formatBytes(state.selectedFile.size)}).`);
});

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("drag-over");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("drag-over");
});

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("drag-over");
  state.selectedFile = event.dataTransfer.files[0] || null;
  renderUploadState();
  if (state.selectedFile) log(`Selected ${state.selectedFile.name} (${formatBytes(state.selectedFile.size)}).`);
});

uploadButton.addEventListener("click", async () => {
  if (!state.selectedFile && fileInput.files[0]) {
    state.selectedFile = fileInput.files[0];
  }
  if (!state.selectedFile) {
    const message = "Choose a file first.";
    setUploadStatus(message, "error");
    log(message);
    return;
  }
  if (selectedFileIsTooLarge()) {
    const message = `File exceeds the ${formatBytes(state.maxUploadBytes)} upload limit.`;
    setUploadStatus(message, "error");
    recordTransfer({
      name: state.selectedFile.name,
      bytes: state.selectedFile.size,
      status: "rejected",
      detail: "Too large",
    });
    log(message);
    return;
  }

  const form = new FormData();
  form.append("file", state.selectedFile);

  try {
    setUploadStatus("Uploading...", "neutral");
    const data = await api("/api/upload", { method: "POST", body: form });
    const message = `Uploaded ${data.file.filename} (${formatBytes(data.file.bytes)}).`;
    setUploadStatus(message, "success");
    recordTransfer({
      name: data.file.filename,
      bytes: data.file.bytes,
      status: "received",
      detail: state.receiveDir,
    });
    log(message);
  } catch (error) {
    setUploadStatus(error.message, "error");
    recordTransfer({
      name: state.selectedFile.name,
      bytes: state.selectedFile.size,
      status: "rejected",
      detail: error.message,
    });
    log(error.message);
  }
});

lockoutButton.addEventListener("click", async () => {
  try {
    state.lockout = !state.lockout;
    const data = await api("/api/lockout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ disabled: state.lockout }),
    });
    lockoutButton.textContent = data.disabled ? "Re-enable Control" : "Emergency Stop";
    log(data.disabled ? "Emergency lockout enabled." : "Control re-enabled.");
    await loadSecurityState();
  } catch (error) {
    log(error.message);
  }
});

loadStatus().catch((error) => {
  renderUploadState();
  log(`Could not load server status: ${error.message}`);
});
renderTransfers();

if (state.token) {
  connectSocket();
  loadMacros().catch((error) => log(error.message));
  loadFileTransferSettings().catch((error) => log(error.message));
  loadSecurityState().catch((error) => log(error.message));
} else {
  renderSecurityState();
}
