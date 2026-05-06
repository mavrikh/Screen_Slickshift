const state = {
  token: localStorage.getItem("wdcToken") || "",
  socket: null,
  reconnectTimer: null,
  heartbeatTimer: null,
  manualSocketClose: false,
  pointerLocked: false,
  lastPointer: null,
  selectedFile: null,
  lockout: false,
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
const lockoutButton = document.getElementById("lockoutButton");
const captureCursorButton = document.getElementById("captureCursorButton");
const scrollSpeed = document.getElementById("scrollSpeed");
const scrollSpeedValue = document.getElementById("scrollSpeedValue");

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
  const url = `${protocol}://${window.location.host}/ws/touchpad?token=${encodeURIComponent(state.token)}`;
  state.socket = new WebSocket(url);

  state.socket.addEventListener("open", () => {
    setStatus("Connected");
    log("Touchpad connected.");
    startHeartbeat();
  });

  state.socket.addEventListener("close", () => {
    stopHeartbeat();
    setStatus("Disconnected");
    if (!state.manualSocketClose && state.token) {
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
  state.reconnectTimer = setTimeout(() => {
    state.socket = null;
    connectSocket();
  }, 1200);
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

saveTokenButton.addEventListener("click", async () => {
  state.token = tokenInput.value.trim();
  localStorage.setItem("wdcToken", state.token);
  resetSocket();
  try {
    await api("/api/auth/check");
    log("Pairing token accepted.");
    await loadMacros();
    connectSocket();
  } catch (error) {
    log(error.message);
  }
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
  if (state.selectedFile) log(`Selected ${state.selectedFile.name}`);
});

uploadButton.addEventListener("click", async () => {
  if (!state.selectedFile && fileInput.files[0]) {
    state.selectedFile = fileInput.files[0];
  }
  if (!state.selectedFile) {
    log("Choose a file first.");
    return;
  }

  const form = new FormData();
  form.append("file", state.selectedFile);

  try {
    const data = await api("/api/upload", { method: "POST", body: form });
    log(`Uploaded ${data.file.filename} (${data.file.bytes} bytes).`);
  } catch (error) {
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
  } catch (error) {
    log(error.message);
  }
});

if (state.token) {
  connectSocket();
  loadMacros().catch((error) => log(error.message));
}
