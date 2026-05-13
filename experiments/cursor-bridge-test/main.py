"""
cursor-bridge-test -- entry point.

Run with:
    python main.py

Requires PyQt6 and pyautogui. Install via:
    python -m venv .venv
    source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
    pip install -r requirements.txt

Step 3 of 8 from mouse-integration-restart-brainstorm.md.
Adds TCP transport, hello handshake, heartbeat, and connection UI.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback


# ---------------------------------------------------------------------------
# Global exception hooks -- installed BEFORE any Qt imports.
#
# PyQt6 calls qFatal() (which calls abort()) on unhandled exceptions in Qt
# slots and timer callbacks, discarding the Python traceback. These hooks
# intercept those exceptions first and print + log the full traceback so the
# actual error is visible in the terminal. The hooks do NOT terminate the
# process, allowing Master to read the error and continue.
# ---------------------------------------------------------------------------

def _install_exception_hooks() -> None:
    """
    Install sys.excepthook and threading.excepthook for full Python traceback
    visibility on unhandled exceptions in Qt slots and background threads.

    Must be called before QApplication is created so Qt's internal error
    handler does not suppress the traceback first.
    """

    def _format_exc(exc_type: type, exc_value: BaseException, exc_tb) -> str:
        return "".join(traceback.format_exception(exc_type, exc_value, exc_tb))

    def _main_excepthook(exc_type: type, exc_value: BaseException, exc_tb) -> None:
        """
        Replaces sys.excepthook. Fires for unhandled exceptions on the main
        thread, including exceptions that bubble up through Qt slot dispatch.
        """
        msg = _format_exc(exc_type, exc_value, exc_tb)
        sys.stderr.write(f"=== Unhandled exception (main thread) ===\n{msg}\n")
        sys.stderr.flush()
        # Logger may not be configured yet at import time; guard accordingly.
        _hook_logger = logging.getLogger("excepthook")
        _hook_logger.error("Unhandled exception (main thread):\n%s", msg)

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        """
        Replaces threading.excepthook. Fires for unhandled exceptions in any
        background thread (capture, transport, sender, heartbeat).
        """
        if args.exc_type is SystemExit:
            # Let SystemExit propagate normally.
            return
        msg = _format_exc(args.exc_type, args.exc_value, args.exc_traceback)
        thread_name = args.thread.name if args.thread is not None else "<unknown thread>"
        sys.stderr.write(
            f"=== Unhandled exception in thread '{thread_name}' ===\n{msg}\n"
        )
        sys.stderr.flush()
        _hook_logger = logging.getLogger("excepthook")
        _hook_logger.error(
            "Unhandled exception in thread '%s':\n%s", thread_name, msg
        )

    sys.excepthook = _main_excepthook
    threading.excepthook = _thread_excepthook


# Install hooks immediately -- before any Qt import triggers initialization.
_install_exception_hooks()


# ---------------------------------------------------------------------------
# Qt and application imports (after hook installation).
# ---------------------------------------------------------------------------

from PyQt6.QtWidgets import QApplication  # noqa: E402

import config  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.DEBUG),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    _configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("cursor-bridge-test starting -- Step 6: two-way screen-edge handoff")

    app = QApplication(sys.argv)
    app.setApplicationName("Cursor Bridge Test")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
