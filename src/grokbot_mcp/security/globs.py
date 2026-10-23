"""Opt-in Git-index expansion; never a filesystem walk or a policy extension."""
from __future__ import annotations

import re
import subprocess

from grokbot_mcp.manifest.store import ManifestError
from grokbot_mcp.security.gitread import run_git
from grokbot_mcp.security.paths import normalize_repo_path, normalize_policy_path, PathRejected
from grokbot_mcp.security.targets import _require_existing_file, TargetRejected
from grokbot_mcp.security.prefixes import policy_allows


def parse_glob(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ManifestError("allowed_glob_invalid")
    match = re.fullmatch(r"([^*?\[\]{}:<>|\\\x00-\x1f]+)/\*\*(?:/\*(\.[A-Za-z0-9_-]+))?", value)
    if not match:
        raise ManifestError("allowed_glob_invalid")
    prefix, suffix = match.group(1), match.group(2) or ""
    if any(p in ("", ".", "..") or p.endswith((".", " ")) for p in prefix.split("/")):
        raise ManifestError("allowed_glob_invalid")
    try:
        if normalize_repo_path(prefix) != prefix:
            raise ManifestError("allowed_glob_invalid")
    except PathRejected as exc:
        raise ManifestError("allowed_glob_invalid") from exc
    return prefix + "/", suffix


def expand(workspace, patterns: list[str]) -> list[str]:
    if not workspace.allow_globs:
        raise ManifestError("allowed_globs_disabled")
    if not isinstance(patterns, list) or not patterns or len(patterns) > 2000:
        raise ManifestError("allowed_glob_invalid")
    parsed = [parse_glob(value) for value in patterns]
    result = run_git(workspace.root, ["ls-files", "--stage", "-z"], subprocess.run)
    if result.returncode:
        raise ManifestError("git_status_failed")
    forbidden = [normalize_policy_path(p, casefold=True) for p in
                 (*workspace.forbidden_files, workspace.protected_file, ".git", ".bridge")]
    found = set()
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        try:
            meta, name = entry.split("\t", 1)
            mode, _sha, stage = meta.split(" ")
        except ValueError as exc:
            raise ManifestError("git_status_failed") from exc
        if not any(name.startswith(prefix) and name.endswith(suffix) for prefix, suffix in parsed):
            continue
        key = name.casefold()
        if any(part in {".git", ".bridge"} for part in key.split("/")):
            continue
        if any(key == p or key.startswith(p + "/") for p in forbidden):
            continue
        if not policy_allows(workspace, name):
            continue
        if mode not in {"100644", "100755"} or stage != "0":
            raise ManifestError("allowed_glob_unsafe_file")
        try:
            _require_existing_file(workspace.root, name)
        except TargetRejected as exc:
            if exc.code == "target_file_missing":
                continue
            raise ManifestError("allowed_glob_unsafe_file") from exc
        found.add(name)
        if len(found) > workspace.glob_max_files:
            raise ManifestError("allowed_glob_limit_exceeded")
    if not found:
        raise ManifestError("allowed_glob_empty")
    return sorted(found)
