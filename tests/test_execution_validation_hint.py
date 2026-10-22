"""Tests for validation_missing hint on prepare_execution."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import facade
from grokbot_mcp.execution import service
from tests.support import WORKSPACE_ID, make_workspace

HINT = "run grok_submit_validation for this manifest first, then call grok_prepare_execution again"


class ExecutionValidationHintTest(unittest.TestCase):
    def tearDown(self) -> None:
        facade.install_transport(None)

    def test_validation_missing_includes_exact_hint(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            make_workspace(Path(name))
            prepared = facade.prepare_task(
                WORKSPACE_ID,
                "code_change",
                "Add a label helper.",
                ["src/counter.py"],
                ["The helper exists."],
                "python_unittest",
            )
            result = facade.prepare_execution_tool(WORKSPACE_ID, prepared["task_id"])
            self.assertEqual(result["error"], "validation_missing")
            self.assertEqual(result["hint"], HINT)
            self.assertEqual(result["approval_state"], "denied")
            self.assertIs(result["accepted"], False)

    def test_other_errors_omit_hint(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            make_workspace(Path(name), writes=False)
            result = facade.prepare_execution_tool(WORKSPACE_ID, str(uuid.uuid4()))
            self.assertEqual(result["error"], "workspace_writes_disabled")
            self.assertNotIn("hint", result)

        with tempfile.TemporaryDirectory() as name:
            make_workspace(Path(name))
            result = facade.prepare_execution_tool(WORKSPACE_ID, "not-a-uuid")
            self.assertEqual(result["error"], "invalid_task_id")
            self.assertNotIn("hint", result)

        workspace = object()
        with patch.object(service, "assess", return_value={"ok": False, "error": "not_prepared", "manifest_sha256": "abc"}):
            with patch.object(service, "is_uuid", return_value=True):
                # Build a minimal stand-in with writes_enabled
                class WS:
                    writes_enabled = True

                payload = service.prepare_execution(WS(), str(uuid.uuid4()), [])  # type: ignore[arg-type]
        self.assertEqual(payload["error"], "not_prepared")
        self.assertNotIn("hint", payload)


if __name__ == "__main__":
    unittest.main()
