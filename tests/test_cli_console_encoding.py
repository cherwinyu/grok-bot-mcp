"""Real subprocess console codecs; operation results are memory-only doubles."""
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from grokbot_mcp.local_cli import emit


VALUE = {"ok": True, "text": "中文 é 😀 \\ 路径\n下一行"}


class ConsoleEncodingTest(unittest.TestCase):
    def test_mcp_stdio_json_under_legacy_console_encodings(self):
        # The server speaks MCP, not a single CLI JSON document. The existing
        # fictional handshake uses the SDK to parse initialization and tools.
        for encoding in ("cp1252", "cp936"):
            with self.subTest(encoding=encoding):
                result = subprocess.run(
                    [sys.executable, "-B", "-m", "unittest", "tests.test_stdio"],
                    env={**os.environ, "PYTHONIOENCODING": encoding, "PYTHONUTF8": "0",
                         "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_utf8_bytes_unchanged_and_legacy_lossless(self):
        for encoding in ("utf-8", "cp1252", "cp936"):
            with self.subTest(encoding=encoding):
                buffer = io.BytesIO()
                stream = io.TextIOWrapper(buffer, encoding=encoding, newline="\n")
                with patch("sys.stdout", stream):
                    emit(VALUE)
                stream.flush()
                raw = buffer.getvalue()
                self.assertEqual(stream.encoding, encoding)
                self.assertEqual(json.loads(raw.decode(encoding)), VALUE)
                if encoding == "utf-8":
                    self.assertEqual(raw, (json.dumps(VALUE, ensure_ascii=False, indent=2) + "\n").encode())
                else:
                    self.assertTrue(raw.isascii())
                stream.detach()

    def test_cli_success_results_across_console_encodings(self):
        # Exercise each JSON entry's parser, result routing, serializer and exit
        # status without accessing a real registry, configuration or ledger.
        cases = [
            ("client_cli", "grokbot_mcp.client_config.fragment", ["fragment", "--python", "fictional.exe", "--registry", "fictional.json", "--workspace", "fictional"]),
            ("result_cli", "grokbot_mcp.result_cli.precheck", ["--registry", "fictional.json", "--workspace", "fictional", "--phase", "validate", "--id", "fictional", "--staged", "fictional.tmp"]),
            ("migration_cli", "grokbot_mcp.migration.plan", ["plan", "--registry", "fictional.json", "--workspace", "fictional", "--python", "fictional.exe"]),
            ("agent_cli", "grokbot_mcp.agent_cli.check", ["check", "--config", "fictional.json"]),
            ("safety_cli", "grokbot_mcp.safety.preview", ["preview", "--registry", "fictional.json", "--workspace", "fictional"]),
            ("admin_cli", "grokbot_mcp.registry_prefixes.preview", ["registry-prefixes", "--registry", "fictional.json", "--workspace", "fictional"]),
            ("test_run_cli", "grokbot_mcp.test_run_cli.compact", ["--registry", "fictional.json", "--workspace", "fictional", "fictional"]),
            ("test_command_cli", "grokbot_mcp.test_command_cli.preview", ["--registry", "fictional.json", "--workspace", "fictional", "fictional"]),
        ]
        for module, target, args in cases:
            baseline = None
            for encoding in ("utf-8", "cp1252", "cp936"):
                with self.subTest(module=module, encoding=encoding):
                    script = (
                        "from unittest.mock import patch; "
                        f"from grokbot_mcp import {module} as cli; "
                        f"value={ascii(VALUE)}; "
                        f"patch({target!r}, return_value=value).start(); "
                    )
                    if module == "test_run_cli":
                        script += "patch.object(cli, 'load_registry', return_value={'fictional': object()}).start(); patch.object(cli, 'rerun', return_value={}).start(); "
                    script += f"raise SystemExit(cli.main({args!r}))"
                    env = {**os.environ, "PYTHONIOENCODING": encoding, "PYTHONUTF8": "0"}
                    result = subprocess.run([sys.executable, "-B", "-c", script], env=env, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    value = json.loads(result.stdout.decode(encoding))
                    if baseline is None:
                        baseline = value
                    self.assertEqual(value, baseline)
                    payload = value["mcpServers"]["grokbot-mcp"] if module == "client_cli" else value
                    self.assertEqual(payload, VALUE)

    def test_real_module_diagnostics_are_lossless(self):
        for module in ("client_cli", "result_cli", "migration_cli", "agent_cli", "safety_cli", "admin_cli", "test_run_cli", "test_command_cli"):
            baseline = None
            for encoding in ("utf-8", "cp1252", "cp936"):
                with self.subTest(module=module, encoding=encoding):
                    result = subprocess.run([sys.executable, "-B", "-m", "grokbot_mcp." + module],
                        env={**os.environ, "PYTHONIOENCODING": encoding, "PYTHONUTF8": "0"}, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    value = json.loads(result.stdout.decode(encoding))
                    self.assertFalse(value["ok"])
                    if baseline is None:
                        baseline = value
                    self.assertEqual(value, baseline)
