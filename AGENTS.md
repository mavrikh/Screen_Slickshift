# Screen Slickshift Project Notes

Screen Slickshift is a local-only LAN input sharing project.

Current prototype:

- Windows FastAPI server.
- Steam Deck-friendly browser UI.
- Token-protected local control.
- Mouse/touchpad, clicks, scroll, text, clipboard, macros, uploads.
- Experimental protocol layer.
- Experimental macOS receiver and sender test tools.

Long-term goal:

- Native cross-platform agents for Windows, macOS, and Linux/SteamOS.
- Mouse and keyboard sharing between paired devices on the local network.
- Optional clipboard sharing.
- File transfer only if explicitly enabled.

Hard constraints:

- No cloud services.
- No required accounts.
- No telemetry.
- No arbitrary shell commands from remote clients.
- No screen recording, screenshots, remote desktop previews, OCR, or video capture unless the user explicitly asks for that feature later.
- Keep security and emergency stop behavior visible.
- Prefer small understandable modules over large abstractions.

Current project docs:

- `docs/ROADMAP.md`
- `docs/PROTOCOL.md`
- `docs/SECURITY.md`
- `docs/DESIGN.md`
- `docs/AGENTS.md`

When making code changes:

- Preserve the working Windows browser-control MVP.
- Keep protocol messages compatible unless intentionally migrating.
- Avoid admin/root assumptions unless the user explicitly accepts them.
- Do not log tokens, clipboard contents, full keystrokes, or file contents.
- Use `apply_patch` for manual edits.
