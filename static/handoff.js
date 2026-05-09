const handoffState = {
  token: localStorage.getItem("wdcToken") || "",
  remoteHost: localStorage.getItem("slickshiftHandoffRemoteHost") || "",
  remotePort: localStorage.getItem("slickshiftHandoffRemotePort") || "8765",
  remoteToken: localStorage.getItem("slickshiftHandoffRemoteToken") || "",
  mode: "idle",
  activeTargetId: null,
  activeEdge: null,
  notice: "Handoff is idle.",
  screens: [
    {
      screen_id: "local-main",
      device_id: "local",
      name: "This Mac",
      rect: { x: 80, y: 110, width: 360, height: 220 },
      primary: true,
      edge_enabled: true,
    },
    {
      screen_id: "target-main",
      device_id: "target",
      name: "Target Mac",
      rect: { x: 440, y: 130, width: 300, height: 190 },
      primary: true,
      edge_enabled: true,
    },
  ],
  selectedScreenId: "target-main",
  freeformScreens: false,
  routes: [],
  overlaps: [],
  dragging: null,
  panning: null,
  detectorPollTimer: null,
  returnPollTimer: null,
  dwellProgress: null,
  screenWidth: null,
  screenHeight: null,
  realDims: {},
  remoteConnected: false,
  remoteTargetLabel: "",
  remoteDragging: false,
  activeLayerDragging: false,
  pointerLocked: false,
  lastRemoteMoveAt: 0,
  viewport: { x: 0, y: 0, scale: 1 },
  localMonitors: [],
  discoveryAdvertising: false,
  discoveredDevices: [],
  discoveryPollTimer: null,
  discoverySearchStart: null,
  pairingState: {
    step: "idle", // idle | requesting | waiting_pin | submitting
    target: null,
    expiresAt: null,
    countdownTimer: null,
  },
  pendingPairExpiresAt: null,
  pendingPairCountdownTimer: null,
  activeTab: "state",
};

const OPPOSITE_EDGE = { left: "right", right: "left", top: "bottom", bottom: "top" };

const handoffTokenInput = document.getElementById("handoffTokenInput");
const handoffConnectButton = document.getElementById("handoffConnectButton");
const handoffStatus = document.getElementById("handoffStatus");
const handoffStopButton = document.getElementById("handoffStopButton");
const layoutCanvas = document.getElementById("layoutCanvas");
const layoutWorld = document.getElementById("layoutWorld");
const resetLayoutButton = document.getElementById("resetLayoutButton");
const resetViewButton = document.getElementById("resetViewButton");
const zoomOutButton = document.getElementById("zoomOutButton");
const zoomInButton = document.getElementById("zoomInButton");
const freeformButton = document.getElementById("freeformButton");
const addMachineButton = document.getElementById("addMachineButton");
const addMonitorButton = document.getElementById("addMonitorButton");
const toggleEdgeButton = document.getElementById("toggleEdgeButton");
const selectedScreenText = document.getElementById("selectedScreenText");
const handoffModeText = document.getElementById("handoffModeText");
const handoffTargetText = document.getElementById("handoffTargetText");
const handoffEdgeText = document.getElementById("handoffEdgeText");
const localScreenText = document.getElementById("localScreenText");
const dwellProgressItem = document.getElementById("dwellProgressItem");
const dwellProgressFill = document.getElementById("dwellProgressFill");
const targetSelect = document.getElementById("targetSelect");
const edgeSelect = document.getElementById("edgeSelect");
const armHandoffButton = document.getElementById("armHandoffButton");
const disarmHandoffButton = document.getElementById("disarmHandoffButton");
const simulateEdgeButton = document.getElementById("simulateEdgeButton");
const confirmHandoffButton = document.getElementById("confirmHandoffButton");
const handoffNotice = document.getElementById("handoffNotice");
const refreshRoutesButton = document.getElementById("refreshRoutesButton");
const routeList = document.getElementById("routeList");
const remoteHostInput = document.getElementById("remoteHostInput");
const remotePortInput = document.getElementById("remotePortInput");
const remoteTokenInput = document.getElementById("remoteTokenInput");
const remoteConnectButton = document.getElementById("remoteConnectButton");
const remoteDisconnectButton = document.getElementById("remoteDisconnectButton");
const goActiveButton = document.getElementById("goActiveButton");
const remoteMousePad = document.getElementById("remoteMousePad");
const remoteStatusText = document.getElementById("remoteStatusText");
const remoteWiggleButton = document.getElementById("remoteWiggleButton");
const remoteLeftClickButton = document.getElementById("remoteLeftClickButton");
const remoteRightClickButton = document.getElementById("remoteRightClickButton");
const activeHandoffLayer = document.getElementById("activeHandoffLayer");
const activeHandoffStatus = document.getElementById("activeHandoffStatus");
const activeLayerCaptureButton = document.getElementById("activeLayerCaptureButton");
const activeLayerLeftClickButton = document.getElementById("activeLayerLeftClickButton");
const activeLayerRightClickButton = document.getElementById("activeLayerRightClickButton");
const activeLayerStopButton = document.getElementById("activeLayerStopButton");
const discoveryToggleButton = document.getElementById("discoveryToggleButton");
const discoveryList = document.getElementById("discoveryList");
const pairModal = document.getElementById("pairModal");
const pairModalTargetName = document.getElementById("pairModalTargetName");
const pairCountdownText = document.getElementById("pairCountdownText");
const pairPinInput = document.getElementById("pairPinInput");
const permMouseCheck = document.getElementById("permMouseCheck");
const permKeyboardCheck = document.getElementById("permKeyboardCheck");
const pairRememberCheck = document.getElementById("pairRememberCheck");
const pairModalError = document.getElementById("pairModalError");
const pairConnectButton = document.getElementById("pairConnectButton");
const pairCancelButton = document.getElementById("pairCancelButton");
const pendingPairModal = document.getElementById("pendingPairModal");
const pendingRequesterName = document.getElementById("pendingRequesterName");
const pendingPairCountdown = document.getElementById("pendingPairCountdown");
const pendingPairCode = document.getElementById("pendingPairCode");
const pendingDismissButton = document.getElementById("pendingDismissButton");
const discoveryBadge = document.getElementById("discoveryBadge");
const remoteArmButton = document.getElementById("remoteArmButton");
const remotePreviewRoutesButton = document.getElementById("remotePreviewRoutesButton");
const remoteRouteHint = document.getElementById("remoteRouteHint");

handoffTokenInput.value = handoffState.token;
remoteHostInput.value = handoffState.remoteHost;
remotePortInput.value = handoffState.remotePort;
remoteTokenInput.value = handoffState.remoteToken;

