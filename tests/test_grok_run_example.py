"""Offline client orchestration contracts; no real registry or Routine access."""
from __future__ import annotations

import asyncio
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from scripts import grok_run_example as example
from tests.support import temporary_directory


TASK = "00000000-0000-4000-8000-000000000001"
EXECUTION = "00000000-0000-4000-8000-000000000002"
SPEC = dict(task_type="code_change", task_description="Improve the fictional counter.",
            allowed_files=["src/counter.py"], acceptance_criteria=["Targeted tests pass."],
            test_command_id="unit")


def status(state="succeeded", **extra):
    return {"qa_accepted": False, "execution": {
        "execution_id": EXECUTION, "manifest_task_id": TASK, "status": state,
        "tests": {"command_id": "unit"}, **extra}}


def test_receipt(**extra):
    return {"ok": True, "executed": True, "execution_id": EXECUTION,
            "source_evidence_unchanged": True, "command_id": "unit", "exit_code": 0,
            "agreement": {"exit_code": "match", "output_sha256": "unreported"}, **extra}


class FakeClient:
    def __init__(self, *, local=True):
        self.calls = []
        self.responses = {
            "grok_list_workspaces": [{"ok": True, "workspaces": [{"workspace_id": "example-workspace"}]}],
            "grok_preflight": [{"local_ready": True, "qa_accepted": False}],
            "grok_prepare_task": [{"task_id": TASK}],
            "grok_prepare_execution": [{"manifest_task_id": TASK, "execution_id": EXECUTION,
                                        "approval_state": "granted"}],
            "grok_submit_execution": [{"accepted": True, "submission_state": "accepted"}],
            "grok_submit_validation": [{"accepted": True, "task_state": "accepted"}],
            "grok_get_status": [status(), {**status(), "bridge_tests": test_receipt()}],
        }
        if not local:
            self.responses["grok_prepare_execution"].insert(0, {"error": "validation_missing"})
            self.responses["grok_get_status"].insert(0, {
                "qa_accepted": False, "validation": {"task_id": TASK, "status": "succeeded", "validation_passed": True}})

    async def call(self, name, arguments):
        self.calls.append((name, copy.deepcopy(arguments)))
        values = self.responses[name]
        value = values.pop(0) if len(values) > 1 else values[0]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def count(self, name):
        return sum(tool == name for tool, _ in self.calls)


