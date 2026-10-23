"""Explicit local recovery. Immutable intent, move-only evidence, permanent IDs.

The journal is a refusal barrier, not a replacement ledger. Interrupted moves
remain blocking and can only resume the same hash-bound plan under both locks.
"""
from __future__ import annotations

import getpass
import os
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from grokbot_mcp.local_safety import LocalError, safe_path, read_document, create_new, json_bytes, digest
from grokbot_mcp.registry import load_registry
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.storage import JsonLedger, utc_now


def _journals(root: Path):
    folder = root / ".bridge/ledger_archives"
    safe_path(folder)
    if folder.exists():
        for path in sorted(folder.glob("*.json")):
            data, raw = read_document(path)
            task = data.get("task_id")
            ids = data.get("execution_ids")
            if (type(task) is not str or not is_uuid(task) or path.stem != task
                    or not isinstance(ids, list) or any(type(i) is not str or not is_uuid(i) for i in ids)):
                raise LocalError("archive_journal_invalid")
            yield path, data, raw


def archived(root: Path, identity: str, *, execution: bool = False) -> bool:
    return any(identity in data["execution_ids"] if execution else identity == data["task_id"]
               for _path, data, _raw in _journals(root))


def incomplete(root: Path) -> bool:
    for path, _data, raw in _journals(root):
        completed = path.with_suffix(".complete")
        safe_path(completed)
        if not completed.exists():
            return True
        done, _ = read_document(completed)
        if done.get("journal_sha256") != digest(raw):
            raise LocalError("archive_journal_invalid")
    return False


def _plan(registry: Path, workspace, task_id: str, reason: str, accept_risk: bool) -> dict:
    if workspace.root.drive.casefold() != registry.drive.casefold():
        raise LocalError("archive_cross_volume_unsupported")
    bridge = workspace.root / ".bridge"
    spec, spec_raw = read_document(bridge / "task_specs" / (task_id + ".json"))
    record, raw = read_document(bridge / "tasks_v2" / (task_id + ".json"))
    if (record.get("task_id") != task_id or spec.get("task_id") != task_id
            or spec.get("workspace_id") != workspace.workspace_id
            or record.get("manifest_sha256") != digest(spec_raw)):
        raise LocalError("archive_identity_mismatch")
    files = [{"path": "task_specs/" + task_id + ".json", "sha256": digest(spec_raw)},
             {"path": "tasks_v2/" + task_id + ".json", "sha256": digest(raw)}]
    states = [{"task_id": task_id, "state": record.get("task_state"),
               "terminal": record.get("terminal") is True and record.get("task_state") in {"succeeded", "failed"}}]
    ids = []
    executions = bridge / "executions"
    safe_path(executions)
    if executions.exists():
        for path in sorted(executions.glob("*.json")):
            item, item_raw = read_document(path)
            if item.get("manifest_task_id") != task_id:
                continue
            identity = item.get("execution_id")
            if (type(identity) is not str or not is_uuid(identity) or path.stem != identity
                    or item.get("manifest_sha256") != digest(spec_raw)):
                raise LocalError("archive_identity_mismatch")
            ids.append(identity)
            files.append({"path": "executions/" + identity + ".json", "sha256": digest(item_raw)})
            states.append({"execution_id": identity, "state": item.get("execution_state"),
                           "terminal": item.get("terminal") is True and item.get("execution_state") in {"succeeded", "failed", "cancelled"}})
    risk = any(not item["terminal"] for item in states)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return {"schema_version": 1, "workspace_id": workspace.workspace_id, "task_id": task_id,
            "execution_ids": ids, "reason": reason, "operator": getpass.getuser(), "created_at": utc_now(),
            "states": states, "files": files, "late_result_risk": risk,
            "accept_late_result_risk": accept_risk,
            "archive_path": str(registry.parent / "archive" / workspace.workspace_id / (stamp + "_" + task_id))}


def _report(plan: dict, *, confirmed: bool) -> dict:
    return {"ok": True, "dry_run": not confirmed, "archived": confirmed,
            "task_id": plan["task_id"], "execution_ids": plan["execution_ids"],
            "archive_path": plan["archive_path"], "moves": plan["files"],
            "late_result_risk": plan["late_result_risk"],
            "confirmation_required": not confirmed,
            "risk_acceptance_required": plan["late_result_risk"] and not plan["accept_late_result_risk"],
            "results_and_claims_preserved_in_place": True,
            "webhook_sent": False, "send_authorized": False, "qa_accepted": False}


