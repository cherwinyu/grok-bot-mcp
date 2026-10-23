"""Precheck staged results; explicit publication is not bridge acceptance or QA.

Private ``--source`` input is not an arbitrary path. For both validate and execute
the only accepted candidate is::

    %TEMP%/grokbot-mcp-candidates/<phase>/.<id>.result.tmp

That filename matches the official staged name. The staged copy itself stays at
``.bridge/results_v2/.<task-id>.result.tmp`` or
``.bridge/results_execute/.<execution-id>.result.tmp``. ``--source`` must be
outside the registered workspace. ``.result.candidate.json`` is not a protocol name.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from grokbot_mcp.execution.evidence import verify_evidence
from grokbot_mcp.execution.service import assess
from grokbot_mcp.identity import digest
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.local_safety import (
    LocalError, atomic_replace, create_new, current_hash, json_bytes, parse_json, read_document, safe_path,
)
from grokbot_mcp.manifest.store import ManifestError, load_manifest
from grokbot_mcp.registry import load_registry
from grokbot_mcp.result_contract import ContractError, diagnosis, result_identity, validate_result
from grokbot_mcp.security.paths import contained, is_uuid
from grokbot_mcp.storage import JsonLedger, utc_now


CANDIDATE_DIRECTORY_NAME = "grokbot-mcp-candidates"
_TERMINAL = {"succeeded", "failed"}


def candidate_root() -> Path:
    return Path(tempfile.gettempdir()) / CANDIDATE_DIRECTORY_NAME


def private_candidate_path(phase: str, identity: str) -> Path:
    """Deterministic private candidate. Same filename for both phases."""
    if phase not in {"validate", "execute"} or type(identity) is not str or not is_uuid(identity):
        raise LocalError("result_identity_invalid")
    return candidate_root() / phase / ("." + identity + ".result.tmp")


def _result_dir(phase: str) -> str:
    if phase == "validate":
        return "results_v2"
    if phase == "execute":
        return "results_execute"
    raise LocalError("result_phase_invalid")


def _location_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _require_private_source(source: Path, workspace_root: Path, phase: str, identity: str) -> None:
    """Accept only the temp candidate. Reject in-workspace guesses and other names."""
    safe_path(source)
    expected = private_candidate_path(phase, identity)
    inside = contained(workspace_root, source) or contained(workspace_root, expected)
    if inside or _location_key(source) != _location_key(expected):
        raise LocalError("result_source_location_invalid")


def _check(registry: Path, workspace_id: str, phase: str, identity: str, staged: Path, *, incoming_raw: bytes | None = None):
    if phase not in {"validate", "execute"}:
        raise LocalError("result_phase_invalid")
    if type(identity) is not str or not is_uuid(identity):
        raise LocalError("result_identity_invalid")
    safe_path(registry, regular=True)
    workspaces = load_registry(registry)
    workspace = workspaces.get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    from grokbot_mcp.ledger_archive import archived
    if archived(workspace.root, identity, execution=phase == "execute"):
        raise LocalError("archived_task")
    directory = _result_dir(phase)
    target = workspace.root / ".bridge" / directory / (identity + ".json")
    ledger_dir = workspace.root / ".bridge" / ("tasks_v2" if phase == "validate" else "executions")
    safe_path(target)
    safe_path(staged, regular=incoming_raw is None)
    if staged.parent.resolve() != target.parent.resolve() or staged.name != "." + identity + ".result.tmp":
        raise LocalError("result_staging_path_invalid")
    record, record_raw = read_document(ledger_dir / (identity + ".json"))
    if record.get("terminal") or record.get("result_sha256"):
        raise LocalError("result_terminal_frozen")
    if record.get("submission_state") != "accepted" or record.get("accepted") is not True:
        raise LocalError("result_submission_not_accepted")
    task_id = identity if phase == "validate" else record.get("manifest_task_id")
    if type(task_id) is not str or not is_uuid(task_id):
        raise LocalError("result_identity_invalid")
    expected_path = ".bridge/" + directory + "/" + identity + ".json"
    path_key = "result_path" if phase == "validate" else "execution_result_path"
    id_key = "task_id" if phase == "validate" else "execution_id"
    if record.get(path_key) != expected_path or record.get(id_key) != identity or record.get("action") != phase + "_manifest":
        raise LocalError("result_ledger_identity_mismatch")
    try:
        document, manifest_sha = load_manifest(workspace.root, task_id)
    except ManifestError as exc:
        raise LocalError(exc.code) from exc
    if (document.get("workspace_id") != workspace_id or document.get("task_id") != task_id
            or record.get("manifest_task_id", task_id) != task_id or manifest_sha != record.get("manifest_sha256")):
        raise LocalError("result_manifest_mismatch")
    value, raw = read_document(staged) if incoming_raw is None else (parse_json(incoming_raw), incoming_raw)
    expected = result_identity(workspace, phase, record)
    validate_result(value, phase, expected=expected)
    if phase == "validate":
        if "allowed_files" in value and value["allowed_files"] != document.get("allowed_files"):
            raise ContractError("allowed_files", "manifest allow-list", category="identity_error", code="result_identity_mismatch")
    else:
        if "tests" in value and value["tests"]["command_id"] != document.get("test_command_id"):
            raise ContractError("tests.command_id", "manifest command ID", category="identity_error", code="result_identity_mismatch")
        # Do not create or retrofit a claim. Existing evidence is the authority.
        verify_evidence(workspace, record, assess(workspace, task_id, check_workspace=False),
                        require_claim=value["status"] != "pending" or record.get("execution_claim_state") == "claimed",
                        registry=registry)
    previous = None
    before_hash = current_hash(target)
    if before_hash is not None:
        previous, _ = read_document(target)
        try:
            validate_result(previous, phase, expected=expected)
        except ContractError as exc:
            raise LocalError("result_existing_evidence_frozen") from exc
        if previous["status"] not in {"pending", "running"}:
            raise LocalError("result_terminal_frozen")
        if previous["status"] == "running" and value["status"] == "pending":
            raise LocalError("result_transition_invalid")
    state_key = "task_state" if phase == "validate" else "execution_state"
    if record.get(state_key) == "running" and value["status"] == "pending":
        raise LocalError("result_transition_invalid")
    report = {"ok": True, "format_valid": True, "identity_valid": True,
              "result_diagnosis": diagnosis(value, phase, expected=expected, require_test_output=workspace.require_test_output),
              "staged_sha256": digest(raw), "destination": expected_path,
              "previous_status": previous["status"] if previous else None, "next_status": value["status"],
              "published": False, "bridge_accepted": False, "qa_accepted": False,
              "webhook_sent": False, "ledger_modified": False}
    snapshot = {"registry": current_hash(registry), "ledger": digest(record_raw), "target": before_hash}
    return report, raw, target, ledger_dir, snapshot, workspace


def precheck(registry: Path, workspace_id: str, phase: str, identity: str, staged: Path) -> dict:
    return _check(registry, workspace_id, phase, identity, staged)[0]


def _cleanup_record_path(root: Path, phase: str, identity: str) -> Path:
    return root / ".bridge" / "protocol_cleanup" / phase / (identity + ".json")


def _plan_temp(path: Path, role: str, identity: str, expected_hash: str, workspace_root: Path, published: Path) -> dict:
    """Decide whether one contract temp file may be removed. Does not delete it."""
    name = "." + identity + ".result.tmp"
    if path.name != name or _location_key(path) == _location_key(published):
        raise LocalError("result_cleanup_path_invalid")
    safe_path(path)
    if role == "candidate" and contained(workspace_root, path):
        return {"role": role, "name": name, "state": "skipped", "reason": "result_source_location_invalid"}
    if not path.is_file():
        return {"role": role, "name": name, "state": "absent"}
    safe_path(path, regular=True)
    file_hash = current_hash(path)
    if file_hash != expected_hash:
        return {"role": role, "name": name, "state": "skipped", "reason": "hash_mismatch"}
    return {"role": role, "name": name, "state": "removed", "sha256": file_hash, "path": path}


def _cleanup_unlocked(registry: Path, workspace_id: str, phase: str, identity: str) -> dict:
    if phase not in {"validate", "execute"} or type(identity) is not str or not is_uuid(identity):
        raise LocalError("result_identity_invalid")
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    directory = _result_dir(phase)
    target = workspace.root / ".bridge" / directory / (identity + ".json")
    safe_path(target, regular=True)
    value, raw = read_document(target)
    if value.get("status") not in _TERMINAL:
        raise LocalError("result_cleanup_not_terminal")
    published_hash = digest(raw)
    record_path = _cleanup_record_path(workspace.root, phase, identity)
    existing = None
    if record_path.exists():
        existing, _ = read_document(record_path)
        if (existing.get("kind") != "protocol_file_cleanup" or existing.get("schema_version") != 1
                or existing.get("phase") != phase or existing.get("identity") != identity
                or type(existing.get("events")) is not list):
            raise LocalError("result_cleanup_record_invalid")
        if existing.get("published_sha256") != published_hash:
            raise LocalError("result_cleanup_conflict")
    staged = workspace.root / ".bridge" / directory / ("." + identity + ".result.tmp")
    candidate = private_candidate_path(phase, identity)
    planned = [
        _plan_temp(staged, "staged", identity, published_hash, workspace.root, target),
        _plan_temp(candidate, "candidate", identity, published_hash, workspace.root, target),
    ]
    for item in planned:
        if item["state"] != "removed":
            continue
        path = item.pop("path")
        path.unlink()
        if path.exists():
            raise LocalError("result_cleanup_failed")
    outcomes = planned
    removed = [item for item in outcomes if item["state"] == "removed"]
    skipped = [item for item in outcomes if item["state"] == "skipped"]
    absent = [item for item in outcomes if item["state"] == "absent"]
    relative = ".bridge/protocol_cleanup/" + phase + "/" + identity + ".json"
    event = {"removed": removed, "skipped": skipped, "absent": absent, "recorded_at": utc_now()}
    if existing is None:
        document = {
            "schema_version": 1, "kind": "protocol_file_cleanup", "phase": phase, "identity": identity,
            "published_result": ".bridge/" + directory + "/" + identity + ".json",
            "published_sha256": published_hash, "result_preserved": True,
            "submission_ledger_modified": False, "events": [event],
        }
        record_path.parent.mkdir(parents=True, exist_ok=True)
        create_new(record_path, json_bytes(document))
        recorded = True
    elif removed:
        events = list(existing.get("events") or [])
        events.append(event)
        existing["events"] = events
        existing["result_preserved"] = True
        existing["submission_ledger_modified"] = False
        atomic_replace(record_path, json_bytes(existing), expected=current_hash(record_path))
        recorded = True
    else:
        recorded = True
    if current_hash(target) != published_hash:
        raise LocalError("result_readback_mismatch")
    return {
        "ok": True, "kind": "protocol_file_cleanup", "removed": removed, "skipped": skipped, "absent": absent,
        "cleanup_recorded": recorded, "record_path": relative, "published_sha256": published_hash,
        "submission_ledger_modified": False, "result_preserved": True, "ledger_modified": False,
    }


def cleanup_protocol_files(registry: Path, workspace_id: str, phase: str, identity: str) -> dict:
    """Delete only this id's terminal staged file and matching temp candidate."""
    if phase not in {"validate", "execute"} or type(identity) is not str or not is_uuid(identity):
        raise LocalError("result_identity_invalid")
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    ledger_dir = workspace.root / ".bridge" / ("tasks_v2" if phase == "validate" else "executions")
    with JsonLedger(ledger_dir).locked():
        return _cleanup_unlocked(registry, workspace_id, phase, identity)


