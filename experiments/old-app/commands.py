from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass

from app.config import load_macros
from app.platform_support import platform_allows
from app.state import lockout_state


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Macro:
    id: str
    label: str
    description: str
    command: list[str]
    platforms: tuple[str, ...]


def list_macros() -> list[Macro]:
    macros: list[Macro] = []
    for item in load_macros():
        macro_id = item.get("id")
        label = item.get("label", macro_id)
        command = item.get("command")
        description = item.get("description", "")
        platforms = item.get("platforms")

        if not isinstance(macro_id, str) or not isinstance(command, list):
            logger.warning("Skipping invalid macro entry: %s", item)
            continue
        if not all(isinstance(part, str) and part for part in command):
            logger.warning("Skipping macro with invalid command: %s", macro_id)
            continue
        if not platform_allows(platforms):
            continue

        macros.append(Macro(macro_id, str(label), str(description), command, _macro_platforms(platforms)))
    return macros


def public_macro_list() -> list[dict]:
    return [
        {"id": macro.id, "label": macro.label, "description": macro.description}
        for macro in list_macros()
    ]


def run_macro(macro_id: str) -> dict:
    if lockout_state.is_disabled():
        raise RuntimeError("Macro execution is disabled by emergency lockout.")

    macro = next((item for item in list_macros() if item.id == macro_id), None)
    if macro is None:
        raise KeyError(f"Unknown macro: {macro_id}")

    logger.info("Running approved macro %s: %s", macro.id, macro.command)
    try:
        process = subprocess.Popen(
            macro.command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=_windows_creation_flags(),
        )
    except FileNotFoundError as exc:
        logger.exception("Macro executable was not found: %s", macro.command[0])
        raise RuntimeError(f"Macro executable was not found: {macro.command[0]}") from exc
    except OSError as exc:
        logger.exception("Could not start macro %s.", macro.id)
        raise RuntimeError(f"Could not start macro {macro.label}: {exc}") from exc

    return {"id": macro.id, "pid": process.pid, "label": macro.label}


def _windows_creation_flags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS


def _macro_platforms(platforms: object) -> tuple[str, ...]:
    if isinstance(platforms, str):
        return (platforms,)
    if isinstance(platforms, list):
        return tuple(str(item) for item in platforms)
    return ("all",)
