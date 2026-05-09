// devices.jsx — Devices section + multi-step pairing flow

const { useState, useEffect, useRef } = React;

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
    return <span className="tag live" style={{ color: "var(--accent-2)", borderColor: "rgba(192,132,252,0.4)", background: "rgba(192,132,252,0.12)" }}><i className="live-dot" style={{ background: "var(--accent-2)", boxShadow: "0 0 6px var(--accent-2)" }} />Being controlled</span>;
  if (d.status === "connected") return <span className="tag" style={{ color: "var(--ok)", borderColor: "rgba(74,222,128,0.3)" }}>Online</span>;
  if (d.status === "paired") return <span className="tag">Paired · {d.lastSeen}</span>;
  return null;
}

function PermPill({ label, on, onChange }) {
  return (
    <div className={"perm-pill " + (on ? "on" : "")} onClick={() => onChange(!on)} title={`${label}: ${on ? "allowed" : "blocked"}`}>
      <span className={"toggle-mini " + (on ? "on" : "")}><i /></span>
      <span>{label}</span>
    </div>
  );
}

function DeviceRow({ d, onUnpair, onPerm, compact }) {
  return (
    <div className={"dev-row" + (compact ? " compact" : "")}>
      <div className={"dev-icon " + (d.self ? "self" : "")}>
        {platformIcon(d.platform)}
        {!d.self && <span className="stat-dot" style={{ background: statusColor(d.status === "connected" || d.direction ? "connected" : "paired") }} />}
      </div>
      <div className="dev-main">
        <div className="dev-name">
          {d.name}
          {statusTag(d)}
        </div>
        <div className="dev-meta">{d.model}{d.host ? ` · ${d.host}` : ""}</div>
      </div>
      <div className="dev-actions">
        {d.self ? (
          <button className="btn-ghost">Rename</button>
        ) : (
          <>
            {!compact && d.perms && (
              <div className="perm-row-inline">
                <PermPill label="Clipboard" on={d.perms.clipboard} onChange={(v) => onPerm(d.id, "clipboard", v)} />
                <PermPill label="Files" on={d.perms.files} onChange={(v) => onPerm(d.id, "files", v)} />
                <PermPill label="Macros" on={d.perms.macros} onChange={(v) => onPerm(d.id, "macros", v)} />
              </div>
            )}
            <button className="btn-ghost" onClick={() => onUnpair && onUnpair(d.id)}>Unpair</button>
          </>
        )}
      </div>
    </div>
  );
}

function DiscoveredRow({ d, onPair }) {
  return (
    <div className="dev-row">
      <div className="dev-icon">
        {platformIcon(d.platform)}
      </div>
      <div>
        <div className="dev-name">{d.name}</div>
        <div className="dev-meta">{d.host} · advertising on LAN</div>
      </div>
      <div className="dev-actions">
        <button className="btn-secondary" onClick={() => onPair(d)}>Pair</button>
      </div>
    </div>
  );
}

