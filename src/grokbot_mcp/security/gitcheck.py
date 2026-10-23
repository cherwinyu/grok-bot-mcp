"""Git baseline checks. Dirty files are legal only when they are on the allow list."""

from __future__ import annotations

import subprocess
from pathlib import Path

from grokbot_mcp.security.paths import contained, is_reparse_point, normalize_policy_path


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


def git_snapshot(root: Path, allowed: tuple[str, ...], expected_head: str,
                 forbidden: tuple[str, ...] = (), runner=subprocess.run) -> dict[str, object]:
    head = _run(root, ["rev-parse", "HEAD"], runner)
    status = _run(root, ["status", "--porcelain"], runner)
    diff = _run(root, ["diff", "--name-only", "HEAD"], runner)
    protected = tuple(
        normalize_policy_path(name) for name in forbidden
        if normalize_policy_path(name, casefold=True) not in {".git", ".bridge"}
    )
    protected_status = (
        _run(root, ["status", "--porcelain=v1", "--ignored", "--untracked-files=all",
                    "--", *(":(icase,literal)" + name for name in protected)], runner)
        if protected else None
    )
    empty = {
        "git_head": "unconfirmed",
        "head_text": "",
        "known_legal_changes": [],
        "unexpected_changes": [],
        "unconfirmed_changes": ["git_status_failed"],
        "diff_files": [],
    }
    if (head.returncode != 0 or status.returncode != 0 or diff.returncode != 0
            or protected_status is not None and protected_status.returncode != 0):
        if protected:
            empty["protected_or_forbidden_changes"] = []
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
    result = {
        "git_head": "match" if head_text == expected_head else "mismatch",
        "head_text": head_text,
        "known_legal_changes": known,
        "unexpected_changes": unexpected,
        "unconfirmed_changes": unconfirmed,
        "diff_files": diff_files,
    }
    if protected_status is not None:
        protected_changes: list[str] = []
        for line in (protected_status.stdout or "").splitlines():
            if len(line) < 4:
                continue
            body = line[3:].strip()
            names = [part.strip() for part in body.split(" -> ")] if " -> " in body else [body]
            for name in names:
                name = normalize_policy_path(name, git_quoted=True)
                if name and name not in protected_changes:
                    protected_changes.append(name)
        result["protected_or_forbidden_changes"] = sorted(protected_changes)
    return result


def git_error(git: dict[str, object], git_gate: str = "strict") -> str | None:
    if git.get("git_head") == "unconfirmed":
        return "git_status_failed"
    if git.get("protected_or_forbidden_changes"):
        return "protected_or_forbidden_changed"
    if git_gate == "advisory":
        return None
    if git.get("git_head") != "match":
        return "git_head_mismatch"
    if git.get("unexpected_changes"):
        return "unexpected_changes"
    if git.get("unconfirmed_changes"):
        return "unconfirmed_changes"
    return None
