"""Reviewable local binding migration. Remote edits and live acceptance are separate."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
import uuid

from grokbot_mcp import file_transaction
from grokbot_mcp.client_config import inspect_installation, workspace_checks
from grokbot_mcp.identity import TEMPLATE_VERSION, digest, path_id
from grokbot_mcp.local_safety import LocalError, atomic_replace, create_new, current_hash, json_bytes, read_document, safe_path, local_lock
from grokbot_mcp.registry import load_registry, policy_fingerprint
from grokbot_mcp.routine import confirmation, read_routine
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.storage import JsonLedger, utc_now

STATE_DIRS = ("task_specs", "tasks_v2", "results_v2", "executions", "execution_baselines", "execution_claims", "results_execute")
_BINDING_KEYS = {"schema_version", "revision", "mode", "workspace_id", "workspace_root", "action", "policy_revision",
                 "policy_fingerprint", "claim_entry", "registry_path", "protocol_capabilities", "reuse_scope",
                 "template_version", "verified_at", "verification_source", "claim_interpreter", "expected_installation_id",
                 "result_contract_version"}


def unresolved_tasks(workspace) -> list[str]:
    found = []
    for directory, key, unresolved in (
        ("executions", "execution_state", {"prepared", "pending", "running", "timeout", "unknown_submission"}),
        ("tasks_v2", "task_state", {"created", "accepted", "pending", "running", "timeout", "submission_started", "unknown_submission"}),
    ):
        folder = workspace.root / ".bridge" / directory
        safe_path(folder)
        for path in folder.glob("*.json"):
            record, _ = read_document(path)
            if (record.get(key) in unresolved or record.get("submission_state") in {"submission_started", "unknown_submission"}
                    or key not in record):
                found.append(directory + "/" + path.name)
    return sorted(found)


def state_snapshot(workspace) -> dict:
    result = {}
    for directory in STATE_DIRS:
        folder = workspace.root / ".bridge" / directory
        safe_path(folder)
        for path in sorted(folder.glob("*")):
            safe_path(path)
            if path.is_file() and path.name != ".submit.lock":
                result[directory + "/" + path.name] = current_hash(path)
    return result


def _targets(workspace) -> dict[str, Path]:
    return {phase: workspace.root / ".bridge" / (phase + "_routine_status.json") for phase in ("validate", "execute")}


def _snapshot_binding(path: Path) -> dict | None:
    if not path.exists():
        return None
    data, _ = read_document(path)
    # Refuse unknown payload-bearing fields rather than copying possible credentials.
    if set(data) - {"routine_status", "routine_status_source", "routine_status_verified_at", "binding"}:
        raise LocalError("migration_binding_snapshot_unsupported")
    binding = data.get("binding")
    if binding is not None and (type(binding) is not dict or set(binding) - _BINDING_KEYS):
        raise LocalError("migration_binding_snapshot_unsupported")
    return data


def _profile(workspace, *, local_details=False) -> dict:
    value = asdict(workspace)
    value["root_id"] = path_id("workspace-root", workspace.root)
    value.pop("root")
    if not local_details:
        for key in ("validate_credential_target", "execute_credential_target", "expected_machine"):
            value.pop(key, None)
    if local_details:
        value["root"] = str(workspace.root)
    value["policy_fingerprint"] = policy_fingerprint(workspace)
    return value


def _candidate(workspace, phase, registry, python, install, previous, *, pending):
    old = previous.get("binding", {}) if previous else {}
    if not isinstance(old, dict):
        old = {}
    revision = old.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise LocalError("binding_invalid")
    value = confirmation(workspace, phase, registry, mode="migrate", revision=revision or 1,
                         verified_at=utc_now(), legacy=False)
    value["binding"].update(template_version=TEMPLATE_VERSION, claim_interpreter=str(python),
        expected_installation_id=install["installation_id"], result_contract_version=1,
        protocol_capabilities=["manifest-v1", "claim-once-v1", "claim-evidence-v2", "result-contract-v1"])
    keys = set(value["binding"]) - {"verified_at", "revision"}
    changed = any(old.get(k) != value["binding"][k] for k in keys)
    if changed:
        value["binding"]["revision"] = revision + 1
    if pending:
        value["routine_status"] = "unknown"
        value["binding"]["verification_source"] = "pending_administrator_review"
    return value, changed


def plan(registry: Path, workspace_id: str, python: Path, *, target_registry: Path | None = None,
         local_details: bool = False) -> dict:
    target_registry = target_registry or registry
    install = inspect_installation(python)
    safe_path(registry, regular=True)
    current = load_registry(registry).get(workspace_id)
    target, codes = workspace_checks(target_registry, workspace_id)
    if current is None:
        raise LocalError("unknown_workspace")
    # Binding/old-claim failures are WHY migration is needed; local policy failures block it.
    codes = [code for code in codes if not code.startswith("binding_") and code != "migration_pending"]
    if current.root.resolve() != target.root.resolve():
        codes.append("migration_workspace_move_forbidden")
    if current.expected_head != target.expected_head:
        codes.append("migration_head_change_forbidden")
    if not set(target.allowed_files).issubset(current.allowed_files):
        codes.append("migration_scope_expansion_forbidden")
    if not set(current.forbidden_files).issubset(target.forbidden_files) or current.protected_file != target.protected_file:
        codes.append("migration_protection_change_forbidden")
    if current.execution_profile != target.execution_profile:
        # This migration moves bindings, not the authority of in-flight tasks.
        codes.append("migration_execution_profile_change_forbidden")
    if current.writes_enabled != target.writes_enabled:
        codes.append("migration_write_permission_change_forbidden")
    pending = unresolved_tasks(current)
    if pending:
        codes.append("binding_change_has_unresolved_tasks")
    marker = current.root / ".bridge/deployment_migration.json"
    if marker.exists():
        state, _ = read_document(marker)
        if state.get("state") not in {"completed", "rolled_back"}:
            codes.append("migration_pending")
    bindings = {key: _snapshot_binding(path) for key, path in _targets(current).items()}
    changed = any(_candidate(target, phase, target_registry, python, install, old, pending=False)[1]
                  for phase, old in bindings.items())
    snapshot = state_snapshot(current)
    basis = {"registry_id": path_id("registry", registry), "target_registry_id": path_id("registry", target_registry),
             "registry_sha256": current_hash(registry), "target_registry_sha256": current_hash(target_registry),
             "workspace_id": workspace_id, "installation_id": install["installation_id"],
             "installation_source_sha256": install["source_sha256"],
             "binding_sha256": {key: current_hash(path) for key, path in _targets(current).items()},
             "state_sha256": digest(json_bytes(snapshot)), "marker_sha256": current_hash(marker)}
    return {"ok": not codes, "plan_sha256": digest(json_bytes(basis)), **basis,
            "current_bindings": {phase: {
                "revision": (value or {}).get("binding", {}).get("revision") if isinstance((value or {}).get("binding"), dict) else None,
                "routine_status": (value or {}).get("routine_status"),
                "capabilities": (value or {}).get("binding", {}).get("protocol_capabilities") if isinstance((value or {}).get("binding"), dict) else None,
                "claim_entry": (value or {}).get("binding", {}).get("claim_entry") if isinstance((value or {}).get("binding"), dict) else None,
            } for phase, value in bindings.items()},
            "no_change": not changed, "current": _profile(current, local_details=local_details),
            "target": _profile(target, local_details=local_details), "blocking_reasons": sorted(set(codes)),
            "unresolved_count": len(pending), "claim_entry": "grokbot_mcp.claim_cli --workspace " + workspace_id,
            "claim_interpreter_id": path_id("python", python), "template_version": TEMPLATE_VERSION,
            "target_capabilities": ["manifest-v1", "claim-once-v1", "claim-evidence-v2", "result-contract-v1"],
            "local_configuration_applied": False, "administrator_remote_review_confirmed": False,
            "remote_binding_verified": False, "live_execution_verified": False, "qa_accepted": False,
            "registry_modified": False, "credentials_copied": False,
            **({"local_paths": {"registry": str(registry), "target_registry": str(target_registry), "claim_python": str(python)}} if local_details else {}),
            "next_step": "Retain the existing Routine/Webhooks. Apply the reviewed local plan, update the remote Routine manually using its packet, then explicitly finalize the administrator attestation. No remote readback API is claimed."}


def _folder(registry: Path, workspace_id: str, transaction: str) -> Path:
    if not is_uuid(transaction):
        raise LocalError("transaction_id_invalid")
    # workspace_id is checked via the registry before this helper is called.
    folder = registry.parent / "migrations" / workspace_id / transaction
    safe_path(folder)
    for ws in load_registry(registry).values():
        if folder.resolve().is_relative_to(ws.root.resolve()):
            raise LocalError("migration_backup_inside_workspace")
    return folder


def _locks(workspace, registry, stack):
    stack.enter_context(local_lock(registry.parent / ".grokbot-migration.lock"))
    for directory in ("tasks_v2", "executions"):
        stack.enter_context(JsonLedger(workspace.root / ".bridge" / directory).locked())


def _packet(profile: dict, python: Path, registry: Path) -> str:
    from grokbot_mcp.materials import render_routine
    text = "# Local migration packet / 本地迁移材料\n\nKeep the existing Routine and its Webhook. No remote API or live execution has been verified.\n"
    text += "\nReview workspace, allowed files, protected files and baseline:\n```json\n" + json_bytes(profile).decode() + "```\n"
    text += "\nUse this interpreter and explicit registry (local private paths; do not publish):\n```json\n" + json_bytes({"python": str(python), "registry": str(registry)}).decode() + "```\n"
    for phase in ("validate", "execute"):
        text += "\n" + render_routine(phase, workspace_root=Path(profile["root"]),
            workspace_id=profile["workspace_id"], registry=registry, python=python, mode="migrate")
    text += "\n## Manual remote readback / 人工回读\nVerify both existing Routine identities; exact root/ID, baseline, whitelist/protected files, interpreter/registry, claim-evidence-v2 and result-contract-v1. Do not paste keys into this file.\n"
    text += "Local finalize is an administrator attestation, NOT remote API verification. Live acceptance needs separate authorization. Never rerun an uncertain task or retrofit claim hashes.\n"
    return text


def apply_plan(registry: Path, workspace_id: str, python: Path, *, target_registry: Path | None = None, confirm: str) -> dict:
    target_registry = target_registry or registry
    reviewed = plan(registry, workspace_id, python, target_registry=target_registry)
    if reviewed["plan_sha256"] != confirm:
        raise LocalError("plan_confirmation_mismatch")
    if not reviewed["ok"]:
        raise LocalError("migration_blocked")
    if reviewed["no_change"]:
        return {**reviewed, "transaction_id": None}
    current = load_registry(registry)[workspace_id]
    target = load_registry(target_registry)[workspace_id]
    with ExitStack() as stack:
        _locks(current, registry, stack)
        fresh = plan(registry, workspace_id, python, target_registry=target_registry)
        if not fresh["ok"] or fresh["plan_sha256"] != confirm:
            raise LocalError("input_changed")
        transaction = str(uuid.uuid4())
        folder = _folder(registry, workspace_id, transaction)
        install = inspect_installation(python)
        desired = {phase: (path, json_bytes(_candidate(target, phase, target_registry, python, install,
                    _snapshot_binding(path), pending=True)[0])) for phase, path in _targets(current).items()}
        context = {"plan_sha256": confirm, "registry_id": path_id("registry", registry), "target_registry_id": path_id("registry", target_registry),
                   "registry_sha256": current_hash(registry), "target_registry_sha256": current_hash(target_registry),
                   "workspace_id": workspace_id, "state_snapshot": state_snapshot(current), "previous_marker_sha256": reviewed["marker_sha256"], "target_python": str(python),
                   "installation_id": install["installation_id"], "installation_source_sha256": install["source_sha256"], "target_profile": _profile(target, local_details=True)}
        file_transaction.prepare(folder, desired, context, attachments={
            "profile-snapshots.json": json_bytes({"current": _profile(current, local_details=True), "target": _profile(target, local_details=True)}),
            "routine-update.md": _packet(context["target_profile"], python, target_registry).encode("utf-8")})
        marker = current.root / ".bridge/deployment_migration.json"
        atomic_replace(marker, json_bytes({"schema_version": 1, "transaction_id": transaction, "state": "pending_remote_review"}), expected=current_hash(marker))
        file_transaction.apply(folder, _targets(current))
    return {**reviewed, "local_configuration_applied": True, "transaction_id": transaction,
            "remote_state": "pending_manual_readback", "binding_submit_ready": False}


def transaction_plan(registry: Path, workspace_id: str, transaction: str, *, target_registry: Path | None = None,
                     operation: str = "recover") -> dict:
    if operation not in {"recover", "rollback", "finalize"}:
        raise LocalError("invalid_arguments")
    target_registry = target_registry or registry
    workspace = load_registry(registry)[workspace_id]
    folder = _folder(registry, workspace_id, transaction)
    receipt, receipt_hash = file_transaction.read_receipt(folder, _targets(workspace))
    context = receipt["context"]
    expected = {"registry_id": path_id("registry", registry), "target_registry_id": path_id("registry", target_registry),
                "registry_sha256": current_hash(registry), "target_registry_sha256": current_hash(target_registry), "workspace_id": workspace_id}
    if any(context.get(k) != v for k, v in expected.items()):
        raise LocalError("migration_inputs_changed")
    installation = inspect_installation(Path(context["target_python"]))
    if (installation["installation_id"] != context.get("installation_id")
            or installation["source_sha256"] != context.get("installation_source_sha256")):
        raise LocalError("migration_installation_changed")
    if unresolved_tasks(workspace):
        raise LocalError("binding_change_has_unresolved_tasks")
    if state_snapshot(workspace) != context["state_snapshot"]:
        raise LocalError("migration_new_evidence_present")
    marker_path = workspace.root / ".bridge/deployment_migration.json"
    marker_hash = current_hash(marker_path)
    marker = read_document(marker_path)[0] if marker_hash is not None else {}
    if marker.get("transaction_id") != transaction:
        if (receipt["state"] == "prepared" and marker_hash == context.get("previous_marker_sha256")
                and all(current_hash(path) == receipt["files"][key]["before"] for key, path in _targets(workspace).items())):
            marker = {"state": "prepared"}  # No binding was written before the marker.
        else:
            raise LocalError("migration_superseded")
    if marker.get("state") == "rolled_back" and operation != "rollback":
        raise LocalError("migration_already_rolled_back")
    final_hash = None
    final = None
    if (folder / "finalize/receipt.json").exists():
        final, final_hash = file_transaction.read_receipt(folder / "finalize", _targets(workspace))
    for key, path in _targets(workspace).items():
        allowed = set(receipt["files"][key].values())
        if final:
            allowed.update(final["files"][key].values())
        if current_hash(path) not in allowed:
            raise LocalError("transaction_target_changed")
    basis = {"transaction_id": transaction, "operation": operation, "receipt_sha256": receipt_hash,
             "final_receipt_sha256": final_hash, "marker_sha256": marker_hash,
             "current_binding_sha256": {k: current_hash(v) for k, v in _targets(workspace).items()}}
    return {**basis, "plan_sha256": digest(json_bytes(basis)), "ok": True, "state": marker["state"],
            "remote_binding_verified": False, "live_execution_verified": False, "qa_accepted": False}


def finish(registry: Path, workspace_id: str, transaction: str, *, operation: str, confirm: str,
           target_registry: Path | None = None, remote_review_confirmed: bool = False, routine_status: str = "enabled") -> dict:
    target_registry = target_registry or registry
    reviewed = transaction_plan(registry, workspace_id, transaction, target_registry=target_registry, operation=operation)
    if reviewed["plan_sha256"] != confirm:
        raise LocalError("plan_confirmation_mismatch")
    if operation == "finalize" and (not remote_review_confirmed or routine_status not in {"enabled", "paused"}):
        raise LocalError("migration_remote_review_required")
    workspace = load_registry(registry)[workspace_id]
    folder = _folder(registry, workspace_id, transaction)
    targets = _targets(workspace)
    with ExitStack() as stack:
        _locks(workspace, registry, stack)
        fresh = transaction_plan(registry, workspace_id, transaction, target_registry=target_registry, operation=operation)
        if fresh["plan_sha256"] != confirm:
            raise LocalError("input_changed")
        receipt, _ = file_transaction.read_receipt(folder, targets)
        if fresh["state"] == "prepared":
            marker_path = workspace.root / ".bridge/deployment_migration.json"
            atomic_replace(marker_path, json_bytes({"schema_version": 1, "transaction_id": transaction,
                           "state": "pending_remote_review"}), expected=current_hash(marker_path))
        final_folder = folder / "finalize"
        if operation == "rollback":
            if (final_folder / "receipt.json").exists():
                file_transaction.apply(final_folder, targets, rollback=True)
            file_transaction.apply(folder, targets, rollback=True)
            state = "rolled_back"
        else:
            if receipt["state"] == "rolled_back":
                raise LocalError("migration_already_rolled_back")
            if not (final_folder / "receipt.json").exists():
                file_transaction.apply(folder, targets)
            if operation == "finalize" and not final_folder.exists():
                context = receipt["context"]
                python = Path(context["target_python"])
                install = inspect_installation(python)
                if install["installation_id"] != context["installation_id"]:
                    raise LocalError("migration_installation_changed")
                desired = {}
                for phase, path in targets.items():
                    value, _ = read_document(path)
                    value["routine_status"] = routine_status
                    value["routine_status_verified_at"] = utc_now()
                    value["binding"]["verification_source"] = "administrator_confirmed"
                    value["binding"]["verified_at"] = utc_now()
                    desired[phase] = (path, json_bytes(value))
                file_transaction.prepare(final_folder, desired, {"remote_review_attested": True, "source": "administrator", "live_execution_verified": False})
            if (final_folder / "receipt.json").exists():
                final, _ = file_transaction.read_receipt(final_folder, targets)
                if final["context"].get("remote_review_attested") is not True:
                    raise LocalError("migration_remote_review_required")
                file_transaction.apply(final_folder, targets)
                state = "completed"
            else:
                state = "pending_remote_review"
        marker = workspace.root / ".bridge/deployment_migration.json"
        atomic_replace(marker, json_bytes({"schema_version": 1, "transaction_id": transaction, "state": state}), expected=current_hash(marker))
    return {**reviewed, "state": state, "local_configuration_applied": state != "rolled_back",
            "administrator_remote_review_confirmed": state == "completed", "remote_binding_verified": False,
            "live_execution_verified": False, "qa_accepted": False}
