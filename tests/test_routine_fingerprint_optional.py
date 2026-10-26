"""Optional remote claim pinning never disables local policy identity checks."""
from __future__ import annotations

import hashlib
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import facade, migration, setup_wizard
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.materials import render_routine
from grokbot_mcp.registry import RegistryError, _workspace, load_registry, policy_fingerprint
from grokbot_mcp.routine import CLAIM_GUIDANCE, binding_error, claim_command, confirmation
from grokbot_mcp.routine_check import check, inspect_text
from grokbot_mcp.setup_environment import interpreter
from tests.support import WORKSPACE_ID, git, make_workspace, temporary_directory
from tests.test_u2a_contract import entry


class OptionalFingerprintTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        tmp = temporary_directory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = make_workspace(self.base)
        self.registry = self.base / "registry.json"
        self.addCleanup(install_backend, None, None)
        self.addCleanup(facade.install_transport, None)

    def configure(self, **fields):
        raw = json.loads(self.registry.read_bytes())
        raw["workspaces"][0].update(fields)
        self.registry.write_text(json.dumps(raw), encoding="utf-8")
        ws = load_registry(self.registry)[WORKSPACE_ID]
        # Explicit synthetic fixture confirmations, never production migration.
        for phase in ("validate", "execute"):
            path = self.root / ".bridge" / (phase + "_routine_status.json")
            status = json.loads(path.read_bytes())
            status["binding"]["policy_fingerprint"] = policy_fingerprint(ws)
            path.write_text(json.dumps(status), encoding="utf-8")
        return ws

    def test_default_and_false_packets_pass_without_claim_warning(self):
        for gate in ("strict", "advisory"):
            for enabled in (None, False):
                for prefixes in ({}, {"allowed_prefixes": [{"prefix": "src/", "exts": [".py"]}]},
                                 {"prefix_ceiling": [{"prefix": "src/", "exts": [".py"]}]}):
                    fields = {"git_gate": gate, **prefixes}
                    if enabled is not None:
                        fields["routine_claim_fingerprint"] = enabled
                    # Each case starts from an omitted optional policy.
                    raw = json.loads(self.registry.read_bytes())
                    for key in ("routine_claim_fingerprint", "allowed_prefixes", "prefix_ceiling"):
                        raw["workspaces"][0].pop(key, None)
                    self.registry.write_text(json.dumps(raw), encoding="utf-8")
                    ws = self.configure(**fields)
                    for mode in ("create", "reuse", "migrate"):
                        with self.subTest(gate=gate, enabled=enabled, prefixes=prefixes, mode=mode):
                            folder = setup_wizard.generate(self.registry, WORKSPACE_ID, mode)
                            text = (folder / "routine-setup.md").read_text(encoding="utf-8")
                            self.assertNotIn("--expect-fingerprint", text)
                            self.assertNotIn(CLAIM_GUIDANCE, text)
                            self.assertNotIn("and `policy_fingerprint`", text)
                            self.assertEqual(text.count(claim_command(WORKSPACE_ID, None)), 2)
                            result = check(self.registry, WORKSPACE_ID)
                            self.assertTrue(result["ok"], result)
                            self.assertEqual(result["warnings"], [])
                    packet = migration._packet(migration._profile(ws, local_details=True),
                                               Path(interpreter()), self.registry)
                    self.assertNotIn("--expect-fingerprint", packet)
                    self.assertNotIn("and `policy_fingerprint`", packet)
                    self.assertEqual(inspect_text(packet, ws, self.registry, Path(interpreter()), "migration"), [])

    def test_explicit_true_renders_pin_and_missing_pin_still_warns(self):
        ws = self.configure(routine_claim_fingerprint=True)
        folder = setup_wizard.generate(self.registry, WORKSPACE_ID)
        path = folder / "routine-setup.md"
        text = path.read_text(encoding="utf-8")
        fingerprint = policy_fingerprint(ws)
        self.assertEqual(text.count(claim_command(WORKSPACE_ID, fingerprint)), 2)
        self.assertEqual(text.count(CLAIM_GUIDANCE), 2)
        self.assertTrue(check(self.registry, WORKSPACE_ID)["ok"])
        path.write_text(text.replace(" --expect-fingerprint " + fingerprint, ""), encoding="utf-8")
        report = check(self.registry, WORKSPACE_ID)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["warnings"], ["routine_claim_fingerprint_missing"])

    def test_advisory_head_advance_and_registration_refresh_do_not_change_fingerprint(self):
        ws = self.configure(git_gate="advisory", routine_claim_fingerprint=True)
        strict = replace(ws, git_gate="strict")
        fingerprint = policy_fingerprint(ws)
        # A real commit in the owned fictional repository, never this source checkout.
        (self.root / "src/counter.py").write_text("VALUE = 2\n", encoding="utf-8")
        git(self.root, "add", "src/counter.py")
        git(self.root, "commit", "-m", "fictional head advance")
        head = git(self.root, "rev-parse", "HEAD")
        self.assertNotEqual(head, ws.expected_head)
        self.assertEqual(fingerprint, policy_fingerprint(load_registry(self.registry)[WORKSPACE_ID]))
        refreshed = self.configure(expected_head=head)
        self.assertEqual(fingerprint, policy_fingerprint(refreshed))
        self.assertNotEqual(policy_fingerprint(strict), policy_fingerprint(replace(strict, expected_head=head)))

    def test_local_binding_drift_and_template_tampering_remain_blocking(self):
        ws = self.configure()
        for phase in ("validate", "execute"):
            binding = confirmation(ws, phase, self.registry, mode="reuse", revision=1,
                                   verified_at="2026-01-01T00:00:00+00:00")["binding"]
            self.assertEqual(binding["policy_fingerprint"], policy_fingerprint(ws))
            for changed in (replace(ws, forbidden_files=ws.forbidden_files + ("private",)),
                            replace(ws, routine_claim_fingerprint=True)):
                self.assertEqual(binding_error(changed, phase, binding, self.registry), "binding_policy_mismatch")
        folder = setup_wizard.generate(self.registry, WORKSPACE_ID)
        path = folder / "routine-setup.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Edit only those files", "Edit any files"), encoding="utf-8")
        self.assertIn("routine_template_mismatch", check(self.registry, WORKSPACE_ID)["issues"])


