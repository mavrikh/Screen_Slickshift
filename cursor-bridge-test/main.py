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

from PyQt6.QtWidgets import QApplication

import config
from ui.main_window import MainWindow


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.DEBUG),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    _configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("cursor-bridge-test starting -- Step 4: one-way delta mirroring")

    app = QApplication(sys.argv)
    app.setApplicationName("Cursor Bridge Test")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
