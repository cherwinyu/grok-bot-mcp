"""stdio MCP server. Execution stays off unless the local switch file is enabled."""

from __future__ import annotations

import logging
import inspect
import json
import sys

logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent

from grokbot_mcp import facade, __version__
from grokbot_mcp.tool_errors import invalid_argument, internal_error


class BridgeServer(MCPServer):
    async def call_tool(self, name, arguments, context=None):
        function = globals().get(name) if name in TOOL_NAMES else None
        if function is not None:
            signature = inspect.signature(function)
            try:
                bound = signature.bind(**arguments)
                for key, value in bound.arguments.items():
                    annotation = signature.parameters[key].annotation
                    if annotation == "str" and type(value) is not str:
                        raise TypeError
                    if annotation == "bool" and type(value) is not bool:
                        raise TypeError
                    if key == "wait_seconds" and (type(value) is not int or not 0 <= value <= 50):
                        raise TypeError
            except TypeError:
                return _error_result(invalid_argument(list(signature.parameters)))
        try:
            return await super().call_tool(name, arguments, context)
        except Exception as exc:
            return _error_result(internal_error(exc))


def _error_result(value):
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(value))],
                          structured_content=value, is_error=True)


TOOL_NAMES = frozenset({"grok_list_workspaces", "grok_preflight", "grok_prepare_task",
                        "grok_submit_validation", "grok_prepare_execution",
                        "grok_submit_execution", "grok_get_status"})

mcp = BridgeServer(
    name="grokbot-mcp-bridge",
    version=__version__,
    instructions=(
        "Unofficial local bridge for an administrator-registered Grok Bot workspace. "
        "grok_prepare_task writes a local manifest and sends no webhook. "
        "grok_submit_validation and grok_submit_execution do not send a webhook "
        "while the local execution switch is false or the routine is unconfirmed. "
        "HTTP 200 means accepted, not succeeded. qa_accepted is always false. "
        "Do not pass a webhook URL, shell command, absolute path, or model. "
        "Never ask for or repeat webhook credentials."
    ),
    log_level="WARNING",
)


@mcp.tool(structured_output=False)
def grok_list_workspaces() -> dict:
    """List registered workspace ids. A missing registry returns ok=false, not an empty list. Sends no webhook and does not reveal keys."""
    return facade.list_workspaces()


@mcp.tool(structured_output=False)
def grok_preflight(workspace_id: str) -> dict:
    """Read git, credential presence, and the execution switch. Sends no webhook."""
    return facade.preflight(workspace_id)


@mcp.tool(structured_output=False)
def grok_prepare_task(
    workspace_id: str,
    task_type: str,
    task_description: str,
    allowed_files: list[str],
    acceptance_criteria: list[str],
    test_command_id: str,
    execution_timeout: int | None = None,
    baseline_commit: str | None = None,
    allowed_globs: list[str] | None = None,
    create_files: list[str] | None = None,
) -> dict:
    """Write one local manifest. The server owns the root, id, and file policy.

    Pass execution_timeout=N (seconds) to set this task's remote deadline.
    It must be a positive integer at or below the registered command's
    timeout_cap, which cannot exceed the global ceiling of 3600 seconds.
    Omit it to use the command's default_timeout (at or below timeout_cap).
    Newly registered code-test commands default to 3600 seconds.
    """
    return facade.prepare_task(
        workspace_id,
        task_type,
        task_description,
        allowed_files,
        acceptance_criteria,
        test_command_id,
        execution_timeout,
        baseline_commit,
        **({"allowed_globs": allowed_globs} if allowed_globs is not None else {}),
        **({"create_files": create_files} if create_files is not None else {}),
    )


@mcp.tool(structured_output=False)
def grok_submit_validation(workspace_id: str, task_id: str) -> dict:
    """Validation submit. Refuses before any POST when the routine or credential is missing."""
    return facade.submit_validation_tool(workspace_id, task_id)


@mcp.tool(structured_output=False)
def grok_prepare_execution(workspace_id: str, manifest_task_id: str) -> dict:
    """Create one local execution authorization. Sends no webhook."""
    return facade.prepare_execution_tool(workspace_id, manifest_task_id)


@mcp.tool(structured_output=False)
def grok_submit_execution(workspace_id: str, execution_id: str) -> dict:
    """Submit execute_manifest. The default switch returns before any POST."""
    return facade.submit_execution_tool(workspace_id, execution_id)


@mcp.tool(structured_output=False)
def grok_get_status(workspace_id: str, task_id: str = "", execution_id: str = "",
                    observe_only: bool = False, verify_current: bool = False,
                    rerun_tests: bool = False, wait_seconds: int = 0) -> dict:
    """Observe local evidence. Default reconciles this ledger; observe_only never writes it.

    verify_current rechecks evidence/workspace; cached success is historical, not QA.
    Neither mode sends or retries a webhook.
    rerun_tests explicitly runs the registered local test for a settled execution;
    it requires execution_id and cannot be combined with observe_only.
    wait_seconds (0..50) polls read-only before the final query and any terminal rerun.
    """
    return facade.get_status(workspace_id, task_id, execution_id,
                             observe_only=observe_only, verify_current=verify_current,
                             **({"rerun_tests": True} if rerun_tests else {}),
                             **({"wait_seconds": wait_seconds} if type(wait_seconds) is not int or wait_seconds != 0 else {}))


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in {"ledger-archive", "routine-check", "registry-prefixes"}:
        from grokbot_mcp.admin_cli import main as admin_main
        raise SystemExit(admin_main(sys.argv[1:]))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
