"""Selected strict-JSON client configurations; no defaults or unknown scripts executed."""
from __future__ import annotations

import ast
import copy
import email.parser
import uuid
from pathlib import Path

from grokbot_mcp import __version__
from grokbot_mcp import file_transaction
from grokbot_mcp.identity import digest, installation_id, path_id, package_digest
from grokbot_mcp.local_safety import LocalError, json_bytes, read_document, safe_path, local_lock, current_hash
from grokbot_mcp.registry import load_registry, policy_fingerprint
from grokbot_mcp.routine import routine_readiness
from grokbot_mcp.security.gitcheck import git_error, git_snapshot, workspace_git_rejection
from grokbot_mcp.security.paths import is_uuid
from grokbot_mcp.security.targets import resolve_exact_files, TargetRejected

SERVER_ARGS = ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"]
RECOVERY = "Install the reviewed candidate wheel into a NEW Python 3.12+ venv, then use client_cli fragment/switch with its Scripts/python.exe. Do not overwrite an old installation or recreate a Routine merely because a path is missing."


def _metadata(site: Path, distribution: str) -> str:
    paths = list(site.glob(distribution + "-*.dist-info/METADATA"))
    if len(paths) != 1:
        raise LocalError("installation_metadata_missing_or_ambiguous")
    safe_path(paths[0], regular=True)
    try:
        message = email.parser.Parser().parsestr(paths[0].read_text(encoding="utf-8"))
        version = message.get("Version")
    except (OSError, UnicodeError) as exc:
        raise LocalError("installation_metadata_unreadable") from exc
    if type(version) is not str or not version:
        raise LocalError("installation_metadata_unreadable")
    return version


def inspect_installation(python: Path, *, require_current: bool = True) -> dict:
    safe_path(python)
    if not python.is_file():
        raise LocalError("interpreter_missing")
    if python.name.lower() != "python.exe" or python.parent.name.lower() != "scripts":
        raise LocalError("dedicated_windows_venv_required")
    root = python.parent.parent
    safe_path(root / "pyvenv.cfg", regular=True)
    try:
        config = dict(line.split("=", 1) for line in (root / "pyvenv.cfg").read_text(encoding="utf-8").splitlines() if "=" in line)
        config = {key.strip(): value.strip() for key, value in config.items()}
        version_tuple = tuple(int(part) for part in config.get("version", "").split(".")[:2])
        if len(version_tuple) != 2 or version_tuple < (3, 12):
            raise LocalError("python_unsupported")
        if config.get("include-system-site-packages", "false").lower() != "false":
            raise LocalError("dedicated_windows_venv_required")
        if "home" in config and not Path(config["home"]).is_dir():
            raise LocalError("interpreter_base_missing")
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, LocalError):
            raise
        raise LocalError("venv_configuration_invalid") from exc
    site = root / "Lib" / "site-packages"
    package = site / "grokbot_mcp"
    safe_path(package / "server.py", regular=True)
    safe_path(package / "__init__.py", regular=True)
    try:
        nodes = ast.parse((package / "__init__.py").read_text(encoding="utf-8")).body
        versions = [n.value.value for n in nodes if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                    and any(isinstance(t, ast.Name) and t.id == "__version__" for t in n.targets)]
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise LocalError("installation_metadata_unreadable") from exc
    version = _metadata(site, "grokbot_mcp_bridge")
    if versions != [version]:
        raise LocalError("installation_version_mismatch")
    if require_current and version != __version__:
        raise LocalError("candidate_version_mismatch")
    if _metadata(site, "mcp") != "2.3.0":
        raise LocalError("core_dependency_mismatch")
    safe_path(site / "mcp" / "server" / "__init__.py", regular=True)
    source_digest = package_digest(package)
    if source_digest is None:
        raise LocalError("installation_package_unreadable")
    # Metadata inspection is NOT proof that native dependencies can be imported.
    return {"bridge_version": version, "installation_id": installation_id(python, package),
            "metadata_checked": True, "imports_verified": False,
            "python": str(python), "package": str(package),
            "source_sha256": source_digest}


def read_config(path: Path, name: str) -> tuple[dict, bytes, dict]:
    if path.suffix.lower() != ".json":
        raise LocalError("client_format_unsupported")
    document, raw = read_document(path)
    servers = document.get("mcpServers")
    if type(servers) is not dict or name not in servers or type(servers[name]) is not dict:
        raise LocalError("client_server_missing_or_unsupported")
    entry = servers[name]
    if "url" in entry or entry.get("type", "stdio") != "stdio":
        raise LocalError("client_transport_unsupported")
    return document, raw, entry