def archive(registry: Path, workspace_id: str, task_id: str, *, reason: str,
            confirm: bool = False, accept_late_result_risk: bool = False) -> dict:
    registry = registry.absolute()
    if not isinstance(task_id, str) or not is_uuid(task_id):
        raise LocalError("invalid_task_id")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000 or "\0" in reason:
        raise LocalError("archive_reason_required")
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    bridge = workspace.root / ".bridge"
    journal = bridge / "ledger_archives" / (task_id + ".json")
    safe_path(journal)
    # Preview is truly read-only, including lock directories.
    if not confirm and not journal.exists():
        return _report(_plan(registry, workspace, task_id, reason, accept_late_result_risk), confirmed=False)
    with ExitStack() as stack:
        if confirm:
            # Same lock order as deployment migration.
            for kind in ("tasks_v2", "executions"):
                safe_path(bridge / kind)
                stack.enter_context(JsonLedger(bridge / kind).locked())
            if load_registry(registry).get(workspace_id) != workspace:
                raise LocalError("input_changed")
        if journal.exists():
            marker, marker_raw = read_document(journal)
            folder = Path(marker["archive_path"])
            if folder.parent != registry.parent / "archive" / workspace_id or not folder.name.endswith("_" + task_id):
                raise LocalError("archive_journal_invalid")
            plan, audit_raw = read_document(folder / "audit.json")
            if (digest(audit_raw) != marker.get("audit_sha256") or plan.get("task_id") != task_id
                    or plan.get("workspace_id") != workspace_id or plan.get("execution_ids") != marker.get("execution_ids")):
                raise LocalError("archive_journal_invalid")
            if not confirm:
                return _report(plan, confirmed=False)
            if journal.with_suffix(".complete").exists():
                raise LocalError("archived_task")
        else:
            plan = _plan(registry, workspace, task_id, reason, accept_late_result_risk)
            folder = Path(plan["archive_path"])
            marker_raw = None
        if plan["late_result_risk"] and not accept_late_result_risk:
            raise LocalError("archive_late_result_risk")
        safe_path(folder)
        if marker_raw is None:
            folder.mkdir(parents=True, exist_ok=False)
            audit_raw = json_bytes(plan)
            create_new(folder / "audit.json", audit_raw)
            marker = {"task_id": task_id, "execution_ids": plan["execution_ids"],
                      "archive_path": str(folder), "audit_sha256": digest(audit_raw)}
            journal.parent.mkdir(parents=True, exist_ok=True)
            marker_raw = json_bytes(marker)
            create_new(journal, marker_raw)
        # Once the journal exists, all old IDs are refused even if a move fails.
        permitted = {"task_specs/" + task_id + ".json", "tasks_v2/" + task_id + ".json"}
        permitted.update("executions/" + identity + ".json" for identity in plan["execution_ids"])
        if {item["path"] for item in plan["files"]} != permitted:
            raise LocalError("archive_journal_invalid")
        for item in plan["files"]:
            source, target = bridge / item["path"], folder / item["path"]
            safe_path(source)
            safe_path(target)
            if target.exists():
                if source.exists() or digest(read_document(target)[1]) != item["sha256"]:
                    raise LocalError("archive_evidence_changed")
                continue  # Resume only a previously verified move.
            if digest(read_document(source)[1]) != item["sha256"]:
                raise LocalError("archive_evidence_changed")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Windows rename never replaces a destination. Cross-volume moves
            # are refused: no copy/delete fallback and no evidence loss.
            os.rename(source, target)
            if digest(read_document(target)[1]) != item["sha256"]:
                raise LocalError("archive_evidence_changed")
        completion = {"completed_at": utc_now(), "journal_sha256": digest(marker_raw)}
        receipt = folder / "completed.json"
        if not receipt.exists():
            create_new(receipt, json_bytes(completion))
        elif read_document(receipt)[0].get("journal_sha256") != digest(marker_raw):
            raise LocalError("archive_journal_invalid")
        create_new(journal.with_suffix(".complete"), json_bytes(completion))
        return _report(plan, confirmed=True)
