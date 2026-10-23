"""Seven tool operations. None of them accept a workspace root or a webhook URL."""

from __future__ import annotations

from pathlib import Path
from grokbot_mcp.tool_errors import guarded

from grokbot_mcp.identity import runtime_identity, runtime_drift
from grokbot_mcp.execution_profile import report as environment_report

from grokbot_mcp.credentials.backend import load_for, status_for
from grokbot_mcp.execution.service import claim_execution, get_execution_status, prepare_execution, submit_execution
from grokbot_mcp.manifest.store import ManifestError, prepare_manifest
from grokbot_mcp.registry import RegistryError, Workspace, default_registry_path, execution_switch_enabled, load_registry
from grokbot_mcp.security.gitcheck import git_error, git_snapshot, workspace_git_rejection
from grokbot_mcp.transport.webhook import post_once
from grokbot_mcp.routine import routine_readiness, public_binding
from grokbot_mcp.security.targets import TargetRejected, resolve_exact_files
from grokbot_mcp.validation.service import get_validation_status, submit_validation

_TRANSPORT = None


def install_transport(transport) -> None:
    """Test hook. The MCP tools do not expose this."""
    global _TRANSPORT
    _TRANSPORT = transport


def _send(url: str, key: str, payload: dict[str, object], timeout: float):
    if _TRANSPORT is not None:
        return _TRANSPORT(url, key, payload, timeout)
    return post_once(url, key, payload, timeout, enabled=True)


def _closed(error: str, workspace_id: str | None = None) -> dict[str, object]:
    return {
        "ok": False,
        "runtime_identity": runtime_identity(workspace_id),
        "error": error,
        "workspace_id": workspace_id,
        "webhook_sent": False,
        "webhook_attempted": False,
        "execution_enabled": execution_switch_enabled(),
        "qa_accepted": False,
    }


def _open() -> tuple[dict[str, Workspace], Path]:
    path = default_registry_path()
    return load_registry(path), path


def _workspace(workspace_id: str) -> tuple[Workspace | None, dict[str, object] | None, list[Path]]:
    try:
        registry, _path = _open()
    except RegistryError as exc:
        return None, _closed(exc.code, workspace_id), []
    workspace = registry.get(workspace_id)
    if workspace is None:
        return None, _closed("unknown_workspace", workspace_id), []
    rejection = workspace_git_rejection(workspace.root)
    if rejection:
        return None, _closed(rejection, workspace_id), []
    siblings = [item.root for item_id, item in registry.items() if item_id != workspace.workspace_id]
    return workspace, None, siblings


@guarded
def list_workspaces() -> dict[str, object]:
    path = default_registry_path()
    try:
        registry = load_registry(path)
    except RegistryError as exc:
        # Hosts must not treat a missing registry as "zero workspaces registered".
        return {
            "ok": False,
            "runtime_identity": runtime_identity(),
            "workspaces": None,
            "error": exc.code,
            "registry_path": str(path),
            "message": "The registry could not be loaded. An empty workspace list is not returned.",
            "webhook_sent": False,
            "execution_enabled": False,
        }
    items = []
    for workspace in registry.values():
        item = {
                "workspace_id": workspace.workspace_id,
                "expected_head": workspace.expected_head,
                "allowed_files": list(workspace.allowed_files),
                "writes_enabled": workspace.writes_enabled,
                "execution_enabled": False,
                "credential_targets": {
                    "validate": workspace.validate_credential_target,
                    "execute": workspace.execute_credential_target,
                },
            }
        if workspace.git_gate == "advisory":
            item["git_gate"] = "advisory"
        if workspace.allowed_prefixes or workspace.prefix_ceiling is not None:
            from grokbot_mcp.security.prefixes import prefix_json
            item["allowed_prefixes"] = prefix_json(workspace)
            if workspace.prefix_ceiling is not None:
                item["prefix_ceiling"] = prefix_json(workspace, ceiling=True)
        items.append(item)
    result = {
        "ok": True,
        "runtime_identity": runtime_identity(),
        "workspaces": items,
        "webhook_sent": False,
        "execution_enabled": execution_switch_enabled(),
    }
    result["runtime_drift"] = runtime_drift(registry=path)
    result["warnings"] = ["process_restart_recommended"] if result["runtime_drift"]["package_changed"] else []
    return result


