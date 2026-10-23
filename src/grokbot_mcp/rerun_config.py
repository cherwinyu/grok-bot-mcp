"""Local administrator test configuration, deliberately outside Routine policy."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from grokbot_mcp.security.paths import (
    PathRejected, contained, is_reparse_point, normalize_repo_path,
)
from grokbot_mcp.test_commands import validate_options


@dataclass(frozen=True)
class RerunConfig:
    argv: tuple[str, ...]
    cwd: str | None = None
    env: tuple[tuple[str, str], ...] = ()
    timeout_seconds: int = 600


def resolve_cwd(root: Path, relative: str | None, forbidden: tuple[str, ...], protected: str) -> Path:
    """Recheck on use: a directory may have become a junction since registration."""
    candidate = root
    if relative is not None:
        try:
            relative = normalize_repo_path(relative)
            parts = relative.split("/")
            if any(any(ord(c) < 32 or c in ':<>"|?*' for c in p)
                   or p.endswith((".", " "))
                   or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", p)
                   for p in parts):
                raise ValueError("rerun_cwd_invalid")
            key = relative.casefold()
            blocked = {normalize_repo_path(p).casefold() for p in (*forbidden, protected) if p}
            if any(p.casefold() in {".git", ".bridge"} for p in parts) or any(
                    key == p or key.startswith(p + "/") for p in blocked):
                raise ValueError("rerun_cwd_forbidden")
        except PathRejected as exc:
            raise ValueError("rerun_cwd_invalid") from exc
        candidate = root / relative
    if any(is_reparse_point(p) for p in (candidate, *candidate.parents)):
        raise ValueError("rerun_cwd_reparse")
    if not contained(root, candidate):
        raise ValueError("rerun_cwd_invalid")
    if not candidate.is_dir():
        raise ValueError("rerun_cwd_missing")
    if relative is not None:
        # Compare the resolved spelling too (for example Windows short names).
        try:
            resolved = candidate.resolve().relative_to(root.resolve()).as_posix().casefold()
        except (OSError, ValueError) as exc:
            raise ValueError("rerun_cwd_invalid") from exc
        if any(p in {".git", ".bridge"} for p in resolved.split("/")) or any(
                resolved == p or resolved.startswith(p + "/") for p in blocked):
            raise ValueError("rerun_cwd_forbidden")
    return candidate


def parse_rerun(value: object, *, root: Path | None = None,
                forbidden: tuple[str, ...] = (), protected: str = "") -> RerunConfig:
    if type(value) is not dict or set(value) - {"argv", "cwd", "env", "timeout_seconds"}:
        raise ValueError("rerun_invalid")
    argv = value.get("argv")
    if type(argv) is not list or not argv:
        raise ValueError("rerun_argv_invalid")
    # Reuse the existing executable and token grammar, allowing no placeholder.
    template = argv if "{changed_files}" in argv else [*argv, "{changed_files}"]
    try:
        validate_options({"argv": template})
    except ValueError as exc:
        raise ValueError("rerun_argv_invalid") from exc
    cwd = None
    if "cwd" in value:
        try:
            cwd = normalize_repo_path(value["cwd"])
        except PathRejected as exc:
            raise ValueError("rerun_cwd_invalid") from exc
    env = value.get("env", {})
    if (type(env) is not dict or any(type(k) is not str or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k)
                                   or type(v) is not str or "\x00" in v for k, v in env.items())):
        raise ValueError("rerun_env_invalid")
    timeout = value.get("timeout_seconds", 600)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("rerun_timeout_invalid")
    if root is not None:
        resolve_cwd(root, cwd, forbidden, protected)
    return RerunConfig(tuple(argv), cwd, tuple(env.items()), timeout)
