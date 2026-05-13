from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.config import get_receive_dir, settings
from app.state import lockout_state


SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._ -]+")


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "upload.bin").name
    name = SAFE_NAME_PATTERN.sub("_", name).strip(" .")
    return name or "upload.bin"


def unique_upload_path(filename: str, upload_dir: Path) -> Path:
    safe_name = sanitize_filename(filename)
    target = upload_dir / safe_name
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    counter = 1
    while True:
        candidate = upload_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


async def save_upload(file: UploadFile, max_bytes: Optional[int] = None) -> dict:
    if lockout_state.is_disabled():
        raise RuntimeError("File upload is disabled by emergency lockout.")

    receive_dir = get_receive_dir()
    receive_dir.mkdir(parents=True, exist_ok=True)
    target = unique_upload_path(file.filename or "upload.bin", receive_dir)
    byte_limit = settings.max_upload_bytes if max_bytes is None else max_bytes

    total = 0
    try:
        with target.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > byte_limit:
                    raise RuntimeError(f"File upload exceeds the {byte_limit} byte limit.")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise

    return {"filename": target.name, "bytes": total, "path": str(target)}
