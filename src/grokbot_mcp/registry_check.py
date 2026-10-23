"""Read-only, bounded registration hints; never resolve imports or search PATH."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path, PureWindowsPath

from grokbot_mcp.registry import RegistryError, load_registry
from grokbot_mcp.security.paths import PathRejected, is_reparse_point, normalize_repo_path
from grokbot_mcp.security.prefixes import blocked, literal_path


def _probe(workspace, base: Path, value: str | None, *, directory=False):
    """Reject lexical escapes before IO; walk ancestors before descendants."""
    try:
        candidate = base
        if value is not None:
            raw = value.replace("\\", "/")
            if ".." in raw.split("/"):
                raise PathRejected("traversal")
            path = Path(raw)
            if directory and raw == ".":
                candidate = base
            elif path.is_absolute():
                candidate = path
            else:
                candidate = base / literal_path(normalize_repo_path(raw))
        try:
            relative = candidate.relative_to(workspace.root).as_posix()
        except ValueError:
            return "unchecked", "path_outside_root"
        if relative != ".":
            relative = literal_path(relative)
            if blocked(workspace, relative):
                raise PathRejected("forbidden_file")
        # Do not stat a descendant until every parent has been checked.
        for part in (*reversed(candidate.parents), candidate):
            if is_reparse_point(part):
                return "unchecked", "reparse"
            info = part.lstat()
            if part != candidate and not stat.S_ISDIR(info.st_mode):
                return "missing", "path_missing"
        resolved = candidate.resolve().relative_to(workspace.root.resolve()).as_posix()
        if resolved != "." and blocked(workspace, resolved):
            return "unchecked", "forbidden_file"
        if not directory and info.st_nlink != 1:
            return "unchecked", "target_hardlink_rejected"
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        return ("ok", "path_exists") if valid else ("missing", "path_wrong_type")
    except FileNotFoundError:
        return "missing", "path_missing"
    except PathRejected as exc:
        return "unchecked", exc.code
    except (OSError, ValueError):
        return "unchecked", "path_unavailable"


def check_workspace(workspace) -> dict:
    items = []
    for command in workspace.test_commands:
        variants = [("argv", command.argv, None, ())]
        if command.rerun is not None:
            variants.append(("rerun.argv", command.rerun.argv, command.rerun.cwd, command.rerun.env))
        for source, argv, relative, env in variants:
            def add(field, outcome):
                status, code = outcome
                items.append({"command_id": command.command_id, "source": source,
                              "field": field, "status": status, "code": code})

            cwd_result = _probe(workspace, workspace.root, relative, directory=True)
            add("cwd", ("missing", "cwd_missing") if cwd_result[0] == "missing" else cwd_result)
            if not argv:
                add("argv", ("unchecked", "argv_not_registered"))
                continue
            if cwd_result[0] != "ok":
                add("argv", ("unchecked", "cwd_unavailable"))
                continue
            cwd = workspace.root / normalize_repo_path(relative) if relative else workspace.root
            executable = argv[0]
            if not any(c in executable for c in "/\\:") and not PureWindowsPath(executable).drive:
                add("argv[0]", ("unchecked", "executable_bare_name"))
            else:
                outcome = _probe(workspace, cwd, executable)
                add("argv[0]", ("missing", "executable_missing") if outcome[0] == "missing" else outcome)
            # Only recognize the explicit Python module invocation, not arbitrary tokens.
            try:
                index = argv.index("-m")
                runner = argv[index + 1]
            except (ValueError, IndexError):
                add("targets", ("unchecked", "runner_unrecognized"))
                continue
            if runner not in {"unittest", "pytest"}:
                add("targets", ("unchecked", "runner_unrecognized"))
                continue
            roots = [cwd]
            uncertain_root = False
            for root in dict(env).get("PYTHONPATH", "").split(os.pathsep):
                if not root:
                    continue
                outcome = _probe(workspace, cwd, root, directory=True)
                add("env.PYTHONPATH", outcome)
                if outcome[0] == "ok":
                    roots.append(cwd / root)
                elif outcome[0] == "unchecked":
                    uncertain_root = True
            for position in range(index + 2, len(argv)):
                token = argv[position]
                if token == "{changed_files}" or token.startswith("-"):
                    continue
                if token.endswith(".py") or "/" in token or "\\" in token or ":" in token:
                    outcome = _probe(workspace, cwd, token)
                    if outcome[0] == "missing":
                        outcome = ("missing", "test_file_missing")
                elif runner == "unittest" and "." in token and all(p.isidentifier() for p in token.split(".")):
                    name = token.replace(".", "/")
                    outcomes = [_probe(workspace, root, suffix) for root in roots
                                for suffix in (name + ".py", name + "/__init__.py")]
                    if any(result[0] == "ok" for result in outcomes):
                        outcome = ("ok", "module_exists")
                    elif uncertain_root or any(result[0] == "unchecked" for result in outcomes):
                        outcome = ("unchecked", "module_search_incomplete")
                    else:
                        outcome = ("missing", "unittest_module_missing")
                else:
                    outcome = ("unchecked", "target_unrecognized")
                add(f"argv[{position}]", outcome)
    missing = [item for item in items if item["status"] == "missing"]
    return {"workspace_id": workspace.workspace_id, "items": items,
            "summary": {"total": len(items), "missing_count": len(missing), "missing": missing}}


def check(registry: Path, workspace_id: str | None = None) -> dict:
    workspaces = load_registry(registry)
    if workspace_id is not None:
        if workspace_id not in workspaces:
            raise RegistryError("unknown_workspace")
        workspaces = {workspace_id: workspaces[workspace_id]}
    return {"ok": True, "read_only": True, "webhook_sent": False,
            "workspaces": [check_workspace(ws) for ws in workspaces.values()]}
