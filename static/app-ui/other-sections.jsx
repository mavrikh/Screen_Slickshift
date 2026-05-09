// other-sections.jsx — Settings, Security (wired), Logs

const { useState: useOS, useEffect: useOSEffect, useCallback: useOSCallback } = React;

// ── Shared primitives ────────────────────────────────────────────────────────

function Slider({ value, onChange, min = 0, max = 100, unit = "" }) {
  return (
    <div className="slider-wrap">
      <input className="slider" type="range" min={min} max={max}
             value={value} onChange={e => onChange(Number(e.target.value))} />
      <span className="slider-val">{value}{unit}</span>
    </div>
  );
}

function Toggle({ on, onChange }) {
  return (
    <div className={"toggle " + (on ? "on" : "")} role="switch" aria-checked={on}
         onClick={() => onChange(!on)} tabIndex={0}
         onKeyDown={e => (e.key === "Enter" || e.key === " ") && onChange(!on)} />
  );
}

// ── Settings section ─────────────────────────────────────────────────────────
// Settings are UI-local for now (no backend persistence); they will persist
// in the native shell config once packaging is decided.

function SettingsSection({ accent, density, onAccent, onDensity }) {
  const ACCENT_PALETTES = [
    { id: "cyan",    label: "Cyan",    color: "#22d3ee" },
    { id: "magenta", label: "Magenta", color: "#ec4899" },
    { id: "mint",    label: "Mint",    color: "#34d399" },
    { id: "amber",   label: "Amber",   color: "#fbbf24" },
  ];

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Settings</h1>
          <div className="sub">Tracking, screen edges, clipboard, and appearance.</div>
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

      <div className="settings-group">
        <h3 className="settings-group-h">Appearance</h3>
        <div className="panel">
          <div className="panel-body">
            <div className="set-row">
              <div>
                <div className="set-name">Accent colour</div>
                <div className="set-desc">Changes the highlight colour throughout the app.</div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                {ACCENT_PALETTES.map(p => (
                  <button key={p.id} type="button" title={p.label}
                    onClick={() => onAccent(p.id)}
                    style={{
                      width: 22, height: 22, borderRadius: "50%", border: accent === p.id ? "2px solid var(--text)" : "2px solid transparent",
                      background: p.color, cursor: "pointer", outline: accent === p.id ? "2px solid var(--accent-soft)" : "none",
                    }} />
                ))}
              </div>
            </div>
            <div className="set-row">
              <div>
                <div className="set-name">Density</div>
                <div className="set-desc">Comfortable for large displays; compact for smaller screens.</div>
              </div>
              <div className="seg">
                <button type="button" className={density === "comfortable" ? "on" : ""} onClick={() => onDensity("comfortable")}>Comfortable</button>
                <button type="button" className={density === "compact" ? "on" : ""} onClick={() => onDensity("compact")}>Compact</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function MouseSectionBody() {
  const [tracking, setTracking] = useOS(60);
  const [scroll, setScroll] = useOS(45);
  const [natural, setNatural] = useOS(true);
  const [keyboard, setKeyboard] = useOS(true);
  const [pointerMode, setPointerMode] = useOS("relative");
  return (
    <>
      <div className="panel">
        <div className="panel-h"><h2>Pointer</h2></div>
        <div className="panel-body">
          <div className="set-row"><div><div className="set-name">Tracking speed</div><div className="set-desc">Multiplier applied to remote pointer movement.</div></div><Slider value={tracking} onChange={setTracking} unit="%" /></div>
          <div className="set-row"><div><div className="set-name">Scroll speed</div><div className="set-desc">How fast the wheel forwards across the link.</div></div><Slider value={scroll} onChange={setScroll} unit="%" /></div>
          <div className="set-row"><div><div className="set-name">Natural scrolling</div><div className="set-desc">Match macOS scroll direction on the controlled device.</div></div><Toggle on={natural} onChange={setNatural} /></div>
          <div className="set-row"><div><div className="set-name">Pointer mode</div><div className="set-desc">Relative is recommended when monitor sizes differ.</div></div>
            <div className="seg">
              <button type="button" className={pointerMode === "relative" ? "on" : ""} onClick={() => setPointerMode("relative")}>Relative</button>
              <button type="button" className={pointerMode === "absolute" ? "on" : ""} onClick={() => setPointerMode("absolute")}>Absolute</button>
            </div>
          </div>
        </div>
      </div>
      <div className="panel">
        <div className="panel-h"><h2>Keyboard</h2></div>
        <div className="panel-body">
          <div className="set-row"><div><div className="set-name">Forward keystrokes</div><div className="set-desc">Type on the controlled device while connected.</div></div><Toggle on={keyboard} onChange={setKeyboard} /></div>
          <div className="set-row"><div><div className="set-name">Panic hotkey</div><div className="set-desc">Instantly stop input forwarding from this device.</div></div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <span className="kbd">⌃</span><span className="kbd">⌥</span><span className="kbd">⌘</span><span className="kbd">.</span>
              <button type="button" className="btn-ghost" style={{ marginLeft: 6 }}>Change</button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function EdgesSectionBody() {
  const [returnEdge, setReturnEdge] = useOS(true);
  return (
    <div className="panel">
      <div className="panel-h"><h2>Behaviour</h2><span className="h-sub">Layout lives on the Overview screen</span></div>
      <div className="panel-body">
        <div className="set-row"><div><div className="set-name">Auto-return on opposite edge</div><div className="set-desc">Walking back across the same edge returns control here.</div></div><Toggle on={returnEdge} onChange={setReturnEdge} /></div>
      </div>
    </div>
  );
}

function ClipboardSectionBody() {
  const [share, setShare] = useOS(true);
  const [textOnly, setTextOnly] = useOS(true);
  return (
    <div className="panel">
      <div className="panel-h"><h2>Sharing</h2></div>
      <div className="panel-body">
        <div className="set-row"><div><div className="set-name">Share clipboard with trusted devices</div><div className="set-desc">Granted per-device on the Devices tab.</div></div><Toggle on={share} onChange={setShare} /></div>
        <div className="set-row"><div><div className="set-name">Text-only mode</div><div className="set-desc">Skip images and rich content. Recommended.</div></div><Toggle on={textOnly} onChange={setTextOnly} /></div>
        <div className="set-row"><div><div className="set-name">Manual sync</div><div className="set-desc">Push the current clipboard once.</div></div><button type="button" className="btn-secondary">Sync now</button></div>
      </div>
    </div>
  );
}

// ── Security section ─────────────────────────────────────────────────────────

function SecuritySection({ lockout, onToggleLockout }) {
  const [identity, setIdentity] = useOS(null);
  const [sessions, setSessions] = useOS([]);
  const [loading, setLoading] = useOS(true);
  const [error, setError] = useOS("");

  const load = useOSCallback(async () => {
    setLoading(true);
    try {
      const [devRes, sessRes] = await Promise.all([
        SS.api("/api/device"),
        SS.api("/api/pairing-sessions"),
      ]);
      setIdentity(devRes.device || null);
      setSessions(sessRes.sessions || []);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useOSEffect(() => { load(); }, [load]);

  async function revokeSession(session_id) {
    try {
      await SS.api(`/api/pairing-sessions/${session_id}`, { method: "DELETE" });
      await load();
    } catch (e) { setError(e.message); }
  }

  async function revokeAll() {
    try {
      await SS.api("/api/pairing-sessions", { method: "DELETE" });
      await load();
    } catch (e) { setError(e.message); }
  }

  function formatExpiry(ts) {
    if (!ts) return "";
    const diff = Math.floor(ts - Date.now() / 1000);
    if (diff <= 0) return "expired";
    if (diff < 3600) return `${Math.floor(diff / 60)}m left`;
    return `${Math.floor(diff / 3600)}h left`;
  }

  return (
    <>
      <div className="main-head">
        <div>
          <h1>Security</h1>
          <div className="sub">Trust state and active sessions. Local-LAN only — nothing leaves your network.</div>
        </div>
        <button type="button" className="btn-danger" onClick={revokeAll}>Revoke all sessions</button>
      </div>

      {error && <div style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>{error}</div>}

      <div className="panel">
        <div className="panel-h"><h2>This device</h2></div>
        <div className="panel-body">
          <div className="set-row">
            <div>
              <div className="set-name">Identity</div>
              <div className="set-desc">Random local identity, regenerated only on request.</div>
            </div>
            {identity ? (
              <span className="kbd" style={{ fontFamily: "var(--mono)", fontSize: 11 }}>
                {identity.device_id?.slice(0, 16) || "—"}
              </span>
            ) : <span className="kbd">—</span>}
          </div>
          <div className="set-row">
            <div>
              <div className="set-name">Device name</div>
              <div className="set-desc">Shown to other devices during pairing.</div>
            </div>
            <span style={{ color: "var(--text-dim)", fontSize: 13 }}>{identity?.name || "—"}</span>
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
        <div className="panel-h">
          <h2>Active sessions</h2>
          <span className="h-sub">{sessions.length} session{sessions.length !== 1 ? "s" : ""}</span>
        </div>
        <div className="panel-body">
          {loading && <div style={{ color: "var(--muted)", fontSize: 13, padding: "8px 0" }}>Loading…</div>}
          {!loading && sessions.length === 0 && (
            <div style={{ color: "var(--muted)", fontSize: 13, padding: "8px 0" }}>No active sessions.</div>
          )}
          {sessions.map(s => (
            <div className="set-row" key={s.session_id}>
              <div>
                <div className="set-name">
                  {s.guest ? "Guest session" : "Trusted session"}
                  {" "}
                  <span style={{ color: "var(--muted)", fontWeight: 400, fontSize: 11 }}>{s.device_id?.slice(0, 12)}</span>
                </div>
                <div className="set-desc">
                  {Object.entries(s.permissions || {}).filter(([,v]) => v).map(([k]) => k).join(", ") || "no permissions"} · {formatExpiry(s.expires_at)}
                </div>
              </div>
              <button type="button" className="btn-ghost" onClick={() => revokeSession(s.session_id)}>Revoke</button>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

// ── Logs section ─────────────────────────────────────────────────────────────
// Live log streaming is not yet implemented; the activity log from the
// existing browser UI (/) provides the current working equivalent.

function LogsSection() {
  return (
    <>
      <div className="main-head">
        <div>
          <h1>Logs</h1>
          <div className="sub">Connection and control events from this server run. Sensitive content is never recorded.</div>
        </div>
      </div>
      <div className="panel">
        <div className="panel-h"><h2>Activity log</h2></div>
        <div className="panel-body">
          <div style={{ color: "var(--text-dim)", fontSize: 13, lineHeight: 1.7, padding: "4px 0" }}>
            Live log streaming is not yet available in this UI. The activity log is shown on the{" "}
            <a href="/" target="_blank" rel="noopener"
               style={{ color: "var(--accent)", textDecoration: "none" }}>
              main browser page
            </a>.
          </div>
        </div>
      </div>
    </>
  );
}

window.SettingsSection = SettingsSection;
window.SecuritySection = SecuritySection;
window.LogsSection = LogsSection;
