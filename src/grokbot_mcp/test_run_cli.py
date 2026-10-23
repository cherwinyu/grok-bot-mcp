"""Explicit local test rerun for a settled execution; no sends or reconciliation."""
from __future__ import annotations

import json
import os
from pathlib import Path

from grokbot_mcp.bridge_tests import compact, rerun
from grokbot_mcp.local_cli import Parser, failure
from grokbot_mcp.registry import default_registry_path, load_registry
from grokbot_mcp.setup_wizard import _configure_console


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = Parser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    parser.add_argument("--workspace", required=True)
    parser.add_argument("execution_id")
    previous = os.environ.get("GROKBOT_MCP_REGISTRY")
    try:
        args = parser.parse_args(argv)
        registry = args.registry.absolute()
        workspace = load_registry(registry).get(args.workspace)
        if workspace is None:
            raise ValueError("unknown_workspace")
        # Existing settlement checks resolve the binding through this variable.
        os.environ["GROKBOT_MCP_REGISTRY"] = str(registry)
        result = compact(rerun(workspace, args.execution_id))
    except Exception as exc:
        result = failure(exc)
    finally:
        if previous is None:
            os.environ.pop("GROKBOT_MCP_REGISTRY", None)
        else:
            os.environ["GROKBOT_MCP_REGISTRY"] = previous
    # ASCII JSON escapes preserve Unicode evidence on a legacy console as well.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
