"""Director gate: native temporary fixtures, real Git and locks; memory transport."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import facade, setup_wizard, result_cli
from grokbot_mcp.ledger_archive import archive, incomplete
from grokbot_mcp.local_safety import LocalError
from grokbot_mcp.registry import load_registry, policy_fingerprint
from grokbot_mcp.routine_check import check
from grokbot_mcp.security.globs import expand
from grokbot_mcp.manifest.store import ManifestError
from grokbot_mcp.storage import JsonLedger
from tests.deployment_support import DeploymentCase
from tests.support import WORKSPACE_ID


class NativeRecoveryTest(DeploymentCase):
    def timed_out(self):
        task = self.case.prepare()
        self.assertTrue(facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])["accepted"])
        self.case.expire("tasks_v2", task["task_id"])
        status = facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]
        self.assertEqual(status["status"], "timeout")
        return task

    def archive(self, task, **kw):
        return archive(self.registry, WORKSPACE_ID, task["task_id"], reason="fictional binding recovery", **kw)

    def test_preview_guard_archive_and_new_task_admission(self):
        task = self.timed_out()
        before = self.files()
        preview = self.archive(task)
        self.assertEqual(before, self.files())
        self.assertTrue(preview["risk_acceptance_required"])
        with self.assertRaisesRegex(LocalError, "late_result_risk"):
            self.archive(task, confirm=True)
        report = self.archive(task, confirm=True, accept_late_result_risk=True)
        for move in report["moves"]:
            self.assertEqual((Path(report["archive_path"]) / move["path"]).read_bytes(), before[move["path"]])
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["error"], "archived_task")
        calls = len(self.case.transport.calls)
        self.assertFalse(facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])["accepted"])
        self.assertEqual(len(self.case.transport.calls), calls)
        new = self.case.prepare()
        self.assertTrue(facade.submit_validation_tool(WORKSPACE_ID, new["task_id"])["accepted"])

    def test_late_candidate_and_direct_result_cannot_revive_archived_task(self):
        task = self.timed_out()
        self.archive(task, confirm=True, accept_late_result_risk=True)
        staged = self.root / ".bridge/results_v2" / ("." + task["task_id"] + ".result.tmp")
        self.write_json(staged, {"status": "succeeded"})
        with self.assertRaisesRegex(LocalError, "archived_task"):
            result_cli.precheck(self.registry, WORKSPACE_ID, "validate", task["task_id"], staged)
        self.write_json(self.case.path("results_v2", task["task_id"]), {"status": "succeeded"})
        self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"], "archived")

    def test_interrupted_archive_retains_barrier_then_resumes(self):
        task = self.timed_out()
        original = os.rename
        calls = []
        def interrupted(source, target):
            calls.append(source)
            if len(calls) == 2:
                raise OSError("fictional interruption")
            return original(source, target)
        with patch("grokbot_mcp.ledger_archive.os.rename", side_effect=interrupted), self.assertRaises(OSError):
            self.archive(task, confirm=True, accept_late_result_risk=True)
        self.assertTrue(incomplete(self.root))
        with self.assertRaisesRegex(ValueError, "archive_incomplete"):
            JsonLedger(self.root / ".bridge/tasks_v2").list_records()
        self.archive(task, confirm=True, accept_late_result_risk=True)
        self.assertFalse(incomplete(self.root))

    def test_execution_record_moved_without_rewriting_claim_or_result(self):
        task, execution = self.case.start()
        claim = self.case.path("execution_claims", execution["execution_id"])
        original = claim.read_bytes()
        self.archive(task, confirm=True, accept_late_result_risk=True)
        self.assertEqual(claim.read_bytes(), original)
        self.assertEqual(facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])["execution"]["error"], "archived_task")

    def test_routine_local_packet_and_attestation_drift(self):
        output = setup_wizard.generate(self.registry, WORKSPACE_ID)
        self.assertTrue(check(self.registry, WORKSPACE_ID)["ok"])
        before = self.files()
        packet = output / "routine-setup.md"
        original = packet.read_text(encoding="utf-8")
        packet.write_text(original + "\n<ADMIN_REGISTRY>\n", encoding="utf-8")
        self.assertIn("routine_placeholders_remaining", check(self.registry, WORKSPACE_ID)["issues"])
        packet.write_text(original.replace("python_unittest", "fictional_wrong_test"), encoding="utf-8")
        self.assertIn("routine_profile_mismatch", check(self.registry, WORKSPACE_ID)["issues"])
        packet.write_text(original, encoding="utf-8")
        status = self.root / ".bridge/validate_routine_status.json"
        value = json.loads(status.read_text(encoding="utf-8"))
        value["binding"]["policy_fingerprint"] = "0" * 64
        self.write_json(status, value)
        self.assertIn("routine_attestation_stale", check(self.registry, WORKSPACE_ID)["issues"])
        before.pop("validate_routine_status.json")
        after = self.files()
        after.pop("validate_routine_status.json")
        self.assertEqual(before, after)

    def test_git_expansion_excludes_untracked_forbidden_and_audits_scope(self):
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        data["workspaces"][0]["allow_globs"] = True
        self.write_json(self.registry, data)
        workspace = load_registry(self.registry)[WORKSPACE_ID]
        (self.root / "src/untracked.py").write_text("# fictional\n", encoding="utf-8")
        # Only index members and policy members can be selected.
        self.assertEqual(expand(workspace, ["src/**"]), ["src/counter.py"])
        with self.assertRaisesRegex(ManifestError, "limit"):
            expand(replace(workspace, glob_max_files=1), ["src/**", "tests/**"])
        with self.assertRaisesRegex(ManifestError, "disabled"):
            expand(replace(workspace, allow_globs=False), ["src/**"])

    def test_native_hardlink_and_symlink_refused(self):
        workspace = replace(load_registry(self.registry)[WORKSPACE_ID], allow_globs=True)
        target = self.root / "src/counter.py"
        alias = self.root / "src/fictional_alias.py"
        os.link(target, alias)
        with self.assertRaisesRegex(ManifestError, "unsafe"):
            expand(workspace, ["src/**"])
        alias.unlink()
        target.rename(alias)
        try:
            target.symlink_to(alias)
        except OSError:
            alias.rename(target)
            self.skipTest("Windows symlink privilege unavailable; hardlink refusal verified")
        with self.assertRaisesRegex(ManifestError, "unsafe"):
            expand(workspace, ["src/**"])