class FingerprintPolicyTest(unittest.TestCase):
    def test_strict_omitted_and_false_keep_legacy_hash(self):
        for fields in ({}, {"routine_claim_fingerprint": False}):
            ws = _workspace(entry(**fields))
            self.assertEqual(policy_fingerprint(ws), "e07a777ebdc54e30c0bb19cc46b809a0a6fe0455e5ae0b2bbaa36b740084e6c5")
            self.assertFalse(ws.routine_claim_fingerprint)
        self.assertNotEqual(policy_fingerprint(ws), policy_fingerprint(replace(ws, routine_claim_fingerprint=True)))

    def test_non_boolean_values_are_rejected_with_specific_code(self):
        for value in (None, 0, 1, "true", "false", [], {}):
            with self.subTest(value=value), self.assertRaisesRegex(RegistryError, "^routine_claim_fingerprint_invalid$"):
                _workspace(entry(routine_claim_fingerprint=value))

    def test_direct_render_defaults_off_even_with_a_local_fingerprint(self):
        ws = _workspace(entry())
        self.assertEqual(claim_command(ws.workspace_id),
                         "grokbot_mcp.claim_cli --workspace " + ws.workspace_id + " <execution_id>")
        text = render_routine("execute", workspace_root=ws.root, workspace_id=ws.workspace_id,
                              registry=Path("D:/fictional/registry.json"), python=Path("D:/fictional/python.exe"),
                              mode="reuse", expected_fingerprint=policy_fingerprint(ws))
        self.assertNotIn("--expect-fingerprint", text)
        self.assertNotIn(CLAIM_GUIDANCE, text)

    def test_existing_advisory_binding_requires_explicit_reconfirmation(self):
        raw = entry(git_gate="advisory")
        ws = _workspace(raw)
        legacy = {key: value for key, value in raw.items() if key not in {"root", "writes_enabled"}}
        legacy["policy_revision"] = 1
        old = hashlib.sha256(json.dumps(legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        registry = Path("D:/fictional/registry.json")
        binding = confirmation(ws, "execute", registry, mode="reuse", revision=1,
                               verified_at="2026-01-01T00:00:00+00:00")["binding"]
        binding["policy_fingerprint"] = old
        before = json.dumps(binding, sort_keys=True)
        self.assertNotEqual(old, policy_fingerprint(ws))
        self.assertEqual(binding_error(ws, "execute", binding, registry), "binding_policy_mismatch")
        self.assertEqual(json.dumps(binding, sort_keys=True), before)
