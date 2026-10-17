"""Git baseline checks. Dirty files are legal only when they are on the allow list."""

from __future__ import annotations

import subprocess
from pathlib import Path

from grokbot_mcp.security.paths import contained, is_reparse_point


def _run(root: Path, args: list[str], runner=subprocess.run):
    from grokbot_mcp.security.gitread import run_git
    return run_git(root, args, runner)


def workspace_git_rejection(root: Path, runner=subprocess.run) -> str | None:
    if is_reparse_point(root):
        return "reparse"
    git_pointer = root / ".git"
    if git_pointer.is_symlink() or (git_pointer.exists() and is_reparse_point(git_pointer)):
        return "reparse"
    if not git_pointer.exists():
        return "git_status_failed"
    proc = _run(root, ["rev-parse", "--git-common-dir"], runner)
    if proc.returncode != 0:
        return "git_status_failed"
    common = Path((proc.stdout or "").strip())
    if not common.is_absolute():
        common = (root / common).resolve()
    else:
        common = common.resolve()
    if not contained(root, common):
        return "shared_object_store_rejected"
    return None


def git_snapshot(root: Path, allowed: tuple[str, ...], expected_head: str, runner=subprocess.run) -> dict[str, object]:
    head = _run(root, ["rev-parse", "HEAD"], runner)
    status = _run(root, ["status", "--porcelain"], runner)
    diff = _run(root, ["diff", "--name-only", "HEAD"], runner)
    empty = {
        "git_head": "unconfirmed",
        "head_text": "",
        "known_legal_changes": [],
        "unexpected_changes": [],
        "unconfirmed_changes": ["git_status_failed"],
        "diff_files": [],
    }
    if head.returncode != 0 or status.returncode != 0 or diff.returncode != 0:
        return empty
    known: list[str] = []
    unexpected: list[str] = []
    diff_files: list[str] = []
    allowed_set = set(allowed)
    for line in (diff.stdout or "").splitlines():
        name = line.strip().replace("\\", "/")
        if not name:
            continue
        diff_files.append(name)
        if name in allowed_set:
            known.append(name)
        else:
            unexpected.append(name)
    unconfirmed: list[str] = []
    for line in (status.stdout or "").splitlines():
        if not line.startswith("?? "):
            continue
        name = line[3:].strip().replace("\\", "/")
        if name in {".bridge", ".bridge/"} or name.startswith(".bridge/"):
            continue
        unconfirmed.append(name)
    head_text = (head.stdout or "").strip()
    return {
        "git_head": "match" if head_text == expected_head else "mismatch",
        "head_text": head_text,
        "known_legal_changes": known,
        "unexpected_changes": unexpected,
        "unconfirmed_changes": unconfirmed,
        "diff_files": diff_files,
    }


def git_error(git: dict[str, object]) -> str | None:
    if git.get("git_head") == "unconfirmed":
        return "git_status_failed"
    if git.get("git_head") != "match":
        return "git_head_mismatch"
    if git.get("unexpected_changes"):
        return "unexpected_changes"
    if git.get("unconfirmed_changes"):
        return "unconfirmed_changes"
    return None
