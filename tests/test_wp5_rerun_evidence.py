"""WP5: synthetic evidence and registered local processes, no live configuration."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import sys
import shutil
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import bridge_tests, facade
from grokbot_mcp.execution import service
from grokbot_mcp.materials import render_routine
from grokbot_mcp.observation import LedgerView
from grokbot_mcp.registry import _workspace, policy_fingerprint, routine_profile
from grokbot_mcp.rerun_config import parse_rerun
from grokbot_mcp.result_examples import examples
from grokbot_mcp.storage import JsonLedger
from tests.test_u2a_contract import entry


def fixture_directory(test):
    # Fictional, credential-free fixtures inherit the test runner's ACL.
    # Python's Windows 0700 TemporaryDirectory ACL excludes this sandbox token.
    parent = Path(tempfile.gettempdir()).resolve()
    root = parent / ("wp5-fictional-" + uuid.uuid4().hex)
    root.mkdir()
    def cleanup():
        if root.resolve().parent != parent or not root.name.startswith("wp5-fictional-"):
            raise ValueError("fixture_cleanup_outside_temp")
        def remove_readonly(function, name, error):
            target = Path(name).resolve()
            if not target.is_relative_to(root.resolve()) or not isinstance(error, PermissionError):
                raise error
            # Git creates read-only object files inside this owned fixture.
            os.chmod(target, stat.S_IWRITE)
            function(name)
        shutil.rmtree(root, onexc=remove_readonly)
    test.addCleanup(cleanup)
    return root


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = fixture_directory(self)
        workspace = _workspace(entry(require_test_output=True))
        command = replace(workspace.test_commands[0], rerun=parse_rerun(
            {"argv": [sys.executable, "-B", "-c", "print('fictional WP5')"]}))
        self.workspace = replace(workspace, root=self.root, test_commands=(command,))
        self.parsed = copy.deepcopy(examples()["execute-succeeded"])
        self.identity = self.parsed["execution_id"]
        self.store = JsonLedger(self.root / ".bridge/executions")
        self.path = self.root / ".bridge/results_execute" / (self.identity + ".json")
        self.path.parent.mkdir(parents=True)
        self.record = {k: self.parsed[k] for k in ("execution_id", "manifest_task_id", "manifest_sha256", "action")}
        self.record.update(execution_result_path=f".bridge/results_execute/{self.identity}.json",
                           submission_state="accepted", execution_state="running", terminal=False)
        self.document = dict(test_command_id="python_unittest", task_type="code_change",
                             allowed_files=["src/counter.py"])
        self.baseline = dict(git_head_text="a" * 40, file_hashes={"src/counter.py": "old"})
        self.current = dict(git_head_text="a" * 40, file_hashes={"src/counter.py": "new"})
        self.report = {"ok": True, "document": self.document}
        self.stack.enter_context(patch.object(service, "assess", return_value=self.report))
        self.stack.enter_context(patch.object(bridge_tests, "assess", return_value=self.report))
        self.verify = self.stack.enter_context(patch.object(service, "verify_evidence",
                                                side_effect=lambda *a, **kw: (self.baseline, self.current)))
        self.stack.enter_context(patch.object(facade, "_workspace", side_effect=lambda _: (self.workspace, None, [])))
        self.stack.enter_context(patch.object(facade, "execution_switch_enabled", return_value=False))

    def settle(self, expected="test_evidence_missing"):
        self.path.write_text(json.dumps(self.parsed), encoding="utf-8")
        self.store.create(self.identity, self.record)
        result = self.status()["execution"]
        self.assertEqual(result["status"], expected, result)
        self.original = self.path.read_bytes(), self.store.path_for(self.identity).read_bytes()

    def status(self, **options):
        return facade.get_status(self.workspace.workspace_id, execution_id=self.identity, **options)

    def unchanged(self):
        self.assertEqual(self.original, (self.path.read_bytes(), self.store.path_for(self.identity).read_bytes()))

    def test_success_real_process_idempotence_and_original_bytes(self):
        self.settle()
        first = self.status(rerun_tests=True)
        self.assertEqual(first["execution"]["status"], "succeeded_bridge_evidence", first)
        self.assertEqual(first["execution"]["historical_status"], "test_evidence_missing")
        self.assertEqual(first["execution"]["evidence_source"], "bridge_rerun")
        self.assertFalse(first["execution"]["qa_accepted"])
        self.assertEqual(first["bridge_tests"]["agreement"]["exit_code"], "match")
        receipt = self.root / first["bridge_tests"]["evidence_path"]
        original_receipt = receipt.read_bytes()
        with patch("grokbot_mcp.rerun_process.run") as run:
            second = self.status(rerun_tests=True)
            for options in ({}, {"observe_only": True}, {"verify_current": True}):
                observed = self.status(**options)["execution"]
                self.assertEqual(observed["status"], "succeeded_bridge_evidence", observed)
            run.assert_not_called()
        self.assertEqual(first["bridge_tests"], second["bridge_tests"])
        self.assertEqual(receipt.read_bytes(), original_receipt)
        self.assertEqual(len(list(receipt.parent.glob("*.json"))), 1)
        self.unchanged()

    def test_exit_disagreement_is_failed_and_cannot_be_replaced(self):
        self.settle()
        with patch("grokbot_mcp.rerun_process.run", return_value={
                "executed": True, "raw": b"failed", "exit_code": 7, "timed_out": False}) as run:
            first = self.status(rerun_tests=True)
            second = self.status(rerun_tests=True)
        run.assert_called_once()
        self.assertFalse(first["bridge_tests"]["ok"])
        self.assertEqual(first["bridge_tests"]["agreement"]["exit_code"], "mismatch")
        self.assertEqual(first["execution"]["status"], "failed_bridge_evidence")
        self.assertEqual(first["bridge_tests"], second["bridge_tests"])
        self.unchanged()

    def test_ordinary_queries_never_run_tests(self):
        self.settle()
        with patch("grokbot_mcp.rerun_process.run") as run:
            for options in ({}, {"observe_only": True}, {"verify_current": True}):
                self.assertEqual(self.status(**options)["execution"]["status"], "test_evidence_missing")
        run.assert_not_called()
        self.assertFalse((self.root / ".bridge/bridge_test_runs").exists())
        self.unchanged()

    def test_no_registered_rerun_keeps_missing_evidence(self):
        self.workspace = replace(self.workspace, test_commands=(replace(self.workspace.test_commands[0], rerun=None),))
        self.settle()
        with patch("grokbot_mcp.rerun_process.run") as run:
            result = self.status(rerun_tests=True)
        self.assertEqual(result["execution"]["status"], "test_evidence_missing")
        self.assertEqual(result["bridge_tests"]["error"], "rerun_not_configured")
        run.assert_not_called()
        self.unchanged()

    def test_identity_failure_refuses_rerun(self):
        self.parsed["machine"] = "WRONG-FICTIONAL-PC"
        self.settle("identity_mismatch")
        with patch("grokbot_mcp.rerun_process.run") as run:
            self.assertFalse(self.status(rerun_tests=True)["bridge_tests"]["executed"])
        run.assert_not_called()
        self.unchanged()

    def test_scope_failure_refuses_rerun(self):
        self.parsed["changed_files"] = ["outside.py"]
        self.settle("scope_violation")
        with patch("grokbot_mcp.rerun_process.run") as run:
            self.assertEqual(self.status(rerun_tests=True)["bridge_tests"]["error"], "rerun_result_unverified")
        run.assert_not_called()
        self.unchanged()

    def test_missing_output_cannot_hide_failed_tests(self):
        self.parsed["status"] = "failed"
        self.parsed["tests"].update(failed=1, exit_code=1)
        self.settle()
        with patch("grokbot_mcp.rerun_process.run") as run:
            self.assertEqual(self.status(rerun_tests=True)["bridge_tests"]["error"], "rerun_result_unverified")
        run.assert_not_called()
        self.unchanged()

    def test_missing_output_cannot_hide_unmodified_required_file(self):
        self.current = copy.deepcopy(self.baseline)
        self.parsed.update(changed_files=[], code_modified=False)
        self.settle()
        with patch("grokbot_mcp.rerun_process.run") as run:
            self.assertEqual(self.status(rerun_tests=True)["bridge_tests"]["error"], "rerun_result_unverified")
        run.assert_not_called()
        self.unchanged()

    def test_git_review_head_change_refuses_before_run(self):
        self.settle()
        self.verify.side_effect = ValueError("git_head_mismatch")
        with patch("grokbot_mcp.rerun_process.run") as run:
            self.assertEqual(self.status(rerun_tests=True)["bridge_tests"]["error"], "rerun_result_unverified")
        run.assert_not_called()
        self.unchanged()

    def test_changes_during_rerun_cannot_supply_success(self):
        self.settle()
        def changed(*args, **kwargs):
            self.current = {**self.current, "file_hashes": {"src/counter.py": "changed again"}}
            return {"executed": True, "raw": b"ok", "exit_code": 0, "timed_out": False}
        with patch("grokbot_mcp.rerun_process.run", side_effect=changed):
            result = self.status(rerun_tests=True)
        self.assertEqual(result["execution"]["status"], "failed_bridge_evidence")
        self.assertEqual(result["bridge_tests"]["error"], "rerun_source_changed")
        self.unchanged()

    def test_changed_result_cannot_reuse_supplement(self):
        self.settle()
        self.assertTrue(self.status(rerun_tests=True)["bridge_tests"]["ok"])
        self.path.write_bytes(self.path.read_bytes() + b"\n")
        with patch("grokbot_mcp.rerun_process.run") as run:
            result = self.status(rerun_tests=True)
        self.assertNotEqual(result["execution"]["status"], "succeeded_bridge_evidence")
        self.assertEqual(result["bridge_tests"]["error"], "result_changed")
        run.assert_not_called()

    def test_output_deferral_cannot_write_a_ledger(self):
        with self.assertRaisesRegex(ValueError, "rerun_result_unverified"):
            service._interpret(LedgerView(self.store, self.record, True), self.workspace,
                               self.record, {}, self.parsed, "a" * 64, defer_test_output=True)

    def test_wait_option_reaches_explicit_recovery(self):
        self.settle()
        with patch("grokbot_mcp.status_wait.time.sleep") as sleep:
            result = self.status(rerun_tests=True, wait_seconds=1)
        sleep.assert_not_called()
        self.assertEqual(result["execution"]["status"], "succeeded_bridge_evidence", result)

    def test_concurrent_recovery_publishes_only_one_run(self):
        from concurrent.futures import ThreadPoolExecutor
        self.settle()
        with patch("grokbot_mcp.rerun_process.run", side_effect=lambda *a, **kw: {
                "executed": True, "raw": b"ok", "exit_code": 0, "timed_out": False}) as run:
            with ThreadPoolExecutor(max_workers=2) as workers:
                results = list(workers.map(lambda _: bridge_tests.rerun(self.workspace, self.identity), range(2)))
        run.assert_called_once()
        self.assertTrue(all(result["ok"] for result in results), results)
        self.assertEqual(results[0], results[1])
        self.unchanged()

    def test_current_workspace_drift_does_not_reuse_success(self):
        self.settle()
        self.assertTrue(self.status(rerun_tests=True)["bridge_tests"]["ok"])
        self.current = {**self.current, "file_hashes": {"src/counter.py": "later"}}
        with patch("grokbot_mcp.rerun_process.run") as run:
            result = self.status(verify_current=True)["execution"]
        self.assertNotEqual(result["status"], "succeeded_bridge_evidence")
        self.assertFalse(result["current_integrity"])
        run.assert_not_called()


class NativeWorkflowTest(unittest.TestCase):
    """Actual Git/claim/ledger/runner path, memory credentials and transport only."""
    def test_advisory_claim_to_supplemental_status(self):
        import os
        from tests import support
        from tests.test_git_gate_advisory import GitGateAdvisoryTest
        from grokbot_mcp.credentials.backend import install_backend
        from grokbot_mcp.registry import load_registry
        self.base = fixture_directory(self)
        original_git = support.git
        with patch.dict(os.environ), patch.object(support, "git", side_effect=lambda root, *args:
                original_git(root, "-c", "safe.directory=*", *args)):
            self.addCleanup(facade.install_transport, None)
            self.addCleanup(install_backend, None, None)
            self.root = support.make_advisory_workspace(self.base)
            self.registry = self.base / "registry.json"
            data = json.loads(self.registry.read_text(encoding="utf-8"))
            data["workspaces"][0]["require_test_output"] = True
            data["workspaces"][0]["test_commands"][1]["rerun"] = {
                "argv": [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"]}
            self.registry.write_text(json.dumps(data), encoding="utf-8")
            workspace = load_registry(self.registry)[support.WORKSPACE_ID]
            for phase in ("validate", "execute"):
                path = self.root / ".bridge" / (phase + "_routine_status.json")
                binding = json.loads(path.read_text(encoding="utf-8"))
                binding["binding"]["policy_fingerprint"] = policy_fingerprint(workspace)
                path.write_text(json.dumps(binding), encoding="utf-8")
            (self.base / "execution_switch.json").write_text('{"enabled": true}', encoding="utf-8")
            self.prepare = lambda: GitGateAdvisoryTest.prepare(self)
            task, execution = GitGateAdvisoryTest.pending_claim(self)
            identity = execution["execution_id"]
            self.assertTrue(facade.claim_for(support.WORKSPACE_ID, identity)["ok"])
            source = self.root / "src/counter.py"
            source.write_text(source.read_text(encoding="utf-8") + "\n# fictional WP5 change\n", encoding="utf-8")
            GitGateAdvisoryTest.write_result(self, task, execution, ["src/counter.py"])
            self.assertEqual(facade.get_status(support.WORKSPACE_ID, execution_id=identity)["execution"]["status"],
                             "test_evidence_missing")
            result_path = self.root / ".bridge/results_execute" / (identity + ".json")
            ledger_path = self.root / ".bridge/executions" / (identity + ".json")
            original = result_path.read_bytes(), ledger_path.read_bytes()
            result = facade.get_status(support.WORKSPACE_ID, execution_id=identity, rerun_tests=True)
            self.assertEqual(result["execution"]["status"], "succeeded_bridge_evidence", result)
            self.assertEqual(result["bridge_tests"]["agreement"]["exit_code"], "match")
            self.assertEqual(original, (result_path.read_bytes(), ledger_path.read_bytes()))


class TemplateTest(unittest.TestCase):
    def render(self, workspace, phase="execute"):
        return render_routine(phase, workspace_root=Path("D:/fictional/workspace"),
                              workspace_id="example-workspace", registry=Path("D:/fictional/registry.json"),
                              python=Path("D:/fictional/python.exe"), mode="reuse",
                              test_options=routine_profile(workspace))

    def test_output_requirements_are_in_execute_body_and_writing(self):
        workspace = _workspace(entry(require_test_output=True))
        fingerprint = policy_fingerprint(workspace)
        rendered = self.render(workspace)
        body, writing = rendered.split("## Result writing:", 1)
        for section in (body, writing):
            for field in ('tests.output={"head","tail","truncated"}', "tests.output_sha256", "tests.output_bytes",
                          "grokbot_mcp.result_contract.output_evidence", "not_run=true"):
                self.assertIn(field, section)
        self.assertEqual(policy_fingerprint(workspace), fingerprint)
        self.assertEqual(self.render(workspace, "validate"), self.render(replace(workspace, require_test_output=False), "validate"))

    def test_false_and_omitted_preserve_fixed_legacy_bytes(self):
        from grokbot_mcp.routine import CLAIM_GUIDANCE, claim_command
        from tests.test_u3_prefixes import workspace
        original = workspace(routine_claim_fingerprint=True)
        expected = {"validate": "91c6d72f05f6c65a0282dcda05fb6321ce321f4dcb4b6fbec4fe30c80dc7a9cd",
                    "execute": "ec6bd97b5b0ff4f3a704d26e6b153c8ca0d2f20fcc904311b0711477b20abbfe"}
        with patch("grokbot_mcp.materials.__version__", "0.1.4b1"):
            for ws in (original, replace(original, require_test_output=False)):
                for phase, digest in expected.items():
                    text = self.render(ws, phase)
                    fingerprint_option = " --expect-fingerprint " + policy_fingerprint(ws)
                    guidance_block = CLAIM_GUIDANCE + "\n\n"
                    command_count = 1 if phase == "execute" else 0
                    self.assertEqual(text.count(fingerprint_option), command_count)
                    self.assertEqual(text.count(claim_command("example-workspace", policy_fingerprint(ws))),
                                     command_count)
                    self.assertEqual(text.count(CLAIM_GUIDANCE), 1)
                    self.assertEqual(text.count(guidance_block), 1)
                    self.assertNotIn("tests.output_sha256", text)
                    # Exclude only the new claim option and guidance; all
                    # remaining template bytes keep their pinned legacy hashes.
                    text = text.replace(fingerprint_option, "", command_count)
                    text = text.replace(guidance_block, "", 1)
                    self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), digest)

    def test_policy_fingerprint_does_not_depend_on_template_text(self):
        workspace = _workspace(entry(require_test_output=True))
        fingerprint = policy_fingerprint(workspace)
        with patch("grokbot_mcp.materials.__version__", "fictional-template-revision"):
            self.render(workspace)
            self.assertEqual(policy_fingerprint(workspace), fingerprint)


if __name__ == "__main__":
    unittest.main()