@guarded
def preflight(workspace_id: str) -> dict[str, object]:
    workspace, error, _siblings = _workspace(workspace_id)
    if error is not None or workspace is None:
        return error or _closed("unknown_workspace", workspace_id)
    git = git_snapshot(
        workspace.root, workspace.allowed_files, workspace.expected_head,
        workspace.forbidden_files if workspace.git_gate == "advisory" else (),
    )
    validate_status = status_for(workspace.validate_credential_target, (workspace.execute_credential_target,))
    execute_status = status_for(workspace.execute_credential_target, (workspace.validate_credential_target,))
    enabled = execution_switch_enabled()
    local_error = git_error(git, workspace.git_gate)
    try:
        if workspace.allowed_files or (not workspace.allowed_prefixes and workspace.prefix_ceiling is None):
            resolve_exact_files(workspace.root, list(workspace.allowed_files), workspace.forbidden_files)
    except TargetRejected as exc:
        local_error = exc.code
    local_ready = local_error is None
    bindings = {phase: routine_readiness(workspace, phase) for phase in ("validate", "execute")}
    blocking = []
    if not local_ready:
        blocking.append(str(local_error))
    if not workspace.writes_enabled:
        blocking.append("workspace_writes_disabled")
    if not enabled:
        blocking.append("execution_switch_disabled")
    per_phase = {}
    for phase, credential in (("validate", validate_status), ("execute", execute_status)):
        reasons = list(blocking)
        if credential != "present":
            reasons.append("credentials_" + credential)
        if not bindings[phase]["submit_ready"]:
            reasons.append(str(bindings[phase]["error"]))
        per_phase[phase] = {**bindings[phase], "credentials": credential,
                            "submit_permitted": not reasons, "blocking_reasons": reasons}
    confirmed = all(item["binding_confirmed"] for item in bindings.values())
    ready = all(item["submit_permitted"] for item in per_phase.values())
    result = {
        "workspace_id": workspace.workspace_id,
        "runtime_identity": runtime_identity(workspace.workspace_id),
        "execution_environment": environment_report(workspace),
        "expected_head": workspace.expected_head,
        "git_head": git.get("git_head"),
        "known_legal_changes": git.get("known_legal_changes"),
        "unexpected_changes": git.get("unexpected_changes"),
        "unconfirmed_changes": git.get("unconfirmed_changes"),
        **({"protected_or_forbidden_changes": git.get("protected_or_forbidden_changes")}
           if workspace.git_gate == "advisory" else {}),
        "credentials": execute_status,
        "validate_credentials": validate_status,
        "execute_credentials": execute_status,
        "validate_target": workspace.validate_credential_target,
        "execute_target": workspace.execute_credential_target,
        "execution_enabled": enabled,
        "writes_enabled": workspace.writes_enabled,
        "webhook_sent": False,
        "webhook_attempted": False,
        "qa_accepted": False,
        "ready": ready,
        "local_ready": local_ready,
        "binding_confirmed": confirmed,
        "remote_binding_verified": False,
        "submit_permitted": ready,
        "phases": {phase: {**entry, "binding": public_binding(workspace, entry.get("binding"))}
                   for phase, entry in per_phase.items()},
        "blocking_reasons": sorted({reason for item in per_phase.values() for reason in item["blocking_reasons"]}),
    }
    if workspace.git_gate == "advisory":
        result["git_gate"] = "advisory"
    from grokbot_mcp.execution.service import stale_execution_records
    result["stale_execution_records"] = stale_execution_records(workspace)
    result["runtime_drift"] = runtime_drift()
    result["warnings"] = ["process_restart_recommended"] if result["runtime_drift"]["package_changed"] else []
    if workspace.prefix_ceiling is not None:
        from grokbot_mcp.security.prefixes import prefix_json
        result["prefix_ceiling"] = prefix_json(workspace, ceiling=True)
        result["allowed_prefixes"] = prefix_json(workspace)
    # Optional local material summary; it never changes existing readiness keys.
    from grokbot_mcp.routine_check import check, packets
    from grokbot_mcp.local_safety import LocalError
    try:
        registry = default_registry_path()
        if any(path.exists() for path, _kind in packets(registry, workspace)):
            result["routine_check"] = check(registry, workspace_id)
    except (OSError, ValueError, RegistryError, LocalError):
        from grokbot_mcp.routine_check import SCOPE
        result["routine_check"] = {"ok": False, "issues": ["routine_packet_unreadable"],
                                   "scope": SCOPE, "remote_binding_verified": False}
    return result


@guarded
def prepare_task(
    workspace_id: str,
    task_type: str,
    task_description: str,
    allowed_files: list[str],
    acceptance_criteria: list[str],
    test_command_id: str,
    execution_timeout: int | None = None,
    baseline_commit: str | None = None,
    allowed_globs: list[str] | None = None,
    create_files: list[str] | None = None,
) -> dict[str, object]:
    workspace, error, _siblings = _workspace(workspace_id)
    if error is not None or workspace is None:
        return error or _closed("unknown_workspace", workspace_id)
    if not workspace.writes_enabled:
        return _closed("workspace_writes_disabled", workspace_id)
    git = git_snapshot(
        workspace.root, workspace.allowed_files, workspace.expected_head,
        workspace.forbidden_files if workspace.git_gate == "advisory" else (),
    )
    problem = git_error(git, workspace.git_gate)
    if problem:
        result = _closed(problem, workspace_id)
        if git.get("protected_or_forbidden_changes"):
            result["protected_or_forbidden_changes"] = git["protected_or_forbidden_changes"]
        return result
    try:
        result = prepare_manifest(
            workspace,
            task_type=task_type,
            task_description=task_description,
            allowed_files=allowed_files,
            acceptance_criteria=acceptance_criteria,
            test_command_id=test_command_id,
            execution_timeout=execution_timeout,
            baseline_commit=baseline_commit,
            effective_baseline=str(git.get("head_text") or "") if workspace.git_gate == "advisory" else None,
            **({"allowed_globs": allowed_globs} if allowed_globs is not None else {}),
            **({"create_files": create_files} if create_files is not None else {}),
        )
    except ManifestError as exc:
        return _closed(exc.code, workspace_id)
    result["execution_enabled"] = execution_switch_enabled()
    return result


