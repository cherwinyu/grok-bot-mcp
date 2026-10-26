"""Routine fingerprints: fictional Windows fixtures, no live sends or switches."""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import claim_cli, facade, setup_wizard
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.registry import load_registry, policy_fingerprint
from grokbot_mcp.routine import CLAIM_GUIDANCE, FINGERPRINT_PLACEHOLDER, claim_command, claim_entry, confirmation, public_binding
from grokbot_mcp.routine_check import check, inspect_text
from grokbot_mcp.setup_environment import interpreter
from tests.support import WORKSPACE_ID, MockTransport, make_workspace, write_proof


class ClaimFingerprintTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = make_workspace(self.base)
        self.addCleanup(install_backend, None, None)
        self.addCleanup(facade.install_transport, None)
        self.transport = MockTransport()
        facade.install_transport(self.transport)
        self.registry = self.base / "registry.json"
        raw = json.loads(self.registry.read_bytes())
        raw["workspaces"][0]["routine_claim_fingerprint"] = True
        self.registry.write_text(json.dumps(raw), encoding="utf-8")
        self.workspace = load_registry(self.registry)[WORKSPACE_ID]
        self.fingerprint = policy_fingerprint(self.workspace)
        for phase in ("validate", "execute"):
            path = self.root / ".bridge" / (phase + "_routine_status.json")
            binding = json.loads(path.read_bytes())
            binding["binding"]["policy_fingerprint"] = self.fingerprint
            path.write_text(json.dumps(binding), encoding="utf-8")
        self.execution_id = "11111111-1111-4111-8111-111111111111"

    def pending(self):
        # The fictional on-disk switch stays OFF. Only this fixture setup's
        # admission query is doubled, with memory credentials and transport.
        with patch("grokbot_mcp.facade.execution_switch_enabled", return_value=True):
            task = facade.prepare_task(WORKSPACE_ID, "code_change", "Fictional label",
                                       ["src/counter.py"], ["Keep labels"], "python_unittest")
            self.assertTrue(facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])["accepted"])
            write_proof(self.root, task["task_id"], task["manifest_sha256"], ["src/counter.py"])
            self.assertEqual(facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"], "succeeded")
            execution = facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])
            self.execution_id = execution["execution_id"]
            self.assertTrue(facade.submit_execution_tool(WORKSPACE_ID, self.execution_id)["accepted"])
        self.assertFalse(json.loads((self.base / "execution_switch.json").read_bytes())["enabled"])
        return task

    def snapshot(self):
        # Includes ledgers, baseline, claim, registry, switch, and Git files;
        # directory membership also detects transient setup left behind.
        return {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                if p.is_file() else None for p in self.base.rglob("*")}

    def invoke(self, fingerprint=None, *, args=None):
        if args is None:
            args = ["--workspace", WORKSPACE_ID, self.execution_id]
            if fingerprint is not None:
                args = ["--expect-fingerprint", fingerprint] + args
        with patch("grokbot_mcp.setup_wizard._configure_console"), redirect_stdout(io.StringIO()) as out:
            code = claim_cli.main(args)
        raw = out.getvalue()
        self.assertEqual(len(raw.splitlines()), 1)
        self.assertTrue(raw.isascii())
        return code, json.loads(raw), raw

    def test_matching_fingerprint_claims_normally(self):
        self.pending()
        code, result, _ = self.invoke(self.fingerprint)
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])
        self.assertNotIn("routine_fingerprint_checked", result)
        record = json.loads((self.root / ".bridge/executions" / (self.execution_id + ".json")).read_bytes())
        self.assertEqual(record["execution_claim_state"], "claimed")
        self.assertTrue((self.root / ".bridge/execution_claims" / (self.execution_id + ".json")).is_file())

    def test_matching_and_omitted_outputs_are_byte_identical(self):
        result = {"ok": True, "error": None, "allowed_files": ["src/counter.py"]}
        with patch("grokbot_mcp.facade.claim_execution", return_value=result) as claim:
            checked = self.invoke(self.fingerprint)
            legacy = self.invoke()
        self.assertEqual(checked, legacy)
        self.assertEqual(claim.call_count, 2)

    def test_mismatch_preserves_every_file_hash_and_never_enters_claim(self):
        self.pending()
        before = self.snapshot()
        with patch("grokbot_mcp.facade.claim_execution", side_effect=AssertionError("claim touched")):
            code, result, raw = self.invoke("0" * 64)
        self.assertEqual(code, 1)
        self.assertEqual(result, {"ok": False, "error": "routine_binding_mismatch",
                                 "expected_fingerprint": "0" * 64, "policy_fingerprint": self.fingerprint})
        self.assertEqual(before, self.snapshot())
        self.assertNotIn(str(self.root), raw)

    def test_invalid_format_has_no_state_change_or_registry_read(self):
        before = self.snapshot()
        for value in ("", "0" * 63, "0" * 65, "A" * 64, "g" * 64, " " + "a" * 64,
                      "a" * 64 + "\n", "<policy_fingerprint from this paste packet>"):
            with self.subTest(value=value), patch("grokbot_mcp.claim_cli.claim_for") as claim, patch(
                    "grokbot_mcp.claim_cli.list_workspaces") as listed:
                code, result, _ = self.invoke(value)
                self.assertEqual((code, result), (2, {"ok": False, "error": "invalid_expected_fingerprint"}))
                claim.assert_not_called()
                listed.assert_not_called()
        self.assertEqual(before, self.snapshot())

    def test_missing_or_duplicate_option_is_invalid(self):
        for args in (["--expect-fingerprint"],
                     ["--expect-fingerprint", "a" * 64, "--expect-fingerprint", "b" * 64]):
            self.assertEqual(self.invoke(args=args)[:2],
                             (2, {"ok": False, "error": "invalid_expected_fingerprint"}))

    def test_legacy_claim_without_option_still_succeeds(self):
        self.pending()
        code, result, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(result["ok"])

    def test_mismatch_does_not_consume_claim_once(self):
        self.pending()
        self.assertEqual(self.invoke("0" * 64)[1]["error"], "routine_binding_mismatch")
        self.assertEqual(self.invoke(self.fingerprint)[0], 0)
        self.assertEqual(self.invoke(self.fingerprint)[1]["error"], "duplicate_claim")

    def test_workspace_option_order_and_single_workspace_form(self):
        forms = [
            ["--expect-fingerprint", self.fingerprint, "--workspace", WORKSPACE_ID, self.execution_id],
            ["--workspace", WORKSPACE_ID, "--expect-fingerprint", self.fingerprint, self.execution_id],
            ["--workspace", WORKSPACE_ID, self.execution_id, "--expect-fingerprint", self.fingerprint],
            ["--expect-fingerprint", self.fingerprint, self.execution_id],
            [self.execution_id], ["--workspace", WORKSPACE_ID, self.execution_id],
        ]
        for args in forms:
            with self.subTest(args=args):
                self.assertEqual(claim_cli.parse_cli(args), (WORKSPACE_ID, self.execution_id, None))
                with patch("grokbot_mcp.facade.claim_execution", return_value={"ok": True}):
                    self.assertEqual(self.invoke(args=args)[0], 0)

    def test_claim_entry_and_binding_bytes_remain_legacy(self):
        expected = b"grokbot_mcp.claim_cli --workspace example-workspace"
        self.assertEqual(claim_entry(self.workspace).encode(), expected)
        binding = confirmation(self.workspace, "execute", self.registry, mode="reuse", revision=1,
                               verified_at="2026-01-01T00:00:00+00:00")["binding"]
        self.assertEqual(binding["claim_entry"].encode(), expected)
        self.assertEqual(public_binding(self.workspace, binding)["claim_entry"].encode(), expected)

    def test_refused_claim_remains_unclaimed_then_times_out_without_resend(self):
        self.pending()
        # Set an expired timestamp only in this synthetic, unclaimed fixture.
        path = self.root / ".bridge/executions" / (self.execution_id + ".json")
        record = json.loads(path.read_bytes())
        record["request_attempted_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        path.write_text(json.dumps(record), encoding="utf-8")
        calls = len(self.transport.calls)
        before = self.snapshot()
        self.assertEqual(self.invoke("0" * 64)[1]["error"], "routine_binding_mismatch")
        self.assertEqual(before, self.snapshot())
        status = facade.get_status(WORKSPACE_ID, execution_id=self.execution_id)["execution"]
        self.assertEqual(status["status"], "timeout")
        self.assertEqual(json.loads(path.read_bytes())["execution_claim_state"], "unclaimed")
        self.assertEqual(len(self.transport.calls), calls)

    def packet(self):
        output = setup_wizard.generate(self.registry, WORKSPACE_ID)
        return output / "routine-setup.md"

    def test_current_packet_and_all_claim_commands_use_current_fingerprint(self):
        path = self.packet()
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count(claim_command(WORKSPACE_ID, self.fingerprint)), 2)
        self.assertNotIn("<policy_fingerprint from this paste packet>", text)
        before = self.snapshot()
        result = check(self.registry, WORKSPACE_ID)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(before, self.snapshot())

    def test_old_packet_only_advises_update_and_preserves_readiness(self):
        path = self.packet()
        text = path.read_text(encoding="utf-8")
        legacy = text.replace(" --expect-fingerprint " + self.fingerprint, "").replace(CLAIM_GUIDANCE + "\n\n", "")
        path.write_text(legacy, encoding="utf-8")
        result = check(self.registry, WORKSPACE_ID)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["warnings"], ["routine_claim_fingerprint_missing"])
        self.assertTrue(all(p["binding_current"] for p in result["phases"].values()))
        # An unrelated alteration in an old packet remains blocking.
        path.write_text(legacy.replace("Edit only those files", "Edit any files"), encoding="utf-8")
        self.assertIn("routine_template_mismatch", check(self.registry, WORKSPACE_ID)["issues"])

    def test_missing_fingerprint_and_stale_command_have_specific_codes(self):
        path = self.packet()
        text = path.read_text(encoding="utf-8")
        codes = inspect_text(text.replace(self.fingerprint, "0" * 64), self.workspace,
                             self.registry, Path(interpreter()), "setup")
        self.assertIn("routine_policy_fingerprint_missing", codes)
        self.assertIn("routine_claim_fingerprint_mismatch", codes)
        # Current binding elsewhere must not hide a stale command's fingerprint.
        path.write_text(text.replace("--expect-fingerprint " + self.fingerprint,
                                     "--expect-fingerprint " + "0" * 64), encoding="utf-8")
        self.assertIn("routine_claim_fingerprint_mismatch", check(self.registry, WORKSPACE_ID)["issues"])

    def test_missing_command_not_satisfied_by_prose_mention(self):
        path = self.packet()
        text = path.read_text(encoding="utf-8").replace(" --expect-fingerprint " + self.fingerprint, "")
        path.write_text(text, encoding="utf-8")
        result = check(self.registry, WORKSPACE_ID)
        self.assertTrue(result["ok"], result)
        self.assertIn("routine_claim_fingerprint_missing", result["warnings"])

    def test_static_templates_share_command_source_and_guidance(self):
        templates = files("grokbot_mcp").joinpath("templates")
        execute = templates.joinpath("execute.md").read_text(encoding="utf-8")
        self.assertIn(claim_command("example-workspace", FINGERPRINT_PLACEHOLDER), execute)
        for phase in ("validate", "execute"):
            self.assertIn(CLAIM_GUIDANCE, templates.joinpath(phase + ".md").read_text(encoding="utf-8"))

    def test_migration_packet_uses_current_fingerprint_and_accepts_legacy(self):
        from grokbot_mcp.migration import _packet, _profile
        python = Path(interpreter())
        text = _packet(_profile(self.workspace, local_details=True), python, self.registry)
        self.assertIn(claim_command(WORKSPACE_ID, self.fingerprint), text)
        self.assertEqual(inspect_text(text, self.workspace, self.registry, python, "migration"), [])
        legacy = text.replace(" --expect-fingerprint " + self.fingerprint, "").replace(CLAIM_GUIDANCE + "\n\n", "")
        for packet in (legacy, legacy.replace("\n", "\r\n")):
            self.assertEqual(inspect_text(packet, self.workspace, self.registry, python, "migration"),
                             ["routine_claim_fingerprint_missing"])


if __name__ == "__main__":
    unittest.main()
