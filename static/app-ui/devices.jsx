// devices.jsx — Devices section + Overview devices panel, wired to real APIs

const { useState, useEffect, useRef, useCallback } = React;

// ── Data helpers ─────────────────────────────────────────────────────────────

function osToPlatform(os) {
  if (os === "windows") return "windows";
  if (os === "macos") return "macos";
  return "other";
}

function backendToUiPerms(p = {}) {
  return {
    clipboard: !!(p.clipboard_read || p.clipboard_write),
    files: !!p.file_receive,
    macros: !!p.macros,
  };
}

function uiToBackendPerms(p) {
  return {
    clipboard_read: !!p.clipboard,
    clipboard_write: !!p.clipboard,
    file_receive: !!p.files,
    macros: !!p.macros,
  };
}

function formatLastSeen(ts) {
  if (!ts) return "unknown";
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Shared data hook ─────────────────────────────────────────────────────────

function useDeviceData() {
  const [thisDevice, setThisDevice] = useState(null);
  const [paired, setPaired] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [trustedRes, sessRes] = await Promise.all([
        SS.api("/api/trusted-devices"),
        SS.api("/api/pairing-sessions"),
      ]);

      let me = null;
      try { const r = await SS.api("/api/device"); me = r.device; } catch {}
      setThisDevice(me);

      const sessionMap = {};
      (sessRes.sessions || []).forEach(s => { sessionMap[s.device_id] = s; });
      setSessions(sessRes.sessions || []);

      const devices = (trustedRes.devices || []).map(d => ({
        id: d.device_id,
        device_id: d.device_id,
        name: d.name,
        platform: osToPlatform(d.os || ""),
        model: d.name,
        status: sessionMap[d.device_id] ? "connected" : "paired",
        lastSeen: formatLastSeen(d.last_seen_at),
        perms: backendToUiPerms(d.permissions || {}),
        review_required: !!d.review_required,
      }));
      setPaired(devices);
      setError("");
    } catch (e) {
      setError(e.message || "Failed to load devices.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function unpair(device_id) {
    try {
      await SS.api(`/api/trusted-devices/${device_id}`, { method: "DELETE" });
      await load();
    } catch (e) { setError(e.message); }
  }

  async function setPerm(device_id, key, val) {
    const prev = paired.find(d => d.id === device_id);
    if (!prev) return;
    const newUiPerms = { ...prev.perms, [key]: val };
    setPaired(arr => arr.map(d => d.id === device_id ? { ...d, perms: newUiPerms } : d));
    try {
      await SS.api(`/api/trusted-devices/${device_id}/permissions`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ permissions: uiToBackendPerms(newUiPerms) }),
      });
    } catch (e) {
      setError(e.message);
      await load();
    }
  }

  return { thisDevice, paired, sessions, loading, error, load, unpair, setPerm };
}

// ── Discovery hook ───────────────────────────────────────────────────────────

function useDiscovery(active) {
  const [devices, setDevices] = useState([]);
  const [advertising, setAdvertising] = useState(false);
  const [scanning, setScanning] = useState(false);
  const timerRef = useRef(null);

  const poll = useCallback(async () => {
    try {
      const d = await SS.api("/api/discovery/browse");
      setDevices((d.devices || []).filter(x => !x.trusted));
      setAdvertising(!!d.advertising);
    } catch {}
  }, []);

  useEffect(() => {
    if (!active) return;
    setScanning(true);
    poll();
    timerRef.current = setInterval(poll, 3000);
    return () => {
      clearInterval(timerRef.current);
      setScanning(false);
    };
  }, [active, poll]);

  async function toggleAdvertise() {
    try {
      if (advertising) {
        await SS.api("/api/discovery/stop", { method: "POST" });
      } else {
        await SS.api("/api/discovery/advertise", { method: "POST" });
      }
      await poll();
    } catch {}
  }

  return { devices, advertising, scanning, toggleAdvertise };
}

// ── UI components ────────────────────────────────────────────────────────────

function platformIcon(platform, sz = 22) {
  if (platform === "windows") return <Icons.Win size={sz} />;
  if (platform === "macos") return <Icons.Mac size={sz} />;
  return <Icons.Deck size={sz} />;
}

