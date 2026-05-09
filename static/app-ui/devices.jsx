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
  const [browsing, setBrowsing] = useState(false);
  const [trustedConnectionsEnabled, setTrustedConnectionsEnabled] = useState(true);
  const timerRef = useRef(null);

  const poll = useCallback(async () => {
    try {
      const d = await SS.api("/api/discovery/browse");
      setDevices((d.devices || []).filter(x => !x.trusted));
      setAdvertising(!!d.advertising);
      setBrowsing(!!d.browsing);
      setTrustedConnectionsEnabled(d.trusted_connections_enabled !== false);
    } catch {}
  }, []);

  useEffect(() => {
    if (!active) return;
    poll();
    timerRef.current = setInterval(poll, 3000);
    return () => clearInterval(timerRef.current);
  }, [active, poll]);

  async function toggleBrowse() {
    try {
      if (browsing) {
        await SS.api("/api/discovery/browse/stop", { method: "POST" });
      } else {
        await SS.api("/api/discovery/browse/start", { method: "POST" });
      }
      await poll();
    } catch {}
  }

  async function toggleAdvertise() {
    try {
      if (advertising) {
        await SS.api("/api/discovery/advertise/stop", { method: "POST" });
      } else {
        await SS.api("/api/discovery/advertise", { method: "POST" });
      }
      await poll();
    } catch {}
  }

  async function toggleTrustedConnections() {
    try {
      await SS.api("/api/trusted-connections", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !trustedConnectionsEnabled }),
      });
      await poll();
    } catch {}
  }

  return { devices, advertising, browsing, trustedConnectionsEnabled, toggleAdvertise, toggleBrowse, toggleTrustedConnections, refresh: poll };
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
    return <span className="tag">Saved</span>;
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

function DeviceRow({ d, onUnpair, onPerm, onConnect, compact }) {
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
            <button type="button" className="btn-secondary" onClick={() => onConnect && onConnect(d.id)}>Connect</button>
            <button type="button" className="btn-ghost" onClick={() => onUnpair && onUnpair(d.id)}>Remove</button>
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
        <button type="button" className="btn-secondary" onClick={() => onPair(d)}>Connect</button>
      </div>
    </div>
  );
}

// ── Pending pair display (this device — receives the request) ────────────────
// Shows the 6-digit code when another device requests to pair.
// After the code is consumed and the session eventually ends, asks the owner
// whether to keep or remove the remote device's trust record.

