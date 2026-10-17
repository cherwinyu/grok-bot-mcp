"""MCP stdio handshake. No webhook is sent."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests.support import WORKSPACE_ID, make_workspace


class StdioTest(unittest.TestCase):
    def test_handshake_lists_seven_tools(self) -> None:
        import asyncio

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        with self._workspace() as root_info:
            registry, python = root_info

            async def run() -> list[str]:
                params = StdioServerParameters(
                    command=python,
                    args=["-m", "grokbot_mcp.server"],
                    env={**os.environ, "GROKBOT_MCP_REGISTRY": registry},
                )
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        names = sorted(tool.name for tool in listed.tools)
                        result = await session.call_tool("grok_preflight", {"workspace_id": WORKSPACE_ID})
                        text = "".join(getattr(block, "text", "") for block in result.content)
                        self.assertNotIn("Bearer ", text)
                        self.assertNotIn("https://", text)
                        self.assertIn("\"execution_enabled\":false", text.replace(" ", ""))
                        unknown = await session.call_tool("grok_preflight", {"workspace_id": "not-a-registered-root"})
                        unknown_text = "".join(getattr(block, "text", "") for block in unknown.content)
                        self.assertIn("unknown_workspace", unknown_text)
                        return names

            names = asyncio.run(run())
        self.assertEqual(
            names,
            [
                "grok_get_status",
                "grok_list_workspaces",
                "grok_preflight",
                "grok_prepare_execution",
                "grok_prepare_task",
                "grok_submit_execution",
                "grok_submit_validation",
            ],
        )

    def _workspace(self):
        import tempfile

        return _Workspace(tempfile.TemporaryDirectory())


class _Workspace:
    def __init__(self, directory) -> None:
        self.directory = directory

    def __enter__(self):
        import sys

        root = Path(self.directory.name)
        make_workspace(root)
        return str(root / "registry.json"), sys.executable

    def __exit__(self, exc_type, exc, tb) -> None:
        self.directory.cleanup()
