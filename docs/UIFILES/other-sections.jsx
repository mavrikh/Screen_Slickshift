// other-sections.jsx — Mouse, Edges, Clipboard, Security, Logs

const { useState: useStateOS } = React;

function Slider({ value, onChange, min = 0, max = 100, unit = "" }) {
  return (
    <div className="slider-wrap">
      <input className="slider" type="range" min={min} max={max}
        value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <span className="slider-val">{value}{unit}</span>
    </div>
  );
}
function Toggle({ on, onChange }) {
  return <div className={"toggle " + (on ? "on" : "")} onClick={() => onChange(!on)} />;
}

function MouseSection() {
  const [tracking, setTracking] = useStateOS(60);
  const [scroll, setScroll] = useStateOS(45);
  const [natural, setNatural] = useStateOS(true);
  const [keyboard, setKeyboard] = useStateOS(true);
  const [pointerMode, setPointerMode] = useStateOS("relative");

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Mouse &amp; Keyboard</h1>
          <div className="sub">Tune how input is forwarded to other devices. Settings apply to every trusted connection.</div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-h"><h2>Pointer</h2></div>
        <div className="panel-body">
          <div className="set-row">
            <div>
              <div className="set-name">Tracking speed</div>
              <div className="set-desc">Multiplier applied to remote pointer movement.</div>
            </div>
            <Slider value={tracking} onChange={setTracking} unit="%" />
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Scroll speed</div>
              <div className="set-desc">How fast the wheel forwards across the link.</div>
            </div>
            <Slider value={scroll} onChange={setScroll} unit="%" />
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Natural scrolling</div>
              <div className="set-desc">Match macOS scroll direction on the controlled device.</div>
            </div>
            <Toggle on={natural} onChange={setNatural} />
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Pointer mode</div>
              <div className="set-desc">Relative is recommended when monitor sizes differ.</div>
            </div>
            <div className="seg">
              <button className={pointerMode === "relative" ? "on" : ""} onClick={() => setPointerMode("relative")}>Relative</button>
              <button className={pointerMode === "absolute" ? "on" : ""} onClick={() => setPointerMode("absolute")}>Absolute</button>
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-h"><h2>Keyboard</h2></div>
        <div className="panel-body">
          <div className="set-row">
            <div>
              <div className="set-name">Forward keystrokes</div>
              <div className="set-desc">Type on the controlled device while connected.</div>
            </div>
            <Toggle on={keyboard} onChange={setKeyboard} />
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Panic hotkey</div>
              <div className="set-desc">Instantly stop input forwarding from this device.</div>
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span className="kbd">⌃</span><span className="kbd">⌥</span><span className="kbd">⌘</span><span className="kbd">.</span>
              <button className="btn-ghost" style={{ marginLeft: 6 }}>Change</button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ─────────────── Screen Edges (interesting move) ───────
function EdgesSection() {
  const W = 760, H = 280;
  const [tiles, setTiles] = useStateOS([
    { id: "mac", name: "Mac mini", role: "local", x: 80, y: 80, w: 220, h: 130, label: "1920×1080" },
    { id: "pc",  name: "Studio PC", role: "target", x: 320, y: 80, w: 280, h: 158, label: "2560×1440" },
  ]);
  const [drag, setDrag] = useStateOS(null);
  const [edgeDelay, setEdgeDelay] = useStateOS(180);
  const [returnEdge, setReturnEdge] = useStateOS(true);

  function onDown(id, e) {
    const tile = tiles.find((t) => t.id === id);
    setDrag({ id, ox: e.clientX - tile.x, oy: e.clientY - tile.y });
  }
  function onMove(e) {
    if (!drag) return;
    setTiles((arr) => arr.map((t) => {
      if (t.id !== drag.id) return t;
      let nx = e.clientX - drag.ox, ny = e.clientY - drag.oy;
      // soft snap to neighbor
      arr.forEach((o) => {
        if (o.id === t.id) return;
        if (Math.abs(nx - (o.x + o.w)) < 12) nx = o.x + o.w;
        if (Math.abs((nx + t.w) - o.x) < 12) nx = o.x - t.w;
        if (Math.abs(ny - o.y) < 12) ny = o.y;
      });
      nx = Math.max(0, Math.min(W - t.w, nx));
      ny = Math.max(0, Math.min(H - t.h, ny));
      return { ...t, x: nx, y: ny };
    }));
  }
  function onUp() { setDrag(null); }

  // edges between adjacent tiles
  const arrows = [];
  if (tiles.length === 2) {
    const [a, b] = tiles[0].x < tiles[1].x ? [tiles[0], tiles[1]] : [tiles[1], tiles[0]];
    if (Math.abs((a.x + a.w) - b.x) < 4) {
      const cy = Math.max(a.y, b.y) + Math.min(a.y + a.h, b.y + b.h - Math.max(a.y, b.y)) / 2;
      arrows.push({ x: a.x + a.w - 8, y: cy - 8, dir: "right" });
    }
  }

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Screen Edges</h1>
          <div className="sub">Drag screens to arrange them. The cursor will hand off where edges touch.</div>
        </div>
        <button className="btn-ghost"><Icons.Refresh size={14} />Auto-arrange</button>
      </div>

      <div className="panel">
        <div className="panel-h"><h2>Layout</h2><span className="h-sub">2 displays · drag to rearrange</span></div>
        <div className="edges-canvas"
             onMouseMove={onMove} onMouseUp={onUp} onMouseLeave={onUp}
             style={{ height: H }}>
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

      <div className="panel">
        <div className="panel-h"><h2>Behavior</h2></div>
        <div className="panel-body">
          <div className="set-row">
            <div>
              <div className="set-name">Edge dwell delay</div>
              <div className="set-desc">How long the pointer must rest at the edge before handoff.</div>
            </div>
            <Slider value={edgeDelay} min={50} max={600} onChange={setEdgeDelay} unit="ms" />
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Auto-return on opposite edge</div>
              <div className="set-desc">Walking back across the same edge returns control here.</div>
            </div>
            <Toggle on={returnEdge} onChange={setReturnEdge} />
          </div>
        </div>
      </div>
    </>
  );
}

