"""WP2: fictional local reruns, never real registries, credentials or transport."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from grokbot_mcp import bridge_tests, facade
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.diagnostics import CATALOG
from grokbot_mcp.materials import render_routine
from grokbot_mcp.registry import RegistryError, _workspace, load_registry, policy_fingerprint, routine_profile
from grokbot_mcp.rerun_config import RerunConfig, parse_rerun, resolve_cwd
from grokbot_mcp.result_contract import output_evidence
from grokbot_mcp.result_examples import examples
from grokbot_mcp.storage import JsonLedger
from grokbot_mcp.test_commands import changed_files_argv
from tests.test_u2a_contract import entry

ID = "00000000-0000-4000-8000-000000000099"
CONFIG = {"argv": [sys.executable, "-B", "-c", "print('fictional')"]}


class ConfigTest(unittest.TestCase):
    def load(self, config):
        item = entry()
        item["test_commands"][0]["rerun"] = config
        raw = json.dumps({"schema_version": 1, "workspaces": [item]})
        with patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "stat", return_value=SimpleNamespace(st_nlink=1, st_size=len(raw))), \
             patch.object(Path, "read_text", return_value=raw), \
             patch("grokbot_mcp.security.paths.is_reparse_point", return_value=False), \
             patch("grokbot_mcp.rerun_config.is_reparse_point", return_value=False), \
             patch.object(Path, "is_dir", return_value=True):
            return load_registry(Path.cwd() / "fictional-registry.json")["example-workspace"]

    def test_registry_rejects_each_invalid_field(self):
        cases = [(None, "rerun_invalid"), ([], "rerun_invalid"), ({"argv": CONFIG["argv"], "extra": 1}, "rerun_invalid")]
        for argv in (None, [], "python", [1], [""], ["python", "\n"], ["cmd.exe"], ["pwsh"],
                     ["powershell.exe"], ["bash"], ["tool.bat"], ["tool.cmd"], ["tool.ps1"],
                     ["{changed_files}"], ["p{y}"], ["python", "--x={changed_files}"],
                     ["python", "}"], ["python", "{changed_files}", "{changed_files}"]):
            cases.append(({"argv": argv}, "rerun_argv_invalid"))
        for cwd in (None, 1, "", ".", "../outside", "C:/outside", "/outside", "a/../../outside",
                    "a:stream", "dir/NUL", "dir/CON.txt", "a./b", "a /b", "a\x00b", "a?b", "a\nb"):
            cases.append(({**CONFIG, "cwd": cwd}, "rerun_cwd_invalid"))
        for cwd in ("private", "PRIVATE/sub", ".git", "a/.bridge"):
            cases.append(({**CONFIG, "cwd": cwd}, "rerun_cwd_forbidden"))
        for env in (None, [], {"lower": "x"}, {"1BAD": "x"}, {"A-B": "x"}, {"A": 1}, {"A": "\x00"}):
            cases.append(({**CONFIG, "env": env}, "rerun_env_invalid"))
        for timeout in (True, False, None, "1", 0, -1, 3601, 1.5):
            cases.append(({**CONFIG, "timeout_seconds": timeout}, "rerun_timeout_invalid"))
        for config, code in cases:
            with self.subTest(config=config), self.assertRaises(RegistryError) as caught:
                self.load(config)
            self.assertEqual(caught.exception.code, code)
            self.assertIn(code, CATALOG)

    def test_defaults_and_literal_env_and_normalized_cwd(self):
        command = self.load(CONFIG).test_commands[0]
        self.assertEqual(command.rerun.timeout_seconds, 600)
        self.assertIsNone(command.rerun.cwd)
        for timeout in (1, 1800):
            config = self.load({**CONFIG, "cwd": ".\\tests\\", "timeout_seconds": timeout,
                                "env": {"_A1": "<relative-path>", "B": "", "C": "中文"}}).test_commands[0].rerun
            self.assertEqual(config.cwd, "tests")
            self.assertEqual(dict(config.env), {"_A1": "<relative-path>", "B": "", "C": "中文"})
            self.assertEqual(config.timeout_seconds, timeout)

    def test_cwd_reparse_missing_and_containment_refuse(self):
        for code, patches in (
            ("rerun_cwd_reparse", [("grokbot_mcp.rerun_config.is_reparse_point", True)]),
            ("rerun_cwd_missing", [("grokbot_mcp.rerun_config.is_reparse_point", False), ("pathlib.Path.is_dir", False)]),
            ("rerun_cwd_invalid", [("grokbot_mcp.rerun_config.is_reparse_point", False), ("grokbot_mcp.rerun_config.contained", False)]),
        ):
            with self.subTest(code=code), ExitStack() as stack:
                for name, value in patches:
                    stack.enter_context(patch(name, return_value=value))
                with self.assertRaisesRegex(ValueError, code):
                    resolve_cwd(Path.cwd(), "tests", (), "private")
        with self.assertRaisesRegex(ValueError, "rerun_cwd_forbidden"):
            resolve_cwd(Path.cwd(), "secret/sub", (), "secret")

    def test_registry_cwd_io_refusals_are_named(self):
        item = entry()
        item["test_commands"][0]["rerun"] = CONFIG
        for code in ("rerun_cwd_reparse", "rerun_cwd_missing"):
            with patch("grokbot_mcp.rerun_config.resolve_cwd", side_effect=ValueError(code)), \
                 self.assertRaises(RegistryError) as caught:
                _workspace(item)
            self.assertEqual(caught.exception.code, code)

    def test_resolved_directory_alias_cannot_bypass_forbidden_policy(self):
        root = Path.cwd()
        resolved = root / "private"
        with patch("grokbot_mcp.rerun_config.is_reparse_point", return_value=False), \
             patch("grokbot_mcp.rerun_config.contained", return_value=True), \
             patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "resolve", side_effect=[resolved, root]), \
             self.assertRaisesRegex(ValueError, "rerun_cwd_forbidden"):
            resolve_cwd(root, "PRIVAT~1", ("private",), "private")

    def test_rerun_does_not_change_policy_or_routine_bytes(self):
        original = _workspace(entry())
        fingerprint = policy_fingerprint(original)
        profile = json.dumps(routine_profile(original), default=str, sort_keys=True).encode("utf-8")
        def render(workspace, phase):
            return render_routine(phase, workspace_root=workspace.root, workspace_id=workspace.workspace_id,
                                  registry=workspace.root / "fictional.json", python=Path(sys.executable),
                                  mode="reuse", test_options=routine_profile(workspace)).encode("utf-8")
        for config in (CONFIG, {**CONFIG, "argv": [sys.executable, "-c", "print('different')"],
                               "timeout_seconds": 1, "env": {"WP2": "fictional"}}):
            workspace = self.load(config)
            self.assertEqual(policy_fingerprint(workspace), fingerprint)
            self.assertEqual(json.dumps(routine_profile(workspace), default=str, sort_keys=True).encode("utf-8"), profile)
            for phase in ("validate", "execute"):
                self.assertEqual(render(workspace, phase), render(original, phase))

    def test_optional_placeholder_uses_existing_expansion_rules(self):
        config = parse_rerun({"argv": [sys.executable, "-B", "{changed_files}"]})
        paths = ["docs/中 文.md", "docs/a.md"]
        self.assertEqual(changed_files_argv(config, paths, paths), [sys.executable, "-B", *sorted(paths)])
        for paths in ([], ["-x"], ["@args"], ["a;evil.md"], ["../outside"], ["a:ads"], ["a%PATH%.md"]):
            with self.subTest(paths=paths), self.assertRaises(Exception):
                changed_files_argv(config, paths, paths)


class PreconditionsTest(unittest.TestCase):
    def setUp(self):
        self.workspace = _workspace(entry())
        self.parsed = copy.deepcopy(examples()["execute-succeeded"])
        self.parsed["execution_id"] = ID
        self.record = dict(terminal=True, execution_id=ID, manifest_task_id=self.parsed["manifest_task_id"],
                           manifest_sha256=self.parsed["manifest_sha256"], action="execute_manifest",
                           execution_result_path=f".bridge/results_execute/{ID}.json", result_sha256="a" * 64,
                           verified_at="fictional", execution_state="succeeded")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.read = self.stack.enter_context(patch.object(JsonLedger, "read", return_value=self.record))
        self.result = self.stack.enter_context(patch.object(bridge_tests, "read_result", return_value=(self.parsed, "a" * 64, "complete")))
        self.interpret = self.stack.enter_context(patch.object(bridge_tests, "_interpret", return_value={"status": "succeeded", "error": None, "changed_files": ["src/counter.py"]}))
        self.assess = self.stack.enter_context(patch.object(bridge_tests, "assess", return_value={"ok": True, "document": {"test_command_id": "python_unittest", "allowed_files": ["src/counter.py"]}}))

    def rejected(self, code, identity=ID):
        result = bridge_tests.rerun(self.workspace, identity)
        self.assertEqual(result["error"], code, result)
        self.assertFalse(result["executed"])
        self.assertFalse(result["ledger_modified"])

    def test_invalid_id(self):
        for identity in ("../escape", "", None):
            self.rejected("invalid_execution_id", identity)
        self.read.assert_not_called()

    def test_missing_and_archived_record(self):
        for error, expected in (("missing", "missing_or_unreadable_execution"), ("archived_task", "archived_task")):
            self.read.side_effect = ValueError(error)
            self.rejected(expected)

    def test_not_terminal_including_unknown_submission(self):
        self.record["terminal"] = False
        self.record["execution_state"] = "unknown_submission"
        self.rejected("execution_not_terminal")
        self.result.assert_not_called()

    def test_record_identity(self):
        for field in ("execution_id", "action", "execution_result_path", "workspace_id"):
            with self.subTest(field=field):
                modified = {**self.record, field: "wrong"}
                self.read.return_value = modified
                self.rejected("result_identity_mismatch")

    def test_missing_and_incomplete_result(self):
        for observation, code in (("missing", "result_missing"), ("writing", "result_unreadable")):
            self.result.return_value = None, None, observation
            self.rejected(code)

    def test_unverified_or_changed_result(self):
        for field in ("result_sha256", "verified_at"):
            self.read.return_value = {**self.record, field: None}
            self.rejected("rerun_result_unverified")
        self.read.return_value = self.record
        self.result.return_value = self.parsed, "b" * 64, "complete"
        self.rejected("result_changed")

    def test_result_identity_and_format_rejected(self):
        self.parsed["execution_id"] = "00000000-0000-4000-8000-000000000088"
        self.rejected("result_identity_mismatch")
        self.parsed["execution_id"] = ID
        self.parsed["tests"]["exit_code"] = True
        self.rejected("result_format_invalid")

    def test_current_settlement_refused_without_rewriting_frozen_decision(self):
        before = json.dumps(self.record)
        self.interpret.return_value = {"status": "scope_violation", "error": "scope_violation"}
        self.rejected("rerun_result_unverified")
        self.assertEqual(json.dumps(self.record), before)
        self.assertFalse(self.interpret.call_args.args[0].persist)

    def test_unsettled_result_and_assessment_failure(self):
        self.parsed["status"] = "running"
        self.rejected("rerun_result_unverified")
        self.parsed["status"] = "succeeded"
        self.assess.return_value = {"ok": False}
        self.rejected("rerun_result_unverified")

    def test_rerun_not_configured(self):
        self.rejected("rerun_not_configured")

    def test_inputs_expand_verified_files_and_recheck_directory(self):
        config = parse_rerun({"argv": [sys.executable, "-B", "{changed_files}"]})
        command = replace(self.workspace.test_commands[0], rerun=config)
        self.workspace = replace(self.workspace, test_commands=(command,))
        with patch.object(bridge_tests, "resolve_exact_files") as resolve, \
             patch.object(bridge_tests, "resolve_cwd", return_value=self.workspace.root) as cwd:
            inputs = bridge_tests._inputs(self.workspace, ID)
        self.assertEqual(inputs[4], [sys.executable, "-B", "src/counter.py"])
        resolve.assert_called_once_with(self.workspace.root, ["src/counter.py"], self.workspace.forbidden_files)
        cwd.assert_called_once_with(self.workspace.root, None, self.workspace.forbidden_files, self.workspace.protected_file)
        self.interpret.return_value["changed_files"] = ["outside.py"]
        self.rejected("test_file_outside_scope")

    def test_success_snapshot_drift_is_refused(self):
        self.record["verified_snapshot"] = {"file_hashes": {"src/counter.py": "old"}}
        def interpret(view, *_args):
            view.record["verified_snapshot"] = {"file_hashes": {"src/counter.py": "new"}}
            return {"status": "succeeded", "error": None}
        self.interpret.side_effect = interpret
        self.rejected("rerun_result_unverified")


class InterfaceTest(unittest.TestCase):
    def test_mcp_conflicts_and_seven_tools(self):
        from grokbot_mcp.server import TOOL_NAMES, mcp
        self.assertEqual(len(TOOL_NAMES), 7)
        for args in ({"rerun_tests": True}, {"rerun_tests": True, "execution_id": ID, "observe_only": True},
                     {"rerun_tests": "true", "execution_id": ID}):
            self.assertEqual(facade.get_status("example-workspace", **args)["error"], "invalid_argument")
            with patch.object(facade, "_workspace") as workspace:
                result = asyncio.run(mcp.call_tool("grok_get_status", {"workspace_id": "example-workspace", **args}))
            self.assertEqual(json.loads(result.content[0].text)["error"], "invalid_argument")
            workspace.assert_not_called()

    def test_default_response_matches_literal_legacy_bytes(self):
        workspace = _workspace(entry())
        with patch.object(facade, "_workspace", return_value=(workspace, None, [])), \
             patch.object(facade, "execution_switch_enabled", return_value=False), \
             patch.object(facade, "get_execution_status", return_value={"status": "succeeded"}) as status:
            actual = json.dumps(facade.get_status("example-workspace", execution_id=ID)).encode("utf-8")
            explicit = json.dumps(facade.get_status("example-workspace", execution_id=ID, rerun_tests=False)).encode("utf-8")
        expected = b'{"workspace_id": "example-workspace", "webhook_sent": false, "execution_enabled": false, "qa_accepted": false, "execution": {"status": "succeeded"}}'
        self.assertEqual(actual, expected)
        self.assertEqual(explicit, expected)
        status.assert_called_with(workspace, ID, observe_only=False, verify_current=False)

    def test_opt_in_uses_read_only_status_and_bounded_unicode_output(self):
        workspace = _workspace(entry())
        evidence = {"ok": True, **output_evidence(("中" * 10000).encode("utf-8"))}
        with patch.object(facade, "_workspace", return_value=(workspace, None, [])), \
             patch.object(facade, "execution_switch_enabled", return_value=False), \
             patch.object(facade, "get_execution_status", return_value={}) as status, \
             patch.object(bridge_tests, "rerun", return_value=evidence) as run:
            result = facade.get_status("example-workspace", execution_id=ID, rerun_tests=True)
        status.assert_called_once_with(workspace, ID, observe_only=True, verify_current=False)
        run.assert_called_once_with(workspace, ID)
        for key in ("head", "tail"):
            self.assertLessEqual(len(result["bridge_tests"]["output"][key].encode("utf-8")), 2048)
        self.assertTrue(result["bridge_tests"]["output"]["truncated"])
        self.assertEqual(result["bridge_tests"]["output_sha256"], evidence["output_sha256"])
        self.assertGreater(len(evidence["output"]["head"].encode("utf-8")), 2048)

    def test_cli_cp1252_no_pythonutf8(self):
        from grokbot_mcp import test_run_cli
        buffer = io.BytesIO()
        console = io.TextIOWrapper(buffer, encoding="cp1252")
        self.addCleanup(console.close)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(Path.cwd())}, clear=True), patch.object(sys, "stdout", console), \
             patch.object(test_run_cli, "load_registry", return_value={"example-workspace": object()}), \
             patch.object(test_run_cli, "rerun", return_value={"ok": True, **output_evidence("中文 ✓".encode("utf-8"))}):
            self.assertEqual(test_run_cli.main(["--registry", "fictional.json", "--workspace", "example-workspace", ID]), 0)
            console.flush()
            self.assertNotIn("PYTHONUTF8", os.environ)
            self.assertNotIn("GROKBOT_MCP_REGISTRY", os.environ)
        self.assertEqual(json.loads(buffer.getvalue().decode("cp1252"))["output"]["head"], "中文 ✓")


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.workspace = _workspace(entry())
        self.command = replace(self.workspace.test_commands[0], rerun=parse_rerun(CONFIG))
        self.raw = "fictional 中文\n".encode("utf-8")
        self.parsed = {"tests": {"exit_code": 0, **output_evidence(self.raw)}}
        self.record = {"qa_state": "not_reviewed"}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(bridge_tests, "_inputs", return_value=(self.record, self.parsed, "a" * 64, self.command, list(self.command.rerun.argv), self.workspace.root)))
        self.stack.enter_context(patch.object(bridge_tests, "safe_path"))
        self.stack.enter_context(patch.object(JsonLedger, "read", return_value=self.record))
        self.read_result = self.stack.enter_context(patch.object(bridge_tests, "read_json", return_value=(self.parsed, "a" * 64)))
        self.append = self.stack.enter_context(patch.object(bridge_tests, "_append", side_effect=lambda _d, _id, evidence: evidence))
        self.run = self.stack.enter_context(patch("grokbot_mcp.rerun_process.run", side_effect=lambda *a, **kw: {
            "raw": self.raw, "exit_code": 0, "timed_out": False, "executed": True}))

    def test_output_hash_count_and_both_agreement_matches(self):
        result = bridge_tests.rerun(self.workspace, ID)
        self.assertTrue(result["ok"])
        self.assertEqual(result["agreement"], {"exit_code": "match", "output_sha256": "match"})
        self.assertEqual(result["output_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(result["output_bytes"], len(self.raw))
        self.assertEqual(result["output"]["head"].encode("utf-8"), self.raw)
        self.assertGreaterEqual(result["duration_ms"], 0)
        self.assertFalse(result["ledger_modified"])
        self.assertFalse(result["qa_accepted"])

    def test_mismatch_and_unreported(self):
        self.parsed["tests"] = {"exit_code": 8, "output_sha256": "b" * 64}
        self.assertEqual(bridge_tests.rerun(self.workspace, ID)["agreement"],
                         {"exit_code": "mismatch", "output_sha256": "mismatch"})
        self.parsed["tests"] = {"not_run": True}
        self.assertEqual(bridge_tests.rerun(self.workspace, ID)["agreement"],
                         {"exit_code": "unreported", "output_sha256": "unreported"})

    def test_invalid_utf8_and_large_output(self):
        self.raw = b"start\xff" + b"x" * 20000 + b"\xfeend"
        result = bridge_tests.rerun(self.workspace, ID)
        replaced = self.raw.decode("utf-8", errors="replace").encode("utf-8")
        self.assertTrue(result["output_not_utf8"])
        self.assertEqual(result["raw_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertEqual(result["output_sha256"], hashlib.sha256(replaced).hexdigest())
        self.assertEqual(result["output_bytes"], len(replaced))
        self.assertTrue(result["output"]["truncated"])
        self.assertLessEqual(len(result["output"]["head"].encode("utf-8")), 8192)
        self.assertTrue(result["output"]["tail"].endswith("�end"))

    def test_inherits_env_then_overrides_without_placeholder_expansion(self):
        config = parse_rerun({**CONFIG, "env": {"WP2_VALUE": "<relative>", "WP2_NEW": "added"}})
        self.command = replace(self.command, rerun=config)
        with patch.object(bridge_tests, "_inputs", return_value=(self.record, self.parsed, "a" * 64,
                          self.command, list(config.argv), self.workspace.root)), \
             patch.dict(os.environ, {"WP2_VALUE": "old", "WP2_INHERITED": "inherited"}):
            bridge_tests.rerun(self.workspace, ID)
        env = self.run.call_args.kwargs["env"]
        self.assertEqual(env["WP2_VALUE"], "<relative>")
        self.assertEqual(env["WP2_INHERITED"], "inherited")
        self.assertEqual(env["WP2_NEW"], "added")

    def test_timeout_recorded_and_not_success(self):
        self.run.side_effect = None
        self.run.return_value = {"raw": b"partial", "exit_code": None, "timed_out": True,
                                 "executed": True, "error": "rerun_timeout"}
        result = bridge_tests.rerun(self.workspace, ID)
        self.assertFalse(result["ok"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["output"]["head"], "partial")
        self.append.assert_called_once()

    def test_changed_original_is_not_accepted(self):
        self.read_result.return_value = self.parsed, "b" * 64
        result = bridge_tests.rerun(self.workspace, ID)
        self.assertFalse(result["ok"])
        self.assertFalse(result["source_evidence_unchanged"])
        self.assertEqual(result["error"], "rerun_source_changed")

    def test_unsafe_receipt_path_refuses_before_launch(self):
        with patch.object(bridge_tests, "safe_path", side_effect=ValueError("linked_path_rejected")):
            result = bridge_tests.rerun(self.workspace, ID)
        self.assertEqual(result["error"], "linked_path_rejected")
        self.run.assert_not_called()


class NativeRerunTest(unittest.TestCase):
    """Real Windows filesystem/process checks, with a wholly fictional workflow."""
    def setUp(self):
        from tests import support
        from tests.test_git_gate_advisory import GitGateAdvisoryTest
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        original_git = support.git
        patched_git = patch.object(support, "git", side_effect=lambda root, *args: original_git(root, "-c", "safe.directory=*", *args))
        patched_git.start()
        self.addCleanup(patched_git.stop)
        GitGateAdvisoryTest.setUp(self)
        self.fixture = GitGateAdvisoryTest
        self.addCleanup(install_backend, None, None)

    def prepare(self):
        return self.fixture.prepare(self)

    def pending_claim(self):
        return self.fixture.pending_claim(self)

    def settled(self, script="print('fictional')", *, failed=0, output=b"fictional\r\n", not_run=False, **options):
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        data["workspaces"][0]["test_commands"][1]["rerun"] = {"argv": [sys.executable, "-B", "-c", script], **options}
        self.registry.write_text(json.dumps(data), encoding="utf-8")
        task, execution, _claim = self.fixture.start(self)
        (self.root / "src/counter.py").write_text("VALUE = 7\n", encoding="utf-8")
        self.fixture.write_result(self, task, execution, ["src/counter.py"], failed=failed)
        self.execution_id = str(execution["execution_id"])
        self.result_path = self.root / ".bridge/results_execute" / (self.execution_id + ".json")
        self.record_path = self.root / ".bridge/executions" / (self.execution_id + ".json")
        parsed = json.loads(self.result_path.read_text(encoding="utf-8"))
        if not_run:
            parsed["status"] = "failed"
            parsed["tests"] = {"command_id": "python_unittest", "not_run": True}
        elif output is not None:
            parsed["tests"].update(output_evidence(output))
        self.result_path.write_text(json.dumps(parsed), encoding="utf-8")
        status = facade.get_status("example-workspace", execution_id=self.execution_id)["execution"]
        self.assertIn(status["status"], {"succeeded", "failed", "execution_failed"}, status)
        self.workspace = load_registry(self.registry)["example-workspace"]
        return self.result_path.read_bytes(), self.record_path.read_bytes()

    def test_real_run_append_only_and_frozen_bytes(self):
        original_result, original_record = self.settled()
        one = bridge_tests.rerun(self.workspace, self.execution_id)
        self.assertTrue(one["ok"], one)
        first_path = self.root / one["evidence_path"]
        first_bytes = first_path.read_bytes()
        two = bridge_tests.rerun(self.workspace, self.execution_id)
        self.assertTrue(two["ok"], two)
        self.assertEqual((one["run_number"], two["run_number"]), (1, 2))
        self.assertEqual(first_path.read_bytes(), first_bytes)
        self.assertFalse(first_bytes.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(one["agreement"], {"exit_code": "match", "output_sha256": "match"})
        self.assertEqual(self.result_path.read_bytes(), original_result)
        self.assertEqual(self.record_path.read_bytes(), original_record)
        self.assertFalse(list(first_path.parent.glob("*.tmp")))

    def test_real_failed_test_can_compare_mismatches_without_changing_qa(self):
        original_result, original_record = self.settled(failed=1, output=b"different")
        result = bridge_tests.rerun(self.workspace, self.execution_id)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["agreement"], {"exit_code": "mismatch", "output_sha256": "mismatch"})
        self.assertEqual(self.result_path.read_bytes(), original_result)
        self.assertEqual(self.record_path.read_bytes(), original_record)

    def test_real_not_run_is_unreported(self):
        self.settled(not_run=True)
        result = bridge_tests.rerun(self.workspace, self.execution_id)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["agreement"], {"exit_code": "unreported", "output_sha256": "unreported"})

    def test_real_non_utf8(self):
        self.settled("import sys; sys.stdout.buffer.write(bytes([255, 254]))", output=None)
        result = bridge_tests.rerun(self.workspace, self.execution_id)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["output_not_utf8"])
        self.assertEqual(result["raw_sha256"], hashlib.sha256(b"\xff\xfe").hexdigest())

    def test_real_timeout_with_child_process_tree(self):
        script = ("import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(60)']); "
                  "print(p.pid,flush=True); time.sleep(60)")
        self.settled(script, timeout_seconds=1, output=None)
        result = bridge_tests.rerun(self.workspace, self.execution_id)
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["error"], "rerun_timeout")
        child = int(result["output"]["head"].strip())
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        handle = kernel.OpenProcess(0x1000, False, child)
        if handle:
            try:
                code = ctypes.c_ulong()
                import time
                for attempt in range(6):
                    self.assertTrue(kernel.GetExitCodeProcess(handle, ctypes.byref(code)))
                    if code.value != 259 or attempt == 5:
                        break
                    time.sleep(0.2)
                self.assertNotEqual(code.value, 259)
            finally:
                kernel.CloseHandle(handle)

    def test_real_cli_cp1252_and_mcp_preserve_source(self):
        original_result, original_record = self.settled("import sys; sys.stdout.buffer.write('中文'.encode('utf-8'))", output=None)
        env = dict(os.environ)
        env.pop("PYTHONUTF8", None)
        env["PYTHONIOENCODING"] = "cp1252"
        proc = subprocess.run([sys.executable, "-B", "-m", "grokbot_mcp.test_run_cli", "--registry", str(self.registry),
                               "--workspace", "example-workspace", self.execution_id], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout.decode("cp1252"))
        self.assertEqual(result["output"]["head"], "中文")
        status = facade.get_status("example-workspace", execution_id=self.execution_id, rerun_tests=True)
        self.assertTrue(status["bridge_tests"]["ok"], status)
        self.assertEqual(self.result_path.read_bytes(), original_result)
        self.assertEqual(self.record_path.read_bytes(), original_record)

    def test_atomic_receipts_do_not_replace_on_concurrent_creation(self):
        from concurrent.futures import ThreadPoolExecutor
        directory = self.root / ".bridge/bridge_test_runs"
        with ThreadPoolExecutor(max_workers=4) as workers:
            receipts = list(workers.map(lambda _: bridge_tests._append(directory, ID, {"ok": True}), range(8)))
        self.assertEqual(sorted(r["run_number"] for r in receipts), list(range(1, 9)))
        for receipt in receipts:
            self.assertEqual(json.loads((self.root / receipt["evidence_path"]).read_text(encoding="utf-8")), receipt)
        self.assertFalse(list(directory.glob("*.tmp")))


class ProcessTest(unittest.TestCase):
    def test_native_argument_array_and_merged_binary_output(self):
        from grokbot_mcp import rerun_process
        process = Mock(pid=1234, returncode=7)
        process.communicate.return_value = b"fictional\xff", None
        with patch.object(rerun_process.subprocess, "Popen", return_value=process) as popen:
            result = rerun_process.run(CONFIG["argv"], cwd=Path.cwd(), env={"WP2": "test"}, timeout=3)
        self.assertEqual(result["exit_code"], 7)
        self.assertEqual(result["raw"], b"fictional\xff")
        self.assertFalse(result["timed_out"])
        self.assertEqual(popen.call_args.args[0], CONFIG["argv"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(popen.call_args.kwargs["cwd"], Path.cwd())
        self.assertEqual(popen.call_args.kwargs["env"], {"WP2": "test"})
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.STDOUT)
        process.communicate.assert_called_once_with(timeout=3)

    def test_timeout_taskkill_precedes_direct_kill_and_drains_output(self):
        from grokbot_mcp import rerun_process
        process = Mock(pid=1234, returncode=1)
        process.poll.return_value = None
        process.communicate.side_effect = [subprocess.TimeoutExpired(CONFIG["argv"], 1, output=b"partial"), (b"partial end", None)]
        order = []
        process.kill.side_effect = lambda: order.append("direct")
        def kill(*args, **kwargs):
            order.append("tree")
            return SimpleNamespace(returncode=0)
        with patch.object(rerun_process.subprocess, "Popen", return_value=process), \
             patch.object(rerun_process.subprocess, "run", side_effect=kill) as taskkill:
            result = rerun_process.run(CONFIG["argv"], cwd=Path.cwd(), env={}, timeout=1)
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["error"], "rerun_timeout")
        self.assertEqual(result["raw"], b"partial end")
        self.assertEqual(order, ["tree", "direct"])
        self.assertEqual(taskkill.call_args.args[0][1:], ["/T", "/F", "/PID", "1234"])
        self.assertFalse(taskkill.call_args.kwargs["shell"])

    def test_start_failure_hides_exception_text(self):
        from grokbot_mcp import rerun_process
        with patch.object(rerun_process.subprocess, "Popen", side_effect=OSError("private-marker")):
            result = rerun_process.run(CONFIG["argv"], cwd=Path.cwd(), env={}, timeout=1)
        self.assertEqual(result["error"], "rerun_start_failed")
        self.assertFalse(result["executed"])
        self.assertNotIn("private-marker", str(result))

    def test_cleanup_failure_is_explicit_and_drain_is_bounded(self):
        from grokbot_mcp import rerun_process
        process = Mock(pid=1234, returncode=1)
        process.poll.return_value = 1
        process.communicate.side_effect = subprocess.TimeoutExpired(CONFIG["argv"], 1, output=b"partial")
        with patch.object(rerun_process.subprocess, "Popen", return_value=process), \
             patch.object(rerun_process.subprocess, "run", side_effect=OSError("private-marker")):
            result = rerun_process.run(CONFIG["argv"], cwd=Path.cwd(), env={}, timeout=1)
        self.assertEqual(result["error"], "rerun_tree_cleanup_failed")
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["raw"], b"partial")
        process.stdout.close.assert_not_called()
        self.assertEqual(process.communicate.call_count, 2)
        self.assertNotIn("private-marker", str(result))


if __name__ == "__main__":
    unittest.main()
