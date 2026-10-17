from __future__ import annotations
import json
import shutil
from pathlib import Path
from unittest.mock import patch
from grokbot_mcp import migration, file_transaction, facade, setup_wizard
from grokbot_mcp.local_safety import LocalError, current_hash
from grokbot_mcp.registry import load_registry
from grokbot_mcp.routine import routine_readiness
from tests.deployment_support import DeploymentCase, fake_install
from tests.support import WORKSPACE_ID


class MigrationDeploymentTest(DeploymentCase):
    def setUp(self):
        super().setUp()
        self.python = fake_install(self.base / "新 安装")
        self.workspace = load_registry(self.registry)[WORKSPACE_ID]

    def plan(self, **kwargs):
        return migration.plan(self.registry, WORKSPACE_ID, self.python, **kwargs)

    def apply(self):
        return migration.apply_plan(self.registry, WORKSPACE_ID, self.python, confirm=self.plan()["plan_sha256"])

    def finish(self, transaction, operation, **kwargs):
        plan = migration.transaction_plan(self.registry, WORKSPACE_ID, transaction, operation=operation)
        return migration.finish(self.registry, WORKSPACE_ID, transaction, operation=operation, confirm=plan["plan_sha256"], **kwargs)

    def test_plan_is_readonly_lists_old_target_scope_and_claim(self):
        before = self.files(self.base)
        report = self.plan()
        self.assertTrue(report["ok"])
        self.assertFalse(report["no_change"])
        self.assertEqual(report["current"]["allowed_files"], report["target"]["allowed_files"])
        self.assertEqual(report["current"]["expected_head"], report["target"]["expected_head"])
        self.assertIn("claim-evidence-v2", report["target_capabilities"])
        self.assertIn("execute", report["current_bindings"])
        self.assertFalse(report["remote_binding_verified"])
        self.assertEqual(before, self.files(self.base))
        self.assertEqual(len(self.case.transport.calls), 0)

    def test_apply_is_pending_not_remote_success_and_finalization_is_explicit(self):
        original = self.registry.read_bytes()
        result = self.apply()
        transaction = result["transaction_id"]
        self.assertTrue(result["local_configuration_applied"])
        self.assertFalse(result["remote_binding_verified"])
        ready = routine_readiness(self.workspace, "execute", self.registry)
        self.assertFalse(ready["submit_ready"])
        self.assertEqual(ready["binding_error"], "migration_pending")
        with self.assertRaises(LocalError) as error:
            self.finish(transaction, "finalize")
        self.assertEqual(error.exception.code, "migration_remote_review_required")
        final = self.finish(transaction, "finalize", remote_review_confirmed=True)
        self.assertTrue(final["administrator_remote_review_confirmed"])
        self.assertFalse(final["remote_binding_verified"])
        self.assertFalse(final["live_execution_verified"])
        self.assertTrue(routine_readiness(self.workspace, "execute", self.registry)["submit_ready"])
        self.assertEqual(original, self.registry.read_bytes())
        self.assertTrue(self.plan()["no_change"])

    def test_idempotent_no_change_does_not_create_more_transactions(self):
        transaction = self.apply()["transaction_id"]
        self.finish(transaction, "finalize", remote_review_confirmed=True)
        before = self.files(self.base)
        result = self.apply()
        self.assertTrue(result["no_change"])
        self.assertIsNone(result["transaction_id"])
        self.assertEqual(before, self.files(self.base))

    def test_rollback_restores_exact_bindings_without_changing_business_evidence(self):
        paths = migration._targets(self.workspace)
        before = {k: v.read_bytes() for k, v in paths.items()}
        business = migration.state_snapshot(self.workspace)
        transaction = self.apply()["transaction_id"]
        self.finish(transaction, "finalize", remote_review_confirmed=True, routine_status="paused")
        self.assertFalse(routine_readiness(self.workspace, "execute", self.registry)["submit_ready"])
        self.assertEqual(self.finish(transaction, "rollback")["state"], "rolled_back")
        self.assertEqual(before, {k: v.read_bytes() for k, v in paths.items()})
        self.assertEqual(business, migration.state_snapshot(self.workspace))
        with self.assertRaises(LocalError):
            self.finish(transaction, "recover")

    def test_unresolved_submission_blocks_apply_and_preview_reports_reason(self):
        task = self.case.prepare()
        facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])
        plan = self.plan()
        self.assertFalse(plan["ok"])
        self.assertIn("binding_change_has_unresolved_tasks", plan["blocking_reasons"])
        before = self.files(self.base)
        with self.assertRaises(LocalError):
            migration.apply_plan(self.registry, WORKSPACE_ID, self.python, confirm=plan["plan_sha256"])
        self.assertEqual(before, self.files(self.base))

    def test_new_evidence_after_migration_blocks_rollback_even_if_not_pending(self):
        transaction = self.apply()["transaction_id"]
        self.finish(transaction, "finalize", remote_review_confirmed=True)
        self.case.prepare()  # An unsent new manifest still belongs to the new epoch.
        before = self.files()
        with self.assertRaises(LocalError) as error:
            migration.transaction_plan(self.registry, WORKSPACE_ID, transaction, operation="rollback")
        self.assertEqual(error.exception.code, "migration_new_evidence_present")
        self.assertEqual(before, self.files())

    def test_unknown_submission_blocks_rollback_and_never_deletes_ledger(self):
        transaction = self.apply()["transaction_id"]
        path = self.root / ".bridge/tasks_v2/00000000-0000-4000-8000-000000000099.json"
        self.write_json(path, {"task_state": "unknown_submission", "submission_state": "unknown_submission"})
        before = path.read_bytes()
        with self.assertRaises(LocalError) as error:
            migration.transaction_plan(self.registry, WORKSPACE_ID, transaction, operation="rollback")
        self.assertEqual(error.exception.code, "binding_change_has_unresolved_tasks")
        self.assertEqual(before, path.read_bytes())

    def test_missing_interpreter_and_missing_capability_are_not_fixed_by_credentials(self):
        path = migration._targets(self.workspace)["execute"]
        value = json.loads(path.read_text(encoding="utf-8"))
        value["binding"]["claim_entry"] = "claim_dynamic.py"
        self.write_json(path, value)
        ready = facade.preflight(WORKSPACE_ID)
        self.assertEqual(ready["execute_credentials"], "present")
        self.assertFalse(ready["phases"]["execute"]["submit_permitted"])
        self.assertEqual(ready["phases"]["execute"]["binding_error"], "binding_claim_entry_mismatch")
        value["binding"]["claim_entry"] = "grokbot_mcp.claim_cli --workspace " + WORKSPACE_ID
        value["binding"]["protocol_capabilities"] = ["manifest-v1"]
        self.write_json(path, value)
        self.assertEqual(routine_readiness(self.workspace, "execute", self.registry)["binding_error"], "binding_capability_missing")

    def test_pending_marker_cannot_be_bypassed_by_legacy_confirmation_entry(self):
        transaction = self.apply()["transaction_id"]
        before = self.files()
        with self.assertRaises(ValueError):
            setup_wizard.confirm_binding(self.registry, WORKSPACE_ID, "execute", "reuse")
        self.assertEqual(before, self.files())
        self.assertFalse(routine_readiness(self.workspace, "validate", self.registry)["submit_ready"])

    def test_partial_multi_file_write_can_recover(self):
        actual = file_transaction.atomic_replace
        failing = migration._targets(self.workspace)["execute"]
        def fail(source_path, raw, *, expected):
            if source_path == failing:
                raise LocalError("atomic_write_failed")
            return actual(source_path, raw, expected=expected)
        with patch("grokbot_mcp.file_transaction.atomic_replace", side_effect=fail):
            with self.assertRaises(LocalError):
                self.apply()
        marker = json.loads((self.root / ".bridge/deployment_migration.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["state"], "pending_remote_review")
        self.assertFalse(routine_readiness(self.workspace, "validate", self.registry)["submit_ready"])
        restored = self.finish(marker["transaction_id"], "recover")
        self.assertEqual(restored["state"], "pending_remote_review")
        self.assertFalse(restored["remote_binding_verified"])
        self.assertEqual(self.finish(marker["transaction_id"], "rollback")["state"], "rolled_back")

    def test_interruption_before_marker_is_recoverable_without_claiming_remote_review(self):
        with patch("grokbot_mcp.migration.atomic_replace", side_effect=LocalError("atomic_write_failed")):
            with self.assertRaises(LocalError):
                self.apply()
        self.assertFalse((self.root / ".bridge/deployment_migration.json").exists())
        folder = self.registry.parent / "migrations" / WORKSPACE_ID
        transaction = next(p.name for p in folder.iterdir() if p.is_dir())
        plan = migration.transaction_plan(self.registry, WORKSPACE_ID, transaction)
        self.assertEqual(plan["state"], "prepared")
        result = self.finish(transaction, "recover")
        self.assertEqual(result["state"], "pending_remote_review")
        self.assertFalse(result["administrator_remote_review_confirmed"])

    def test_finalization_interruption_preserves_explicit_attestation_and_recovers(self):
        transaction = self.apply()["transaction_id"]
        actual = file_transaction.atomic_replace
        failing = migration._targets(self.workspace)["execute"]
        def fail(path, raw, *, expected):
            if path == failing and b"administrator_confirmed" in raw:
                raise LocalError("atomic_write_failed")
            return actual(path, raw, expected=expected)
        with patch("grokbot_mcp.file_transaction.atomic_replace", side_effect=fail):
            with self.assertRaises(LocalError):
                self.finish(transaction, "finalize", remote_review_confirmed=True)
        self.assertFalse(routine_readiness(self.workspace, "validate", self.registry)["submit_ready"])
        recovered = self.finish(transaction, "recover")
        self.assertEqual(recovered["state"], "completed")
        self.assertTrue(recovered["administrator_remote_review_confirmed"])
        self.assertFalse(recovered["remote_binding_verified"])

    def test_scope_expansion_baseline_change_and_workspace_move_are_blocked(self):
        original = json.loads(self.registry.read_text(encoding="utf-8"))
        target = self.base / "目标登记.json"
        other_root = self.base / "另一虚构工作区"
        shutil.copytree(self.root, other_root)
        for field, value, code in (("allowed_files", ["src/counter.py", "tests/test_counter.py", "src/extra.py"], "migration_scope_expansion_forbidden"),
                                   ("expected_head", "a"*40, "migration_head_change_forbidden"),
                                   ("writes_enabled", False, "migration_write_permission_change_forbidden"),
                                   ("root", str(other_root), "migration_workspace_move_forbidden"),
                                   ("forbidden_files", [original["workspaces"][0]["protected_file"]], "migration_protection_change_forbidden")):
            data = json.loads(json.dumps(original))
            data["workspaces"][0][field] = value
            self.write_json(target, data)
            with self.subTest(field=field):
                report = self.plan(target_registry=target)
                self.assertFalse(report["ok"])
                self.assertIn(code, report["blocking_reasons"])
        self.assertEqual(self.registry.read_bytes(), json.dumps(original).encode("utf-8"))

    def test_registry_or_installation_change_blocks_recovery(self):
        transaction = self.apply()["transaction_id"]
        server = self.python.parent.parent / "Lib/site-packages/grokbot_mcp/server.py"
        server.write_text("# different code with the same version\n", encoding="utf-8")
        with self.assertRaises(LocalError) as error:
            migration.transaction_plan(self.registry, WORKSPACE_ID, transaction)
        self.assertEqual(error.exception.code, "migration_installation_changed")

    def test_local_packet_does_not_copy_unknown_secret_fields(self):
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        data["fictional_unknown_secret"] = "DO_NOT_BACKUP_FICTIONAL_SECRET"
        self.write_json(self.registry, data)
        transaction = self.apply()["transaction_id"]
        folder = migration._folder(self.registry, WORKSPACE_ID, transaction)
        combined = b"".join(path.read_bytes() for path in folder.rglob("*") if path.is_file())
        self.assertNotIn(b"DO_NOT_BACKUP_FICTIONAL_SECRET", combined)
        self.assertIn(b"grokbot_mcp.claim_cli", combined)
        self.assertIn(b"grokbot_mcp.result_cli", combined)

    def test_unknown_binding_payload_is_refused_instead_of_backed_up(self):
        path = migration._targets(self.workspace)["execute"]
        value = json.loads(path.read_text(encoding="utf-8"))
        value["private_key"] = "fictional-do-not-copy"
        self.write_json(path, value)
        with self.assertRaises(LocalError) as error:
            self.plan()
        self.assertEqual(error.exception.code, "migration_binding_snapshot_unsupported")