// ─────────────── Pair Modal ────────────────────
function PairModal({ open, target, onClose, onComplete }) {
  // step: 'method' | 'enter' | 'show' | 'connecting' | 'success'
  const [step, setStep] = useState("method");
  const [pin, setPin] = useState(["", "", "", "", "", ""]);
  const [secondsLeft, setSecondsLeft] = useState(60);
  const [perms, setPerms] = useState({ mouse: true, keyboard: true, clipboard: true, files: false });
  const [showCode] = useState(generateCode());
  const inputRefs = useRef([]);

  function generateCode() {
    return String(Math.floor(100000 + Math.random() * 900000));
  }

  // reset on open
  useEffect(() => {
    if (open) {
      setStep(target ? "enter" : "method");
      setPin(["", "", "", "", "", ""]);
      setSecondsLeft(60);
    }
  }, [open, target]);

  // countdown for "show" mode
  useEffect(() => {
    if (step !== "show") return;
    const t = setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          clearInterval(t);
          setStep("connecting");
          setTimeout(() => setStep("success"), 1400);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(t);
  }, [step]);

  function handlePinChange(i, v) {
    const cleaned = v.replace(/\D/g, "").slice(0, 1);
    const next = [...pin];
    next[i] = cleaned;
    setPin(next);
    if (cleaned && i < 5) inputRefs.current[i + 1]?.focus();
    if (next.every((c) => c)) {
      // submit
      setTimeout(() => {
        setStep("connecting");
        setTimeout(() => setStep("success"), 1500);
      }, 200);
    }
  }
  function handlePinKey(i, e) {
    if (e.key === "Backspace" && !pin[i] && i > 0) inputRefs.current[i - 1]?.focus();
  }
  function fillDemoPin() {
    setPin(["3", "1", "4", "1", "5", "9"]);
    setTimeout(() => {
      setStep("connecting");
      setTimeout(() => setStep("success"), 1500);
    }, 250);
  }

  if (!open) return null;
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {step === "method" && (
          <>
            <div className="modal-h">
              <h2>Pair a new device</h2>
              <p>Both devices must be on the same local network. No data leaves your LAN.</p>
            </div>
            <div className="modal-body">
              <div className="method">
                <button className="method-card" onClick={() => setStep("enter")}>
                  <div className="m-ico"><Icons.Key size={16} /></div>
                  <strong>Enter a code</strong>
                  <span>The other device is showing a 6-digit pairing code.</span>
                </button>
                <button className="method-card" onClick={() => setStep("show")}>
                  <div className="m-ico"><Icons.QR size={16} /></div>
                  <strong>Show a code</strong>
                  <span>Display a one-time code for the other device to enter.</span>
                </button>
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
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
                  <input
                    key={i}
                    ref={(el) => (inputRefs.current[i] = el)}
                    className={c ? "filled" : ""}
                    value={c}
                    inputMode="numeric"
                    maxLength={1}
                    onChange={(e) => handlePinChange(i, e.target.value)}
                    onKeyDown={(e) => handlePinKey(i, e)}
                    autoFocus={i === 0}
                  />
                ))}
              </div>
              <div style={{ textAlign: "center", marginTop: 8 }}>
                <button className="btn-ghost" style={{ height: 28, fontSize: 11 }} onClick={fillDemoPin}>Fill demo code</button>
              </div>
              <div className="perm-list">
                {[
                  { k: "mouse", n: "Mouse control", d: "Move pointer and click on this device" },
                  { k: "keyboard", n: "Keyboard input", d: "Type text and send shortcuts" },
                  { k: "clipboard", n: "Clipboard sync", d: "Copy and paste between devices" },
                  { k: "files", n: "File transfer", d: "Receive files into Downloads" },
                ].map((p) => (
                  <div className="perm-row" key={p.k}>
                    <div className="pmeta">
                      <div className="pname">{p.n}</div>
                      <div className="pdesc">{p.d}</div>
                    </div>
                    <div
                      className={"toggle " + (perms[p.k] ? "on" : "")}
                      onClick={() => setPerms({ ...perms, [p.k]: !perms[p.k] })}
                    />
                  </div>
                ))}
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn-ghost" onClick={() => setStep("method")}>Back</button>
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
            </div>
          </>
        )}

        {step === "show" && (
          <>
            <div className="modal-h">
              <h2>Pairing code</h2>
              <p>Type this on the other device. Code expires in {secondsLeft}s.</p>
            </div>
            <div className="modal-body">
              <div className="pin-display">
                {showCode.split("").map((c, i) => (
                  <div className="pin-cell glow" key={i}>{c}</div>
                ))}
              </div>
              <div className="countdown">
                <span>0:{String(secondsLeft).padStart(2, "0")}</span>
                <div className="countdown-bar">
                  <div style={{ width: `${(secondsLeft / 60) * 100}%` }} />
                </div>
                <span>1:00</span>
              </div>
              <div style={{ textAlign: "center", marginTop: 18, color: "var(--text-dim)", fontSize: 12 }}>
                Waiting for connection…
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn-ghost" onClick={() => setStep("method")}>Back</button>
              <button className="btn-ghost" onClick={onClose}>Cancel</button>
            </div>
          </>
        )}

        {step === "connecting" && (
          <>
            <div className="modal-body connecting">
              <div className="spinner" />
              <div style={{ fontSize: 15, fontWeight: 600 }}>Establishing trust…</div>
              <div className="connecting-msg" style={{ marginTop: 6 }}>Exchanging shared secret over the LAN</div>
            </div>
          </>
        )}

        {step === "success" && (
          <>
            <div className="modal-body connecting">
              <div className="success-mark"><Icons.Check size={32} /></div>
              <div style={{ fontSize: 16, fontWeight: 600 }}>{target ? `Paired with ${target.name}` : "Device paired"}</div>
              <div className="connecting-msg" style={{ marginTop: 8 }}>This device is now in your trusted list.</div>
            </div>
            <div className="modal-foot">
              <button className="btn-primary" onClick={() => { onComplete && onComplete(target, perms); }}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ─────────────── Devices Section ──────────────────────
function DevicesSection() {
  const [paired, setPaired] = useState([
    { id: "this", self: true, name: "Maverick's Mac mini", platform: "macos", model: "Mac mini · M4 · this device" },
    { id: "macbook", name: "MacBook Pro", platform: "macos", model: 'MacBook Pro 14" · M3', status: "connected", direction: "controlling", lastSeen: "now", perms: { clipboard: true, files: false, macros: false } },
    { id: "winpc", name: "Studio PC", platform: "windows", model: "Windows 11 · ThinkStation", status: "paired", lastSeen: "2 min ago", perms: { clipboard: true, files: true, macros: false } },
  ]);
  const [discovered, setDiscovered] = useState([
    { id: "d1", name: "Anya's MacBook Air", platform: "macos", host: "192.168.1.34" },
    { id: "d2", name: "Lounge PC", platform: "windows", host: "192.168.1.51" },
  ]);
  const [pairOpen, setPairOpen] = useState(false);
  const [pairTarget, setPairTarget] = useState(null);

  function startPair(target) {
    setPairTarget(target || null);
    setPairOpen(true);
  }
  function completePair(target /*, perms */) {
    setPairOpen(false);
    if (target) {
      setDiscovered((arr) => arr.filter((d) => d.id !== target.id));
      setPaired((arr) => [
        ...arr,
        {
          id: target.id, name: target.name, platform: target.platform,
          model: target.platform === "macos" ? "Apple · macOS 15" : "Windows 11 · Desktop",
          status: "paired", lastSeen: "just now",
          perms: { clipboard: true, files: false, macros: false },
        },
      ]);
    }
  }
  function unpair(id) {
    setPaired((arr) => arr.filter((d) => d.id !== id));
  }
  function setPerm(id, key, val) {
    setPaired((arr) => arr.map((d) => d.id === id ? { ...d, perms: { ...d.perms, [key]: val } } : d));
  }

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Devices</h1>
          <div className="sub">Devices you trust to share input, clipboard, and files. Trust is local-only and can be revoked at any time.</div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn-ghost"><Icons.Refresh size={14} />Rescan</button>
          <button className="btn-primary" onClick={() => startPair(null)}><Icons.Plus size={14} />Add device</button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-h">
          <h2>Trusted devices</h2>
          <span className="h-sub">{paired.filter((d) => !d.self).length} paired · 1 online</span>
        </div>
        <div className="panel-body">
          {paired.map((d) => <DeviceRow key={d.id} d={d} onUnpair={unpair} onPerm={setPerm} />)}
        </div>
      </div>

      <div className="panel">
        <div className="panel-h">
          <h2>Available on this network</h2>
          <span className="h-sub"><span className="scan-dot" style={{ display: "inline-block", marginRight: 6 }} /> Scanning…</span>
        </div>
        <div className="panel-body">
          {discovered.length === 0 ? (
            <div className="disc-empty">
              <div className="scan"><span className="scan-dot" />Looking for nearby devices…</div>
              <div>Make sure Screen Slickshift is running on the other device and that both are on the same Wi-Fi.</div>
            </div>
          ) : (
            discovered.map((d) => <DiscoveredRow key={d.id} d={d} onPair={startPair} />)
          )}
        </div>
      </div>

      <PairModal
        open={pairOpen}
        target={pairTarget}
        onClose={() => setPairOpen(false)}
        onComplete={completePair}
      />
    </>
  );
}

// ─────────────── Overview (Devices + Edges side-by-side) ───────────
function OverviewSection() {
  const [paired, setPaired] = useState([
    { id: "this", self: true, name: "Maverick's Mac mini", platform: "macos", model: "Mac mini · M4 · this device" },
    { id: "macbook", name: "MacBook Pro", platform: "macos", model: 'MacBook Pro 14" · M3', status: "connected", direction: "controlling", lastSeen: "now", perms: { clipboard: true, files: false, macros: false } },
    { id: "winpc", name: "Studio PC", platform: "windows", model: "Windows 11 · ThinkStation", status: "paired", lastSeen: "2 min ago", perms: { clipboard: true, files: true, macros: false } },
  ]);
  const [discovered, setDiscovered] = useState([
    { id: "d1", name: "Anya's MacBook Air", platform: "macos", host: "192.168.1.34" },
  ]);
  const [pairOpen, setPairOpen] = useState(false);
  const [pairTarget, setPairTarget] = useState(null);

  function startPair(target) { setPairTarget(target || null); setPairOpen(true); }
  function completePair(target) {
    setPairOpen(false);
    if (target) {
      setDiscovered((arr) => arr.filter((d) => d.id !== target.id));
      setPaired((arr) => [...arr, {
        id: target.id, name: target.name, platform: target.platform,
        model: target.platform === "macos" ? "Apple · macOS 15" : "Windows 11 · Desktop",
        status: "paired", lastSeen: "just now",
        perms: { clipboard: true, files: false, macros: false },
      }]);
    }
  }
  function unpair(id) { setPaired((arr) => arr.filter((d) => d.id !== id)); }

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Overview</h1>
          <div className="sub">Trusted devices on your LAN and how their screens are arranged. Open Settings for tracking, clipboard, security, and logs.</div>
        </div>
        <button className="btn-primary" onClick={() => startPair(null)}><Icons.Plus size={14} />Add device</button>
      </div>

      <div className="overview-grid">
        <div className="panel">
          <div className="panel-h">
            <h2>Trusted devices</h2>
            <span className="h-sub">{paired.filter((d) => !d.self).length} paired</span>
          </div>
          <div className="panel-body">
            {paired.map((d) => <DeviceRow key={d.id} d={d} onUnpair={unpair} compact />)}
          </div>
          {discovered.length > 0 && (
            <>
              <div className="panel-h" style={{ borderTop: "1px solid var(--border)" }}>
                <h2 style={{ fontSize: 12, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.08em" }}>On this network</h2>
                <span className="h-sub"><span className="scan-dot" style={{ display: "inline-block", marginRight: 6 }} />Scanning…</span>
              </div>
              <div className="panel-body">
                {discovered.map((d) => <DiscoveredRow key={d.id} d={d} onPair={startPair} />)}
              </div>
            </>
          )}
        </div>

        <OverviewEdges />
      </div>

      <PairModal open={pairOpen} target={pairTarget} onClose={() => setPairOpen(false)} onComplete={completePair} />
    </>
  );
}

function OverviewEdges() {
  const W = 360, H = 220;
  const [tiles, setTiles] = useState([
    { id: "mac", name: "Mac mini", role: "local", x: 36, y: 56, w: 130, h: 80, label: "1920×1080" },
    { id: "pc",  name: "Studio PC", role: "target", x: 178, y: 50, w: 150, h: 92, label: "2560×1440" },
  ]);
  const [drag, setDrag] = useState(null);
  const wrapRef = useRef(null);

  function onDown(id, e) {
    const t = tiles.find((x) => x.id === id);
    setDrag({ id, ox: e.clientX - t.x, oy: e.clientY - t.y });
  }
  function onMove(e) {
    if (!drag) return;
    setTiles((arr) => arr.map((t) => {
      if (t.id !== drag.id) return t;
      let nx = e.clientX - drag.ox, ny = e.clientY - drag.oy;
      arr.forEach((o) => {
        if (o.id === t.id) return;
        if (Math.abs(nx - (o.x + o.w)) < 10) nx = o.x + o.w;
        if (Math.abs((nx + t.w) - o.x) < 10) nx = o.x - t.w;
        if (Math.abs(ny - o.y) < 10) ny = o.y;
      });
      nx = Math.max(0, Math.min(W - t.w, nx));
      ny = Math.max(0, Math.min(H - t.h, ny));
      return { ...t, x: nx, y: ny };
    }));
  }
  function onUp() { setDrag(null); }

  const arrows = [];
  if (tiles.length === 2) {
    const [a, b] = tiles[0].x < tiles[1].x ? [tiles[0], tiles[1]] : [tiles[1], tiles[0]];
    if (Math.abs((a.x + a.w) - b.x) < 4) {
      const cy = Math.max(a.y, b.y) + 8;
      arrows.push({ x: a.x + a.w - 8, y: cy });
    }
  }

  return (
    <div className="panel">
      <div className="panel-h">
        <h2>Screen edges</h2>
        <span className="h-sub">Drag to arrange · cursor hands off where edges touch</span>
      </div>
      <div className="edges-canvas" ref={wrapRef}
           onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}
           style={{ height: H, margin: "0 18px 18px" }}>
        {tiles.map((t) => (
          <div key={t.id}
               className={"screen-tile " + t.role}
               style={{ left: t.x, top: t.y, width: t.w, height: t.h, cursor: drag?.id === t.id ? "grabbing" : "grab" }}
               onMouseDown={(e) => onDown(t.id, e)}>
            <div className="tile-name">{t.name}</div>
            <div className="tile-meta">{t.role === "local" ? "This device" : "Studio PC · paired"}</div>
            <div className="tile-corner">{t.label}</div>
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
