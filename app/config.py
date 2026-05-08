from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
LOG_DIR = PROJECT_ROOT / "logs"
STATIC_DIR = PROJECT_ROOT / "static"
MACROS_FILE = CONFIG_DIR / "macros.json"
TOKEN_FILE = CONFIG_DIR / "pairing_token.txt"
TRUSTED_DEVICES_FILE = CONFIG_DIR / "trusted_devices.json"
DEVICE_IDENTITY_FILE = CONFIG_DIR / "device_identity.json"
RECEIVE_DIR_FILE = CONFIG_DIR / "receive_dir.txt"


@dataclass(frozen=True)
class Settings:
    app_name: str = "Screen Slickshift"
    max_upload_bytes: int = 50 * 1024 * 1024
    macros_file: Path = MACROS_FILE
    token_file: Path = TOKEN_FILE
    trusted_devices_file: Path = TRUSTED_DEVICES_FILE
    device_identity_file: Path = DEVICE_IDENTITY_FILE
    receive_dir_file: Path = RECEIVE_DIR_FILE
    allowed_origins: tuple[str, ...] = ()
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "not-needed"


settings = Settings()


def ensure_directories() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def default_receive_dir() -> Path:
    return Path.home() / "Downloads"


def get_receive_dir() -> Path:
    ensure_directories()
    if RECEIVE_DIR_FILE.exists():
        configured = RECEIVE_DIR_FILE.read_text(encoding="utf-8").strip()
        if configured:
            return Path(configured).expanduser()
    return default_receive_dir()


def set_receive_dir(path: str) -> Path:
    receive_dir = Path(path).expanduser()
    if not receive_dir.is_absolute():
        raise ValueError("Receive folder must be an absolute path.")
    receive_dir.mkdir(parents=True, exist_ok=True)
    ensure_directories()
    RECEIVE_DIR_FILE.write_text(str(receive_dir) + "\n", encoding="utf-8")
    return receive_dir


def get_or_create_pairing_token() -> str:
    ensure_directories()
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token and len(token) == 6 and token.isdigit():
            return token

    token = str(secrets.randbelow(1000000)).zfill(6)
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
