"""Reject absolute paths, traversal, and Windows reparse points."""

from __future__ import annotations

import ctypes
import posixpath
import re
import unicodedata
from pathlib import Path

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DRIVE = re.compile(r"^[A-Za-z]:")


class PathRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def decode_git_path(value: str) -> str:
    """Decode Git C quoting as UTF-8 bytes; retain malformed input verbatim."""
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        return value
    escapes = dict(zip('abtnvfr"\\', '\a\b\t\n\v\f\r"\\'))
    raw = bytearray()
    index = 1
    try:
        while index < len(value) - 1:
            char = value[index]
            index += 1
            if char == "\\":
                if index >= len(value) - 1:
                    return value
                char = value[index]
                index += 1
                if char in "01234567":
                    digits = char
                    while index < len(value) - 1 and len(digits) < 3 and value[index] in "01234567":
                        digits += value[index]
                        index += 1
                    raw.append(int(digits, 8))
                    continue
                if char not in escapes:
                    return value
                char = escapes[char]
            raw.extend(char.encode("utf-8"))
        return raw.decode("utf-8")
    except (UnicodeError, ValueError):
        return value


def normalize_policy_path(value: str, *, casefold: bool = False, git_quoted: bool = False) -> str:
    """Canonical advisory repo path. Case folding is for comparisons only.

    Strict callers retain normalize_repo_path and their existing byte format.
    Advisory always folds comparisons, including Windows/core.ignorecase repos.
    """
    if not isinstance(value, str):
        raise PathRejected("absolute_path")
    text = decode_git_path(value) if git_quoted else value
    if git_quoted and value.startswith('"') and text == value:
        raise PathRejected("path_encoding_invalid")
    text = unicodedata.normalize("NFC", text).replace("\\", "/")
    if text.startswith("/") or _DRIVE.match(text):
        raise PathRejected("absolute_path")
    text = posixpath.normpath(text)
    if text == ".." or text.startswith("../"):
        raise PathRejected("traversal")
    if text in ("", ".") or "\x00" in text or _DRIVE.match(text):
        raise PathRejected("absolute_path")
    return text.casefold() if casefold else text


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
