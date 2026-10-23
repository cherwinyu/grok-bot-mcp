"""Explicit local test reruns. Never reconcile, resend, or replace frozen evidence."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path

from grokbot_mcp.execution.service import _from_record, _interpret, assess
from grokbot_mcp.local_safety import json_bytes, safe_path
from grokbot_mcp.observation import LedgerView, read_result
from grokbot_mcp.rerun_config import resolve_cwd
from grokbot_mcp.result_contract import output_evidence, result_identity, validate_result
from grokbot_mcp.security.baseline import same_snapshot
from grokbot_mcp.security.paths import PathRejected, is_uuid
from grokbot_mcp.security.targets import TargetRejected, resolve_exact_files
from grokbot_mcp.storage import JsonLedger, read_json, utc_now
from grokbot_mcp.test_commands import changed_files_argv


def _inputs(workspace, execution_id):
    if type(execution_id) is not str or not is_uuid(execution_id):
        raise ValueError("invalid_execution_id")
    store = JsonLedger(workspace.root / ".bridge/executions")
    try:
        record = store.read(execution_id)
    except ValueError as exc:
        if str(exc) == "missing":
            raise ValueError("missing_or_unreadable_execution") from exc
        raise
    if record.get("terminal") is not True:
        raise ValueError("execution_not_terminal")
    if (record.get("execution_id") != execution_id or record.get("action") != "execute_manifest"
            or ("workspace_id" in record and record["workspace_id"] != workspace.workspace_id)
            or record.get("execution_result_path") != ".bridge/results_execute/" + execution_id + ".json"):
        raise ValueError("result_identity_mismatch")
    parsed, digest, observation = read_result(workspace.root / ".bridge/results_execute" / (execution_id + ".json"))
    if observation == "missing":
        raise ValueError("result_missing")
    if parsed is None:
        raise ValueError("result_unreadable")
    if not record.get("result_sha256") or not record.get("verified_at"):
        raise ValueError("rerun_result_unverified")
    if digest != record["result_sha256"]:
        raise ValueError("result_changed")
    validate_result(parsed, "execute", expected=result_identity(workspace, "execute", record))
    if parsed.get("status") not in {"succeeded", "failed"}:
        raise ValueError("rerun_result_unverified")
    # Replay the CURRENT settlement checks in a memory-only ledger view. This
    # neither archives old code nor changes a frozen failure to a success.
    view = LedgerView(store, record, False)
    verified = _interpret(view, workspace, record, _from_record(record, error=record.get("error")), parsed, digest)
    if (verified.get("status") not in {"succeeded", "failed", "execution_failed"}
            or verified.get("status") != record.get("execution_state")
            or verified.get("error") not in {None, "test_failed", "execution_failed"}):
        raise ValueError("rerun_result_unverified")
    if isinstance(record.get("verified_snapshot"), dict) and not same_snapshot(
            record["verified_snapshot"], view.record.get("verified_snapshot", {})):
        raise ValueError("rerun_result_unverified")
    assessment = assess(workspace, str(record["manifest_task_id"]), check_workspace=False)
    if assessment.get("ok") is not True:
        raise ValueError("rerun_result_unverified")
    document = assessment["document"]
    command = workspace.command(document["test_command_id"])
    if command is None or command.rerun is None:
        raise ValueError("rerun_not_configured")
    config = command.rerun
    argv = list(config.argv)
    if "{changed_files}" in argv:
        # The measured delta, not a caller-supplied command or file list.
        changed = verified["changed_files"]
        argv = changed_files_argv(config, changed, document["allowed_files"])
        resolve_exact_files(workspace.root, changed, workspace.forbidden_files)
    cwd = resolve_cwd(workspace.root, config.cwd, workspace.forbidden_files, workspace.protected_file)
    return record, parsed, digest, command, argv, cwd


def _append(directory: Path, execution_id: str, evidence: dict) -> dict:
    safe_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    safe_path(directory)
    # Stage in the same directory; publish atomically without replacement.
    # Windows rename refuses an existing destination. POSIX link is exclusive.
    highest = 0
    pattern = re.compile(re.escape(execution_id) + r"\.([1-9][0-9]*)\.json\Z")
    for path in directory.iterdir():
        match = pattern.fullmatch(path.name)
        if match:
            highest = max(highest, int(match[1]))
    fd, name = tempfile.mkstemp(prefix=".rerun-", suffix=".tmp", dir=directory)
    temporary = Path(name)
    try:
        os.close(fd)
        number = highest + 1
        while True:
            target = directory / f"{execution_id}.{number}.json"
            safe_path(target)
            document = {**evidence, "run_number": number,
                        "evidence_path": ".bridge/bridge_test_runs/" + target.name}
            with temporary.open("wb") as stream:
                stream.write(json_bytes(document))
                stream.flush()
                os.fsync(stream.fileno())
            safe_path(directory)
            try:
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
                return document
            except FileExistsError:
                number += 1
    finally:
        temporary.unlink(missing_ok=True)


def compact(evidence: dict) -> dict:
    """Bound transport output by UTF-8 bytes, retaining whole Unicode scalars."""
    result = dict(evidence)
    if "output" in result:
        output = dict(result["output"])
        head = output["head"].encode("utf-8")
        tail = output["tail"].encode("utf-8")
        output.update(head=head[:2048].decode("utf-8", errors="ignore"),
                      tail=tail[-2048:].decode("utf-8", errors="ignore"),
                      truncated=output["truncated"] or len(head) > 2048 or len(tail) > 2048)
        result["output"] = output
    return result


def rerun(workspace, execution_id: str) -> dict:
    """Run only the registered local command for an already settled execution."""
    from grokbot_mcp.local_cli import failure
    executed = False
    try:
        record, parsed, digest, command, argv, cwd = _inputs(workspace, execution_id)
        # Refuse an unsafe evidence destination before starting any process.
        directory = workspace.root / ".bridge/bridge_test_runs"
        safe_path(directory)
        started = time.monotonic()
        from grokbot_mcp.rerun_process import run
        outcome = run(argv, cwd=cwd, env={**os.environ, **dict(command.rerun.env)},
                      timeout=command.rerun.timeout_seconds)
        executed = outcome["executed"]
        duration = max(0, round((time.monotonic() - started) * 1000))
        raw = outcome.pop("raw")
        try:
            output = output_evidence(raw)
        except UnicodeDecodeError:
            output = {**output_evidence(raw.decode("utf-8", errors="replace").encode("utf-8")),
                      "output_not_utf8": True, "raw_sha256": hashlib.sha256(raw).hexdigest()}
        tests = parsed["tests"]
        actual = {**outcome, **output}
        agreement = {key: ("unreported" if key not in tests else
                           "match" if tests[key] == actual.get(key) else "mismatch")
                     for key in ("exit_code", "output_sha256")}
        # Detect concurrent replacement without writing either original file.
        try:
            fresh_record = JsonLedger(workspace.root / ".bridge/executions").read(execution_id)
            _, fresh_digest = read_json(workspace.root / ".bridge/results_execute" / (execution_id + ".json"))
            unchanged = record == fresh_record and fresh_digest == digest
        except (OSError, ValueError):
            unchanged = False
        evidence = {"ok": outcome.get("error") is None and unchanged, **actual,
                    "execution_id": execution_id, "workspace_id": workspace.workspace_id,
                    "command_id": command.command_id, "result_sha256": digest,
                    "recorded_at": utc_now(), "duration_ms": duration, "agreement": agreement,
                    "source_evidence_unchanged": unchanged, "shell": False,
                    "webhook_sent": False, "ledger_modified": False, "qa_accepted": False}
        if not unchanged:
            evidence["error"] = "rerun_source_changed"
        return _append(directory, execution_id, evidence)
    except (OSError, ValueError, PathRejected, TargetRejected) as exc:
        if isinstance(exc, (PathRejected, TargetRejected)):
            exc = ValueError(exc.code)
        result = failure(exc)
        return {**result, "error": result["diagnostic"]["code"], "executed": executed,
                "ledger_modified": False}
