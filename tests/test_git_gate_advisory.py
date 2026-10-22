"""Synthetic coverage for the opt-in advisory Git gate."""
from __future__ import annotations

import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import facade, setup_wizard
from grokbot_mcp.registry import RegistryError, load_registry, policy_fingerprint
from grokbot_mcp.security.gitcheck import git_error, git_snapshot
from tests.support import (
    WORKSPACE_ID, execution_result, git, make_advisory_workspace, make_workspace, write_proof,
)


class GitGateAdvisoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = make_advisory_workspace(self.base, extra_forbidden=("private",))
        self.registry = self.base / "registry.json"
        (self.base / "execution_switch.json").write_text('{"enabled": true}\n', encoding="utf-8")
        self.addCleanup(facade.install_transport, None)

    def prepare(self) -> dict[str, object]:
        return facade.prepare_task(
            WORKSPACE_ID, "code_change", "Add a fictional advisory label.",
            ["src/counter.py"], ["The existing test remains green."], "python_unittest",
        )

    def pending_claim(self) -> tuple[dict[str, object], dict[str, object]]:
        task = self.prepare()
        self.assertIsNone(task.get("error"))
        self.assertEqual(facade.submit_validation_tool(WORKSPACE_ID, str(task["task_id"]))["http_status"], 200)
        write_proof(self.root, str(task["task_id"]), str(task["manifest_sha256"]), ["src/counter.py"])
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=str(task["task_id"]))["validation"]["status"], "succeeded")
        execution = facade.prepare_execution_tool(WORKSPACE_ID, str(task["task_id"]))
        self.assertIsNone(execution["error"])
        self.assertEqual(facade.submit_execution_tool(WORKSPACE_ID, str(execution["execution_id"]))["http_status"], 200)
        return task, execution

    def start(self) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        task, execution = self.pending_claim()
        claim = facade.claim_for(WORKSPACE_ID, str(execution["execution_id"]))
        self.assertTrue(claim["ok"])
        return task, execution, claim

    def write_result(self, task: dict[str, object], execution: dict[str, object],
                     changed: list[str], *, failed: int = 0) -> None:
        directory = self.root / ".bridge/results_execute"
        directory.mkdir(parents=True, exist_ok=True)
        payload = execution_result(
            str(execution["execution_id"]), str(task["task_id"]),
            str(task["manifest_sha256"]), changed, failed=failed,
        )
        (directory / f"{execution['execution_id']}.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_registry_validation_default_and_legacy_bytes(self) -> None:
        legacy = json.loads(self.registry.read_text(encoding="utf-8"))
        legacy["workspaces"][0].pop("git_gate")
        raw = json.dumps(legacy).encode("utf-8")
        self.registry.write_bytes(raw)
        self.assertEqual(load_registry(self.registry)[WORKSPACE_ID].git_gate, "strict")
        self.assertEqual(self.registry.read_bytes(), raw)
        for value in ("loose", 1, None, True):
            with self.subTest(value=value):
                candidate = json.loads(raw)
                candidate["workspaces"][0]["git_gate"] = value
                self.registry.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(RegistryError):
                    load_registry(self.registry)

    def test_strict_registry_roundtrip_policy_and_baseline_are_byte_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = make_workspace(Path(name))
            registry = Path(name) / "registry.json"
            raw = registry.read_bytes()
            document = json.loads(raw)
            item = document["workspaces"][0]
            baseline = item["expected_head"]
            legacy_policy = {key: item[key] for key in (
                "workspace_id", "expected_head", "allowed_files", "forbidden_files",
                "protected_file", "test_commands", "expected_machine",
                "validate_credential_target", "execute_credential_target",
            )}
            legacy_policy["policy_revision"] = 1
            expected_fingerprint = hashlib.sha256(json.dumps(
                legacy_policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            ).encode("utf-8")).hexdigest()
            workspace = load_registry(registry)[WORKSPACE_ID]
            self.assertEqual(workspace.git_gate, "strict")
            self.assertEqual(workspace.expected_head, baseline)
            self.assertEqual(policy_fingerprint(workspace), expected_fingerprint)
            self.assertEqual(json.dumps(document).encode("utf-8"), raw)
            self.assertEqual(registry.read_bytes(), raw)
            self.assertNotIn("git_gate", item)
            self.assertEqual(git(root, "rev-parse", "HEAD"), baseline)

    def test_advisory_registration_uses_flag_without_new_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = make_workspace(Path(name))
            registry = Path(name) / "advisory" / "registry.json"
            answers = [
                str(root), "src/counter.py", "", "src/do_not_touch.py",
                "python_unittest", "python -m unittest", "y", "n",
            ]
            with patch("builtins.input", side_effect=answers):
                setup_wizard.register(
                    registry, "advisory-workspace", git_gate="advisory",
                )
            document = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(document["workspaces"][0]["git_gate"], "advisory")

    def test_main_forwards_advisory_command_line_flag(self) -> None:
        with patch.object(setup_wizard, "register") as register:
            code = setup_wizard.main([
                "--registry", str(self.registry),
                "--workspace", "new-advisory-workspace",
                "--git-gate", "advisory",
            ])
        self.assertEqual(code, 1)
        register.assert_called_once_with(
            self.registry.absolute(), "new-advisory-workspace",
            mode="create", git_gate="advisory",
        )

    def test_advisory_claim_records_head_and_existing_changes(self) -> None:
        (self.root / "src/counter.py").write_text("VALUE = 2\n", encoding="utf-8")
        git(self.root, "add", "src/counter.py")
        git(self.root, "commit", "-m", "advance fictional head")
        current = git(self.root, "rev-parse", "HEAD")
        (self.root / "tests/test_counter.py").write_text("# pre-existing tracked change\n", encoding="utf-8")
        (self.root / "notes.tmp").write_text("pre-existing untracked\n", encoding="utf-8")
        task, _execution, claim = self.start()
        manifest = json.loads((self.root / ".bridge/task_specs" / f"{task['task_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["baseline_commit"], current)
        self.assertEqual(claim["baseline_commit"], current)
        self.assertEqual(claim["git_gate"], "advisory")
        self.assertEqual(set(claim["existing_changes"]), {"notes.tmp", "tests/test_counter.py"})

    def test_advisory_reports_out_of_scope_without_downgrade(self) -> None:
        task, execution, _claim = self.start()
        (self.root / "src/counter.py").write_text("VALUE = 3\n", encoding="utf-8")
        (self.root / "parallel.txt").write_text("another writer\n", encoding="utf-8")
        self.write_result(task, execution, ["src/counter.py", "parallel.txt"])
        status = facade.get_status(WORKSPACE_ID, execution_id=str(execution["execution_id"]))["execution"]
        self.assertEqual(status["status"], "succeeded")
        self.assertEqual(status["out_of_scope_changes"], ["parallel.txt"])

    def test_advisory_protected_change_still_fails(self) -> None:
        task, execution, _claim = self.start()
        (self.root / "src/counter.py").write_text("VALUE = 4\n", encoding="utf-8")
        (self.root / "src/do_not_touch.py").write_text("VALUE = 999\n", encoding="utf-8")
        self.write_result(task, execution, ["src/counter.py", "src/do_not_touch.py"])
        status = facade.get_status(WORKSPACE_ID, execution_id=str(execution["execution_id"]))["execution"]
        self.assertEqual(status["error"], "scope_violation")

    def test_advisory_prepare_rejects_dirty_protected_file(self) -> None:
        (self.root / "src/do_not_touch.py").write_text("VALUE = 999\n", encoding="utf-8")
        result = self.prepare()
        self.assertEqual(result["error"], "protected_or_forbidden_changed")
        self.assertEqual(result["protected_or_forbidden_changes"], ["src/do_not_touch.py"])

    def test_advisory_claim_rejects_protected_change_since_head(self) -> None:
        _task, execution = self.pending_claim()
        (self.root / "src/do_not_touch.py").write_text("VALUE = 999\n", encoding="utf-8")
        git(self.root, "add", "src/do_not_touch.py")
        claim = facade.claim_for(WORKSPACE_ID, str(execution["execution_id"]))
        self.assertFalse(claim["ok"])
        self.assertEqual(claim["error"], "protected_or_forbidden_changed")
        self.assertEqual(claim["protected_or_forbidden_changes"], ["src/do_not_touch.py"])

    def test_advisory_validation_rejects_protected_change_since_head(self) -> None:
        task = self.prepare()
        self.assertIsNone(task.get("error"))
        (self.root / "src/do_not_touch.py").write_text("VALUE = 999\n", encoding="utf-8")
        result = facade.submit_validation_tool(WORKSPACE_ID, str(task["task_id"]))
        self.assertEqual(result["error"], "protected_or_forbidden_changed")
        self.assertEqual(result["protected_or_forbidden_changes"], ["src/do_not_touch.py"])

    def test_advisory_claim_rejects_untracked_forbidden_path(self) -> None:
        _task, execution = self.pending_claim()
        (self.root / "private").mkdir()
        (self.root / "private/secret.txt").write_text("fictional\n", encoding="utf-8")
        claim = facade.claim_for(WORKSPACE_ID, str(execution["execution_id"]))
        self.assertFalse(claim["ok"])
        self.assertEqual(claim["error"], "protected_or_forbidden_changed")
        self.assertEqual(claim["protected_or_forbidden_changes"], ["private/secret.txt"])

    def test_advisory_claim_allows_other_preexisting_dirty_file(self) -> None:
        _task, execution = self.pending_claim()
        (self.root / "notes.tmp").write_text("pre-existing\n", encoding="utf-8")
        claim = facade.claim_for(WORKSPACE_ID, str(execution["execution_id"]))
        self.assertTrue(claim["ok"])
        self.assertEqual(claim["existing_changes"], ["notes.tmp"])

    def test_failed_summary_keeps_out_of_scope_changes(self) -> None:
        task, execution, _claim = self.start()
        (self.root / "src/counter.py").write_text("VALUE = 5\n", encoding="utf-8")
        (self.root / "parallel.txt").write_text("another writer\n", encoding="utf-8")
        self.write_result(task, execution, ["src/counter.py", "parallel.txt"], failed=1)
        first = facade.get_status(WORKSPACE_ID, execution_id=str(execution["execution_id"]))["execution"]
        self.assertEqual(first["error"], "test_failed")
        self.assertEqual(first["out_of_scope_changes"], ["parallel.txt"])
        frozen = facade.get_status(WORKSPACE_ID, execution_id=str(execution["execution_id"]))["execution"]
        self.assertEqual(frozen["out_of_scope_changes"], ["parallel.txt"])

    def test_git_status_failure_is_closed_in_both_modes(self) -> None:
        failed = type("Failed", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        snapshot = git_snapshot(self.root, ("src/counter.py",), "0" * 40, runner=lambda *_args, **_kwargs: failed)
        self.assertEqual(git_error(snapshot, "strict"), "git_status_failed")
        self.assertEqual(git_error(snapshot, "advisory"), "git_status_failed")

    def test_strict_regressions_keep_head_dirty_and_untracked_refusals(self) -> None:
        head = git(self.root, "rev-parse", "HEAD")
        self.assertEqual(
            git_error(git_snapshot(self.root, ("src/counter.py",), "0" * 40)),
            "git_head_mismatch",
        )
        (self.root / "tests/test_counter.py").write_text("# dirty outside allow-list\n", encoding="utf-8")
        self.assertEqual(
            git_error(git_snapshot(self.root, ("src/counter.py",), head)),
            "unexpected_changes",
        )
        (self.root / "tests/test_counter.py").write_text(
            "import unittest\nfrom src.counter import count_above\n\nclass CounterTest(unittest.TestCase):\n"
            "    def test_count(self):\n        self.assertEqual(count_above([1, 3, 5], 2), 2)\n",
            encoding="utf-8",
        )
        (self.root / "untracked.tmp").write_text("untracked\n", encoding="utf-8")
        self.assertEqual(
            git_error(git_snapshot(self.root, ("src/counter.py",), head)),
            "unconfirmed_changes",
        )


if __name__ == "__main__":
    unittest.main()
