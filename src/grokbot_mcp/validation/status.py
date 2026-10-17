"""Validation observation: intermediate files never masquerade as final proofs."""
from __future__ import annotations

from grokbot_mcp.validation.presentation import public_validation_fields

from grokbot_mcp.manifest.store import load_manifest, ManifestError
from grokbot_mcp.observation import LedgerView, deadline, observation_lock, query_metadata, read_result
from grokbot_mcp.security.baseline import capture_snapshot, same_snapshot
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.storage import utc_now


def observe_validation(workspace, task_id: str, *, observe_only=False, verify_current=False) -> dict:
    from grokbot_mcp.validation.service import _ledger, _interpret
    result = {"task_id": task_id, "action": "validate_manifest", "status": "unknown", "error": None,
              "reported_success": False, "qa_accepted": False, "webhook_sent": False,
              "validation_passed": None, "code_modified": None, "manifest_sha256": None}
    if not is_uuid(task_id):
        return {**result, "error": "invalid_task_id"}
    store = _ledger(workspace)
    with observation_lock(store, observe_only):
        try:
            record = store.read(task_id)
        except (OSError, ValueError):
            return {**result, "error": "missing_task"}
        view = LedgerView(store, record, not observe_only)
        if record.get("task_state") == "submission_started":
            view.update(task_id, task_state="unknown_submission", submission_state="unknown_submission",
                        error="unknown_submission", submission_final=True)
            record = view.read(task_id)
        state = record.get("task_state")
        result.update(task_state=state, http_status=record.get("http_status"), expected_sha256=record.get("manifest_sha256"))
        if state == "unknown_submission":
            result.update(status=state, error="unknown_submission")
            return query_metadata(result, view, observe_only)
        try:
            parsed, digest, observation = read_result(workspace.root / ".bridge/results_v2" / (task_id + ".json"))
            read_error = None
        except ValueError as exc:
            parsed, digest, observation, read_error = None, None, "rejected", str(exc)
        result["result_observation"] = observation
        if record.get("terminal"):
            result.update(record.get("verified_summary", {}))
            result.update(public_validation_fields(result, result.get("result_diagnosis"),
                          manifest_sha256=record.get("manifest_sha256"), workspace_id=workspace.workspace_id))
            result.update(status=state, task_state=state, verified_at=record.get("verified_at"),
                          historical_status=record.get("historical_status"), verification_scope="historical",
                          current_integrity=None, integrity_error=None, reported_success=state == "succeeded")
            if digest != record.get("result_sha256"):
                result.update(current_integrity=False, integrity_error=read_error or "result_changed")
            elif verify_current:
                result["verification_scope"] = "current"
                try:
                    _, actual = load_manifest(workspace.root, task_id)
                    if actual != record.get("manifest_sha256"):
                        raise ValueError("hash_mismatch")
                    current = capture_snapshot(workspace.root, list(workspace.allowed_files) + list(workspace.forbidden_files))
                    if not same_snapshot(record.get("verified_snapshot") or {}, current):
                        raise ValueError("workspace_changed_since_verification")
                    result["current_integrity"] = True
                except (ManifestError, OSError, ValueError) as exc:
                    result.update(current_integrity=False, integrity_error=str(exc))
            return query_metadata(result, view, observe_only)
        if parsed is not None:
            scratch = LedgerView(store, record, False)
            result = _interpret(scratch, workspace, record, result, parsed)
        elif read_error:
            result.update(status="failed", error=read_error)
        else:
            result["status"] = "running" if state == "running" else ("pending" if state == "accepted" else state)
        status = result.get("status")
        if (parsed is None and not read_error) or status in {"pending", "running"}:
            if state == "running" and status == "pending":
                status = "running"
            if deadline(record)["deadline_exceeded"]:
                status = "timeout"
            view.update(task_id, task_state=status, error="result_timeout" if status == "timeout" else None)
            result.update(status=status, task_state=status, error=view.record.get("error"))
        else:
            verified_at = utc_now()
            summary = {key: result.get(key) for key in ("validation_passed", "validation_errors", "code_modified",
                        "manifest_sha256", "workspace_id", "allowed_files", "error", "result_diagnosis")}
            try:
                snapshot = capture_snapshot(workspace.root, list(workspace.allowed_files) + list(workspace.forbidden_files))
            except (OSError, ValueError):
                snapshot = None
                result.update(status="failed", error="snapshot_unavailable", reported_success=False)
                status = "failed"
                summary["error"] = "snapshot_unavailable"
            view.update(task_id, task_state=status, terminal=True, historical_status=status,
                        result_sha256=digest, verified_at=verified_at, verified_summary=summary,
                        verified_snapshot=snapshot, error=result.get("error"),
                        late_result=deadline(record)["deadline_exceeded"])
            result.update(task_state=status, verified_at=verified_at, historical_status=status,
                          verification_scope="current", current_integrity=status == "succeeded")
        return query_metadata(result, view, observe_only)
