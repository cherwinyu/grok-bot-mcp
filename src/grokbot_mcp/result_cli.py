"""Precheck staged results; explicit publication is not bridge acceptance or QA."""
from __future__ import annotations

from pathlib import Path

from grokbot_mcp.execution.evidence import verify_evidence
from grokbot_mcp.execution.service import assess
from grokbot_mcp.identity import digest
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.local_safety import LocalError, atomic_replace, read_document, safe_path, current_hash, parse_json, create_new
from grokbot_mcp.manifest.store import load_manifest
from grokbot_mcp.registry import load_registry
from grokbot_mcp.result_contract import ContractError, diagnosis, result_identity, validate_result
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.storage import JsonLedger


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
    directory = "results_v2" if phase == "validate" else "results_execute"
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
    document, manifest_sha = load_manifest(workspace.root, task_id)
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
              "result_diagnosis": diagnosis(value, phase, expected=expected),
              "staged_sha256": digest(raw), "destination": expected_path,
              "previous_status": previous["status"] if previous else None, "next_status": value["status"],
              "published": False, "bridge_accepted": False, "qa_accepted": False,
              "webhook_sent": False, "ledger_modified": False}
    snapshot = {"registry": current_hash(registry), "ledger": digest(record_raw), "target": before_hash}
    return report, raw, target, ledger_dir, snapshot


def precheck(registry: Path, workspace_id: str, phase: str, identity: str, staged: Path) -> dict:
    return _check(registry, workspace_id, phase, identity, staged)[0]


def publish(registry: Path, workspace_id: str, phase: str, identity: str, staged: Path,
            *, confirm_sha256: str) -> dict:
    initial, _, _, ledger_dir, snapshot = _check(registry, workspace_id, phase, identity, staged)
    if confirm_sha256 != initial["staged_sha256"]:
        raise LocalError("result_confirmation_mismatch")
    # This is the existing submission/observation lock. No ledger field is changed.
    with JsonLedger(ledger_dir).locked():
        report, raw, target, _, current = _check(registry, workspace_id, phase, identity, staged)
        if current != snapshot or digest(raw) != confirm_sha256:
            raise LocalError("input_changed")
        atomic_replace(target, raw, expected=snapshot["target"])
        if current_hash(target) != confirm_sha256:
            raise LocalError("result_readback_mismatch")
    report["published"] = True
    return report


def stage_candidate(registry: Path, workspace_id: str, phase: str, identity: str, source: Path,
                    *, apply: bool = False, confirm_sha256: str | None = None, previous_staged_sha256: str | None = None) -> dict:
    """Optional safe staging. The caller chooses an INPUT, never an output path."""
    if phase not in {"validate", "execute"} or type(identity) is not str or not is_uuid(identity):
        raise LocalError("result_identity_invalid")
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    _, raw = read_document(source)
    directory = "results_v2" if phase == "validate" else "results_execute"
    staged = workspace.root / ".bridge" / directory / ("." + identity + ".result.tmp")
    report, _, _, ledger_dir, snapshot = _check(registry, workspace_id, phase, identity, staged, incoming_raw=raw)
    stage_hash = current_hash(staged)
    report.update(staged_written=False, previous_staged_sha256=stage_hash, staging_path=".bridge/" + directory + "/" + staged.name)
    if not apply:
        return report
    if confirm_sha256 != digest(raw):
        raise LocalError("result_confirmation_mismatch")
    if stage_hash != previous_staged_sha256:
        raise LocalError("result_staging_changed")
    with JsonLedger(ledger_dir).locked():
        _, _, _, _, fresh = _check(registry, workspace_id, phase, identity, staged, incoming_raw=raw)
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
        source = parser.add_mutually_exclusive_group(required=True)
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
        if args.source:
            result = stage_candidate(*fields, args.source.absolute(), apply=args.stage, confirm_sha256=args.confirm_sha256, previous_staged_sha256=args.previous_staged_sha256)
        else:
            fields = (*fields, args.staged.absolute())
            result = publish(*fields, confirm_sha256=args.confirm_sha256) if args.publish else precheck(*fields)
        emit(result)
        return 0
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
