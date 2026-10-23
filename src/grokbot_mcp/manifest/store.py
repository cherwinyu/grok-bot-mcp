"""Write one manifest. This module never calls a webhook."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from grokbot_mcp.registry import Workspace
from grokbot_mcp.security.paths import PathRejected, normalize_repo_path
from grokbot_mcp.storage import read_json, utc_now, write_json_new

MAX_DESCRIPTION = 2000
MAX_CRITERION = 500
MAX_ACCEPTANCE = 20
# Bump this when the constraint generation changes. Verifiers accept every
# integer from 1 through this value, and manifests that omit the field.
# They must not require the stored manifest to equal the current constraint prose.
CONSTRAINT_VERSION = 1
_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|https?://|\\\\)", re.IGNORECASE)


def constraint_version_accepted(value: object, *, present: bool = True) -> bool:
    """Historical manifests omit the field and stay verifiable after upgrades.

    A present value must be an integer in ``1..CONSTRAINT_VERSION``. Equality
    with the current constraint text is not a requirement and is not checked.
    """
    if not present:
        return True
    return type(value) is int and 1 <= value <= CONSTRAINT_VERSION


class ManifestError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def manifest_path(root: Path, task_id: str) -> Path:
    return root / ".bridge" / "task_specs" / f"{task_id}.json"


def prepare_manifest(
    workspace: Workspace,
    *,
    task_type: str,
    task_description: str,
    allowed_files: list[str],
    acceptance_criteria: list[str],
    test_command_id: str,
    execution_timeout: int | None,
    baseline_commit: str | None,
    effective_baseline: str | None = None,
    allowed_globs: list[str] | None = None,
) -> dict[str, object]:
    selected_baseline = effective_baseline or workspace.expected_head
    accepted_inputs = (None, "", workspace.expected_head)
    if workspace.git_gate == "advisory":
        accepted_inputs += (selected_baseline,)
    if baseline_commit not in accepted_inputs:
        raise ManifestError("baseline_mismatch")
    if task_type not in {"code_change", "read_only"}:
        raise ManifestError("invalid_task_type")
    description = _text(task_description, MAX_DESCRIPTION, "description_empty")
    files = _files(workspace, allowed_files)
    expansion = None
    if allowed_globs is not None:
        from grokbot_mcp.security.globs import expand
        expanded = expand(workspace, allowed_globs)
        files = list(dict.fromkeys([*files, *expanded]))
        if len(files) > workspace.glob_max_files:
            raise ManifestError("allowed_glob_limit_exceeded")
        expansion = {"allowed_globs": list(allowed_globs), "expanded_files": expanded}
    if task_type == "code_change" and not files:
        raise ManifestError("allowed_files_empty")
    criteria = _criteria(acceptance_criteria)
    from grokbot_mcp.test_commands import selection_warnings
    try:
        warnings = selection_warnings(workspace, test_command_id, task_type, files)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    command = workspace.command(test_command_id)
    timeout = command.default_timeout if execution_timeout is None else execution_timeout
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1 or timeout > command.timeout_cap:
        raise ManifestError("invalid_timeout")
    task_id = str(uuid.uuid4())
    from grokbot_mcp.ledger_archive import archived
    if archived(workspace.root, task_id):
        raise ManifestError("archived_task")
    document: dict[str, object] = {
        "acceptance_criteria": criteria,
        "allowed_files": files,
        "baseline_commit": selected_baseline,
        "constraint_version": CONSTRAINT_VERSION,
        "created_at": utc_now(),
        "execution_timeout": timeout,
        "forbidden_files": list(workspace.forbidden_files),
        "lifecycle": "prepared",
        "manifest_task_id": task_id,
        "schema_version": 1,
        "task_description": description,
        "task_id": task_id,
        "task_type": task_type,
        "test_command_id": test_command_id,
        "test_policy": {"command_id": test_command_id},
        "workspace_id": workspace.workspace_id,
    }
    relative = ".bridge/task_specs/" + task_id + ".json"
    if expansion is not None:
        document["scope_expansion"] = expansion
    write_json_new(manifest_path(workspace.root, task_id), document)
    stored, digest = read_json(manifest_path(workspace.root, task_id))
    if stored.get("task_id") != task_id:
        raise ManifestError("schema_invalid")
    return {
        "task_id": task_id,
        "workspace_id": workspace.workspace_id,
        "manifest_path": relative,
        "manifest_sha256": digest,
        "constraint_version": stored.get("constraint_version"),
        "validation_status": "valid",
        "action_required": "none",
        "webhook_sent": False,
        "execution_enabled": False,
        **({"warnings": warnings} if warnings else {}),
        **({"scope_expansion": expansion, "allowed_files": files,
            "action_required": "confirm_expanded_files",
            "operator_message": f"Review and confirm all {len(files)} explicit files before submission. / 提交前请确认全部 {len(files)} 个文件。"}
           if expansion is not None else {}),
    }


def load_manifest(root: Path, task_id: str) -> tuple[dict[str, object], str]:
    from grokbot_mcp.ledger_archive import archived
    if archived(root, task_id):
        raise ManifestError("archived_task")
    path = manifest_path(root, task_id)
    if not path.is_file():
        raise ManifestError("manifest_missing")
    try:
        document, digest = read_json(path)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if not constraint_version_accepted(document.get("constraint_version"), present="constraint_version" in document):
        raise ManifestError("constraint_version_unsupported")
    return document, digest


def _text(value: object, limit: int, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or len(value) > limit:
        raise ManifestError(code)
    if _ABSOLUTE.search(value):
        raise ManifestError("absolute_path")
    return value


def _criteria(values: object) -> list[str]:
    if not isinstance(values, list) or not values or len(values) > MAX_ACCEPTANCE:
        raise ManifestError("missing_acceptance")
    cleaned: list[str] = []
    for item in values:
        cleaned.append(_text(item, MAX_CRITERION, "missing_acceptance"))
    return cleaned


def _files(workspace: Workspace, values: object) -> list[str]:
    if not isinstance(values, list):
        raise ManifestError("allowed_file_outside_policy")
    allowed = set(workspace.allowed_files)
    found: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ManifestError("allowed_file_outside_policy")
        try:
            text = normalize_repo_path(item)
        except PathRejected as exc:
            raise ManifestError(exc.code) from exc
        if text not in allowed or _blocked(text, workspace.forbidden_files):
            raise ManifestError("allowed_file_outside_policy")
        if text not in found:
            found.append(text)
    return found


def _blocked(name: str, forbidden: tuple[str, ...]) -> bool:
    return any(name == item or name.startswith(item + "/") for item in forbidden)
