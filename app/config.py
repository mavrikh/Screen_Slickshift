from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
LOG_DIR = PROJECT_ROOT / "logs"
STATIC_DIR = PROJECT_ROOT / "static"
MACROS_FILE = CONFIG_DIR / "macros.json"
TOKEN_FILE = CONFIG_DIR / "pairing_token.txt"
TRUSTED_DEVICES_FILE = CONFIG_DIR / "trusted_devices.json"
DEVICE_IDENTITY_FILE = CONFIG_DIR / "device_identity.json"


@dataclass(frozen=True)
class Settings:
    app_name: str = "Screen Slickshift"
    upload_dir: Path = UPLOAD_DIR
    max_upload_bytes: int = 50 * 1024 * 1024
    macros_file: Path = MACROS_FILE
    token_file: Path = TOKEN_FILE
    trusted_devices_file: Path = TRUSTED_DEVICES_FILE
    device_identity_file: Path = DEVICE_IDENTITY_FILE
    allowed_origins: tuple[str, ...] = ()


settings = Settings()


def ensure_directories() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def get_or_create_pairing_token() -> str:
    ensure_directories()
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token

    token = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    return token


def load_macros() -> list[dict]:
    if not MACROS_FILE.exists():
        return []

    with MACROS_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    macros = data.get("macros", [])
    if not isinstance(macros, list):
        return []
    return macros
