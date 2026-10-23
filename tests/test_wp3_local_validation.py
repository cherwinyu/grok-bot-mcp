"""WP3 contracts: fictional workspaces, memory-only credentials and transport."""
from __future__ import annotations

import hashlib
import asyncio
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from grokbot_mcp import facade
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.execution.service import assess
from grokbot_mcp.materials import render_routine
from grokbot_mcp.registry import RegistryError, _workspace, load_registry, policy_fingerprint, routine_profile
from grokbot_mcp.status_wait import wait_for_status
from grokbot_mcp.storage import JsonLedger, read_json
from grokbot_mcp.validation.evidence import local_validation_error
from tests.support import WORKSPACE_ID, execution_result, make_advisory_workspace, write_proof
from tests.test_u2a_contract import entry
from tests.test_u3_prefixes import workspace as fixed_workspace


class LocalValidationTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = make_advisory_workspace(self.base)
        self.registry = self.base / "registry.json"
        self.configure(local_validation=True)
        self.addCleanup(facade.install_transport, None)
        self.addCleanup(install_backend, None, None)

    def configure(self, **fields):
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        data["workspaces"][0].update(fields)
        self.registry.write_text(json.dumps(data), encoding="utf-8")
        ws = load_registry(self.registry)[WORKSPACE_ID]
        for phase in ("validate", "execute"):
            path = self.root / ".bridge" / (phase + "_routine_status.json")
            binding = json.loads(path.read_text(encoding="utf-8"))
            binding["binding"]["policy_fingerprint"] = policy_fingerprint(ws)
            path.write_text(json.dumps(binding), encoding="utf-8")
        return ws

    def prepare(self):
        task = facade.prepare_task(WORKSPACE_ID, "code_change", "Fictional local check.",
                                   ["src/counter.py"], ["Tests pass."], "python_unittest")
        self.assertIsNone(task.get("error"), task)
        return task

    def submit(self, task):
        with patch.object(facade, "status_for") as status, patch.object(facade, "load_for") as load, \
                patch.object(facade, "_send") as send:
            result = facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])
        status.assert_not_called()
        load.assert_not_called()
        send.assert_not_called()
        self.assertFalse(result["webhook_sent"], result)
        self.assertTrue(result["validated_locally"], result)
        return result

    def proof(self, task):
        return self.root / ".bridge/results_v2" / (task["task_id"] + ".json")

    def test_success_switch_off_and_frozen_duplicate(self):
        task = self.prepare()
        result = self.submit(task)
        self.assertEqual(result["task_state"], "succeeded", result)
        proof = read_json(self.proof(task))[0]
        self.assertEqual(proof["validator"], "bridge_local")
        self.assertEqual(proof["machine"], "EXAMPLE-PC")
        self.assertEqual(proof["validation_errors"], [])
        store = JsonLedger(self.root / ".bridge/tasks_v2")
        self.assertTrue(store.read(task["task_id"])["terminal"])
        before = (self.proof(task).read_bytes(), store.path_for(task["task_id"]).read_bytes())
        self.assertEqual(self.submit(task)["error"], "duplicate_task_id")
        self.assertEqual(before, (self.proof(task).read_bytes(), store.path_for(task["task_id"]).read_bytes()))

    def test_workspace_writes_disabled_does_not_consume_task(self):
        task = self.prepare()
        self.configure(writes_enabled=False)
        self.assertEqual(self.submit(task)["error"], "workspace_writes_disabled")
        self.assertFalse(self.proof(task).exists())
        self.assertFalse((self.root / ".bridge/tasks_v2" / (task["task_id"] + ".json")).exists())

    def test_wait_observe_only_preserves_unfrozen_legacy_result_and_ledger(self):
        self.configure(local_validation=False)
        task = self.prepare()
        (self.base / "execution_switch.json").write_text('{"enabled":true}', encoding="utf-8")
        self.assertTrue(facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])["accepted"])
        write_proof(self.root, task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        ledger = self.root / ".bridge/tasks_v2" / (task["task_id"] + ".json")
        before = (ledger.read_bytes(), self.proof(task).read_bytes())
        with patch("grokbot_mcp.status_wait.time.sleep") as sleep:
            result = facade.get_status(WORKSPACE_ID, task_id=task["task_id"], observe_only=True, wait_seconds=50)
            sleep.assert_not_called()
        self.assertEqual(result["validation"]["status"], "succeeded")
        self.assertEqual(before, (ledger.read_bytes(), self.proof(task).read_bytes()))
        self.assertNotIn("terminal", read_json(ledger)[0])
        result = facade.get_status(WORKSPACE_ID, task_id=task["task_id"], wait_seconds=50)
        self.assertEqual(result["validation"]["status"], "succeeded")
        self.assertTrue(read_json(ledger)[0]["terminal"])
        self.assertEqual(before[1], self.proof(task).read_bytes())

    def test_prepare_automatically_validates_and_preserves_existing_proof(self):
        task = self.prepare()
        result = facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])
        self.assertEqual(result["approval_state"], "granted", result)
        original = self.proof(task).read_bytes()
        self.assertEqual(facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])["error"], "duplicate_execution")
        self.assertEqual(original, self.proof(task).read_bytes())

    def test_failed_checks_publish_failed_receipts(self):
        cases = [({"workspace_id": "other-workspace"}, "not_prepared"),
                 ({"allowed_files": ["src/outside.py"]}, "manifest_policy_mismatch"),
                 ({"allowed_files": ["src/do_not_touch.py"]}, "manifest_policy_mismatch"),
                 ({"forbidden_files": []}, "forbidden_mismatch"),
                 ({"baseline_commit": "bad"}, "manifest_policy_mismatch")]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                task = self.prepare()
                path = self.root / task["manifest_path"]
                doc = read_json(path)[0]
                doc.update(changes)
                path.write_text(json.dumps(doc), encoding="utf-8")
                result = self.submit(task)
                self.assertNotEqual(result["task_state"], "succeeded")
                proof = read_json(self.proof(task))[0]
                self.assertEqual(proof["status"], "failed")
                self.assertEqual(proof["validation_errors"], [expected])
                self.assertFalse(proof["code_modified"])
                before = self.proof(task).read_bytes()
                self.assertNotEqual(facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])["approval_state"], "granted")
                self.assertEqual(before, self.proof(task).read_bytes())

    def test_hash_race_is_failed_not_rebased(self):
        task = self.prepare()
        from grokbot_mcp.execution.service import assess_manifest
        def changed(ws, task_id):
            path = self.root / task["manifest_path"]
            doc = read_json(path)[0]
            doc["task_description"] = "Changed during validation."
            path.write_text(json.dumps(doc), encoding="utf-8")
            return assess_manifest(ws, task_id)
        with patch("grokbot_mcp.execution.service.assess_manifest", side_effect=changed):
            result = self.submit(task)
        self.assertEqual(result["error"], "hash_mismatch")
        proof = read_json(self.proof(task))[0]
        self.assertEqual(proof["manifest_sha256"], task["manifest_sha256"])
        self.assertEqual(proof["validation_errors"], ["hash_mismatch"])

    def test_git_read_failure_and_protected_changes(self):
        task = self.prepare()
        with patch("grokbot_mcp.validation.local.git_snapshot", return_value={"git_head": "unconfirmed"}):
            self.submit(task)
        self.assertEqual(read_json(self.proof(task))[0]["validation_errors"], ["git_status_failed"])
        task = self.prepare()
        (self.root / "src/do_not_touch.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.submit(task)
        self.assertEqual(read_json(self.proof(task))[0]["validation_errors"], ["protected_or_forbidden_changed"])

    def test_advisory_does_not_require_registered_head_equality(self):
        self.configure(expected_head="a" * 40)
        self.assertEqual(self.submit(self.prepare())["task_state"], "succeeded")

    def test_prefix_ceiling_snapshot_reused(self):
        self.configure(allowed_prefixes=[{"prefix": "src/", "exts": [".py"]}],
                       prefix_ceiling=[{"prefix": "src/", "exts": [".py"]}])
        task = self.prepare()
        self.configure(allowed_prefixes=[])
        self.assertEqual(self.submit(task)["task_state"], "succeeded")
        task = self.prepare()
        doc = read_json(self.root / task["manifest_path"])[0]
        doc["active_prefixes"] = [{"prefix": "elsewhere/", "exts": [".py"]}]
        (self.root / task["manifest_path"]).write_text(json.dumps(doc), encoding="utf-8")
        self.submit(task)
        self.assertEqual(read_json(self.proof(task))[0]["validation_errors"], ["active_prefixes_outside_ceiling"])

    def start_execution(self):
        task = self.prepare()
        execution = facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])
        self.assertIsNone(execution["error"], execution)
        (self.base / "execution_switch.json").write_text('{"enabled":true}', encoding="utf-8")
        self.assertTrue(facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])["accepted"])
        return task, execution

    def test_claim_accepts_local_proof_and_execution_verifies(self):
        task, execution = self.start_execution()
        self.assertTrue(facade.claim_for(WORKSPACE_ID, execution["execution_id"])["ok"])
        source = self.root / "src/counter.py"
        source.write_text(source.read_text(encoding="utf-8") + "\n# Fictional local execution.\n", encoding="utf-8")
        path = self.root / ".bridge/results_execute" / (execution["execution_id"] + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        proof = execution_result(execution["execution_id"], task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        path.write_text(json.dumps(proof), encoding="utf-8")
        self.assertEqual(facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])["execution"]["status"], "succeeded")

    def test_claim_rejects_disabled_or_changed_local_policy(self):
        task, execution = self.start_execution()
        ws = load_registry(self.registry)[WORKSPACE_ID]
        for fields in ({"local_validation": False}, {"policy_revision": 2},
                       {"forbidden_files": [*ws.forbidden_files, "private"]}):
            self.configure(**fields)
            result = facade.claim_for(WORKSPACE_ID, execution["execution_id"])
            self.assertEqual(result["error"], "local_validation_policy_mismatch", result)
            self.configure(local_validation=True, policy_revision=ws.policy_revision,
                           forbidden_files=list(ws.forbidden_files))
        self.assertTrue(facade.claim_for(WORKSPACE_ID, execution["execution_id"])["ok"])
        self.configure(local_validation=False)
        self.assertEqual(assess(load_registry(self.registry)[WORKSPACE_ID], task["task_id"])["error"], "local_validation_policy_mismatch")

    def test_claim_refuses_missing_or_tampered_local_fingerprint(self):
        task, execution = self.start_execution()
        proof = read_json(self.proof(task))[0]
        for value in (None, "a" * 64):
            changed = dict(proof)
            if value is None:
                changed.pop("policy_fingerprint")
            else:
                changed["policy_fingerprint"] = value
            self.proof(task).write_text(json.dumps(changed), encoding="utf-8")
            self.assertEqual(facade.claim_for(WORKSPACE_ID, execution["execution_id"])["error"],
                             "local_validation_policy_mismatch")

    def test_execution_result_refuses_disabled_local_policy(self):
        task, execution = self.start_execution()
        self.assertTrue(facade.claim_for(WORKSPACE_ID, execution["execution_id"])["ok"])
        path = self.root / ".bridge/results_execute" / (execution["execution_id"] + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        proof = execution_result(execution["execution_id"], task["task_id"], task["manifest_sha256"], [])
        proof["code_modified"] = False
        path.write_text(json.dumps(proof), encoding="utf-8")
        self.configure(local_validation=False)
        result = facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])
        self.assertEqual(result["execution"]["error"], "local_validation_policy_mismatch", result)

    def test_orphan_and_unknown_submissions_are_not_automatically_replaced(self):
        task = self.prepare()
        write_proof(self.root, task["task_id"], task["manifest_sha256"], ["src/counter.py"])
        original = self.proof(task).read_bytes()
        self.assertEqual(facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])["error"], "validation_ledger_missing")
        self.assertEqual(original, self.proof(task).read_bytes())
        task = self.prepare()
        store = JsonLedger(self.root / ".bridge/tasks_v2")
        store.create(task["task_id"], {"task_id": task["task_id"], "task_state": "unknown_submission"})
        before = store.path_for(task["task_id"]).read_bytes()
        facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])
        self.assertEqual(store.path_for(task["task_id"]).read_bytes(), before)
        self.assertFalse(self.proof(task).exists())


