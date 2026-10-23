"""Claim one execution. This script never sends a webhook.

Usage:
  python -m grokbot_mcp.claim_cli --workspace <workspace_id> <execution_id>
  python -m grokbot_mcp.claim_cli <execution_id>

The second form works only when the registry contains exactly one workspace.
The execution id is taken from the webhook payload. Do not invent a filename.
"""

from __future__ import annotations

import json
import sys

from grokbot_mcp.facade import claim_for, list_workspaces


def parse_cli(argv: list[str]) -> tuple[str | None, str | None, str | None]:
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
    workspace_id, execution_id, error = parse_cli(list(sys.argv[1:] if argv is None else argv))
    if error is not None or workspace_id is None or execution_id is None:
        print(json.dumps({"ok": False, "error": error or "missing_argument"}))
        return 2
    result = claim_for(workspace_id, execution_id)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
