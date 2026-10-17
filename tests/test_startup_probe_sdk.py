"""Pinned SDK response-model regression; mocked transport, real SDK model."""
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from mcp import types
from grokbot_mcp import __version__
from grokbot_mcp.startup_probe import _handshake, TOOL_NAMES


@asynccontextmanager
async def context(value):
    yield value


class StartupProbeSDKTest(unittest.IsolatedAsyncioTestCase):
    def initialized(self):
        return types.InitializeResult(protocol_version="2025-11-25",
            capabilities=types.ServerCapabilities(),
            server_info=types.Implementation(name="grokbot-mcp-bridge", version=__version__))

    async def test_probe_consumes_native_sdk_server_info(self):
        identity = {"bridge_version": __version__, "process_instance_id": "fictional-sdk-fixture"}
        session = SimpleNamespace(initialize=AsyncMock(return_value=self.initialized()),
            list_tools=AsyncMock(return_value=SimpleNamespace(tools=[SimpleNamespace(name=n) for n in TOOL_NAMES])),
            call_tool=AsyncMock(return_value=SimpleNamespace(content=[SimpleNamespace(text=json.dumps({"runtime_identity": identity}))])))
        with patch("mcp.ClientSession", return_value=context(session)), patch("mcp.client.stdio.stdio_client", return_value=context((object(), object()))):
            try:
                result = await _handshake("fictional-python", {}, "example-workspace")
            except AttributeError:
                self.fail("existing startup probe cannot consume the pinned SDK initialize response model")
        self.assertEqual(result["server_version"], __version__)
        self.assertEqual(result["runtime_identity"], identity)
        self.assertTrue(result["standalone_handshake_passed"])
        self.assertFalse(result["client_reconnection_confirmed"])
        self.assertFalse(result["credential_values_read"])
        self.assertFalse(result["webhook_sent"])
        session.call_tool.assert_awaited_once_with("grok_preflight", {"workspace_id": "example-workspace"})

    async def test_public_sdk_uses_python_names_not_wire_aliases(self):
        result = self.initialized()
        self.assertEqual(result.server_info.version, __version__)
        self.assertFalse(hasattr(result, "serverInfo"))
        self.assertIn("server_info", type(result).model_fields)
