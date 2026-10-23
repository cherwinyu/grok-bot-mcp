"""Opt-in deterministic validation; no credentials, switch or transport access."""
from __future__ import annotations

from grokbot_mcp.local_safety import atomic_replace, current_hash, json_bytes, safe_path
from grokbot_mcp.manifest.store import ManifestError, load_manifest
from grokbot_mcp.registry import policy_fingerprint
from grokbot_mcp.result_contract import validate_result
from grokbot_mcp.security.gitcheck import git_error, git_snapshot
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.storage import sha256_bytes, utc_now


def validate_if_absent(workspace, task_id):
    from grokbot_mcp.validation.service import _ledger
    # Existing submissions (including uncertain ones) and orphan proofs are
    # handled by the original assessment; never fabricate their replacement.
    store = _ledger(workspace)
    if (not store.path_for(task_id).exists()
            and not (workspace.root / ".bridge/results_v2" / (task_id + ".json")).exists()):
        return submit_local_validation(workspace, task_id)
    return None


def submit_local_validation(workspace, task_id):
    from grokbot_mcp.execution.service import assess_manifest
    from grokbot_mcp.validation.service import BLOCKING, _ledger, _result, get_validation_status

    def response(**fields):
        return _result(task_id, validated_locally=True, **fields)

    if not is_uuid(task_id):
        return response(error="invalid_task_id")
    if not workspace.local_validation or workspace.git_gate != "advisory":
        return response(error="local_validation_invalid")
    if not workspace.writes_enabled:
        return response(error="workspace_writes_disabled")
    store = _ledger(workspace)
    relative = ".bridge/results_v2/" + task_id + ".json"
    target = workspace.root / relative
    with store.locked():
        if store.path_for(task_id).exists():
            return response(error="duplicate_task_id")
        safe_path(target)
        if target.exists():
            return response(error="result_already_exists")
        for record in store.list_records():
            if record.get("task_state") in BLOCKING:
                return response(error="incomplete_task_exists", blocking_task_id=record.get("task_id"))
        try:
            document, digest = load_manifest(workspace.root, task_id)
        except ManifestError as exc:
            return response(error=exc.code)
        # The first digest replaces the webhook's expected hash. Assessment
        # reloads the manifest, so concurrent replacement cannot become success.
        report = assess_manifest(workspace, task_id)
        problem = report.get("error")
        if report.get("manifest_sha256", digest) != digest:
            problem = "hash_mismatch"
        if not problem:
            git = git_snapshot(workspace.root, workspace.allowed_files,
                               workspace.expected_head, workspace.forbidden_files)
            problem = git_error(git, workspace.git_gate)
        try:
            if load_manifest(workspace.root, task_id)[1] != digest:
                problem = "hash_mismatch"
        except ManifestError as exc:
            problem = exc.code
        proof = {
            "protocol_version": 1, "action": "validate_manifest", "task_id": task_id,
            "manifest_sha256": digest, "workspace_id": workspace.workspace_id,
            "machine": workspace.expected_machine, "allowed_files": document.get("allowed_files"),
            "status": "failed" if problem else "succeeded", "validation_passed": not bool(problem),
            "validation_errors": [problem] if problem else [], "code_modified": False,
            "error": problem, "validator": "bridge_local",
            "policy_fingerprint": policy_fingerprint(workspace),
        }
        validate_result(proof, "validate")
        store.create(task_id, {
            "schema_version": 1, "task_id": task_id, "action": "validate_manifest",
            "manifest_sha256": digest, "created_at": utc_now(), "request_attempted_at": None,
            "http_status": None, "accepted": True, "task_state": "accepted",
            "submission_state": "accepted", "submission_final": True,
            "execution_timeout": document.get("execution_timeout", 600), "result_path": relative,
            "webhook_attempted": False, "webhook_sent": False, "validator": "bridge_local",
            "policy_fingerprint": policy_fingerprint(workspace),
            **({"scope_expansion": document["scope_expansion"]} if "scope_expansion" in document else {}),
        })
        raw = json_bytes(proof)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Same compare-and-replace publisher used by result_cli, under the
        # submission lock, with no existing target permitted.
        atomic_replace(target, raw, expected=None)
        if current_hash(target) != sha256_bytes(raw):
            raise ValueError("result_readback_mismatch")
    status = get_validation_status(workspace, task_id)
    return response(accepted=True, task_state=status["status"], manifest_sha256=digest,
                    result_path=relative, error=status.get("error"),
                    reported_success=status.get("reported_success") is True)