function authHeaders() {
  return { "X-Pairing-Token": handoffState.token };
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setNotice(message) {
  handoffState.notice = message;
  renderState();
}

function renderAll() {
  renderTargets();
  renderCanvas();
  renderRoutes();
  renderState();
}

function switchTab(name) {
  ["state", "remote", "discovery"].forEach((tab) => {
    const panel = document.getElementById(`tab${tab.charAt(0).toUpperCase() + tab.slice(1)}`);
    const btn = document.querySelector(`.tab-button[data-tab="${tab}"]`);
    if (panel) panel.hidden = tab !== name;
    if (btn) btn.classList.toggle("active", tab === name);
  });
  handoffState.activeTab = name;
}

function renderTargets() {
  const devices = uniqueDeviceIds()
    .filter((deviceId) => deviceId !== "local")
    .map((deviceId) => {
      const screen = handoffState.screens.find((item) => item.device_id === deviceId);
      return { deviceId, name: screen ? screen.name.replace(/\s+\d+$/, "") : deviceId };
    });
  targetSelect.innerHTML = devices
    .map((device) => `<option value="${escapeHtml(device.deviceId)}">${escapeHtml(device.name)}</option>`)
    .join("");
  if (!handoffState.activeTargetId && targetSelect.value) {
    handoffState.activeTargetId = targetSelect.value;
  }
}

function renderCanvas() {
  layoutWorld.style.transform = `matrix(${handoffState.viewport.scale}, 0, 0, ${handoffState.viewport.scale}, ${handoffState.viewport.x}, ${handoffState.viewport.y})`;
  layoutWorld.innerHTML = handoffState.screens
    .map((screen) => {
      const rect = screen.rect;
      const monitorSub = (() => {
        if (screen.device_id !== "local" || screen.monitor_index === undefined) return "";
        if (handoffState.localMonitors.length <= 1) return "";
        const m = handoffState.localMonitors.find((mon) => mon.index === screen.monitor_index);
        return m ? m.name : "";
      })();
      return `
        <button
          class="layout-screen ${screen.device_id === "local" ? "local" : "target"}"
          data-selected="${screen.screen_id === handoffState.selectedScreenId ? "true" : "false"}"
          data-edge-enabled="${screen.edge_enabled !== false ? "true" : "false"}"
          type="button"
          data-screen-id="${escapeHtml(screen.screen_id)}"
          style="left:${rect.x}px;top:${rect.y}px;width:${rect.width}px;height:${rect.height}px"
        >
          <span>${escapeHtml(screen.name)}</span>
          <small>${escapeHtml(`${rect.width} x ${rect.height}${screen.edge_enabled === false ? " / edges off" : ""}`)}</small>
          ${monitorSub ? `<small>${escapeHtml(monitorSub)}</small>` : ""}
        </button>
      `;
    })
    .join("");

  layoutCanvas.querySelectorAll(".layout-screen").forEach((element) => {
    element.addEventListener("pointerdown", startDrag);
  });
}

function renderState() {
  const selectedScreen = getSelectedScreen();
  selectedScreenText.textContent = selectedScreen
    ? `${selectedScreen.name} (${selectedScreen.edge_enabled === false ? "edges off" : "edges on"})`
    : "none";
  handoffModeText.textContent = handoffState.mode;
  handoffTargetText.textContent = handoffState.activeTargetId || "none";
  handoffEdgeText.textContent = handoffState.activeEdge || "none";
  const selScreen = getSelectedScreen();
  const monitorIdx = selScreen?.monitor_index ?? 0;
  const monitorInfo = handoffState.localMonitors[monitorIdx];
  localScreenText.textContent = handoffState.screenWidth
    ? `${handoffState.screenWidth} × ${handoffState.screenHeight}${monitorInfo && handoffState.localMonitors.length > 1 ? ` (${monitorInfo.name})` : ""}`
    : "unknown";
  const dp = handoffState.dwellProgress;
  dwellProgressItem.hidden = dp === null || dp === undefined;
  if (dp !== null && dp !== undefined) {
    dwellProgressFill.style.width = `${Math.round(dp * 100)}%`;
  }
  handoffNotice.textContent = handoffState.notice;
  handoffStatus.textContent = handoffState.notice;
  confirmHandoffButton.disabled = handoffState.mode !== "pending_handoff";
  simulateEdgeButton.disabled = handoffState.mode !== "armed";
  freeformButton.textContent = handoffState.freeformScreens ? "Freeform On" : "Freeform Off";
  remoteMousePad.dataset.active = handoffState.remoteConnected ? "true" : "false";
  remoteStatusText.textContent = handoffState.remoteConnected
    ? `connected ${handoffState.remoteTargetLabel}`
    : "disconnected";
  remoteConnectButton.disabled = !handoffState.token || handoffState.remoteConnected;
  remoteDisconnectButton.disabled = !handoffState.remoteConnected;
  goActiveButton.disabled = !handoffState.remoteConnected || handoffState.mode === "active_remote";
  remoteWiggleButton.disabled = !handoffState.remoteConnected;
  remoteLeftClickButton.disabled = !handoffState.remoteConnected;
  remoteRightClickButton.disabled = !handoffState.remoteConnected;
  activeHandoffLayer.hidden = handoffState.mode !== "active_remote";
  activeHandoffStatus.textContent = handoffState.remoteTargetLabel
    ? `Remote handoff active: ${handoffState.remoteTargetLabel}`
    : "Remote handoff active";
  activeLayerCaptureButton.textContent = handoffState.pointerLocked ? "Release Cursor" : "Capture Cursor";
  discoveryBadge.hidden = pendingPairModal.hidden;

  const remoteTargetDeviceId = handoffState.screens.find((s) => s.device_id !== "local")?.device_id;
  const connectedRoutes = handoffState.routes.filter((r) => r.to_device_id === remoteTargetDeviceId);
  remoteArmButton.disabled = !handoffState.remoteConnected || handoffState.mode === "active_remote";
  remoteRouteHint.textContent = handoffState.remoteConnected
    ? connectedRoutes.length
      ? `${connectedRoutes.length} route${connectedRoutes.length === 1 ? "" : "s"} ready — will use best overlap.`
      : "No routes yet. Click Routes to scan layout adjacency."
    : "";
}

function renderRoutes() {
  if (!handoffState.routes.length) {
    routeList.innerHTML = '<div class="empty-state">No adjacent handoff routes.</div>';
    return;
  }

  routeList.innerHTML = handoffState.routes
    .map((route) => {
      return `
        <div class="state-item route-item">
          <span>${escapeHtml(`${route.from_screen_id} ${route.exit_edge} -> ${route.to_screen_id} ${route.enter_edge}`)}</span>
          <strong>${escapeHtml(`${route.overlap_px}px`)}</strong>
          <button
            class="secondary compact-button route-arm-button"
            type="button"
            data-to-device-id="${escapeHtml(route.to_device_id)}"
            data-exit-edge="${escapeHtml(route.exit_edge)}"
          >Arm</button>
        </div>
      `;
    })
    .join("");

  routeList.querySelectorAll(".route-arm-button").forEach((button) => {
    button.addEventListener("click", () => {
      armRoute(button.dataset.toDeviceId, button.dataset.exitEdge);
    });
  });
}

async function previewRoutes() {
  if (!handoffState.token) {
    setNotice("Enter the pairing token before previewing routes.");
    return;
  }

  try {
    const data = await api("/api/handoff/layout/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        screens: handoffState.screens,
        snap_tolerance_px: handoffState.freeformScreens ? 10000 : 24,
        min_overlap_px: 80,
      }),
    });
    handoffState.routes = data.routes || [];
    handoffState.overlaps = data.overlaps || [];
    renderRoutes();
    const overlapText = handoffState.overlaps.length
      ? ` ${handoffState.overlaps.length} overlap${handoffState.overlaps.length === 1 ? "" : "s"} blocked.`
      : "";
    setNotice(`Preview found ${handoffState.routes.length} route${handoffState.routes.length === 1 ? "" : "s"}.${overlapText}`);
  } catch (error) {
    setNotice(error.message);
  }
}

function startDrag(event) {
  event.stopPropagation();
  const screenId = event.currentTarget.dataset.screenId;
  const screen = handoffState.screens.find((item) => item.screen_id === screenId);
  if (!screen) return;
  handoffState.selectedScreenId = screenId;
  renderState();
  const worldPoint = clientToWorld(event.clientX, event.clientY);
  handoffState.dragging = {
    screenId,
    offsetX: worldPoint.x - screen.rect.x,
    offsetY: worldPoint.y - screen.rect.y,
  };
  event.currentTarget.setPointerCapture(event.pointerId);
}

function handleDrag(event) {
  if (!handoffState.dragging) return;
  const dragging = handoffState.dragging;
  const movingScreen = handoffState.screens.find((screen) => screen.screen_id === dragging.screenId);
  if (!movingScreen) return;
  const worldPoint = clientToWorld(event.clientX, event.clientY);
  const proposedRect = {
    ...movingScreen.rect,
    x: Math.round(worldPoint.x - dragging.offsetX),
    y: Math.round(worldPoint.y - dragging.offsetY),
  };
  // Apply edge-snap only when it won't cause overlap — skip it while pushing through a tile.
  let nextRect = proposedRect;
  if (!handoffState.freeformScreens) {
    const snapped = snapRectToScreens(dragging.screenId, proposedRect, 28);
    if (!screenWouldOverlap(dragging.screenId, snapped)) {
      nextRect = snapped;
    }
  }
  handoffState.screens = handoffState.screens.map((screen) => {
    if (screen.screen_id !== dragging.screenId) return screen;
    return { ...screen, rect: nextRect };
  });
  renderCanvas();
}

function resolveOverlapOnRelease(tileRect, blockerRect, cursor) {
  const dx = (tileRect.x + tileRect.width / 2) - (blockerRect.x + blockerRect.width / 2);
  const dy = (tileRect.y + tileRect.height / 2) - (blockerRect.y + blockerRect.height / 2);
  if (Math.abs(dx) >= Math.abs(dy)) {
    // Horizontal overlap — snap to whichever side of the blocker the cursor is on
    const refX = cursor ? cursor.x : (tileRect.x + tileRect.width / 2);
    const blockerMidX = blockerRect.x + blockerRect.width / 2;
    return refX < blockerMidX
      ? { ...tileRect, x: blockerRect.x - tileRect.width }
      : { ...tileRect, x: blockerRect.x + blockerRect.width };
  }
  // Vertical overlap — always snap back to entry edge
  return dy < 0
    ? { ...tileRect, y: blockerRect.y - tileRect.height }
    : { ...tileRect, y: blockerRect.y + blockerRect.height };
}