def _launch(entry: dict) -> tuple[Path, Path]:
    command, args, env = entry.get("command"), entry.get("args"), entry.get("env")
    if type(command) is not str or type(args) is not list or not all(type(x) is str for x in args):
        raise LocalError("client_launch_invalid")
    python = Path(command)
    if not python.is_absolute():
        raise LocalError("absolute_interpreter_required")
    if not python.is_file():
        raise LocalError("interpreter_missing")
    # Legacy launchers are diagnosed, never executed in a probe.
    if args != SERVER_ARGS:
        scripts = [Path(x) for x in args if x.lower().endswith((".py", ".ps1", ".cmd", ".bat"))]
        if any(p.is_absolute() and not p.is_file() for p in scripts):
            raise LocalError("entrypoint_missing")
        raise LocalError("client_arguments_unsupported")
    if type(env) is not dict or not all(type(k) is str and type(v) is str for k, v in env.items()):
        raise LocalError("client_environment_invalid")
    value = env.get("GROKBOT_MCP_REGISTRY")
    if type(value) is not str or not Path(value).is_absolute():
        raise LocalError("registry_selection_required")
    return python, Path(value)


def workspace_checks(registry: Path, workspace_id: str) -> tuple[object, list[str]]:
    safe_path(registry, regular=True)
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    safe_path(workspace.root)
    safe_path(workspace.root / ".bridge")
    if not workspace.root.is_dir():
        raise LocalError("workspace_missing")
    codes = []
    error = workspace_git_rejection(workspace.root) or git_error(
        git_snapshot(
            workspace.root, workspace.allowed_files, workspace.expected_head,
            workspace.forbidden_files if workspace.git_gate == "advisory" else (),
        ),
        workspace.git_gate,
    )
    if error:
        codes.append(error)
    try:
        if workspace.allowed_files or not workspace.allowed_prefixes:
            resolve_exact_files(workspace.root, list(workspace.allowed_files), workspace.forbidden_files)
        resolve_exact_files(workspace.root, [workspace.protected_file], (".git", ".bridge"))
    except TargetRejected as exc:
        codes.append(exc.code)
    for phase in ("validate", "execute"):
        binding = routine_readiness(workspace, phase, registry)
        if binding["binding_error"]:
            codes.append(binding["binding_error"])
    return workspace, sorted(set(codes))


def inspect_config(path: Path, name: str, workspace_id: str, *, local_details: bool = False) -> dict:
    _, raw, entry = read_config(path, name)
    python, registry = _launch(entry)
    install = inspect_installation(python, require_current=False)
    workspace, codes = workspace_checks(registry, workspace_id)
    if install["bridge_version"] != __version__:
        codes.append("candidate_version_mismatch")
    identity = {key: install[key] for key in ("bridge_version", "installation_id")}
    identity["package_sha256_at_start"] = install["source_sha256"]
    identity.update(registry_id=path_id("registry", registry), registry_sha256=current_hash(registry), workspace_id=workspace_id)
    result = {"ok": not codes, "check_kind": "static", "configuration_sha256": digest(raw),
              "expected_identity": identity, "codes": codes,
              "configuration_updated": False, "standalone_handshake_passed": False,
              "client_reconnection_confirmed": False, "remote_binding_verified": False,
              "imports_verified": False, "credentials_checked": False, "webhook_sent": False,
              "qa_accepted": False, "next_step": "Use an explicit standalone probe; then reload the actual client and compare its preflight runtime_identity. " + RECOVERY}
    if local_details:
        result["local_paths"] = {"client_config": str(path), "python": str(python), "registry": str(registry)}
    if workspace.git_gate == "advisory":
        result["git_gate"] = "advisory"
    return result


def fragment(python: Path, registry: Path, workspace_id: str) -> dict:
    inspect_installation(python)
    _, codes = workspace_checks(registry, workspace_id)
    if codes:
        raise LocalError("target_workspace_not_ready")
    return {"command": str(python), "args": list(SERVER_ARGS), "env": {"GROKBOT_MCP_REGISTRY": str(registry)}}


