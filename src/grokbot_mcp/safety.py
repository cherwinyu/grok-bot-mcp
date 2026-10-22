"""Read-only P0 planning from original registry/task evidence; never grants a send."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import os

from grokbot_mcp.execution_profile import report as profile_report
from grokbot_mcp.identity import digest, compare_identity
from grokbot_mcp.local_safety import LocalError, read_document, json_bytes, safe_path
from grokbot_mcp.registry import load_registry, policy_fingerprint, execution_switch_enabled
from grokbot_mcp.security.paths import is_uuid, normalize_repo_path


@contextmanager
def selected_registry(registry: Path):
    # CLI process scoped, not a persistent environment edit or a thread-safe API.
    old = os.environ.get("GROKBOT_MCP_REGISTRY")
    os.environ["GROKBOT_MCP_REGISTRY"] = str(registry)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("GROKBOT_MCP_REGISTRY", None)
        else:
            os.environ["GROKBOT_MCP_REGISTRY"] = old


def selected(registry: Path, workspace_id: str):
    safe_path(registry, regular=True)
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    safe_path(workspace.root)
    return workspace


def _id(value: str):
    if type(value) is not str or not is_uuid(value):
        raise LocalError("result_identity_invalid")


def task_scope(workspace, task_id: str, execution_id: str | None = None) -> dict:
    from grokbot_mcp.manifest.store import load_manifest
    _id(task_id)
    document, sha = load_manifest(workspace.root, task_id)
    if (document.get("task_id") != task_id or document.get("manifest_task_id") != task_id
            or document.get("workspace_id") != workspace.workspace_id
            or (workspace.git_gate == "strict" and document.get("baseline_commit") != workspace.expected_head)
            or (workspace.git_gate == "advisory"
                and (not isinstance(document.get("baseline_commit"), str)
                     or len(document["baseline_commit"]) != 40))
            or document.get("forbidden_files") != list(workspace.forbidden_files)):
        raise LocalError("result_manifest_mismatch")
    allowed = document.get("allowed_files")
    if (type(allowed) is not list or not all(type(x) is str and x in workspace.allowed_files for x in allowed)
            or len(set(allowed)) != len(allowed)):
        raise LocalError("result_manifest_mismatch")
    if any(normalize_repo_path(name) != name for name in allowed):
        raise LocalError("result_manifest_mismatch")
    command_id = document.get("test_command_id")
    if type(command_id) is not str or workspace.command(command_id) is None:
        raise LocalError("result_manifest_mismatch")
    out = {"task_id": task_id, "manifest_sha256": sha, "allowed_files": allowed,
           "command_id": document.get("test_command_id"), "execution_id": execution_id}
    if execution_id is not None:
        _id(execution_id)
        record, _ = read_document(workspace.root / ".bridge/executions" / (execution_id + ".json"))
        if (record.get("execution_id") != execution_id or record.get("manifest_task_id") != task_id
                or record.get("manifest_sha256") != sha or record.get("action") != "execute_manifest"):
            raise LocalError("result_ledger_identity_mismatch")
        for field, choices in (("execution_state", {"prepared", "pending", "running", "succeeded", "failed", "timeout", "unknown_submission", "cancelled"}),
                               ("submission_state", {"not_sent", "submission_started", "accepted", "rejected", "unknown_submission"})):
            value = record.get(field)
            out[field] = value if type(value) is str and value in choices else "UNKNOWN"
    return out


def preview(registry: Path, workspace_id: str, *, task_id: str | None = None,
            execution_id: str | None = None, local_details: bool = False) -> dict:
    workspace = selected(registry, workspace_id)
    if execution_id is not None and task_id is None:
        raise LocalError("original_task_required")
    scope = task_scope(workspace, task_id, execution_id) if task_id else None
    report = {
        "schema_version": 1, "ok": True, "kind": "permission_preview",
        "workspace_id": workspace_id, "policy_fingerprint": policy_fingerprint(workspace),
        "registry_sha256": digest(registry.read_bytes()), "baseline": workspace.expected_head,
        "environment": profile_report(workspace), "task": scope,
        "write_permission_enabled": workspace.writes_enabled,
        "submission_switch_enabled": execution_switch_enabled(registry),
        "registered_allowed_file_count": len(workspace.allowed_files),
        "selected_allowed_file_count": len(scope["allowed_files"]) if scope else None,
        "receivers": ["local_bridge", "operator_selected_routine", "registered_windows_host"],
        "webhook_body_fields": ["protocol_version", "action", "task_id", "manifest_sha256"] if not execution_id
                               else ["protocol_version", "action", "execution_id", "manifest_task_id"],
        "data_boundary": "Webhook carries IDs; the Bot may read selected source via its separately authorized tools.",
        "source_text_included": False, "credential_values_read": False,
        "remote_permission_verified": False, "send_authorized": False, "webhook_sent": False,
        "ledger_modified": False, "qa_accepted": False,
    }
    if workspace.git_gate == "advisory":
        report["git_gate"] = "advisory"
    if local_details:
        report["local_details"] = {"registry": str(registry), "root": str(workspace.root),
            "registered_allowed_files": list(workspace.allowed_files), "protected_files": list(workspace.forbidden_files)}
    report["plan_sha256"] = digest(json_bytes(report))
    return report


def credential_plan(registry: Path, workspace_id: str, *, check_presence: bool = False) -> dict:
    from grokbot_mcp.migration import unresolved_tasks, state_snapshot
    from grokbot_mcp.routine import routine_readiness, public_binding
    from grokbot_mcp.credentials.backend import status_for
    workspace = selected(registry, workspace_id)
    pending = unresolved_tasks(workspace)
    blockers = []
    if pending:
        blockers.append("binding_change_has_unresolved_tasks")
    if execution_switch_enabled(registry):
        blockers.append("credential_maintenance_switch_on")
    phases = {}
    for phase, target, other in (("validate", workspace.validate_credential_target, workspace.execute_credential_target),
                                 ("execute", workspace.execute_credential_target, workspace.validate_credential_target)):
        ready = routine_readiness(workspace, phase, registry)
        phases[phase] = {"presence": status_for(target, (other,)) if check_presence else "NOT_CHECKED",
            "binding_confirmed": ready["binding_confirmed"], "binding_error": ready["binding_error"],
            "credential_validity": "NOT_VERIFIED", "remote_routine_verified": False}
    result = {"schema_version": 1, "kind": "credential_maintenance_plan", "ok": not blockers,
        "workspace_id": workspace_id, "registry_sha256": digest(registry.read_bytes()),
        "business_state_sha256": digest(json_bytes(state_snapshot(workspace))),
        "pending_count": len(pending), "blocking_reasons": blockers, "phases": phases,
        "presence_checked": check_presence, "credential_values_read": False,
        "credential_written": False, "remote_revoked": False, "webhook_sent": False,
        "ledger_modified": False, "send_authorized": False, "qa_accepted": False,
        "next_steps": ["Quiesce senders and disable the local switch explicitly.",
          "Resolve original pending/unknown tasks without retry or ledger deletion.",
          "Use the provider's official credential lifecycle UI; the bridge has no remote rotation API.",
          "Use the existing hidden-input wizard with --replace-credentials only after explicit review.",
          "Recheck both bindings and claim entry; presence is not key validity or remote readback."]}
    result["plan_sha256"] = digest(json_bytes(result))
    return result


def acceptance(registry: Path, workspace_id: str, task_id: str, execution_id: str,
               *, observed_identity: dict | None = None) -> dict:
    from grokbot_mcp import facade
    from grokbot_mcp.execution.service import assess as assess_execution
    from grokbot_mcp.diagnostics import CATALOG
    workspace = selected(registry, workspace_id)
    task_scope(workspace, task_id, execution_id)
    with selected_registry(registry):
        # The original observers recheck evidence without persisting or contacting a provider.
        result = facade.get_status(workspace_id, task_id=task_id, execution_id=execution_id,
                                   observe_only=True, verify_current=True)
        expected = facade.runtime_identity(workspace_id)
        frozen_validation = assess_execution(workspace, task_id, check_workspace=False)
    phases = {}
    for name in ("validation", "execution"):
        value = result.get(name, {})
        phases[name] = {}
        for key in ("status", "historical_status"):
            actual = value.get(key)
            states = {"pending", "running", "succeeded", "failed", "unknown", "timeout", "cancelled", "unknown_submission"}
            phases[name][key] = actual if type(actual) is str and actual in states else "rejected"
        for key in ("current_integrity", "ledger_updated"):
            phases[name][key] = value.get(key) if type(value.get(key)) is bool else None
        scope = value.get("verification_scope")
        phases[name]["verification_scope"] = scope if scope in ("historical", "current", "none") else "none"
        error = value.get("error")
        phases[name]["error"] = error if error is None or type(error) is str and error in CATALOG else "unclassified_refusal"
    matched = compare_identity(expected, observed_identity) if observed_identity is not None else None
    return {"schema_version": 1, "kind": "acceptance_observation", "ok": True, "phases": phases,
            "stored_evidence_success": (frozen_validation.get("ok") is True and phases["validation"]["status"] == "succeeded"
                                         and phases["execution"]["status"] == "succeeded" and phases["execution"]["current_integrity"] is True),
            "frozen_validation_evidence_valid": frozen_validation.get("ok") is True,
            "validation_snapshot_note": "Pre-execution validation snapshot can legitimately differ after authorized edits; frozen proof is checked separately.",
            "source": "original local evidence; not authenticated remote execution or test instrumentation",
            "supplied_client_identity_comparison": matched, "client_reconnection_confirmed": False,
            "remote_routine_verified": False, "tests_independently_measured": False,
            "credential_values_read": False, "send_authorized": False, "webhook_sent": False,
            "ledger_modified": False, "qa_accepted": False}


def shareable(report: dict) -> dict:
    """Reconstruct fixed enums/counts only. Not low-entropy path hashing as anonymity."""
    safe = {"schema_version": 1, "scope": "shareable P0 summary", "qa_accepted": False,
            "send_authorized": False, "remote_routine_verified": False, "client_reconnection_confirmed": False}
    for name in ("ok", "presence_checked", "credential_values_read", "credential_written", "webhook_sent",
                 "ledger_modified", "stored_evidence_success", "remote_revoked"):
        safe[name] = report.get(name) if type(report.get(name)) is bool else None
    for name in ("registered_allowed_file_count", "selected_allowed_file_count", "pending_count"):
        value = report.get(name)
        safe[name] = value if type(value) is int and value >= 0 else None
    return safe