function snapRectToScreens(screenId, rect) {
  const snapTolerance = arguments.length > 2 ? arguments[2] : Infinity;
  return snapRectToScreenList(screenId, rect, handoffState.screens, snapTolerance);
}

function snapRectToScreenList(screenId, rect, screens, snapTolerance = Infinity) {
  const minOverlap = 80;
  let best = rect;
  let bestDistance = Infinity;
  screens.forEach((screen) => {
    if (screen.screen_id === screenId) return;
    const target = screen.rect;
    const horizontalY = clamp(rect.y, target.y - rect.height + minOverlap, target.y + target.height - minOverlap);
    const verticalX = clamp(rect.x, target.x - rect.width + minOverlap, target.x + target.width - minOverlap);
    const candidates = [
      {
        rect: { ...rect, x: target.x - rect.width, y: horizontalY },
      },
      {
        rect: { ...rect, x: target.x + target.width, y: horizontalY },
      },
      {
        rect: { ...rect, x: verticalX, y: target.y - rect.height },
      },
      {
        rect: { ...rect, x: verticalX, y: target.y + target.height },
      },
    ];
    candidates.forEach((candidate) => {
      const distance = Math.abs(rect.x - candidate.rect.x) + Math.abs(rect.y - candidate.rect.y);
      const overlap = rectsOverlap(candidate.rect, target)
        ? Math.min(candidate.rect.width, target.width, candidate.rect.height, target.height)
        : Math.max(
            rangeOverlap(candidate.rect.y, candidate.rect.y + candidate.rect.height, target.y, target.y + target.height),
            rangeOverlap(candidate.rect.x, candidate.rect.x + candidate.rect.width, target.x, target.x + target.width)
          );
      if (distance <= snapTolerance && overlap >= minOverlap && distance < bestDistance) {
        best = candidate.rect;
        bestDistance = distance;
      }
    });
  });
  return best;
}

function rangeOverlap(firstStart, firstEnd, secondStart, secondEnd) {
  return Math.max(0, Math.min(firstEnd, secondEnd) - Math.max(firstStart, secondStart));
}

function clamp(value, minimum, maximum) {
  if (minimum > maximum) return value;
  return Math.min(Math.max(value, minimum), maximum);
}

function screenWouldOverlap(screenId, rect) {
  return screenWouldOverlapList(screenId, rect, handoffState.screens);
}

function screenWouldOverlapList(screenId, rect, screens) {
  return screens.some((screen) => {
    if (screen.screen_id === screenId) return false;
    return rectsOverlap(rect, screen.rect);
  });
}

function rectsOverlap(first, second) {
  return (
    first.x < second.x + second.width &&
    first.x + first.width > second.x &&
    first.y < second.y + second.height &&
    first.y + first.height > second.y
  );
}

function clientToWorld(clientX, clientY) {
  const canvasRect = layoutCanvas.getBoundingClientRect();
  return {
    x: (clientX - canvasRect.left - handoffState.viewport.x) / handoffState.viewport.scale,
    y: (clientY - canvasRect.top - handoffState.viewport.y) / handoffState.viewport.scale,
  };
}

function startPan(event) {
  if (event.target !== layoutCanvas && event.target !== layoutWorld) return;
  handoffState.panning = {
    startX: event.clientX,
    startY: event.clientY,
    viewportX: handoffState.viewport.x,
    viewportY: handoffState.viewport.y,
  };
  layoutCanvas.setPointerCapture(event.pointerId);
}

function handlePan(event) {
  if (!handoffState.panning || handoffState.dragging) return;
  handoffState.viewport = {
    ...handoffState.viewport,
    x: handoffState.panning.viewportX + event.clientX - handoffState.panning.startX,
    y: handoffState.panning.viewportY + event.clientY - handoffState.panning.startY,
  };
  renderCanvas();
}

function endPan() {
  handoffState.panning = null;
}

function setZoom(nextScale) {
  const scale = Math.min(2, Math.max(0.35, Number(nextScale.toFixed(2))));
  handoffState.viewport = {
    ...handoffState.viewport,
    scale,
  };
  renderCanvas();
  setNotice(`Zoom ${Math.round(scale * 100)}%. Drag empty space to pan.`);
  saveLayout();
}

function resetView() {
  fitLayoutToView();
  renderCanvas();
  setNotice("View fitted to all screens.");
  saveLayout();
}

function fitLayoutToView() {
  const bounds = layoutBounds();
  if (!bounds) {
    handoffState.viewport = { x: 0, y: 0, scale: 1 };
    return;
  }
  const padding = 36;
  const availableWidth = Math.max(1, layoutCanvas.clientWidth - padding * 2);
  const availableHeight = Math.max(1, layoutCanvas.clientHeight - padding * 2);
  const scale = Math.min(2, Math.max(0.25, Math.min(availableWidth / bounds.width, availableHeight / bounds.height)));
  handoffState.viewport = {
    scale: Number(scale.toFixed(3)),
    x: Math.round((layoutCanvas.clientWidth - bounds.width * scale) / 2 - bounds.left * scale),
    y: Math.round((layoutCanvas.clientHeight - bounds.height * scale) / 2 - bounds.top * scale),
  };
}

function layoutBounds() {
  if (!handoffState.screens.length) return null;
  const left = Math.min(...handoffState.screens.map((screen) => screen.rect.x));
  const top = Math.min(...handoffState.screens.map((screen) => screen.rect.y));
  const right = Math.max(...handoffState.screens.map((screen) => screen.rect.x + screen.rect.width));
  const bottom = Math.max(...handoffState.screens.map((screen) => screen.rect.y + screen.rect.height));
  return {
    left,
    top,
    right,
    bottom,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
  };
}

function endDrag(event) {
  if (!handoffState.dragging) return;
  const dragging = handoffState.dragging;
  const movingScreen = handoffState.screens.find((screen) => screen.screen_id === dragging.screenId);
  if (movingScreen) {
    const blocker = handoffState.screens.find((screen) => {
      if (screen.screen_id === dragging.screenId) return false;
      return rectsOverlap(movingScreen.rect, screen.rect);
    });
    let resolvedRect = movingScreen.rect;
    if (blocker) {
      const cursor = event && event.clientX != null ? clientToWorld(event.clientX, event.clientY) : null;
      resolvedRect = resolveOverlapOnRelease(movingScreen.rect, blocker.rect, cursor);
    }
    if (!handoffState.freeformScreens && !screenWouldOverlap(dragging.screenId, resolvedRect)) {
      const snappedRect = snapRectToScreens(dragging.screenId, resolvedRect, Infinity);
      if (!screenWouldOverlap(dragging.screenId, snappedRect)) {
        resolvedRect = snappedRect;
      }
    }
    handoffState.screens = handoffState.screens.map((screen) => {
      if (screen.screen_id !== dragging.screenId) return screen;
      return { ...screen, rect: resolvedRect };
    });
  }
  handoffState.dragging = null;
  renderCanvas();
  setNotice(
    handoffState.freeformScreens
      ? "Layout changed in freeform mode. Preview routes to refresh nearest side routing."
      : "Layout snapped. Preview routes to refresh adjacency."
  );
  saveLayout();
}

function resetLayout() {
  handoffState.screens = [
    {
      screen_id: "local-main",
      device_id: "local",
      name: "This Mac",
      rect: { x: 80, y: 110, width: 360, height: 220 },
      primary: true,
      edge_enabled: true,
    },
    {
      screen_id: "target-main",
      device_id: "target",
      name: "Target Mac",
      rect: { x: 440, y: 130, width: 300, height: 190 },
      primary: true,
      edge_enabled: true,
    },
  ];
  handoffState.selectedScreenId = "target-main";
  handoffState.freeformScreens = false;
  handoffState.routes = [];
  handoffState.overlaps = [];
  handoffState.viewport = { x: 0, y: 0, scale: 1 };
  setNotice("Layout reset.");
  renderAll();
  saveLayout();
}

function getSelectedScreen() {
  return handoffState.screens.find((screen) => screen.screen_id === handoffState.selectedScreenId) || null;
}

function uniqueDeviceIds() {
  return [...new Set(handoffState.screens.map((screen) => screen.device_id))];
}

