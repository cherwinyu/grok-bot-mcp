from __future__ import annotations
import copy
import json
import os
from unittest.mock import patch
from grokbot_mcp import facade, result_cli
from grokbot_mcp.local_safety import LocalError, current_hash
from grokbot_mcp.result_contract import ContractError
from tests.deployment_support import DeploymentCase
from tests.support import WORKSPACE_ID, proof, execution_result


class ResultPublicationTest(DeploymentCase):
    def staged(self, phase, identity, data):
        directory = "results_v2" if phase == "validate" else "results_execute"
        path = self.root / ".bridge" / directory / ("." + identity + ".result.tmp")
        self.write_json(path, data)
        return path

    def ready_validation(self, status="succeeded"):
        task = self.case.prepare()
        self.assertTrue(facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])["accepted"])
        data = proof(task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        data["status"] = status
        if status in {"pending", "running"}:
            data["validation_passed"] = None
        if status == "failed":
            data.update(validation_passed=False, validation_errors=["fictional validation failed"])
        path = self.staged("validate", task["task_id"], data)
        return task, data, path

    def test_validation_precheck_is_readonly_and_publication_is_not_acceptance(self):
        task, _, path = self.ready_validation()
        before = self.files()
        report = result_cli.precheck(self.registry, WORKSPACE_ID, "validate", task["task_id"], path)
        self.assertTrue(report["format_valid"])
        self.assertEqual(before, self.files())
        result = result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path,
                                    confirm_sha256=report["staged_sha256"])
        self.assertTrue(result["published"])
        self.assertFalse(result["bridge_accepted"])
        self.assertFalse(result["ledger_modified"])
        self.assertEqual(before["tasks_v2/"+task["task_id"]+".json"], self.files()["tasks_v2/"+task["task_id"]+".json"])
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"], "succeeded")

    def test_validation_progress_to_terminal_and_running_cannot_regress(self):
        task, data, path = self.ready_validation("running")
        result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))
        data["status"] = "pending"
        self.write_json(path, data)
        with self.assertRaises(LocalError) as error:
            result_cli.precheck(self.registry, WORKSPACE_ID, "validate", task["task_id"], path)
        self.assertEqual(error.exception.code, "result_transition_invalid")
        data.update(status="succeeded", validation_passed=True)
        self.write_json(path, data)
        self.assertTrue(result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))["published"])

    def test_frozen_failed_validation_cannot_be_replaced(self):
        task, data, path = self.ready_validation("failed")
        result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"], "failed")
        before = self.files()
        data.update(status="succeeded", validation_passed=True, validation_errors=[])
        self.write_json(path, data)
        with self.assertRaises(LocalError) as error:
            result_cli.precheck(self.registry, WORKSPACE_ID, "validate", task["task_id"], path)
        self.assertEqual(error.exception.code, "result_terminal_frozen")
        self.assertEqual(before["results_v2/"+task["task_id"]+".json"], self.files()["results_v2/"+task["task_id"]+".json"])
        self.assertIsNotNone(facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])["error"])

    def test_unobserved_terminal_also_cannot_be_overwritten(self):
        task, _, path = self.ready_validation()
        raw = path.read_bytes()
        result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))
        self.assertFalse(path.exists())
        path.write_bytes(raw)
        with self.assertRaises(LocalError) as error:
            result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))
        self.assertEqual(error.exception.code, "result_terminal_frozen")

    def test_identity_format_and_wrong_stage_do_not_touch_ledger(self):
        task, data, path = self.ready_validation()
        original = copy.deepcopy(data)
        before = self.case.path("tasks_v2", task["task_id"]).read_bytes()
        for key, value, category in (("task_id", "00000000-0000-4000-8000-000000000099", "identity_error"),
                                     ("validation_passed", True + 1, "format_error"),
                                     ("action", "execute_manifest", "identity_error")):
            with self.subTest(key=key):
                data = {**original, key: value}
                self.write_json(path, data)
                with self.assertRaises(ContractError) as error:
                    result_cli.precheck(self.registry, WORKSPACE_ID, "validate", task["task_id"], path)
                self.assertEqual(error.exception.category, category)
                self.assertEqual(before, self.case.path("tasks_v2", task["task_id"]).read_bytes())
        self.assertEqual(len(self.case.transport.calls), 1)

    def test_execution_published_only_with_original_claim_and_hash(self):
        task, ex = self.case.start()
        self.case.change()
        data = execution_result(ex["execution_id"], task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        path = self.staged("execute", ex["execution_id"], data)
        before = self.files()
        report = result_cli.precheck(self.registry, WORKSPACE_ID, "execute", ex["execution_id"], path)
        self.assertEqual(before, self.files())
        published = result_cli.publish(self.registry, WORKSPACE_ID, "execute", ex["execution_id"], path,
                                       confirm_sha256=report["staged_sha256"])
        self.assertTrue(published["published"])
        self.assertFalse(path.exists())
        self.assertTrue(self.case.path("results_execute", ex["execution_id"]).is_file())
        self.assertIn("protocol_cleanup", published)
        self.assertEqual(self.case.status(ex)["status"], "succeeded")
        claim = self.case.path("execution_claims", ex["execution_id"])
        self.assertEqual(before["execution_claims/"+claim.name], claim.read_bytes())

    def test_missing_claim_does_not_create_one(self):
        task, ex = self.case.start(claim=False)
        data = execution_result(ex["execution_id"], task["task_id"], task["manifest_sha256"], [])
        path = self.staged("execute", ex["execution_id"], data)
        before = self.files()
        with self.assertRaises(ValueError):
            result_cli.precheck(self.registry, WORKSPACE_ID, "execute", ex["execution_id"], path)
        self.assertEqual(before, self.files())
        self.assertFalse(self.case.path("execution_claims", ex["execution_id"]).exists())

    def test_tampered_claim_hash_never_repaired(self):
        task, ex = self.case.start()
        claim = self.case.path("execution_claims", ex["execution_id"])
        claim.write_bytes(claim.read_bytes() + b" ")
        data = execution_result(ex["execution_id"], task["task_id"], task["manifest_sha256"], [])
        path = self.staged("execute", ex["execution_id"], data)
        before = self.files()
        with self.assertRaises(ValueError) as error:
            result_cli.precheck(self.registry, WORKSPACE_ID, "execute", ex["execution_id"], path)
        self.assertEqual(str(error.exception), "claim_hash_mismatch")
        self.assertEqual(before, self.files())

    def test_format_failure_can_be_fixed_before_first_publish(self):
        task, ex = self.case.start()
        self.case.change()
        data = execution_result(ex["execution_id"], task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        data["tests"]["passed"] = True
        path = self.staged("execute", ex["execution_id"], data)
        with self.assertRaises(ContractError) as error:
            result_cli.precheck(self.registry, WORKSPACE_ID, "execute", ex["execution_id"], path)
        self.assertEqual(error.exception.field, "tests.passed")
        self.assertFalse(self.case.path("results_execute", ex["execution_id"]).exists())
        data["tests"]["passed"] = 1
        self.write_json(path, data)
        self.assertTrue(result_cli.publish(self.registry, WORKSPACE_ID, "execute", ex["execution_id"], path,
                                          confirm_sha256=current_hash(path))["published"])
        self.assertEqual(self.case.status(ex)["status"], "succeeded")

    def test_partial_and_invalid_utf8_candidates_remain_unpublished(self):
        task, _, path = self.ready_validation()
        ledger = self.case.path("tasks_v2", task["task_id"]).read_bytes()
        for raw in (b'{"status":', b'{"x":"\xe4\xb8'):
            path.write_bytes(raw)
            with self.assertRaises(LocalError):
                result_cli.precheck(self.registry, WORKSPACE_ID, "validate", task["task_id"], path)
            self.assertFalse(self.case.path("results_v2", task["task_id"]).exists())
            self.assertEqual(ledger, self.case.path("tasks_v2", task["task_id"]).read_bytes())

    def test_arbitrary_path_and_hardlink_rejected(self):
        task, data, path = self.ready_validation()
        outside = self.base / "elsewhere.tmp"
        self.write_json(outside, data)
        with self.assertRaises(LocalError) as error:
            result_cli.precheck(self.registry, WORKSPACE_ID, "validate", task["task_id"], outside)
        self.assertEqual(error.exception.code, "result_staging_path_invalid")
        path.unlink()
        os.link(outside, path)
        with self.assertRaises(LocalError) as error:
            result_cli.precheck(self.registry, WORKSPACE_ID, "validate", task["task_id"], path)
        self.assertEqual(error.exception.code, "linked_path_rejected")
        with self.assertRaises(LocalError):
            result_cli.precheck(self.registry, WORKSPACE_ID, "validate", "../../outside", outside)

    def test_wrong_confirm_and_atomic_failure_preserve_target(self):
        task, _, path = self.ready_validation()
        with self.assertRaises(LocalError):
            result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256="wrong")
        with patch("grokbot_mcp.local_safety.os.replace", side_effect=PermissionError("fictional-private")):
            with self.assertRaises(LocalError):
                result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))
        self.assertFalse(self.case.path("results_v2", task["task_id"]).exists())
        self.assertFalse(list(path.parent.glob(".grokbot-*.tmp")))

    def candidate(self, phase, identity, data):
        path = result_cli.private_candidate_path(phase, identity)
        self.write_json(path, data)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_source_must_be_the_temp_candidate_for_both_phases(self):
        task, data, staged = self.ready_validation()
        staged.unlink()
        inside = self.root / ".bridge" / "results_v2" / ("." + task["task_id"] + ".result.candidate.json")
        self.write_json(inside, data)
        with self.assertRaises(LocalError) as error:
            result_cli.stage_candidate(self.registry, WORKSPACE_ID, "validate", task["task_id"], inside)
        self.assertEqual(error.exception.code, "result_source_location_invalid")
        wrong_name = result_cli.candidate_root() / "validate" / (task["task_id"] + ".json")
        self.write_json(wrong_name, data)
        self.addCleanup(wrong_name.unlink, missing_ok=True)
        with self.assertRaises(LocalError) as error:
            result_cli.stage_candidate(self.registry, WORKSPACE_ID, "validate", task["task_id"], wrong_name)
        self.assertEqual(error.exception.code, "result_source_location_invalid")
        source = self.candidate("validate", task["task_id"], data)
        report = result_cli.stage_candidate(self.registry, WORKSPACE_ID, "validate", task["task_id"], source)
        self.assertEqual(report["source_contract"], "temp_candidate")
        self.assertFalse(report["staged_written"])
        execute_source = self.candidate("execute", task["task_id"], data)
        with self.assertRaises(LocalError) as error:
            result_cli.stage_candidate(self.registry, WORKSPACE_ID, "validate", task["task_id"], execute_source)
        self.assertEqual(error.exception.code, "result_source_location_invalid")

    def test_terminal_publish_cleans_contract_files_and_records_it(self):
        task, data, path = self.ready_validation()
        source = self.candidate("validate", task["task_id"], data)
        staged_bytes = path.read_bytes()
        self.assertEqual(source.read_bytes(), staged_bytes)
        ledger = self.case.path("tasks_v2", task["task_id"]).read_bytes()
        other = self.root / ".bridge" / "results_v2" / ("." + task["task_id"] + ".result.candidate.json")
        self.write_json(other, data)
        result = result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path,
                                    confirm_sha256=current_hash(path))
        self.assertTrue(result["published"])
        self.assertFalse(result["ledger_modified"])
        self.assertFalse(path.exists())
        self.assertFalse(source.exists())
        self.assertTrue(other.is_file())
        published = self.case.path("results_v2", task["task_id"])
        self.assertTrue(published.is_file())
        self.assertEqual(ledger, self.case.path("tasks_v2", task["task_id"]).read_bytes())
        cleanup = result["protocol_cleanup"]
        self.assertTrue(cleanup["cleanup_recorded"])
        self.assertFalse(cleanup["submission_ledger_modified"])
        self.assertEqual({item["role"] for item in cleanup["removed"]}, {"staged", "candidate"})
        record = self.root / ".bridge" / "protocol_cleanup" / "validate" / (task["task_id"] + ".json")
        self.assertTrue(record.is_file())
        recorded = record.read_bytes()
        again = result_cli.cleanup_protocol_files(self.registry, WORKSPACE_ID, "validate", task["task_id"])
        self.assertEqual(again["removed"], [])
        self.assertEqual(recorded, record.read_bytes())
        self.assertTrue(published.is_file())
        self.assertTrue(other.is_file())

    def test_running_publish_keeps_the_staged_file(self):
        task, _data, path = self.ready_validation("running")
        result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))
        self.assertTrue(path.is_file())
        self.assertFalse((self.root / ".bridge" / "protocol_cleanup").exists())

    def test_cleanup_refuses_nonterminal_and_hash_mismatch_and_other_ids(self):
        task, data, path = self.ready_validation("running")
        result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))
        with self.assertRaises(LocalError) as error:
            result_cli.cleanup_protocol_files(self.registry, WORKSPACE_ID, "validate", task["task_id"])
        self.assertEqual(error.exception.code, "result_cleanup_not_terminal")
        self.assertTrue(path.is_file())
        data.update(status="succeeded", validation_passed=True, validation_errors=[])
        self.write_json(path, data)
        result_cli.publish(self.registry, WORKSPACE_ID, "validate", task["task_id"], path, confirm_sha256=current_hash(path))
        published = self.case.path("results_v2", task["task_id"])
        mismatched = self.candidate("validate", task["task_id"], {**data, "validation_errors": ["still drafting"]})
        report = result_cli.cleanup_protocol_files(self.registry, WORKSPACE_ID, "validate", task["task_id"])
        self.assertTrue(mismatched.is_file())
        self.assertEqual(report["skipped"][0]["reason"], "hash_mismatch")
        self.assertTrue(published.is_file())
        stranger = self.root / ".bridge" / "results_v2" / ".00000000-0000-4000-8000-000000000099.result.tmp"
        stranger.write_bytes(published.read_bytes())
        result_cli.cleanup_protocol_files(self.registry, WORKSPACE_ID, "validate", task["task_id"])
        self.assertTrue(stranger.is_file())

    def test_safe_staging_requires_explicit_confirmation_and_derives_path(self):
        task, data, path = self.ready_validation()
        path.unlink()
        source = self.candidate("validate", task["task_id"], data)
        before = self.files()
        report = result_cli.stage_candidate(self.registry, WORKSPACE_ID, "validate", task["task_id"], source)
        self.assertFalse(report["staged_written"])
        self.assertEqual(before, self.files())
        with self.assertRaises(LocalError):
            result_cli.stage_candidate(self.registry, WORKSPACE_ID, "validate", task["task_id"], source, apply=True, confirm_sha256="wrong")
        result = result_cli.stage_candidate(self.registry, WORKSPACE_ID, "validate", task["task_id"], source,
                                            apply=True, confirm_sha256=report["staged_sha256"])
        self.assertTrue(result["staged_written"])
        self.assertEqual(path.read_bytes(), source.read_bytes())
        with self.assertRaises(LocalError):
            result_cli.stage_candidate(self.registry, WORKSPACE_ID, "validate", task["task_id"], source, apply=True, confirm_sha256=report["staged_sha256"])
        self.assertFalse(self.case.path("results_v2", task["task_id"]).exists())

    def test_not_run_is_code_execution_failure_not_fake_test_counts(self):
        task, ex = self.case.start()
        data = execution_result(ex["execution_id"], task["task_id"], task["manifest_sha256"], [])
        data.update(status="failed", tests={"command_id": "python_unittest", "not_run": True})
        path = self.staged("execute", ex["execution_id"], data)
        result_cli.publish(self.registry, WORKSPACE_ID, "execute", ex["execution_id"], path, confirm_sha256=current_hash(path))
        result = self.case.status(ex)
        self.assertEqual(result["error"], "execution_failed")
        self.assertEqual(result["result_diagnosis"]["category"], "code_execution_failure")
        self.assertIsNone(result["test_passed"])
