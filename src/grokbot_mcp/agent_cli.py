"""Optional dispatcher material. Resolves only an explicit config; never runs a prompt."""
from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from grokbot_mcp import __version__
from grokbot_mcp.identity import TEMPLATE_VERSION, digest
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.local_safety import LocalError, create_new, json_bytes, read_document, safe_path, current_hash, local_lock


def check(config: Path, *, local_details=False) -> dict:
    data, _ = read_document(config)
    if (type(data.get("schema_version")) is not int or data["schema_version"] != 1
            or data.get("integration") != "optional-dispatcher"
            or data.get("template_version") != TEMPLATE_VERSION):
        raise LocalError("agent_configuration_unsupported")
    value = data.get("prompt_file")
    if type(value) is not str or not value:
        raise LocalError("agent_prompt_selection_required")
    selected = Path(value)
    if not selected.is_absolute():
        if ".." in selected.parts:
            raise LocalError("agent_relative_escape_rejected")
        selected = config.parent / selected
    safe_path(selected)
    if selected.suffix.casefold() != ".md":
        raise LocalError("agent_prompt_format_unsupported")
    if not selected.is_file():
        raise LocalError("agent_prompt_missing")
    try:
        raw = selected.read_bytes()
        if len(raw) > 1024 * 1024:
            raise LocalError("agent_prompt_too_large")
        raw.decode("utf-8")
    except UnicodeError as exc:
        raise LocalError("agent_prompt_encoding_invalid") from exc
    return {"ok": True, "mandatory_for_bridge": False, "prompt_executed": False,
            "template_version": TEMPLATE_VERSION, "generator_version": data.get("generator_version"),
            "prompt_sha256": digest(raw), "relative_path_base": "selected configuration file parent, never current directory",
            "local_paths": {"config": str(config), "prompt": str(selected)} if local_details else None,
            "next_step": "Have the local agent read this exact reviewed file. No arbitrary-file search, download or execution is performed."}


def generation_plan(directory: Path) -> tuple[dict, dict]:
    safe_path(directory)
    desired = {
        "agent.json": json_bytes({"schema_version": 1, "integration": "optional-dispatcher",
            "template_version": TEMPLATE_VERSION, "generator_version": __version__, "prompt_file": "dispatcher.md"}),
        "dispatcher.md": files("grokbot_mcp").joinpath("templates", "dispatcher.md").read_bytes(),
    }
    hashes = {key: digest(value) for key, value in desired.items()}
    for name, value in hashes.items():
        actual = current_hash(directory / name)
        if actual is not None and actual != value:
            raise LocalError("agent_existing_file_changed")
    from grokbot_mcp.identity import path_id
    basis = {"directory_id": path_id("agent-directory", directory), "files": hashes,
             "existing": {name: current_hash(directory / name) for name in desired}}
    return {**basis, "plan_sha256": digest(json_bytes(basis)), "files_written": False,
            "mandatory_for_bridge": False, "template_version": TEMPLATE_VERSION,
            "next_step": "Preview the built-in dispatcher with agent_cli template; use generate --apply --confirm <plan_sha256> only for a chosen private project folder."}, desired


def generate(directory: Path, *, confirm: str) -> dict:
    report, desired = generation_plan(directory)
    if confirm != report["plan_sha256"]:
        raise LocalError("plan_confirmation_mismatch")
    # Exclusive creates and exact-content recovery; never overwrite a customized prompt.
    directory.mkdir(parents=True, exist_ok=True)
    with local_lock(directory / ".grokbot-agent.lock"):
        fresh, _ = generation_plan(directory)
        if fresh["plan_sha256"] != confirm:
            raise LocalError("input_changed")
        for name, raw in desired.items():
            if not (directory / name).exists():
                create_new(directory / name, raw)
        checked = check(directory / "agent.json")
    return {**report, "files_written": True, "check": checked}


def main(argv=None) -> int:
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("operation", choices=("check", "generate", "template"))
        parser.add_argument("--config", type=Path)
        parser.add_argument("--directory", type=Path)
        parser.add_argument("--local-details", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm")
        args = parser.parse_args(argv)
        if (args.apply or args.confirm) and args.operation != "generate" or args.confirm and not args.apply:
            raise LocalError("invalid_arguments")
        if args.operation == "template":
            emit({"template_version": TEMPLATE_VERSION, "mandatory_for_bridge": False,
                  "text": files("grokbot_mcp").joinpath("templates", "dispatcher.md").read_text(encoding="utf-8")})
        elif args.operation == "check":
            if not args.config:
                raise LocalError("invalid_arguments")
            emit(check(args.config.absolute(), local_details=args.local_details))
        else:
            if not args.directory:
                raise LocalError("invalid_arguments")
            path = args.directory.absolute()
            emit(generate(path, confirm=args.confirm) if args.apply else generation_plan(path)[0])
        return 0
    except Exception as exc:
        result = failure(exc)
        result["mandatory_for_bridge"] = False
        emit(result)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