function statusColor(status) {
  if (status === "connected") return "var(--ok)";
  if (status === "controlling") return "var(--accent)";
  if (status === "controlled") return "var(--accent-2)";
  return "var(--muted)";
}

function statusTag(d) {
  if (d.self) return <span className="tag you">This Device</span>;
  if (d.direction === "controlling")
    return <span className="tag live"><i className="live-dot" />Controlling</span>;
  if (d.direction === "controlled")
    return <span className="tag live" style={{ color: "var(--accent-2)", borderColor: "rgba(192,132,252,0.4)", background: "rgba(192,132,252,0.12)" }}>
      <i className="live-dot" style={{ background: "var(--accent-2)", boxShadow: "0 0 6px var(--accent-2)" }} />Being controlled
    </span>;
  if (d.status === "connected")
    return <span className="tag" style={{ color: "var(--ok)", borderColor: "rgba(74,222,128,0.3)" }}>Online</span>;
  if (d.status === "paired")
    return <span className="tag">Paired</span>;
  if (d.review_required)
    return <span className="tag" style={{ color: "var(--warn)", borderColor: "rgba(251,191,36,0.3)" }}>Review required</span>;
  return null;
}

function PermPill({ label, on, onChange }) {
  return (
    <div className={"perm-pill " + (on ? "on" : "")} onClick={() => onChange(!on)}
         title={`${label}: ${on ? "allowed" : "blocked"}`}>
      <span className={"toggle-mini " + (on ? "on" : "")}><i /></span>
      <span>{label}</span>
    </div>
  );
}

function DeviceRow({ d, onUnpair, onPerm, onReconnect, compact }) {
  return (
    <div className={"dev-row" + (compact ? " compact" : "")}>
      <div className={"dev-icon " + (d.self ? "self" : "")}>
        {platformIcon(d.platform)}
        {!d.self && (
          <span className="stat-dot"
            style={{ background: statusColor(d.status === "connected" ? "connected" : "paired") }} />
        )}
      </div>
      <div className="dev-main">
        <div className="dev-name">{d.name}{statusTag(d)}</div>
        <div className="dev-meta">{d.self ? "" : (d.lastSeen ? `Last seen ${d.lastSeen}` : "")}</div>
      </div>
      <div className="dev-actions">
        {d.self ? (
          <span style={{ color: "var(--muted)", fontSize: 12 }}>This device</span>
        ) : (
          <>
            {!compact && d.perms && (
              <div className="perm-row-inline">
                <PermPill label="Clipboard" on={d.perms.clipboard} onChange={v => onPerm && onPerm(d.id, "clipboard", v)} />
                <PermPill label="Files"     on={d.perms.files}     onChange={v => onPerm && onPerm(d.id, "files", v)} />
                <PermPill label="Macros"    on={d.perms.macros}    onChange={v => onPerm && onPerm(d.id, "macros", v)} />
              </div>
            )}
            <button type="button" className="btn-ghost" onClick={() => onReconnect && onReconnect(d.id)}>Reconnect</button>
            <button type="button" className="btn-ghost" onClick={() => onUnpair && onUnpair(d.id)}>Unpair</button>
          </>
        )}
      </div>
    </div>
  );
}

function DiscoveredRow({ d, onPair }) {
  return (
    <div className="dev-row">
      <div className="dev-icon">{platformIcon(d.platform || "other")}</div>
      <div>
        <div className="dev-name">{d.name}</div>
        <div className="dev-meta">{d.host} · advertising on LAN</div>
      </div>
      <div className="dev-actions">
        <button type="button" className="btn-secondary" onClick={() => onPair(d)}>Pair</button>
      </div>
    </div>
  );
}

// ── Pair modal ───────────────────────────────────────────────────────────────
// Phase 1: "Enter a code" path is wired; "Show a code" path shows guidance
// to use the Handoff page (where the pending-pair modal already exists).