class ConfigurationTest(unittest.TestCase):
    def test_load_registry_rejects_strict_opt_in(self):
        raw = json.dumps({"schema_version": 1, "workspaces": [entry(local_validation=True)]})
        with patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "stat", return_value=Mock(st_nlink=1, st_size=len(raw))), \
             patch.object(Path, "read_text", return_value=raw), \
             patch("grokbot_mcp.security.paths.is_reparse_point", return_value=False), \
             self.assertRaisesRegex(RegistryError, "local_validation_invalid"):
            load_registry(Path("D:/fictional/registry.json"))

    def test_cp1252_cli_diagnostic_output_without_pythonutf8(self):
        from grokbot_mcp.local_cli import emit
        from grokbot_mcp.diagnostics import CATALOG
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252")
        with patch.dict(os.environ):
            os.environ.pop("PYTHONUTF8", None)
            with patch("sys.stdout", stream):
                emit(CATALOG["local_validation_policy_mismatch"])
            stream.flush()
            self.assertIn(b"local_validation_policy_mismatch", buffer.getvalue())
        stream.detach()

    def test_field_types_and_strict_refusal(self):
        for value in (None, 0, 1, "true", [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(RegistryError, "local_validation_invalid"):
                _workspace(entry(git_gate="advisory", local_validation=value))
        with self.assertRaisesRegex(RegistryError, "local_validation_invalid"):
            _workspace(entry(local_validation=True))
        self.assertFalse(_workspace(entry()).local_validation)
        self.assertTrue(_workspace(entry(git_gate="advisory", local_validation=True)).local_validation)

    def test_fixed_fingerprint_and_routine_bytes(self):
        original = fixed_workspace()
        self.assertEqual(policy_fingerprint(original), "8c70e8c20ad7105804c41f6364228888927a5d0a76fbd8071366e42e0d9c6110")
        self.assertEqual(policy_fingerprint(original), policy_fingerprint(replace(original, local_validation=False)))
        enabled = replace(original, local_validation=True)
        self.assertNotEqual(policy_fingerprint(original), policy_fingerprint(enabled))
        expected = {"validate": "91c6d72f05f6c65a0282dcda05fb6321ce321f4dcb4b6fbec4fe30c80dc7a9cd",
                    "execute": "ec6bd97b5b0ff4f3a704d26e6b153c8ca0d2f20fcc904311b0711477b20abbfe"}
        with patch("grokbot_mcp.materials.__version__", "0.1.4b1"):
            for ws in (original, replace(original, local_validation=False)):
                for phase, digest in expected.items():
                    text = render_routine(phase, workspace_root=Path("D:/fictional/workspace"),
                        workspace_id="example-workspace", registry=Path("D:/fictional/registry.json"),
                        python=Path("D:/fictional/python.exe"), mode="reuse", test_options=routine_profile(ws))
                    self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), digest)

    def test_local_marker_requires_flag_and_exact_fingerprint_legacy_unchanged(self):
        ws = _workspace(entry(git_gate="advisory", local_validation=True))
        for fingerprint in (None, "", "a" * 64, True):
            self.assertEqual(local_validation_error(ws, {"validator": "bridge_local", "policy_fingerprint": fingerprint}),
                             "local_validation_policy_mismatch")
        proof = {"validator": "bridge_local", "policy_fingerprint": policy_fingerprint(ws)}
        self.assertIsNone(local_validation_error(ws, proof))
        self.assertEqual(local_validation_error(replace(ws, local_validation=False), proof), "local_validation_policy_mismatch")
        self.assertIsNone(local_validation_error(replace(ws, local_validation=False), {}))


