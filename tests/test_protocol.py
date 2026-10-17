"""Offline protocol tests. These tests never open a socket."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

from grokbot_mcp import facade
from grokbot_mcp.credentials.blob import pack_blob, unpack_blob
from grokbot_mcp.errors.http_errors import classify_http_failure
from grokbot_mcp.security.paths import PathRejected, is_reparse_point, normalize_repo_path
from grokbot_mcp.security.redact import redact
from tests.support import EXECUTE, VALIDATE, WORKSPACE_ID, make_workspace, write_proof


class ProtocolTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_workspace(Path(self._tmp.name))

    def tearDown(self) -> None:
        facade.install_transport(None)
        self._tmp.cleanup()

    def test_missing_registry_fails_closed(self) -> None:
        os.environ["GROKBOT_MCP_REGISTRY"] = str(Path(self._tmp.name) / "absent.json")
        listed = facade.list_workspaces()
        self.assertEqual(listed["error"], "registry_missing")
        self.assertEqual(listed["workspaces"], [])
        self.assertFalse(listed["webhook_sent"])

    def test_unknown_workspace_and_absolute_root_argument_are_rejected(self) -> None:
        self.assertEqual(facade.preflight("not-registered")["error"], "unknown_workspace")
        self.assertEqual(facade.preflight(str(self.root))["error"], "unknown_workspace")

    def test_allow_list_cannot_be_expanded(self) -> None:
        denied = facade.prepare_task(
            WORKSPACE_ID,
            "code_change",
            "Add a comment to the counter.",
            ["src/counter_copy.py"],
            ["The public test still passes."],
            "python_unittest",
        )
        self.assertEqual(denied["error"], "allowed_file_outside_policy")
        self.assertFalse(denied["webhook_sent"])

    def test_prepare_does_not_send_webhook(self) -> None:
        result = self._prepare()
        self.assertEqual(result["validation_status"], "valid")
        self.assertFalse(result["webhook_sent"])
        self.assertTrue(result["manifest_sha256"])

    def test_validation_mock_then_status(self) -> None:
        prepared = self._prepare()
        self._enable_switch()
        submitted = facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
        self.assertEqual(submitted["http_status"], 200)
        self.assertTrue(submitted["webhook_sent"])
        self.assertEqual(submitted["task_state"], "accepted")
        self.assertNotEqual(submitted["task_state"], "succeeded")
        write_proof(self.root, prepared["task_id"], prepared["manifest_sha256"], ["src/counter.py"])
        status = facade.get_status(WORKSPACE_ID, task_id=prepared["task_id"])
        self.assertTrue(status["validation"]["validation_passed"])
        self.assertFalse(status["validation"]["code_modified"])
        self.assertEqual(status["validation"]["status"], "succeeded")
        self.assertFalse(status["qa_accepted"])

    def test_validation_switch_blocks_without_consuming_submission(self) -> None:
        prepared = self._prepare()
        switch = Path(self._tmp.name) / "execution_switch.json"
        for content in (None, '{"enabled": false}', '{"enabled": "true"}', '{"enabled": 1}', 'broken'):
            with self.subTest(content=content):
                if content is None:
                    switch.unlink(missing_ok=True)
                else:
                    switch.write_text(content, encoding="utf-8")
                with patch.object(facade, "status_for") as status, patch.object(facade, "load_for") as load, patch.object(facade, "_send") as send:
                    result = facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
                    status.assert_not_called()
                    load.assert_not_called()
                    send.assert_not_called()
                self.assertEqual(result["error"], "validate_manifest_not_enabled")
                self.assertFalse(result["webhook_sent"])
                self.assertFalse(result["webhook_attempted"])
                self.assertFalse((self.root / ".bridge" / "tasks_v2").exists())
        from tests.support import MockTransport

        transport = MockTransport()
        facade.install_transport(transport)
        self._enable_switch()
        self.assertEqual(facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])["http_status"], 200)
        facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
        self.assertEqual(len(transport.calls), 1)

    def test_validation_service_defaults_closed_even_with_custom_transport(self) -> None:
        from grokbot_mcp.registry import load_registry
        from grokbot_mcp.validation.service import submit_validation

        prepared = self._prepare()
        workspace = load_registry()[WORKSPACE_ID]
        status, load, send = Mock(), Mock(), Mock()
        result = submit_validation(workspace, prepared["task_id"], status_fn=status, load_fn=load, transport=send)
        self.assertEqual(result["error"], "validate_manifest_not_enabled")
        status.assert_not_called()
        load.assert_not_called()
        send.assert_not_called()

    def test_validation_paused_routine_still_blocks_when_switch_enabled(self) -> None:
        prepared = self._prepare()
        self._enable_switch()
        path = self.root / ".bridge" / "validate_routine_status.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["routine_status"] = "paused"
        path.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(facade, "_send") as send:
            result = facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
            send.assert_not_called()
        self.assertEqual(result["error"], "routine_paused")

    def test_execute_switch_blocks_before_retry_is_consumed(self) -> None:
        prepared = self._validated()
        execution = facade.prepare_execution_tool(WORKSPACE_ID, prepared["task_id"])
        self.assertEqual(execution["approval_state"], "granted")
        self.assertFalse(execution["webhook_sent"])
        blocked = facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
        self.assertEqual(blocked["error"], "execute_manifest_not_enabled")
        self.assertFalse(blocked["webhook_attempted"])
        self._enable_switch()
        sent = facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
        self.assertEqual(sent["http_status"], 200)
        self.assertEqual(sent["submission_state"], "accepted")
        again = facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
        self.assertEqual(again["error"], "retry_forbidden")

    def test_claim_once_and_scope(self) -> None:
        prepared = self._validated()
        execution = facade.prepare_execution_tool(WORKSPACE_ID, prepared["task_id"])
        self._enable_switch()
        facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
        first = facade.claim_for(WORKSPACE_ID, execution["execution_id"])
        self.assertTrue(first["ok"])
        self.assertEqual(first["allowed_files"], ["src/counter.py"])
        self.assertNotIn("src/counter_copy.py", first["allowed_files"])
        second = facade.claim_for(WORKSPACE_ID, execution["execution_id"])
        self.assertEqual(second["error"], "duplicate_claim")
        (self.root / "src" / "counter.py").write_text(
            "def count_above(values, threshold):\n    return sum(1 for item in values if item > threshold)\n\ndef label():\n    return 'example'\n",
            encoding="utf-8",
        )
        self._write_execution_result(execution["execution_id"], prepared, ["src/counter.py"])
        status = facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])
        self.assertEqual(status["execution"]["status"], "succeeded")
        self.assertFalse(status["execution"]["qa_accepted"])
        self.assertEqual(status["execution"]["changed_files"], ["src/counter.py"])

    def test_similar_filename_is_scope_violation(self) -> None:
        prepared = self._validated()
        execution = facade.prepare_execution_tool(WORKSPACE_ID, prepared["task_id"])
        self._enable_switch()
        facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
        facade.claim_for(WORKSPACE_ID, execution["execution_id"])
        (self.root / "src" / "counter_copy.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "src" / "counter.py").write_text("def count_above(values, threshold):\n    return 0\n", encoding="utf-8")
        self._write_execution_result(execution["execution_id"], prepared, ["src/counter.py"])
        status = facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])
        self.assertEqual(status["execution"]["error"], "scope_violation")

    def test_parallel_claim_allows_only_one(self) -> None:
        prepared = self._validated()
        execution = facade.prepare_execution_tool(WORKSPACE_ID, prepared["task_id"])
        self._enable_switch()
        facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])
        results: list[dict[str, object]] = []

        def once() -> None:
            results.append(facade.claim_for(WORKSPACE_ID, execution["execution_id"]))

        threads = [threading.Thread(target=once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        oks = [item for item in results if item.get("ok") is True]
        self.assertEqual(len(oks), 1)
        self.assertIn("duplicate_claim", {item.get("error") for item in results})

    def test_no_credential_fails_closed(self) -> None:
        facade.install_transport(lambda *_args: (_ for _ in ()).throw(AssertionError("network")))
        from grokbot_mcp.credentials.backend import install_backend

        install_backend(lambda _target, _others: "missing", lambda _target, _others: (_ for _ in ()).throw(CredentialError("missing")))
        prepared = self._prepare()
        self._enable_switch()
        result = facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
        self.assertEqual(result["error"], "credentials_missing")
        self.assertFalse(result["webhook_attempted"])

    def test_http_400_is_not_quota(self) -> None:
        diagnosis = classify_http_failure(400, b'{"message":"malformed payload"}')
        self.assertEqual(diagnosis["failure_category"], "invalid_payload")
        diagnosis = classify_http_failure(400, b"{}")
        self.assertEqual(diagnosis["failure_category"], "http_400_unknown")
        diagnosis = classify_http_failure(400, b'{"message":"weekly quota exhausted"}')
        self.assertEqual(diagnosis["failure_category"], "quota_exhausted_confirmed")
        self.assertTrue(diagnosis["weekly_quota_exhausted"])
        self.assertFalse(diagnosis["retry_allowed"])

    def test_redact_and_blob_roundtrip_without_logging_secret(self) -> None:
        url = "https://example.invalid/hook"
        key = "example-key"
        packed = pack_blob(url, key)
        self.assertNotIn(key.encode("utf-8"), packed[:4])
        self.assertEqual(unpack_blob(packed), (url, key))
        leaked = redact("Authorization: Bearer " + key + " https://example.invalid/hook")
        self.assertNotIn(key, leaked)
        self.assertNotIn("example.invalid/hook", leaked)

    def test_normalize_rejects_absolute_and_traversal(self) -> None:
        with self.assertRaises(PathRejected):
            normalize_repo_path("C:/ExampleWorkspace/src/counter.py")
        with self.assertRaises(PathRejected):
            normalize_repo_path("../src/counter.py")
        self.assertEqual(normalize_repo_path("src/counter.py"), "src/counter.py")

    def test_reparse_point_detects_symlink(self) -> None:
        link = Path(self._tmp.name) / "link"
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation is not available")
        self.assertTrue(is_reparse_point(link))

    def _prepare(self) -> dict[str, object]:
        return facade.prepare_task(
            WORKSPACE_ID,
            "code_change",
            "Add a label helper to the counter module.",
            ["src/counter.py"],
            ["The helper returns the example label."],
            "python_unittest",
            baseline_commit=json.loads(Path(os.environ["GROKBOT_MCP_REGISTRY"]).read_text(encoding="utf-8"))["workspaces"][0]["expected_head"],
        )

    def _validated(self) -> dict[str, object]:
        prepared = self._prepare()
        self._enable_switch()
        submitted = facade.submit_validation_tool(WORKSPACE_ID, prepared["task_id"])
        self.assertEqual(submitted["http_status"], 200)
        write_proof(self.root, prepared["task_id"], prepared["manifest_sha256"], ["src/counter.py"])
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=prepared["task_id"])["validation"]["status"], "succeeded")
        (Path(self._tmp.name) / "execution_switch.json").write_text('{"enabled": false}\n', encoding="utf-8")
        return prepared

    def _enable_switch(self) -> None:
        path = Path(os.environ["GROKBOT_MCP_REGISTRY"]).parent / "execution_switch.json"
        path.write_text('{"enabled": true}\n', encoding="utf-8")

    def _write_execution_result(self, execution_id: str, prepared: dict[str, object], changed: list[str]) -> None:
        from tests.support import execution_result

        directory = self.root / ".bridge" / "results_execute"
        directory.mkdir(parents=True, exist_ok=True)
        payload = execution_result(execution_id, str(prepared["task_id"]), str(prepared["manifest_sha256"]), changed)
        (directory / f"{execution_id}.json").write_text(json.dumps(payload), encoding="utf-8")


class CredentialError(Exception):
    pass


if __name__ == "__main__":
    unittest.main()
