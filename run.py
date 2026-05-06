from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Screen Slickshift FastAPI server.")
    parser.add_argument("--host", default="127.0.0.1", help="Address to listen on. Use 0.0.0.0 for LAN access.")
    parser.add_argument("--port", default=8765, type=int, help="Port to listen on.")
    parser.add_argument("--reload", action="store_true", help="Reload on code changes during development.")
    args = parser.parse_args()

    if sys.version_info < (3, 9):
        raise SystemExit("Screen Slickshift requires Python 3.9 or newer.")

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Missing dependencies. Run: python -m pip install -r requirements.txt") from exc

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload, access_log=False)


if __name__ == "__main__":
    main()