class ExampleTest(unittest.TestCase):
    def run_fake(self, client=None, **kwargs):
        self.client = client or FakeClient()
        self.runner = example.Runner(self.client, "example-workspace")
        return asyncio.run(self.runner.run(SPEC, **kwargs))

    def test_pass_skips_validate_and_reruns_registered_test(self):
        result = self.run_fake()
        self.assertEqual((result["status"], result["exit_code"]), ("PASS", 0))
        self.assertEqual(self.client.count("grok_submit_validation"), 0)
        self.assertEqual(self.client.count("grok_submit_execution"), 1)
        self.assertEqual(self.client.calls[-1][1]["rerun_tests"], True)
        self.assertFalse(result["qa_accepted"])
        self.assertIn("独立验收由调用方负责", result["qa_note"])
        self.assertEqual((result["task_id"], result["execution_id"]), (TASK, EXECUTION))

    def test_remote_validation_waits_before_execution(self):
        client = FakeClient(local=False)
        self.assertEqual(self.run_fake(client)["status"], "PASS")
        names = [name for name, _ in client.calls]
        self.assertEqual(len(set(names)), 7)
        self.assertLess(names.index("grok_submit_validation"), names.index("grok_get_status"))
        self.assertLess(names.index("grok_get_status"), names.index("grok_submit_execution"))

    def test_failed_local_validation_never_falls_back_to_remote(self):
        client = FakeClient()
        client.responses["grok_prepare_execution"] = [{"error": "validation_rejected"}]
        self.assertEqual(self.run_fake(client)["status"], "BLOCKED")
        self.assertEqual(client.count("grok_submit_validation"), 0)
        self.assertEqual(client.count("grok_submit_execution"), 0)

    def test_pending_local_test_has_distinct_status(self):
        client = FakeClient()
        client.responses["grok_get_status"][-1]["bridge_tests"] = {
            "ok": False, "error": "rerun_not_configured", "executed": False}
        result = self.run_fake(client)
        self.assertEqual((result["status"], result["exit_code"]), ("PASS_PENDING_LOCAL_TEST", 2))

    def test_preflight_blocks_before_prepare(self):
        client = FakeClient()
        client.responses["grok_preflight"] = [{"local_ready": False}]
        self.assertEqual(self.run_fake(client)["status"], "BLOCKED")
        self.assertEqual(client.count("grok_prepare_task"), 0)

    def test_missing_registry_blocks(self):
        client = FakeClient()
        client.responses["grok_list_workspaces"] = [{"ok": False, "workspaces": None}]
        self.assertEqual(self.run_fake(client)["status"], "BLOCKED")

    def test_submit_unknown_never_resends(self):
        for value in (TimeoutError(), OSError("sensitive details"), None, {},
                      {"accepted": False, "submission_state": "unknown_submission"},
                      {"accepted": True}):
            with self.subTest(value=type(value).__name__):
                client = FakeClient()
                client.responses["grok_submit_execution"] = [value]
                result = self.run_fake(client)
                self.assertEqual((result["status"], result["exit_code"]), ("SUBMIT_UNKNOWN", 2))
                self.assertEqual(client.count("grok_submit_execution"), 1)
                self.assertEqual(client.count("grok_get_status"), 0)
                self.assertIn("status --workspace", result["next_step"])
                self.assertIn(EXECUTION, result["next_step"])
                self.assertNotIn("sensitive details", json.dumps(result))

    def test_validation_unknown_never_executes_and_retains_task_query(self):
        client = FakeClient(local=False)
        client.responses["grok_submit_validation"] = [TimeoutError()]
        result = self.run_fake(client)
        self.assertEqual(result["status"], "SUBMIT_UNKNOWN")
        self.assertEqual(client.count("grok_submit_validation"), 1)
        self.assertEqual(client.count("grok_submit_execution"), 0)
        self.assertIn("--task-id " + TASK, result["next_step"])

    def test_status_unknown_does_not_resubmit(self):
        for value in (OSError(), TimeoutError(), None, {}, {"error": "unreadable"},
                      status(execution_id=TASK), status(manifest_task_id=EXECUTION), status("novel_state")):
            with self.subTest(value=type(value).__name__):
                client = FakeClient()
                client.responses["grok_get_status"] = [value]
                self.assertEqual(self.run_fake(client)["status"], "STATUS_UNKNOWN")
                self.assertEqual(client.count("grok_submit_execution"), 1)

    def test_wait_budget_and_backoff(self):
        now = [0.0]
        async def sleep(seconds):
            now[0] += seconds
        client = FakeClient()
        client.responses["grok_get_status"] = [status("running")]
        runner = example.Runner(client, "example-workspace", clock=lambda: now[0], sleep=sleep)
        result = asyncio.run(runner.run(SPEC, wait_budget=2.5))
        self.assertEqual(result["status"], "TIMEOUT_PENDING")
        self.assertEqual(now[0], 2.5)
        self.assertEqual(client.count("grok_get_status"), 3)
        for name, args in client.calls:
            if name == "grok_get_status":
                self.assertTrue(0 <= args["wait_seconds"] <= 50)
        self.assertEqual(self.run_fake(wait_budget=0)["status"], "TIMEOUT_PENDING")

    def test_actual_wait_timeout(self):
        class SlowClient(FakeClient):
            async def call(self, name, arguments):
                if name == "grok_get_status":
                    await asyncio.Event().wait()
                return await super().call(name, arguments)
        # Keep the logical deadline in the future even after the real timer fires.
        runner = example.Runner(SlowClient(), "example-workspace", clock=lambda: 0.0)
        result = asyncio.run(runner.run(SPEC, wait_budget=0.01))
        self.assertEqual((result["status"], result["reason"], result["exit_code"]),
                         ("TIMEOUT_PENDING", "wait_budget_exhausted", 2))

    def test_wait_timeout_before_deadline(self):
        now = [0.0]
        client = FakeClient()
        runner = example.Runner(client, "example-workspace", clock=lambda: now[0])

        async def early_wait_for(task, timeout):
            self.assertEqual(timeout, 1.0)
            now[0] = 0.995
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            raise TimeoutError()

        with patch.object(example.asyncio, "wait_for", side_effect=early_wait_for) as wait_for:
            result = asyncio.run(runner.run(execution_id=EXECUTION, wait_budget=1.0))
        wait_for.assert_called_once()
        self.assertLess(now[0], 1.0)
        self.assertEqual((result["status"], result["reason"], result["exit_code"]),
                         ("TIMEOUT_PENDING", "wait_budget_exhausted", 2))
        self.assertEqual(client.count("grok_submit_execution"), 0)
        self.assertIn(EXECUTION, result["next_step"])

    def test_client_timeout_before_wait_budget(self):
        client = FakeClient()
        client.responses["grok_get_status"] = [TimeoutError()]
        runner = example.Runner(client, "example-workspace", clock=lambda: 0.0)
        result = asyncio.run(runner.run(SPEC, wait_budget=900))
        self.assertEqual((result["status"], result["reason"], result["exit_code"]),
                         ("STATUS_UNKNOWN", "operation_unconfirmed", 2))
        self.assertEqual(client.count("grok_get_status"), 1)
        self.assertEqual(client.count("grok_submit_execution"), 1)

    def test_bridge_timeout_and_unknown_are_unresolved(self):
        for state, expected in (("timeout", "TIMEOUT_PENDING"), ("unknown_submission", "SUBMIT_UNKNOWN")):
            client = FakeClient()
            client.responses["grok_get_status"] = [status(state, error=state)]
            self.assertEqual(self.run_fake(client)["status"], expected)

    def test_switch_off_is_blocked_and_no_switch_call_exists(self):
        client = FakeClient()
        client.responses["grok_submit_execution"] = [{"accepted": False, "webhook_attempted": False,
                                                    "error": "execution_switch_disabled"}]
        self.assertEqual(self.run_fake(client)["status"], "BLOCKED")
        self.assertEqual(client.count("grok_submit_execution"), 1)
        self.assertFalse(any("switch" in name for name, _ in client.calls))

    def test_dry_run_prepares_without_submission_or_grant(self):
        result = self.run_fake(dry_run=True)
        self.assertEqual(result["reason"], "dry_run_prepared_no_submission")
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(self.client.count("grok_prepare_task"), 1)
        self.assertEqual(self.client.count("grok_prepare_execution"), 0)
        self.assertFalse(any("submit" in name for name, _ in self.client.calls))

    def test_agreement_mismatch_at_either_terminal_step(self):
        for at_rerun in (False, True):
            client = FakeClient()
            if at_rerun:
                client.responses["grok_get_status"][-1]["bridge_tests"]["agreement"]["exit_code"] = "mismatch"
            else:
                client.responses["grok_get_status"][0]["execution"]["agreement"] = "mismatch"
            self.assertEqual(self.run_fake(client)["status"], "AGREEMENT_MISMATCH")

    def test_failed_and_scope_states(self):
        for state, expected in (("failed", "FAILED"), ("identity_mismatch", "FAILED"),
                                ("scope_violation", "OUT_OF_SCOPE")):
            client = FakeClient()
            client.responses["grok_get_status"] = [status(state)]
            result = self.run_fake(client)
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["exit_code"], 3 if expected == "FAILED" else 2)
        client = FakeClient()
        client.responses["grok_get_status"] = [status(out_of_scope_changes=["other.py"])]
        self.assertEqual(self.run_fake(client)["status"], "OUT_OF_SCOPE")

    def test_local_test_failure_and_untrusted_receipt(self):
        for extra, expected in (({"exit_code": 1}, "FAILED"), ({"exit_code": True}, "STATUS_UNKNOWN"),
                                ({"source_evidence_unchanged": False}, "STATUS_UNKNOWN"),
                                ({"command_id": "other"}, "STATUS_UNKNOWN")):
            client = FakeClient()
            client.responses["grok_get_status"][-1]["bridge_tests"].update(extra)
            self.assertEqual(self.run_fake(client)["status"], expected)

    def test_rerun_transport_failure_is_status_unknown(self):
        client = FakeClient()
        client.responses["grok_get_status"][-1] = OSError()
        self.assertEqual(self.run_fake(client)["status"], "STATUS_UNKNOWN")
        self.assertEqual(client.count("grok_submit_execution"), 1)

    def test_supplemental_bridge_evidence_can_pass(self):
        client = FakeClient()
        client.responses["grok_get_status"][0] = status("test_evidence_missing", error="test_evidence_missing")
        client.responses["grok_get_status"][-1]["execution"]["status"] = "succeeded_bridge_evidence"
        self.assertEqual(self.run_fake(client)["status"], "PASS")

    def test_validation_failure_never_submits_execution(self):
        client = FakeClient(local=False)
        client.responses["grok_get_status"][0]["validation"].update(status="failed", validation_passed=False)
        self.assertEqual(self.run_fake(client)["status"], "FAILED")
        self.assertEqual(client.count("grok_submit_execution"), 0)
        self.assertEqual(client.count("grok_prepare_execution"), 1)

    def test_status_command_only_observes_and_reruns(self):
        result = self.run_fake(execution_id=EXECUTION)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(name == "grok_get_status" for name, _ in self.client.calls))

    def test_validation_status_cannot_start_execution(self):
        client = FakeClient(local=False)
        result = self.run_fake(client, task_id=TASK)
        self.assertEqual(result["reason"], "validation_settled_manual_continuation_required")
        self.assertEqual(client.count("grok_submit_execution"), 0)

    def test_qa_is_passed_through_not_inferred(self):
        for value in (False, None, True):
            client = FakeClient()
            for reply in client.responses["grok_get_status"]:
                reply["qa_accepted"] = value
            self.assertIs(self.run_fake(client)["qa_accepted"], value)
            self.assertFalse(any("qa_accepted" in args for _, args in client.calls))

    def test_evidence_omits_untrusted_text_and_credentials(self):
        secret = "sk_" + "Z" * 24
        bearer = "Bearer " + "Q" * 24
        client = FakeClient()
        for replies in client.responses.values():
            for reply in replies:
                reply.update(message=secret, credential_targets={"execute": bearer},
                             output={"head": secret}, unknown={secret: bearer})
        client.responses["grok_get_status"][-1]["bridge_tests"]["error"] = secret
        with temporary_directory() as directory:
            runner = example.Runner(client, "example-workspace", Path(directory))
            result = asyncio.run(runner.run(SPEC))
            files = list(Path(directory).glob("*.json"))
            self.assertEqual(len(files), len(client.calls))
            evidence = "".join(file.read_text(encoding="utf-8") for file in files) + json.dumps(result)
            self.assertNotIn(secret, evidence)
            self.assertNotIn(bearer, evidence)
            self.assertNotIn("credential_targets", evidence)

    def test_evidence_failure_after_send_is_unknown_without_resend(self):
        client = FakeClient()
        runner = example.Runner(client, "example-workspace")
        original = runner.call
        async def call(name, **args):
            result = await original(name, **args)
            if name == "grok_submit_execution":
                raise OSError("disk full")
            return result
        runner.call = call
        self.assertEqual(asyncio.run(runner.run(SPEC))["status"], "SUBMIT_UNKNOWN")
        self.assertEqual(client.count("grok_submit_execution"), 1)

    def test_reject_command_model_absolute_paths_and_bad_budget(self):
        for extra in ({"model": "fictional"}, {"shell": "echo example"}, {"argv": ["cmd"]},
                      {"allowed_files": ["C:/fictional/file.py"]}, {"create_files": ["../outside.py"]},
                      {"allowed_globs": ["/outside/*"]}, {"execution_timeout": True}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                example.task_spec({**SPEC, **extra})
        for budget in (-1, float("nan"), float("inf"), True):
            self.assertEqual(self.run_fake(wait_budget=budget)["status"], "BLOCKED")
            self.assertEqual(self.client.calls, [])

    def test_sdk_adapter_text_and_structured_results(self):
        for structured in (None, {"ok": True}):
            result = SimpleNamespace(structured_content=structured, is_error=False,
                                     content=[SimpleNamespace(text='{"ok":true}')])
            session = SimpleNamespace(call_tool=AsyncMock(return_value=result))
            self.assertEqual(asyncio.run(example.StdioClient(session).call("grok_list_workspaces", {})), {"ok": True})
            self.assertIsInstance(session.call_tool.call_args.kwargs["read_timeout_seconds"], (float, int))
        result.is_error = True
        with self.assertRaises(ValueError):
            asyncio.run(example.StdioClient(session).call("grok_submit_execution", {}))

    def test_native_stdio_with_owned_missing_registry(self):
        # Explicit missing registry in an owned temporary directory: the server
        # cannot resolve a workspace or reach any credential/transport path.
        with temporary_directory() as directory:
            async def probe():
                async with example.connect(sys.executable, str(Path(directory) / "absent.json")) as client:
                    return await client.call("grok_list_workspaces", {})
            result = asyncio.run(probe())
        self.assertIs(result["ok"], False)
        self.assertEqual(result["error"], "registry_missing")
        self.assertIsNone(result["workspaces"])

    def test_setup_rejects_non_python_executable_before_start(self):
        args = SimpleNamespace(command="run", python="cmd.exe", workspace="example-workspace",
                               evidence_dir=None, wait_budget=900, registry=None)
        with patch.object(example, "connect") as connect:
            result = asyncio.run(example.execute(args))
        self.assertEqual(result["status"], "BLOCKED")
        connect.assert_not_called()

    def test_help_without_server_or_registry(self):
        script = Path(example.__file__)
        for args in (["--help"], ["run", "--help"], ["status", "--help"]):
            completed = subprocess.run([sys.executable, "-B", str(script), *args],
                                       capture_output=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn(b"usage:", completed.stdout)

    def test_utf8_single_json_stdout_and_stderr_hint(self):
        out_bytes, err_bytes = io.BytesIO(), io.BytesIO()
        out = io.TextIOWrapper(out_bytes, encoding="cp936")
        err = io.TextIOWrapper(err_bytes, encoding="cp936")
        result = self.run_fake()
        with patch.object(example, "execute", new=AsyncMock(return_value=result)), \
             patch.object(sys, "stdout", out), patch.object(sys, "stderr", err):
            code = example.main(["run", "--workspace", "example-workspace", "--task-json", "task.json"])
        out.flush()
        err.flush()
        self.assertEqual(code, 0)
        self.assertEqual(len(out_bytes.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(out_bytes.getvalue().decode("utf-8")), result)
        self.assertIn("独立验收由调用方负责", err_bytes.getvalue().decode("utf-8"))
        out.detach()
        err.detach()


if __name__ == "__main__":
    unittest.main()