class WaitTest(unittest.TestCase):
    def test_seven_server_tools_and_invalid_wait_type(self):
        from grokbot_mcp import server
        self.assertEqual(len(server.TOOL_NAMES), 7)
        for value in (True, "1", 1.5, -1, 51):
            result = asyncio.run(server.mcp.call_tool("grok_get_status", {
                "workspace_id": "fictional", "wait_seconds": value}))
            self.assertEqual(result.structured_content["error"], "invalid_argument")
        with patch.object(facade, "get_status", return_value={}) as status:
            server.grok_get_status("fictional", wait_seconds=50)
            self.assertEqual(status.call_args.kwargs["wait_seconds"], 50)
            server.grok_get_status("fictional")
            self.assertNotIn("wait_seconds", status.call_args.kwargs)

    def clock(self):
        self.now, self.sleeps = 0, []
        def sleep(seconds):
            self.sleeps.append(seconds)
            self.now += seconds
        return lambda: self.now, sleep

    def test_backoff_boundary_and_early_return(self):
        clock, sleep = self.clock()
        self.assertEqual(wait_for_status(lambda: [{"status": "pending"}], 18, clock=clock, sleep=sleep), 18000)
        self.assertEqual(self.sleeps, [1, 2, 4, 5, 5, 1])
        clock, sleep = self.clock()
        probe = Mock(side_effect=[[{"status": "pending"}], [{"status": "running"}], [{"status": "succeeded"}]])
        self.assertEqual(wait_for_status(probe, 50, clock=clock, sleep=sleep), 3000)
        self.assertEqual(self.sleeps, [1, 2])
        clock, sleep = self.clock()
        self.assertEqual(wait_for_status(lambda: [{"status": "running", "result_observation": "complete", "error": None}],
                                        50, clock=clock, sleep=sleep), 0)
        self.assertEqual(self.sleeps, [])

    def test_argument_bounds_and_default_bytes(self):
        for value in (-1, 51, True, False, None, "1", 1.0):
            self.assertEqual(facade.get_status("fictional", wait_seconds=value)["error"], "invalid_argument")
        ws = _workspace(entry())
        with patch.object(facade, "_workspace", return_value=(ws, None, [])), \
             patch.object(facade, "execution_switch_enabled", return_value=False), \
             patch.object(facade, "get_validation_status", return_value={"status": "pending"}):
            expected = b'{"workspace_id": "example-workspace", "webhook_sent": false, "execution_enabled": false, "qa_accepted": false, "validation": {"status": "pending"}}'
            for kwargs in ({}, {"wait_seconds": 0}):
                self.assertEqual(json.dumps(facade.get_status(WORKSPACE_ID, task_id="fictional", **kwargs)).encode("utf-8"), expected)

    def test_wait_is_readonly_then_one_normal_query_and_rerun(self):
        ws = _workspace(entry())
        for observe, rerun in ((False, False), (True, False), (False, True)):
            clock, sleep = self.clock()
            calls = []
            def status(*args, **kwargs):
                calls.append(kwargs["observe_only"])
                return {"status": "pending" if len(calls) == 1 else "succeeded"}
            with patch.object(facade, "_workspace", return_value=(ws, None, [])), \
                 patch.object(facade, "execution_switch_enabled", return_value=False), \
                 patch.object(facade, "get_execution_status", side_effect=status), \
                 patch("grokbot_mcp.status_wait.time.monotonic", side_effect=clock), \
                 patch("grokbot_mcp.status_wait.time.sleep", side_effect=sleep), \
                 patch("grokbot_mcp.bridge_tests.rerun", return_value={}) as run:
                result = facade.get_status(WORKSPACE_ID, execution_id="fictional", observe_only=observe,
                                           rerun_tests=rerun, wait_seconds=50)
                self.assertEqual(result["waited_ms"], 1000, result)
                self.assertEqual(calls, [True, True, observe])
                self.assertEqual(run.call_count, int(rerun))

    def test_wait_timeout_does_not_rerun_pending_execution(self):
        clock, sleep = self.clock()
        with patch.object(facade, "_workspace", return_value=(_workspace(entry()), None, [])), \
             patch.object(facade, "execution_switch_enabled", return_value=False), \
             patch.object(facade, "get_execution_status", return_value={"status": "pending"}), \
             patch("grokbot_mcp.status_wait.time.monotonic", side_effect=clock), \
             patch("grokbot_mcp.status_wait.time.sleep", side_effect=sleep), \
             patch("grokbot_mcp.bridge_tests.rerun") as run:
            result = facade.get_status(WORKSPACE_ID, execution_id="fictional", rerun_tests=True, wait_seconds=2)
            self.assertEqual(result["waited_ms"], 2000)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
