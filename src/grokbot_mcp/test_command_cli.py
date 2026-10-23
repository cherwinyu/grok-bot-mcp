"""Read-only local argv preview for an existing claimed execution. Never runs it."""
from __future__ import annotations

from pathlib import Path

from grokbot_mcp.execution.evidence import verify_evidence
from grokbot_mcp.execution.service import assess
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.registry import default_registry_path, load_registry
from grokbot_mcp.security.baseline import execution_delta
from grokbot_mcp.security.git_review import changes_since_baseline
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.storage import JsonLedger
from grokbot_mcp.test_commands import changed_files_argv


def preview(registry: Path, workspace_id: str, execution_id: str) -> dict:
    if not is_uuid(execution_id):
        raise ValueError("invalid_execution_id")
    workspace = load_registry(registry)[workspace_id]
    record = JsonLedger(workspace.root / ".bridge/executions").read(execution_id)
    if (record.get("execution_id") != execution_id or record.get("action") != "execute_manifest"
            or record.get("execution_result_path") != ".bridge/results_execute/" + execution_id + ".json"):
        raise ValueError("result_identity_mismatch")
    if (record.get("terminal") or record.get("submission_state") not in {"accepted", "submission_started"}
            or record.get("execution_state") not in {"pending", "running"}
            or record.get("approval_state") != "granted"):
        raise ValueError("not_claimable")
    assessment = assess(workspace, str(record["manifest_task_id"]), check_workspace=False)
    baseline, current = verify_evidence(workspace, record, assessment, require_claim=True, registry=registry)
    document = assessment["document"]
    allowed = document["allowed_files"]
    delta = (changes_since_baseline(workspace.root, baseline, current) if workspace.git_gate == "advisory"
             else execution_delta(baseline, current))
    from grokbot_mcp.execution.service import _blocked
    if any(_blocked(p, set(workspace.forbidden_files), advisory=workspace.git_gate == "advisory") for p in delta):
        raise ValueError("scope_violation")
    if workspace.git_gate == "strict" and any(p not in allowed for p in delta):
        raise ValueError("scope_violation")
    command = workspace.command(document["test_command_id"])
    selected = [p for p in delta if p in allowed]
    argv = changed_files_argv(command, selected, allowed)
    from grokbot_mcp.security.targets import resolve_exact_files
    resolve_exact_files(workspace.root, selected, workspace.forbidden_files)
    return {"ok": True, "command_id": command.command_id, "argv": argv, "shell": False,
            "executed": False, "webhook_sent": False, "ledger_modified": False}


def main(argv: list[str] | None = None) -> int:
    parser = Parser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    parser.add_argument("--workspace", required=True)
    parser.add_argument("execution_id")
    try:
        args = parser.parse_args(argv)
        emit(preview(args.registry, args.workspace, args.execution_id))
        return 0
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
