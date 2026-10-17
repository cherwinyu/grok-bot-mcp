"""Selected client JSON: inspect, dry-run switch, explicit apply and conflict-aware recovery."""
from __future__ import annotations
from pathlib import Path

from grokbot_mcp import client_config as client
from grokbot_mcp.identity import compare_identity
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.local_safety import LocalError, read_document


def main(argv=None) -> int:
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("operation", choices=("inspect", "fragment", "switch", "rollback", "recover", "compare", "transactions"))
        parser.add_argument("--config", type=Path)
        parser.add_argument("--server", default="grokbot-mcp")
        parser.add_argument("--format", choices=("cursor-json", "codex-toml"), default="cursor-json")
        parser.add_argument("--workspace")
        parser.add_argument("--python", type=Path)
        parser.add_argument("--registry", type=Path)
        parser.add_argument("--transaction")
        parser.add_argument("--observed", type=Path)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm")
        parser.add_argument("--probe", action="store_true")
        parser.add_argument("--local-details", action="store_true")
        parser.add_argument("--export", type=Path)
        args = parser.parse_args(argv)
        if args.export and (args.apply or args.probe or args.operation == "fragment" or not args.registry):
            raise LocalError("invalid_arguments")
        if args.apply and args.operation not in {"switch", "rollback", "recover"}:
            raise LocalError("invalid_arguments")
        if args.confirm and not args.apply:
            raise LocalError("invalid_arguments")
        if args.probe and args.operation != "inspect":
            raise LocalError("invalid_arguments")
        if args.format == "codex-toml" and args.operation != "fragment":
            raise LocalError("invalid_arguments")
        if args.operation == "fragment":
            if not all((args.python, args.registry, args.workspace)):
                raise LocalError("invalid_arguments")
            selected = client.fragment(args.python.absolute(), args.registry.absolute(), args.workspace)
            if args.format == "codex-toml":
                from grokbot_mcp.codex_config import fragment_toml
                result = fragment_toml(args.server, selected)
            else:
                result = {"mcpServers": {args.server: selected},
                          "scope": "local configuration fragment only; no file has been changed"}
        else:
            if not args.config:
                raise LocalError("invalid_arguments")
            path = args.config.absolute()
            if args.operation in {"inspect", "compare"}:
                if not args.workspace:
                    raise LocalError("invalid_arguments")
                result = client.inspect_config(path, args.server, args.workspace, local_details=args.local_details)
                if args.probe:
                    from grokbot_mcp.startup_probe import probe_config
                    result = probe_config(path, args.server, args.workspace)
                if args.operation == "compare":
                    if not args.observed:
                        raise LocalError("invalid_arguments")
                    observed, _ = read_document(args.observed.absolute())
                    result = {**result, **compare_identity(result["expected_identity"], observed.get("runtime_identity", observed))}
            elif args.operation == "switch":
                if not all((args.python, args.registry, args.workspace)):
                    raise LocalError("invalid_arguments")
                fields = (path, args.server, args.python.absolute(), args.registry.absolute(), args.workspace)
                result = client.switch(*fields, confirm=args.confirm) if args.apply else client.switch_plan(*fields, local_details=args.local_details)[0]
            elif args.operation == "transactions":
                from grokbot_mcp.local_safety import safe_path
                folder = path.parent / ("." + path.name + ".grokbot-history")
                safe_path(folder)
                result = {"transactions": sorted(p.name for p in folder.glob("*") if p.is_dir() and client.is_uuid(p.name)), "configuration_updated": False}
            else:
                if not args.transaction:
                    raise LocalError("invalid_arguments")
                rollback = args.operation == "rollback"
                result = client.recover(path, args.transaction, rollback=rollback, confirm=args.confirm) if args.apply else client.transaction_plan(path, args.transaction, rollback=rollback)
        if args.export:
            if not args.registry or args.apply or args.probe:
                raise LocalError("invalid_arguments")
            from grokbot_mcp.deployment_diagnostics import export, public_report
            export(args.export.absolute(), result, args.registry.absolute())
            result = public_report(result)
        if args.operation == "fragment" and args.format == "codex-toml":
            print(result, end="")
            return 0
        emit(result)
        return 1 if result.get("ok") is False or result.get("identity_matches") is False else 0
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
