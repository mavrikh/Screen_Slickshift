const state = {
  token: localStorage.getItem("wdcToken") || "",
  selectedFile: null,
  maxUploadBytes: null,
  receiveDir: "",
  transfers: [],
  targets: [],
};

const tokenInput = document.getElementById("tokenInput");
const connectButton = document.getElementById("connectButton");
const fileTransferStatus = document.getElementById("fileTransferStatus");
const receiveDirInput = document.getElementById("receiveDirInput");
const saveReceiveDirButton = document.getElementById("saveReceiveDirButton");
const dropZone = document.getElementById("dropZone");
const fileInput = document.getElementById("fileInput");
const uploadLimitText = document.getElementById("uploadLimitText");
const uploadStatus = document.getElementById("uploadStatus");
const uploadButton = document.getElementById("uploadButton");
const transferList = document.getElementById("transferList");
const clearTransfersButton = document.getElementById("clearTransfersButton");
const targetList = document.getElementById("targetList");
const refreshTargetsButton = document.getElementById("refreshTargetsButton");

tokenInput.value = state.token;

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
  if (!response.ok) throw new Error(data.detail || `Request failed: ${response.status}`);
  return data;
}

function setStatus(message) {
  fileTransferStatus.textContent = message;
}

function requireToken() {
  if (!state.token) {
    setUploadStatus("Enter the pairing token first.", "error");
    return false;
  }
  return true;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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

function setUploadStatus(message, type = "neutral") {
  uploadStatus.textContent = message;
  uploadStatus.classList.toggle("success", type === "success");
  uploadStatus.classList.toggle("error", type === "error");
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

function setTransfers(transfers = []) {
  state.transfers = transfers.map((transfer) => ({
    name: transfer.filename,
    bytes: transfer.bytes,
    status: transfer.status || "received",
    detail: transfer.detail || transfer.path || transfer.source || "",
    time: new Date(transfer.created_at * 1000),
  }));
  renderTransfers();
}

function setTargets(targets = []) {
  state.targets = targets;
  renderTargets();
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
      const statusClass = transfer.status === "received" ? "enabled" : "warning";
      return `
        <div class="transfer-item">
          <div class="item-title">
            <span>${escapeHtml(transfer.name)}</span>
            <span class="pill ${statusClass}">${escapeHtml(transfer.status)}</span>
          </div>
          <div class="item-meta">
            <span>${escapeHtml(formatBytes(transfer.bytes))}</span>
            <span>${escapeHtml(transfer.time.toLocaleTimeString())}</span>
            <span>${escapeHtml(transfer.detail)}</span>
          </div>
        </div>
      `;
    })
    .join("");
}

function renderTargets() {
  if (!state.targets.length) {
    targetList.innerHTML = '<div class="empty-state">No trusted recipients.</div>';
    return;
  }

  targetList.innerHTML = state.targets
    .map((target) => {
      const fileReceive = Boolean(target.permissions && target.permissions.file_receive);
      const reviewRequired = Boolean(target.review_required);
      const canReceive = Boolean(target.can_receive_files);
      const statusText = canReceive ? "ready" : target.receive_blocked_reason || "not ready";
      const statusClass = canReceive ? "enabled" : "warning";
      return `
        <div class="state-item ${reviewRequired ? "review-required" : ""}">
          <div class="item-title">
            <span>${escapeHtml(target.name)}</span>
            <span class="pill ${statusClass}">${escapeHtml(statusText)}</span>
          </div>
          <div class="item-meta">
            <span>ID: ${escapeHtml(target.device_id)}</span>
            <span>Last seen: ${escapeHtml(target.last_seen_at ? new Date(target.last_seen_at * 1000).toLocaleString() : "never")}</span>
          </div>
          <label class="permission-toggle">
            <input
              type="checkbox"
              data-action="file-receive-toggle"
              data-device-id="${escapeHtml(target.device_id)}"
              ${fileReceive ? "checked" : ""}
              ${reviewRequired ? "disabled" : ""}
            />
            <span>File receive</span>
          </label>
        </div>
      `;
    })
    .join("");
}

async function loadFileTransferState() {
  if (!requireToken()) return;
  const [settingsData, transferData, targetData] = await Promise.all([
    api("/api/file-transfer/settings"),
    api("/api/file-transfer/transfers"),
    api("/api/file-transfer/targets"),
  ]);
  state.maxUploadBytes = settingsData.max_upload_bytes;
  state.receiveDir = settingsData.receive_dir || "";
  receiveDirInput.value = state.receiveDir;
  setTransfers(transferData.transfers || []);
  setTargets(targetData.targets || []);
  renderUploadState();
  setStatus("Connected");
}

connectButton.addEventListener("click", async () => {
  state.token = tokenInput.value.trim();
  localStorage.setItem("wdcToken", state.token);
  try {
    await api("/api/auth/check");
    await loadFileTransferState();
    setUploadStatus("Ready.", "success");
  } catch (error) {
    setStatus("Disconnected");
    setUploadStatus(error.message, "error");
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
  } catch (error) {
    setUploadStatus(error.message, "error");
  }
});

fileInput.addEventListener("change", () => {
  state.selectedFile = fileInput.files[0] || null;
  renderUploadState();
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
});

uploadButton.addEventListener("click", async () => {
  if (!state.selectedFile && fileInput.files[0]) {
    state.selectedFile = fileInput.files[0];
  }
  if (!state.selectedFile) {
    setUploadStatus("Choose a file first.", "error");
    return;
  }
  if (selectedFileIsTooLarge()) {
    setUploadStatus(`File exceeds the ${formatBytes(state.maxUploadBytes)} upload limit.`, "error");
    return;
  }

  const form = new FormData();
  form.append("file", state.selectedFile);

  try {
    setUploadStatus("Uploading...", "neutral");
    const data = await api("/api/upload", { method: "POST", body: form });
    setUploadStatus(`Uploaded ${data.file.filename} (${formatBytes(data.file.bytes)}).`, "success");
    await loadFileTransferState();
  } catch (error) {
    setUploadStatus(error.message, "error");
    await loadFileTransferState().catch(() => {});
  }
});

clearTransfersButton.addEventListener("click", async () => {
  if (!requireToken()) return;
  try {
    const data = await api("/api/file-transfer/transfers", { method: "DELETE" });
    setTransfers([]);
    setUploadStatus(`Cleared ${data.removed} transfer record(s).`, "success");
  } catch (error) {
    setUploadStatus(error.message, "error");
  }
});

targetList.addEventListener("change", async (event) => {
  const input = event.target.closest("input[data-action='file-receive-toggle']");
  if (!input || !input.dataset.deviceId) return;
  if (!requireToken()) return;
  try {
    await api(`/api/trusted-devices/${encodeURIComponent(input.dataset.deviceId)}/permissions`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permissions: { file_receive: input.checked } }),
    });
    await loadFileTransferState();
    setUploadStatus("Updated recipient file receive permission.", "success");
  } catch (error) {
    setUploadStatus(error.message, "error");
    await loadFileTransferState().catch(() => {});
  }
});

refreshTargetsButton.addEventListener("click", async () => {
  if (!requireToken()) return;
  try {
    const data = await api("/api/file-transfer/targets");
    setTargets(data.targets || []);
    setUploadStatus("Recipient list refreshed.", "success");
  } catch (error) {
    setUploadStatus(error.message, "error");
  }
});

renderTransfers();
renderTargets();
renderUploadState();
if (state.token) {
  loadFileTransferState().catch((error) => {
    setStatus("Disconnected");
    setUploadStatus(error.message, "error");
  });
}
