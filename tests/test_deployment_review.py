"""Four real dev2 behavior regressions. Imports only APIs that already existed in dev2."""
from __future__ import annotations
import json
import unittest
from grokbot_mcp import __version__, facade, setup_wizard
from tests import test_reliability as reliability
from tests.support import WORKSPACE_ID


class DeploymentReviewTest(unittest.TestCase):
    def setUp(self):
        self.case = reliability.ReliabilityTest()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def test_server_announces_actual_version(self):
        from grokbot_mcp.server import mcp
        # MCPServer.version is the existing SDK initialization version, not a new bridge API.
        self.assertEqual(mcp.version, __version__)
        self.assertTrue(mcp.version)

    def test_default_preflight_does_not_expose_private_absolute_root(self):
        result = facade.preflight(WORKSPACE_ID)
        values = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(json.dumps(str(self.case.root.resolve()), ensure_ascii=False), values)
        self.assertFalse(result["webhook_sent"])

    def test_boolean_count_is_format_error_not_an_actual_test_failure_diagnosis(self):
        task, execution = self.case.start()
        self.case.change()
        payload = self.case.emit(task, execution)
        payload["tests"]["passed"] = True
        self.case.put(self.case.path("results_execute", execution["execution_id"]), payload)
        result = self.case.status(execution)
        self.assertEqual(result["error"], "test_failed")  # Preserve the legacy state/error vocabulary.
        self.assertEqual(result.get("result_diagnosis", {}).get("category"), "format_error")
        self.assertFalse(result["qa_accepted"])

    def test_generated_routine_contains_local_result_precheck(self):
        folder = setup_wizard.generate(self.case.registry, WORKSPACE_ID, "reuse")
        text = (folder / "routine-setup.md").read_text(encoding="utf-8")
        self.assertTrue("grokbot_mcp.result_cli" in text, "Generated Routine has no local result precheck")
        self.assertEqual(len(self.case.transport.calls), 0)
