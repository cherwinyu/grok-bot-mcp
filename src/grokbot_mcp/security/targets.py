"""Exact allow-list match. A nearby filename is not a match."""

from __future__ import annotations

from pathlib import Path

from grokbot_mcp.security.paths import PathRejected, contained, is_reparse_point, normalize_repo_path


class TargetRejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def resolve_exact_files(root: Path, allowed: list[str], forbidden: tuple[str, ...]) -> list[str]:
    if not allowed:
        raise TargetRejected("target_file_missing")
    exact: list[str] = []
    for item in allowed:
        stored = item.strip().replace("\\", "/")
        try:
            text = normalize_repo_path(item)
        except PathRejected as exc:
            raise TargetRejected(exc.code) from exc
        if text != stored or text in exact:
            raise TargetRejected("target_path_rejected")
        if _forbidden(text, forbidden):
            raise TargetRejected("forbidden_file")
        _require_existing_file(root, text)
        exact.append(text)
    return exact


def _forbidden(name: str, forbidden: tuple[str, ...]) -> bool:
    return any(name == item or name.startswith(item + "/") for item in forbidden)


def _require_existing_file(root: Path, relative: str) -> None:
    if is_reparse_point(root):
        raise TargetRejected("reparse")
    current = root
    for part in relative.split("/"):
        if is_reparse_point(current) or not current.is_dir():
            raise TargetRejected("reparse" if is_reparse_point(current) else "target_file_missing")
        exact = None
        folded = None
        for entry in current.iterdir():
            if entry.name == part:
                exact = entry
                break
            if entry.name.lower() == part.lower():
                folded = entry
        chosen = exact or folded
        if chosen is None:
            raise TargetRejected("target_file_missing")
        if exact is None:
            raise TargetRejected("target_case_mismatch")
        if is_reparse_point(chosen):
            raise TargetRejected("reparse")
        if not contained(root, chosen):
            raise TargetRejected("traversal")
        current = chosen
    if not current.is_file():
        raise TargetRejected("target_not_file")
    if current.stat().st_nlink != 1:
        raise TargetRejected("target_hardlink_rejected")
