"""U2b memory-only regressions. No tempfile, credentials, Git writes or sends."""
from __future__ import annotations

import copy
import io
import json
import subprocess
import unittest
from contextlib import nullcontext, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import ledger_archive, admin_cli, diagnostics
from grokbot_mcp.local_safety import LocalError, digest, json_bytes
from grokbot_mcp.manifest.store import ManifestError, load_manifest, prepare_manifest
from grokbot_mcp.registry import _workspace, RegistryError, policy_fingerprint, workspace_profile
from grokbot_mcp.routine import confirmation
from grokbot_mcp.routine_check import inspect_text
from grokbot_mcp.materials import render_routine
from grokbot_mcp.security.globs import expand, parse_glob
from grokbot_mcp.security.targets import TargetRejected
from grokbot_mcp.storage import JsonLedger
from tests.test_u2a_contract import entry

TASK = "11111111-1111-4111-8111-111111111111"
EXEC = "22222222-2222-4222-8222-222222222222"


class GlobsTest(unittest.TestCase):
    def setUp(self):
        self.ws = replace(_workspace(entry()), allow_globs=True)

    def expand(self, names, patterns=None, **options):
        mode = options.pop("mode", "100644")
        listing = "".join(f"{mode} {'a' * 40} 0\t{name}\0" for name in names)
        with patch("grokbot_mcp.security.globs.run_git", return_value=subprocess.CompletedProcess([], 0, listing)), patch(
                "grokbot_mcp.security.globs._require_existing_file"):
            return expand(replace(self.ws, **options), patterns or ["src/**"])

    def test_grammar(self):
        self.assertEqual(parse_glob("src/**/*.py"), ("src/", ".py"))
        self.assertEqual(parse_glob("docs/sub/**"), ("docs/sub/", ""))
        for value in ("../src/**", "src/../x/**", "/src/**", "C:/src/**", "src/**/x/**",
                      "**", "src/*", "src/**/x.py", "src/**/*.p?", "src/**/[a]", "src//**",
                      "src\\**", "src./**", "src /**", "./src/**", "src/**/", "src/**/../a", None):
            with self.subTest(value=value), self.assertRaises(ManifestError):
                parse_glob(value)

    def test_default_off_before_git(self):
        with patch("grokbot_mcp.security.globs.run_git") as git, self.assertRaisesRegex(ManifestError, "disabled"):
            expand(replace(self.ws, allow_globs=False), ["src/**"])
        git.assert_not_called()

    def test_tracked_intersection_and_suffix(self):
        self.assertEqual(self.expand(["src/counter.py", "src/unregistered.py", "docs/guide.md"], ["src/**/*.py"]), ["src/counter.py"])

    def test_untracked_not_in_index_never_included(self):
        with self.assertRaisesRegex(ManifestError, "empty"):
            self.expand([])

    def test_forbidden_descendants_and_bridge_excluded(self):
        names = ["src/counter.py", "src/private/a.py", "src/.bridge/a.py", "src/protected/a.py"]
        self.assertEqual(self.expand(names, allowed_files=tuple(names), forbidden_files=("./SRC/private/",),
                                    protected_file="src/protected"), ["src/counter.py"])

    def test_symlink_and_submodule_modes_rejected(self):
        for mode in ("120000", "160000"):
            with self.assertRaisesRegex(ManifestError, "unsafe"):
                self.expand(["src/counter.py"], mode=mode)

    def test_reparse_and_hardlink_refused(self):
        listing = f"100644 {'a' * 40} 0\tsrc/counter.py\0"
        for reason in ("reparse", "target_hardlink_rejected", "target_case_mismatch"):
            with patch("grokbot_mcp.security.globs.run_git", return_value=subprocess.CompletedProcess([], 0, listing)), patch(
                    "grokbot_mcp.security.globs._require_existing_file", side_effect=TargetRejected(reason)), self.assertRaisesRegex(ManifestError, "unsafe"):
                expand(self.ws, ["src/**"])

    def test_limit_no_truncation_and_deduplication(self):
        self.assertEqual(self.expand(["src/counter.py", "src/counter.py"], glob_max_files=1), ["src/counter.py"])
        with self.assertRaisesRegex(ManifestError, "limit"):
            self.expand(["src/counter.py", "src/other.py"], allowed_files=("src/counter.py", "src/other.py"), glob_max_files=1)

    def test_registry_default_identity_and_validation(self):
        ws = _workspace(entry())
        explicit = _workspace(entry(allow_globs=False, glob_max_files=200))
        self.assertEqual(policy_fingerprint(ws), policy_fingerprint(explicit))
        self.assertEqual(workspace_profile(ws), workspace_profile(explicit))
        self.assertNotIn("allow_globs", workspace_profile(ws))
        self.assertNotEqual(policy_fingerprint(ws), policy_fingerprint(self.ws))
        for options in ({"allow_globs": 1}, {"glob_max_files": True}, {"glob_max_files": 0}, {"glob_max_files": 2001}):
            with self.assertRaises(RegistryError):
                _workspace(entry(**options))

    def test_prepare_records_explicit_expansion_and_confirmation_count(self):
        saved = {}
        with patch("grokbot_mcp.security.globs.expand", return_value=["src/counter.py"]), patch(
                "grokbot_mcp.manifest.store.write_json_new", side_effect=lambda _p, d: saved.update(d)), patch(
                "grokbot_mcp.manifest.store.read_json", side_effect=lambda _p: (saved, "b" * 64)):
            result = prepare_manifest(self.ws, task_type="code_change", task_description="Fictional",
                allowed_files=["docs/guide.md"], allowed_globs=["src/**"], acceptance_criteria=["Measured"],
                test_command_id="python_unittest", execution_timeout=None, baseline_commit=None)
        self.assertEqual(saved["allowed_files"], ["docs/guide.md", "src/counter.py"])
        self.assertEqual(result["scope_expansion"], saved["scope_expansion"])
        self.assertEqual(result["allowed_files"], saved["allowed_files"])
        self.assertIn("2", result["operator_message"])


