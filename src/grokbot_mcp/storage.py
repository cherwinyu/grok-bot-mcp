"""Small locked JSON ledgers. Records are per workspace and are not shared."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from grokbot_mcp.security.paths import is_reparse_point, is_uuid
from grokbot_mcp.strict_json import check_size


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def read_json(path: Path) -> tuple[dict[str, object], str]:
    if any(is_reparse_point(part) for part in (path, *path.parents)):
        raise ValueError("reparse")
    if path.stat().st_nlink != 1:
        raise ValueError("linked_path_rejected")
    from grokbot_mcp.strict_json import decode, MAX_BYTES
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    check_size(raw)
    parsed = decode(raw)
    return parsed, sha256_bytes(raw)


def write_json_new(path: Path, document: dict[str, object]) -> None:
    if path.exists():
        raise ValueError("duplicate")
    path.parent.mkdir(parents=True, exist_ok=True)
    if is_reparse_point(path.parent):
        raise ValueError("reparse")
    raw = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
    check_size(raw)
    temporary = path.parent / f".{path.stem}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class JsonLedger:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.lock_file = directory / ".submit.lock"

    @contextmanager
    def locked(self) -> Iterator[None]:
        import msvcrt  # Windows only; importing the diagnostics CLI is side-effect free.

        self.directory.mkdir(parents=True, exist_ok=True)
        if is_reparse_point(self.directory):
            raise ValueError("reparse")
        handle = open(self.lock_file, "a+b")
        try:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()

    def path_for(self, record_id: str) -> Path:
        if not is_uuid(record_id):
            raise ValueError("invalid_id")
        return self.directory / f"{record_id}.json"

    def list_records(self) -> list[dict[str, object]]:
        if self.directory.name in {"tasks_v2", "executions"}:
            from grokbot_mcp.ledger_archive import incomplete
            if incomplete(self.directory.parent.parent):
                raise ValueError("archive_incomplete")
        records: list[dict[str, object]] = []
        if not self.directory.exists():
            return records
        for path in self.directory.iterdir():
            if not path.is_file() or path.suffix != ".json" or path.name.startswith("."):
                continue
            if is_reparse_point(path):
                raise ValueError("reparse")
            document, _digest = read_json(path)
            records.append(document)
        return records

    def read(self, record_id: str) -> dict[str, object]:
        self._reject_archived(record_id)
        path = self.path_for(record_id)
        if not path.is_file():
            raise ValueError("missing")
        document, _digest = read_json(path)
        return document

    def create(self, record_id: str, document: dict[str, object]) -> None:
        self._reject_archived(record_id)
        write_json_new(self.path_for(record_id), document)

    def _reject_archived(self, record_id: str) -> None:
        if self.directory.name in {"tasks_v2", "executions"}:
            from grokbot_mcp.ledger_archive import archived
            if archived(self.directory.parent.parent, record_id, execution=self.directory.name == "executions"):
                raise ValueError("archived_task")

    def update(self, record_id: str, **fields: object) -> dict[str, object]:
        document = self.read(record_id)
        document.update(fields)
        raw = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
        check_size(raw)
        path = self.path_for(record_id)
        temporary = path.parent / f".{record_id}.update.tmp"
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return document
