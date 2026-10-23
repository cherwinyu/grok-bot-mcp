"""Claim one execution. This script never sends a webhook.

Usage:
  python -m grokbot_mcp.claim_cli --workspace <workspace_id> --expect-fingerprint <64 lowercase hex> <execution_id>
  python -m grokbot_mcp.claim_cli --workspace <workspace_id> <execution_id>
  python -m grokbot_mcp.claim_cli <execution_id>

The workspace-less form works only when the registry contains exactly one workspace.
The optional fingerprint comes from this Routine's paste packet, not the trigger.
The execution id is taken from the webhook payload. Do not invent a filename.
"""

from __future__ import annotations

import json
import re
import sys

from grokbot_mcp.facade import claim_for, list_workspaces


def _expected_argument(argv: list[str]) -> tuple[list[str], str | None, str | None]:
    args = list(argv)
    if "--expect-fingerprint" not in args:
        return args, None, None
    index = args.index("--expect-fingerprint")
    if (args.count("--expect-fingerprint") != 1 or index + 1 == len(args)
            or re.fullmatch(r"[0-9a-f]{64}", args[index + 1]) is None):
        return args, None, "invalid_expected_fingerprint"
    expected = args[index + 1]
    del args[index:index + 2]
    return args, expected, None


def parse_cli(argv: list[str]) -> tuple[str | None, str | None, str | None]:
    argv, _expected, error = _expected_argument(argv)
    if error:
        return None, None, error
    if not argv:
        return None, None, "missing_argument"
    workspace_id = None
    args = list(argv)
    if args[0] == "--workspace":
        if len(args) != 3:
            return None, None, "too_many_arguments" if len(args) > 3 else "missing_argument"
        workspace_id = args[1]
        execution_id = args[2]
    elif len(args) == 1:
        execution_id = args[0]
    else:
        return None, None, "too_many_arguments"
    if workspace_id is None:
        listed = list_workspaces()
        if listed.get("error"):
            return None, None, str(listed["error"])
        items = listed.get("workspaces")
        if not isinstance(items, list) or len(items) != 1:
            return None, None, "workspace_required"
        workspace_id = str(items[0].get("workspace_id"))
    return workspace_id, execution_id, None


def main(argv: list[str] | None = None) -> int:
    from grokbot_mcp.setup_wizard import _configure_console
    _configure_console()
    args, expected, error = _expected_argument(list(sys.argv[1:] if argv is None else argv))
    if error:
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=True))
        return 2
    workspace_id, execution_id, error = parse_cli(args)
    if error is not None or workspace_id is None or execution_id is None:
        print(json.dumps({"ok": False, "error": error or "missing_argument"}))
        return 2
    result = (claim_for(workspace_id, execution_id) if expected is None else
              claim_for(workspace_id, execution_id, expected_fingerprint=expected))
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
