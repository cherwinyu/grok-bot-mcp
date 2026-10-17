"""Preview/apply local migration; remote review always requires explicit attestation."""
from pathlib import Path
from grokbot_mcp import migration
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.local_safety import LocalError, safe_path
from grokbot_mcp.registry import load_registry


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("operation", choices=("plan", "apply", "finalize", "recover", "rollback", "transactions"))
        parser.add_argument("--registry", required=True, type=Path)
        parser.add_argument("--target-registry", type=Path)
        parser.add_argument("--workspace", required=True)
        parser.add_argument("--python", type=Path)
        parser.add_argument("--transaction")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm")
        parser.add_argument("--remote-reviewed", action="store_true")
        parser.add_argument("--routine-status", choices=("enabled", "paused"), default="enabled")
        parser.add_argument("--local-details", action="store_true")
        args = parser.parse_args(argv)
        registry = args.registry.absolute()
        target = args.target_registry.absolute() if args.target_registry else registry
        if args.operation in {"plan", "transactions"} and args.apply or args.confirm and not args.apply:
            raise LocalError("invalid_arguments")
        if args.remote_reviewed and not (args.apply and args.operation == "finalize"):
            raise LocalError("invalid_arguments")
        if args.operation in {"plan", "apply"}:
            if not args.python:
                raise LocalError("invalid_arguments")
            if args.apply:
                result = migration.apply_plan(registry, args.workspace, args.python.absolute(), target_registry=target, confirm=args.confirm)
            else:
                result = migration.plan(registry, args.workspace, args.python.absolute(), target_registry=target, local_details=args.local_details)
        elif args.operation == "transactions":
            load_registry(registry)[args.workspace]
            folder = registry.parent / "migrations" / args.workspace
            safe_path(folder)
            result = {"transactions": sorted(p.name for p in folder.glob("*") if p.is_dir() and migration.is_uuid(p.name)), "local_configuration_applied": False}
        else:
            if not args.transaction:
                raise LocalError("invalid_arguments")
            if args.apply:
                result = migration.finish(registry, args.workspace, args.transaction, operation=args.operation, confirm=args.confirm,
                    target_registry=target, remote_review_confirmed=args.remote_reviewed, routine_status=args.routine_status)
            else:
                result = migration.transaction_plan(registry, args.workspace, args.transaction, target_registry=target, operation=args.operation)
        emit(result)
        return 1 if result.get("ok") is False else 0
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
