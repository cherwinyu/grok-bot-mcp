"""Additional fail-closed checks. No socket is opened."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests.support import temporary_directory

from grokbot_mcp import facade
from grokbot_mcp.transport.webhook import post_once
from tests.support import WORKSPACE_ID, make_workspace


class EdgeTest(unittest.TestCase):
    def tearDown(self) -> None:
        facade.install_transport(None)

    def test_disabled_post_does_not_call_factory(self) -> None:
        def factory(*_args):
            raise AssertionError("socket")

        outcome = post_once(
            "https://example.invalid/hook",
            "example-key",
            {"protocol_version": 1, "action": "execute_manifest", "execution_id": "x", "manifest_task_id": "y"},
            1,
            enabled=False,
            connection_factory=factory,
        )
        self.assertEqual(outcome.kind, "disabled")

    def test_writes_disabled_and_untracked_file(self) -> None:
        with temporary_directory() as name:
            root = make_workspace(Path(name), writes=False)
            denied = facade.prepare_task(
                WORKSPACE_ID,
                "code_change",
                "Add a label helper.",
                ["src/counter.py"],
                ["The helper exists."],
                "python_unittest",
            )
            self.assertEqual(denied["error"], "workspace_writes_disabled")
            self.assertFalse(denied["webhook_sent"])
        with temporary_directory() as name:
            root = make_workspace(Path(name), writes=True)
            (root / "src/untracked_marker.py").write_text("draft\n", encoding="utf-8")
            denied = facade.prepare_task(
                WORKSPACE_ID,
                "code_change",
                "Add a label helper.",
                ["src/counter.py"],
                ["The helper exists."],
                "python_unittest",
            )
            self.assertEqual(denied["error"], "unconfirmed_changes")
            self.assertFalse((root / ".bridge" / "task_specs").exists())

    def test_terminal_execution_cannot_be_claimed_again(self) -> None:
        with temporary_directory() as name:
            root = make_workspace(Path(name))
            prepared = facade.prepare_task(
                WORKSPACE_ID,
                "code_change",
                "Add a label helper.",
                ["src/counter.py"],
                ["The helper exists."],
                "python_unittest",
            )
            switch = Path(os.environ["GROKBOT_MCP_REGISTRY"]).parent / "execution_switch.json"
            switch.write_text('{"enabled": true}\n', encoding="utf-8")
            facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
            from tests.support import write_proof

            write_proof(root, prepared["task_id"], prepared["manifest_sha256"], ["src/counter.py"])
            self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=prepared["task_id"])["validation"]["status"], "succeeded")
            execution = facade.prepare_execution_tool(WORKSPACE_ID, prepared["task_id"])
            switch = Path(os.environ["GROKBOT_MCP_REGISTRY"]).parent / "execution_switch.json"
            switch.write_text('{"enabled": true}\n', encoding="utf-8")
            facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
            self.assertTrue(facade.claim_for(WORKSPACE_ID, execution["execution_id"])["ok"])
            (root / "src" / "counter.py").write_text("def count_above(values, threshold):\n    return 1\n", encoding="utf-8")
            from tests.support import execution_result
            import json

            directory = root / ".bridge" / "results_execute"
            directory.mkdir(parents=True, exist_ok=True)
            payload = execution_result(execution["execution_id"], prepared["task_id"], prepared["manifest_sha256"], ["src/counter.py"])
            (directory / f"{execution['execution_id']}.json").write_text(json.dumps(payload), encoding="utf-8")
            status = facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])
            self.assertEqual(status["execution"]["status"], "succeeded")
            again = facade.claim_for(WORKSPACE_ID, execution["execution_id"])
            self.assertEqual(again["error"], "already_terminal")

    def test_example_counter(self) -> None:
        import subprocess
        import sys

        example = Path(__file__).resolve().parents[1] / "examples" / "minimal_workspace"
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=example, capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
