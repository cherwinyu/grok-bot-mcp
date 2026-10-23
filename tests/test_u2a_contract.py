"""U2a synthetic in-memory regressions: no tempfile, credentials or transport."""
from __future__ import annotations

import hashlib
import json
import io
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

from grokbot_mcp.registry import _workspace, _commands, RegistryError, policy_fingerprint, workspace_profile
from grokbot_mcp.result_contract import diagnosis, output_evidence, validate_result
from grokbot_mcp.result_examples import examples
from grokbot_mcp.test_commands import applicable, changed_files_argv, selection_warnings
from grokbot_mcp.security.git_review import changes_since_baseline, review
from grokbot_mcp.security.paths import PathRejected
from grokbot_mcp.execution.service import _interpret
from grokbot_mcp.manifest.store import prepare_manifest, ManifestError
from grokbot_mcp.materials import render_routine


def entry(**overrides):
    return dict(workspace_id="example-workspace", root=str(Path.cwd()), expected_head="a" * 40,
                allowed_files=["src/counter.py", "docs/guide.md"], forbidden_files=["private"],
                protected_file="private", writes_enabled=False, expected_machine="EXAMPLE-PC",
                validate_credential_target="fictional/validate", execute_credential_target="fictional/execute",
                test_commands=[dict(id="python_unittest", task_types=["code_change"], timeout_cap=60, default_timeout=30)],
                **overrides)


class SelectionTest(unittest.TestCase):
    def command(self, **options):
        return _commands([dict(id="docs_lint", task_types=["code_change"], timeout_cap=60, default_timeout=30, **options)])[0]

    def test_multiple_commands_and_default_options(self):
        data = entry()
        data["test_commands"].append(dict(id="docs_lint", task_types=["code_change"], timeout_cap=60, default_timeout=30, applies_to="docs"))
        workspace = _workspace(data)
        self.assertIsNotNone(workspace.command("docs_lint"))
        self.assertFalse(workspace.require_test_output)
        self.assertEqual(workspace.test_selection, "warn")

    def test_known_kinds_and_unknown_are_conservative(self):
        command = self.command(applies_to="docs")
        self.assertTrue(applicable(command, ["docs/a.md", "docs/B.RST"]))
        self.assertFalse(applicable(command, ["src/a.py"]))
        self.assertIsNone(applicable(command, ["a.py", "b.md"]))
        self.assertIsNone(applicable(command, ["asset.bin"]))
        self.assertIsNone(applicable(command, []))
        self.assertTrue(applicable(self.command(applies_to="any"), []))
        self.assertTrue(applicable(self.command(applies_to="code"), ["main.gd"]))

    def test_all_files_prefix_boundary_and_suffix_intersection(self):
        command = self.command(applies_to={"prefixes": [".\\docs\\"], "suffixes": [".md"]})
        self.assertTrue(applicable(command, ["DOCS/中.md", "docs/b.md"]))
        for paths in (["docs2/a.md"], ["docs/a.txt"], ["docs/a.md", "src/b.md"]):
            self.assertFalse(applicable(command, paths))

    def test_warn_and_configured_refusal(self):
        workspace = replace(_workspace(entry()), test_commands=(self.command(applies_to="docs"),))
        self.assertEqual(selection_warnings(workspace, "docs_lint", "code_change", ["src/a.py"]), ["test_command_scope_mismatch"])
        with self.assertRaisesRegex(ValueError, "test_command_scope_mismatch"):
            selection_warnings(replace(workspace, test_selection="reject"), "docs_lint", "code_change", ["src/a.py"])
        with self.assertRaisesRegex(ValueError, "test_command_required"):
            selection_warnings(workspace, "", "code_change", ["src/a.py"])

    def test_prepare_warning_does_not_change_manifest(self):
        workspace = replace(_workspace(entry()), test_commands=(self.command(applies_to="docs"),))
        captured = {}
        def save(_path, document):
            captured.update(document)
        with patch("grokbot_mcp.manifest.store.write_json_new", side_effect=save), patch(
                "grokbot_mcp.manifest.store.read_json", side_effect=lambda _: (captured, "b" * 64)):
            result = prepare_manifest(workspace, task_type="code_change", task_description="Fictional edit",
                allowed_files=["src/counter.py"], acceptance_criteria=["Measured tests"], test_command_id="docs_lint",
                execution_timeout=None, baseline_commit=None)
        self.assertEqual(result["warnings"], ["test_command_scope_mismatch"])
        self.assertNotIn("warnings", captured)
        with patch("grokbot_mcp.manifest.store.write_json_new") as save, self.assertRaises(ManifestError):
            prepare_manifest(replace(workspace, test_selection="reject"), task_type="code_change", task_description="Fictional edit",
                allowed_files=["src/counter.py"], acceptance_criteria=["Measured tests"], test_command_id="docs_lint",
                execution_timeout=None, baseline_commit=None)
        save.assert_not_called()

    def test_duplicate_ids_and_invalid_options_rejected(self):
        commands = entry()["test_commands"]
        with self.assertRaises(RegistryError):
            _commands(commands * 2)
        for value in (True, [], {}, "guess", {"prefixes": ["../escape"]}, {"suffixes": ["*"]}):
            with self.subTest(value=value), self.assertRaises(RegistryError):
                self.command(applies_to=value)

    def test_argv_expands_as_individual_unicode_space_paths(self):
        command = self.command(argv=["python", "-m", "fictional_lint", "--", "{changed_files}"])
        names = ["docs/中 文.md", "docs/a.md"]
        self.assertEqual(changed_files_argv(command, names, names), ["python", "-m", "fictional_lint", "--", *sorted(names)])

    def test_injection_response_files_switches_and_traversal_rejected(self):
        command = self.command(argv=["lint.exe", "{changed_files}"])
        for name in ("-x", "@args", "a;evil.md", "a&evil.md", "a`evil.md", "a$(evil).md", "a%PATH%.md",
                     "a\n.md", "a\x00.md", "../a.md", "C:/a.md", "docs/a:stream", 'a\"b.md'):
            with self.subTest(name=name), self.assertRaises((ValueError, PathRejected)):
                changed_files_argv(command, [name], [name])
        with self.assertRaisesRegex(ValueError, "empty"):
            changed_files_argv(command, [], [])
        with self.assertRaisesRegex(ValueError, "outside_scope"):
            changed_files_argv(command, ["other.md"], ["docs/a.md"])

    def test_shell_templates_and_embedded_placeholder_rejected(self):
        for argv in (["cmd.exe", "/c", "{changed_files}"], ["lint.cmd", "{changed_files}"],
                     ["pwsh", "{changed_files}"], ["lint", "--files={changed_files}"],
                     ["lint", "{changed_files}", "{changed_files}"], ["{changed_files}"]):
            with self.subTest(argv=argv), self.assertRaises(RegistryError):
                self.command(argv=argv)


