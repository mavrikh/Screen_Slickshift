"""
cursor-bridge-test -- entry point.

Run with:
    python main.py

Requires PyQt6 and pyautogui. Install via:
    python -m venv .venv
    source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
    pip install -r requirements.txt

Step 1 of 8 from mouse-integration-restart-brainstorm.md.
This entry point sets up logging, boots the QApplication, and shows the
main window. No transport, capture, or injection is wired at this step.
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
    logger.info("cursor-bridge-test starting -- Step 1 skeleton")

    app = QApplication(sys.argv)
    app.setApplicationName("Cursor Bridge Test")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
