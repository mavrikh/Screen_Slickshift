"""Desktop launcher — starts the local server then opens a pywebview window.

Usage:
    python run_app.py

The FastAPI server runs in a daemon thread; closing the window exits the
process and takes the server with it.
"""
from __future__ import annotations

import sys
import threading
import time
import urllib.request
import urllib.error

_HOST = "127.0.0.1"
_PORT = 8765
_URL = f"http://{_HOST}:{_PORT}"


def _start_server() -> None:
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Missing dependencies. Run: pip install -r requirements.txt") from exc

    uvicorn.run(
        "app.main:app",
        host=_HOST,
        port=_PORT,
        access_log=False,
        log_level="warning",
    )


def _wait_for_server(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{_URL}/api/status", timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


def main() -> None:
    if sys.version_info < (3, 9):
        raise SystemExit("Screen Slickshift requires Python 3.9 or newer.")

    try:
        import webview
    except ImportError as exc:
        raise SystemExit(
            "pywebview is not installed. Run: pip install pywebview"
        ) from exc

    threading.Thread(target=_start_server, daemon=True).start()

    if not _wait_for_server():
        raise SystemExit("Server did not start within 15 seconds.")

    webview.create_window(
        "Screen Slickshift",
        _URL,
        width=1220,
        height=800,
        min_size=(900, 620),
        resizable=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