class OutputEvidenceTest(unittest.TestCase):
    def test_roundtrip_empty_short_unicode_and_truncated(self):
        for raw in (b"", b"passed\n", "中".encode("utf-8") * 4000, b"a" * 30000):
            value = examples()["execute-succeeded"]
            value["tests"].update(output_evidence(raw))
            self.assertIs(validate_result(value, "execute"), value)
            self.assertEqual(value["tests"]["output_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(value["tests"]["output_bytes"], len(raw))
            self.assertEqual(value["tests"]["output"]["truncated"], len(raw) > 16384)

    def test_invalid_utf8_helper_refuses(self):
        with self.assertRaises(UnicodeError):
            output_evidence(b"\xff")

    def test_required_off_by_default_and_on_blocks_terminal(self):
        value = examples()["execute-succeeded"]
        self.assertEqual(diagnosis(value, "execute")["category"], "format_valid")
        self.assertEqual(diagnosis(value, "execute", require_test_output=True)["category"], "test_evidence_missing")
        self.assertEqual(diagnosis(examples()["execute-running"], "execute", require_test_output=True)["category"], "intermediate_result")

    def test_malformed_output_is_safe_format_error(self):
        bad = [{"output": "private marker"}, {"output_bytes": True}, {"output_sha256": "bad"}]
        for key, value in (("head", "中" * 3000), ("tail", "x" * 8193), ("truncated", 1), ("head", "\ud800")):
            fields = output_evidence(b"hello")
            fields["output"][key] = value
            bad.append(fields)
        for fields in bad:
            value = examples()["execute-succeeded"]
            value["tests"].update(fields)
            issue = diagnosis(value, "execute")
            self.assertEqual(issue["category"], "format_error")
            self.assertNotIn("private marker", json.dumps(issue))

    def test_untruncated_count_and_hash_verified(self):
        for key, bad in (("output_bytes", 6), ("output_sha256", "0" * 64)):
            value = examples()["execute-succeeded"]
            value["tests"].update(output_evidence(b"hello"))
            value["tests"][key] = bad
            self.assertEqual(diagnosis(value, "execute")["field"], "tests." + key)

    def test_four_megabyte_limit_still_applies(self):
        from grokbot_mcp.strict_json import decode, MAX_BYTES
        with self.assertRaisesRegex(ValueError, "json_too_large"):
            decode(json.dumps({"tests": {"output": "x" * MAX_BYTES}}).encode("utf-8"))


class RegistryCompatibilityTest(unittest.TestCase):
    def test_old_policy_fingerprint_matches_literal_old_fields(self):
        item = entry()
        workspace = _workspace(item)
        policy = {k: v for k, v in item.items() if k not in {"root", "writes_enabled"}}
        policy["policy_revision"] = 1
        self.assertEqual(policy_fingerprint(workspace), hashlib.sha256(json.dumps(
            policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest())
        profile = workspace_profile(workspace)
        self.assertNotIn("require_test_output", profile)
        self.assertNotIn("argv", profile["test_commands"][0])

    def test_new_security_options_change_fingerprint(self):
        workspace = _workspace(entry())
        for changed in (replace(workspace, require_test_output=True), replace(workspace, test_selection="reject"),
                        replace(workspace, test_commands=(replace(workspace.test_commands[0], applies_to="docs"),))):
            self.assertNotEqual(policy_fingerprint(workspace), policy_fingerprint(changed))

    def test_other_prefixes_normalize_and_only_advisory(self):
        workspace = _workspace(entry(git_gate="advisory", other_prefixes=[".//Other///", ".\\Other\\", "scope/../cafe\u0301"]))
        self.assertEqual(workspace.other_prefixes, ("other", "café"))
        for options in (dict(other_prefixes=["other"]), dict(git_gate="advisory", other_prefixes=["../escape"]),
                        dict(git_gate="advisory", other_prefixes=[1]), dict(require_test_output=1), dict(test_selection="skip")):
            with self.subTest(options=options), self.assertRaises(RegistryError):
                _workspace(entry(**options))

    def test_existing_result_files_remain_readable_and_unchanged(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "examples/results").glob("*.json"):
            raw = path.read_bytes()
            validate_result(json.loads(raw), path.name.split("-")[0])
            self.assertEqual(path.read_bytes(), raw)


class ReviewTest(unittest.TestCase):
    def test_current_integrity_compares_new_dirty_hashes_but_accepts_legacy_snapshot(self):
        from grokbot_mcp.security.baseline import same_snapshot
        before = {"dirty_hashes": {"other/a": "1"}}
        after = {"dirty_hashes": {"other/a": "2"}}
        self.assertFalse(same_snapshot(before, after))
        self.assertTrue(same_snapshot({}, after))

    def test_dirty_file_changed_removed_and_preexisting_unchanged(self):
        before = dict(git_head_text="a" * 40, dirty_hashes={"other/a": "1", "other/b": "2", "other/c": "3"})
        after = dict(git_head_text="a" * 40, dirty_hashes={"other/a": "4", "other/c": "3"})
        self.assertEqual(changes_since_baseline(Path.cwd(), before, after), ["other/a", "other/b"])

    def test_head_diff_uses_no_renames_and_nul_delimited_names(self):
        before, after = dict(git_head_text="a" * 40), dict(git_head_text="b" * 40)
        with patch("grokbot_mcp.security.git_review.run_git", return_value=subprocess.CompletedProcess([], 0, "other/中 文.md\0old.md\0")) as run:
            names = changes_since_baseline(Path.cwd(), before, after)
        self.assertEqual(names, ["old.md", "other/中 文.md"])
        self.assertIn("--no-renames", run.call_args.args[1])
        self.assertIn("-z", run.call_args.args[1])

    def test_git_failure_remains_closed(self):
        with patch("grokbot_mcp.security.git_review.run_git", return_value=subprocess.CompletedProcess([], 1, "")):
            with self.assertRaisesRegex(ValueError, "git_status_failed"):
                changes_since_baseline(Path.cwd(), dict(git_head_text="a" * 40), dict(git_head_text="b" * 40))

    def test_classification_allowed_first_casefold_and_prefix_boundary(self):
        value = review(dict(git_head_text="a" * 40), dict(git_head_text="b" * 40),
                       ["other/a", "Other/own", "other2/a"], ["reported.md"], ["other/own"], ["./other/"])
        self.assertEqual(value["in_scope"], ["Other/own"])
        self.assertEqual(value["declared_others"], ["other/a"])
        self.assertEqual(value["out_of_scope"], ["other2/a", "reported.md"])
        self.assertTrue(value["head_changed"])


class InterpretationTest(unittest.TestCase):
    def interpret(self, workspace, *, output=False, head_changed=False, failed=False, command_id="python_unittest"):
        value = examples()["execute-failed" if failed else "execute-succeeded"]
        value["tests"]["command_id"] = command_id
        if output:
            value["tests"].update(output_evidence(b"fictional private output"))
        record = {k: value[k] for k in ("execution_id", "manifest_task_id", "manifest_sha256")}
        document = dict(allowed_files=["src/counter.py"], task_type="code_change", test_command_id=command_id)
        baseline = dict(git_head_text="a" * 40, file_hashes={"src/counter.py": "old"})
        current = dict(git_head_text=("b" if head_changed else "a") * 40, file_hashes={"src/counter.py": "new"})
        store = Mock()
        store.read.return_value = record
        with patch("grokbot_mcp.execution.service.assess", return_value={"document": document}), patch(
                "grokbot_mcp.execution.service.verify_evidence", return_value=(baseline, current)), patch(
                "grokbot_mcp.execution.service.utc_now", return_value="fixed-time"), patch(
                "grokbot_mcp.security.git_review.run_git", return_value=subprocess.CompletedProcess([], 0, "other/a\0")):
            result = _interpret(store, workspace, record, {}, value, "b" * 64)
        return result, store.update.call_args.kwargs

    def test_strict_result_json_matches_fixed_legacy_bytes(self):
        result, saved = self.interpret(_workspace(entry()))
        expected = b'{"changed_files": ["src/counter.py"], "code_modified": true, "current_integrity": true, "error": null, "execution_state": "succeeded", "godot_exit_code": 0, "historical_status": "succeeded", "integrity_error": null, "qa_accepted": false, "reported_success": true, "result_diagnosis": {"bridge_accepted": false, "category": "format_valid", "code": "format_valid", "contract_version": 1, "qa_accepted": false}, "status": "succeeded", "test_failed": 0, "test_passed": 3, "verification_scope": "current", "verified_at": "fixed-time"}'
        self.assertEqual(json.dumps(result, sort_keys=True).encode("utf-8"), expected)
        self.assertNotIn("git_review", saved["verified_summary"])
        self.assertNotIn("tests", saved["verified_summary"])

    def test_missing_required_evidence_cannot_succeed_and_is_frozen(self):
        result, saved = self.interpret(_workspace(entry(require_test_output=True)))
        self.assertEqual(result["error"], "test_evidence_missing")
        self.assertEqual(saved["verified_summary"]["result_diagnosis"]["category"], "test_evidence_missing")
        self.assertTrue(saved["terminal"])

    def test_neutral_summary_no_output_echo_and_non_godot_null(self):
        result, saved = self.interpret(_workspace(entry(require_test_output=True)), output=True)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["tests"]["exit_code"], 0)
        self.assertIsNone(result["godot_exit_code"])
        self.assertNotIn("fictional private output", json.dumps(result) + json.dumps(saved))

    def test_opted_in_godot_command_keeps_compatibility_exit_code(self):
        workspace = _workspace(entry(require_test_output=True))
        workspace = replace(workspace, test_commands=(replace(workspace.test_commands[0], command_id="godot_headless"),))
        result, _saved = self.interpret(workspace, output=True, command_id="godot_headless")
        self.assertEqual(result["tests"]["command_id"], "godot_headless")
        self.assertEqual(result["godot_exit_code"], 0)

    def test_git_review_head_drift_still_refused_and_summary_preserved(self):
        result, saved = self.interpret(_workspace(entry(git_gate="advisory", other_prefixes=["other"])), head_changed=True)
        self.assertEqual(result["error"], "git_head_mismatch")
        self.assertEqual(result["git_review"]["declared_others"], ["other/a"])
        self.assertEqual(result["out_of_scope_changes"], ["other/a"])
        self.assertEqual(saved["verified_summary"]["git_review"], result["git_review"])

    def test_failed_tests_keep_review(self):
        result, saved = self.interpret(_workspace(entry(git_gate="advisory")), failed=True)
        self.assertEqual(result["error"], "test_failed")
        self.assertEqual(saved["verified_summary"]["git_review"], result["git_review"])


class MaterialTest(unittest.TestCase):
    def test_optional_template_only_rendered_with_new_policy(self):
        arguments = dict(workspace_root=Path("C:/Fictional"), workspace_id="fictional", registry=Path("C:/registry.json"),
                         python=Path("C:/python.exe"), mode="create")
        old = render_routine("execute", **arguments)
        self.assertEqual(old, render_routine("execute", **arguments, test_options=workspace_profile(_workspace(entry()))))
        new = render_routine("execute", **arguments, test_options={"require_test_output": True})
        self.assertIn("tests.output_sha256", new)
        self.assertIn("test_command_cli", new)
        self.assertNotIn("{{", new)
        root = Path(__file__).resolve().parents[1]
        self.assertEqual((root / "routines/test-evidence.md").read_bytes(), (root / "src/grokbot_mcp/templates/test-evidence.md").read_bytes())

    def test_wizard_forwards_repeated_prefix_without_prompt(self):
        from grokbot_mcp import setup_wizard
        with patch.object(setup_wizard, "inspect_environment", return_value={"ready": True, "bridge_version": "fictional"}), patch.object(
                setup_wizard, "safe_path"), patch.object(Path, "exists", return_value=False), patch.object(
                setup_wizard, "register", side_effect=ValueError("registration_cancelled")) as register, patch(
                "builtins.input", side_effect=AssertionError("unexpected prompt")), redirect_stdout(io.StringIO()):
            result = setup_wizard.main(["--registry", str(Path.cwd() / "fictional.json"), "--workspace", "fictional",
                "--git-gate", "advisory", "--other-prefix", "./Other/", "--other-prefix", "second"])
        self.assertEqual(result, 1)
        self.assertEqual(register.call_args.kwargs, dict(mode="create", git_gate="advisory", other_prefixes=["./Other/", "second"]))

    def test_read_only_flag_rejects_prefix(self):
        from grokbot_mcp import setup_wizard
        with patch.object(setup_wizard, "register") as register, redirect_stdout(io.StringIO()):
            result = setup_wizard.main(["--check", "--workspace", "fictional", "--git-gate", "advisory", "--other-prefix", "other"])
        self.assertEqual(result, 1)
        register.assert_not_called()


class PreviewTest(unittest.TestCase):
    def call_preview(self, **record_updates):
        from grokbot_mcp import test_command_cli
        from grokbot_mcp.result_examples import EXECUTION, TASK
        workspace = _workspace(entry())
        workspace = replace(workspace, test_commands=(replace(workspace.test_commands[0], argv=("lint.exe", "{changed_files}")),))
        record = dict(execution_id=EXECUTION, manifest_task_id=TASK, action="execute_manifest",
                      execution_result_path=".bridge/results_execute/" + EXECUTION + ".json",
                      execution_state="running", submission_state="accepted", approval_state="granted")
        record.update(record_updates)
        document = dict(allowed_files=["src/counter.py"], test_command_id="python_unittest")
        before = dict(file_hashes={"src/counter.py": "old"})
        after = dict(file_hashes={"src/counter.py": "new"})
        with patch.object(test_command_cli, "load_registry", return_value={workspace.workspace_id: workspace}), patch.object(
                test_command_cli, "JsonLedger") as ledger, patch.object(test_command_cli, "assess", return_value={"document": document}), patch.object(
                test_command_cli, "verify_evidence", return_value=(before, after)) as verify, patch(
                "grokbot_mcp.security.targets.resolve_exact_files", return_value=["src/counter.py"]) as exact:
            ledger.return_value.read.return_value = record
            result = test_command_cli.preview(Path("fictional.json"), workspace.workspace_id, EXECUTION)
        ledger.return_value.update.assert_not_called()
        ledger.return_value.create.assert_not_called()
        verify.assert_called_once()
        self.assertTrue(verify.call_args.kwargs["require_claim"])
        exact.assert_called_once()
        return result

    def test_preview_only_expands_observed_changes_after_verified_claim(self):
        result = self.call_preview()
        self.assertEqual(result["argv"], ["lint.exe", "src/counter.py"])
        self.assertFalse(result["executed"])
        self.assertFalse(result["ledger_modified"])

    def test_terminal_preview_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not_claimable"):
            self.call_preview(terminal=True)

    def test_unknown_submission_preview_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not_claimable"):
            self.call_preview(submission_state="unknown_submission")

    def test_mismatched_ledger_identity_is_refused(self):
        with self.assertRaisesRegex(ValueError, "result_identity_mismatch"):
            self.call_preview(execution_id="00000000-0000-4000-8000-000000000099")


if __name__ == "__main__":
    unittest.main()
