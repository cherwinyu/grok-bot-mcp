"""Read-only directory-policy suggestions; optional new registry copy, never rebinding."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path, PurePosixPath

from grokbot_mcp.local_safety import LocalError, read_document, json_bytes, create_new
from grokbot_mcp.registry import load_registry, _workspace, policy_fingerprint
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
