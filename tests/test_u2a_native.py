"""Director's native Windows fixture gate; requires working tempfile permissions."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from grokbot_mcp import facade, setup_wizard
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.registry import load_registry, policy_fingerprint
from grokbot_mcp.result_contract import output_evidence
from grokbot_mcp.test_command_cli import preview
from tests.test_git_gate_advisory import GitGateAdvisoryTest as _AdvisoryFixture
from tests.support import WORKSPACE_ID, execution_result, git


class NativeU2aTest(unittest.TestCase):
    # Reuse only fixture helpers, not the existing test methods/assertions.
    prepare = _AdvisoryFixture.prepare
    pending_claim = _AdvisoryFixture.pending_claim
    start = _AdvisoryFixture.start
    write_result = _AdvisoryFixture.write_result
    _fixture_setup = _AdvisoryFixture.setUp

    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(install_backend, None, None)
        self._fixture_setup()

    def configure(self, *, required=False, template=False):
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        item = data["workspaces"][0]
        item["require_test_output"] = required
        item["other_prefixes"] = ["./Other/"]
        if template:
            item["test_commands"][1].update(applies_to="code", argv=["python", "-m", "fictional_lint", "--", "{changed_files}"])
        self.registry.write_text(json.dumps(data), encoding="utf-8")
        fingerprint = policy_fingerprint(load_registry(self.registry)[WORKSPACE_ID])
        for phase in ("validate", "execute"):
            path = self.root / ".bridge" / (phase + "_routine_status.json")
            value = json.loads(path.read_text(encoding="utf-8"))
            value["binding"]["policy_fingerprint"] = fingerprint
            path.write_text(json.dumps(value), encoding="utf-8")

    def status(self, execution):
        return facade.get_status(WORKSPACE_ID, execution_id=str(execution["execution_id"]))["execution"]

    def test_actual_git_review_detects_reedited_preexisting_dirty_file(self):
        self.configure()
        (self.root / "Other").mkdir()
        (self.root / "Other/shared.md").write_text("before\n", encoding="utf-8")
        task, execution, _claim = self.start()
        (self.root / "Other/shared.md").write_text("after\n", encoding="utf-8")
        (self.root / "src/counter.py").write_text("VALUE = 3\n", encoding="utf-8")
        self.write_result(task, execution, ["src/counter.py"])
        result = self.status(execution)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["git_review"]["declared_others"], ["Other/shared.md"])
        self.assertEqual(result["out_of_scope_changes"], ["Other/shared.md"])
        self.assertEqual(self.status(execution)["git_review"], result["git_review"])

    def test_actual_head_change_is_reported_and_refused(self):
        self.configure()
        task, execution, _claim = self.start()
        (self.root / "src/counter.py").write_text("VALUE = 4\n", encoding="utf-8")
        git(self.root, "add", "src/counter.py")
        git(self.root, "commit", "-m", "fictional concurrent commit")
        self.write_result(task, execution, ["src/counter.py"])
        result = self.status(execution)
        self.assertEqual(result["error"], "git_head_mismatch")
        self.assertTrue(result["git_review"]["head_changed"])
        self.assertEqual(result["git_review"]["in_scope"], ["src/counter.py"])

    def test_required_missing_output_is_frozen(self):
        self.configure(required=True)
        task, execution, _claim = self.start()
        (self.root / "src/counter.py").write_text("VALUE = 5\n", encoding="utf-8")
        self.write_result(task, execution, ["src/counter.py"])
        result = self.status(execution)
        self.assertEqual(result["error"], "test_evidence_missing")
        self.assertEqual(self.status(execution)["result_diagnosis"], result["result_diagnosis"])

    def test_actual_preview_output_and_generated_packet(self):
        self.configure(required=True, template=True)
        task, execution, _claim = self.start()
        (self.root / "src/counter.py").write_text("VALUE = 6\n", encoding="utf-8")
        result = preview(self.registry, WORKSPACE_ID, str(execution["execution_id"]))
        self.assertEqual(result["argv"], ["python", "-m", "fictional_lint", "--", "src/counter.py"])
        self.assertFalse(result["executed"])
        payload = execution_result(str(execution["execution_id"]), str(task["task_id"]), str(task["manifest_sha256"]), ["src/counter.py"])
        payload["tests"].update(output_evidence(b"fictional raw output"))
        path = self.root / ".bridge/results_execute" / (str(execution["execution_id"]) + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        status = self.status(execution)
        self.assertEqual(status["status"], "succeeded")
        self.assertIsNone(status["godot_exit_code"])
        self.assertNotIn("fictional raw output", json.dumps(status))
        generated = setup_wizard.generate(self.registry, WORKSPACE_ID)
        text = (generated / "routine-setup.md").read_text(encoding="utf-8")
        self.assertIn("tests.output_sha256", text)
        self.assertIn("test_command_cli", text)


del _AdvisoryFixture  # Avoid collecting the imported TestCase a second time.
