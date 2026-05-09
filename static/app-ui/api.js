// Shared API helper for the new UI.
// Uses the same localStorage token key (wdcToken) as the existing browser UI
// so both UIs share the same saved token.

window.SS = window.SS || {};

SS.token = () => localStorage.getItem("wdcToken") || "";
SS.setToken = (t) => { if (t) localStorage.setItem("wdcToken", t); else localStorage.removeItem("wdcToken"); };

SS.api = async function(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "X-Pairing-Token": SS.token(),
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return res.json();
};
