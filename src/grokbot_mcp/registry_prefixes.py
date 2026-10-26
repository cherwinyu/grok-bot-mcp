"""Directory-policy previews and explicit audited updates; never rebinding."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path, PurePosixPath

from grokbot_mcp.local_safety import LocalError, json_bytes, create_new
from grokbot_mcp.registry import load_registry, _workspace, policy_fingerprint
from grokbot_mcp.registry import read_registry_document as read_document
from grokbot_mcp.security.paths import PathRejected
from grokbot_mcp.security.prefixes import literal_path, parse_prefixes, prefix_json, prefix_allows


def preview(registry: Path, workspace_id: str, *, write_to: Path | None = None) -> dict:
    registry = registry.absolute()
    registered = load_registry(registry)
    workspace = registered.get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    raw, before = read_document(registry)
    source = next(w for w in raw["workspaces"] if w["workspace_id"] == workspace_id)
    if _workspace(source) != workspace:
        raise LocalError("input_changed")
    groups: dict[str, set[str]] = {}
    for name in workspace.allowed_files:
        try:
            path = PurePosixPath(literal_path(name))
            if path.parent == PurePosixPath(".") or not path.suffix:
                continue  # No root-wide grants or invented extension wildcard.
            prefix = path.parent.as_posix() + "/"
            parse_prefixes([{"prefix": prefix, "exts": [path.suffix]}])
        except PathRejected:
            continue
        groups.setdefault(prefix, set()).add(path.suffix)
    candidate = deepcopy(raw)
    item = next(w for w in candidate["workspaces"] if w["workspace_id"] == workspace_id)
    item["allowed_prefixes"] = prefix_json(workspace) + [
        {"prefix": prefix, "exts": sorted(exts)} for prefix, exts in sorted(groups.items())]
    proposed = _workspace(item)
    item["allowed_prefixes"] = prefix_json(proposed)
    item["allowed_files"] = [n for n in workspace.allowed_files if not prefix_allows(proposed, n)]
    proposed = _workspace(item)
    result = {"ok": True, "workspace_id": workspace_id, "dry_run": write_to is None,
              "allowed_prefixes": prefix_json(proposed), "allowed_files": list(proposed.allowed_files),
              "old_policy_fingerprint": policy_fingerprint(workspace),
              "new_policy_fingerprint": policy_fingerprint(proposed),
              "scope_broadens": bool(groups), "binding_changed": False,
              "credential_values_read": False, "webhook_sent": False,
              "operator_message": "Review directory grants; a copied registry does not update or confirm Routine bindings. / 请审查目录授权；副本不会更新或确认绑定。"}
    if write_to is not None:
        from grokbot_mcp.setup_wizard import validate_export_destination
        destination = validate_export_destination(write_to.absolute(), registry)
        if registry.read_bytes() != before:
            raise LocalError("input_changed")
        create_new(destination, json_bytes(candidate))
        result["copy_written"] = True
    return result


def change(registry: Path, workspace_id: str, *, add=(), remove=(), apply=False) -> dict:
    """Validate the entire proposal before creating even a lock or backup file."""
    import json
    import os
    from datetime import datetime, timezone
    from grokbot_mcp.identity import digest
    from grokbot_mcp.local_safety import local_lock, safe_path, atomic_replace

    registry = registry.absolute()
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    raw, before = read_document(registry)
    source = next(w for w in raw["workspaces"] if w["workspace_id"] == workspace_id)
    if _workspace(source) != workspace:
        raise LocalError("input_changed")
    try:
        removed = {parse_prefixes([{"prefix": p, "exts": [".md"]}])[0].prefix for p in remove}
        prefixes = [p for p in prefix_json(workspace) if p["prefix"] not in removed]
        for spec in add:
            prefix, separator, extensions = spec.partition(":")
            if not separator:
                raise LocalError("invalid_arguments")
            prefixes.append({"prefix": prefix, "exts": extensions.split(",")})
        normalized = parse_prefixes(prefixes)
    except (PathRejected, AttributeError) as exc:
        raise LocalError("invalid_arguments") from exc
    candidate = deepcopy(raw)
    item = next(w for w in candidate["workspaces"] if w["workspace_id"] == workspace_id)
    item["allowed_prefixes"] = [{"prefix": p.prefix, "exts": list(p.exts)} for p in normalized]
    proposed = _workspace(item)  # Includes ceiling validation, before ALL writes.
    encoded = json_bytes(candidate)
    old_hash, new_hash = policy_fingerprint(workspace), policy_fingerprint(proposed)
    result = {"ok": True, "workspace_id": workspace_id, "dry_run": not apply,
              "applied": False, "before_prefixes": prefix_json(workspace),
              "allowed_prefixes": prefix_json(proposed), "old_policy_fingerprint": old_hash,
              "new_policy_fingerprint": new_hash, "binding_changed": False,
              "routine_update_required": old_hash != new_hash,
              "credential_values_read": False, "webhook_sent": False}
    if not apply:
        return result
    audit = registry.parent / "policy_changes.jsonl"
    safe_path(audit)
    if audit.exists() and not audit.is_file():
        raise LocalError("audit_not_file")
    with local_lock(registry.parent / ".registry-prefixes.lock"):
        if registry.read_bytes() != before:
            raise LocalError("input_changed")
        timestamp = datetime.now(timezone.utc)
        backup = registry.with_name(registry.name + ".bak_" + timestamp.strftime("%Y%m%dT%H%M%S%fZ"))
        event = {"timestamp": timestamp.isoformat(), "workspace_id": workspace_id,
                 "before_prefixes": prefix_json(workspace), "after_prefixes": prefix_json(proposed),
                 "before_fingerprint": old_hash, "after_fingerprint": new_hash}
        safe_path(audit)
        create_new(backup, before)
        # Open the append sink before replacement to detect permission errors.
        # Audit is retrospective evidence, not an authorization gate.
        with audit.open("a", encoding="utf-8", newline="\n") as handle:
            atomic_replace(registry, encoded, expected=digest(before))
            result.update(applied=True, backup=backup.name, audit=audit.name)
            try:
                handle.write(json.dumps(event, ensure_ascii=True, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            except OSError:
                # Never claim rollback after the atomic registry commit.
                result.update(ok=False, error="policy_audit_write_failed")
    return result