function findPlacementRect(referenceRect, width, height) {
  const ordered = [
    { x: referenceRect.x + referenceRect.width, y: referenceRect.y, width, height },
    { x: referenceRect.x - width,               y: referenceRect.y, width, height },
    { x: referenceRect.x, y: referenceRect.y - height,              width, height },
    { x: referenceRect.x, y: referenceRect.y + referenceRect.height, width, height },
  ];
  for (const candidate of ordered) {
    if (!screenWouldOverlap(null, candidate)) return candidate;
  }
  return findNonOverlappingRect(ordered[0]);
}

function addMachine() {
  const deviceIds = uniqueDeviceIds();
  if (deviceIds.length >= 5) {
    setNotice("Prototype supports up to 5 machines at once.");
    return;
  }
  const nextIndex = deviceIds.length + 1;
  const deviceId = `device-${nextIndex}`;
  const localScreen = handoffState.screens.find((s) => s.device_id === "local" && s.primary)
    || handoffState.screens[0];
  const ref = localScreen ? localScreen.rect : { x: 80, y: 110, width: 300, height: 200 };
  const screen = {
    screen_id: `${deviceId}-main`,
    device_id: deviceId,
    name: `Machine ${nextIndex}`,
    rect: findPlacementRect(ref, ref.width, ref.height),
    primary: true,
    edge_enabled: true,
  };
  handoffState.screens.push(screen);
  handoffState.selectedScreenId = screen.screen_id;
  renderAll();
  setNotice("Machine added.");
  saveLayout();
}

function addMonitor() {
  const selected = getSelectedScreen();
  if (!selected) {
    setNotice("Select a monitor before adding another monitor.");
    return;
  }

  // For local tiles with real monitor data, offer the next unrepresented real monitor
  if (selected.device_id === "local" && handoffState.localMonitors.length > 1) {
    const usedIndices = new Set(
      handoffState.screens
        .filter((s) => s.device_id === "local" && s.monitor_index !== undefined)
        .map((s) => s.monitor_index)
    );
    const nextMonitor = handoffState.localMonitors.find((m) => !usedIndices.has(m.index));
    if (nextMonitor) {
      const norm = computeNormalizedSize(
        { width: nextMonitor.width, height: nextMonitor.height },
        handoffState.localMonitors.map((m) => ({ width: m.width, height: m.height }))
      );
      const screen = {
        screen_id: `local-monitor-${nextMonitor.index}`,
        device_id: "local",
        name: nextMonitor.name,
        rect: findPlacementRect(selected.rect, norm.width, norm.height),
        primary: false,
        edge_enabled: true,
        monitor_index: nextMonitor.index,
      };
      handoffState.screens.push(screen);
      handoffState.selectedScreenId = screen.screen_id;
      handoffState.realDims[`local-${nextMonitor.index}`] = { width: nextMonitor.width, height: nextMonitor.height };
      renderAll();
      setNotice(`Added ${nextMonitor.name} (monitor ${nextMonitor.index}).`);
      saveLayout();
      return;
    }
    setNotice("All local monitors are already in the layout.");
    return;
  }

  // Default: duplicate selected tile's size
  const siblingCount = handoffState.screens.filter((screen) => screen.device_id === selected.device_id).length + 1;
  const screen = {
    screen_id: `${selected.device_id}-monitor-${siblingCount}`,
    device_id: selected.device_id,
    name: `${selected.name} ${siblingCount}`,
    rect: findPlacementRect(selected.rect, selected.rect.width, selected.rect.height),
    primary: false,
    edge_enabled: true,
  };
  handoffState.screens.push(screen);
  handoffState.selectedScreenId = screen.screen_id;
  renderAll();
  setNotice("Monitor added.");
  saveLayout();
}

function findNonOverlappingRect(rect, preferredNeighborId = null) {
  const neighbors = preferredNeighborId
    ? [
        ...handoffState.screens.filter((screen) => screen.screen_id === preferredNeighborId),
        ...handoffState.screens.filter((screen) => screen.screen_id !== preferredNeighborId),
      ]
    : handoffState.screens;
  const candidates = [];
  neighbors.forEach((screen, index) => {
    const target = screen.rect;
    candidates.push(
      { priority: index * 4, rect: { ...rect, x: target.x + target.width, y: target.y } },
      { priority: index * 4 + 1, rect: { ...rect, x: target.x - rect.width, y: target.y } },
      { priority: index * 4 + 2, rect: { ...rect, x: target.x, y: target.y + target.height } },
      { priority: index * 4 + 3, rect: { ...rect, x: target.x, y: target.y - rect.height } }
    );
  });

  const sorted = candidates.sort((first, second) => {
    if (first.priority !== second.priority) return first.priority - second.priority;
    return rectDistance(rect, first.rect) - rectDistance(rect, second.rect);
  });
  const found = sorted.find((candidate) => !screenWouldOverlap(null, candidate.rect));
  if (found) return found.rect;

  const stepX = rect.width;
  const stepY = rect.height;
  for (let row = 0; row < 8; row += 1) {
    for (let column = 0; column < 8; column += 1) {
      const candidate = {
        ...rect,
        x: rect.x + column * stepX,
        y: rect.y + row * stepY,
      };
      if (!screenWouldOverlap(null, candidate)) return candidate;
    }
  }
  return rect;
}

function rectDistance(first, second) {
  return Math.abs(first.x - second.x) + Math.abs(first.y - second.y);
}

function toggleSelectedEdges() {
  const selected = getSelectedScreen();
  if (!selected) {
    setNotice("Select a monitor before toggling edge routing.");
    return;
  }
  handoffState.screens = handoffState.screens.map((screen) => {
    if (screen.screen_id !== selected.screen_id) return screen;
    return { ...screen, edge_enabled: screen.edge_enabled === false };
  });
  renderAll();
  setNotice("Monitor edge routing toggled.");
  saveLayout();
}

function toggleFreeform() {
  const turningOff = handoffState.freeformScreens;
  handoffState.freeformScreens = !handoffState.freeformScreens;
  if (turningOff) {
    snapAllScreensInward();
    renderAll();
    setNotice("Freeform screens disabled. Screens snapped inward to nearest edges.");
    saveLayout();
    return;
  }
  renderState();
  setNotice("Freeform screens enabled. Dragging will not snap, but preview can find nearest side routes.");
  saveLayout();
}

function snapAllScreensInward() {
  const placed = [];
  handoffState.screens.forEach((screen, index) => {
    if (index === 0) {
      placed.push(screen);
      return;
    }
    let snappedRect = snapRectToScreenList(screen.screen_id, screen.rect, placed, Infinity);
    if (screenWouldOverlapList(screen.screen_id, snappedRect, placed)) {
      snappedRect = findNonOverlappingRectInList(snappedRect, placed);
    }
    placed.push({ ...screen, rect: snappedRect });
  });
  handoffState.screens = placed;
}

function findNonOverlappingRectInList(rect, screens) {
  const candidates = [];
  screens.forEach((screen, index) => {
    const target = screen.rect;
    candidates.push(
      { priority: index * 4, rect: { ...rect, x: target.x + target.width, y: target.y } },
      { priority: index * 4 + 1, rect: { ...rect, x: target.x - rect.width, y: target.y } },
      { priority: index * 4 + 2, rect: { ...rect, x: target.x, y: target.y + target.height } },
      { priority: index * 4 + 3, rect: { ...rect, x: target.x, y: target.y - rect.height } }
    );
  });
  const sorted = candidates.sort((first, second) => {
    if (first.priority !== second.priority) return first.priority - second.priority;
    return rectDistance(rect, first.rect) - rectDistance(rect, second.rect);
  });
  const found = sorted.find((candidate) => !screenWouldOverlapList(null, candidate.rect, screens));
  return found ? found.rect : rect;
}

async function armHandoff() {
  const targetId = targetSelect.value;
  const edge = edgeSelect.value;
  if (!targetId || !edge) {
    setNotice("Choose a target and edge before arming.");
    return;
  }
  const armed = await armDetector(edge);
  if (!armed) return;
  handoffState.mode = "armed";
  handoffState.activeTargetId = targetId;
  handoffState.activeEdge = edge;
  setNotice(buildArmNotice(edge));
}

async function armRoute(targetId, edge) {
  if (!targetId || !edge) {
    setNotice("Route is missing target or edge data.");
    return;
  }
  const armed = await armDetector(edge);
  if (!armed) return;
  handoffState.mode = "armed";
  handoffState.activeTargetId = targetId;
  handoffState.activeEdge = edge;
  targetSelect.value = targetId;
  edgeSelect.value = edge;
  setNotice(buildArmNotice(edge));
  renderState();
}