@guarded
def submit_validation_tool(workspace_id: str, task_id: str) -> dict[str, object]:
    workspace, error, _siblings = _workspace(workspace_id)
    if error is not None or workspace is None:
        return error or _closed("unknown_workspace", workspace_id)

    def status() -> str:
        return status_for(workspace.validate_credential_target, (workspace.execute_credential_target,))

    def load() -> tuple[str, str]:
        return load_for(workspace.validate_credential_target, (workspace.execute_credential_target,))

    enabled = execution_switch_enabled()
    result = submit_validation(workspace, task_id, status_fn=status, load_fn=load, transport=_send, webhook_enabled=enabled)
    result["execution_enabled"] = enabled
    return result


@guarded
def prepare_execution_tool(workspace_id: str, manifest_task_id: str) -> dict[str, object]:
    workspace, error, siblings = _workspace(workspace_id)
    if error is not None or workspace is None:
        return error or _closed("unknown_workspace", workspace_id)
    result = prepare_execution(workspace, manifest_task_id, siblings)
    result["webhook_sent"] = False
    result["execution_enabled"] = execution_switch_enabled()
    result["execute_enabled"] = False
    return result


@guarded
def submit_execution_tool(workspace_id: str, execution_id: str) -> dict[str, object]:
    workspace, error, _siblings = _workspace(workspace_id)
    if error is not None or workspace is None:
        return error or _closed("unknown_workspace", workspace_id)
    enabled = execution_switch_enabled()

    def status() -> str:
        return status_for(workspace.execute_credential_target, (workspace.validate_credential_target,))

    def load() -> tuple[str, str]:
        return load_for(workspace.execute_credential_target, (workspace.validate_credential_target,))

    result = submit_execution(
        workspace,
        execution_id,
        switch_enabled=enabled,
        status_fn=status,
        load_fn=load,
        transport=_send,
    )
    result["execution_enabled"] = enabled
    return result


@guarded
def get_status(workspace_id: str, task_id: str = "", execution_id: str = "", *,
               observe_only: bool = False, verify_current: bool = False,
               rerun_tests: bool = False, wait_seconds: int = 0) -> dict[str, object]:
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 50:
        from grokbot_mcp.tool_errors import invalid_argument
        return invalid_argument(["workspace_id", "task_id", "execution_id", "observe_only", "verify_current", "rerun_tests", "wait_seconds"])
    if type(rerun_tests) is not bool or (rerun_tests and (not execution_id or observe_only)):
        from grokbot_mcp.tool_errors import invalid_argument
        return invalid_argument(["workspace_id", "task_id", "execution_id", "observe_only", "verify_current", "rerun_tests"])
    workspace, error, _siblings = _workspace(workspace_id)
    if error is not None or workspace is None:
        return error or _closed("unknown_workspace", workspace_id)
    waited_ms = None
    if wait_seconds:
        from grokbot_mcp.status_wait import TERMINAL, wait_for_status
        def probe():
            results = []
            if execution_id:
                results.append(get_execution_status(workspace, execution_id, observe_only=True, verify_current=verify_current))
            if task_id:
                results.append(get_validation_status(workspace, task_id, observe_only=True, verify_current=verify_current))
            return results
        waited_ms = wait_for_status(probe, wait_seconds)
    payload: dict[str, object] = {
        "workspace_id": workspace.workspace_id,
        "webhook_sent": False,
        "execution_enabled": execution_switch_enabled(),
        "qa_accepted": False,
    }
    if execution_id:
        payload["execution"] = get_execution_status(workspace, execution_id, observe_only=observe_only or (rerun_tests and not wait_seconds), verify_current=verify_current)
    if task_id:
        payload["validation"] = get_validation_status(workspace, task_id, observe_only=observe_only or (rerun_tests and not wait_seconds), verify_current=verify_current)
    if not execution_id and not task_id:
        payload["preflight"] = preflight(workspace_id)
    if rerun_tests and (not wait_seconds or payload.get("execution", {}).get("status") in TERMINAL):
        from grokbot_mcp.bridge_tests import compact, rerun
        payload["bridge_tests"] = compact(rerun(workspace, execution_id))
    if waited_ms is not None:
        payload["waited_ms"] = waited_ms
    return payload


@guarded
def claim_for(workspace_id: str, execution_id: str) -> dict[str, object]:
    workspace, error, siblings = _workspace(workspace_id)
    if error is not None or workspace is None:
        return error or _closed("unknown_workspace", workspace_id)
    return claim_execution(workspace, execution_id, siblings)