function PairModal({ open, target, onClose, onComplete }) {
  const [step, setStep] = useState("method");
  const [pin, setPin] = useState(["", "", "", "", "", ""]);
  const [perms, setPerms] = useState({ mouse: true, keyboard: true, clipboard: true, files: false });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRefs = useRef([]);

  useEffect(() => {
    if (open) { setStep(target ? "enter" : "method"); setPin(["","","","","",""]); setErr(""); }
  }, [open, target]);

  function handlePinChange(i, v) {
    const c = v.replace(/\D/g, "").slice(0, 1);
    const next = [...pin];
    next[i] = c;
    setPin(next);
    if (c && i < 5) inputRefs.current[i + 1]?.focus();
    if (next.every(x => x)) submitPin(next.join(""));
  }

  function handlePinKey(i, e) {
    if (e.key === "Backspace" && !pin[i] && i > 0) inputRefs.current[i - 1]?.focus();
  }

  async function submitPin(code) {
    if (busy) return;
    setBusy(true);
    setErr("");
    setStep("connecting");
    try {
      const host = target?.host || "";
      const port = target?.port || 8765;
      const data = await SS.api("/api/discovery/remote/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          host, port, code,
          permissions: {
            mouse: perms.mouse,
            keyboard: perms.keyboard,
            clipboard_read: perms.clipboard,
            clipboard_write: perms.clipboard,
            file_receive: perms.files,
          },
          remember_device: true,
        }),
      });
      if (data.trusted && data.shared_secret) {
        try { localStorage.setItem(`slickshiftTrusted_${target?.device_id}`, JSON.stringify({ shared_secret: data.shared_secret })); } catch {}
      }
      setStep("success");
      onComplete && onComplete(target, perms);
    } catch (e) {
      setErr(e.message || "Pairing failed.");
      setStep("enter");
      setPin(["","","","","",""]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>

        {step === "method" && (
          <>
            <div className="modal-h">
              <h2>Pair a new device</h2>
              <p>Both devices must be on the same local network.</p>
            </div>
            <div className="modal-body">
              <div className="method">
                <button type="button" className="method-card" onClick={() => setStep("enter")}>
                  <div className="m-ico"><Icons.Key size={16} /></div>
                  <strong>Enter a code</strong>
                  <span>The other device is showing a 6-digit pairing code.</span>
                </button>
                <button type="button" className="method-card" onClick={() => setStep("show-guide")}>
                  <div className="m-ico"><Icons.QR size={16} /></div>
                  <strong>Show a code</strong>
                  <span>Let the other device enter your code.</span>
                </button>
              </div>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
            </div>
          </>
        )}

        {step === "enter" && (
          <>
            <div className="modal-h">
              <h2>Enter pairing code</h2>
              <p>{target ? <>Pairing with <strong style={{ color: "var(--text)" }}>{target.name}</strong>. Type the 6 digits shown on it.</> : "Type the 6 digits shown on the other device."}</p>
            </div>
            <div className="modal-body">
              <div className="pin-input">
                {pin.map((c, i) => (
                  <input key={i} ref={el => inputRefs.current[i] = el}
                    className={c ? "filled" : ""} value={c} inputMode="numeric" maxLength={1}
                    onChange={e => handlePinChange(i, e.target.value)}
                    onKeyDown={e => handlePinKey(i, e)} autoFocus={i === 0} />
                ))}
              </div>
              {err && <div style={{ color: "var(--danger)", fontSize: 12, textAlign: "center", marginTop: 8 }}>{err}</div>}
              <div className="perm-list">
                {[
                  { k: "mouse",     n: "Mouse control",   d: "Move pointer and click on this device" },
                  { k: "keyboard",  n: "Keyboard input",  d: "Type text and send shortcuts" },
                  { k: "clipboard", n: "Clipboard sync",  d: "Copy and paste between devices" },
                  { k: "files",     n: "File transfer",   d: "Receive files into Downloads" },
                ].map(p => (
                  <div className="perm-row" key={p.k}>
                    <div className="pmeta">
                      <div className="pname">{p.n}</div>
                      <div className="pdesc">{p.d}</div>
                    </div>
                    <div className={"toggle " + (perms[p.k] ? "on" : "")}
                         onClick={() => setPerms({ ...perms, [p.k]: !perms[p.k] })} />
                  </div>
                ))}
              </div>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn-ghost" onClick={() => setStep("method")}>Back</button>
              <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
            </div>
          </>
        )}

        {step === "show-guide" && (
          <>
            <div className="modal-h">
              <h2>Show a code</h2>
              <p>Use the <strong style={{ color: "var(--text)" }}>Handoff page</strong> to display a code for another device to enter.</p>
            </div>
            <div className="modal-body" style={{ color: "var(--text-dim)", fontSize: 13, lineHeight: 1.6 }}>
              Open <code style={{ color: "var(--accent)", fontFamily: "var(--mono)" }}>/handoff</code> in a browser on this machine, navigate to the Discovery tab, and click <strong>Start Advertising</strong>. The incoming pairing modal will show the code.
            </div>
            <div className="modal-foot">
              <button type="button" className="btn-ghost" onClick={() => setStep("method")}>Back</button>
              <button type="button" className="btn-ghost" onClick={onClose}>Close</button>
            </div>
          </>
        )}

        {step === "connecting" && (
          <div className="modal-body connecting">
            <div className="spinner" />
            <div style={{ fontSize: 15, fontWeight: 600 }}>Establishing trust…</div>
            <div className="connecting-msg">Verifying code over the LAN</div>
          </div>
        )}

        {step === "success" && (
          <>
            <div className="modal-body connecting">
              <div className="success-mark"><Icons.Check size={32} /></div>
              <div style={{ fontSize: 16, fontWeight: 600 }}>{target ? `Paired with ${target.name}` : "Device paired"}</div>
              <div className="connecting-msg" style={{ marginTop: 8 }}>This device is now in your trusted list.</div>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn-primary" onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Devices section ──────────────────────────────────────────────────────────

function DevicesSection() {
  const { thisDevice, paired, loading, error, load, unpair, setPerm } = useDeviceData();
  const { devices: discovered, advertising, scanning, toggleAdvertise } = useDiscovery(true);
  const [pairOpen, setPairOpen] = useState(false);
  const [pairTarget, setPairTarget] = useState(null);
  const [reconnectMsg, setReconnectMsg] = useState("");

  const selfRow = thisDevice ? [{
    id: "__self__", self: true,
    name: thisDevice.name,
    platform: osToPlatform(thisDevice.os || ""),
    model: thisDevice.name,
  }] : [];

  async function completePair() {
    setPairOpen(false);
    await load();
  }

  async function handleReconnect(device_id) {
    setReconnectMsg("");
    let cred = null;
    try { const raw = localStorage.getItem(`slickshiftTrusted_${device_id}`); if (raw) cred = JSON.parse(raw); } catch {}
    if (!cred?.shared_secret) {
      setReconnectMsg("No stored credential for this device. Pair it again to enable silent reconnect.");
      return;
    }
    const found = discovered.find(d => d.device_id === device_id);
    if (!found) {
      setReconnectMsg("Device not found on the network. Make sure Screen Slickshift is running on it.");
      return;
    }
    try {
      await SS.api("/api/discovery/remote/reconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: found.host, port: found.port || 8765, shared_secret: cred.shared_secret }),
      });
      const name = paired.find(d => d.id === device_id)?.name || device_id;
      setReconnectMsg(`Reconnected to ${name}.`);
    } catch (e) {
      setReconnectMsg(`Reconnect failed: ${e.message}`);
    }
  }

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Devices</h1>
          <div className="sub">Devices you trust to share input, clipboard, and files.</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn-ghost" onClick={load}><Icons.Refresh size={14} />Refresh</button>
          <button type="button" className="btn-primary" onClick={() => { setPairTarget(null); setPairOpen(true); }}>
            <Icons.Plus size={14} />Add device
          </button>
        </div>
      </div>

      {error && <div style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>{error}</div>}
      {reconnectMsg && <div style={{ color: reconnectMsg.startsWith("Reconnected") ? "var(--ok)" : "var(--warn)", fontSize: 13, marginBottom: 12 }}>{reconnectMsg}</div>}

      <div className="panel">
        <div className="panel-h">
          <h2>Trusted devices</h2>
          <span className="h-sub">{paired.length} paired</span>
        </div>
        <div className="panel-body">
          {loading ? (
            <div style={{ color: "var(--muted)", fontSize: 13, padding: "12px 0" }}>Loading…</div>
          ) : (
            <>
              {selfRow.map(d => <DeviceRow key={d.id} d={d} />)}
              {paired.length === 0 && !loading ? (
                <div style={{ color: "var(--muted)", fontSize: 13, padding: "12px 0" }}>
                  No paired devices yet. Click Add device to pair one.
                </div>
              ) : (
                paired.map(d => <DeviceRow key={d.id} d={d} onUnpair={unpair} onPerm={setPerm} onReconnect={handleReconnect} />)
              )}
            </>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-h">
          <h2>Available on this network</h2>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {scanning && <span className="h-sub"><span className="scan-dot" style={{ display: "inline-block", marginRight: 6 }} />Scanning…</span>}
            <button type="button" className="btn-ghost" style={{ height: 26, fontSize: 12 }} onClick={toggleAdvertise}>
              {advertising ? "Stop advertising" : "Start advertising"}
            </button>
          </div>
        </div>
        <div className="panel-body">
          {discovered.length === 0 ? (
            <div className="disc-empty">
              <div className="scan"><span className="scan-dot" />Looking for nearby devices…</div>
              <div>Make sure Screen Slickshift is running on the other device and both are on the same network.</div>
            </div>
          ) : (
            discovered.map(d => (
              <DiscoveredRow key={d.device_id} d={d}
                onPair={dev => { setPairTarget(dev); setPairOpen(true); }} />
            ))
          )}
        </div>
      </div>

      <PairModal open={pairOpen} target={pairTarget}
        onClose={() => setPairOpen(false)} onComplete={completePair} />
    </>
  );
}

// ── Overview section ─────────────────────────────────────────────────────────

function OverviewSection() {
  const { thisDevice, paired, loading, load, unpair } = useDeviceData();
  const { devices: discovered } = useDiscovery(true);
  const [pairOpen, setPairOpen] = useState(false);

  async function handleReconnect(device_id) {
    let cred = null;
    try { const raw = localStorage.getItem(`slickshiftTrusted_${device_id}`); if (raw) cred = JSON.parse(raw); } catch {}
    if (!cred?.shared_secret) return;
    const found = discovered.find(d => d.device_id === device_id);
    if (!found) return;
    try {
      await SS.api("/api/discovery/remote/reconnect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: found.host, port: found.port || 8765, shared_secret: cred.shared_secret }),
      });
    } catch {}
  }

  const selfRow = thisDevice ? [{
    id: "__self__", self: true,
    name: thisDevice.name,
    platform: osToPlatform(thisDevice.os || ""),
    model: thisDevice.name,
  }] : [];

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Overview</h1>
          <div className="sub">Trusted devices on your LAN and how their screens are arranged.</div>
        </div>
        <button type="button" className="btn-primary" onClick={() => setPairOpen(true)}>
          <Icons.Plus size={14} />Add device
        </button>
      </div>

      <div className="overview-grid">
        <div className="panel">
          <div className="panel-h">
            <h2>Trusted devices</h2>
            <span className="h-sub">{paired.length} paired</span>
          </div>
          <div className="panel-body">
            {loading ? (
              <div style={{ color: "var(--muted)", fontSize: 13, padding: "8px 0" }}>Loading…</div>
            ) : (
              <>
                {selfRow.map(d => <DeviceRow key={d.id} d={d} compact />)}
                {paired.map(d => <DeviceRow key={d.id} d={d} onUnpair={unpair} onReconnect={handleReconnect} compact />)}
              </>
            )}
          </div>
          {discovered.length > 0 && (
            <>
              <div className="panel-h" style={{ borderTop: "1px solid var(--border)" }}>
                <h2 style={{ fontSize: 12, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                  On this network
                </h2>
                <span className="h-sub"><span className="scan-dot" style={{ display: "inline-block", marginRight: 6 }} />Scanning…</span>
              </div>
              <div className="panel-body">
                {discovered.map(d => (
                  <DiscoveredRow key={d.device_id} d={d}
                    onPair={dev => { setPairOpen(true); }} />
                ))}
              </div>
            </>
          )}
        </div>

        <OverviewEdges paired={paired} thisDevice={thisDevice} />
      </div>

      <PairModal open={pairOpen} target={null}
        onClose={() => setPairOpen(false)} onComplete={async () => { setPairOpen(false); await load(); }} />
    </>
  );
}

// ── Overview edge canvas ─────────────────────────────────────────────────────

function OverviewEdges({ paired, thisDevice }) {
  const W = 360, H = 220;
  const [tiles, setTiles] = useState([]);
  const [drag, setDrag] = useState(null);

  useEffect(() => {
    const baseTiles = [];
    if (thisDevice) {
      baseTiles.push({ id: "local", name: thisDevice.name, role: "local", x: 36, y: 56, w: 130, h: 80, label: "" });
    }
    const online = paired.filter(d => d.status === "connected").slice(0, 3);
    online.forEach((d, i) => {
      baseTiles.push({ id: d.id, name: d.name, role: "target", x: 178 + i * 16, y: 50 + i * 8, w: 150, h: 92, label: "" });
    });
    if (baseTiles.length === 0) {
      baseTiles.push(
        { id: "mac",  name: "This device", role: "local",  x: 36,  y: 56, w: 130, h: 80,  label: "" },
        { id: "pc",   name: "Remote",      role: "target", x: 178, y: 50, w: 150, h: 92, label: "" },
      );
    }
    setTiles(baseTiles);
  }, [thisDevice, paired]);

  function onDown(id, e) {
    const t = tiles.find(x => x.id === id);
    setDrag({ id, ox: e.clientX - t.x, oy: e.clientY - t.y });
  }
  function onMove(e) {
    if (!drag) return;
    setTiles(arr => arr.map(t => {
      if (t.id !== drag.id) return t;
      let nx = e.clientX - drag.ox, ny = e.clientY - drag.oy;
      arr.forEach(o => {
        if (o.id === t.id) return;
        if (Math.abs(nx - (o.x + o.w)) < 10) nx = o.x + o.w;
        if (Math.abs((nx + t.w) - o.x) < 10) nx = o.x - t.w;
        if (Math.abs(ny - o.y) < 10) ny = o.y;
      });
      return { ...t, x: Math.max(0, Math.min(W - t.w, nx)), y: Math.max(0, Math.min(H - t.h, ny)) };
    }));
  }
  function onUp() { setDrag(null); }

  const arrows = [];
  if (tiles.length >= 2) {
    const [a, b] = tiles[0].x < tiles[1].x ? [tiles[0], tiles[1]] : [tiles[1], tiles[0]];
    if (Math.abs((a.x + a.w) - b.x) < 4) {
      arrows.push({ x: a.x + a.w - 8, y: Math.max(a.y, b.y) + 8 });
    }
  }

  return (
    <div className="panel">
      <div className="panel-h">
        <h2>Screen edges</h2>
        <span className="h-sub">Drag to arrange · cursor hands off where edges touch</span>
      </div>
      <div className="edges-canvas" onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}
           style={{ height: H, margin: "0 18px 18px" }}>
        {tiles.map(t => (
          <div key={t.id} className={"screen-tile " + t.role}
               style={{ left: t.x, top: t.y, width: t.w, height: t.h, cursor: drag?.id === t.id ? "grabbing" : "grab" }}
               onMouseDown={e => onDown(t.id, e)}>
            <div className="tile-name">{t.name}</div>
            <div className="tile-meta">{t.role === "local" ? "This device" : "Paired"}</div>
          </div>
        ))}
        {arrows.map((a, i) => (
          <div key={i} className="edge-arrow" style={{ left: a.x, top: a.y }}>
            <svg width="20" height="16" viewBox="0 0 20 16" fill="none">
              <path d="M2 8h14M11 3l5 5-5 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
        ))}
      </div>
    </div>
  );
}

window.DevicesSection = DevicesSection;
window.OverviewSection = OverviewSection;