function PendingPairDisplay() {
  const [pending, setPending] = useState(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [trustPrompt, setTrustPrompt] = useState(null); // {name, device_id}

  const pendingRef = useRef(null);
  const dismissedRef = useRef(false);
  const pollRef = useRef(null);
  const tickRef = useRef(null);
  const sessionMonitorRef = useRef(null);

  useEffect(() => {
    async function poll() {
      try {
        const d = await SS.api("/api/discovery/pending-request");
        const wasPending = pendingRef.current;

        if (d.pending && d.code) {
          dismissedRef.current = false;
          pendingRef.current = d;
          setPending(prev => {
            if (!prev || prev.code !== d.code) setSecondsLeft(d.seconds_remaining || 45);
            return d;
          });
        } else {
          pendingRef.current = null;
          setPending(null);
          // Pending cleared and not dismissed — check if it was consumed
          if (wasPending && !dismissedRef.current) {
            try {
              const sessData = await SS.api("/api/pairing-sessions");
              const sess = (sessData.sessions || []).find(s => s.device_id === wasPending.requester_id);
              if (sess) monitorSession(sess.session_id, wasPending.requester_name, wasPending.requester_id);
            } catch {}
          }
        }
      } catch {}
    }
    poll();
    pollRef.current = setInterval(poll, 2000);
    return () => { clearInterval(pollRef.current); clearInterval(sessionMonitorRef.current); };
  }, []);

  function monitorSession(session_id, name, device_id) {
    clearInterval(sessionMonitorRef.current);
    sessionMonitorRef.current = setInterval(async () => {
      try {
        const sessData = await SS.api("/api/pairing-sessions");
        if (!(sessData.sessions || []).some(s => s.session_id === session_id)) {
          clearInterval(sessionMonitorRef.current);
          setTrustPrompt({ name, device_id });
        }
      } catch {}
    }, 3000);
  }

  useEffect(() => {
    if (!pending) { clearInterval(tickRef.current); return; }
    clearInterval(tickRef.current);
    tickRef.current = setInterval(() => {
      setSecondsLeft(s => {
        if (s <= 1) { clearInterval(tickRef.current); return 0; }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(tickRef.current);
  }, [pending?.code]);

  async function dismiss() {
    dismissedRef.current = true;
    try { await SS.api("/api/discovery/dismiss-request", { method: "POST" }); } catch {}
    setPending(null);
  }

  async function saveTrust() {
    if (trustPrompt?.device_id) {
      try {
        await SS.api("/api/trusted-devices/record", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ device_id: trustPrompt.device_id, name: trustPrompt.name }),
        });
      } catch {}
    }
    setTrustPrompt(null);
  }

  async function dismissTrust() {
    // Remove the device that was auto-added during pairing (remember_device:true).
    // This makes it reappear in discovery and requires re-pairing to reconnect.
    if (trustPrompt?.device_id) {
      try {
        await SS.api(`/api/trusted-devices/${trustPrompt.device_id}`, { method: "DELETE" });
      } catch {}
    }
    setTrustPrompt(null);
  }

  // Trust prompt — shown after the controlled session ends
  if (trustPrompt) {
    return (
      <div className="modal-overlay">
        <div className="modal">
          <div className="modal-h">
            <h2>Session ended</h2>
            <p>
              <strong style={{ color: "var(--text)" }}>{trustPrompt.name}</strong>
              {" "}just controlled this device. Save them as a trusted device to allow future connections without re-pairing?
            </p>
          </div>
          <div className="modal-foot">
            <button type="button" className="btn-primary" onClick={saveTrust}>Save as trusted</button>
            <button type="button" className="btn-ghost" onClick={dismissTrust}>Not now</button>
          </div>
        </div>
      </div>
    );
  }

  if (!pending) return null;
  const totalSecs = 45;
  return (
    <div className="modal-overlay">
      <div className="modal">
        <div className="modal-h">
          <h2>Incoming pairing request</h2>
          <p>
            <strong style={{ color: "var(--text)" }}>{pending.requester_name || "Another device"}</strong>
            {" "}wants to pair with this machine. Show them this code.
          </p>
        </div>
        <div className="modal-body" style={{ textAlign: "center" }}>
          <div className="pin-display">
            {String(pending.code).split("").map((c, i) => (
              <div key={i} className="pin-cell glow">{c}</div>
            ))}
          </div>
          <div className="countdown">
            <span>0:{String(secondsLeft).padStart(2, "0")}</span>
            <div className="countdown-bar">
              <div style={{ width: `${Math.max(0, (secondsLeft / totalSecs) * 100)}%` }} />
            </div>
            <span>0:{String(totalSecs).padStart(2, "0")}</span>
          </div>
          <div style={{ color: "var(--text-dim)", fontSize: 12, marginTop: 12 }}>
            Code expires automatically. Only accept requests from devices you recognise.
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn-ghost" onClick={dismiss}>Dismiss</button>
        </div>
      </div>
    </div>
  );
}

// ── Pair modal (initiating device side) ─────────────────────────────────────
// When target is a discovered device: auto-requests pair, goes straight to PIN.
// When opened via Add device: shows discovered devices first as quick options,
// with manual IP entry as a fallback.

function PairModal({ open, target, onClose, onComplete, discoveredDevices = [] }) {
  // steps: pick | requesting | enter | manual-host | connecting | failed
  // (no success step — modal closes immediately, parent shows a toast)
  const [step, setStep] = useState("idle");
  const [pin, setPin] = useState(["", "", "", "", "", ""]);
  const [manualHost, setManualHost] = useState("");
  const [manualPort, setManualPort] = useState("8765");
  const [pickedDevice, setPickedDevice] = useState(null);
  const [perms, setPerms] = useState({ mouse: true, keyboard: true, clipboard: true, files: false });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRefs = useRef([]);
  const pendingRequestRef = useRef(null); // {host, port} when a request-pair is in flight

  // Close the modal. If a request-pair was sent and not yet completed or cancelled,
  // tell Screen B to discard it immediately rather than waiting for the 45s timeout.
  function handleClose() {
    if (pendingRequestRef.current) {
      const { host, port } = pendingRequestRef.current;
      pendingRequestRef.current = null;
      SS.api("/api/discovery/remote/cancel-pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host, port }),
      }).catch(() => {});
    }
    onClose();
  }

  // Reset and kick off the right flow when modal opens
  useEffect(() => {
    if (!open) return;
    setPin(["","","","","",""]);
    setErr("");
    setBusy(false);
    setPickedDevice(null);
    if (target) {
      // Clicked Connect on a discovered row — auto-request
      setStep("requesting");
      pendingRequestRef.current = { host: target.host, port: target.port || 8765 };
      SS.api("/api/discovery/remote/request-pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: target.host, port: target.port || 8765 }),
      }).then(() => setStep("enter"))
        .catch(e => { pendingRequestRef.current = null; setErr(e.message || "Could not reach device."); setStep("failed"); });
    } else {
      // Add device button — show discovered devices first
      setStep("pick");
    }
  }, [open]);

  function handlePinChange(i, v) {
    const c = v.replace(/\D/g, "").slice(0, 1);
    const next = [...pin];
    next[i] = c;
    setPin(next);
    if (c && i < 5) inputRefs.current[i + 1]?.focus();
  }

  function handlePinKey(i, e) {
    if (e.key === "Backspace" && !pin[i] && i > 0) inputRefs.current[i - 1]?.focus();
    if (e.key === "Enter") {
      const full = [...pin.slice(0, i), pin[i] || "", ...pin.slice(i + 1)];
      if (full.every(c => c)) submitPin(full.join(""));
    }
  }

  function quickPair(d) {
    setPickedDevice(d);
    setStep("requesting");
    pendingRequestRef.current = { host: d.host, port: d.port || 8765 };
    SS.api("/api/discovery/remote/request-pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host: d.host, port: d.port || 8765 }),
    }).then(() => setStep("enter"))
      .catch(e => { pendingRequestRef.current = null; setErr(e.message || "Could not reach device."); setPickedDevice(null); setStep("pick"); });
  }

  async function startManualRequest(e) {
    e.preventDefault();
    if (!manualHost.trim()) return;
    setErr("");
    setStep("requesting");
    const mHost = manualHost.trim(), mPort = Number(manualPort) || 8765;
    pendingRequestRef.current = { host: mHost, port: mPort };
    try {
      await SS.api("/api/discovery/remote/request-pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host: mHost, port: mPort }),
      });
      setStep("enter");
    } catch (e2) {
      pendingRequestRef.current = null;
      setErr(e2.message || "Could not reach device.");
      setStep("manual-host");
    }
  }

  async function submitPin(code) {
    if (busy) return;
    setBusy(true);
    setErr("");
    setStep("connecting");
    const eff = target || pickedDevice;
    const host = eff?.host || manualHost.trim();
    const port = eff?.port || Number(manualPort) || 8765;
    const deviceId = eff?.device_id;
    const deviceName = eff?.name || host;
    try {
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
      if (data.shared_secret && deviceId) {
        try { localStorage.setItem(`slickshiftTrusted_${deviceId}`, JSON.stringify({ shared_secret: data.shared_secret, host, port })); } catch {}
      }
      // Record Screen B in Screen A's own trusted-device store so it appears
      // in this machine's trusted list (pairing only writes to Screen B's store
      // by default — this call writes the symmetric record on Screen A).
      if (deviceId) {
        try {
          await SS.api("/api/trusted-devices/record", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ device_id: deviceId, name: deviceName }),
          });
        } catch {}
      }
      // Pairing succeeded — clear cancel guard before closing
      pendingRequestRef.current = null;
      onComplete && onComplete({ host, port, device_id: deviceId, name: deviceName }, perms, data);
      onClose();
    } catch (e2) {
      setErr(e2.message || "Pairing failed — check the code and try again.");
      setStep("enter");
      setPin(["","","","","",""]);
    } finally {
      setBusy(false);
    }
  }

  const effName = (target || pickedDevice)?.name || manualHost;

  if (!open) return null;
  return (
    <div className="modal-overlay" onClick={step === "connecting" ? undefined : handleClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>

        {/* Pick step — shown when "Add device" is clicked with no specific target */}
        {step === "pick" && (
          <>
            <div className="modal-h">
              <h2>Add device</h2>
              <p>Select a device on your network, or enter an IP address manually.</p>
            </div>
            <div className="modal-body">
              {discoveredDevices.length > 0 ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {discoveredDevices.map(d => (
                    <button key={d.device_id} type="button"
                      style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 14px", background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: 8, cursor: "pointer", textAlign: "left" }}
                      onClick={() => quickPair(d)}>
                      {platformIcon(d.platform || "other", 20)}
                      <div>
                        <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text)" }}>{d.name}</div>
                        <div style={{ fontSize: 11, color: "var(--muted)" }}>{d.host}</div>
                      </div>
                      <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--accent)" }}>Pair →</span>
                    </button>
                  ))}
                </div>
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 13, marginBottom: 12 }}>
                  No devices found yet. Enable "Allow connections" and "Search" on the other device.
                </div>
              )}
              {err && <div style={{ color: "var(--danger)", fontSize: 12, marginTop: 8 }}>{err}</div>}
              <button type="button" className="btn-ghost"
                style={{ marginTop: 14, width: "100%", justifyContent: "center" }}
                onClick={() => setStep("manual-host")}>
                Enter IP address manually…
              </button>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn-ghost" onClick={handleClose}>Cancel</button>
            </div>
          </>
        )}

        {step === "requesting" && (
          <div className="modal-body connecting">
            <div className="spinner" />
            <div style={{ fontSize: 14, color: "var(--text-dim)" }}>
              Sending request to {target?.name || pickedDevice?.name || manualHost}…
            </div>
          </div>
        )}

        {step === "manual-host" && (
          <>
            <div className="modal-h">
              <h2>Enter IP address</h2>
              <p>The device must be on the same network and have Screen Slickshift running.</p>
            </div>
            <div className="modal-body">
              <form onSubmit={startManualRequest}>
                <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                  <input type="text" value={manualHost} onChange={e => setManualHost(e.target.value)}
                    placeholder="192.168.1.x" autoFocus
                    style={{ flex: 1, padding: "8px 10px", background: "var(--panel-2)", border: "1px solid var(--border-strong)", borderRadius: 8, color: "var(--text)", fontSize: 14, fontFamily: "var(--mono)", outline: "none" }} />
                  <input type="text" value={manualPort} onChange={e => setManualPort(e.target.value)}
                    placeholder="8765"
                    style={{ width: 72, padding: "8px 10px", background: "var(--panel-2)", border: "1px solid var(--border-strong)", borderRadius: 8, color: "var(--text)", fontSize: 14, fontFamily: "var(--mono)", outline: "none" }} />
                </div>
                {err && <div style={{ color: "var(--danger)", fontSize: 12, marginBottom: 10 }}>{err}</div>}
                <button type="submit" className="btn-primary" style={{ width: "100%" }}>Request pairing code</button>
              </form>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn-ghost" onClick={() => setStep("pick")}>Back</button>
              <button type="button" className="btn-ghost" onClick={handleClose}>Cancel</button>
            </div>
          </>
        )}

        {step === "enter" && (
          <>
            <div className="modal-h">
              <h2>Enter pairing code</h2>
              <p>A 6-digit code is now appearing on <strong style={{ color: "var(--text)" }}>{effName}</strong>. Enter it here.</p>
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
              <button type="button" className="btn-primary"
                disabled={!pin.every(c => c) || busy}
                onClick={() => submitPin(pin.join(""))}>
                {busy ? "Confirming…" : "Confirm"}
              </button>
              <button type="button" className="btn-ghost" onClick={handleClose}>Cancel</button>
            </div>
          </>
        )}

        {step === "failed" && (
          <>
            <div className="modal-h">
              <h2>Could not connect</h2>
              <p style={{ color: "var(--danger)" }}>{err}</p>
            </div>
            <div className="modal-foot">
              <button type="button" className="btn-ghost" onClick={() => setStep("pick")}>Back</button>
              <button type="button" className="btn-ghost" onClick={handleClose}>Close</button>
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
      </div>
    </div>
  );
}