class RoutineCheckTest(unittest.TestCase):
    def setUp(self):
        self.ws = _workspace(entry())
        self.registry = Path("D:/fictional/registry.json")
        self.python = Path("D:/fictional/python.exe")

    def packet(self):
        profile = workspace_profile(self.ws)
        profile["root"] = str(self.ws.root)
        for key in ("git_gate", "validate_credential_target", "execute_credential_target"):
            profile.pop(key)
        bindings = {p: confirmation(self.ws, p, self.registry, mode="create", revision=1,
                    verified_at="not-confirmed")["binding"] for p in ("validate", "execute")}
        text = "```json\n" + json.dumps(profile) + "\n```\n```json\n" + json.dumps(bindings) + "\n```\n"
        for phase in ("validate", "execute"):
            text += render_routine(phase, workspace_root=self.ws.root, workspace_id=self.ws.workspace_id,
                registry=self.registry, python=self.python, mode="create", test_options=profile,
                expected_fingerprint=policy_fingerprint(self.ws))
        return text

    def inspect(self, text):
        return inspect_text(text, self.ws, self.registry, self.python, "setup")

    def test_current_packet(self):
        self.assertEqual(self.inspect(self.packet()), [])

    def test_placeholder_without_echo(self):
        errors = self.inspect(self.packet() + "\n<ADMIN_SECRET_EXAMPLE> {{PYTHON_PS}}")
        self.assertIn("routine_placeholders_remaining", errors)
        self.assertNotIn("SECRET", str(errors))

    def test_binding_values_mismatch(self):
        for old in (self.ws.workspace_id, policy_fingerprint(self.ws), str(self.registry).replace("\\", "\\\\")):
            with self.subTest(old=old):
                self.assertTrue(self.inspect(self.packet().replace(old, "fictional-changed")))

    def test_python_and_test_command_mismatch(self):
        self.assertIn("routine_template_mismatch", self.inspect(self.packet().replace(str(self.python), "wrong-python")))
        self.assertIn("routine_profile_mismatch", self.inspect(self.packet().replace("python_unittest", "wrong_test")))

    def test_invalid_packet(self):
        self.assertIn("routine_packet_invalid", self.inspect("```json\n{}\n```"))

    def test_check_local_packet_and_stale_attestation_without_credentials(self):
        from grokbot_mcp.routine_check import check
        packet = self.registry.parent / "setup" / self.ws.workspace_id / "routine-setup.md"
        raw = self.packet().encode("utf-8")
        real_open, real_exists = Path.open, Path.exists
        readiness = {"binding": {"claim_interpreter": str(self.python)}, "binding_confirmed": True,
                     "binding_verified_at": "2026-01-01T00:00:00+00:00", "routine_enabled": True}
        with patch("grokbot_mcp.routine_check.load_registry", return_value={self.ws.workspace_id: self.ws}), patch(
                "grokbot_mcp.routine_check.routine_readiness", return_value=readiness), patch(
                "grokbot_mcp.routine_check.packets", return_value=[(packet, "setup")]), patch(
                "grokbot_mcp.routine_check.safe_path"), patch.object(
                Path, "open", lambda p, *a, **kw: io.BytesIO(raw) if p == packet else real_open(p, *a, **kw)), patch.object(
                Path, "exists", lambda p: True if p == packet else real_exists(p)), patch(
                "grokbot_mcp.credentials.backend.load_for", side_effect=AssertionError("credential access")):
            result = check(self.registry, self.ws.workspace_id)
            self.assertTrue(result["ok"])
            self.assertFalse(result["remote_binding_verified"])
            self.assertIn("只能验证本地包与登记值", result["scope"])
            readiness["binding_confirmed"] = False
            self.assertIn("routine_attestation_stale", check(self.registry, self.ws.workspace_id)["issues"])

    def test_diagnostic_message_safe_and_specific(self):
        report = diagnostics.public_report({"operator_message": "untrusted", "findings": [{"code": "routine_timeout_no_result"}]})
        self.assertIn("routine-check", report["operator_message"])
        self.assertNotIn("untrusted", json.dumps(report))
        self.assertNotIn("operator_message", diagnostics.public_report({"findings": [{"code": "result_timeout"}]}))


