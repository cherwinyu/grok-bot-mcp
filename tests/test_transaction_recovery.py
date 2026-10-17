"""Preparation interruption and runtime binding regressions; fictional data only."""
from pathlib import Path
from unittest.mock import patch
import json
from grokbot_mcp import file_transaction, migration, facade
from grokbot_mcp.identity import digest
from grokbot_mcp.local_safety import LocalError, current_hash
from grokbot_mcp.registry import load_registry
from tests.deployment_support import DeploymentCase, fake_install
from tests.support import WORKSPACE_ID


class TransactionRecoveryTest(DeploymentCase):
    def test_incomplete_preparation_never_publishes_receipt_or_changes_target(self):
        target = self.base / "client.json"
        target.write_bytes(b'{"original":true}')
        folder = self.base / "history" / "fixture"
        actual = file_transaction.create_new
        def fail(path, raw):
            if path.name == "receipt.json":
                raise LocalError("atomic_write_failed")
            return actual(path, raw)
        with patch("grokbot_mcp.file_transaction.create_new", side_effect=fail):
            with self.assertRaises(LocalError):
                file_transaction.prepare(folder, {"client": (target, b'{}')}, {})
        self.assertFalse(folder.exists())
        self.assertEqual(target.read_bytes(), b'{"original":true}')
        file_transaction.prepare(folder, {"client": (target, b'{}')}, {})
        self.assertEqual(file_transaction.apply(folder, {"client": target})["state"], "applied")
        self.assertEqual(target.read_bytes(), b'{}')
        file_transaction.apply(folder, {"client": target}, rollback=True)
        self.assertEqual(target.read_bytes(), b'{"original":true}')

    def test_migration_packet_hash_is_checked_before_recovery(self):
        python = fake_install(self.base / "new installation")
        plan = migration.plan(self.registry, WORKSPACE_ID, python)
        result = migration.apply_plan(self.registry, WORKSPACE_ID, python, confirm=plan["plan_sha256"])
        folder = migration._folder(self.registry, WORKSPACE_ID, result["transaction_id"])
        packet = folder / "routine-update.md"
        receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["attachments"][packet.name], current_hash(packet))
        packet.write_text("changed fictional update instructions", encoding="utf-8")
        before = self.files()
        with self.assertRaises(LocalError) as error:
            migration.transaction_plan(self.registry, WORKSPACE_ID, result["transaction_id"])
        self.assertEqual(error.exception.code, "transaction_backup_changed")
        self.assertEqual(before, self.files())

    def test_finalization_preparation_can_be_retried_without_inventing_attestation(self):
        python = fake_install(self.base / "new installation")
        plan = migration.plan(self.registry, WORKSPACE_ID, python)
        transaction = migration.apply_plan(self.registry, WORKSPACE_ID, python, confirm=plan["plan_sha256"])["transaction_id"]
        plan = migration.transaction_plan(self.registry, WORKSPACE_ID, transaction, operation="finalize")
        actual = file_transaction.create_new
        def fail(path, raw):
            if path.name == "receipt.json":
                raise LocalError("atomic_write_failed")
            return actual(path, raw)
        with patch("grokbot_mcp.file_transaction.create_new", side_effect=fail):
            with self.assertRaises(LocalError):
                migration.finish(self.registry, WORKSPACE_ID, transaction, operation="finalize",
                                 confirm=plan["plan_sha256"], remote_review_confirmed=True)
        folder = migration._folder(self.registry, WORKSPACE_ID, transaction)
        self.assertFalse((folder / "finalize").exists())
        plan = migration.transaction_plan(self.registry, WORKSPACE_ID, transaction, operation="recover")
        result = migration.finish(self.registry, WORKSPACE_ID, transaction, operation="recover", confirm=plan["plan_sha256"])
        self.assertEqual(result["state"], "pending_remote_review")
        self.assertFalse(result["administrator_remote_review_confirmed"])
        plan = migration.transaction_plan(self.registry, WORKSPACE_ID, transaction, operation="finalize")
        result = migration.finish(self.registry, WORKSPACE_ID, transaction, operation="finalize",
                                  confirm=plan["plan_sha256"], remote_review_confirmed=True)
        self.assertEqual(result["state"], "completed")
        self.assertFalse(result["remote_binding_verified"])

    def test_explicit_new_binding_rejects_wrong_running_claim_installation(self):
        _, execution = self.case.start(claim=False)
        path = self.root / ".bridge/execute_routine_status.json"
        binding = json.loads(path.read_text(encoding="utf-8"))
        binding["binding"]["expected_installation_id"] = "a"*64
        self.write_json(path, binding)
        before = self.files()
        result = facade.claim_for(WORKSPACE_ID, execution["execution_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "binding_claim_runtime_mismatch")
        self.assertEqual(before, self.files())

    def test_default_binding_summary_does_not_forward_arbitrary_path_values(self):
        path = self.root / ".bridge/execute_routine_status.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        private = str(self.base / "private notes.txt")
        for field in ("verified_at", "verification_source", "template_version", "mode"):
            value["binding"][field] = private
        self.write_json(path, value)
        text = json.dumps(facade.preflight(WORKSPACE_ID))
        self.assertNotIn(private, text)
        self.assertNotIn(private.replace("\\", "\\\\"), text)
