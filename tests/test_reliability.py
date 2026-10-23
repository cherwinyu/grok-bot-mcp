"""A-D regressions reconstructed with fictional, temporary data.

No live credentials, remote routines, personal paths, or unrelated project source are used.
These cases assert the current tree. They do not replay an older commit.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import facade
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.registry import load_registry
from grokbot_mcp.transport.webhook import HttpOutcome
from tests.support import WORKSPACE_ID, MockTransport, execution_result, make_workspace, proof, write_proof


class ReliabilityTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_workspace(Path(self.temp.name))
        self.addCleanup(facade.install_transport, None)
        self.addCleanup(install_backend, None, None)
        self.transport = MockTransport()
        facade.install_transport(self.transport)
        self.registry = Path(self.temp.name) / "registry.json"
        (Path(self.temp.name) / "execution_switch.json").write_text('{"enabled": true}', encoding="utf-8")

    def prepare(self, allowed=None):
        return facade.prepare_task(WORKSPACE_ID, "code_change", "Add a fictional label.",
                                   allowed or ["src/counter.py"], ["Keep previous labels."], "python_unittest")

    def validated(self, allowed=None):
        task = self.prepare(allowed)
        self.assertEqual(facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])["http_status"], 200)
        write_proof(self.root, task["task_id"], task["manifest_sha256"], allowed or ["src/counter.py"])
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"], "succeeded")
        return task

    def start(self, *, claim=True, allowed=None):
        task = self.validated(allowed)
        execution = facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])
        self.assertIsNone(execution["error"])
        self.assertEqual(facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])["http_status"], 200)
        if claim:
            self.assertTrue(facade.claim_for(WORKSPACE_ID, execution["execution_id"])["ok"])
        return task, execution

    def path(self, kind, identity):
        return self.root / ".bridge" / kind / (identity + ".json")

    def put(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def mutate(self, path, **fields):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(fields)
        self.put(path, data)

    def emit(self, task, execution, status="succeeded", changed=None):
        payload = execution_result(execution["execution_id"], task["task_id"], task["manifest_sha256"],
                                   ["src/counter.py"] if changed is None else changed)
        payload["status"] = status
        self.put(self.path("results_execute", execution["execution_id"]), payload)
        return payload

    def change(self, name="src/counter.py", label="first"):
        with (self.root / name).open("a", encoding="utf-8") as handle:
            handle.write("\n# fictional " + label + "\n")

    def status(self, execution, **kwargs):
        return facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"], **kwargs)["execution"]

    def expire(self, kind, identity):
        self.mutate(self.path(kind, identity), request_attempted_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())

    def test_execution_running_then_success(self):
        task, ex = self.start()
        self.emit(task, ex, "running", [])
        self.assertEqual(self.status(ex)["status"], "running")
        self.change()
        self.emit(task, ex)
        self.assertEqual(self.status(ex)["status"], "succeeded")

    def test_validation_running_is_not_failure(self):
        task = self.prepare()
        facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])
        payload = proof(task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        payload.update(status="running", validation_passed=None)
        self.put(self.path("results_v2", task["task_id"]), payload)
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"], "running")

    def test_execution_partial_json_does_not_poison_ledger(self):
        task, ex = self.start()
        target = self.path("results_execute", ex["execution_id"])
        target.parent.mkdir(parents=True)
        target.write_text('{"status":', encoding="utf-8")
        status = self.status(ex)
        self.assertIn(status["status"], ("pending", "running"))
        self.assertNotEqual(json.loads(self.path("executions", ex["execution_id"]).read_text(encoding="utf-8"))["execution_state"], "failed")
        self.change()
        self.emit(task, ex)
        self.assertEqual(self.status(ex)["status"], "succeeded")

    def test_validation_partial_json_does_not_poison_ledger(self):
        task = self.prepare()
        facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])
        target = self.path("results_v2", task["task_id"])
        target.parent.mkdir(parents=True)
        target.write_text('{"status":', encoding="utf-8")
        self.assertIn(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"], ("pending", "running"))
        write_proof(self.root, task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"], "succeeded")

    def test_partial_json_has_bounded_wait(self):
        task, ex = self.start()
        target = self.path("results_execute", ex["execution_id"])
        target.parent.mkdir(parents=True)
        target.write_text('{', encoding="utf-8")
        started = time.monotonic()
        result = self.status(ex)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(result.get("result_observation"), "writing")

    def test_running_identity_mismatch_is_still_rejected(self):
        task, ex = self.start()
        payload = self.emit(task, ex, "running", [])
        payload["machine"] = "OTHER-FICTIONAL-PC"
        self.put(self.path("results_execute", ex["execution_id"]), payload)
        self.assertEqual(self.status(ex)["error"], "identity_mismatch")

    def test_terminal_cannot_regress_to_running(self):
        task, ex = self.start()
        self.change()
        self.emit(task, ex)
        self.assertEqual(self.status(ex)["status"], "succeeded")
        self.emit(task, ex, "running", [])
        result = self.status(ex)
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(result.get("current_integrity") is True)
        self.assertEqual(result.get("integrity_error"), "result_changed")

    def test_timeout_blocks_new_execution_and_accepts_late_result(self):
        task, ex = self.start()
        self.expire("executions", ex["execution_id"])
        self.assertEqual(self.status(ex)["status"], "timeout")
        task2 = self.validated(["tests/test_counter.py"])
        self.assertEqual(facade.prepare_execution_tool(WORKSPACE_ID, task2["task_id"])["error"], "concurrent_execution")
        self.change()
        self.emit(task, ex)
        result = self.status(ex)
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["late_result"])
        self.assertEqual(len(self.transport.calls), 3)

    def test_running_file_does_not_hide_deadline(self):
        task, ex = self.start()
        self.emit(task, ex, "running", [])
        self.expire("executions", ex["execution_id"])
        self.assertEqual(self.status(ex)["status"], "timeout")
        self.assertEqual(self.status(ex)["status"], "timeout")

    def test_validation_timeout_and_late_result(self):
        task = self.prepare()
        facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])
        self.expire("tasks_v2", task["task_id"])
        result = facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]
        self.assertEqual(result["status"], "timeout")
        write_proof(self.root, task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        result = facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["late_result"])

    def test_observe_only_never_persists_acceptance(self):
        task, ex = self.start()
        self.change()
        self.emit(task, ex)
        ledger = self.path("executions", ex["execution_id"])
        before = ledger.read_bytes()
        result = self.status(ex, observe_only=True)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(ledger.read_bytes(), before)
        self.assertFalse(result["ledger_updated"])
        self.assertEqual(result["query_mode"], "observe_only")
        self.assertEqual(self.status(ex)["status"], "succeeded")

    def test_query_reports_history_and_never_sends(self):
        task, ex = self.start()
        count = len(self.transport.calls)
        result = self.status(ex)
        self.assertFalse(result.get("sent_this_call", True))
        self.assertFalse(result.get("attempted_this_call", True))
        self.assertEqual(result.get("historical_submission_state"), "accepted")
        self.assertTrue(result.get("last_observed_at"))
        self.assertEqual(len(self.transport.calls), count)

    def test_claim_rechecks_frozen_proof_bytes(self):
        task, ex = self.start(claim=False)
        path = self.path("results_v2", task["task_id"])
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        result = facade.claim_for(WORKSPACE_ID, ex["execution_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "validation_hash_mismatch")

    def test_claim_rechecks_baseline(self):
        task, ex = self.start(claim=False)
        self.change()
        result = facade.claim_for(WORKSPACE_ID, ex["execution_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "baseline_stale")

    def test_deleted_claim_rejects_terminal(self):
        task, ex = self.start()
        self.path("execution_claims", ex["execution_id"]).unlink()
        self.change()
        self.emit(task, ex)
        result = self.status(ex)
        self.assertNotEqual(result["status"], "succeeded")
        self.assertEqual(result["error"], "claim_missing")

    def test_modified_claim_rejects_terminal(self):
        task, ex = self.start()
        self.mutate(self.path("execution_claims", ex["execution_id"]), allowed_files=["tests/test_counter.py"])
        self.change()
        self.emit(task, ex)
        self.assertEqual(self.status(ex)["error"], "claim_hash_mismatch")

    def test_false_validation_proof_rejects_terminal(self):
        task, ex = self.start()
        self.mutate(self.path("results_v2", task["task_id"]), validation_passed=False)
        self.change()
        self.emit(task, ex)
        self.assertNotEqual(self.status(ex)["status"], "succeeded")

    def test_reformatted_proof_rejects_terminal(self):
        task, ex = self.start()
        target = self.path("results_v2", task["task_id"])
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.change()
        self.emit(task, ex)
        self.assertEqual(self.status(ex)["error"], "validation_hash_mismatch")

    def test_missing_proof_is_not_a_crash(self):
        task, ex = self.start()
        self.path("results_v2", task["task_id"]).unlink()
        self.change()
        self.emit(task, ex)
        self.assertNotEqual(self.status(ex)["status"], "succeeded")

    def test_manifest_and_baseline_tampering_rejected(self):
        for kind in ("task_specs", "execution_baselines"):
            with self.subTest(kind=kind):
                # Each subcase uses an isolated task/workspace, not a reused authorization.
                case = ReliabilityTest("runTest")
                case.setUp()
                try:
                    task, ex = case.start()
                    identity = task["task_id"] if kind == "task_specs" else ex["execution_id"]
                    case.mutate(case.path(kind, identity), forged=True)
                    case.change()
                    case.emit(task, ex)
                    self.assertNotEqual(case.status(ex)["status"], "succeeded")
                finally:
                    case.doCleanups()

    def test_cached_success_has_verification_time(self):
        task, ex = self.start()
        self.change()
        self.emit(task, ex)
        first = self.status(ex)
        second = self.status(ex)
        self.assertTrue(first.get("verified_at"))
        self.assertEqual(first["verified_at"], second["verified_at"])
        self.assertEqual(second["verification_scope"], "historical")
        self.assertIsNone(second["current_integrity"])
        self.assertFalse(second["ledger_updated"])

    def test_current_revalidation_preserves_historical_success(self):
        task, ex = self.start()
        self.change()
        self.emit(task, ex)
        first = self.status(ex)
        self.change("src/do_not_touch.py")
        result = self.status(ex, verify_current=True)
        self.assertEqual(result["historical_status"], "succeeded")
        self.assertEqual(result["verified_at"], first["verified_at"])
        self.assertFalse(result["current_integrity"])
        self.assertTrue(result["integrity_error"])
        self.assertFalse(result["qa_accepted"])

    def test_current_revalidation_checks_claim_again(self):
        task, ex = self.start()
        self.change()
        self.emit(task, ex)
        self.status(ex)
        self.path("execution_claims", ex["execution_id"]).unlink()
        result = self.status(ex, verify_current=True)
        self.assertFalse(result["current_integrity"])
        self.assertEqual(result["integrity_error"], "claim_missing")

    def test_current_revalidation_detects_allowed_file_rewrite(self):
        task, ex = self.start()
        self.change()
        self.emit(task, ex)
        self.status(ex)
        self.change(label="later-edit")
        result = self.status(ex, verify_current=True)
        self.assertFalse(result["current_integrity"])
        self.assertEqual(result["integrity_error"], "workspace_changed_since_verification")

    def test_credentials_and_enabled_do_not_prove_binding(self):
        for phase in ("validate", "execute"):
            path = self.root / ".bridge" / (phase + "_routine_status.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("binding")
            self.put(path, data)
        result = facade.preflight(WORKSPACE_ID)
        self.assertFalse(result["ready"])
        self.assertTrue(result["local_ready"])
        self.assertFalse(result["binding_confirmed"])

    def test_wrong_binding_blocks_validation_without_sending(self):
        path = self.root / ".bridge/validate_routine_status.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["binding"]["workspace_id"] = "other-workspace"
        self.put(path, data)
        task = self.prepare()
        result = facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])
        self.assertFalse(result["webhook_sent"])
        self.assertEqual(result["error"], "binding_workspace_mismatch")
        self.assertEqual(len(self.transport.calls), 0)

    def test_wrong_binding_blocks_execution_without_sending(self):
        task = self.validated()
        ex = facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])
        path = self.root / ".bridge/execute_routine_status.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["binding"]["workspace_root"] = str(self.root.parent / "different-workspace")
        self.put(path, data)
        result = facade.submit_execution_tool(WORKSPACE_ID, ex["execution_id"])
        self.assertFalse(result["webhook_sent"])
        self.assertTrue(result["error"].startswith("binding_"))
        self.assertEqual(len(self.transport.calls), 1)

    def test_policy_fingerprint_and_claim_entry_mismatch_block(self):
        path = self.root / ".bridge/validate_routine_status.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for field in ("policy_fingerprint", "claim_entry", "registry_path"):
            with self.subTest(field=field):
                data = json.loads(json.dumps(original))
                data["binding"][field] = "wrong-fixture-value"
                self.put(path, data)
                self.assertFalse(facade.preflight(WORKSPACE_ID)["ready"])
        self.assertEqual(len(self.transport.calls), 0)

    def test_webhook_readiness_does_not_require_desktop(self):
        result = facade.preflight(WORKSPACE_ID)
        self.assertTrue(result["local_ready"])
        self.assertTrue(result["binding_confirmed"])
        self.assertTrue(result["submit_permitted"])
        self.assertFalse(result["remote_binding_verified"])
        self.assertEqual(len(self.transport.calls), 0)

    def test_unknown_submission_never_retries(self):
        task = self.validated()
        ex = facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])
        calls = []
        def uncertain(*args):
            calls.append(1)
            return HttpOutcome(kind="timeout", status=None)
        facade.install_transport(uncertain)
        result = facade.submit_execution_tool(WORKSPACE_ID, ex["execution_id"])
        self.assertEqual(result["submission_state"], "unknown_submission")
        for _ in range(3):
            self.status(ex)
            self.assertEqual(facade.submit_execution_tool(WORKSPACE_ID, ex["execution_id"])["error"], "retry_forbidden")
        self.assertEqual(calls, [1])
        self.assertFalse(facade.claim_for(WORKSPACE_ID, ex["execution_id"])["ok"])

    def test_path_typo_and_near_name_are_not_corrected(self):
        task = self.prepare(["src/counters.py"])
        self.assertEqual(task["error"], "allowed_file_outside_policy")
        task, ex = self.start()
        self.change()
        self.emit(task, ex, changed=["src/counters.py"])
        self.assertEqual(self.status(ex)["error"], "scope_violation")

    def test_two_tasks_keep_previous_work_and_detect_overwrite(self):
        task, ex = self.start()
        self.change(label="preserve-me")
        self.emit(task, ex)
        self.assertEqual(self.status(ex)["status"], "succeeded")
        previous = (self.root / "src/counter.py").read_bytes()
        task2, ex2 = self.start(allowed=["tests/test_counter.py"])
        self.change("tests/test_counter.py", "second")
        self.emit(task2, ex2, changed=["tests/test_counter.py"])
        self.assertEqual(self.status(ex2)["status"], "succeeded")
        self.assertEqual((self.root / "src/counter.py").read_bytes(), previous)
        task3, ex3 = self.start(allowed=["tests/test_counter.py"])
        self.change("tests/test_counter.py", "third")
        self.change("src/counter.py", "unauthorized")
        self.emit(task3, ex3, changed=["tests/test_counter.py"])
        self.assertEqual(self.status(ex3)["error"], "scope_violation")

    def test_setup_three_paths_do_not_invent_remote_verification(self):
        from grokbot_mcp import setup_wizard as wizard
        for mode in ("reuse", "create", "migrate"):
            with self.subTest(mode=mode), contextlib.redirect_stdout(io.StringIO()):
                before = self.registry.read_bytes()
                result = wizard.main(["--registry", str(self.registry), "--workspace", WORKSPACE_ID,
                                      "--generate-only", "--routine-mode", mode])
                self.assertEqual(result, 0)
                self.assertEqual(before, self.registry.read_bytes())
                text = (self.registry.parent / "setup" / WORKSPACE_ID / "routine-setup.md").read_text(encoding="utf-8")
                self.assertIn(mode, text)
                self.assertEqual(len(self.transport.calls), 0)

    def test_partial_json_completed_inside_bounded_window(self):
        task, ex = self.start()
        self.change()
        target = self.path("results_execute", ex["execution_id"])
        target.parent.mkdir(parents=True)
        target.write_text('{', encoding="utf-8")
        payload = execution_result(ex["execution_id"], task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        def complete():
            time.sleep(0.025)
            self.put(target, payload)
        writer = threading.Thread(target=complete)
        writer.start()
        try:
            self.assertEqual(self.status(ex)["status"], "succeeded")
        finally:
            writer.join()

    def test_timeliness_is_not_rewritten_by_later_queries(self):
        task, ex = self.start()
        self.change()
        self.emit(task, ex)
        first = self.status(ex)
        self.assertFalse(first["late_result"])
        self.expire("executions", ex["execution_id"])
        self.assertFalse(self.status(ex)["late_result"])
        self.assertEqual(self.status(ex)["verified_at"], first["verified_at"])

    def test_validation_observe_only_does_not_write_ledger(self):
        task = self.prepare()
        facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])
        write_proof(self.root, task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        target = self.path("tasks_v2", task["task_id"])
        before = target.read_bytes()
        result = facade.get_status(WORKSPACE_ID, task_id=task["task_id"], observe_only=True)["validation"]
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(result["ledger_updated"])
        self.assertEqual(before, target.read_bytes())

    def test_validation_terminal_cannot_regress_to_running(self):
        task = self.validated()
        payload = proof(task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        payload.update(status="running", validation_passed=None)
        self.put(self.path("results_v2", task["task_id"]), payload)
        result = facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]
        self.assertEqual(result["status"], "succeeded")
        self.assertFalse(result["current_integrity"])

    def test_validation_unknown_submission_blocks_new_task(self):
        task = self.prepare()
        calls = []
        def uncertain(*args):
            calls.append(1)
            return HttpOutcome(kind="timeout", status=None)
        facade.install_transport(uncertain)
        self.assertEqual(facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])["task_state"], "unknown_submission")
        task2 = self.prepare()
        for _ in range(2):
            facade.get_status(WORKSPACE_ID, task_id=task["task_id"])
            facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])
            facade.submit_validation_tool(WORKSPACE_ID, task2["task_id"])
        self.assertEqual(calls, [1])

    def test_current_revalidation_checks_proof_and_baseline_again(self):
        task, ex = self.start()
        self.change()
        self.emit(task, ex)
        self.status(ex)
        proof_path = self.path("results_v2", task["task_id"])
        original = proof_path.read_bytes()
        self.mutate(proof_path, validation_passed=False)
        self.assertFalse(self.status(ex, verify_current=True)["current_integrity"])
        proof_path.write_bytes(original)
        self.path("execution_baselines", ex["execution_id"]).unlink()
        self.assertFalse(self.status(ex, verify_current=True)["current_integrity"])

    def test_claim_deletion_does_not_allow_second_claim(self):
        task, ex = self.start()
        self.path("execution_claims", ex["execution_id"]).unlink()
        self.assertFalse(facade.claim_for(WORKSPACE_ID, ex["execution_id"])["ok"])

    def test_migration_confirmation_rejects_unresolved_tasks(self):
        from grokbot_mcp.setup_wizard import confirm_binding
        task, ex = self.start()
        target = self.root / ".bridge/execute_routine_status.json"
        before = target.read_bytes()
        with self.assertRaisesRegex(ValueError, "unresolved_tasks"):
            confirm_binding(self.registry, WORKSPACE_ID, "execute", "migrate")
        self.assertEqual(target.read_bytes(), before)

    def test_binding_revision_change_after_authorization_is_rejected(self):
        task, ex = self.start(claim=False)
        target = self.root / ".bridge/execute_routine_status.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["binding"]["revision"] += 1
        self.put(target, data)
        self.assertFalse(facade.claim_for(WORKSPACE_ID, ex["execution_id"])["ok"])

    def test_preflight_detects_missing_exact_target(self):
        (self.root / "src/counter.py").unlink()
        result = facade.preflight(WORKSPACE_ID)
        self.assertFalse(result["local_ready"])
        self.assertFalse(result["submit_permitted"])

    def test_partial_json_at_timeout_can_still_finish_late(self):
        task, ex = self.start()
        target = self.path("results_execute", ex["execution_id"])
        target.parent.mkdir(parents=True)
        target.write_text('{', encoding="utf-8")
        self.expire("executions", ex["execution_id"])
        self.assertEqual(self.status(ex)["status"], "timeout")
        self.change()
        self.emit(task, ex)
        result = self.status(ex)
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["late_result"])


if __name__ == "__main__":
    unittest.main()
