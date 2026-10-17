"""P0 operator tools. Preview/observe by default; no operation sends a Webhook."""
from __future__ import annotations
from pathlib import Path
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.local_safety import LocalError, read_document, create_new, json_bytes
from grokbot_mcp import safety, canary


def main(argv=None) -> int:
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("operation", choices=("preview", "credential-plan", "canary", "acceptance", "profile", "demo"))
        parser.add_argument("--registry", type=Path)
        parser.add_argument("--workspace")
        parser.add_argument("--task")
        parser.add_argument("--execution")
        parser.add_argument("--observed-identity", type=Path)
        parser.add_argument("--profile", type=Path)
        parser.add_argument("--directory", type=Path)
        parser.add_argument("--local-details", action="store_true")
        parser.add_argument("--check-presence", action="store_true")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm")
        parser.add_argument("--export", type=Path)
        args = parser.parse_args(argv)
        if ((args.apply or args.confirm) and args.operation != "canary" or args.confirm and not args.apply
                or args.check_presence and args.operation != "credential-plan"
                or args.observed_identity and args.operation != "acceptance"
                or args.export and (args.apply or args.operation in {"canary", "profile", "demo"})):
            raise LocalError("invalid_arguments")
        if args.operation == "demo":
            if any((args.registry, args.workspace, args.task, args.execution, args.directory, args.profile, args.export, args.local_details)):
                raise LocalError("invalid_arguments")
            from grokbot_mcp.offline_demo import run_demo
            result = run_demo()
        elif args.operation == "profile":
            if any((args.registry, args.workspace, args.directory, args.task, args.execution, args.local_details)):
                raise LocalError("invalid_arguments")
            from grokbot_mcp.execution_profile import parse, recommended
            value = read_document(args.profile.absolute())[0] if args.profile else recommended()
            parse(value)
            result = {"ok": True, "profile": value, "registry_written": False, "send_authorized": False,
                      "scope": "configuration syntax only; use reviewed registry setup, not this output as authorization"}
        elif args.operation == "canary":
            if not args.directory or any((args.registry, args.workspace, args.task, args.execution, args.profile, args.local_details)):
                raise LocalError("invalid_arguments")
            directory = args.directory.absolute()
            result = canary.create(directory, confirm=args.confirm) if args.apply else canary.plan(directory)
        else:
            if not args.registry or not args.workspace or args.directory or args.profile:
                raise LocalError("invalid_arguments")
            registry = args.registry.absolute()
            if args.operation == "preview":
                result = safety.preview(registry, args.workspace, task_id=args.task, execution_id=args.execution,
                                        local_details=args.local_details)
            elif args.operation == "credential-plan":
                if args.task or args.execution or args.local_details:
                    raise LocalError("invalid_arguments")
                result = safety.credential_plan(registry, args.workspace, check_presence=args.check_presence)
            else:
                if not args.task or not args.execution or args.local_details:
                    raise LocalError("invalid_arguments")
                observed = read_document(args.observed_identity.absolute())[0] if args.observed_identity else None
                result = safety.acceptance(registry, args.workspace, args.task, args.execution, observed_identity=observed)
            if args.export:
                from grokbot_mcp.setup_wizard import validate_export_destination
                destination = validate_export_destination(args.export.absolute(), registry)
                result = safety.shareable(result)
                create_new(destination, json_bytes(result))
        emit(result)
        return int(result.get("ok") is False)
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
