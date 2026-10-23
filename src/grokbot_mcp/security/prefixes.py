"""Literal directory policy and exact prospective targets; never a filesystem sandbox."""
from __future__ import annotations

import re
from dataclasses import dataclass

from grokbot_mcp.security.paths import PathRejected, normalize_policy_path, is_reparse_point


@dataclass(frozen=True)
class AllowedPrefix:
    prefix: str
    exts: tuple[str, ...]


def literal_path(value: str) -> str:
    # Reject BEFORE normalization: normpath would hide traversal/empty segments.
    if not isinstance(value, str) or not value or re.search(r'[\\:*?\[\]{}<>|"\x00-\x1f]', value):
        raise PathRejected("target_path_rejected")
    parts = value.split("/")
    if any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts):
        raise PathRejected("target_path_rejected")
    if any(p.casefold() in {".git", ".bridge"} for p in parts):
        raise PathRejected("forbidden_file")
    # Windows device aliases are not ordinary prospective files.
    if any(re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.I) for p in parts):
        raise PathRejected("target_path_rejected")
    return normalize_policy_path(value)


def parse_prefixes(value: object) -> tuple[AllowedPrefix, ...]:
    if not isinstance(value, list):
        raise PathRejected("target_path_rejected")
    merged: dict[str, set[str]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"prefix", "exts"}:
            raise PathRejected("target_path_rejected")
        prefix, exts = item["prefix"], item["exts"]
        if not isinstance(prefix, str) or not prefix.endswith("/"):
            raise PathRejected("target_path_rejected")
        prefix = literal_path(prefix[:-1]) + "/"
        if not isinstance(exts, list) or not exts:
            raise PathRejected("target_path_rejected")
        normalized = set()
        for ext in exts:
            if not isinstance(ext, str) or not ext.startswith(".") or len(ext) < 2 or "/" in ext:
                raise PathRejected("target_path_rejected")
            normalized.add(literal_path(ext))
        merged.setdefault(prefix, set()).update(normalized)
    return tuple(AllowedPrefix(p, tuple(sorted(exts))) for p, exts in sorted(merged.items()))


def prefix_json(workspace, *, ceiling: bool = False) -> list[dict]:
    prefixes = workspace.prefix_ceiling if ceiling else workspace.allowed_prefixes
    return [{"prefix": p.prefix, "exts": list(p.exts)} for p in prefixes]


def prefixes_within(active, ceiling) -> bool:
    return all(any(p.prefix.startswith(c.prefix) and set(p.exts) <= set(c.exts)
                   for c in ceiling) for p in active)


def manifest_workspace(workspace, document: dict):
    """Use only the frozen grant; current active prefixes cannot expand a task."""
    from dataclasses import replace
    from grokbot_mcp.manifest.store import ManifestError
    ceiling = getattr(workspace, "prefix_ceiling", None)
    if ceiling is None and "active_prefixes" not in document:
        return workspace
    try:
        active = parse_prefixes(document.get("active_prefixes"))
    except PathRejected as exc:
        raise ManifestError("active_prefixes_outside_ceiling") from exc
    if (ceiling is None
            or not prefixes_within(active, ceiling)
            or prefix_json(replace(workspace, allowed_prefixes=active)) != document["active_prefixes"]):
        raise ManifestError("active_prefixes_outside_ceiling")
    return replace(workspace, allowed_prefixes=active)


def active_prefix_fields(workspace, document: dict) -> dict:
    import hashlib
    import json
    frozen = manifest_workspace(workspace, document)
    if getattr(workspace, "prefix_ceiling", None) is None:
        return {}
    active = prefix_json(frozen)
    raw = json.dumps(active, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"active_prefixes": active, "active_prefixes_sha256": hashlib.sha256(raw).hexdigest()}


def prefix_excluded(name: str) -> bool:
    """Built-in exclusions for derived grants, never registry fingerprint input."""
    key = normalize_policy_path(name, casefold=True)
    return any(part in {".git", ".bridge", ".cursor", "agents.md"}
               for part in key.split("/"))


def prefix_allows(workspace, name: str) -> bool:
    try:
        name = literal_path(name)
        if prefix_excluded(name):
            return False
    except PathRejected:
        return False
    return any(name.startswith(p.prefix) and any(name.endswith(ext) for ext in p.exts)
               for p in workspace.allowed_prefixes)


