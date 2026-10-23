"""Registered command metadata and argv construction; never executes a shell."""
from __future__ import annotations

import re
from pathlib import PureWindowsPath

from grokbot_mcp.security.paths import normalize_repo_path, normalize_policy_path

_DOCS = {".md", ".rst", ".txt", ".adoc"}
_CODE = {".py", ".gd", ".cs", ".cpp", ".c", ".h", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go"}
_SHELLS = {"cmd", "powershell", "pwsh", "sh", "bash", "wscript", "cscript"}


def validate_options(item: dict) -> tuple[str | dict | None, tuple[str, ...]]:
    applies = item.get("applies_to")
    if applies is not None:
        if isinstance(applies, str):
            if applies not in {"docs", "code", "any"}:
                raise ValueError("invalid_test_applicability")
        elif type(applies) is dict and applies and set(applies) <= {"prefixes", "suffixes"}:
            normalized = {}
            for key, values in applies.items():
                if not isinstance(values, list) or not values or any(type(v) is not str or not v for v in values):
                    raise ValueError("invalid_test_applicability")
                if key == "prefixes":
                    normalized[key] = [normalize_policy_path(v, casefold=True) for v in values]
                else:
                    if any(not re.fullmatch(r"\.[a-zA-Z0-9.]+", v) for v in values):
                        raise ValueError("invalid_test_applicability")
                    normalized[key] = [v.casefold() for v in values]
            applies = normalized
        else:
            raise ValueError("invalid_test_applicability")
    argv = item.get("argv", [])
    if not isinstance(argv, list) or any(type(v) is not str or not v or any(ord(c) < 32 for c in v) for v in argv):
        raise ValueError("invalid_test_argv")
    if argv:
        executable = PureWindowsPath(argv[0])
        if (executable.stem.casefold() in _SHELLS or executable.suffix.casefold() in {".bat", ".cmd", ".ps1"}
                or "{" in argv[0] or "}" in argv[0]):
            raise ValueError("invalid_test_argv")
        if argv.count("{changed_files}") != 1 or any(("{" in v or "}" in v) and v != "{changed_files}" for v in argv):
            raise ValueError("invalid_test_argv")
    return applies, tuple(argv)


def applicable(command, files: list[str]) -> bool | None:
    """Every path must match; unknown extensions/mixed kinds are not guessed."""
    rule = command.applies_to
    if rule is None or rule == "any":
        return True
    if not files:
        return None
    paths = [normalize_policy_path(p, casefold=True) for p in files]
    if isinstance(rule, str):
        kinds = {"docs" if PureWindowsPath(p).suffix in _DOCS else
                 "code" if PureWindowsPath(p).suffix in _CODE else "unknown" for p in paths}
        return rule in kinds if len(kinds) == 1 and "unknown" not in kinds else None
    return all((not rule.get("prefixes") or any(p == v or p.startswith(v + "/") for v in rule["prefixes"]))
               and (not rule.get("suffixes") or any(p.endswith(v) for v in rule["suffixes"])) for p in paths)


def selection_warnings(workspace, command_id, task_type, files) -> list[str]:
    if not command_id:
        raise ValueError("test_command_required")
    command = workspace.command(command_id)
    if command is None or task_type not in command.task_types:
        raise ValueError("illegal_test_command")
    if applicable(command, files) is False:
        if workspace.test_selection == "reject":
            raise ValueError("test_command_scope_mismatch")
        return ["test_command_scope_mismatch"]
    return []


def changed_files_argv(command, changed_files: list[str], allowed_files: list[str]) -> list[str]:
    """Expand one whole argv token, never format/join shell text or accept options."""
    if not command.argv:
        raise ValueError("test_command_template_missing")
    validate_options({"argv": list(command.argv)})
    paths = []
    for value in changed_files:
        if type(value) is not str or value != normalize_repo_path(value) or value not in allowed_files:
            raise ValueError("test_file_outside_scope")
        # Deliberately narrower than the repository path grammar. No switches,
        # Windows shell metacharacters, response-file syntax, ADS or controls.
        if any(ord(c) < 32 or c in '\"\'`$;&|<>%!*?[]{}():@' for c in value) or any(p.startswith("-") for p in value.split("/")):
            raise ValueError("test_file_unsafe")
        if value not in paths:
            paths.append(value)
    if not paths:
        raise ValueError("test_changed_files_empty")
    return [part for token in command.argv for part in (sorted(paths) if token == "{changed_files}" else [token])]


def enhanced_tests(workspace, command) -> bool:
    return bool(workspace.require_test_output or workspace.test_selection != "warn"
                or (command and (command.applies_to is not None or command.argv)))
