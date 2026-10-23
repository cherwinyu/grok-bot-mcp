from __future__ import annotations
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tests.support import temporary_directory
from unittest.mock import patch
from grokbot_mcp import __version__, client_config as client, client_cli, deployment_diagnostics
from grokbot_mcp.identity import RuntimeIdentity, compare_identity
from grokbot_mcp.local_safety import LocalError, current_hash, json_bytes
from tests.deployment_support import DeploymentCase, fake_install
from tests.support import WORKSPACE_ID


class IdentityTest(unittest.TestCase):
    def test_installation_version_registry_and_process_lifetimes(self):
        with temporary_directory() as directory:
            root = Path(directory)
            reg = root / "登记.json"
            reg.write_text("{}", encoding="utf-8")
            a = RuntimeIdentity(python=root/"A/python.exe", package=root/"A/package", registry=reg)
            b = RuntimeIdentity(python=root/"B/python.exe", package=root/"B/package", registry=reg)
            first = a.report(registry=reg, workspace_id=WORKSPACE_ID)
            self.assertNotEqual(first["installation_id"], b.report(registry=reg)["installation_id"])
            self.assertEqual(first["bridge_version"], b.version)
            self.assertNotEqual(first["process_instance_id"], b.instance_id)
            reg.write_text('{"changed":true}', encoding="utf-8")
            second = a.report(registry=reg, workspace_id=WORKSPACE_ID)
            self.assertEqual(first["registry_sha256_at_start"], second["registry_sha256_at_start"])
            self.assertNotEqual(first["registry_sha256"], second["registry_sha256"])
            self.assertEqual(first["installation_id"], second["installation_id"])
            other = root / "other.json"
            other.write_bytes(reg.read_bytes())
            self.assertNotEqual(second["registry_id"], a.report(registry=other)["registry_id"])
            old = RuntimeIdentity(python=a.python, package=a.package, registry=reg, version="0.1.4.dev2")
            self.assertEqual(second["installation_id"], old.report(registry=reg)["installation_id"])
            self.assertNotEqual(second["bridge_version"], old.version)
            self.assertNotIn(str(root), json.dumps(second))
            self.assertIn("not-anonymized", second["identity_privacy"])
            self.assertNotIn("local_paths", second)
            self.assertNotIn(json.dumps(str(root))[1:-1], json.dumps(second))
            # Compare decoded fields, not a native path against JSON escape sequences.
            # On Windows, json.dumps doubles backslashes and escapes Chinese text.
            details = json.loads(json.dumps(a.report(registry=reg, local_details=True)))
            self.assertIn(str(root), details["local_paths"]["python"])
            self.assertEqual(details["local_paths"], {
                "python": str(a.python.absolute()), "package": str(a.package.absolute()),
                "registry": str(reg.absolute()), "registry_at_start": str(reg.absolute()),
            })

    def test_comparison_does_not_claim_client_origin(self):
        value = {"bridge_version": __version__, "installation_id": "a", "registry_id": "b", "registry_sha256": "c", "workspace_id": "d"}
        self.assertTrue(compare_identity(value, value)["identity_matches"])
        self.assertFalse(compare_identity(value, value)["client_reconnection_confirmed"])
        self.assertFalse(compare_identity(value, {})["identity_matches"])
        self.assertIn("installation_id", compare_identity(value, {**value, "installation_id": "old"})["mismatched_fields"])

    def test_shareable_reconstruction_drops_paths_correlations_and_unknown_payload(self):
        raw = {"expected_identity": {"bridge_version": __version__, "installation_id": "PRIVATE_ID"},
               "local_paths": {"x": "PRIVATE_PATH"}, "process_id": 999, "registry_sha256": "PRIVATE_HASH",
               "codes": ["interpreter_missing", "PRIVATE_CODE"], "client_reconnection_confirmed": False}
        shared = deployment_diagnostics.public_report(raw)
        self.assertNotIn("PRIVATE", json.dumps(shared))
        self.assertFalse(shared["client_reconnection_confirmed"])
        self.assertEqual(deployment_diagnostics.public_report({"codes": None})["codes"], [])


