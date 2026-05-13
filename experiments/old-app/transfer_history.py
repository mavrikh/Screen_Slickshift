from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TransferRecord:
    filename: str
    bytes: int
    path: str
    source: str
    status: str
    detail: str
    created_at: float

    def public_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "bytes": self.bytes,
            "path": self.path,
            "source": self.source,
            "status": self.status,
            "detail": self.detail,
            "created_at": self.created_at,
        }


class TransferHistory:
    def __init__(self, limit: int = 20) -> None:
        self.limit = limit
        self._records: list[TransferRecord] = []

    def add_received(self, result: dict, source: str) -> TransferRecord:
        record = TransferRecord(
            filename=str(result["filename"]),
            bytes=int(result["bytes"]),
            path=str(result["path"]),
            source=source,
            status="received",
            detail="",
            created_at=time.time(),
        )
        self._add(record)
        return record

    def add_rejected(
        self,
        filename: str,
        source: str,
        detail: str,
        bytes: int = 0,
    ) -> TransferRecord:
        record = TransferRecord(
            filename=filename or "unknown",
            bytes=bytes,
            path="",
            source=source,
            status="rejected",
            detail=detail,
            created_at=time.time(),
        )
        self._add(record)
        return record

    def _add(self, record: TransferRecord) -> None:
        self._records.insert(0, record)
        self._records = self._records[: self.limit]

    def list_records(self) -> list[TransferRecord]:
        return list(self._records)

    def clear(self) -> None:
        self._records = []
