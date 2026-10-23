"""One validation submission. HTTP 200 means accepted, not passed."""

from __future__ import annotations

from grokbot_mcp.validation.presentation import public_validation_fields

from pathlib import Path
from typing import Callable

from grokbot_mcp.credentials.windows import CredentialError
from grokbot_mcp.errors.http_errors import diagnosis_for_outcome
from grokbot_mcp.manifest.store import ManifestError, load_manifest
from grokbot_mcp.registry import Workspace
from grokbot_mcp.routine import routine_readiness
from grokbot_mcp.result_contract import diagnosis, result_identity
from grokbot_mcp.security.gitcheck import git_error, git_snapshot
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.storage import JsonLedger, read_json, utc_now
from grokbot_mcp.transport.webhook import HttpOutcome, build_payload, post_once

StatusFn = Callable[[], str]
LoadFn = Callable[[], tuple[str, str]]
Transport = Callable[[str, str, dict[str, object], float], HttpOutcome]
BLOCKING = {"submission_started", "unknown_submission", "accepted", "pending", "running", "incomplete", "timeout"}


def _result(task_id: str | None = None, **fields: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "task_id": task_id,
        "accepted": False,
        "http_status": None,
        "task_state": "rejected",
        "manifest_sha256": None,
        "result_path": None,
        "error": None,
        "webhook_attempted": False,
        "webhook_sent": False,
        "reported_success": False,
        "qa_accepted": False,
        "execution_enabled": False,
    }
    payload.update(fields)
    payload["sent_this_call"] = fields.get("webhook_sent") is True
    payload["attempted_this_call"] = fields.get("webhook_attempted") is True
    return payload


def _ledger(workspace: Workspace) -> JsonLedger:
    return JsonLedger(workspace.root / ".bridge" / "tasks_v2")


def _recover(store: JsonLedger) -> None:
    for record in store.list_records():
        if record.get("task_state") == "submission_started":
            store.update(
                str(record["task_id"]),
                task_state="unknown_submission",
                submission_state="unknown_submission",
                submission_final=True,
                error="unknown_submission",
            )


def submit_validation(
    workspace: Workspace,
    task_id: str,
    *,
    status_fn: StatusFn,
    load_fn: LoadFn,
    transport: Transport | None = None,
    webhook_enabled: bool = False,
) -> dict[str, object]:
    if not is_uuid(task_id):
        return _result(error="invalid_task_id")
    if not webhook_enabled:
        return _result(task_id, error="validate_manifest_not_enabled")
    if not workspace.writes_enabled:
        return _result(task_id, error="workspace_writes_disabled")
    routine = routine_readiness(workspace, "validate")
    if not routine["submit_ready"]:
        code = str(routine["error"])
        return _result(task_id, error=code, operator_message=_routine_message(code))
    credentials = status_fn()
    if credentials != "present":
        return _result(task_id, error="credentials_" + credentials)
    try:
        document, digest = load_manifest(workspace.root, task_id)
    except ManifestError as exc:
        return _result(task_id, error=exc.code)
    if document.get("workspace_id") != workspace.workspace_id or document.get("lifecycle") != "prepared":
        return _result(task_id, error="not_prepared", manifest_sha256=digest)
    git = git_snapshot(
        workspace.root, workspace.allowed_files, workspace.expected_head,
        workspace.forbidden_files if workspace.git_gate == "advisory" else (),
    )
    problem = git_error(git, workspace.git_gate)
    if problem:
        return _result(
            task_id, error=problem, manifest_sha256=digest,
            **({"protected_or_forbidden_changes": git["protected_or_forbidden_changes"]}
               if git.get("protected_or_forbidden_changes") else {}),
        )
    result_rel = ".bridge/results_v2/" + task_id + ".json"
    result_file = workspace.root / ".bridge" / "results_v2" / f"{task_id}.json"
    if result_file.exists():
        return _result(task_id, error="result_already_exists", manifest_sha256=digest)
    store = _ledger(workspace)
    with store.locked():
        # Recheck after locking: an administrator may have archived the task
        # between the initial read and admission.
        try:
            load_manifest(workspace.root, task_id)
        except ManifestError as exc:
            return _result(task_id, error=exc.code)
        _recover(store)
        for record in store.list_records():
            if record.get("task_state") in BLOCKING:
                return _result(error="incomplete_task_exists", blocking_task_id=record.get("task_id"))
        if store.path_for(task_id).exists():
            return _result(task_id, error="duplicate_task_id", manifest_sha256=digest)
        store.create(
            task_id,
            {
                "schema_version": 1,
                "task_id": task_id,
                "action": "validate_manifest",
                "manifest_sha256": digest,
                "created_at": utc_now(),
                "request_attempted_at": None,
                "http_status": None,
                "accepted": None,
                "task_state": "created",
                "submission_state": "not_sent",
                "execution_timeout": document.get("execution_timeout", 600),
                "result_path": result_rel,
                "submission_final": False,
                **({"scope_expansion": document["scope_expansion"]} if "scope_expansion" in document else {}),
            },
        )
        try:
            url, key = load_fn()
            payload = build_payload("validate_manifest", task_id=task_id, manifest_sha256=digest)
        except (CredentialError, ValueError):
            store.update(task_id, task_state="rejected", submission_final=True, error="credential_unreadable")
            return _result(task_id, task_state="rejected", error="credential_unreadable", manifest_sha256=digest)
        store.update(
            task_id,
            task_state="submission_started",
            submission_state="submission_started",
            request_attempted_at=utc_now(),
            submission_final=True,
            webhook_attempted=True,
        )
        sender = transport or (lambda item_url, item_key, item_payload, timeout: post_once(item_url, item_key, item_payload, timeout, enabled=webhook_enabled))
        try:
            outcome = sender(url, key, payload, 60.0)
        except Exception:
            outcome = HttpOutcome(kind="unknown", status=None)
        finally:
            url = ""
            key = ""
        return _record_http(store, task_id, digest, result_rel, outcome)


