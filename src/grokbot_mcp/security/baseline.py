"""Pre-execution snapshot. Files that were already dirty stay out of the new delta."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from grokbot_mcp.security.paths import is_reparse_point, is_uuid, normalize_policy_path, PathRejected
from grokbot_mcp.strict_json import check_size, MAX_BYTES, decode

_SKIP_EXACT = {".bridge"}
_SKIP_PREFIXES = (".bridge/",)


def _run(root: Path, args: list[str], runner=subprocess.run):
    from grokbot_mcp.security.gitread import run_git
    return run_git(root, args, runner)


def _hash_file(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_dir():
        return "directory"
    if is_reparse_point(path):
        raise ValueError("reparse")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _skipped(name: str) -> bool:
    return name in _SKIP_EXACT or name.startswith(_SKIP_PREFIXES)


def _status_lists(
    stdout: str, *, decode_ignored: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    status_paths: list[str] = []
    ignored: list[str] = []
    untracked: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("!! "):
            name = line[3:].strip()
            if decode_ignored:
                name = normalize_policy_path(name, git_quoted=True)
            name = name.replace("\\", "/").rstrip("/")
            if name and not _skipped(name):
                ignored.append(name)
            continue
        if len(line) < 4:
            continue
        body = line[3:].strip().replace("\\", "/")
        if decode_ignored:
            body = line[3:].strip()
        names = [part.strip() for part in body.split(" -> ")] if " -> " in body else [body]
        for name in names:
            if decode_ignored:
                name = normalize_policy_path(name, git_quoted=True)
            name = name.rstrip("/")
            if not name or _skipped(name):
                continue
            status_paths.append(name)
            if line.startswith("?? ") and name not in untracked:
                untracked.append(name)
    return status_paths, ignored, untracked


def _normalized_match_path(value: str) -> str:
    return normalize_policy_path(value, casefold=True)


def _under_watched(name: str, watched: list[str]) -> bool:
    normalized_name = _normalized_match_path(name)
    for item in watched:
        prefix = _normalized_match_path(item)
        if prefix and (
            normalized_name == prefix or normalized_name.startswith(prefix + "/")
        ):
            return True
    return False


def capture_snapshot(
    root: Path,
    watched: list[str],
    runner=subprocess.run,
    *,
    include_ignored: bool = True,
    prefix_dirs: tuple[str, ...] = (),
) -> dict[str, object]:
    head = _run(root, ["rev-parse", "HEAD"], runner)
    status = _run(root, ["status", "--porcelain=v1", "--ignored", "--untracked-files=all"], runner)
    diff = _run(root, ["diff", "--name-only", "HEAD"], runner)
    if head.returncode != 0 or status.returncode != 0 or diff.returncode != 0:
        raise ValueError("git_status_failed")
    head_text = (head.stdout or "").strip()
    if not head_text:
        raise ValueError("git_status_failed")
    diff_files = [line.strip().replace("\\", "/") for line in (diff.stdout or "").splitlines() if line.strip()]
    if not include_ignored:
        diff_files = [normalize_policy_path(line.strip(), git_quoted=True)
                      for line in (diff.stdout or "").splitlines() if line.strip()]
    status_paths, ignored, untracked = _status_lists(
        status.stdout or "", decode_ignored=not include_ignored,
    )
    if not include_ignored:
        ignored = [name for name in ignored if _under_watched(name, watched)]
    hashes: dict[str, str | None] = {}
    if prefix_dirs:
        # Index + untracked candidates only. Never walk ignored build/cache trees.
        listing = _run(root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"], runner)
        if listing.returncode != 0:
            raise ValueError("git_status_failed")
        names = [normalize_policy_path(n) for n in (listing.stdout or "").split("\0") if n]
        watched = list(dict.fromkeys([*watched, *(n for n in names if _under_watched(n, list(prefix_dirs))
                                                  and not _skipped(n))]))
    for name in watched:
        if prefix_dirs and not _skipped(name) and _under_watched(name, list(prefix_dirs)):
            from grokbot_mcp.security.prefixes import literal_path
            try:
                literal_path(name)
                path = root / name
                if any(is_reparse_point(p) for p in (path, *path.parents)):
                    raise ValueError("reparse")
                if path.is_file() and path.stat().st_nlink != 1:
                    raise ValueError("reparse")
            except (ValueError, PathRejected) as exc:
                raise ValueError("reparse") from exc
        hashes[name] = _hash_file(root / name)
    extra = {}
    if not include_ignored:
        dirty_hashes = {}
        for name in sorted(set(diff_files) | set(status_paths) | set(untracked)):
            if _skipped(name):
                continue
            path = root / name
            if any(is_reparse_point(p) for p in (path, *path.parents)) or (path.is_file() and path.stat().st_nlink != 1):
                raise ValueError("reparse")
            dirty_hashes[name] = _hash_file(path)
        extra["dirty_hashes"] = dirty_hashes
    return {
        "schema_version": 1,
        "git_head_text": head_text,
        "diff_files": diff_files,
        "untracked": sorted(set(untracked)),
        "status_paths": sorted(set(status_paths)),
        "ignored": sorted(set(ignored)),
        "file_hashes": hashes,
        **extra,
    }


def watched_paths(workspace, document: dict | None = None) -> list[str]:
    watched = list(workspace.allowed_files) + list(workspace.forbidden_files)
    if workspace.allowed_prefixes and document:
        watched = list(dict.fromkeys([*watched, *document.get("allowed_files", [])]))
    return watched


def prefix_snapshot_options(workspace) -> dict:
    return ({"prefix_dirs": tuple(p.prefix for p in workspace.allowed_prefixes)}
            if workspace.allowed_prefixes else {})


def write_snapshot(directory: Path, execution_id: str, snapshot: dict[str, object]) -> str:
    if not is_uuid(execution_id):
        raise ValueError("invalid_execution_id")
    directory.mkdir(parents=True, exist_ok=True)
    if is_reparse_point(directory):
        raise ValueError("reparse")
    target = directory / f"{execution_id}.json"
    if target.exists():
        raise ValueError("baseline_exists")
    raw = json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
    check_size(raw)
    temporary = directory / f".{execution_id}.tmp"
    try:
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    if is_reparse_point(target):
        raise ValueError("reparse")
    return hashlib.sha256(raw).hexdigest()


def replace_snapshot(directory: Path, execution_id: str, snapshot: dict[str, object]) -> str:
    """Atomically replace the pre-claim advisory snapshot with claim-time evidence."""
    if not is_uuid(execution_id):
        raise ValueError("invalid_execution_id")
    target = directory / f"{execution_id}.json"
    if not target.is_file() or is_reparse_point(target):
        raise ValueError("baseline_missing")
    raw = json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
    check_size(raw)
    temporary = directory / f".{execution_id}.tmp"
    try:
        with temporary.open("wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


def load_snapshot(directory: Path, execution_id: str, expected_sha256: object) -> dict[str, object]:
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError("baseline_missing")
    target = directory / f"{execution_id}.json"
    if not target.is_file() or any(is_reparse_point(part) for part in (target, *target.parents)):
        raise ValueError("baseline_missing")
    with target.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    check_size(raw)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("baseline_missing")
    parsed = decode(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("file_hashes"), dict):
        raise ValueError("baseline_missing")
    return parsed


def same_snapshot(left: dict[str, object], right: dict[str, object]) -> bool:
    keys = ("git_head_text", "diff_files", "untracked", "status_paths", "file_hashes")
    return (
        all(left.get(key) == right.get(key) for key in keys)
        and (left.get("ignored") or []) == (right.get("ignored") or [])
        and ("dirty_hashes" not in left or left["dirty_hashes"] == right.get("dirty_hashes"))
    )


def execution_delta(baseline: dict[str, object], current: dict[str, object]) -> list[str]:
    changed: list[str] = []
    before = dict(baseline.get("file_hashes") or {})
    after = dict(current.get("file_hashes") or {})
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            changed.append(name)
    for key in ("diff_files", "untracked", "status_paths"):
        if key in baseline and key in current:
            for name in sorted(set(current.get(key) or []) - set(baseline.get(key) or [])):
                if name not in changed:
                    changed.append(name)
    for name in sorted(set(current.get("ignored") or []) - set(baseline.get("ignored") or [])):
        if name not in changed:
            changed.append(name)
    return changed