// ─────────────── Clipboard ─────────────────────────────
function ClipboardSection() {
  const [share, setShare] = useStateOS(true);
  const [textOnly, setTextOnly] = useStateOS(true);
  return (
    <>
      <div className="main-head">
        <div>
          <h1>Clipboard</h1>
          <div className="sub">Sync copy and paste between trusted devices. Contents are never logged.</div>
        </div>
      </div>
      <div className="panel">
        <div className="panel-h"><h2>Sharing</h2></div>
        <div className="panel-body">
          <div className="set-row">
            <div>
              <div className="set-name">Share clipboard with trusted devices</div>
              <div className="set-desc">Granted per-device under Security.</div>
            </div>
            <Toggle on={share} onChange={setShare} />
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Text-only mode</div>
              <div className="set-desc">Skip images and rich content. Recommended.</div>
            </div>
            <Toggle on={textOnly} onChange={setTextOnly} />
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Manual sync</div>
              <div className="set-desc">Push the current clipboard once.</div>
            </div>
            <button className="btn-secondary">Sync now</button>
          </div>
        </div>
      </div>
    </>
  );
}

// ─────────────── Security ──────────────────────────────
function SecuritySection({ lockout, onToggleLockout }) {
  return (
    <>
      <div className="main-head">
        <div>
          <h1>Security</h1>
          <div className="sub">Trust state and per-device permissions. Local-LAN only — nothing leaves your network.</div>
        </div>
        <button className="btn-danger">Revoke all sessions</button>
      </div>

      <div className="panel">
        <div className="panel-h"><h2>This device</h2></div>
        <div className="panel-body">
          <div className="set-row">
            <div>
              <div className="set-name">Identity</div>
              <div className="set-desc">Random local identity, regenerated only on request.</div>
            </div>
            <span className="kbd">mac-mini-9F2A · v1</span>
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Network</div>
              <div className="set-desc">Visible only to devices you accept on this LAN.</div>
            </div>
            <span className="tag" style={{ color: "var(--ok)", borderColor: "rgba(74,222,128,0.3)" }}>Local LAN only</span>
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Emergency lockout</div>
              <div className="set-desc">Blocks all input, clipboard, file, and macro actions.</div>
            </div>
            <Toggle on={lockout} onChange={onToggleLockout} />
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="panel-h"><h2>Active sessions</h2><span className="h-sub">2 sessions</span></div>
        <div className="panel-body">
          {[
            { name: "MacBook Pro", id: "macbook", since: "11:42 · 38m" },
            { name: "Studio PC", id: "winpc", since: "11:09 · 1h 11m" },
          ].map((s) => (
            <div className="set-row" key={s.id}>
              <div>
                <div className="set-name">{s.name}</div>
                <div className="set-desc">Session opened {s.since}. Per-device permissions live on the Devices tab.</div>
              </div>
              <button className="btn-ghost">Revoke</button>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

// ─────────────── Logs ──────────────────────────────────
function LogsSection() {
  const rows = [
    { t: "11:42:08", lvl: "ok", msg: <><strong>MacBook Pro</strong> began controlling this device.</>, tag: "control" },
    { t: "11:41:55", lvl: "info", msg: <><strong>MacBook Pro</strong> session opened with mouse, keyboard, clipboard.</>, tag: "session" },
    { t: "11:39:12", lvl: "info", msg: <>Discovery advertising started.</>, tag: "discovery" },
    { t: "11:38:50", lvl: "ok", msg: <><strong>Studio PC</strong> reconnected as trusted.</>, tag: "trust" },
    { t: "11:14:02", lvl: "warn", msg: <>Pairing code rejected for unknown device <code>192.168.1.77</code>.</>, tag: "pairing" },
    { t: "10:58:11", lvl: "info", msg: <>Server started on <code>0.0.0.0:8765</code>.</>, tag: "boot" },
    { t: "yesterday", lvl: "err", msg: <>Emergency lockout triggered. All sessions revoked.</>, tag: "lockout" },
  ];
  return (
    <>
      <div className="main-head">
        <div>
          <h1>Logs</h1>
          <div className="sub">Connection and control events from this server run. Sensitive content is never recorded.</div>
        </div>
        <button className="btn-ghost">Export…</button>
      </div>

      <div className="panel">
        <div className="panel-h"><h2>Recent activity</h2><span className="h-sub">{rows.length} events</span></div>
        <div className="panel-body">
          {rows.map((r, i) => (
            <div key={i} className="log-row">
              <span className="log-time">{r.t}</span>
              <span className={"log-dot " + r.lvl} />
              <span className="log-msg">{r.msg}</span>
              <span className="log-tag">{r.tag}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

function SettingsSection() {
  return (
    <>
      <div className="main-head">
        <div>
          <h1>Settings</h1>
          <div className="sub">Tracking, screen edges, and clipboard. Per-device permissions live on the Devices tab.</div>
        </div>
      </div>
      <div className="settings-group">
        <h3 className="settings-group-h">Mouse &amp; Keyboard</h3>
        <MouseSectionBody />
      </div>
      <div className="settings-group">
        <h3 className="settings-group-h">Screen Edges</h3>
        <EdgesSectionBody />
      </div>
      <div className="settings-group">
        <h3 className="settings-group-h">Clipboard</h3>
        <ClipboardSectionBody />
      </div>
    </>
  );
}

function MouseSectionBody() {
  const [tracking, setTracking] = useStateOS(60);
  const [scroll, setScroll] = useStateOS(45);
  const [natural, setNatural] = useStateOS(true);
  const [keyboard, setKeyboard] = useStateOS(true);
  const [pointerMode, setPointerMode] = useStateOS("relative");
  return (
    <>
      <div className="panel">
        <div className="panel-h"><h2>Pointer</h2></div>
        <div className="panel-body">
          <div className="set-row"><div><div className="set-name">Tracking speed</div><div className="set-desc">Multiplier applied to remote pointer movement.</div></div><Slider value={tracking} onChange={setTracking} unit="%" /></div>
          <div className="set-row"><div><div className="set-name">Scroll speed</div><div className="set-desc">How fast the wheel forwards across the link.</div></div><Slider value={scroll} onChange={setScroll} unit="%" /></div>
          <div className="set-row"><div><div className="set-name">Natural scrolling</div><div className="set-desc">Match macOS scroll direction on the controlled device.</div></div><Toggle on={natural} onChange={setNatural} /></div>
          <div className="set-row"><div><div className="set-name">Pointer mode</div><div className="set-desc">Relative is recommended when monitor sizes differ.</div></div>
            <div className="seg"><button className={pointerMode === "relative" ? "on" : ""} onClick={() => setPointerMode("relative")}>Relative</button><button className={pointerMode === "absolute" ? "on" : ""} onClick={() => setPointerMode("absolute")}>Absolute</button></div>
          </div>
        </div>
      </div>
      <div className="panel">
        <div className="panel-h"><h2>Keyboard</h2></div>
        <div className="panel-body">
          <div className="set-row"><div><div className="set-name">Forward keystrokes</div><div className="set-desc">Type on the controlled device while connected.</div></div><Toggle on={keyboard} onChange={setKeyboard} /></div>
          <div className="set-row"><div><div className="set-name">Panic hotkey</div><div className="set-desc">Instantly stop input forwarding from this device.</div></div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}><span className="kbd">⌃</span><span className="kbd">⌥</span><span className="kbd">⌘</span><span className="kbd">.</span><button className="btn-ghost" style={{ marginLeft: 6 }}>Change</button></div>
          </div>
        </div>
      </div>
    </>
  );
}

function EdgesSectionBody() {
  const [returnEdge, setReturnEdge] = useStateOS(true);
  return (
    <div className="panel">
      <div className="panel-h"><h2>Behavior</h2><span className="h-sub">Layout lives on the Overview screen</span></div>
      <div className="panel-body">
        <div className="set-row"><div><div className="set-name">Auto-return on opposite edge</div><div className="set-desc">Walking back across the same edge returns control here.</div></div><Toggle on={returnEdge} onChange={setReturnEdge} /></div>
      </div>
    </div>
  );
}

function ClipboardSectionBody() {
  const [share, setShare] = useStateOS(true);
  const [textOnly, setTextOnly] = useStateOS(true);
  return (
    <div className="panel">
      <div className="panel-h"><h2>Sharing</h2></div>
      <div className="panel-body">
        <div className="set-row"><div><div className="set-name">Share clipboard with trusted devices</div><div className="set-desc">Granted per-device on the Devices tab.</div></div><Toggle on={share} onChange={setShare} /></div>
        <div className="set-row"><div><div className="set-name">Text-only mode</div><div className="set-desc">Skip images and rich content. Recommended.</div></div><Toggle on={textOnly} onChange={setTextOnly} /></div>
        <div className="set-row"><div><div className="set-name">Manual sync</div><div className="set-desc">Push the current clipboard once.</div></div><button className="btn-secondary">Sync now</button></div>
      </div>
    </div>
  );
}

window.SettingsSection = SettingsSection;
window.MouseSection = MouseSection;
window.EdgesSection = EdgesSection;
window.ClipboardSection = ClipboardSection;
window.SecuritySection = SecuritySection;
window.LogsSection = LogsSection;
