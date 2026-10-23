"""Native read-only comparison of claim evidence; paths are never instructions."""
from __future__ import annotations

import re

from grokbot_mcp.security.baseline import execution_delta, _skipped
from grokbot_mcp.security.gitread import run_git
from grokbot_mcp.security.paths import normalize_policy_path


def changes_since_baseline(root, baseline: dict, current: dict) -> list[str]:
    names = execution_delta(baseline, current)
    before = baseline.get("dirty_hashes", {})
    after = current.get("dirty_hashes", {})
    # Old snapshots have no dirty hashes. Do not invent a baseline for them.
    if "dirty_hashes" in baseline and "dirty_hashes" in current:
        old = {normalize_policy_path(n, casefold=True): (n, h) for n, h in before.items()}
        new = {normalize_policy_path(n, casefold=True): (n, h) for n, h in after.items()}
        for key in old.keys() | new.keys():
            if old.get(key) != new.get(key):
                names.append(new.get(key, old.get(key))[0])
    head_before, head_after = baseline.get("git_head_text"), current.get("git_head_text")
    if head_before != head_after:
        if any(type(h) is not str or not re.fullmatch(r"[0-9a-f]{40}", h) for h in (head_before, head_after)):
            raise ValueError("git_status_failed")
        diff = run_git(root, ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", head_before, head_after, "--"])
        if diff.returncode:
            raise ValueError("git_status_failed")
        names.extend(n for n in diff.stdout.split("\0") if n)
    normalized = {}
    for name in names:
        name = normalize_policy_path(name)
        if not _skipped(name):
            normalized[normalize_policy_path(name, casefold=True)] = name
    return [normalized[k] for k in sorted(normalized)]


def review(baseline: dict, current: dict, changes: list[str], reported: list[str],
           allowed: list[str], other_prefixes) -> dict:
    allowed_keys = {normalize_policy_path(p, casefold=True) for p in allowed}
    prefixes = [normalize_policy_path(p, casefold=True) for p in other_prefixes]
    groups = {"in_scope": [], "declared_others": [], "out_of_scope": []}
    # Agent reports cannot hide an observed path, and remain explicitly labelled.
    names = {}
    for name in [*reported, *changes]:
        names[normalize_policy_path(name, casefold=True)] = normalize_policy_path(name)
    for key in sorted(names):
        group = ("in_scope" if key in allowed_keys else "declared_others"
                 if any(key == p or key.startswith(p + "/") for p in prefixes) else "out_of_scope")
        groups[group].append(names[key])
    return {"baseline_head": baseline.get("git_head_text"), "head_after": current.get("git_head_text"),
            "head_changed": baseline.get("git_head_text") != current.get("git_head_text"),
            "changes_since_baseline": changes, "reported_changes": reported, **groups}