def blocked(workspace, name: str) -> bool:
    key = normalize_policy_path(name, casefold=True)
    forbidden = [normalize_policy_path(p, casefold=True) for p in
                 (*workspace.forbidden_files, workspace.protected_file)]
    return (any(p in {".git", ".bridge"} for p in key.split("/"))
            or any(key == p or key.startswith(p + "/") for p in forbidden))


def policy_allows(workspace, name: str) -> bool:
    if not workspace.allowed_prefixes:
        return name in workspace.allowed_files
    return (name in workspace.allowed_files or prefix_allows(workspace, name)) and not blocked(workspace, name)


def prospective_file(root, name: str) -> None:
    """Check every existing component, exact case, links and regular leaf type."""
    from grokbot_mcp.security.targets import TargetRejected
    current = root
    for ancestor in (root, *root.parents):
        if is_reparse_point(ancestor):
            raise TargetRejected("reparse")
    parts = name.split("/")
    for index, part in enumerate(parts):
        if not current.is_dir():
            raise TargetRejected("target_not_file")
        exact = folded = None
        for entry in current.iterdir():
            if entry.name == part:
                exact = entry
                break
            if entry.name.casefold() == part.casefold():
                folded = entry
        if exact is None:
            if folded is not None:
                raise TargetRejected("target_case_mismatch")
            return  # All remaining components are absent, and were lexically checked.
        if is_reparse_point(exact):
            raise TargetRejected("reparse")
        if index == len(parts) - 1:
            if not exact.is_file():
                raise TargetRejected("target_not_file")
            if exact.stat().st_nlink != 1:
                raise TargetRejected("target_hardlink_rejected")
        current = exact


def validate_create_files(workspace, values: object, *, check_disk: bool = True) -> list[str]:
    from grokbot_mcp.manifest.store import ManifestError
    from grokbot_mcp.security.targets import TargetRejected
    if not isinstance(values, list):
        raise ManifestError("create_file_outside_policy")
    if len(values) > workspace.glob_max_files:
        raise ManifestError("allowed_glob_limit_exceeded")
    result = []
    for value in values:
        try:
            name = literal_path(value)
            if not prefix_allows(workspace, name) or blocked(workspace, name):
                raise ManifestError("create_file_outside_policy")
            if check_disk:
                prospective_file(workspace.root, name)
        except (PathRejected, TargetRejected) as exc:
            raise ManifestError(exc.code) from exc
        if name not in result:
            result.append(name)
    return result


def manifest_scope_valid(workspace, document: dict) -> bool:
    from grokbot_mcp.manifest.store import ManifestError
    try:
        workspace = manifest_workspace(workspace, document)
    except ManifestError:
        return False
    allowed = document.get("allowed_files")
    if not isinstance(allowed, list) or not all(isinstance(n, str) and policy_allows(workspace, n) for n in allowed):
        return False
    expansion = document.get("scope_expansion", {})
    if not isinstance(expansion, dict):
        return False
    if "create_files" in expansion:
        try:
            declared = validate_create_files(workspace, expansion["create_files"], check_disk=False)
        except ManifestError:
            return False
        if declared != expansion["create_files"] or not set(declared) <= set(allowed):
            return False
        if len(allowed) > workspace.glob_max_files:
            return False
    return True


def resolve_task_files(workspace, document: dict) -> list[str]:
    from grokbot_mcp.manifest.store import ManifestError
    from grokbot_mcp.security.targets import resolve_exact_files, TargetRejected
    try:
        workspace = manifest_workspace(workspace, document)
    except ManifestError as exc:
        raise TargetRejected(exc.code) from exc
    allowed = document.get("allowed_files") or []
    creates = document.get("scope_expansion", {}).get("create_files")
    if creates is None:
        return resolve_exact_files(workspace.root, allowed, workspace.forbidden_files)
    if not manifest_scope_valid(workspace, document):
        raise TargetRejected("target_path_rejected")
    try:
        validate_create_files(workspace, creates)
    except ManifestError as exc:
        raise TargetRejected(exc.code) from exc
    existing = [name for name in allowed if name not in creates]
    if existing:
        resolve_exact_files(workspace.root, existing, workspace.forbidden_files)
    return allowed