async function armBestRoute() {
  if (!handoffState.remoteConnected) {
    setNotice("Connect a remote target first.");
    return;
  }
  const targetDeviceId = handoffState.screens.find((s) => s.device_id !== "local")?.device_id;
  const routes = handoffState.routes.filter((r) => r.to_device_id === targetDeviceId);
  if (routes.length === 0) {
    setNotice("No routes found. Click Routes to scan layout adjacency, then try again.");
    return;
  }
  const best = routes.reduce((a, b) => (b.overlap_px > a.overlap_px ? b : a));
  await armRoute(best.to_device_id, best.exit_edge);
  switchTab("state");
}

function buildArmNotice(edge) {
  const screenHint = handoffState.screenWidth
    ? ` (screen: ${handoffState.screenWidth}×${handoffState.screenHeight})`
    : "";
  const remoteHint = handoffState.remoteConnected
    ? ""
    : " Connect a remote target first.";
  return `Handoff armed. Hold cursor at the ${edge} edge${screenHint} for 400ms.${remoteHint}`;
}

async function disarmHandoff() {
  if (handoffState.mode === "active_remote") {
    setNotice("Stop remote control before disarming.");
    return;
  }
  handoffState.mode = "idle";
  handoffState.activeTargetId = null;
  handoffState.activeEdge = null;
  handoffState.dwellProgress = null;
  await disarmDetector();
  setNotice("Handoff disarmed.");
}

async function armDetector(edge) {
  stopDetectorPolling();
  if (!handoffState.token) {
    setNotice("Enter this machine's pairing token before arming.");
    return false;
  }
  try {
    const selectedScreen = getSelectedScreen();
    const screenIndex = (selectedScreen && selectedScreen.device_id === "local" && selectedScreen.monitor_index !== undefined)
      ? selectedScreen.monitor_index
      : 0;
    await api("/api/handoff/arm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edge, dwell_ms: 400, zone_px: 5, screen_index: screenIndex }),
    });
    startDetectorPolling();
    return true;
  } catch (error) {
    setNotice(`Arm failed: ${error.message}`);
    return false;
  }
}

async function disarmDetector() {
  stopDetectorPolling();
  if (!handoffState.token) return;
  try {
    await api("/api/handoff/disarm", { method: "POST" });
  } catch {
    // best-effort cleanup
  }
}

async function armReturn() {
  const returnEdge = OPPOSITE_EDGE[handoffState.activeEdge];
  if (!returnEdge || !handoffState.remoteConnected || !handoffState.token) return;
  stopReturnPolling();
  try {
    await api("/api/handoff/remote/arm-return", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ return_edge: returnEdge, dwell_ms: 400 }),
    });
    startReturnPolling();
  } catch (error) {
    // Non-fatal: remote may not support return detection (older build)
    // Escape and Stop remain available
    console.warn("Return detector arm failed:", error.message);
  }
}

async function disarmReturn() {
  stopReturnPolling();
  if (!handoffState.token) return;
  try {
    await api("/api/handoff/remote/disarm-return", { method: "POST" });
  } catch {
    // best-effort cleanup
  }
}

function startReturnPolling() {
  stopReturnPolling();
  handoffState.returnPollTimer = setInterval(pollReturnState, 200);
}

function stopReturnPolling() {
  if (handoffState.returnPollTimer !== null) {
    clearInterval(handoffState.returnPollTimer);
    handoffState.returnPollTimer = null;
  }
}

async function pollReturnState() {
  if (handoffState.mode !== "active_remote") {
    stopReturnPolling();
    return;
  }
  try {
    const data = await api("/api/handoff/remote/return-state");
    if (data.state === "pending" && handoffState.mode === "active_remote") {
      stopReturnPolling();
      await stopHandoff();
      setNotice("Returned to local control.");
    }
  } catch {
    // keep polling through transient errors
  }
}

function startDetectorPolling() {
  stopDetectorPolling();
  handoffState.detectorPollTimer = setInterval(pollDetectorState, 100);
}

function stopDetectorPolling() {
  if (handoffState.detectorPollTimer !== null) {
    clearInterval(handoffState.detectorPollTimer);
    handoffState.detectorPollTimer = null;
  }
}

async function pollDetectorState() {
  if (!handoffState.token || handoffState.mode !== "armed") {
    stopDetectorPolling();
    return;
  }
  try {
    const data = await api("/api/handoff/detector/state");
    handoffState.dwellProgress = data.dwell_progress ?? null;
    renderState();
    if (data.state === "pending") {
      stopDetectorPolling();
      handoffState.dwellProgress = null;
      if (handoffState.remoteConnected) {
        goActive();
      } else {
        handoffState.mode = "pending_handoff";
        setNotice("Edge detected. Connect a remote target then click Go Active.");
        renderState();
      }
    } else if (data.state === "idle") {
      stopDetectorPolling();
      handoffState.dwellProgress = null;
      handoffState.mode = "idle";
      handoffState.activeTargetId = null;
      handoffState.activeEdge = null;
      setNotice("Edge detector stopped. Ensure the server is running in a desktop session with pyautogui available.");
      renderState();
    }
  } catch {
    // ignore transient polling errors
  }
}

function simulateEdge() {
  if (handoffState.mode !== "armed") {
    setNotice("Handoff is not armed.");
    return;
  }
  handoffState.mode = "pending_handoff";
  setNotice("Handoff pending.");
}

function confirmHandoff() {
  if (handoffState.mode !== "pending_handoff") {
    setNotice("No pending handoff to confirm.");
    return;
  }
  if (!handoffState.remoteConnected) {
    setNotice("Connect a remote target before confirming handoff.");
    return;
  }
  handoffState.mode = "active_remote";
  setNotice("Remote handoff active.");
  renderState();
  activeHandoffLayer.focus();
}

async function goActive() {
  if (!handoffState.remoteConnected) {
    setNotice("Connect a remote target before going active.");
    return;
  }
  if (handoffState.mode === "active_remote") return;
  handoffState.mode = "active_remote";
  handoffState.activeTargetId = handoffState.remoteTargetLabel;
  setNotice("Remote handoff active.");
  renderState();
  activeHandoffLayer.focus();
  await armReturn();
}

async function stopHandoff() {
  releaseActivePointerLock();
  handoffState.dwellProgress = null;
  stopReturnPolling();
  await disarmReturn();
  await stopRemoteControl({ quiet: true });
  await disarmDetector();
  handoffState.mode = "idle";
  handoffState.activeTargetId = null;
  handoffState.activeEdge = null;
  setNotice("Local stop requested. Local control restored.");
}

async function startRemoteControl() {
  if (!handoffState.token) {
    setNotice("Enter this machine's pairing token first.");
    return;
  }
  const host = remoteHostInput.value.trim();
  const port = Number(remotePortInput.value || 8765);
  const token = remoteTokenInput.value.trim();
  if (!host || !token) {
    setNotice("Enter the remote host and remote token before connecting.");
    return;
  }
  saveRemoteConnectionFields();
  try {
    await api("/api/handoff/remote/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port, token, path: "/ws/touchpad" }),
    });
    handoffState.remoteConnected = true;
    handoffState.remoteTargetLabel = `${host}:${port}`;
    setNotice(`Connected to remote mouse receiver at ${host}:${port}.`);
    await fetchRemoteScreenInfo();
  } catch (error) {
    handoffState.remoteConnected = false;
    handoffState.remoteTargetLabel = "";
    setNotice(error.message);
  }
  renderState();
}

async function fetchRemoteScreenInfo() {
  try {
    const data = await api("/api/handoff/remote/screen-info");
    if (data.width && data.height) {
      applyScreenDimensionsToTile("target", data.width, data.height);
    }
  } catch {
    // non-critical; target tile keeps placeholder dimensions
  }
}

function saveRemoteConnectionFields() {
  handoffState.remoteHost = remoteHostInput.value.trim();
  handoffState.remotePort = remotePortInput.value || "8765";
  handoffState.remoteToken = remoteTokenInput.value.trim();
  localStorage.setItem("slickshiftHandoffRemoteHost", handoffState.remoteHost);
  localStorage.setItem("slickshiftHandoffRemotePort", handoffState.remotePort);
  localStorage.setItem("slickshiftHandoffRemoteToken", handoffState.remoteToken);
}

