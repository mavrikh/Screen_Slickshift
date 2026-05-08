const handoffState = {
  token: localStorage.getItem("wdcToken") || "",
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
  remoteConnected: false,
  remoteTargetLabel: "",
  remoteDragging: false,
  lastRemoteMoveAt: 0,
  viewport: { x: 0, y: 0, scale: 1 },
};

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
const remoteMousePad = document.getElementById("remoteMousePad");
const remoteStatusText = document.getElementById("remoteStatusText");
const remoteWiggleButton = document.getElementById("remoteWiggleButton");
const remoteLeftClickButton = document.getElementById("remoteLeftClickButton");
const remoteRightClickButton = document.getElementById("remoteRightClickButton");

handoffTokenInput.value = handoffState.token;

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
  remoteWiggleButton.disabled = !handoffState.remoteConnected;
  remoteLeftClickButton.disabled = !handoffState.remoteConnected;
  remoteRightClickButton.disabled = !handoffState.remoteConnected;
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
  const nextRect = handoffState.freeformScreens ? proposedRect : snapRectToScreens(dragging.screenId, proposedRect, 28);
  if (screenWouldOverlap(dragging.screenId, nextRect)) {
    setNotice("Screens cannot overlap. Place them edge-to-edge or leave a gap.");
    return;
  }
  handoffState.screens = handoffState.screens.map((screen) => {
    if (screen.screen_id !== dragging.screenId) return screen;
    return {
      ...screen,
      rect: nextRect,
    };
  });
  renderCanvas();
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
}

function resetView() {
  fitLayoutToView();
  renderCanvas();
  setNotice("View fitted to all screens.");
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

function endDrag() {
  if (!handoffState.dragging) return;
  const dragging = handoffState.dragging;
  const movingScreen = handoffState.screens.find((screen) => screen.screen_id === dragging.screenId);
  if (movingScreen && !handoffState.freeformScreens) {
    const snappedRect = snapRectToScreens(dragging.screenId, movingScreen.rect, Infinity);
    if (!screenWouldOverlap(dragging.screenId, snappedRect)) {
      handoffState.screens = handoffState.screens.map((screen) => {
        if (screen.screen_id !== dragging.screenId) return screen;
        return { ...screen, rect: snappedRect };
      });
    }
  }
  handoffState.dragging = null;
  renderCanvas();
  setNotice(
    handoffState.freeformScreens
      ? "Layout changed in freeform mode. Preview routes to refresh nearest side routing."
      : "Layout snapped. Preview routes to refresh adjacency."
  );
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
}

function getSelectedScreen() {
  return handoffState.screens.find((screen) => screen.screen_id === handoffState.selectedScreenId) || null;
}

function uniqueDeviceIds() {
  return [...new Set(handoffState.screens.map((screen) => screen.device_id))];
}

function addMachine() {
  const deviceIds = uniqueDeviceIds();
  if (deviceIds.length >= 5) {
    setNotice("Prototype supports up to 5 machines at once.");
    return;
  }
  const nextIndex = deviceIds.length + 1;
  const deviceId = `device-${nextIndex}`;
  const screen = {
    screen_id: `${deviceId}-main`,
    device_id: deviceId,
    name: `Machine ${nextIndex}`,
    rect: findNonOverlappingRect({ x: 80 + nextIndex * 260, y: 420, width: 280, height: 180 }),
    primary: true,
    edge_enabled: true,
  };
  handoffState.screens.push(screen);
  handoffState.selectedScreenId = screen.screen_id;
  renderAll();
  setNotice("Machine added.");
}

function addMonitor() {
  const selected = getSelectedScreen();
  if (!selected) {
    setNotice("Select a monitor before adding another monitor.");
    return;
  }
  const siblingCount = handoffState.screens.filter((screen) => screen.device_id === selected.device_id).length + 1;
  const screen = {
    screen_id: `${selected.device_id}-monitor-${siblingCount}`,
    device_id: selected.device_id,
    name: `${selected.name} ${siblingCount}`,
    rect: findNonOverlappingRect(
      {
        x: selected.rect.x + selected.rect.width,
        y: selected.rect.y,
        width: selected.rect.width,
        height: selected.rect.height,
      },
      selected.screen_id
    ),
    primary: false,
    edge_enabled: true,
  };
  handoffState.screens.push(screen);
  handoffState.selectedScreenId = screen.screen_id;
  renderAll();
  setNotice("Monitor added.");
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
}

function toggleFreeform() {
  const turningOff = handoffState.freeformScreens;
  handoffState.freeformScreens = !handoffState.freeformScreens;
  if (turningOff) {
    snapAllScreensInward();
    renderAll();
    setNotice("Freeform screens disabled. Screens snapped inward to nearest edges.");
    return;
  }
  renderState();
  setNotice("Freeform screens enabled. Dragging will not snap, but preview can find nearest side routes.");
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

function armHandoff() {
  const targetId = targetSelect.value;
  const edge = edgeSelect.value;
  if (!targetId || !edge) {
    setNotice("Choose a target and edge before arming.");
    return;
  }
  handoffState.mode = "armed";
  handoffState.activeTargetId = targetId;
  handoffState.activeEdge = edge;
  setNotice("Handoff armed.");
}

function armRoute(targetId, edge) {
  if (!targetId || !edge) {
    setNotice("Route is missing target or edge data.");
    return;
  }
  handoffState.mode = "armed";
  handoffState.activeTargetId = targetId;
  handoffState.activeEdge = edge;
  targetSelect.value = targetId;
  edgeSelect.value = edge;
  setNotice(`Handoff armed for ${targetId} on ${edge} edge.`);
  renderState();
}

function disarmHandoff() {
  if (handoffState.mode === "active_remote") {
    setNotice("Stop remote control before disarming.");
    return;
  }
  handoffState.mode = "idle";
  handoffState.activeTargetId = null;
  handoffState.activeEdge = null;
  setNotice("Handoff disarmed.");
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
  handoffState.mode = "active_remote";
  setNotice(
    handoffState.remoteConnected
      ? "Remote handoff active. Drag inside the remote touchpad to move the target mouse."
      : "Handoff active. Connect a remote target before sending mouse input."
  );
}

async function stopHandoff() {
  await stopRemoteControl({ quiet: true });
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
  try {
    await api("/api/handoff/remote/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host, port, token, path: "/ws/touchpad" }),
    });
    handoffState.remoteConnected = true;
    handoffState.remoteTargetLabel = `${host}:${port}`;
    setNotice(`Connected to remote mouse receiver at ${host}:${port}.`);
  } catch (error) {
    handoffState.remoteConnected = false;
    handoffState.remoteTargetLabel = "";
    setNotice(error.message);
  }
  renderState();
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
    handoffState.remoteConnected = false;
    handoffState.remoteDragging = false;
    setNotice(error.message);
    renderState();
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

function clickRemoteMouse(button) {
  sendRemoteEvent({ type: "mouse_button", button, down: true });
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

handoffConnectButton.addEventListener("click", async () => {
  handoffState.token = handoffTokenInput.value.trim();
  localStorage.setItem("wdcToken", handoffState.token);
  await previewRoutes();
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

renderAll();