// ── Key map (browser key name → protocol/pyautogui name) ────────────────────
const REMOTE_KEY_MAP = {
  Enter: "enter", Tab: "tab", Backspace: "backspace", Delete: "delete",
  ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right",
  Home: "home", End: "end", PageUp: "pageup", PageDown: "pagedown",
  Insert: "insert", CapsLock: "capslock", NumLock: "numlock", ScrollLock: "scrolllock",
  F1: "f1", F2: "f2", F3: "f3", F4: "f4", F5: "f5", F6: "f6",
  F7: "f7", F8: "f8", F9: "f9", F10: "f10", F11: "f11", F12: "f12",
};

// ── Active control overlay ───────────────────────────────────────────────────
// Full-screen overlay that forwards mouse and keyboard to the remote machine.
// Pointer lock is used for relative mouse movement; without it, clicking the
// overlay requests lock instead of forwarding.

function ActiveControlOverlay({ deviceName, onStop }) {
  const overlayRef = useRef(null);
  const [pointerLocked, setPointerLocked] = useState(false);
  const lastMoveRef = useRef(0);

  useEffect(() => {
    overlayRef.current?.focus();
    function onChange() {
      setPointerLocked(document.pointerLockElement === overlayRef.current);
    }
    document.addEventListener("pointerlockchange", onChange);
    document.addEventListener("pointerlockerror", onChange);
    return () => {
      document.removeEventListener("pointerlockchange", onChange);
      document.removeEventListener("pointerlockerror", onChange);
      if (document.pointerLockElement) document.exitPointerLock();
    };
  }, []);

  async function sendEvent(msg) {
    try {
      await SS.api("/api/handoff/remote/event", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(msg),
      });
    } catch { onStop(); }
  }

  // Mouse movement via pointer lock
  useEffect(() => {
    function onMove(e) {
      if (!document.pointerLockElement) return;
      const dx = e.movementX || 0, dy = e.movementY || 0;
      if (!dx && !dy) return;
      const now = Date.now();
      if (now - lastMoveRef.current < 12) return;
      lastMoveRef.current = now;
      sendEvent({ type: "mouse_move", dx, dy });
    }
    document.addEventListener("mousemove", onMove);
    return () => document.removeEventListener("mousemove", onMove);
  }, []);

  // Keyboard forwarding
  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === "Escape") {
        if (document.pointerLockElement) { document.exitPointerLock(); return; }
        onStop(); return;
      }
      if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;
      const key = REMOTE_KEY_MAP[e.key] ?? (e.key.length === 1 ? e.key : null);
      if (!key) return;
      e.preventDefault();
      sendEvent({ type: "keyboard", key, ctrl: e.ctrlKey, alt: e.altKey, shift: e.shiftKey, meta: e.metaKey });
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  function requestLock() { overlayRef.current?.requestPointerLock?.(); }

  function onPointerDown(e) {
    if (!document.pointerLockElement) { requestLock(); return; }
    const btn = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
    sendEvent({ type: "mouse_button", button: btn, down: true });
  }
  function onPointerUp(e) {
    if (!document.pointerLockElement) return;
    const btn = e.button === 2 ? "right" : e.button === 1 ? "middle" : "left";
    sendEvent({ type: "mouse_button", button: btn, down: false });
  }
  function onWheel(e) {
    e.preventDefault();
    sendEvent({ type: "scroll", amount: e.deltaY < 0 ? 3 : -3 });
  }

  return (
    <div ref={overlayRef} tabIndex={-1}
      style={{
        position: "fixed", inset: 0, zIndex: 500, outline: "none",
        background: "rgba(8,7,26,0.92)", display: "flex", flexDirection: "column",
        cursor: pointerLocked ? "none" : "default",
      }}
      onPointerDown={onPointerDown} onPointerUp={onPointerUp}
      onWheel={onWheel} onContextMenu={e => e.preventDefault()}>

      {/* Top bar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 12, padding: "14px 20px",
        background: "rgba(21,19,46,0.98)", borderBottom: "1px solid var(--border)",
        userSelect: "none",
      }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--ok)", flexShrink: 0 }} />
        <div style={{ flex: 1 }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>Controlling {deviceName}</span>
          <span style={{ marginLeft: 12, fontSize: 12, color: "var(--text-dim)" }}>
            {pointerLocked ? "Cursor captured · Esc to release" : "Click anywhere to capture cursor"}
          </span>
        </div>
        <button type="button" className="btn-ghost" style={{ fontSize: 12 }}
          onClick={() => pointerLocked ? document.exitPointerLock() : requestLock()}>
          {pointerLocked ? "Release Cursor" : "Capture Cursor"}
        </button>
        <button type="button" className="btn-danger" onClick={onStop}>Disconnect</button>
      </div>

      {/* Centre prompt when cursor is not yet captured */}
      {!pointerLocked && (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <div style={{ textAlign: "center", color: "var(--text-dim)", maxWidth: 360 }}>
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 8, color: "var(--text)" }}>
              Controlling {deviceName}
            </div>
            <div style={{ fontSize: 13, marginBottom: 20 }}>
              Click anywhere to capture your cursor and begin. Mouse and keyboard will be forwarded to {deviceName}.
            </div>
            <button type="button" className="btn-primary" onClick={requestLock}>Capture Cursor</button>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 12 }}>
              Esc releases cursor · Disconnect stops the session
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Devices section ──────────────────────────────────────────────────────────