async function stopRemoteControl(options = {}) {
  if (!handoffState.token) return;
  try {
    await api("/api/handoff/remote/stop", { method: "POST" });
  } catch (error) {
    if (!options.quiet) setNotice(error.message);
  }
  handoffState.remoteConnected = false;
  handoffState.remoteTargetLabel = "";
  handoffState.remoteDragging = false;
  handoffState.activeLayerDragging = false;
  handoffState.pointerLocked = false;
  if (!options.quiet) setNotice("Remote mouse disconnected.");
  renderState();
}

async function sendRemoteEvent(message) {
  if (!handoffState.remoteConnected) return;
  try {
    await api("/api/handoff/remote/event", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message),
    });
  } catch (error) {
    const errorMessage = error.message;
    if (handoffState.mode === "active_remote") {
      await stopHandoff();
      setNotice(`Remote connection lost: ${errorMessage}`);
    } else {
      handoffState.remoteConnected = false;
      handoffState.remoteDragging = false;
      setNotice(errorMessage);
      renderState();
    }
  }
}

function startRemotePad(event) {
  if (!handoffState.remoteConnected) {
    setNotice("Connect remote mouse before using the touchpad.");
    return;
  }
  handoffState.remoteDragging = true;
  remoteMousePad.setPointerCapture(event.pointerId);
}

function moveRemotePad(event) {
  if (!handoffState.remoteConnected || !handoffState.remoteDragging) return;
  const now = performance.now();
  if (now - handoffState.lastRemoteMoveAt < 12) return;
  handoffState.lastRemoteMoveAt = now;
  const dx = event.movementX || 0;
  const dy = event.movementY || 0;
  if (dx === 0 && dy === 0) return;
  sendRemoteEvent({ type: "mouse_move", dx: dx * 1.3, dy: dy * 1.3 });
}

function endRemotePad() {
  handoffState.remoteDragging = false;
}

function startActiveLayer(event) {
  if (event.target.closest("button")) return;
  if (handoffState.mode !== "active_remote" || !handoffState.remoteConnected) return;
  if (handoffState.pointerLocked) {
    // Pointer lock: mirror physical button state so the user can click-and-drag
    // on the remote. down:true on press, down:false on release (endActiveLayer).
    const button = event.button === 2 ? "right" : event.button === 1 ? "middle" : "left";
    sendRemoteEvent({ type: "mouse_button", button, down: true });
    return;
  }
  handoffState.activeLayerDragging = true;
  activeHandoffLayer.setPointerCapture(event.pointerId);
}

function moveActiveLayer(event) {
  if (handoffState.mode !== "active_remote" || !handoffState.remoteConnected) return;
  if (handoffState.pointerLocked) return;
  if (!handoffState.activeLayerDragging) return;
  sendActiveLayerMovement(event);
}

function sendActiveLayerMovement(event) {
  const now = performance.now();
  if (now - handoffState.lastRemoteMoveAt < 12) return;
  handoffState.lastRemoteMoveAt = now;
  const dx = event.movementX || 0;
  const dy = event.movementY || 0;
  if (dx === 0 && dy === 0) return;
  sendRemoteEvent({ type: "mouse_move", dx: dx * 1.3, dy: dy * 1.3 });
}

function endActiveLayer(event) {
  if (handoffState.pointerLocked && event && handoffState.mode === "active_remote" && handoffState.remoteConnected) {
    const button = event.button === 2 ? "right" : event.button === 1 ? "middle" : "left";
    sendRemoteEvent({ type: "mouse_button", button, down: false });
  }
  handoffState.activeLayerDragging = false;
}

function toggleActivePointerLock() {
  if (document.pointerLockElement === activeHandoffLayer) {
    releaseActivePointerLock();
    return;
  }
  if (handoffState.mode !== "active_remote" || !handoffState.remoteConnected) {
    setNotice("Activate remote handoff before capturing the cursor.");
    return;
  }
  activeHandoffLayer.requestPointerLock();
}

function releaseActivePointerLock() {
  if (document.pointerLockElement === activeHandoffLayer) {
    document.exitPointerLock();
  }
}

function updateActivePointerLock() {
  handoffState.pointerLocked = document.pointerLockElement === activeHandoffLayer;
  if (handoffState.pointerLocked) {
    setNotice("Cursor captured for browser handoff. Press Escape to release/stop.");
  }
  renderState();
}

async function clickRemoteMouse(button) {
  await sendRemoteEvent({ type: "mouse_button", button, down: true });
  await sendRemoteEvent({ type: "mouse_button", button, down: false });
}

async function wiggleRemoteMouse() {
  const moves = [
    { dx: 80, dy: 0 },
    { dx: 0, dy: 80 },
    { dx: -80, dy: 0 },
    { dx: 0, dy: -80 },
  ];
  for (const move of moves) {
    await sendRemoteEvent({ type: "mouse_move", ...move });
    await new Promise((resolve) => setTimeout(resolve, 90));
  }
  setNotice("Remote wiggle test sent.");
}

async function fetchScreenInfo() {
  if (!handoffState.token) return;
  try {
    const data = await api("/api/screen/info");
    if (data.width && data.height) {
      handoffState.screenWidth = data.width;
      handoffState.screenHeight = data.height;
      applyScreenDimensionsToTile("local", data.width, data.height);
    }
  } catch {
    // non-critical; leave screenWidth/Height as null
  }
}

async function fetchMonitors() {
  if (!handoffState.token) return;
  try {
    const data = await api("/api/screen/monitors");
    handoffState.localMonitors = data.monitors || [];
    // Tag the primary local tile with monitor_index=0 if not already set
    handoffState.screens = handoffState.screens.map((s) => {
      if (s.device_id === "local" && s.primary && s.monitor_index === undefined) {
        return { ...s, monitor_index: 0 };
      }
      return s;
    });
    // Apply real dimensions from each monitor to any matching tile
    handoffState.localMonitors.forEach((m) => {
      const tile = handoffState.screens.find(
        (s) => s.device_id === "local" && s.monitor_index === m.index
      );
      if (tile) {
        applyScreenDimensionsToTile("local", m.width, m.height);
      }
    });
    renderState();
  } catch {
    // non-critical
  }
}

function applyScreenDimensionsToTile(deviceId, width, height) {
  handoffState.realDims[deviceId] = { width, height };
  refreshNormalizedTiles();
}

function refreshNormalizedTiles() {
  const entries = Object.entries(handoffState.realDims);
  if (!entries.length) return;
  const allDims = entries.map(([, d]) => d);
  let changed = false;
  entries.forEach(([deviceId, dims]) => {
    const norm = computeNormalizedSize(dims, allDims);
    handoffState.screens = handoffState.screens.map((screen) => {
      if (screen.device_id !== deviceId || !screen.primary) return screen;
      if (screen.rect.width === norm.width && screen.rect.height === norm.height) return screen;
      changed = true;
      return { ...screen, rect: { ...screen.rect, width: norm.width, height: norm.height } };
    });
  });
  if (changed) {
    fitLayoutToView();
    renderAll();
    saveLayout();
  } else {
    renderState();
  }
}

function computeNormalizedSize(dims, allDims) {
  const BASE = 300;
  const MAX_RATIO = 1.2;
  const diag = (d) => Math.sqrt(d.width ** 2 + d.height ** 2);
  const thisDiag = diag(dims);
  const diags = allDims.map(diag);
  const minD = Math.min(...diags);
  const maxD = Math.max(...diags);
  const scale = minD === maxD ? 1.0 : 1.0 + ((thisDiag - minD) / (maxD - minD)) * (MAX_RATIO - 1.0);
  const width = Math.round(BASE * scale);
  const height = Math.round(width * dims.height / dims.width);
  return { width, height };
}

// ---------------------------------------------------------------------------
// Discovery
// ---------------------------------------------------------------------------

async function toggleDiscovery() {
  if (!handoffState.token) {
    setNotice("Enter the pairing token before using discovery.");
    return;
  }
  if (handoffState.discoveryAdvertising) {
    try {
      await api("/api/discovery/stop", { method: "POST" });
      handoffState.discoveryAdvertising = false;
      stopDiscoveryPolling();
      handoffState.discoveredDevices = [];
      renderDiscoveryList();
    } catch (error) {
      setNotice(error.message);
    }
  } else {
    try {
      await api("/api/discovery/advertise", { method: "POST" });
      handoffState.discoveryAdvertising = true;
      handoffState.discoverySearchStart = Date.now();
      startDiscoveryPolling();
    } catch (error) {
      setNotice(error.message);
    }
  }
  renderDiscoveryButton();
}

