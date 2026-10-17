"""Negative security contracts that do not replay any previous Git commit.

Each case builds a new fictional workspace. These tests replace historical
red gates that required archiving older source trees.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import facade, safety
from grokbot_mcp.canary import create as create_canary
from grokbot_mcp.canary import plan as plan_canary
from grokbot_mcp.credentials.blob import BlobError, validate_url
from grokbot_mcp.local_safety import LocalError, parse_json
from grokbot_mcp.startup_probe import TOOL_NAMES
from grokbot_mcp.transport.webhook import HttpOutcome
from tests.support import (
    WORKSPACE_ID,
    execution_result,
    make_workspace,
    write_proof,
)


SEVEN_TOOLS = (
    "grok_get_status",
    "grok_list_workspaces",
    "grok_preflight",
    "grok_prepare_execution",
    "grok_prepare_task",
    "grok_submit_execution",
    "grok_submit_validation",
)


class IndependentSecurityContractTest(unittest.TestCase):
    def tearDown(self) -> None:
        facade.install_transport(None)

    def test_tool_surface_stays_the_seven_existing_names(self) -> None:
        self.assertEqual(tuple(TOOL_NAMES), SEVEN_TOOLS)

    def test_ambiguous_json_is_rejected(self) -> None:
        cases = (
            (b'{"status":', "json_incomplete_or_invalid"),
            (b'{"x":1,"x":2}', "json_duplicate_field"),
            (b'{"x":NaN}', "json_nonfinite"),
            (b"[]", "json_object_required"),
            (b"\xef\xbb\xbf{}", "json_encoding_invalid"),
        )
        for raw, code in cases:
            with self.subTest(code=code), self.assertRaises(LocalError) as error:
                parse_json(raw)
            self.assertEqual(error.exception.code, code)

    def test_numeric_url_alias_is_rejected(self) -> None:
        with self.assertRaises(BlobError):
            validate_url("https://2130706433/fixture")

    def test_writes_disabled_and_path_outside_policy(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            make_workspace(Path(name), writes=False)
            denied = facade.prepare_task(
                WORKSPACE_ID, "code_change", "Add a label helper.",
                ["src/counter.py"], ["The helper exists."], "python_unittest",
            )
            self.assertEqual(denied["error"], "workspace_writes_disabled")
            self.assertFalse(denied["webhook_sent"])
        with tempfile.TemporaryDirectory() as name:
            make_workspace(Path(name), writes=True)
            denied = facade.prepare_task(
                WORKSPACE_ID, "code_change", "Add a label helper.",
                ["src/counters.py"], ["The helper exists."], "python_unittest",
            )
            self.assertEqual(denied["error"], "allowed_file_outside_policy")
            denied = facade.prepare_task(
                WORKSPACE_ID, "code_change", "Add a label helper.",
                ["../src/counter.py"], ["The helper exists."], "python_unittest",
            )
            self.assertIsNotNone(denied["error"])
            self.assertNotEqual(denied["error"], "ok")
            self.assertFalse(denied.get("webhook_sent"))

    def test_registered_head_mismatch_blocks_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            make_workspace(Path(name))
            registry = Path(os.environ["GROKBOT_MCP_REGISTRY"])
            data = json.loads(registry.read_text(encoding="utf-8"))
            data["workspaces"][0]["expected_head"] = "0" * 40
            registry.write_text(json.dumps(data), encoding="utf-8")
            denied = facade.prepare_task(
                WORKSPACE_ID, "code_change", "Add a label helper.",
                ["src/counter.py"], ["The helper exists."], "python_unittest",
            )
            self.assertEqual(denied["error"], "git_head_mismatch")
            self.assertFalse(denied["webhook_sent"])

    def test_unknown_submission_cannot_be_resent_or_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = make_workspace(Path(name))
            switch = Path(os.environ["GROKBOT_MCP_REGISTRY"]).parent / "execution_switch.json"
            switch.write_text('{"enabled": true}\n', encoding="utf-8")
            prepared = facade.prepare_task(
                WORKSPACE_ID, "code_change", "Add a label helper.",
                ["src/counter.py"], ["The helper exists."], "python_unittest",
            )
            self.assertIsNone(prepared.get("error"))
            facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
            write_proof(root, prepared["task_id"], prepared["manifest_sha256"], ["src/counter.py"])
            self.assertEqual(
                facade.get_status(WORKSPACE_ID, task_id=prepared["task_id"])["validation"]["status"],
                "succeeded",
            )
            execution = facade.prepare_execution_tool(WORKSPACE_ID, prepared["task_id"])
            calls: list[int] = []

            def uncertain(*_args):
                calls.append(1)
                return HttpOutcome(kind="timeout", status=None)

            facade.install_transport(uncertain)
            result = facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
            self.assertEqual(result["submission_state"], "unknown_submission")
            for _ in range(3):
                facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])
                again = facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
                self.assertEqual(again["error"], "retry_forbidden")
            self.assertEqual(calls, [1])
            self.assertFalse(facade.claim_for(WORKSPACE_ID, execution["execution_id"])["ok"])

    def test_second_claim_and_replaced_result_stay_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = make_workspace(Path(name))
            switch = Path(os.environ["GROKBOT_MCP_REGISTRY"]).parent / "execution_switch.json"
            switch.write_text('{"enabled": true}\n', encoding="utf-8")
            prepared = facade.prepare_task(
                WORKSPACE_ID, "code_change", "Add a label helper.",
                ["src/counter.py"], ["The helper exists."], "python_unittest",
            )
            facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
            write_proof(root, prepared["task_id"], prepared["manifest_sha256"], ["src/counter.py"])
            self.assertEqual(
                facade.get_status(WORKSPACE_ID, task_id=prepared["task_id"])["validation"]["status"],
                "succeeded",
            )
            execution = facade.prepare_execution_tool(WORKSPACE_ID, prepared["task_id"])
            self.assertIsNone(execution.get("error"))
            facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
            self.assertTrue(facade.claim_for(WORKSPACE_ID, execution["execution_id"])["ok"])
            (root / "src" / "counter.py").write_text(
                "def count_above(values, threshold):\n    return 1\n", encoding="utf-8",
            )
            directory = root / ".bridge" / "results_execute"
            directory.mkdir(parents=True, exist_ok=True)
            payload = execution_result(
                execution["execution_id"], prepared["task_id"], prepared["manifest_sha256"], ["src/counter.py"],
            )
            result_path = directory / f"{execution['execution_id']}.json"
            result_path.write_text(json.dumps(payload), encoding="utf-8")
            status = facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])
            self.assertEqual(status["execution"]["status"], "succeeded")
            again = facade.claim_for(WORKSPACE_ID, execution["execution_id"])
            self.assertEqual(again["error"], "already_terminal")
            payload["status"] = "failed"
            payload["tests"]["failed"] = 9
            result_path.write_text(json.dumps(payload), encoding="utf-8")
            frozen = facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])["execution"]
            self.assertEqual(frozen["historical_status"], "succeeded")
            self.assertEqual(frozen["integrity_error"], "result_changed")
            self.assertTrue(frozen["reported_success"])

    def test_wrong_machine_on_validation_proof_is_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = make_workspace(Path(name))
            switch = Path(os.environ["GROKBOT_MCP_REGISTRY"]).parent / "execution_switch.json"
            switch.write_text('{"enabled": true}\n', encoding="utf-8")
            prepared = facade.prepare_task(
                WORKSPACE_ID, "code_change", "Add a label helper.",
                ["src/counter.py"], ["The helper exists."], "python_unittest",
            )
            facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
            write_proof(root, prepared["task_id"], prepared["manifest_sha256"], ["src/counter.py"])
            proof_path = root / ".bridge" / "results_v2" / f"{prepared['task_id']}.json"
            document = json.loads(proof_path.read_text(encoding="utf-8"))
            document["machine"] = "OTHER-FICTIONAL-PC"
            proof_path.write_text(json.dumps(document), encoding="utf-8")
            status = facade.get_status(WORKSPACE_ID, task_id=prepared["task_id"])["validation"]
            self.assertEqual(status["error"], "identity_mismatch")
            self.assertNotEqual(status["status"], "succeeded")

    def test_credential_presence_does_not_validate_the_secret(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            make_workspace(Path(name))
            registry = Path(os.environ["GROKBOT_MCP_REGISTRY"])
            switch = registry.parent / "execution_switch.json"
            switch.write_text('{"enabled": false}\n', encoding="utf-8")
            with patch("grokbot_mcp.credentials.backend.status_for", return_value="present") as status, \
                 patch("grokbot_mcp.credentials.backend.load_for", side_effect=AssertionError("no key load")):
                result = safety.credential_plan(registry, WORKSPACE_ID, check_presence=True)
            self.assertGreaterEqual(status.call_count, 1)
            self.assertEqual(result["phases"]["execute"]["credential_validity"], "NOT_VERIFIED")
            self.assertNotIn("validate-example", json.dumps(result))

    def test_canary_refuses_when_declared_machine_changes(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            folder = Path(name) / "FICTIONAL-canary"
            with patch.dict(os.environ, {"COMPUTERNAME": "FICTIONAL-HOST-A"}):
                reviewed = plan_canary(folder)
            with patch.dict(os.environ, {"COMPUTERNAME": "FICTIONAL-HOST-B"}):
                with self.assertRaisesRegex(LocalError, "plan_confirmation_mismatch"):
                    create_canary(folder, confirm=reviewed["plan_sha256"])
            self.assertFalse(folder.exists())