def get_validation_status(workspace: Workspace, task_id: str, *,
                          observe_only: bool = False, verify_current: bool = False) -> dict[str, object]:
    from grokbot_mcp.validation.status import observe_validation
    return observe_validation(workspace, task_id, observe_only=observe_only, verify_current=verify_current)


def _interpret(store: JsonLedger, workspace: Workspace, record: dict[str, object], result: dict[str, object], parsed: dict[str, object]) -> dict[str, object]:
    task_id = str(record["task_id"])
    result["result_diagnosis"] = diagnosis(parsed, "validate", expected=result_identity(workspace, "validate", record))
    result.update(public_validation_fields(parsed, result["result_diagnosis"],
                  manifest_sha256=record.get("manifest_sha256"), workspace_id=workspace.workspace_id))
    identity_ok = (
        parsed.get("protocol_version") in (1, "1")
        and parsed.get("task_id") == task_id
        and parsed.get("action") == "validate_manifest"
        and parsed.get("manifest_sha256") == record.get("manifest_sha256")
        and parsed.get("machine") == workspace.expected_machine
        and parsed.get("workspace_id") == workspace.workspace_id
    )
    if not identity_ok:
        store.update(task_id, task_state="identity_mismatch")
        result["status"] = "identity_mismatch"
        result["error"] = "identity_mismatch"
        return result
    try:
        document, manifest_digest = load_manifest(workspace.root, task_id)
    except ManifestError as exc:
        result.update(status="hash_mismatch", error=exc.code)
        return result
    baseline_ok = (document.get("baseline_commit") == workspace.expected_head
                   if getattr(workspace, "git_gate", "strict") == "strict"
                   else isinstance(document.get("baseline_commit"), str) and len(document["baseline_commit"]) == 40)
    if manifest_digest != record.get("manifest_sha256") or not baseline_ok:
        result.update(status="hash_mismatch", error="hash_mismatch")
        return result
    if result["result_diagnosis"]["category"] in {"format_error", "identity_error"}:
        issue = result["result_diagnosis"]
        code = "identity_mismatch" if issue["category"] == "identity_error" else "validation_rejected"
        result.update(status="identity_mismatch" if code == "identity_mismatch" else "failed", error=code)
        return result
    if parsed.get("status") in {"pending", "running"}:
        if "allowed_files" in parsed and parsed["allowed_files"] != document.get("allowed_files"):
            result.update(status="failed", error="validation_rejected")
        else:
            result.update(status=parsed["status"], error=None, validation_passed=None)
        return result
    if parsed.get("status") == "failed" or parsed.get("validation_passed") is not True or parsed.get("code_modified") is not False:
        store.update(task_id, task_state="failed")
        result["status"] = "failed"
        result["error"] = "validation_rejected"
        return result
    allowed = parsed.get("allowed_files")
    if allowed != document.get("allowed_files"):
        store.update(task_id, task_state="failed")
        result["status"] = "failed"
        result["error"] = "validation_rejected"
        return result
    errors = parsed.get("validation_errors")
    if not isinstance(errors, list) or errors:
        store.update(task_id, task_state="failed")
        result["status"] = "failed"
        result["error"] = "validation_errors_present"
        return result
    if parsed.get("status") != "succeeded":
        result["status"] = "failed"
        result["error"] = "invalid_result_status"
        return result
    store.update(task_id, task_state="succeeded", error=None)
    result["status"] = "succeeded"
    result["task_state"] = "succeeded"
    result["reported_success"] = True
    result["error"] = None
    return result


def record_allowed(workspace: Workspace, task_id: str) -> list[str]:
    document, _digest = load_manifest(workspace.root, task_id)
    value = document.get("allowed_files")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _record_http(store: JsonLedger, task_id: str, digest: str, result_path: str, outcome: HttpOutcome) -> dict[str, object]:
    diagnosis = diagnosis_for_outcome(outcome.kind, outcome.status, outcome.diagnosis)
    if outcome.kind == "status" and outcome.status == 200:
        store.update(task_id, task_state="accepted", submission_state="accepted", http_status=200, accepted=True, submission_final=True, error=None, **diagnosis)
        return _result(task_id, accepted=True, http_status=200, task_state="accepted", manifest_sha256=digest, result_path=result_path, webhook_attempted=True, webhook_sent=True)
    if outcome.kind == "disabled":
        store.update(task_id, task_state="rejected", submission_final=True, error="webhook_disabled")
        return _result(task_id, error="webhook_disabled", manifest_sha256=digest, webhook_attempted=False)
    if outcome.kind == "status" and outcome.status is not None:
        store.update(task_id, task_state="rejected", submission_state="rejected", http_status=outcome.status, accepted=False, submission_final=True, error="http_rejected", **diagnosis)
        return _result(task_id, http_status=outcome.status, task_state="rejected", error="http_rejected", manifest_sha256=digest, webhook_attempted=True, webhook_sent=True, **diagnosis)
    error = "http_timeout" if outcome.kind == "timeout" else "unknown_submission"
    store.update(task_id, task_state="unknown_submission", submission_state="unknown_submission", accepted=False, submission_final=True, error=error, **diagnosis)
    return _result(task_id, task_state="unknown_submission", error=error, manifest_sha256=digest, webhook_attempted=True, webhook_sent=True, **diagnosis)


def _routine_message(code: str) -> str:
    if code == "routine_paused":
        return "The confirmed routine status is paused. No webhook was sent."
    return "There is no user-confirmed routine status. No webhook was sent."