function startDiscoveryPolling() {
  stopDiscoveryPolling();
  pollDiscovery();
  handoffState.discoveryPollTimer = setInterval(pollDiscovery, 2000);
}

function stopDiscoveryPolling() {
  if (handoffState.discoveryPollTimer !== null) {
    clearInterval(handoffState.discoveryPollTimer);
    handoffState.discoveryPollTimer = null;
  }
}

async function pollDiscovery() {
  if (!handoffState.token) return;
  try {
    const browse = await api("/api/discovery/browse");
    handoffState.discoveryAdvertising = browse.advertising;
    handoffState.discoveredDevices = browse.devices || [];
    renderDiscoveryButton();
    renderDiscoveryList();
  } catch {
    // ignore transient errors
  }
  try {
    const pending = await api("/api/discovery/pending-request");
    if (pending.pending) {
      showPendingPairModal(pending);
      if (!pendingPairModal.hidden && handoffState.activeTab !== "discovery") {
        switchTab("discovery");
      }
    } else if (!pendingPairModal.hidden) {
      hidePendingPairModal();
    }
  } catch {
    // ignore transient errors
  }
}

function renderDiscoveryButton() {
  discoveryToggleButton.textContent = handoffState.discoveryAdvertising
    ? "Stop Advertising"
    : "Start Advertising";
}

function renderDiscoveryList() {
  if (!handoffState.discoveredDevices.length) {
    const isSearching = handoffState.discoverySearchStart !== null &&
      Date.now() - handoffState.discoverySearchStart < 4000;
    discoveryList.innerHTML = isSearching
      ? '<div class="empty-state">Searching for nearby devices…</div>'
      : handoffState.discoveryAdvertising
        ? '<div class="empty-state">No devices found. Make sure the other device is also advertising.</div>'
        : '<div class="empty-state">Start advertising to find nearby devices.</div>';
    return;
  }
  discoveryList.innerHTML = handoffState.discoveredDevices
    .map((device) => {
      const hasCred = Boolean(localStorage.getItem(`slickshiftTrusted_${device.device_id}`));
      return `
        <div class="discovery-device">
          <div class="discovery-device-info">
            <strong>${escapeHtml(device.name)}</strong>
            <small class="muted-text">${escapeHtml(device.host)}</small>
          </div>
          <div class="click-row">
            ${hasCred ? `<button class="compact-button secondary" type="button" data-action="reconnect" data-device-id="${escapeHtml(device.device_id)}">Reconnect</button>` : ""}
            <button class="compact-button secondary" type="button" data-action="pair" data-device-id="${escapeHtml(device.device_id)}">Pair</button>
          </div>
        </div>
      `;
    })
    .join("");

  discoveryList.querySelectorAll("button[data-action]").forEach((btn) => {
    const device = handoffState.discoveredDevices.find((d) => d.device_id === btn.dataset.deviceId);
    if (!device) return;
    if (btn.dataset.action === "pair") {
      btn.addEventListener("click", () => openPairModal(device));
    } else {
      btn.addEventListener("click", () => reconnectTrustedDevice(device));
    }
  });
}

// ---------------------------------------------------------------------------
// Pairing modal (initiating pair with a discovered device)
// ---------------------------------------------------------------------------

async function openPairModal(device) {
  if (!handoffState.token) {
    setNotice("Enter the pairing token before pairing.");
    return;
  }
  handoffState.pairingState.step = "requesting";
  handoffState.pairingState.target = device;

  try {
    const data = await api("/api/discovery/remote/request-pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host: device.host, port: device.port }),
    });
    handoffState.pairingState.expiresAt = Date.now() + (data.expires_in || 45) * 1000;
    handoffState.pairingState.step = "waiting_pin";
    showPairModal(device);
  } catch (error) {
    handoffState.pairingState.step = "idle";
    setNotice(`Pair request failed: ${error.message}`);
  }
}

function showPairModal(device) {
  pairModalTargetName.textContent = device.name;
  pairPinInput.value = "";
  pairModalError.hidden = true;
  pairConnectButton.disabled = false;
  pairModal.hidden = false;
  pairPinInput.focus();
  startPairCountdown();
}

function startPairCountdown() {
  stopPairCountdown();
  renderPairCountdown();
  handoffState.pairingState.countdownTimer = setInterval(() => {
    if (!handoffState.pairingState.expiresAt) return;
    const remaining = Math.ceil((handoffState.pairingState.expiresAt - Date.now()) / 1000);
    if (remaining <= 0) {
      stopPairCountdown();
      cancelPair();
      setNotice("Pairing code expired. Try pairing again.");
      return;
    }
    renderPairCountdown();
  }, 1000);
}

function renderPairCountdown() {
  if (!handoffState.pairingState.expiresAt) {
    pairCountdownText.textContent = "—";
    return;
  }
  const remaining = Math.max(0, Math.ceil((handoffState.pairingState.expiresAt - Date.now()) / 1000));
  pairCountdownText.textContent = `${remaining}s`;
}

function stopPairCountdown() {
  if (handoffState.pairingState.countdownTimer !== null) {
    clearInterval(handoffState.pairingState.countdownTimer);
    handoffState.pairingState.countdownTimer = null;
  }
}

async function submitPair() {
  const ps = handoffState.pairingState;
  if (ps.step !== "waiting_pin" || !ps.target) return;

  const code = pairPinInput.value.trim();
  if (!/^\d{6}$/.test(code)) {
    showPairModalError("Enter the 6-digit PIN from the target device.");
    return;
  }

  const permissions = {};
  if (permMouseCheck.checked) permissions.mouse = true;
  if (permKeyboardCheck.checked) permissions.keyboard = true;
  const remember = pairRememberCheck.checked;

  ps.step = "submitting";
  pairConnectButton.disabled = true;
  pairModalError.hidden = true;

  try {
    const data = await api("/api/discovery/remote/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: ps.target.host,
        port: ps.target.port,
        code,
        permissions,
        remember_device: remember,
      }),
    });

    if (data.trusted && data.shared_secret) {
      localStorage.setItem(
        `slickshiftTrusted_${ps.target.device_id}`,
        JSON.stringify({ shared_secret: data.shared_secret }),
      );
    }

    stopPairCountdown();
    closePairModal();
    await startSessionConnection(
      ps.target.host,
      ps.target.port,
      data.session.session_id,
      data.session_token,
      ps.target.name,
    );
  } catch (error) {
    ps.step = "waiting_pin";
    pairConnectButton.disabled = false;
    showPairModalError(error.message);
  }
}

function showPairModalError(message) {
  pairModalError.textContent = message;
  pairModalError.hidden = false;
}

function cancelPair() {
  stopPairCountdown();
  handoffState.pairingState = { step: "idle", target: null, expiresAt: null, countdownTimer: null };
  closePairModal();
}

function closePairModal() {
  pairModal.hidden = true;
  pairPinInput.value = "";
  pairModalError.hidden = true;
  pairConnectButton.disabled = false;
}

// ---------------------------------------------------------------------------
// Trusted reconnect
// ---------------------------------------------------------------------------

async function reconnectTrustedDevice(device) {
  if (!handoffState.token) {
    setNotice("Enter the pairing token before reconnecting.");
    return;
  }
  const raw = localStorage.getItem(`slickshiftTrusted_${device.device_id}`);
  if (!raw) {
    setNotice("No stored credentials for this device. Pair first.");
    return;
  }
  let cred;
  try {
    cred = JSON.parse(raw);
  } catch {
    setNotice("Stored credential is corrupted. Pair again.");
    return;
  }

  setNotice(`Reconnecting to ${device.name}…`);
  try {
    const data = await api("/api/discovery/remote/reconnect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        host: device.host,
        port: device.port,
        shared_secret: cred.shared_secret,
      }),
    });
    await startSessionConnection(
      device.host,
      device.port,
      data.session.session_id,
      data.session_token,
      device.name,
    );
  } catch (error) {
    setNotice(`Reconnect failed: ${error.message}`);
  }
}

// ---------------------------------------------------------------------------
// Session-based connection (post-pairing / post-reconnect)
// ---------------------------------------------------------------------------