function DevicesSection() {
  const { thisDevice, paired, loading, error, load, unpair, setPerm } = useDeviceData();
  const { devices: discovered, advertising, browsing, trustedConnectionsEnabled, toggleAdvertise, toggleBrowse, toggleTrustedConnections, refresh: discoveryRefresh } = useDiscovery(true);
  const [pairOpen, setPairOpen] = useState(false);
  const [pairTarget, setPairTarget] = useState(null);
  const [reconnectMsg, setReconnectMsg] = useState("");
  const [toast, setToast] = useState("");
  const [activeControl, setActiveControl] = useState(null); // {deviceName}
  const toastTimer = useRef(null);

  function showToast(msg) {
    setToast(msg);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 4000);
  }

  async function completePair(connInfo, _perms, pairData) {
    showToast(`Saved ${connInfo?.name || connInfo?.host || "device"}`);
    await load();
    await discoveryRefresh();

    // Start bridge, warp cursor, enter active control.
    if (pairData?.session?.session_id && pairData?.session_token && connInfo?.host) {
      try {
        const status = await SS.api("/api/handoff/remote/status");
        if (!status.connected) {
          await SS.api("/api/handoff/remote/start-session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              host: connInfo.host,
              port: connInfo.port || 8765,
              session_id: pairData.session.session_id,
              session_token: pairData.session_token,
            }),
          });
        }
        await SS.api("/api/handoff/remote/warp-cursor", { method: "POST" });
        setActiveControl({ deviceName: connInfo.name || connInfo.host });
      } catch {}
    }
  }

  async function handleConnect(device_id) {
    setReconnectMsg("");
    const name = paired.find(d => d.id === device_id)?.name || device_id;
    try {
      // Check if bridge is already active for this device; if not, establish it.
      const status = await SS.api("/api/handoff/remote/status");
      if (!status.connected) {
        let cred = null;
        try { const raw = localStorage.getItem(`slickshiftTrusted_${device_id}`); if (raw) cred = JSON.parse(raw); } catch {}
        if (!cred?.shared_secret) {
          setReconnectMsg(`${name} is not on the network yet. Make sure Screen Slickshift is running on it.`);
          return;
        }
        const found = discovered.find(d => d.device_id === device_id)
          || (cred?.host ? { host: cred.host, port: cred.port || 8765 } : null);
        if (!found) {
          setReconnectMsg(`${name} not found on the network. Make sure Screen Slickshift is running on it.`);
          return;
        }
        const data = await SS.api("/api/discovery/remote/reconnect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host: found.host, port: found.port || 8765, shared_secret: cred.shared_secret }),
        });
        if (data?.session?.session_id && data?.session_token) {
          await SS.api("/api/handoff/remote/start-session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ host: found.host, port: found.port || 8765, session_id: data.session.session_id, session_token: data.session_token }),
          });
        }
      }
      // Warp the remote cursor to the centre of its screen, then enter active control.
      await SS.api("/api/handoff/remote/warp-cursor", { method: "POST" });
      setActiveControl({ deviceName: name });
    } catch (e) {
      setReconnectMsg(`Connect failed: ${e.message}`);
    }
  }

  async function stopControl() {
    setActiveControl(null);
    try { await SS.api("/api/handoff/remote/stop", { method: "POST" }); } catch {}
    showToast("Disconnected.");
  }

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Devices</h1>
          <div className="sub">Devices you trust to share input, clipboard, and files.</div>
        </div>
        <button type="button" className="btn-primary" onClick={() => { setPairTarget(null); setPairOpen(true); }}>
          <Icons.Plus size={14} />Add device
        </button>
      </div>

      {error && <div style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>{error}</div>}
      {reconnectMsg && <div style={{ color: reconnectMsg.startsWith("Reconnected") ? "var(--ok)" : "var(--warn)", fontSize: 13, marginBottom: 12 }}>{reconnectMsg}</div>}

      {/* Trusted devices — This Device excluded, lives at bottom */}
      <div className="panel">
        <div className="panel-h">
          <h2>Trusted devices</h2>
          <span className="h-sub">{paired.length} paired</span>
        </div>
        <div className="panel-body">
          {loading ? (
            <div style={{ color: "var(--muted)", fontSize: 13, padding: "12px 0" }}>Loading…</div>
          ) : paired.length === 0 ? (
            <div style={{ color: "var(--muted)", fontSize: 13, padding: "12px 0" }}>
              No paired devices yet. Click Add device to pair one.
            </div>
          ) : (
            paired.map(d => (
              <DeviceRow key={d.id} d={d} onUnpair={unpair} onPerm={setPerm} onConnect={handleConnect} />
            ))
          )}
        </div>
      </div>

      {/* Available devices — toggle-gated search */}
      <div className="panel">
        <div className="panel-h">
          <h2>Search for available devices</h2>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <span style={{ fontSize: 12, color: browsing ? "var(--ok)" : "var(--muted)" }}>
              {browsing ? "On" : "Off"}
            </span>
            <div className={"toggle " + (browsing ? "on" : "")} onClick={toggleBrowse} />
          </label>
        </div>
        <div className="panel-body">
          {!browsing ? (
            <div style={{ color: "var(--muted)", fontSize: 13, padding: "8px 0" }}>
              Enable search to find nearby devices running Screen Slickshift.
            </div>
          ) : discovered.length === 0 ? (
            <div className="disc-empty">
              <div className="scan"><span className="scan-dot" />Scanning the network…</div>
              <div>Make sure Screen Slickshift is running on the other device.</div>
            </div>
          ) : (
            discovered.map(d => (
              <DiscoveredRow key={d.device_id} d={d}
                onPair={dev => { setPairTarget(dev); setPairOpen(true); }} />
            ))
          )}
        </div>
      </div>

      {/* This device — anchored at bottom */}
      <div style={{ position: "sticky", bottom: "var(--pad)", marginTop: 8, zIndex: 1 }}>
        <div className="panel" style={{ background: "var(--panel-2)" }}>
          <div className="panel-h">
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {thisDevice && platformIcon(osToPlatform(thisDevice.os || ""), 18)}
              <div>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{thisDevice?.name || "This device"}</div>
                <div style={{ fontSize: 11, color: "var(--muted)" }}>This device</div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 20, alignItems: "center" }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <span style={{ fontSize: 12, color: advertising ? "var(--ok)" : "var(--muted)" }}>
                  {advertising ? "Visible" : "Hidden"}
                </span>
                <div className={"toggle " + (advertising ? "on" : "")} onClick={toggleAdvertise} />
                <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Allow connections</span>
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <span style={{ fontSize: 12, color: trustedConnectionsEnabled ? "var(--ok)" : "var(--muted)" }}>
                  {trustedConnectionsEnabled ? "On" : "Off"}
                </span>
                <div className={"toggle " + (trustedConnectionsEnabled ? "on" : "")} onClick={toggleTrustedConnections} />
                <span style={{ fontSize: 12, color: "var(--text-dim)" }}>Trusted device connections</span>
              </label>
            </div>
          </div>
        </div>
      </div>

      <PairModal open={pairOpen} target={pairTarget} discoveredDevices={discovered}
        onClose={() => setPairOpen(false)} onComplete={completePair} />
      <PendingPairDisplay />
      {activeControl && <ActiveControlOverlay deviceName={activeControl.deviceName} onStop={stopControl} />}

      {toast && (
        <div style={{
          position: "fixed", top: 20, right: 20, zIndex: 200,
          background: "var(--panel-2)", border: "1px solid var(--border-strong)",
          borderRadius: 10, padding: "10px 16px", display: "flex", alignItems: "center", gap: 8,
          color: "var(--ok)", fontSize: 13, fontWeight: 500,
          boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
        }}>
          <Icons.Check size={14} />{toast}
        </div>
      )}
    </>
  );
}

