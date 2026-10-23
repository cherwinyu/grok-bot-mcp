"""Execution authorization. A missing switch returns before any network call."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from grokbot_mcp.credentials.windows import CredentialError
from grokbot_mcp.errors.http_errors import diagnosis_for_outcome
from grokbot_mcp.manifest.store import ManifestError, load_manifest
from grokbot_mcp.security.prefixes import manifest_scope_valid, resolve_task_files, policy_allows, manifest_workspace, active_prefix_fields
from grokbot_mcp.registry import Workspace, policy_fingerprint
from grokbot_mcp.routine import routine_readiness
from grokbot_mcp.execution.evidence import verify_evidence
from grokbot_mcp.validation.evidence import verify_validation_decision
from grokbot_mcp.observation import deadline
from grokbot_mcp.result_contract import diagnosis, result_identity, validate_result, ContractError
from grokbot_mcp.security.baseline import watched_paths, prefix_snapshot_options, capture_snapshot, execution_delta, load_snapshot, replace_snapshot, same_snapshot, write_snapshot
from grokbot_mcp.security.gitcheck import git_error, git_snapshot, workspace_git_rejection
from grokbot_mcp.security.paths import is_reparse_point, is_uuid, normalize_repo_path, normalize_policy_path, PathRejected
from grokbot_mcp.strict_json import LedgerTooLarge, check_size
import unicodedata
from grokbot_mcp.security.targets import TargetRejected, resolve_exact_files
from grokbot_mcp.storage import JsonLedger, read_json, utc_now, sha256_bytes
from grokbot_mcp.transport.webhook import HttpOutcome, build_payload, post_once

StatusFn = Callable[[], str]
LoadFn = Callable[[], tuple[str, str]]
Transport = Callable[[str, str, dict[str, object], float], HttpOutcome]
IN_FLIGHT_EXECUTION = {"prepared", "pending", "running", "timeout"}
IN_FLIGHT_SUBMISSION = {"submission_started", "unknown_submission"}
TERMINAL = {
    "cancelled",
    "succeeded",
    "failed",
    "timeout",
    "identity_mismatch",
    "hash_mismatch",
    "scope_violation",
    "unknown_submission",
    "result_inconsistent",
    "external_agent_rejected",
}


def _base(**fields: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "execution_id": None,
        "manifest_task_id": None,
        "manifest_sha256": None,
        "validation_result_ref": None,
        "execution_result_path": None,
        "action": "execute_manifest",
        "approval_state": None,
        "submission_state": None,
        "execution_state": None,
        "qa_state": "not_reviewed",
        "accepted": False,
        "http_status": None,
        "webhook_attempted": False,
        "webhook_sent": False,
        "reported_success": False,
        "qa_accepted": False,
        "execute_enabled": False,
        "error": None,
    }
    payload.update(fields)
    return payload


def _protected_fields(report: dict[str, object]) -> dict[str, object]:
    git = report.get("git")
    changes = git.get("protected_or_forbidden_changes") if isinstance(git, dict) else None
    return {"protected_or_forbidden_changes": changes} if changes else {}


def _ledger(workspace: Workspace) -> JsonLedger:
    return JsonLedger(workspace.root / ".bridge" / "executions")


def _bridge(workspace: Workspace) -> Path:
    return workspace.root / ".bridge"


def _recover(store: JsonLedger) -> None:
    for record in store.list_records():
        if record.get("submission_state") == "submission_started" and record.get("execution_claim_state") != "claimed":
            store.update(
                str(record["execution_id"]),
                submission_state="unknown_submission",
                execution_state="unknown_submission",
                submission_final=True,
                error="unknown_submission",
            )


def _in_flight(record: dict[str, object]) -> bool:
    if record.get("terminal"):
        return False
    if record.get("submission_state") in IN_FLIGHT_SUBMISSION:
        return True
    return record.get("execution_state") in IN_FLIGHT_EXECUTION


def stale_execution_records(workspace: Workspace) -> list[str]:
    """Read-only hint: no lock files or ledger transitions."""
    return sorted(str(r["execution_id"]) for r in _ledger(workspace).list_records()
                  if _in_flight(r) and is_uuid(r.get("execution_id"))
                  and (_bridge(workspace) / "results_execute" / (r["execution_id"] + ".json")).exists())


def _sibling_has(execution_id: str, sibling_roots: list[Path]) -> bool:
    for root in sibling_roots:
        if (root / ".bridge" / "executions" / f"{execution_id}.json").exists():
            return True
    return False


def assess_manifest(workspace: Workspace, task_id: str) -> dict[str, object]:
    """Shared deterministic policy checks, independent of a validation proof."""
    rejection = workspace_git_rejection(workspace.root)
    if rejection:
        return {"ok": False, "error": rejection}
    try:
        document, digest = load_manifest(workspace.root, task_id)
    except ManifestError as exc:
        return {"ok": False, "error": exc.code}
    if document.get("workspace_id") != workspace.workspace_id or document.get("lifecycle") != "prepared":
        return {"ok": False, "error": "not_prepared", "manifest_sha256": digest, "document": document}
    if list(document.get("forbidden_files") or []) != list(workspace.forbidden_files):
        return {"ok": False, "error": "forbidden_mismatch", "manifest_sha256": digest, "document": document}
    try:
        manifest_workspace(workspace, document)
    except ManifestError as exc:
        return {"ok": False, "error": exc.code, "manifest_sha256": digest, "document": document}
    allowed = document.get("allowed_files")
    command = workspace.command(str(document.get("test_command_id")))
    if (document.get("task_id") != task_id or document.get("manifest_task_id") != task_id
            or (workspace.git_gate == "strict" and document.get("baseline_commit") != workspace.expected_head)
            or (workspace.git_gate == "advisory"
                and (not isinstance(document.get("baseline_commit"), str)
                     or len(document["baseline_commit"]) != 40))
            or not isinstance(allowed, list) or not all(isinstance(x, str) for x in allowed)
            or not manifest_scope_valid(workspace, document)
            or len(set(allowed)) != len(allowed) or command is None
            or document.get("task_type") not in command.task_types):
        return {"ok": False, "error": "manifest_policy_mismatch", "manifest_sha256": digest}
    return {"ok": True, "error": None, "manifest_sha256": digest, "document": document}


def assess(workspace: Workspace, task_id: str, *, check_workspace: bool = True) -> dict[str, object]:
    report = assess_manifest(workspace, task_id)
    from grokbot_mcp.validation.evidence import local_validation_error
    proof_path = _bridge(workspace) / "results_v2" / f"{task_id}.json"
    if not report["ok"]:
        # A changed forbidden/ceiling policy can fail manifest admission before
        # proof checking. Still identify the revoked local proof consistently.
        # Preserve the established error precedence for all legacy proofs.
        try:
            rejected_proof, _ = read_json(proof_path)
        except (OSError, ValueError):
            pass
        else:
            problem = local_validation_error(workspace, rejected_proof)
            if problem:
                return {**report, "error": problem}
        return report
    document, digest = report["document"], report["manifest_sha256"]
    if not proof_path.is_file():
        return {"ok": False, "error": "validation_missing", "manifest_sha256": digest, "document": document}
    try:
        proof, proof_sha = read_json(proof_path)
    except LedgerTooLarge as exc:
        return {"ok": False, "error": exc.code, "manifest_sha256": digest, "document": document}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": "validation_unreadable", "manifest_sha256": digest, "document": document}
    problem = local_validation_error(workspace, proof)
    if problem:
        return {"ok": False, "error": problem, "manifest_sha256": digest, "document": document}
    try:
        validate_result(proof, "validate", expected={"task_id": task_id, "workspace_id": workspace.workspace_id,
                        "machine": workspace.expected_machine, "manifest_sha256": digest})
    except ContractError:
        return {"ok": False, "error": "validation_rejected", "manifest_sha256": digest, "document": document}
    allowed = document.get("allowed_files")
    if not isinstance(allowed, list):
        allowed = []
    checks = (
        proof.get("protocol_version") in (1, "1")
        and proof.get("action") == "validate_manifest"
        and proof.get("task_id") == task_id
        and proof.get("manifest_sha256") == digest
        and proof.get("status") == "succeeded"
        and proof.get("validation_passed") is True
        and proof.get("code_modified") is False
        and proof.get("machine") == workspace.expected_machine
        and proof.get("workspace_id") == workspace.workspace_id
        and proof.get("allowed_files") == allowed
        and proof.get("validation_errors") == []
    )
    if not checks:
        return {"ok": False, "error": "validation_rejected", "manifest_sha256": digest, "document": document}
    try:
        validation_verified_at = verify_validation_decision(workspace, task_id, digest, proof_sha, proof)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "manifest_sha256": digest, "document": document}
    git = git_snapshot(
        workspace.root, workspace.allowed_files, workspace.expected_head,
        workspace.forbidden_files if workspace.git_gate == "advisory" else (),
    )
    problem = git_error(git, workspace.git_gate)
    if problem and (check_workspace or problem in {"git_status_failed", "git_head_mismatch"}):
        return {"ok": False, "error": problem, "manifest_sha256": digest, "document": document, "git": git}
    return {
        "ok": True,
        "error": None,
        "manifest_sha256": digest,
        "validation_sha256": proof_sha,
        "validation_verified_at": validation_verified_at,
        "document": document,
        "git": git,
    }


def prepare_execution(workspace: Workspace, task_id: str, sibling_roots: list[Path]) -> dict[str, object]:
    if not is_uuid(task_id):
        return _base(error="invalid_task_id")
    if not workspace.writes_enabled:
        return _base(manifest_task_id=task_id, error="workspace_writes_disabled")
    if getattr(workspace, "local_validation", False):
        from grokbot_mcp.validation.local import validate_if_absent
        validation = validate_if_absent(workspace, task_id)
        if validation is not None and not validation["accepted"]:
            return _base(manifest_task_id=task_id, approval_state="denied", error=validation["error"])
    report = assess(workspace, task_id)
    if report["ok"] is not True:
        return _base(
            manifest_task_id=task_id, manifest_sha256=report.get("manifest_sha256"),
            approval_state="denied", error=report["error"],
            **_protected_fields(report),
        )
    store = _ledger(workspace)
    with store.locked():
        binding = routine_readiness(workspace, "execute")
        if not binding["binding_confirmed"]:
            return _base(manifest_task_id=task_id, approval_state="denied", error=str(binding["binding_error"]))
        report = assess(workspace, task_id)
        if report["ok"] is not True:
            return _base(
                manifest_task_id=task_id, approval_state="denied", error=report["error"],
                **_protected_fields(report),
            )
        _recover(store)
        from grokbot_mcp.execution.status import observe_execution
        for record in store.list_records():
            identity = record.get("execution_id")
            if (_in_flight(record) and is_uuid(identity)
                    and (_bridge(workspace) / "results_execute" / (identity + ".json")).exists()):
                observe_execution(workspace, identity, _locked_store=store)
        records = store.list_records()
        for record in records:
            if record.get("manifest_task_id") == task_id:
                return _base(execution_id=record.get("execution_id"), manifest_task_id=task_id, error="duplicate_execution")
            if _in_flight(record):
                return _base(manifest_task_id=task_id, error="concurrent_execution",
                             blocking_execution_id=record.get("execution_id"),
                             operator_message="An execution is still in flight; observe its original ID, never resend. / 仍有执行在途；请查询该执行 ID，不要重发。")
        execution_id = str(uuid.uuid4())
        if _sibling_has(execution_id, sibling_roots):
            return _base(manifest_task_id=task_id, error="execution_id_reused")
        document = report["document"]
        assert isinstance(document, dict)
        watched = watched_paths(workspace, document)
        try:
            snapshot = capture_snapshot(
                workspace.root, watched, include_ignored=workspace.git_gate != "advisory",
                **prefix_snapshot_options(workspace, document),
            )
            baseline_sha = write_snapshot(_bridge(workspace) / "execution_baselines", execution_id, snapshot)
        except ValueError as exc:
            return _base(manifest_task_id=task_id, error=str(exc))
        timeout = document.get("execution_timeout")
        record = {
            "schema_version": 2,
            "policy_fingerprint": policy_fingerprint(workspace),
            "binding_revision": binding["binding_revision"],
            "execution_id": execution_id,
            "manifest_task_id": task_id,
            "manifest_sha256": report["manifest_sha256"],
            "validation_result_sha256": report["validation_sha256"],
            "validation_verified_at": report["validation_verified_at"],
            "validation_result_ref": ".bridge/results_v2/" + task_id + ".json",
            "action": "execute_manifest",
            "created_at": utc_now(),
            "approval_state": "granted",
            "submission_state": "not_sent",
            "execution_claim_state": "unclaimed",
            "claimed_at": None,
            "execution_state": "prepared",
            "execution_result_path": ".bridge/results_execute/" + execution_id + ".json",
            "baseline_sha256": baseline_sha,
            "execution_timeout": timeout if isinstance(timeout, int) else 600,
            "qa_state": "not_reviewed",
            "request_attempted_at": None,
            "http_status": None,
            "accepted": None,
            "submission_final": False,
            "webhook_attempted": False,
            "error": None,
        }
        store.create(execution_id, record)
    return _base(
        execution_id=execution_id,
        manifest_task_id=task_id,
        manifest_sha256=report["manifest_sha256"],
        validation_result_ref=record["validation_result_ref"],
        execution_result_path=record["execution_result_path"],
        approval_state="granted",
        submission_state="not_sent",
        execution_state="prepared",
        execute_enabled=False,
    )


def submit_execution(
    workspace: Workspace,
    execution_id: str,
    *,
    switch_enabled: bool,
    status_fn: StatusFn,
    load_fn: LoadFn,
    transport: Transport | None = None,
) -> dict[str, object]:
    if not is_uuid(execution_id):
        return _base(error="invalid_execution_id")
    if not workspace.writes_enabled:
        return _base(execution_id=execution_id, error="workspace_writes_disabled",
                     sent_this_call=False, attempted_this_call=False)
    store = _ledger(workspace)
    with store.locked():
        _recover(store)
        try:
            record = store.read(execution_id)
        except ValueError as exc:
            return _base(execution_id=execution_id, error="missing" if str(exc) == "missing" else str(exc))
        if record.get("request_attempted_at") or record.get("submission_state") != "not_sent":
            return _from_record(record, error="retry_forbidden", execute_enabled=switch_enabled)
        if record.get("approval_state") != "granted" or record.get("terminal"):
            return _from_record(record, error="not_granted", execute_enabled=switch_enabled)
        task_id = str(record["manifest_task_id"])
        report = assess(workspace, task_id)
        if report["ok"] is not True or report.get("manifest_sha256") != record.get("manifest_sha256") or report.get("validation_sha256") != record.get("validation_result_sha256"):
            store.update(execution_id, approval_state="revoked", error=report.get("error") or "hash_mismatch")
            return _from_record(
                store.read(execution_id),
                error=str(report.get("error") or "hash_mismatch"),
                execute_enabled=switch_enabled,
                **_protected_fields(report),
            )
        if status_fn() != "present":
            return _from_record(record, error="credentials_" + status_fn(), execute_enabled=switch_enabled)
        try:
            verify_evidence(workspace, record, report, require_claim=False, unchanged=True)
        except (OSError, ValueError) as exc:
            return _from_record(record, error=str(exc), execute_enabled=switch_enabled)
        if not switch_enabled:
            return _from_record(record, error="execute_manifest_not_enabled", execute_enabled=False)
        routine = routine_readiness(workspace, "execute")
        if not routine["submit_ready"]:
            return _from_record(record, error=str(routine["error"]), execute_enabled=True)
        try:
            url, key = load_fn()
            payload = build_payload("execute_manifest", execution_id=execution_id, manifest_task_id=task_id)
        except (CredentialError, ValueError):
            return _from_record(record, error="credential_unreadable", execute_enabled=True)
        store.update(
            execution_id,
            submission_state="submission_started",
            execution_state="pending",
            request_attempted_at=utc_now(),
            submission_final=True,
            webhook_attempted=True,
        )
        sender = transport or (lambda item_url, item_key, item_payload, timeout: post_once(item_url, item_key, item_payload, timeout, enabled=True))
        try:
            outcome = sender(url, key, payload, 60.0)
        except Exception:
            outcome = HttpOutcome(kind="unknown", status=None)
        finally:
            url = ""
            key = ""
        return _record_http(store, execution_id, outcome, switch_enabled)


def claim_execution(workspace: Workspace, execution_id: str, sibling_roots: list[Path]) -> dict[str, object]:
    if not is_uuid(execution_id):
        return {"ok": False, "error": "invalid_execution_id"}
    if not workspace.writes_enabled:
        return {"ok": False, "error": "workspace_writes_disabled"}
    if _sibling_has(execution_id, sibling_roots):
        return {"ok": False, "error": "execution_id_reused"}
    from grokbot_mcp.routine import read_routine
    from grokbot_mcp.identity import runtime_identity
    claim_binding = read_routine(workspace.root / ".bridge/execute_routine_status.json").get("binding")
    if isinstance(claim_binding, dict) and "expected_installation_id" in claim_binding:
        if claim_binding["expected_installation_id"] != runtime_identity()["installation_id"]:
            return {"ok": False, "error": "binding_claim_runtime_mismatch"}
    store = _ledger(workspace)
    with store.locked():
        try:
            record = store.read(execution_id)
        except ValueError as exc:
            return {"ok": False, "error": "invalid_execution_id" if str(exc) == "missing" else str(exc)}
        if record.get("submission_state") == "unknown_submission":
            return {"ok": False, "error": "unknown_submission"}
        if record.get("submission_state") not in {"submission_started", "accepted"}:
            return {"ok": False, "error": "not_claimable"}
        state = record.get("execution_state")
        if state in TERMINAL:
            return {"ok": False, "error": "already_terminal"}
        if state != "pending":
            return {"ok": False, "error": "invalid_execution_state"}
        if record.get("approval_state") != "granted":
            return {"ok": False, "error": "not_granted"}
        claim_file = _bridge(workspace) / "execution_claims" / f"{execution_id}.json"
        if claim_file.exists() or record.get("execution_claim_state") == "claimed":
            return {"ok": False, "error": "duplicate_claim"}
        task_id = str(record["manifest_task_id"])
        report = assess(workspace, task_id)
        try:
            verify_evidence(workspace, record, report, require_claim=False, unchanged=True)
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc), **_protected_fields(report)}
        document = report["document"]
        assert isinstance(document, dict)
        if workspace.git_gate == "advisory":
            watched = watched_paths(workspace, document)
            try:
                snapshot = capture_snapshot(
                    workspace.root, watched, include_ignored=workspace.git_gate != "advisory",
                    **prefix_snapshot_options(workspace, document),
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            if snapshot.get("git_head_text") != document.get("baseline_commit"):
                return {"ok": False, "error": "git_head_mismatch"}
            try:
                baseline_sha = replace_snapshot(
                    _bridge(workspace) / "execution_baselines", execution_id, snapshot,
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            record = store.update(execution_id, baseline_sha256=baseline_sha)
        try:
            allowed = resolve_task_files(workspace, document)
        except TargetRejected as exc:
            return {"ok": False, "error": exc.code}
        claim_file.parent.mkdir(parents=True, exist_ok=True)
        creates = document.get("scope_expansion", {}).get("create_files")
        target_check = "prospective" if creates else "present"
        payload = {
            **active_prefix_fields(workspace, document),
            "schema_version": 2,
            "workspace_id": workspace.workspace_id,
            "machine": workspace.expected_machine,
            "baseline_sha256": record["baseline_sha256"],
            "policy_fingerprint": record["policy_fingerprint"],
            "binding_revision": record["binding_revision"],
            "execution_id": execution_id,
            "manifest_task_id": task_id,
            "manifest_sha256": record.get("manifest_sha256"),
            "validation_result_sha256": record.get("validation_result_sha256"),
            "allowed_files": allowed,
            "target_check": target_check,
            **({"create_files": creates} if creates is not None else {}),
            "enforcement": "advisory_precheck",
            "claimed_at": utc_now(),
        }
        if workspace.git_gate == "advisory":
            payload.update(
                git_gate="advisory",
                baseline_commit=snapshot["git_head_text"],
                existing_changes=sorted(set(snapshot.get("diff_files") or [])
                                        | set(snapshot.get("untracked") or [])),
            )
        import json

        raw = json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8")
        check_size(raw)
        try:
            handle = os.open(str(claim_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return {"ok": False, "error": "duplicate_claim"}
        try:
            with os.fdopen(handle, "wb", closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(handle)
        if is_reparse_point(claim_file):
            return {"ok": False, "error": "reparse"}
        store.update(execution_id, execution_claim_state="claimed", claimed_at=payload["claimed_at"], claim_sha256=sha256_bytes(raw))
        return {
            "ok": True,
            **active_prefix_fields(workspace, document),
            "error": None,
            "execution_id": execution_id,
            "manifest_task_id": task_id,
            "manifest_sha256": record.get("manifest_sha256"),
            "workspace_id": workspace.workspace_id,
            "allowed_files": allowed,
            "forbidden_files": list(workspace.forbidden_files),
            **({"create_files": creates} if creates is not None else {}),
            "baseline_commit": document.get("baseline_commit"),
            **({"git_gate": "advisory", "existing_changes": payload["existing_changes"]}
               if workspace.git_gate == "advisory" else {}),
            "test_command_id": document.get("test_command_id"),
            "acceptance_criteria": document.get("acceptance_criteria"),
            "target_check": target_check,
            "enforcement": "advisory_precheck",
        }


def get_execution_status(workspace: Workspace, execution_id: str, *,
                         observe_only: bool = False, verify_current: bool = False) -> dict[str, object]:
    from grokbot_mcp.execution.status import observe_execution
    return observe_execution(workspace, execution_id, observe_only=observe_only, verify_current=verify_current)


def _interpret(
    store: JsonLedger,
    workspace: Workspace,
    record: dict[str, object],
    result: dict[str, object],
    parsed: dict[str, object],
    result_sha256: str,
) -> dict[str, object]:
    execution_id = str(record["execution_id"])
    task_id = str(record["manifest_task_id"])
    result["result_diagnosis"] = diagnosis(parsed, "execute", expected=result_identity(workspace, "execute", record),
                                           require_test_output=getattr(workspace, "require_test_output", False))
    if parsed.get("cursor_cloud_agent_used") is True:
        return _fail(store, execution_id, result, "external_agent_rejected", result_sha256)
    identity_ok = (
        parsed.get("protocol_version") in (1, "1")
        and parsed.get("execution_id") == execution_id
        and parsed.get("manifest_task_id") == task_id
        and parsed.get("action") == "execute_manifest"
        and parsed.get("manifest_sha256") == record.get("manifest_sha256")
        and parsed.get("machine") == workspace.expected_machine
    )
    if not identity_ok:
        return _fail(store, execution_id, result, "identity_mismatch", result_sha256)
    report = assess(workspace, task_id, check_workspace=False)
    try:
        baseline, current = verify_evidence(workspace, record, report, require_claim=True, review_head=True)
    except (OSError, ValueError) as exc:
        return _fail(store, execution_id, result, str(exc), result_sha256)
    delta = execution_delta(baseline, current)
    if workspace.git_gate == "advisory":
        from grokbot_mcp.security.git_review import changes_since_baseline
        try:
            delta = changes_since_baseline(getattr(workspace, "root", None), baseline, current)
        except (OSError, ValueError) as exc:
            return _fail(store, execution_id, result, str(exc), result_sha256)
    result["changed_files"] = delta
    result["code_modified"] = bool(delta)
    document = report.get("document")
    allowed = set(document.get("allowed_files") or []) if isinstance(document, dict) else set()

    def match_path(name):
        return (normalize_policy_path(name, casefold=True) if workspace.git_gate == "advisory"
                else unicodedata.normalize("NFC", name))

    allowed_keys = {match_path(name) for name in allowed}
    delta_keys = {match_path(name) for name in delta}
    frozen_workspace = manifest_workspace(workspace, document)
    reported = _reported_files(parsed.get("changed_files"), frozen_workspace, advisory=workspace.git_gate == "advisory")
    forbidden = set(workspace.forbidden_files)
    protected_change = any(_blocked(name, forbidden, advisory=workspace.git_gate == "advisory") for name in delta)
    protected_report = reported is not None and any(_blocked(name, forbidden, advisory=workspace.git_gate == "advisory") for name in reported)
    if reported is None or protected_change or protected_report:
        return _fail(store, execution_id, result, "scope_violation", result_sha256)
    reported_keys = {match_path(name) for name in reported}
    out_of_scope = sorted({name for name in set(reported) | set(delta) if match_path(name) not in allowed_keys})
    if workspace.git_gate == "advisory":
        result["out_of_scope_changes"] = out_of_scope
    from grokbot_mcp.execution.evidence import creation_evidence
    try:
        not_created = creation_evidence(workspace, document, baseline, current, reported)
    except ValueError as exc:
        return _fail(store, execution_id, result, str(exc), result_sha256)
    if not_created is not None:
        result["not_created"] = not_created
        if "not_created" in parsed and parsed["not_created"] != not_created:
            return _fail(store, execution_id, result, "result_inconsistent", result_sha256)
    elif "not_created" in parsed:
        return _fail(store, execution_id, result, "result_inconsistent", result_sha256)
    if workspace.git_gate == "advisory":
        from grokbot_mcp.security.git_review import review
        result["git_review"] = review(baseline, current, delta, reported, list(allowed),
                                      getattr(workspace, "other_prefixes", ()))
        # Preserve the old outside-allowed union, including declared others.
        if result["git_review"]["head_changed"]:
            return _fail(store, execution_id, result, "git_head_mismatch", result_sha256)
        if (reported_keys & allowed_keys) != (delta_keys & allowed_keys):
            return _fail(store, execution_id, result, "result_inconsistent", result_sha256)
    elif any(_outside(match_path(name), allowed_keys, workspace.forbidden_files) for name in delta):
        return _fail(store, execution_id, result, "scope_violation", result_sha256)
    elif reported_keys != delta_keys:
        return _fail(store, execution_id, result, "result_inconsistent", result_sha256)
    issue = result["result_diagnosis"]
    if issue["category"] in {"format_error", "identity_error"}:
        legacy = "identity_mismatch" if issue["category"] == "identity_error" else "test_failed"
        return _fail(store, execution_id, result, legacy, result_sha256)
    tests = parsed.get("tests")
    command = document.get("test_command_id") if isinstance(document, dict) else None
    if not isinstance(tests, dict) or tests.get("command_id") != command:
        return _fail(store, execution_id, result, "illegal_test_command", result_sha256)
    if issue["category"] == "test_evidence_missing":
        return _fail(store, execution_id, result, "test_evidence_missing", result_sha256)
    from grokbot_mcp.test_commands import enhanced_tests
    enhanced = enhanced_tests(workspace, workspace.command(command))
    if enhanced:
        # Raw output stays only in the result file; status/ledger contain metadata.
        result["tests"] = {k: tests[k] for k in ("command_id", "passed", "failed", "exit_code", "not_run",
                            "unit", "case_count", "assertion_count", "output_sha256", "output_bytes") if k in tests}
    if tests.get("not_run") is True:
        result.update(test_passed=None, test_failed=None, godot_exit_code=None)
        return _fail(store, execution_id, result, "execution_failed", result_sha256)
    passed = tests.get("passed")
    failed = tests.get("failed")
    exit_code = tests.get("exit_code")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in (passed, failed, exit_code)):
        return _fail(store, execution_id, result, "test_failed", result_sha256)
    result["test_passed"] = passed
    result["test_failed"] = failed
    godot_exit_code = exit_code if not enhanced or command == "godot" or command.startswith("godot_") else None
    result["godot_exit_code"] = godot_exit_code
    if passed < 0 or failed != 0 or exit_code != 0 or parsed.get("status") == "failed":
        return _fail(store, execution_id, result, "test_failed", result_sha256)
    if parsed.get("status") != "succeeded":
        return _fail(store, execution_id, result, "execution_failed", result_sha256)
    task_type = document.get("task_type") if isinstance(document, dict) else None
    optional_creates = {match_path(n) for n in document.get("scope_expansion", {}).get("create_files", [])}
    if task_type == "code_change" and not (allowed_keys - optional_creates) <= delta_keys:
        return _fail(store, execution_id, result, "result_inconsistent", result_sha256)
    verified_at = utc_now()
    store.update(execution_id, execution_state="succeeded", qa_state="not_reviewed", error=None,
                 result_sha256=result_sha256, verified_at=verified_at, historical_status="succeeded",
                 terminal=True, verified_snapshot=current, late_result=deadline(record)["deadline_exceeded"],
                 verified_summary={"changed_files": delta, "code_modified": bool(delta),
                                   **({"not_created": not_created} if not_created is not None else {}),
                                   **({"out_of_scope_changes": out_of_scope, "git_review": result["git_review"]}
                                      if workspace.git_gate == "advisory" else {}),
                                   "test_passed": passed, "test_failed": failed, "godot_exit_code": godot_exit_code,
                                   **({"tests": result["tests"]} if enhanced else {}),
                                   "result_diagnosis": result.get("result_diagnosis")})
    result.update(verified_at=verified_at, historical_status="succeeded", verification_scope="current",
                  current_integrity=True, integrity_error=None)
    result["execution_state"] = "succeeded"
    result["status"] = "succeeded"
    result["reported_success"] = True
    result["qa_accepted"] = False
    result["error"] = None
    return result


def _reported_files(value: object, workspace: Workspace, *, advisory: bool = False) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        try:
            text = (normalize_policy_path(item) if advisory else
                    normalize_repo_path(unicodedata.normalize("NFC", item)))
        except Exception:
            return None
        if not advisory:
            if getattr(workspace, "allowed_prefixes", ()):
                if not policy_allows(workspace, text) or _blocked(text, set(workspace.forbidden_files)):
                    return None
            elif _outside(text, {unicodedata.normalize("NFC", name) for name in workspace.allowed_files}, workspace.forbidden_files):
                return None
        names.append(text)
    return names


def _outside(name: str, allowed: set[str], forbidden: tuple[str, ...]) -> bool:
    if name not in allowed:
        return True
    return any(name == item or name.startswith(item + "/") for item in forbidden)


def _blocked(name: str, forbidden: set[str], *, advisory: bool = False) -> bool:
    if advisory:
        try:
            name = normalize_policy_path(name, casefold=True)
            forbidden = {normalize_policy_path(item, casefold=True) for item in forbidden}
        except PathRejected:
            return True
    return any(name == item or name.startswith(item + "/") for item in forbidden)


def _fail(store: JsonLedger, execution_id: str, result: dict[str, object], code: str, result_sha256: str) -> dict[str, object]:
    state = "failed" if code == "test_failed" else code
    verified_at = utc_now()
    summary = {"result_diagnosis": result.get("result_diagnosis")}
    if isinstance(result.get("out_of_scope_changes"), list):
        summary["out_of_scope_changes"] = result["out_of_scope_changes"]
    for key in ("git_review", "tests", "not_created"):
        if key in result:
            summary[key] = result[key]
    store.update(execution_id, execution_state=state, qa_state="rejected", error=code,
                 result_sha256=result_sha256, verified_at=verified_at, historical_status=state, terminal=True,
                 late_result=deadline(store.read(execution_id))["deadline_exceeded"],
                 verified_summary=summary)
    result.update(verified_at=verified_at, historical_status=state, verification_scope="current",
                  current_integrity=False, integrity_error=code)
    result["execution_state"] = state
    result["status"] = state
    result["qa_state"] = "rejected"
    result["qa_accepted"] = False
    result["reported_success"] = False
    result["error"] = code
    return result


def _record_http(store: JsonLedger, execution_id: str, outcome: HttpOutcome, switch_enabled: bool) -> dict[str, object]:
    diagnosis = diagnosis_for_outcome(outcome.kind, outcome.status, outcome.diagnosis)
    if outcome.kind == "status" and outcome.status == 200:
        record = store.update(execution_id, submission_state="accepted", execution_state="pending", http_status=200, accepted=True, submission_final=True, webhook_attempted=True, error=None, **diagnosis)
        return _from_record(record, error=None, execute_enabled=switch_enabled, webhook_sent=True, webhook_attempted=True)
    if outcome.kind == "status" and outcome.status is not None:
        record = store.update(execution_id, submission_state="rejected", execution_state="failed", http_status=outcome.status, accepted=False, submission_final=True, qa_state="rejected", error="http_rejected", **diagnosis)
        return _from_record(record, error="http_rejected", execute_enabled=switch_enabled, webhook_sent=True, webhook_attempted=True)
    error = "http_timeout" if outcome.kind == "timeout" else "unknown_submission"
    record = store.update(execution_id, submission_state="unknown_submission", execution_state="unknown_submission", accepted=False, submission_final=True, error=error, **diagnosis)
    return _from_record(record, error=error, execute_enabled=switch_enabled, webhook_sent=True, webhook_attempted=True)


def _from_record(record: dict[str, object], error: str | None, **extra: object) -> dict[str, object]:
    payload = _base(
        execution_id=record.get("execution_id"),
        manifest_task_id=record.get("manifest_task_id"),
        manifest_sha256=record.get("manifest_sha256"),
        validation_result_ref=record.get("validation_result_ref"),
        execution_result_path=record.get("execution_result_path"),
        approval_state=record.get("approval_state"),
        submission_state=record.get("submission_state"),
        execution_state=record.get("execution_state"),
        execution_claim_state=record.get("execution_claim_state", "unclaimed"),
        qa_state=record.get("qa_state"),
        accepted=record.get("accepted") is True,
        http_status=record.get("http_status"),
        webhook_attempted=record.get("webhook_attempted") is True,
        request_attempted_at=record.get("request_attempted_at"),
        error=error,
    )
    payload.update(extra)
    payload["historical_submission_state"] = record.get("submission_state")
    payload["sent_this_call"] = extra.get("webhook_sent") is True
    payload["attempted_this_call"] = extra.get("webhook_attempted") is True
    payload["qa_accepted"] = False
    return payload
