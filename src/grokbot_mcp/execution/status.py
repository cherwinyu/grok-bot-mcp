"""Execution observation and optional reconciliation, separate from dispatch."""
from __future__ import annotations

from grokbot_mcp.execution.evidence import verify_evidence
from grokbot_mcp.observation import LedgerView, deadline, observation_lock, query_metadata, read_result
from grokbot_mcp.security.baseline import same_snapshot
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.result_contract import diagnosis, result_identity
from grokbot_mcp.strict_json import LedgerTooLarge


def observe_execution(workspace, execution_id: str, *, observe_only=False, verify_current=False) -> dict:
    from grokbot_mcp.execution.service import _base, _ledger, _from_record, _fail, _interpret, assess
    if not is_uuid(execution_id):
        return _base(error="invalid_execution_id", status="unknown")
    store = _ledger(workspace)
    with observation_lock(store, observe_only):
        try:
            record = store.read(execution_id)
        except LedgerTooLarge:
            raise
        except (OSError, ValueError):
            return _base(execution_id=execution_id, error="missing_or_unreadable_execution", status="unknown")
        view = LedgerView(store, record, not observe_only)
        if record.get("submission_state") == "submission_started" and record.get("execution_claim_state") != "claimed":
            view.update(execution_id, submission_state="unknown_submission", execution_state="unknown_submission",
                        submission_final=True, error="unknown_submission")
            record = view.read(execution_id)
        result = _from_record(record, error=record.get("error"))
        result.update(status=record.get("execution_state"), **record.get("verified_summary", {}))
        path = workspace.root / ".bridge/results_execute" / (execution_id + ".json")
        try:
            parsed, digest, observation = read_result(path)
            read_error = None
        except ValueError as exc:
            parsed, digest, observation, read_error = None, None, "rejected", str(exc)
        result["result_observation"] = observation
        if record.get("execution_state") == "cancelled":
            result.update(historical_status="cancelled", verification_scope="historical",
                          reported_success=False, cancelled_at=record.get("cancelled_at"),
                          cancellation_reason=record.get("cancellation_reason"), current_integrity=None)
            if observation != "missing":
                result.update(current_integrity=False, integrity_error="result_after_cancellation")
            return query_metadata(result, view, observe_only)
        if record.get("terminal") or (record.get("result_sha256") and record.get("execution_state") not in {"pending", "running", "timeout", "unknown_submission"}):
            # Immutable historical decision. A newer file can never roll it back.
            result.update(reported_success=record.get("execution_state") == "succeeded",
                          historical_status=record.get("historical_status", record.get("execution_state")),
                          verified_at=record.get("verified_at"), verification_scope="historical",
                          current_integrity=None, integrity_error=None)
            if digest != record.get("result_sha256"):
                result.update(current_integrity=False, integrity_error=read_error or "result_changed")
            elif verify_current:
                result["verification_scope"] = "current"
                try:
                    _, current = verify_evidence(workspace, record, assess(workspace, str(record["manifest_task_id"]), check_workspace=False), require_claim=True)
                    if not isinstance(record.get("verified_snapshot"), dict):
                        raise ValueError("historical_snapshot_unavailable")
                    if not same_snapshot(record["verified_snapshot"], current):
                        raise ValueError("workspace_changed_since_verification")
                    result["current_integrity"] = True
                except (OSError, ValueError) as exc:
                    result.update(current_integrity=False, integrity_error=str(exc))
            return query_metadata(result, view, observe_only)
        if read_error:
            result = _fail(view, execution_id, result, read_error, digest or "")
        elif parsed is not None:
            result["result_diagnosis"] = diagnosis(parsed, "execute", expected=result_identity(workspace, "execute", record),
                                                   require_test_output=workspace.require_test_output)
            # All intermediate records must carry the same immutable identity.
            identity = (parsed.get("protocol_version") in (1, "1") and parsed.get("execution_id") == execution_id
                        and parsed.get("manifest_task_id") == record.get("manifest_task_id")
                        and parsed.get("action") == "execute_manifest"
                        and parsed.get("manifest_sha256") == record.get("manifest_sha256")
                        and parsed.get("machine") == workspace.expected_machine)
            if not identity:
                result = _fail(view, execution_id, result, "identity_mismatch", digest)
            elif parsed.get("cursor_cloud_agent_used") is True:
                result = _fail(view, execution_id, result, "external_agent_rejected", digest)
            elif result["result_diagnosis"]["category"] in {"format_error", "identity_error"}:
                issue = result["result_diagnosis"]
                code = "identity_mismatch" if issue["category"] == "identity_error" else "test_failed"
                result = _fail(view, execution_id, result, code, digest)
            elif parsed.get("status") not in {"pending", "running"}:
                result = _interpret(view, workspace, record, result, parsed, digest)
            else:
                try:
                    verify_evidence(workspace, record, assess(workspace, str(record["manifest_task_id"]), check_workspace=False),
                                    require_claim=parsed["status"] == "running" or record.get("execution_claim_state") == "claimed")
                    result["status"] = "running" if record.get("execution_state") == "running" else parsed["status"]
                except (OSError, ValueError) as exc:
                    result = _fail(view, execution_id, result, str(exc), digest)
        if not view.record.get("terminal"):
            uncertain = record.get("submission_state") == "unknown_submission"
            if uncertain:
                state = "unknown_submission"
            elif deadline(record)["deadline_exceeded"]:
                state = "timeout"
            elif record.get("submission_state") == "accepted":
                state = "running" if record.get("execution_claim_state") == "claimed" or result.get("status") == "running" else "pending"
            else:
                state = str(record.get("execution_state"))
            error = "result_timeout" if state == "timeout" else ("unknown_submission" if uncertain else None)
            view.update(execution_id, execution_state=state, error=error)
            result.update(status=state, execution_state=state, error=error, reported_success=False)
        return query_metadata(result, view, observe_only)
