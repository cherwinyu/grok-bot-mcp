"""Administrator-registered workspaces. Tool callers cannot supply a root."""

from __future__ import annotations

import json
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from dataclasses import asdict
from grokbot_mcp.execution_profile import ExecutionProfile, parse as parse_profile

_SHA = re.compile(r"^[0-9a-f]{40}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class RegistryError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TestCommand:
    command_id: str
    task_types: tuple[str, ...]
    timeout_cap: int
    default_timeout: int


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    root: Path
    expected_head: str
    allowed_files: tuple[str, ...]
    forbidden_files: tuple[str, ...]
    protected_file: str
    test_commands: tuple[TestCommand, ...]
    validate_credential_target: str
    execute_credential_target: str
    writes_enabled: bool
    expected_machine: str
    policy_revision: int = 1
    execution_profile: ExecutionProfile | None = None
    git_gate: str = "strict"

    def command(self, command_id: str) -> TestCommand | None:
        for item in self.test_commands:
            if item.command_id == command_id:
                return item
        return None


def default_registry_path() -> Path:
    override = os.environ.get("GROKBOT_MCP_REGISTRY", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "grokbot-mcp" / "registry.json"


def switch_path(registry_file: Path) -> Path:
    return registry_file.parent / "execution_switch.json"


def execution_switch_enabled(registry_file: Path | None = None) -> bool:
    path = switch_path(registry_file or default_registry_path())
    if not path.is_file():
        return False
    try:
        from grokbot_mcp.strict_json import decode, MAX_BYTES
        from grokbot_mcp.security.paths import is_reparse_point
        if (any(is_reparse_point(p) for p in (path, *path.parents))
                or path.stat().st_nlink != 1 or path.stat().st_size > MAX_BYTES):
            return False
        data = decode(path.read_text(encoding="utf-8").encode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("enabled") is True


def load_registry(path: Path | None = None) -> dict[str, Workspace]:
    file = path or default_registry_path()
    if not file.is_file():
        raise RegistryError("registry_missing")
    try:
        from grokbot_mcp.strict_json import decode, MAX_BYTES
        from grokbot_mcp.security.paths import is_reparse_point
        if any(is_reparse_point(p) for p in (file, *file.parents)) or file.stat().st_nlink != 1:
            raise ValueError("linked_path_rejected")
        if file.stat().st_size > MAX_BYTES:
            raise ValueError("json_too_large")
        # Keep the established text-reader fault boundary. Parsing is bounded;
        # this is not a filesystem sandbox against concurrent file growth.
        raw = decode(file.read_text(encoding="utf-8").encode("utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RegistryError("registry_unreadable") from exc
    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1 or not isinstance(raw.get("workspaces"), list):
        raise RegistryError("registry_invalid")
    found: dict[str, Workspace] = {}
    roots: set[str] = set()
    for item in raw["workspaces"]:
        workspace = _workspace(item)
        if workspace.workspace_id in found:
            raise RegistryError("registry_invalid")
        key = str(workspace.root.resolve()).lower()
        if key in roots:
            raise RegistryError("registry_invalid")
        roots.add(key)
        found[workspace.workspace_id] = workspace
    return found


def _workspace(item: object) -> Workspace:
    if not isinstance(item, dict):
        raise RegistryError("registry_invalid")
    workspace_id = item.get("workspace_id")
    if not isinstance(workspace_id, str) or not _ID.fullmatch(workspace_id):
        raise RegistryError("registry_invalid")
    root_text = item.get("root")
    if not isinstance(root_text, str) or not root_text.strip():
        raise RegistryError("registry_invalid")
    root = Path(root_text)
    if not root.is_absolute():
        raise RegistryError("registry_invalid")
    expected_head = item.get("expected_head")
    if not isinstance(expected_head, str) or not _SHA.fullmatch(expected_head):
        raise RegistryError("registry_invalid")
    git_gate = item.get("git_gate", "strict")
    if not isinstance(git_gate, str) or git_gate not in {"strict", "advisory"}:
        raise RegistryError("registry_invalid")
    allowed = _strings(item.get("allowed_files"))
    forbidden = tuple(_strings(item.get("forbidden_files")))
    protected = item.get("protected_file")
    if not isinstance(protected, str) or protected not in forbidden:
        raise RegistryError("registry_invalid")
    if any(name in forbidden or any(name == item_name or name.startswith(item_name + "/") for item_name in forbidden) for name in allowed):
        raise RegistryError("registry_invalid")
    commands = _commands(item.get("test_commands"))
    validate_target = item.get("validate_credential_target")
    execute_target = item.get("execute_credential_target")
    if not isinstance(validate_target, str) or not isinstance(execute_target, str):
        raise RegistryError("registry_invalid")
    if not validate_target or validate_target == execute_target:
        raise RegistryError("registry_invalid")
    writes = item.get("writes_enabled")
    if not isinstance(writes, bool):
        raise RegistryError("registry_invalid")
    machine = item.get("expected_machine", "")
    if not isinstance(machine, str):
        raise RegistryError("registry_invalid")
    revision = item.get("policy_revision", 1)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise RegistryError("registry_invalid")
    profile = None
    if "execution_profile" in item:
        try:
            profile = parse_profile(item["execution_profile"])
        except ValueError as exc:
            raise RegistryError(str(exc)) from None
    return Workspace(
        workspace_id=workspace_id,
        root=root,
        expected_head=expected_head,
        allowed_files=tuple(allowed),
        forbidden_files=forbidden,
        protected_file=protected,
        test_commands=commands,
        validate_credential_target=validate_target,
        execute_credential_target=execute_target,
        writes_enabled=writes,
        expected_machine=machine,
        policy_revision=revision,
        execution_profile=profile,
        git_gate=git_gate,
    )


def _strings(value: object) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise RegistryError("registry_invalid")
    return [item.strip().replace("\\", "/") for item in value]


def _commands(value: object) -> tuple[TestCommand, ...]:
    if not isinstance(value, list) or not value:
        raise RegistryError("registry_invalid")
    found: list[TestCommand] = []
    for item in value:
        if not isinstance(item, dict):
            raise RegistryError("registry_invalid")
        command_id = item.get("id")
        task_types = item.get("task_types")
        cap = item.get("timeout_cap")
        default = item.get("default_timeout")
        if not isinstance(command_id, str) or not command_id:
            raise RegistryError("registry_invalid")
        if not isinstance(task_types, list) or not task_types or not all(isinstance(part, str) for part in task_types):
            raise RegistryError("registry_invalid")
        if isinstance(cap, bool) or isinstance(default, bool) or not isinstance(cap, int) or not isinstance(default, int):
            raise RegistryError("registry_invalid")
        if cap < 1 or default < 1 or default > cap or cap > 600:
            raise RegistryError("registry_invalid")
        found.append(TestCommand(command_id, tuple(task_types), cap, default))
    return tuple(found)


def policy_fingerprint(workspace: Workspace) -> str:
    """Stable policy identity. Root and registry binding are checked separately.

    This does not advance HEAD or authorize a migration. Changes invalidate the
    local binding and require an explicit administrator confirmation.
    """
    policy = {
        "workspace_id": workspace.workspace_id,
        "expected_head": workspace.expected_head,
        "allowed_files": list(workspace.allowed_files),
        "forbidden_files": list(workspace.forbidden_files),
        "protected_file": workspace.protected_file,
        "expected_machine": workspace.expected_machine,
        "validate_credential_target": workspace.validate_credential_target,
        "execute_credential_target": workspace.execute_credential_target,
        "policy_revision": workspace.policy_revision,
        "test_commands": [
            {"id": c.command_id, "task_types": list(c.task_types),
             "timeout_cap": c.timeout_cap, "default_timeout": c.default_timeout}
            for c in workspace.test_commands
        ],
    }
    if workspace.execution_profile is not None:
        policy["execution_profile"] = asdict(workspace.execution_profile)
    if workspace.git_gate != "strict":
        policy["git_gate"] = workspace.git_gate
    raw = json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