def publish(registry: Path, workspace_id: str, phase: str, identity: str, staged: Path,
            *, confirm_sha256: str) -> dict:
    initial, _, _, ledger_dir, snapshot, _workspace = _check(registry, workspace_id, phase, identity, staged)
    if confirm_sha256 != initial["staged_sha256"]:
        raise LocalError("result_confirmation_mismatch")
    # This is the existing submission/observation lock. No submission-ledger field is changed.
    cleanup = None
    with JsonLedger(ledger_dir).locked():
        report, raw, target, _, current, _workspace = _check(registry, workspace_id, phase, identity, staged)
        if current != snapshot or digest(raw) != confirm_sha256:
            raise LocalError("input_changed")
        atomic_replace(target, raw, expected=snapshot["target"])
        if current_hash(target) != confirm_sha256:
            raise LocalError("result_readback_mismatch")
        if parse_json(raw).get("status") in _TERMINAL:
            try:
                cleanup = _cleanup_unlocked(registry, workspace_id, phase, identity)
            except LocalError as exc:
                cleanup = {"ok": False, "error": exc.code, "submission_ledger_modified": False,
                           "result_preserved": True, "ledger_modified": False}
    report["published"] = True
    report["ledger_modified"] = False
    if cleanup is not None:
        report["protocol_cleanup"] = cleanup
    return report


