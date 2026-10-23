"""Local administrator maintenance; preview by default and never a send."""
from pathlib import Path

from grokbot_mcp.local_cli import Parser, emit, failure


def main(argv=None) -> int:
    from grokbot_mcp.setup_wizard import _configure_console
    _configure_console()
    try:
        parser = Parser(description=__doc__)
        commands = parser.add_subparsers(dest="operation", required=True)
        archive = commands.add_parser("ledger-archive")
        archive.add_argument("--task-id", required=True)
        archive.add_argument("--reason", required=True)
        archive.add_argument("--confirm", action="store_true")
        archive.add_argument("--accept-late-result-risk", action="store_true")
        check = commands.add_parser("routine-check")
        prefixes = commands.add_parser("registry-prefixes")
        prefixes.add_argument("--write-to", type=Path)
        prefixes.add_argument("--add", action="append", default=[])
        prefixes.add_argument("--remove", action="append", default=[])
        prefixes.add_argument("--apply", action="store_true")
        for command in (archive, check, prefixes):
            command.add_argument("--registry", required=True, type=Path)
            command.add_argument("--workspace", required=True)
        args = parser.parse_args(argv)
        if args.operation == "ledger-archive":
            from grokbot_mcp.ledger_archive import archive as run
            result = run(args.registry, args.workspace, args.task_id, reason=args.reason,
                         confirm=args.confirm, accept_late_result_risk=args.accept_late_result_risk)
        elif args.operation == "registry-prefixes":
            from grokbot_mcp.registry_prefixes import preview, change
            if args.add or args.remove or args.apply:
                if args.write_to is not None:
                    raise ValueError("invalid_arguments")
                result = change(args.registry, args.workspace, add=args.add, remove=args.remove, apply=args.apply)
            else:
                result = preview(args.registry, args.workspace, write_to=args.write_to)
        else:
            from grokbot_mcp.routine_check import check as run
            result = run(args.registry, args.workspace)
        emit(result)
        return int(result.get("ok") is False)
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
