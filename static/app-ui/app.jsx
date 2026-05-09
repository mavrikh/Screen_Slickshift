// app.jsx — root, shell, sidebar, auth gate, section router

const { useState, useEffect } = React;

const SECTIONS = [
  { id: "overview",  label: "Overview",  Icon: Icons.Devices },
  { id: "devices",   label: "Devices",   Icon: Icons.Devices },
  { id: "settings",  label: "Settings",  Icon: Icons.Mouse },
  { id: "security",  label: "Security",  Icon: Icons.Shield },
  { id: "logs",      label: "Logs",      Icon: Icons.Logs },
];

const ACCENT_PALETTES = [
  { id: "cyan",    a: "#22d3ee", b: "#c084fc" },
  { id: "magenta", a: "#ec4899", b: "#818cf8" },
  { id: "mint",    a: "#34d399", b: "#60a5fa" },
  { id: "amber",   a: "#fbbf24", b: "#f472b6" },
];

function loadPrefs() {
  try { return JSON.parse(localStorage.getItem("ss_ui_prefs") || "{}"); } catch { return {}; }
}
function savePrefs(p) {
  try { localStorage.setItem("ss_ui_prefs", JSON.stringify(p)); } catch {}
}

function applyAccent(id) {
  const p = ACCENT_PALETTES.find(x => x.id === id) || ACCENT_PALETTES[0];
  const s = document.documentElement.style;
  s.setProperty("--accent", p.a);
  s.setProperty("--accent-2", p.b);
  const hex = (h, a) => { const n = parseInt(h.replace("#",""), 16); return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`; };
  s.setProperty("--accent-soft", hex(p.a, 0.18));
  s.setProperty("--accent-glow", hex(p.a, 0.35));
}

function App() {
  const prefs = loadPrefs();
  const [section, setSection] = useState(prefs.section || "overview");
  const [accent, setAccent] = useState(prefs.accent || "cyan");
  const [density, setDensity] = useState(prefs.density || "comfortable");
  const [authed, setAuthed] = useState(false);
  const [checking, setChecking] = useState(!!(SS.token() || window.pywebview));
  const [lockout, setLockout] = useState(false);
  const [serverAddr, setServerAddr] = useState("");

  useEffect(() => { applyAccent(accent); }, [accent]);
  useEffect(() => { document.documentElement.dataset.density = density; }, [density]);

  // Verify token on mount; in pywebview auto-inject the owner token
  useEffect(() => {
    async function init() {
      if (window.pywebview && !SS.token()) {
        try {
          const tok = await window.pywebview.api.get_owner_token();
          if (tok) SS.setToken(tok);
        } catch {}
      }
      if (!SS.token()) { setChecking(false); return; }
      try {
        await SS.api("/api/auth/check");
        setAuthed(true);
      } catch {
        setAuthed(false);
      }
      setChecking(false);
    }
    init();
  }, []);

  // Load server state once authenticated
  useEffect(() => {
    if (!authed) return;
    SS.api("/api/status").then(d => {
      setLockout(!!d.disabled);
      const port = window.location.port || "8765";
      setServerAddr(`${window.location.hostname}:${port}`);
    }).catch(() => {});
  }, [authed]);

  function connect(tok) {
    SS.setToken(tok);
    setChecking(true);
    SS.api("/api/auth/check")
      .then(() => { setAuthed(true); setChecking(false); })
      .catch(() => { SS.setToken(""); setAuthed(false); setChecking(false); });
  }

  async function toggleLockout() {
    try {
      const d = await SS.api("/api/lockout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disabled: !lockout }),
      });
      setLockout(!!d.disabled);
    } catch {}
  }

  function go(id) {
    setSection(id);
    savePrefs({ ...loadPrefs(), section: id });
  }

  function changeAccent(id) {
    setAccent(id);
    applyAccent(id);
    savePrefs({ ...loadPrefs(), accent: id });
  }

  function changeDensity(d) {
    setDensity(d);
    savePrefs({ ...loadPrefs(), density: d });
  }

  if (checking) {
    return (
      <div className="stage" style={{ display: "grid", placeItems: "center", color: "var(--text-dim)", fontSize: 14 }}>
        Connecting…
      </div>
    );
  }

  if (!authed) {
    return (
      <div className="stage">
        <AuthGate onConnect={connect} />
      </div>
    );
  }

  return (
    <div className="stage">
      <div className="win">
        <div className="titlebar" style={{ WebkitAppRegion: "drag" }}>
          <div className="win-title">Screen Slickshift</div>
          <div className="win-rhs" style={{ WebkitAppRegion: "no-drag" }}>
            <span className="dot" style={{ background: lockout ? "var(--danger)" : "var(--ok)" }} />
            <span>{lockout ? "Lockout active" : `Connected · ${serverAddr}`}</span>
          </div>
        </div>

        <aside className="side">
          <div className="side-brand">
            <div className="brand-mark" />
            <div>
              <div className="brand-name">Slickshift</div>
              <div className="brand-sub">local</div>
            </div>
          </div>

          <div className="side-section-label">General</div>
          {SECTIONS.slice(0, 3).map(s => (
            <NavItem key={s.id} s={s} active={section === s.id} onClick={() => go(s.id)} />
          ))}

          <div className="side-section-label">Trust &amp; Activity</div>
          {SECTIONS.slice(3).map(s => (
            <NavItem key={s.id} s={s} active={section === s.id} onClick={() => go(s.id)} />
          ))}

          <div className="side-foot">
            <div className="lan-pill">
              <span className="pulse" />
              <div>
                <div style={{ fontWeight: 600 }}>Local LAN only</div>
                <div style={{ fontSize: 10, color: "var(--muted)" }}>No cloud · no telemetry</div>
              </div>
            </div>
            <button
              type="button"
              className={"estop " + (lockout ? "active" : "")}
              onClick={toggleLockout}
            >
              <Icons.Stop size={14} />
              {lockout ? "Lockout active — release" : "Emergency Stop"}
            </button>
          </div>
        </aside>

        <main className="main">
          {lockout && (
            <div className="lockout-banner">
              <Icons.Stop size={16} />
              <div>
                <strong>Emergency lockout active.</strong> All input, clipboard, file, and macro actions are blocked. Trusted devices are marked for review.
              </div>
            </div>
          )}
          {section === "overview"  && <OverviewSection />}
          {section === "devices"   && <DevicesSection />}
          {section === "settings"  && <SettingsSection accent={accent} density={density} onAccent={changeAccent} onDensity={changeDensity} />}
          {section === "security"  && <SecuritySection lockout={lockout} onToggleLockout={toggleLockout} />}
          {section === "logs"      && <LogsSection />}
        </main>
      </div>
    </div>
  );
}

function AuthGate({ onConnect }) {
  const [val, setVal] = useState(SS.token());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (!val.trim()) return;
    setBusy(true);
    setError("");
    SS.setToken(val.trim());
    try {
      await SS.api("/api/auth/check");
      onConnect(val.trim());
    } catch {
      SS.setToken("");
      setError("Token not accepted — check your terminal.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{
      background: "var(--panel)", borderRadius: 14, padding: "32px 28px", width: 360,
      border: "1px solid var(--border)", boxShadow: "var(--shadow-window)"
    }}>
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontWeight: 700, fontSize: 18, marginBottom: 6 }}>Screen Slickshift</div>
        <div style={{ color: "var(--text-dim)", fontSize: 13 }}>
          Enter the token printed in your terminal when the server started.
        </div>
      </div>
      <form onSubmit={submit}>
        <input
          type="password"
          value={val}
          onChange={e => setVal(e.target.value)}
          placeholder="Pairing token"
          autoFocus
          style={{
            width: "100%", padding: "9px 12px", marginBottom: 10,
            background: "var(--panel-2)", border: "1px solid var(--border-strong)",
            borderRadius: 8, color: "var(--text)", fontSize: 14,
            fontFamily: "var(--mono)", outline: "none",
          }}
        />
        {error && <div style={{ color: "var(--danger)", fontSize: 12, marginBottom: 10 }}>{error}</div>}
        <button type="submit" className="btn-primary" style={{ width: "100%" }} disabled={busy}>
          {busy ? "Checking…" : "Connect"}
        </button>
      </form>
    </div>
  );
}

function NavItem({ s, active, onClick }) {
  return (
    <div
      className={"nav-item " + (active ? "active" : "")}
      onClick={onClick}
      tabIndex={0}
      onKeyDown={e => (e.key === "Enter" || e.key === " ") && onClick()}
    >
      <span className="nav-ico"><s.Icon /></span>
      <span>{s.label}</span>
      {s.badge && <span className="badge">{s.badge}</span>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