def stage_candidate(registry: Path, workspace_id: str, phase: str, identity: str, source: Path,
                    *, apply: bool = False, confirm_sha256: str | None = None, previous_staged_sha256: str | None = None) -> dict:
    """Optional safe staging. The caller chooses only the fixed temp candidate, never an output path."""
    if phase not in {"validate", "execute"} or type(identity) is not str or not is_uuid(identity):
        raise LocalError("result_identity_invalid")
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    _require_private_source(source, workspace.root, phase, identity)
    _, raw = read_document(source)
    directory = _result_dir(phase)
    staged = workspace.root / ".bridge" / directory / ("." + identity + ".result.tmp")
    report, _, _, ledger_dir, snapshot, _workspace = _check(
        registry, workspace_id, phase, identity, staged, incoming_raw=raw)
    stage_hash = current_hash(staged)
    report.update(staged_written=False, previous_staged_sha256=stage_hash,
                  staging_path=".bridge/" + directory + "/" + staged.name,
                  source_contract="temp_candidate")
    if not apply:
        return report
    if confirm_sha256 != digest(raw):
        raise LocalError("result_confirmation_mismatch")
    if stage_hash != previous_staged_sha256:
        raise LocalError("result_staging_changed")
    with JsonLedger(ledger_dir).locked():
        _, _, _, _, fresh, _workspace = _check(registry, workspace_id, phase, identity, staged, incoming_raw=raw)
        if fresh != snapshot:
            raise LocalError("input_changed")
        safe_path(staged.parent)
        staged.parent.mkdir(parents=True, exist_ok=True)
        if current_hash(staged) != stage_hash:
            raise LocalError("result_staging_changed")
        if stage_hash is None:
            create_new(staged, raw)
        else:
            atomic_replace(staged, raw, expected=stage_hash)
    report["staged_written"] = True
    return report


