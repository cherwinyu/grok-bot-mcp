"""Reviewable Windows Codex CLI TOML snippets; existing Cursor JSON unchanged."""
from __future__ import annotations

import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from grokbot_mcp import client_cli
from grokbot_mcp import client_config as client
from grokbot_mcp.codex_config import fragment_toml
from grokbot_mcp.local_safety import LocalError
from tests.deployment_support import DeploymentCase, fake_install
from tests.support import WORKSPACE_ID


class CodexClientFragmentTest(DeploymentCase):
    def setUp(self):
        super().setUp()
        self.python = fake_install(self.base / "模拟 Codex CLI 安装 spaces")
        self.args = ["fragment", "--python", str(self.python),
                     "--registry", str(self.registry), "--workspace", WORKSPACE_ID]

    def render(self, *extra):
        stream = io.StringIO()
        with patch("grokbot_mcp.credentials.windows._apis",
                   side_effect=AssertionError("credential access prohibited")), \
             patch("grokbot_mcp.transport.webhook.post_once",
                   side_effect=AssertionError("webhook access prohibited")), \
             redirect_stdout(stream):
            code = client_cli.main([*self.args, *extra])
        return code, stream.getvalue()

    def test_codex_toml_roundtrip_unicode_and_no_workspace_changes(self):
        before = self.files(self.base)
        code, text = self.render("--format", "codex-toml")
        self.assertEqual(code, 0)
        entry = tomllib.loads(text)["mcp_servers"]["grokbot-mcp"]
        self.assertEqual(entry, client.fragment(self.python, self.registry, WORKSPACE_ID))
        self.assertEqual(entry["env"], {"GROKBOT_MCP_REGISTRY": str(self.registry)})
        self.assertIn("模拟", entry["command"])
        self.assertEqual(before, self.files(self.base))
        self.assertEqual(self.case.transport.calls, [])

    def test_custom_server_unicode_and_spaces_stays_one_table(self):
        code, output = self.render("--format", "codex-toml", "--server", "Grok Bot 任务桥")
        self.assertEqual(code, 0)
        parsed = tomllib.loads(output)
        self.assertEqual(list(parsed["mcp_servers"]), ["Grok Bot 任务桥"])
        self.assertNotIn("Bearer ", output)

    def test_existing_cursor_json_default_preserved(self):
        code, output = self.render()
        self.assertEqual(code, 0)
        document = json.loads(output)
        self.assertEqual(document["mcpServers"]["grokbot-mcp"],
                         client.fragment(self.python, self.registry, WORKSPACE_ID))
        self.assertIn("no file has been changed", document["scope"])

    def test_injected_server_name_refused_without_change(self):
        before = self.files(self.base)
        code, output = self.render("--format", "codex-toml", "--server", "bad\n[mcp_servers.evil]")
        self.assertEqual(code, 1)
        self.assertIn("invalid_arguments", output)
        self.assertEqual(before, self.files(self.base))

    def test_toml_is_fragment_only(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = client_cli.main(["inspect", "--format", "codex-toml",
                                    "--config", str(self.base / "missing.json"),
                                    "--workspace", WORKSPACE_ID])
        self.assertEqual(code, 1)
        self.assertIn("invalid_arguments", output.getvalue())

    def test_unknown_environment_fields_are_refused(self):
        with self.assertRaisesRegex(LocalError, "invalid_arguments"):
            fragment_toml("server", {"command": "python", "args": ["-m", "module"],
                                    "env": {"GROKBOT_MCP_REGISTRY": "test",
                                            "EXTRA": "unapproved"}})
        with self.assertRaisesRegex(LocalError, "invalid_arguments"):
            fragment_toml("server\n[other]", {"command": "python",
                                            "args": ["-m", "module"],
                                            "env": {"GROKBOT_MCP_REGISTRY": "test"}})