// ── Overview section ─────────────────────────────────────────────────────────

function OverviewSection() {
  const { thisDevice, paired, loading, load, unpair } = useDeviceData();
  const { devices: discovered, refresh: discoveryRefresh } = useDiscovery(true);
  const [pairOpen, setPairOpen] = useState(false);
  const [activeControl, setActiveControl] = useState(null);
  const [edgeHandoff, setEdgeHandoff] = useState(false);
  const [edgeRel, setEdgeRel] = useState(null);
  const [detectorState, setDetectorState] = useState("idle");
  const [dwellProgress, setDwellProgress] = useState(0);
  const connectingRef = useRef(false);
  const bridgeWarmRef = useRef(false);
  const [overviewToast, setOverviewToast] = useState("");
  const overviewToastTimer = useRef(null);

  function showOverviewToast(msg) {
    setOverviewToast(msg);
    clearTimeout(overviewToastTimer.current);
    overviewToastTimer.current = setTimeout(() => setOverviewToast(""), 6000);
  }

  async function stopControl(keepBridgeForHandoff = false) {
    // Warp local cursor to center so it doesn't snap back to the edge
    try { await SS.api("/api/handoff/warp-cursor", { method: "POST" }); } catch {}
    setActiveControl(null);
    if (keepBridgeForHandoff && edgeHandoff) {
      bridgeWarmRef.current = true;
    } else {
      bridgeWarmRef.current = false;
      try { await SS.api("/api/handoff/remote/stop", { method: "POST" }); } catch {}
    }
  }

  async function handleConnect(device_id, returnEdge = null) {
    if (connectingRef.current) return;
    connectingRef.current = true;
    const name = paired.find(d => d.id === device_id)?.name || device_id;
    let cred = null;
    try { const raw = localStorage.getItem(`slickshiftTrusted_${device_id}`); if (raw) cred = JSON.parse(raw); } catch {}
    if (!cred?.shared_secret) { connectingRef.current = false; return; }
    const found = discovered.find(d => d.device_id === device_id)
      || (cred?.host ? { host: cred.host, port: cred.port || 8765 } : null);
    if (!found) { connectingRef.current = false; return; }
    try {
      const status = await SS.api("/api/handoff/remote/status");
      if (!status.connected) {
        const data = await SS.api("/api/discovery/remote/reconnect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host: found.host, port: found.port || 8765, shared_secret: cred.shared_secret }),
        });
        if (data?.session?.session_id && data?.session_token) {
          await SS.api("/api/handoff/remote/start-session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ host: found.host, port: found.port || 8765, session_id: data.session.session_id, session_token: data.session_token }),
          });
        }
      }
      // Warp local cursor off the edge immediately so it doesn't feel "stuck"
      try { await SS.api("/api/handoff/warp-cursor", { method: "POST" }); } catch {}
      await SS.api("/api/handoff/remote/warp-cursor", { method: "POST" });
      if (returnEdge) {
        try {
          await SS.api("/api/handoff/remote/arm-return", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ return_edge: returnEdge, dwell_ms: 0 }),
          });
        } catch {}
      }
      setActiveControl({ deviceName: name });
    } catch (e) {
      if (e?.message) showOverviewToast(e.message);
    } finally {
      connectingRef.current = false;
    }
  }

  // Arm local edge detector when handoff is on, screens are adjacent, and not currently controlling
  useEffect(() => {
    if (!edgeHandoff || !edgeRel || activeControl) {
      SS.api("/api/handoff/disarm", { method: "POST" }).catch(() => {});
      if (!edgeHandoff && bridgeWarmRef.current) {
        bridgeWarmRef.current = false;
        SS.api("/api/handoff/remote/stop", { method: "POST" }).catch(() => {});
      }
      setDetectorState("idle");
      setDwellProgress(0);
      return;
    }
    SS.api("/api/handoff/arm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ edge: edgeRel.localEdge, dwell_ms: 0 }),
    }).catch(() => {});
  }, [edgeHandoff, edgeRel?.localEdge, !!activeControl]);

  // Poll local detector — trigger connect when cursor dwells at the armed edge
  useEffect(() => {
    if (!edgeHandoff || !edgeRel || activeControl) return;
    let alive = true;
    const poll = async () => {
      try {
        const d = await SS.api("/api/handoff/detector/state");
        if (!alive) return;
        setDetectorState(d.state || "idle");
        setDwellProgress(d.dwell_progress ?? 0);
        if (d.state === "pending") handleConnect(edgeRel.remoteId, edgeRel.returnEdge);
      } catch {}
    };
    poll();
    const t = setInterval(poll, 16);
    return () => { alive = false; clearInterval(t); };
  }, [edgeHandoff, edgeRel?.localEdge, edgeRel?.remoteId, !!activeControl]);

  // Poll remote return detector — auto-disconnect when cursor dwells at the return edge
  useEffect(() => {
    if (!edgeHandoff || !activeControl) return;
    let alive = true;
    const t = setInterval(async () => {
      try {
        const d = await SS.api("/api/handoff/remote/return-state");
        if (!alive) return;
        if (d.state === "pending") stopControl(true); // keep bridge warm for next edge trigger
      } catch {}
    }, 200);
    return () => { alive = false; clearInterval(t); };
  }, [edgeHandoff, !!activeControl]);

  // Pre-connect bridge in background so the edge trigger is near-instant
  useEffect(() => {
    if (!edgeHandoff || !edgeRel || activeControl) return;
    let alive = true;
    async function warmBridge() {
      const device_id = edgeRel.remoteId;
      let cred = null;
      try { const raw = localStorage.getItem(`slickshiftTrusted_${device_id}`); if (raw) cred = JSON.parse(raw); } catch {}
      if (!cred?.shared_secret) return;
      const found = discovered.find(d => d.device_id === device_id)
        || (cred?.host ? { host: cred.host, port: cred.port || 8765 } : null);
      if (!found || !alive) return;
      try {
        const status = await SS.api("/api/handoff/remote/status");
        if (!alive) return;
        if (status.connected) { bridgeWarmRef.current = true; return; }
        const data = await SS.api("/api/discovery/remote/reconnect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host: found.host, port: found.port || 8765, shared_secret: cred.shared_secret }),
        });
        if (!alive || !data?.session?.session_id) return;
        await SS.api("/api/handoff/remote/start-session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ host: found.host, port: found.port || 8765, session_id: data.session.session_id, session_token: data.session_token }),
        });
        if (alive) bridgeWarmRef.current = true;
      } catch {}
    }
    warmBridge();
    return () => { alive = false; };
  }, [edgeHandoff, edgeRel?.remoteId, !!activeControl]);

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
            ) : paired.length === 0 ? (
              <div style={{ color: "var(--muted)", fontSize: 13, padding: "8px 0" }}>No paired devices yet.</div>
            ) : (
              paired.map(d => <DeviceRow key={d.id} d={d} onUnpair={unpair} onConnect={handleConnect} compact />)
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

        <OverviewEdges paired={paired} thisDevice={thisDevice}
          onEdgeChange={setEdgeRel}
          edgeHandoff={edgeHandoff}
          onToggleEdgeHandoff={() => setEdgeHandoff(v => !v)}
          detectorState={detectorState}
          dwellProgress={dwellProgress}
          activeControl={activeControl} />
      </div>

      {overviewToast && (
        <div style={{
          position: "fixed", top: 20, right: 20, zIndex: 200,
          background: "var(--panel-2)", border: "1px solid var(--border-strong)",
          borderRadius: 10, padding: "10px 16px", maxWidth: 360,
          color: "var(--warn)", fontSize: 13, fontWeight: 500,
          boxShadow: "0 8px 24px rgba(0,0,0,0.45)",
        }}>
          {overviewToast}
        </div>
      )}

      <PairModal open={pairOpen} target={null}
        onClose={() => setPairOpen(false)}
        onComplete={async (connInfo, _p, pairData) => {
          await load();
          await discoveryRefresh();
          if (pairData?.session?.session_id && pairData?.session_token && connInfo?.host) {
            try {
              const st = await SS.api("/api/handoff/remote/status");
              if (!st.connected) {
                await SS.api("/api/handoff/remote/start-session", {
                  method: "POST", headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ host: connInfo.host, port: connInfo.port || 8765, session_id: pairData.session.session_id, session_token: pairData.session_token }),
                });
              }
              await SS.api("/api/handoff/remote/warp-cursor", { method: "POST" });
              setActiveControl({ deviceName: connInfo.name || connInfo.host });
            } catch {}
          }
        }} />
      <PendingPairDisplay />
      {activeControl && <ActiveControlOverlay deviceName={activeControl.deviceName} onStop={stopControl} />}
    </>
  );
}