def main(argv=None) -> int:
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--registry", type=Path, required=True)
        parser.add_argument("--workspace", required=True)
        parser.add_argument("--phase", choices=("validate", "execute"), required=True)
        parser.add_argument("--id", required=True)
        parser.add_argument("--cleanup", action="store_true")
        source = parser.add_mutually_exclusive_group()
        source.add_argument("--staged", type=Path)
        source.add_argument("--source", type=Path)
        write = parser.add_mutually_exclusive_group()
        write.add_argument("--publish", action="store_true")
        write.add_argument("--stage", action="store_true")
        parser.add_argument("--confirm-sha256")
        parser.add_argument("--previous-staged-sha256")
        args = parser.parse_args(argv)
        if args.previous_staged_sha256 and not (args.stage and args.source):
            raise LocalError("invalid_arguments")
        if ((args.confirm_sha256 and not (args.publish or args.stage)) or (args.stage and not args.source)
                or (args.publish and not args.staged)):
            raise LocalError("invalid_arguments")
        fields = (args.registry.absolute(), args.workspace, args.phase, args.id)
        if args.cleanup:
            if args.source or args.staged or args.publish or args.stage or args.confirm_sha256 or args.previous_staged_sha256:
                raise LocalError("invalid_arguments")
            result = cleanup_protocol_files(*fields)
        elif args.source:
            result = stage_candidate(*fields, args.source.absolute(), apply=args.stage, confirm_sha256=args.confirm_sha256, previous_staged_sha256=args.previous_staged_sha256)
        elif args.staged:
            staged_fields = (*fields, args.staged.absolute())
            result = publish(*staged_fields, confirm_sha256=args.confirm_sha256) if args.publish else precheck(*staged_fields)
        else:
            raise LocalError("invalid_arguments")
        emit(result)
        return 0
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