async function startSessionConnection(host, port, sessionId, sessionToken, deviceName) {
  try {
    await api("/api/handoff/remote/start-session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port, session_id: sessionId, session_token: sessionToken }),
    });
    handoffState.remoteConnected = true;
    handoffState.remoteTargetLabel = `${deviceName} (${host})`;
    handoffState.screens = handoffState.screens.map((s) => {
      if (s.device_id === "target" && s.primary) return { ...s, name: deviceName };
      return s;
    });
    setNotice(`Connected to ${deviceName} via discovery.`);
    switchTab("remote");
    renderAll();
    saveLayout();
    await fetchRemoteScreenInfo();
  } catch (error) {
    handoffState.remoteConnected = false;
    handoffState.remoteTargetLabel = "";
    setNotice(`Connection failed: ${error.message}`);
    renderState();
  }
}

// ---------------------------------------------------------------------------
// Pending pair modal (someone wants to pair with this device)
// ---------------------------------------------------------------------------

function showPendingPairModal(data) {
  pendingRequesterName.textContent = data.requester_name || "Unknown device";
  pendingPairCode.textContent = data.code || "——————";
  // Only reset the countdown timer if a new or refreshed expiry is provided
  const remaining = data.seconds_remaining ?? 0;
  if (remaining > 0 && pendingPairModal.hidden) {
    handoffState.pendingPairExpiresAt = Date.now() + remaining * 1000;
    startPendingPairCountdown();
  }
  pendingPairModal.hidden = false;
}

function startPendingPairCountdown() {
  stopPendingPairCountdown();
  renderPendingPairCountdown();
  handoffState.pendingPairCountdownTimer = setInterval(() => {
    if (!handoffState.pendingPairExpiresAt) return;
    const secs = Math.ceil((handoffState.pendingPairExpiresAt - Date.now()) / 1000);
    if (secs <= 0) {
      stopPendingPairCountdown();
      hidePendingPairModal();
      return;
    }
    renderPendingPairCountdown();
  }, 1000);
}

function renderPendingPairCountdown() {
  if (!handoffState.pendingPairExpiresAt) { pendingPairCountdown.textContent = "—"; return; }
  pendingPairCountdown.textContent = String(Math.max(0, Math.ceil((handoffState.pendingPairExpiresAt - Date.now()) / 1000)));
}

function stopPendingPairCountdown() {
  if (handoffState.pendingPairCountdownTimer !== null) {
    clearInterval(handoffState.pendingPairCountdownTimer);
    handoffState.pendingPairCountdownTimer = null;
  }
}

function hidePendingPairModal() {
  stopPendingPairCountdown();
  handoffState.pendingPairExpiresAt = null;
  pendingPairModal.hidden = true;
}

async function dismissPendingRequest() {
  try {
    await api("/api/discovery/dismiss-request", { method: "POST" });
  } catch {
    // best-effort
  }
  hidePendingPairModal();
}

handoffConnectButton.addEventListener("click", async () => {
  handoffState.token = handoffTokenInput.value.trim();
  localStorage.setItem("wdcToken", handoffState.token);
  await previewRoutes();
  await fetchScreenInfo();
  await fetchMonitors();
});

refreshRoutesButton.addEventListener("click", previewRoutes);
resetLayoutButton.addEventListener("click", resetLayout);
resetViewButton.addEventListener("click", resetView);
zoomOutButton.addEventListener("click", () => setZoom(handoffState.viewport.scale - 0.15));
zoomInButton.addEventListener("click", () => setZoom(handoffState.viewport.scale + 0.15));
freeformButton.addEventListener("click", toggleFreeform);
addMachineButton.addEventListener("click", addMachine);
addMonitorButton.addEventListener("click", addMonitor);
toggleEdgeButton.addEventListener("click", toggleSelectedEdges);
armHandoffButton.addEventListener("click", armHandoff);
disarmHandoffButton.addEventListener("click", disarmHandoff);
simulateEdgeButton.addEventListener("click", simulateEdge);
confirmHandoffButton.addEventListener("click", confirmHandoff);
handoffStopButton.addEventListener("click", stopHandoff);
remoteConnectButton.addEventListener("click", startRemoteControl);
remoteDisconnectButton.addEventListener("click", () => stopRemoteControl());
goActiveButton.addEventListener("click", goActive);
remoteWiggleButton.addEventListener("click", wiggleRemoteMouse);
remoteMousePad.addEventListener("pointerdown", startRemotePad);
remoteMousePad.addEventListener("pointermove", moveRemotePad);
remoteMousePad.addEventListener("pointerup", endRemotePad);
remoteMousePad.addEventListener("pointercancel", endRemotePad);
remoteMousePad.addEventListener("wheel", (event) => {
  event.preventDefault();
  sendRemoteEvent({ type: "scroll", amount: event.deltaY > 0 ? -8 : 8 });
});
remoteLeftClickButton.addEventListener("click", () => clickRemoteMouse("left"));
remoteRightClickButton.addEventListener("click", () => clickRemoteMouse("right"));
activeLayerLeftClickButton.addEventListener("click", () => clickRemoteMouse("left"));
activeLayerRightClickButton.addEventListener("click", () => clickRemoteMouse("right"));
activeLayerCaptureButton.addEventListener("click", toggleActivePointerLock);
activeLayerStopButton.addEventListener("click", stopHandoff);
activeHandoffLayer.addEventListener("pointerdown", startActiveLayer);
activeHandoffLayer.addEventListener("pointermove", moveActiveLayer);
activeHandoffLayer.addEventListener("pointerup", endActiveLayer);
activeHandoffLayer.addEventListener("pointercancel", endActiveLayer);
activeHandoffLayer.addEventListener("contextmenu", (event) => event.preventDefault());
activeHandoffLayer.addEventListener("wheel", (event) => {
  event.preventDefault();
  sendRemoteEvent({ type: "scroll", amount: event.deltaY > 0 ? -8 : 8 });
});
document.addEventListener("pointerlockchange", updateActivePointerLock);
document.addEventListener("mousemove", (event) => {
  if (document.pointerLockElement !== activeHandoffLayer) return;
  sendActiveLayerMovement(event);
});
targetSelect.addEventListener("change", () => {
  handoffState.activeTargetId = targetSelect.value;
  renderState();
});
edgeSelect.addEventListener("change", () => {
  handoffState.activeEdge = edgeSelect.value;
  renderState();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!handoffState.remoteConnected && handoffState.mode === "idle") return;
  event.preventDefault();
  stopHandoff();
});
layoutCanvas.addEventListener("pointerdown", startPan);
layoutCanvas.addEventListener("pointermove", handleDrag);
layoutCanvas.addEventListener("pointermove", handlePan);
layoutCanvas.addEventListener("pointerup", endDrag);
layoutCanvas.addEventListener("pointerup", endPan);
layoutCanvas.addEventListener("pointercancel", endDrag);
layoutCanvas.addEventListener("pointercancel", endPan);
layoutCanvas.addEventListener("wheel", (event) => {
  event.preventDefault();
  const direction = event.deltaY > 0 ? -1 : 1;
  setZoom(handoffState.viewport.scale + direction * 0.1);
});

// ---------------------------------------------------------------------------
// Layout persistence
// ---------------------------------------------------------------------------

const LAYOUT_STORAGE_KEY = "slickshiftLayout_v1";

function saveLayout() {
  try {
    localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify({
      screens: handoffState.screens,
      freeformScreens: handoffState.freeformScreens,
      viewport: handoffState.viewport,
      selectedScreenId: handoffState.selectedScreenId,
    }));
  } catch {
    // localStorage unavailable or full — silently skip
  }
}

function loadLayout() {
  try {
    const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (!data || !Array.isArray(data.screens)) return;
    const valid = data.screens.filter((s) =>
      s && s.screen_id && s.device_id && s.rect &&
      typeof s.rect.x === "number" && typeof s.rect.y === "number" &&
      s.rect.width > 0 && s.rect.height > 0
    );
    if (!valid.length) return;
    handoffState.screens = valid;
    if (typeof data.freeformScreens === "boolean") handoffState.freeformScreens = data.freeformScreens;
    if (data.viewport && typeof data.viewport.scale === "number") handoffState.viewport = data.viewport;
    if (data.selectedScreenId) handoffState.selectedScreenId = data.selectedScreenId;
  } catch {
    // Corrupt saved data — ignore and use defaults
  }
}

document.querySelectorAll(".tab-button[data-tab]").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});
remoteArmButton.addEventListener("click", armBestRoute);
remotePreviewRoutesButton.addEventListener("click", previewRoutes);

discoveryToggleButton.addEventListener("click", toggleDiscovery);
pairConnectButton.addEventListener("click", submitPair);
pairCancelButton.addEventListener("click", cancelPair);
pairPinInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") submitPair();
});
pendingDismissButton.addEventListener("click", dismissPendingRequest);

loadLayout();
renderAll();