class ArchiveMemoryTest(unittest.TestCase):
    """Actual archive state machine with an in-memory filesystem/lock adapter."""
    def setUp(self):
        self.ws = replace(_workspace(entry()), root=Path("D:/fictional/workspace"))
        self.registry = Path("D:/fictional/config/registry.json")
        self.bridge = self.ws.root / ".bridge"
        self.files = {}
        self.dirs = set()
        self.put("task_specs/" + TASK + ".json", {"task_id": TASK, "workspace_id": self.ws.workspace_id})
        sha = digest(self.files[self.bridge / "task_specs" / (TASK + ".json")])
        self.put("tasks_v2/" + TASK + ".json", {"task_id": TASK, "manifest_sha256": sha, "task_state": "timeout"})
        self.put("executions/" + EXEC + ".json", {"execution_id": EXEC, "manifest_task_id": TASK,
                 "manifest_sha256": sha, "execution_state": "timeout"})
        self.put("results_v2/" + TASK + ".json", {"frozen": "original"})
        self.put("execution_claims/" + EXEC + ".json", {"frozen": "claim"})
        patches = [patch("grokbot_mcp.ledger_archive.load_registry", return_value={self.ws.workspace_id: self.ws}),
            patch("grokbot_mcp.ledger_archive.safe_path"),
            patch("grokbot_mcp.ledger_archive.read_document", side_effect=self.read),
            patch("grokbot_mcp.ledger_archive.create_new", side_effect=self.create),
            patch("grokbot_mcp.ledger_archive.os.rename", side_effect=self.move),
            patch("grokbot_mcp.ledger_archive.getpass.getuser", return_value="fictional-operator"),
            patch.object(JsonLedger, "locked", return_value=nullcontext()),
            patch.object(Path, "exists", lambda p: p in self.files or p in self.dirs),
            patch.object(Path, "is_file", lambda p: p in self.files),
            patch.object(Path, "mkdir", lambda p, **_kw: self.dirs.add(p)),
            patch.object(Path, "glob", lambda p, _pattern: iter([f for f in self.files if f.parent == p and f.suffix == ".json"]))]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def put(self, relative, value):
        path = self.bridge / relative
        self.files[path] = json_bytes(value)
        self.dirs.update(path.parents)

    def read(self, path):
        if path not in self.files:
            raise LocalError("file_missing")
        return json.loads(self.files[path]), self.files[path]

    def create(self, path, raw):
        if path in self.files:
            raise LocalError("backup_already_exists")
        self.files[path] = raw
        self.dirs.update(path.parents)

    def move(self, source, target):
        if target in self.files:
            raise FileExistsError
        self.files[target] = self.files.pop(source)
        self.dirs.update(target.parents)

    def archive(self, **kw):
        return ledger_archive.archive(self.registry, self.ws.workspace_id, TASK, reason="fictional recovery", **kw)

    def test_dry_run_never_writes_or_locks(self):
        before = copy.deepcopy(self.files)
        with patch.object(JsonLedger, "locked", side_effect=AssertionError("write lock")):
            report = self.archive()
        self.assertTrue(report["dry_run"])
        self.assertTrue(report["risk_acceptance_required"])
        self.assertEqual(self.files, before)

    def test_risk_refusal_preserves_bytes(self):
        before = copy.deepcopy(self.files)
        with self.assertRaisesRegex(LocalError, "late_result_risk"):
            self.archive(confirm=True)
        self.assertEqual(self.files, before)

    def test_move_audit_and_permanent_refusal(self):
        before = copy.deepcopy(self.files)
        report = self.archive(confirm=True, accept_late_result_risk=True)
        folder = Path(report["archive_path"])
        audit = self.read(folder / "audit.json")[0]
        self.assertEqual(audit["operator"], "fictional-operator")
        self.assertEqual(audit["reason"], "fictional recovery")
        for item in report["moves"]:
            self.assertEqual(self.files[folder / item["path"]], before[self.bridge / item["path"]])
            self.assertEqual(item["sha256"], digest(self.files[folder / item["path"]]))
            self.assertNotIn(self.bridge / item["path"], self.files)
        self.assertTrue(ledger_archive.archived(self.ws.root, TASK))
        self.assertTrue(ledger_archive.archived(self.ws.root, EXEC, execution=True))
        self.assertFalse(ledger_archive.incomplete(self.ws.root))
        for kind, identity in (("tasks_v2", TASK), ("executions", EXEC)):
            with self.assertRaisesRegex(ValueError, "archived_task"):
                JsonLedger(self.bridge / kind).create(identity, {})
        with self.assertRaisesRegex(ManifestError, "archived_task"):
            load_manifest(self.ws.root, TASK)
        for kind, identity in (("results_v2", TASK), ("execution_claims", EXEC)):
            path = self.bridge / kind / (identity + ".json")
            self.assertEqual(before[path], self.files[path])

    def test_late_result_status_refused_in_both_phases(self):
        self.archive(confirm=True, accept_late_result_risk=True)
        from grokbot_mcp.validation.status import observe_validation
        from grokbot_mcp.execution.status import observe_execution
        self.put("results_v2/" + TASK + ".json", {"status": "succeeded"})
        self.put("results_execute/" + EXEC + ".json", {"status": "succeeded"})
        self.assertEqual(observe_validation(self.ws, TASK, observe_only=True)["error"], "archived_task")
        self.assertEqual(observe_execution(self.ws, EXEC, observe_only=True)["error"], "archived_task")

    def test_interrupted_move_stays_blocking_and_resumes_original_plan(self):
        moves = []
        def crash(source, target):
            moves.append(source)
            if len(moves) == 2:
                raise OSError("fictional interruption")
            self.move(source, target)
        with patch("grokbot_mcp.ledger_archive.os.rename", side_effect=crash), self.assertRaises(OSError):
            self.archive(confirm=True, accept_late_result_risk=True)
        self.assertTrue(ledger_archive.incomplete(self.ws.root))
        with self.assertRaisesRegex(ValueError, "archive_incomplete"):
            JsonLedger(self.bridge / "tasks_v2").list_records()
        journal = self.bridge / "ledger_archives" / (TASK + ".json")
        original = self.files[journal]
        report = self.archive(confirm=True, accept_late_result_risk=True)
        self.assertTrue(report["archived"])
        self.assertFalse(ledger_archive.incomplete(self.ws.root))
        self.assertEqual(self.files[journal], original)

    def test_terminal_does_not_need_risk_flag(self):
        for kind, identity in (("tasks_v2", TASK), ("executions", EXEC)):
            path = self.bridge / kind / (identity + ".json")
            value = self.read(path)[0]
            value["terminal"] = True
            value["task_state" if kind == "tasks_v2" else "execution_state"] = "failed"
            self.put(kind + "/" + identity + ".json", value)
        self.assertTrue(self.archive(confirm=True)["archived"])

    def test_rearchive_does_not_overwrite_audit(self):
        self.archive(confirm=True, accept_late_result_risk=True)
        before = copy.deepcopy(self.files)
        with self.assertRaisesRegex(LocalError, "archived_task"):
            self.archive(confirm=True, accept_late_result_risk=True)
        self.assertEqual(self.files, before)

    def test_inconsistent_terminal_flag_still_requires_risk(self):
        for kind, identity in (("tasks_v2", TASK), ("executions", EXEC)):
            value = self.read(self.bridge / kind / (identity + ".json"))[0]
            value["terminal"] = True
            self.put(kind + "/" + identity + ".json", value)
        with self.assertRaisesRegex(LocalError, "late_result_risk"):
            self.archive(confirm=True)

    def test_cross_volume_refused_before_writes(self):
        before = copy.deepcopy(self.files)
        with self.assertRaisesRegex(LocalError, "cross_volume"):
            ledger_archive.archive(Path("C:/fictional/config/registry.json"), self.ws.workspace_id, TASK,
                                   reason="fictional", confirm=True, accept_late_result_risk=True)
        self.assertEqual(before, self.files)

    def test_recreated_task_uuid_cannot_be_prepared(self):
        self.archive(confirm=True, accept_late_result_risk=True)
        with patch("grokbot_mcp.manifest.store.uuid.uuid4", return_value=TASK), self.assertRaisesRegex(ManifestError, "archived_task"):
            prepare_manifest(self.ws, task_type="code_change", task_description="Fictional",
                allowed_files=["src/counter.py"], acceptance_criteria=["Measured"], test_command_id="python_unittest",
                execution_timeout=None, baseline_commit=None)

    def test_changed_source_after_interruption_cannot_resume(self):
        with patch("grokbot_mcp.ledger_archive.os.rename", side_effect=OSError), self.assertRaises(OSError):
            self.archive(confirm=True, accept_late_result_risk=True)
        journal = self.files[self.bridge / "ledger_archives" / (TASK + ".json")]
        self.put("tasks_v2/" + TASK + ".json", {"task_id": TASK, "tampered": True})
        with self.assertRaisesRegex(LocalError, "archive_evidence_changed"):
            self.archive(confirm=True, accept_late_result_risk=True)
        self.assertTrue(ledger_archive.incomplete(self.ws.root))
        self.assertEqual(self.files[self.bridge / "ledger_archives" / (TASK + ".json")], journal)

    def test_late_result_publication_refused_before_payload_read(self):
        self.archive(confirm=True, accept_late_result_risk=True)
        from grokbot_mcp.result_cli import precheck
        with patch("grokbot_mcp.result_cli.load_registry", return_value={self.ws.workspace_id: self.ws}), patch("grokbot_mcp.result_cli.safe_path"):
            for phase, identity in (("validate", TASK), ("execute", EXEC)):
                with self.assertRaisesRegex(LocalError, "archived_task"):
                    precheck(self.registry, self.ws.workspace_id, phase, identity, self.bridge / "unused.tmp")


class AdminCliTest(unittest.TestCase):
    def test_no_confirmation_means_preview(self):
        with patch("grokbot_mcp.ledger_archive.archive", return_value={"ok": True}) as run, redirect_stdout(io.StringIO()):
            code = admin_cli.main(["ledger-archive", "--registry", "D:/fictional/registry.json", "--workspace", "fictional",
                                   "--task-id", TASK, "--reason", "fictional"])
        self.assertEqual(code, 0)
        self.assertFalse(run.call_args.kwargs["confirm"])

    def test_seven_tools_and_optional_parameter(self):
        import inspect
        from grokbot_mcp.server import TOOL_NAMES, grok_prepare_task
        self.assertEqual(len(TOOL_NAMES), 7)
        self.assertIsNone(inspect.signature(grok_prepare_task).parameters["allowed_globs"].default)
