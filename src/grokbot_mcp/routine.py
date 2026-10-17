"""Administrator attestations of a Routine's binding; never a remote handshake."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from grokbot_mcp.registry import Workspace, default_registry_path, policy_fingerprint
from grokbot_mcp.storage import read_json

_ALLOWED = {"enabled", "paused", "unknown"}
MODES = ("reuse", "create", "migrate")


def _timestamp(value: object) -> bool:
    try:
        moment = datetime.fromisoformat(str(value))
        return moment.tzinfo is not None and moment <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def read_routine(path: Path) -> dict[str, object]:
    unknown = {"routine_status": "unknown", "routine_status_source": "absent",
               "routine_status_verified_at": None, "binding": None}
    try:
        data, digest = read_json(path)
    except FileNotFoundError:
        return unknown
    except (OSError, ValueError):
        return {**unknown, "routine_status_source": "unreadable"}
    if data.get("routine_status_source") != "user_confirmed":
        return {**unknown, "routine_status_source": "unverified"}
    if data.get("routine_status") not in _ALLOWED or not _timestamp(data.get("routine_status_verified_at")):
        return unknown
    return {"routine_status": data["routine_status"], "routine_status_source": "user_confirmed",
            "routine_status_verified_at": data["routine_status_verified_at"],
            "binding": data.get("binding"), "confirmation_sha256": digest}


def routine_allows_submit(status: dict[str, object]) -> bool:
    """Compatibility helper: enabled attestation ONLY, not full binding readiness."""
    return status.get("routine_status") == "enabled" and status.get("routine_status_source") == "user_confirmed"


def claim_entry(workspace: Workspace) -> str:
    return "grokbot_mcp.claim_cli --workspace " + workspace.workspace_id


def binding_error(workspace: Workspace, phase: str, binding: object, registry: Path) -> str | None:
    if not isinstance(binding, dict):
        return "binding_unknown"
    revision = binding.get("revision")
    if (binding.get("schema_version") != 1 or isinstance(revision, bool)
            or not isinstance(revision, int) or revision < 1 or binding.get("mode") not in MODES
            or binding.get("verification_source") != "administrator_confirmed"
            or not _timestamp(binding.get("verified_at"))):
        return "binding_invalid"
    root = binding.get("workspace_root")
    if (binding.get("workspace_id") != workspace.workspace_id or not isinstance(root, str)
            or not Path(root).is_absolute() or Path(root).resolve() != workspace.root.resolve()
            or binding.get("action") != phase + "_manifest"):
        return "binding_workspace_mismatch"
    if (binding.get("policy_fingerprint") != policy_fingerprint(workspace)
            or binding.get("policy_revision") != workspace.policy_revision):
        return "binding_policy_mismatch"
    registry_text = binding.get("registry_path")
    if (binding.get("claim_entry") != claim_entry(workspace) or not isinstance(registry_text, str)
            or not Path(registry_text).is_absolute() or Path(registry_text).resolve() != registry.resolve()):
        return "binding_claim_entry_mismatch"
    capabilities = binding.get("protocol_capabilities")
    required = {"manifest-v1"} | ({"claim-once-v1", "claim-evidence-v2"} if phase == "execute" else set())
    if not isinstance(capabilities, list) or not all(isinstance(x, str) for x in capabilities) or not required.issubset(capabilities):
        return "binding_capability_missing"
    if "claim_interpreter" in binding or "expected_installation_id" in binding:
        from grokbot_mcp.client_config import inspect_installation
        from grokbot_mcp.local_safety import LocalError
        value = binding.get("claim_interpreter")
        if type(value) is not str or not Path(value).is_absolute():
            return "binding_claim_interpreter_invalid"
        try:
            installation = inspect_installation(Path(value))
        except (LocalError, OSError, ValueError):
            return "binding_claim_interpreter_invalid"
        if installation["installation_id"] != binding.get("expected_installation_id"):
            return "binding_installation_mismatch"
        if binding.get("result_contract_version") != 1 or type(binding.get("result_contract_version")) is not int:
            return "binding_capability_missing"
    return None


def migration_gate(workspace: Workspace) -> str | None:
    """A pending/crashed local migration cannot be bypassed by a status-file edit."""
    path = workspace.root / ".bridge/deployment_migration.json"
    try:
        from grokbot_mcp.local_safety import read_document, safe_path
        from grokbot_mcp.security.paths import is_uuid
        safe_path(path)
        if not path.exists():
            return None
        data, _ = read_document(path)
        if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
                or type(data.get("transaction_id")) is not str or not is_uuid(data["transaction_id"])
                or data.get("state") not in {"pending_remote_review", "completed", "rolled_back"}):
            return "migration_state_invalid"
        return None if data["state"] in {"completed", "rolled_back"} else "migration_pending"
    except (OSError, ValueError):
        return "migration_state_invalid"


def routine_readiness(workspace: Workspace, phase: str, registry: Path | None = None) -> dict:
    if phase not in {"validate", "execute"}:
        raise ValueError("invalid_routine_phase")
    status = read_routine(workspace.root / ".bridge" / (phase + "_routine_status.json"))
    binding = status.get("binding")
    try:
        problem = binding_error(workspace, phase, binding, registry or default_registry_path())
    except (OSError, ValueError):
        problem = "binding_invalid"
    from grokbot_mcp.execution_profile import block
    problem = migration_gate(workspace) or block(workspace, phase) or problem
    enabled = routine_allows_submit(status)
    error = ("routine_paused" if status["routine_status"] == "paused" else "routine_status_unknown") if not enabled else problem
    metadata = public_binding(workspace, binding) or {}
    return {
        **status, "binding_confirmed": problem is None, "binding_error": problem,
        "binding_revision": metadata.get("revision"), "binding_verified_at": metadata.get("verified_at"),
        "binding_source": metadata.get("verification_source"), "binding_mode": metadata.get("mode"),
        "template_version": metadata.get("template_version"), "routine_enabled": enabled,
        "remote_binding_verified": False, "submit_ready": enabled and problem is None, "error": error,
    }


def confirmation(workspace: Workspace, phase: str, registry: Path, *, mode: str,
                 revision: int, verified_at: str, legacy: bool = True) -> dict:
    """Build an attestation AFTER the administrator explicitly checks the binding.

    Not exposed to MCP callers; does not write, grant a task, enable the global
    switch, or claim a remote model/connection was verified.
    """
    if mode not in MODES or phase not in {"validate", "execute"}:
        raise ValueError("invalid_binding_mode")
    return {
        "routine_status": "enabled", "routine_status_source": "user_confirmed",
        "routine_status_verified_at": verified_at,
        "binding": {
            "schema_version": 1, "revision": revision, "mode": mode,
            "workspace_id": workspace.workspace_id, "workspace_root": str(workspace.root.resolve()),
            "action": phase + "_manifest", "policy_revision": workspace.policy_revision,
            "policy_fingerprint": policy_fingerprint(workspace), "claim_entry": claim_entry(workspace),
            "registry_path": str(registry.resolve()),
            "protocol_capabilities": ["manifest-v1", "claim-once-v1", "claim-evidence-v2"],
            "reuse_scope": "retain-routine-update-claim" if mode == "reuse" else "not-applicable",
            "template_version": "legacy-unverified" if legacy else "v1",
            "verified_at": verified_at, "verification_source": "administrator_confirmed",
        },
    }


def public_binding(workspace: Workspace, binding: object) -> dict | None:
    """Preserve recognized metadata, never forward arbitrary local binding payloads."""
    if not isinstance(binding, dict):
        return None
    import re
    result = {}
    for key in ("schema_version", "revision", "policy_revision", "result_contract_version"):
        if type(binding.get(key)) is int:
            result[key] = binding[key]
    for key, choices in (("mode", MODES), ("action", ("validate_manifest", "execute_manifest")),
                         ("verification_source", ("administrator_confirmed", "pending_administrator_review")),
                         ("reuse_scope", ("retain-routine-update-claim", "not-applicable"))):
        result[key] = binding.get(key) if binding.get(key) in choices else None
    result["workspace_id"] = workspace.workspace_id if binding.get("workspace_id") == workspace.workspace_id else None
    result["claim_entry"] = claim_entry(workspace) if binding.get("claim_entry") == claim_entry(workspace) else None
    for key in ("policy_fingerprint", "expected_installation_id"):
        value = binding.get(key)
        if type(value) is str and re.fullmatch("[0-9a-f]{64}", value):
            result[key] = value
    value = binding.get("verified_at")
    result["verified_at"] = value if type(value) is str and _timestamp(value) else None
    value = binding.get("template_version")
    result["template_version"] = value if value in ("v1", "legacy-unverified", "deployment-v1") else None
    values = binding.get("protocol_capabilities")
    from grokbot_mcp.identity import CAPABILITIES
    result["protocol_capabilities"] = [item for item in CAPABILITIES if type(values) is list and item in values]
    result["local_fields_omitted"] = ["workspace_root", "registry_path", "claim_interpreter"]
    return result