class ClientDeploymentTest(DeploymentCase):
    def setUp(self):
        super().setUp()
        self.a = fake_install(self.base / "安装 A spaces")
        self.b = fake_install(self.base / "安装 B spaces")
        self.config = self.base / "客户端 设置.json"
        self.document = {"future": {"unknown": [1, 2]}, "mcpServers": {
            "another": {"command": "leave-as-is", "env": {"EXAMPLE": "fictional-private-value"}},
            "grokbot-mcp": {"command": str(self.a), "args": list(client.SERVER_ARGS),
                "env": {"GROKBOT_MCP_REGISTRY": str(self.registry), "EXTRA": "preserve-unknown"},
                "disabled": False, "unknownField": {"nested": ["keep"]}}}}
        self.write_json(self.config, self.document)

    def switch_plan(self):
        return client.switch_plan(self.config, "grokbot-mcp", self.b, self.registry, WORKSPACE_ID)[0]

    def switch(self):
        return client.switch(self.config, "grokbot-mcp", self.b, self.registry, WORKSPACE_ID, confirm=self.switch_plan()["plan_sha256"])

    def test_preview_has_no_files_or_authorization_changes(self):
        before = self.files(self.base)
        plan = self.switch_plan()
        self.assertIn("command", plan["changed_fields"])
        self.assertFalse(plan["configuration_updated"])
        self.assertFalse(plan["backup_created"])
        self.assertEqual(before, self.files(self.base))
        self.assertNotIn("fictional-private-value", json.dumps(plan))
        self.assertEqual(len(self.case.transport.calls), 0)

    def test_apply_preserves_unknown_fields_and_rollback_restores_exact_bytes(self):
        original = self.config.read_bytes()
        result = self.switch()
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(data["future"], self.document["future"])
        self.assertEqual(data["mcpServers"]["another"], self.document["mcpServers"]["another"])
        self.assertEqual(data["mcpServers"]["grokbot-mcp"]["env"]["EXTRA"], "preserve-unknown")
        self.assertEqual(data["mcpServers"]["grokbot-mcp"]["unknownField"], {"nested": ["keep"]})
        self.assertEqual(data["mcpServers"]["grokbot-mcp"]["command"], str(self.b))
        self.assertFalse(result["client_reconnection_confirmed"])
        review = client.transaction_plan(self.config, result["transaction_id"], rollback=True)
        client.recover(self.config, result["transaction_id"], rollback=True, confirm=review["plan_sha256"])
        self.assertEqual(original, self.config.read_bytes())

    def test_wrong_confirmation_never_writes(self):
        before = self.files(self.base)
        with self.assertRaises(LocalError):
            client.switch(self.config, "grokbot-mcp", self.b, self.registry, WORKSPACE_ID, confirm="wrong")
        self.assertEqual(before, self.files(self.base))

    def test_stale_configuration_confirmation_is_rejected(self):
        plan = self.switch_plan()
        self.document["future"]["new"] = "concurrent"
        self.write_json(self.config, self.document)
        before = self.config.read_bytes()
        with self.assertRaises(LocalError):
            client.switch(self.config, "grokbot-mcp", self.b, self.registry, WORKSPACE_ID, confirm=plan["plan_sha256"])
        self.assertEqual(before, self.config.read_bytes())

    def test_rollback_refuses_new_configuration_edits(self):
        result = self.switch()
        self.config.write_bytes(self.config.read_bytes() + b" ")
        before = self.config.read_bytes()
        with self.assertRaises(LocalError) as error:
            client.transaction_plan(self.config, result["transaction_id"], rollback=True)
        self.assertEqual(error.exception.code, "transaction_target_changed")
        self.assertEqual(before, self.config.read_bytes())

    def test_replace_failure_preserves_file_and_explicit_recovery_works(self):
        before = self.config.read_bytes()
        original_replace = os.replace
        def fail_target(source, destination):
            if Path(destination) == self.config:
                raise PermissionError("fictional-sensitive-error")
            return original_replace(source, destination)
        with patch("grokbot_mcp.local_safety.os.replace", side_effect=fail_target):
            with self.assertRaises(LocalError) as error:
                self.switch()
        self.assertEqual(error.exception.code, "client_apply_interrupted")
        self.assertEqual(before, self.config.read_bytes())
        transaction = error.exception.transaction_id
        self.assertTrue(transaction)
        review = client.transaction_plan(self.config, transaction, rollback=False)
        result = client.recover(self.config, transaction, rollback=False, confirm=review["plan_sha256"])
        self.assertEqual(result["state"], "applied")
        self.assertFalse(list(self.config.parent.glob(".grokbot-*.tmp")))

    def test_backup_tampering_prevents_rollback(self):
        result = self.switch()
        folder = client._folder(self.config, result["transaction_id"])
        (folder / "client.before").write_bytes(b"{}")
        with self.assertRaises(LocalError) as error:
            client.transaction_plan(self.config, result["transaction_id"], rollback=True)
        self.assertEqual(error.exception.code, "transaction_backup_changed")

    def test_second_switch_is_idempotent(self):
        self.switch()
        before = self.files(self.base)
        result = self.switch()
        self.assertTrue(result["no_change"])
        self.assertIsNone(result["transaction_id"])
        self.assertEqual(before, self.files(self.base))

    def test_still_connected_old_instance_is_detectable(self):
        old = client.inspect_config(self.config, "grokbot-mcp", WORKSPACE_ID)["expected_identity"]
        self.switch()
        new = client.inspect_config(self.config, "grokbot-mcp", WORKSPACE_ID)["expected_identity"]
        self.assertFalse(compare_identity(new, old)["identity_matches"])
        self.assertTrue(compare_identity(new, new)["identity_matches"])
        self.assertFalse(compare_identity(new, new)["client_reconnection_confirmed"])

    def test_missing_interpreter_old_script_wrong_registry_and_venv_diagnosed(self):
        entry = self.document["mcpServers"]["grokbot-mcp"]
        cases = [({**entry, "command": str(self.base / "gone/python.exe")}, "interpreter_missing"),
                 ({**entry, "args": [str(self.base / "gone/old_agent.py")]}, "entrypoint_missing"),
                 ({**entry, "args": ["-c", "raise RuntimeError('must not execute')"]}, "client_arguments_unsupported"),
                 ({**entry, "env": {}}, "registry_selection_required")]
        for bad, code in cases:
            with self.subTest(code=code), self.assertRaises(LocalError) as error:
                client._launch(bad)
            self.assertEqual(error.exception.code, code)
        (self.a.parent.parent / "pyvenv.cfg").unlink()
        with self.assertRaises(LocalError):
            client.inspect_installation(self.a)

    def test_old_installed_version_is_visible_not_reported_as_current(self):
        old = fake_install(self.base / "older", "0.1.4.dev2")
        self.document["mcpServers"]["grokbot-mcp"]["command"] = str(old)
        self.write_json(self.config, self.document)
        report = client.inspect_config(self.config, "grokbot-mcp", WORKSPACE_ID)
        self.assertEqual(report["expected_identity"]["bridge_version"], "0.1.4.dev2")
        self.assertIn("candidate_version_mismatch", report["codes"])
        self.assertFalse(report["ok"])

    def test_unknown_format_transport_and_duplicate_json_are_refused(self):
        for suffix, raw in ((".jsonc", b'{}'), (".toml", b'{}'), (".json", b'{"mcpServers":{},"mcpServers":{}}')):
            path = self.base / ("unsupported" + suffix)
            path.write_bytes(raw)
            with self.assertRaises(LocalError):
                client.read_config(path, "grokbot-mcp")
        self.document["mcpServers"]["grokbot-mcp"]["url"] = "fictional-remote-endpoint"
        self.write_json(self.config, self.document)
        with self.assertRaises(LocalError) as error:
            self.switch_plan()
        self.assertEqual(error.exception.code, "client_transport_unsupported")

    def test_apply_export_argument_conflict_rejected_before_mutation(self):
        before = self.files(self.base)
        with redirect_stdout(io.StringIO()) as out:
            code = client_cli.main(["switch", "--config", str(self.config), "--python", str(self.b),
                "--registry", str(self.registry), "--workspace", WORKSPACE_ID, "--apply",
                "--confirm", self.switch_plan()["plan_sha256"], "--export", str(self.base / "export.json")])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())["diagnostic"]["code"], "invalid_arguments")
        self.assertEqual(before, self.files(self.base))

    def test_default_static_inspection_does_not_execute_fake_interpreter(self):
        before = self.files()
        with patch("grokbot_mcp.startup_probe.probe_config", side_effect=AssertionError("not permitted")):
            report = client.inspect_config(self.config, "grokbot-mcp", WORKSPACE_ID)
        self.assertTrue(report["ok"])
        self.assertFalse(report["imports_verified"])
        self.assertFalse(report["credentials_checked"])
        self.assertEqual(before, self.files())
        self.assertNotIn(str(self.base), json.dumps(report))

    def test_hardlinked_config_refused(self):
        linked = self.base / "linked.json"
        os.link(self.config, linked)
        with self.assertRaises(LocalError) as error:
            client.read_config(linked, "grokbot-mcp")
        self.assertEqual(error.exception.code, "linked_path_rejected")

    def test_export_is_redacted_and_refuses_unknown_registry(self):
        report = client.inspect_config(self.config, "grokbot-mcp", WORKSPACE_ID, local_details=True)
        target = self.base / "share.json"
        deployment_diagnostics.export(target, report, self.registry)
        text = target.read_text(encoding="utf-8")
        self.assertNotIn(str(self.base), text)
        self.assertNotIn(report["expected_identity"]["installation_id"], text)
        missing = self.base / "missing-export.json"
        with self.assertRaises(ValueError):
            deployment_diagnostics.export(missing, report, self.base / "absent-registry.json")
        self.assertFalse(missing.exists())
