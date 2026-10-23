"""Pre-execution snapshot. Files that were already dirty stay out of the new delta."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from grokbot_mcp.security.paths import is_reparse_point, is_uuid

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


def _status_lists(stdout: str) -> tuple[list[str], list[str], list[str]]:
    status_paths: list[str] = []
    ignored: list[str] = []
    untracked: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("!! "):
            name = line[3:].strip().replace("\\", "/").rstrip("/")
            if name and not _skipped(name):
                ignored.append(name)
            continue
        if len(line) < 4:
            continue
        body = line[3:].strip().replace("\\", "/")
        names = [part.strip() for part in body.split(" -> ")] if " -> " in body else [body]
        for name in names:
            name = name.rstrip("/")
            if not name or _skipped(name):
                continue
            status_paths.append(name)
            if line.startswith("?? ") and name not in untracked:
                untracked.append(name)
    return status_paths, ignored, untracked


def capture_snapshot(
    root: Path,
    watched: list[str],
    runner=subprocess.run,
    *,
    include_ignored: bool = True,
) -> dict[str, object]:
    head = _run(root, ["rev-parse", "HEAD"], runner)
    status_args = ["status", "--porcelain=v1"]
    if include_ignored:
        status_args.append("--ignored")
    status_args.append("--untracked-files=all")
    status = _run(root, status_args, runner)
    diff = _run(root, ["diff", "--name-only", "HEAD"], runner)
    if head.returncode != 0 or status.returncode != 0 or diff.returncode != 0:
        raise ValueError("git_status_failed")
    head_text = (head.stdout or "").strip()
    if not head_text:
        raise ValueError("git_status_failed")
    diff_files = [line.strip().replace("\\", "/") for line in (diff.stdout or "").splitlines() if line.strip()]
    status_paths, ignored, untracked = _status_lists(status.stdout or "")
    hashes: dict[str, str | None] = {}
    for name in watched:
        hashes[name] = _hash_file(root / name)
    return {
        "schema_version": 1,
        "git_head_text": head_text,
        "diff_files": diff_files,
        "untracked": sorted(set(untracked)),
        "status_paths": sorted(set(status_paths)),
        "ignored": sorted(set(ignored)),
        "file_hashes": hashes,
    }


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
    raw = target.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("baseline_missing")
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("file_hashes"), dict):
        raise ValueError("baseline_missing")
    return parsed


def same_snapshot(left: dict[str, object], right: dict[str, object]) -> bool:
    keys = ("git_head_text", "diff_files", "untracked", "status_paths", "ignored", "file_hashes")
    return all(left.get(key) == right.get(key) for key in keys)


def execution_delta(baseline: dict[str, object], current: dict[str, object]) -> list[str]:
    changed: list[str] = []
    before = dict(baseline.get("file_hashes") or {})
    after = dict(current.get("file_hashes") or {})
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            changed.append(name)
    for key in ("diff_files", "untracked", "status_paths", "ignored"):
        if key in baseline and key in current:
            for name in sorted(set(current.get(key) or []) - set(baseline.get(key) or [])):
                if name not in changed:
                    changed.append(name)
    return changed
