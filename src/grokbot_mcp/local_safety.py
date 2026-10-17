"""Strict local JSON and bounded, conflict-aware writes. Not an OS sandbox."""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from grokbot_mcp.identity import digest
from grokbot_mcp.security.paths import is_reparse_point


class LocalError(ValueError):
    def __init__(self, code: str, *, transaction_id: str | None = None):
        self.code = code
        self.transaction_id = transaction_id
        super().__init__(code)


def safe_path(path: Path, *, regular: bool = False) -> None:
    if not path.is_absolute():
        raise LocalError("absolute_path_required")
    if any(is_reparse_point(p) for p in (path, *path.parents)):
        raise LocalError("linked_path_rejected")
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise LocalError("linked_path_rejected")
    if regular and not path.is_file():
        raise LocalError("file_missing")


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise LocalError("json_duplicate_field")
        result[key] = value
    return result


def parse_json(raw: bytes) -> dict:
    from grokbot_mcp.strict_json import decode
    try:
        return decode(raw)
    except UnicodeError as exc:
        raise LocalError("json_encoding_invalid") from exc
    except json.JSONDecodeError as exc:
        raise LocalError("json_incomplete_or_invalid") from exc
    except ValueError as exc:
        codes = {"encoding_rejected": "json_encoding_invalid", "schema_invalid": "json_object_required",
                 "json_too_large": "json_too_large", "json_duplicate_field": "json_duplicate_field",
                 "json_nonfinite": "json_nonfinite", "json_depth_exceeded": "json_depth_exceeded"}
        raise LocalError(codes.get(str(exc), "json_incomplete_or_invalid")) from None


def read_document(path: Path) -> tuple[dict, bytes]:
    safe_path(path, regular=True)
    try:
        with path.open("rb") as handle:
            raw = handle.read(4 * 1024 * 1024 + 1)
    except OSError as exc:
        raise LocalError("file_unreadable") from exc
    return parse_json(raw), raw


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def current_hash(path: Path) -> str | None:
    safe_path(path)
    return digest(path.read_bytes()) if path.exists() else None


def atomic_replace(path: Path, raw: bytes, *, expected: str | None) -> None:
    """Call with the relevant application lock. Refuse external edits before replace."""
    safe_path(path)
    if not path.parent.is_dir():
        raise LocalError("parent_missing")
    if current_hash(path) != expected:
        raise LocalError("input_changed")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".grokbot-", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        safe_path(path)
        if current_hash(path) != expected:
            raise LocalError("input_changed")
        os.replace(temporary, path)
    except OSError as exc:
        raise LocalError("atomic_write_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def create_new(path: Path, raw: bytes) -> None:
    safe_path(path)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise LocalError("backup_already_exists") from exc


@contextmanager
def local_lock(path: Path):
    """Only mutation commands call this; read-only checks do not create lock files."""
    import msvcrt
    safe_path(path)
    with path.open("a+b") as handle:
        handle.seek(0)
        handle.write(b"\0")
        handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise LocalError("operation_locked") from exc
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
