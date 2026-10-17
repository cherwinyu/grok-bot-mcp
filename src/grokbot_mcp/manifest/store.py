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
_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|https?://|\\\\)", re.IGNORECASE)


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
) -> dict[str, object]:
    if baseline_commit not in (None, "", workspace.expected_head):
        raise ManifestError("baseline_mismatch")
    if task_type not in {"code_change", "read_only"}:
        raise ManifestError("invalid_task_type")
    description = _text(task_description, MAX_DESCRIPTION, "description_empty")
    files = _files(workspace, allowed_files)
    if task_type == "code_change" and not files:
        raise ManifestError("allowed_files_empty")
    criteria = _criteria(acceptance_criteria)
    command = workspace.command(test_command_id)
    if command is None or task_type not in command.task_types:
        raise ManifestError("illegal_test_command")
    timeout = command.default_timeout if execution_timeout is None else execution_timeout
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1 or timeout > command.timeout_cap:
        raise ManifestError("invalid_timeout")
    task_id = str(uuid.uuid4())
    document: dict[str, object] = {
        "acceptance_criteria": criteria,
        "allowed_files": files,
        "baseline_commit": workspace.expected_head,
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
    write_json_new(manifest_path(workspace.root, task_id), document)
    stored, digest = read_json(manifest_path(workspace.root, task_id))
    if stored.get("task_id") != task_id:
        raise ManifestError("schema_invalid")
    return {
        "task_id": task_id,
        "workspace_id": workspace.workspace_id,
        "manifest_path": relative,
        "manifest_sha256": digest,
        "validation_status": "valid",
        "action_required": "none",
        "webhook_sent": False,
        "execution_enabled": False,
    }


def load_manifest(root: Path, task_id: str) -> tuple[dict[str, object], str]:
    path = manifest_path(root, task_id)
    if not path.is_file():
        raise ManifestError("manifest_missing")
    try:
        return read_json(path)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc


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
