// app.jsx — root + window shell + sidebar + section router

const { useState: useAppState, useEffect: useAppEffect } = React;

const SECTIONS = [
  { id: "overview",   label: "Overview",         Icon: Icons.Devices },
  { id: "devices",    label: "Devices",          Icon: Icons.Devices },
  { id: "settings",   label: "Settings",         Icon: Icons.Mouse },
  { id: "security",   label: "Security",         Icon: Icons.Shield },
  { id: "logs",       label: "Logs",             Icon: Icons.Logs },
];

const ACCENT_PALETTES = [
  { id: "cyan",    name: "Cyan",    a: "#22d3ee", b: "#c084fc" },
  { id: "magenta", name: "Magenta", a: "#ec4899", b: "#818cf8" },
  { id: "mint",    name: "Mint",    a: "#34d399", b: "#60a5fa" },
  { id: "amber",   name: "Amber",   a: "#fbbf24", b: "#f472b6" },
];

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "cyan",
  "section": "overview",
  "density": "comfortable"
}/*EDITMODE-END*/;

function applyAccent(id) {
  const p = ACCENT_PALETTES.find((x) => x.id === id) || ACCENT_PALETTES[0];
  const r = document.documentElement.style;
  r.setProperty("--accent", p.a);
  r.setProperty("--accent-2", p.b);
  // derive soft + glow from the accent
  r.setProperty("--accent-soft", hexA(p.a, 0.18));
  r.setProperty("--accent-glow", hexA(p.a, 0.35));
}
function hexA(hex, a) {
  const h = hex.replace("#", "");
  const n = parseInt(h, 16);
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  return `rgba(${r},${g},${b},${a})`;
}

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [section, setSection] = useAppState(t.section || "overview");
  const [lockout, setLockout] = useAppState(false);

  // sync external section tweak → local state
  useAppEffect(() => { if (t.section && t.section !== section) setSection(t.section); }, [t.section]);
  useAppEffect(() => { applyAccent(t.accent); }, [t.accent]);
  useAppEffect(() => { document.documentElement.dataset.density = t.density || "comfortable"; }, [t.density]);

  // cross-component navigation (e.g. "Configure…" link from Overview)
  useAppEffect(() => {
    function onNav(e) {
      const id = e.detail === "edges" || e.detail === "mouse" || e.detail === "clipboard" ? "settings" : (e.detail || "settings");
      setSection(id);
      setTweak("section", id);
    }
    window.addEventListener("ss-open-settings", onNav);
    return () => window.removeEventListener("ss-open-settings", onNav);
  }, []);

  function go(id) {
    setSection(id);
    setTweak("section", id);
  }

  const accentPalette = ACCENT_PALETTES.find((p) => p.id === t.accent) || ACCENT_PALETTES[0];

  return (
    <div className="stage">
      <div className="win" data-screen-label={`SS · ${SECTIONS.find((s) => s.id === section)?.label}`}>
        {/* titlebar spans full width */}
        <div className="titlebar">
          <div className="tl"><span className="r" /><span className="y" /><span className="g" /></div>
          <div className="win-title">Screen Slickshift</div>
          <div className="win-rhs">
            <span className="dot" />
            <span>Connected · LAN 192.168.1.0/24</span>
          </div>
        </div>

        {/* sidebar */}
        <aside className="side">
          <div className="side-brand">
            <div className="brand-mark" />
            <div>
              <div className="brand-name">Slickshift</div>
              <div className="brand-sub">v0.6 · local</div>
            </div>
          </div>

          <div className="side-section-label">General</div>
          {SECTIONS.slice(0, 3).map((s) => (
            <NavItem key={s.id} s={s} active={section === s.id} onClick={() => go(s.id)} />
          ))}

          <div className="side-section-label">Trust &amp; Activity</div>
          {SECTIONS.slice(3).map((s) => (
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
            <button className={"estop " + (lockout ? "active" : "")}
                    onClick={() => setLockout((v) => !v)}>
              <Icons.Stop size={14} />
              {lockout ? "Lockout active — release" : "Emergency Stop"}
            </button>
          </div>
        </aside>

        {/* main */}
        <main className="main" data-screen-label={`Main · ${section}`}>
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
          {section === "settings"  && <SettingsSection />}
          {section === "mouse"     && <SettingsSection />}
          {section === "edges"     && <SettingsSection />}
          {section === "clipboard" && <SettingsSection />}
          {section === "security"  && <SecuritySection lockout={lockout} onToggleLockout={setLockout} />}
          {section === "logs"      && <LogsSection />}
        </main>
      </div>

      <TweaksPanel title="Tweaks">
        <TweakSection label="Theme" />
        <TweakColor
          label="Accent"
          value={[accentPalette.a, accentPalette.b]}
          options={ACCENT_PALETTES.map((p) => [p.a, p.b])}
          onChange={(v) => {
            const found = ACCENT_PALETTES.find((p) => p.a === v[0]);
            setTweak("accent", found?.id || "cyan");
          }}
        />
        <TweakSection label="Layout" />
        <TweakRadio
          label="Density"
          value={t.density}
          options={[
            { value: "comfortable", label: "Comfortable" },
            { value: "compact", label: "Compact" },
          ]}
          onChange={(v) => setTweak("density", v)}
        />
          <TweakSelect
          label="Active section"
          value={t.section}
          options={SECTIONS.map((s) => ({ value: s.id, label: s.label }))}
          onChange={(v) => setTweak("section", v)}
        />
      </TweaksPanel>
    </div>
  );
}

function NavItem({ s, active, onClick }) {
  return (
    <div className={"nav-item " + (active ? "active" : "")} onClick={onClick}>
      <span className="nav-ico"><s.Icon /></span>
      <span>{s.label}</span>
      {s.badge && <span className="badge">{s.badge}</span>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