def switch_plan(path: Path, name: str, python: Path, registry: Path, workspace_id: str, *, local_details: bool = False) -> tuple[dict, bytes]:
    old, before, old_entry = read_config(path, name)
    # Client configuration is not business state. Never rewrite a registered root,
    # the selected registry, a switch, or any .bridge path as a client config.
    from grokbot_mcp.registry import switch_path
    if (path.resolve() in {registry.resolve(), switch_path(registry).resolve()}
            or any(part.rstrip(" .").casefold() == ".bridge" for part in path.parts)
            or any(path.resolve().is_relative_to(ws.root.resolve()) for ws in load_registry(registry).values())):
        raise LocalError("client_destination_protected")
    install = inspect_installation(python)
    workspace, codes = workspace_checks(registry, workspace_id)
    if codes:
        raise LocalError("target_workspace_not_ready")
    if "env" in old_entry and (type(old_entry["env"]) is not dict or not all(type(k) is str and type(v) is str for k, v in old_entry["env"].items())):
        raise LocalError("client_environment_invalid")
    new = copy.deepcopy(old)
    entry = new["mcpServers"][name]
    entry["command"], entry["args"] = str(python), list(SERVER_ARGS)
    entry.setdefault("env", {})["GROKBOT_MCP_REGISTRY"] = str(registry)
    after = json_bytes(new)
    fields = [key for key in ("command", "args", "env") if old_entry.get(key) != entry.get(key)]
    identity = {"bridge_version": install["bridge_version"], "installation_id": install["installation_id"],
                "registry_id": path_id("registry", registry), "registry_sha256": current_hash(registry), "workspace_id": workspace_id,
                "package_sha256_at_start": install["source_sha256"]}
    basis = {"config_id": path_id("client", path), "before_sha256": digest(before), "after_sha256": digest(after),
             "expected_identity": identity, "policy_fingerprint": policy_fingerprint(workspace),
             "installation_source_sha256": install["source_sha256"]}
    plan = {"ok": True, "plan_sha256": digest(json_bytes(basis)), **basis,
            "changed_fields": fields, "configuration_updated": False,
            "standalone_handshake_passed": False, "client_reconnection_confirmed": False,
            "backup_created": False, "remote_binding_verified": False,
            "diff_scope": "selected server command/args/registry; unknown fields and other servers preserved",
            "old_launch": {"command_id": path_id("python", Path(old_entry["command"])) if type(old_entry.get("command")) is str else None,
                           "args_changed": old_entry.get("args") != entry["args"]},
            "next_step": "Review this plan. Re-run switch with --apply --confirm <plan_sha256>. A standalone probe does not reconnect the client."}
    if workspace.git_gate == "advisory":
        plan["git_gate"] = "advisory"
    if local_details:
        old_env = old_entry.get("env", {})
        plan["local_path_diff"] = {
            "command": {"before": old_entry.get("command"), "after": str(python)},
            "registry": {"before": old_env.get("GROKBOT_MCP_REGISTRY"), "after": str(registry)},
            "new_args": list(SERVER_ARGS),
            "other_values": "not displayed; unknown values preserved, potentially sensitive"}
    return plan, after


def _folder(path: Path, transaction: str) -> Path:
    if not is_uuid(transaction):
        raise LocalError("transaction_id_invalid")
    return path.parent / ("." + path.name + ".grokbot-history") / transaction


def switch(path: Path, name: str, python: Path, registry: Path, workspace_id: str, *, confirm: str) -> dict:
    plan, after = switch_plan(path, name, python, registry, workspace_id)
    if confirm != plan["plan_sha256"]:
        raise LocalError("plan_confirmation_mismatch")
    if not plan["changed_fields"]:
        return {**plan, "no_change": True, "transaction_id": None}
    with local_lock(path.parent / ("." + path.name + ".grokbot.lock")):
        fresh, after = switch_plan(path, name, python, registry, workspace_id)
        if fresh["plan_sha256"] != confirm:
            raise LocalError("input_changed")
        transaction = str(uuid.uuid4())
        folder = _folder(path, transaction)
        context = {"config_id": path_id("client", path), "plan_sha256": confirm,
                   "expected_identity": plan["expected_identity"]}
        file_transaction.prepare(folder, {"client": (path, after)}, context)
        try:
            file_transaction.apply(folder, {"client": path})
        except Exception as exc:
            # Receipt and backups remain readable for explicit recover/rollback.
            raise LocalError("client_apply_interrupted", transaction_id=transaction) from exc
    return {**plan, "configuration_updated": True, "backup_created": True,
            "transaction_id": transaction, "next_step": "Probe the new server separately. Reload the actual client, call grok_preflight there, compare runtime_identity. Keep the local backup private."}


def transaction_plan(path: Path, transaction: str, *, rollback: bool) -> dict:
    folder = _folder(path, transaction)
    receipt, receipt_hash = file_transaction.read_receipt(folder, {"client": path})
    if receipt["context"].get("config_id") != path_id("client", path):
        raise LocalError("transaction_context_mismatch")
    current = current_hash(path)
    if current not in receipt["files"]["client"].values():
        raise LocalError("transaction_target_changed")
    plan = {"transaction_id": transaction, "receipt_sha256": receipt_hash, "current_sha256": current,
            "rollback": rollback, "config_id": path_id("client", path)}
    return {**plan, "plan_sha256": digest(json_bytes(plan)), "configuration_updated": False}


def recover(path: Path, transaction: str, *, rollback: bool, confirm: str) -> dict:
    plan = transaction_plan(path, transaction, rollback=rollback)
    if plan["plan_sha256"] != confirm:
        raise LocalError("plan_confirmation_mismatch")
    with local_lock(path.parent / ("." + path.name + ".grokbot.lock")):
        if transaction_plan(path, transaction, rollback=rollback)["plan_sha256"] != confirm:
            raise LocalError("input_changed")
        receipt = file_transaction.apply(_folder(path, transaction), {"client": path}, rollback=rollback)
    return {**plan, "configuration_updated": True, "state": receipt["state"],
            "client_reconnection_confirmed": False, "next_step": "Reload the actual client and compare its identity; this operation only changed its configuration file."}
