"""Reject absolute paths, traversal, and Windows reparse points."""

from __future__ import annotations

import ctypes
import re
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DRIVE = re.compile(r"^[A-Za-z]:")


class PathRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_uuid(value: str) -> bool:
    return bool(UUID_RE.fullmatch(value))


def is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    get_attrs = ctypes.windll.kernel32.GetFileAttributesW
    get_attrs.argtypes = [ctypes.c_wchar_p]
    get_attrs.restype = ctypes.c_uint32
    attrs = int(get_attrs(str(path)))
    if attrs == INVALID_FILE_ATTRIBUTES:
        return False
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def normalize_repo_path(value: str) -> str:
    if not isinstance(value, str):
        raise PathRejected("absolute_path")
    text = value.strip().replace("\\", "/")
    if text.startswith("/") or text.startswith("//") or _DRIVE.match(text):
        raise PathRejected("absolute_path")
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PathRejected("traversal")
    if not parts:
        raise PathRejected("absolute_path")
    return "/".join(parts)


def contained(root: Path, candidate: Path) -> bool:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    return resolved == resolved_root or resolved_root in resolved.parents
