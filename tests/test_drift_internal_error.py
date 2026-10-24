"""Safe drift hints with synthetic identity/workspace objects and memory doubles."""
from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from grokbot_mcp import facade, identity, tool_errors


LEGACY_BYTES = (
    b'{"ok": false, "error": "internal_error", "exception_type": "AttributeError", '
    b'"reason": "Local operation failed. Inspect local diagnostics; do not resend."}'
)
PRIVATE_MESSAGE = "fictional-sensitive-message D:/fictional/private/secret.txt"


class DriftInternalErrorTest(unittest.TestCase):
    def setUp(self):
        # Follow DriftContractTest: change only the in-memory digest results.
        self.package = self.enterContext(patch.object(identity, "package_digest", return_value="package-before"))
        self.registry = self.enterContext(patch.object(identity, "registry_digest", return_value="registry-before"))
        self.enterContext(patch.object(identity, "default_registry_path", return_value=Path("D:/fictional/registry.json")))
        runtime = identity.RuntimeIdentity(package=Path("D:/fictional/package"))
        self.enterContext(patch.object(identity, "_RUNTIME", runtime))

    def capture(self, call, kind="AttributeError"):
        with self.assertLogs("grokbot_mcp.tool_errors", level="ERROR") as logs:
            result = call()
        self.assertEqual(logs.output, [
            "ERROR:grokbot_mcp.tool_errors:internal_error exception_type=" + kind
        ])
        for record in logs.records:
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)
        rendered = json.dumps(result) + str(logs.output)
        for marker in (PRIVATE_MESSAGE, "fictional-sensitive-message", "D:/fictional", "Traceback"):
            self.assertNotIn(marker, rendered)
        return result

    def failing_tool(self):
        @tool_errors.guarded
        def tool():
            raise AttributeError(PRIVATE_MESSAGE)
        return self.capture(tool)

    def assert_restart_hint(self, result, kind="AttributeError"):
        original = {key: value for key, value in result.items() if key not in {"warnings", "next_step"}}
        self.assertEqual(json.dumps(original).encode("utf-8"),
                         LEGACY_BYTES.replace(b"AttributeError", kind.encode("ascii")))
        self.assertEqual(result["warnings"], ["process_restart_recommended"])
        self.assertIn("Restart the MCP client process", result["next_step"])
        self.assertIn("重启 MCP 客户端进程", result["next_step"])
        self.assertIn("不要重发或重试提交", result["next_step"])

    def test_changed_package_adds_hint_to_guarded_attribute_error(self):
        self.package.return_value = "package-after"
        self.assert_restart_hint(self.failing_tool())

    def test_unchanged_package_preserves_exact_response_bytes(self):
        self.assertEqual(json.dumps(self.failing_tool()).encode("utf-8"), LEGACY_BYTES)

    def test_registry_only_drift_preserves_exact_response_bytes(self):
        self.registry.return_value = "registry-after"
        self.assertEqual(json.dumps(self.failing_tool()).encode("utf-8"), LEGACY_BYTES)

    def test_drift_failure_preserves_original_error_and_redacts_diagnostic(self):
        with patch.object(identity, "runtime_drift", side_effect=RuntimeError(PRIVATE_MESSAGE)) as drift:
            self.assertEqual(json.dumps(self.failing_tool()).encode("utf-8"), LEGACY_BYTES)
        drift.assert_called_once_with()

    def test_diagnostic_import_failure_preserves_original_error(self):
        import builtins
        original_import = builtins.__import__

        def fail_identity(name, *args, **kwargs):
            if name == "grokbot_mcp.identity":
                raise ImportError(PRIVATE_MESSAGE)
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_identity):
            self.assertEqual(json.dumps(self.failing_tool()).encode("utf-8"), LEGACY_BYTES)

    def test_preflight_missing_field_is_covered_before_normal_drift_check(self):
        self.package.return_value = "package-after"
        workspace = SimpleNamespace(root=Path("D:/fictional/workspace"), allowed_files=(), expected_head="a" * 40)
        with patch.object(facade, "_workspace", return_value=(workspace, None, [])), \
             patch.object(facade, "runtime_drift") as normal_drift, \
             patch.object(facade, "git_snapshot") as git:
            self.assert_restart_hint(self.capture(lambda: facade.preflight("fictional")))
        normal_drift.assert_not_called()
        git.assert_not_called()

    def test_list_missing_field_is_covered_before_normal_drift_check(self):
        self.package.return_value = "package-after"
        workspace = SimpleNamespace(workspace_id="fictional", expected_head="a" * 40, allowed_files=(),
                                    writes_enabled=False, validate_credential_target="memory-validate",
                                    execute_credential_target="memory-execute")
        with patch.object(facade, "default_registry_path", return_value=Path("D:/fictional/registry.json")), \
             patch.object(facade, "load_registry", return_value={"fictional": workspace}), \
             patch.object(facade, "runtime_drift") as normal_drift:
            self.assert_restart_hint(self.capture(facade.list_workspaces))
        normal_drift.assert_not_called()

    def test_server_exception_boundary_also_gets_restart_hint(self):
        from grokbot_mcp.server import mcp

        self.package.return_value = "package-after"
        with patch.object(facade, "get_status", side_effect=AttributeError(PRIVATE_MESSAGE)):
            def call():
                result = asyncio.run(mcp.call_tool("grok_get_status", {"workspace_id": "fictional"}))
                self.assertTrue(result.is_error)
                self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                return result.structured_content
            # The SDK wraps the tool exception before BridgeServer catches it.
            # Preserve that existing exception type as well as the error code.
            self.assert_restart_hint(self.capture(call, "UnexpectedToolError"), "UnexpectedToolError")


if __name__ == "__main__":
    unittest.main()