// ── Edge relationship helper ─────────────────────────────────────────────────

function computeEdgeRel(tiles) {
  const local = tiles.find(t => t.role === "local");
  const remote = tiles.find(t => t.role === "target");
  if (!local || !remote) return null;
  const SNAP = 16;
  if (Math.abs((local.x + local.w) - remote.x) < SNAP)
    return { localEdge: "right", returnEdge: "left", remoteId: remote.id, remoteName: remote.name };
  if (Math.abs(local.x - (remote.x + remote.w)) < SNAP)
    return { localEdge: "left", returnEdge: "right", remoteId: remote.id, remoteName: remote.name };
  if (Math.abs((local.y + local.h) - remote.y) < SNAP)
    return { localEdge: "bottom", returnEdge: "top", remoteId: remote.id, remoteName: remote.name };
  if (Math.abs(local.y - (remote.y + remote.h)) < SNAP)
    return { localEdge: "top", returnEdge: "bottom", remoteId: remote.id, remoteName: remote.name };
  return null;
}

// ── Overview edge canvas ─────────────────────────────────────────────────────

function OverviewEdges({ paired, thisDevice, onEdgeChange, edgeHandoff, onToggleEdgeHandoff, detectorState, dwellProgress, activeControl }) {
  const W = 360, H = 220;
  const [tiles, setTiles] = useState([]);
  const [drag, setDrag] = useState(null);

  useEffect(() => {
    const baseTiles = [];
    if (thisDevice) {
      baseTiles.push({ id: "local", name: thisDevice.name, role: "local", x: 36, y: 56, w: 130, h: 80, label: "" });
    }
    paired.slice(0, 3).forEach((d, i) => {
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

  // Report edge relationship to parent whenever tiles are repositioned
  useEffect(() => {
    onEdgeChange && onEdgeChange(computeEdgeRel(tiles));
  }, [tiles]);

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

  const edgeRel = computeEdgeRel(tiles);

  return (
    <div className="panel">
      <div className="panel-h">
        <div>
          <h2>Screen edges</h2>
          <span className="h-sub">
            {edgeHandoff && edgeRel
              ? `${edgeRel.localEdge} edge → ${edgeRel.remoteName}`
              : "Drag to arrange · snap edges to enable handoff"}
          </span>
        </div>
        <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
          {edgeHandoff && edgeRel && !activeControl && detectorState === "armed" && dwellProgress > 0 && (
            <div style={{ width: 48, height: 4, borderRadius: 2, background: "var(--border-strong)", overflow: "hidden" }}>
              <div style={{ width: `${dwellProgress * 100}%`, height: "100%", background: "var(--accent)", transition: "width 0.1s linear" }} />
            </div>
          )}
          {edgeHandoff && activeControl && (
            <span style={{ fontSize: 11, color: "var(--ok)" }}>Active</span>
          )}
          {edgeHandoff && !edgeRel && (
            <span style={{ fontSize: 11, color: "var(--warn)" }}>Snap screens first</span>
          )}
          <span style={{ fontSize: 12, color: edgeHandoff ? (edgeRel ? "var(--ok)" : "var(--warn)") : "var(--muted)" }}>
            {edgeHandoff ? "On" : "Off"}
          </span>
          <div className={"toggle " + (edgeHandoff ? "on" : "")} onClick={onToggleEdgeHandoff} />
        </label>
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
