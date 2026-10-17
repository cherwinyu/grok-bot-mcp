"""stdio MCP server. Execution stays off unless the local switch file is enabled."""

from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)

from mcp.server import MCPServer

from grokbot_mcp import facade, __version__

mcp = MCPServer(
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
    """List registered workspace ids. Sends no webhook and does not reveal keys."""
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
) -> dict:
    """Write one local manifest. The server owns the root, id, and file policy."""
    return facade.prepare_task(
        workspace_id,
        task_type,
        task_description,
        allowed_files,
        acceptance_criteria,
        test_command_id,
        execution_timeout,
        baseline_commit,
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
                    observe_only: bool = False, verify_current: bool = False) -> dict:
    """Observe local evidence. Default reconciles this ledger; observe_only never writes it.

    verify_current rechecks evidence/workspace; cached success is historical, not QA.
    Neither mode sends or retries a webhook.
    """
    return facade.get_status(workspace_id, task_id, execution_id,
                             observe_only=observe_only, verify_current=verify_current)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
