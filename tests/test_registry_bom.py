"""Registry-only BOM tolerance preserves raw-byte guards and evidence refusal."""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import admin_cli, diagnostics, facade, migration, registry_prefixes, setup_wizard
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.identity import digest
from grokbot_mcp.local_safety import LocalError, parse_json
from grokbot_mcp.registry import RegistryError, load_registry, read_registry_document
from grokbot_mcp.strict_json import MAX_BYTES
from tests.deployment_support import fake_install
from tests.support import WORKSPACE_ID, make_workspace, temporary_directory

BOM = b"\xef\xbb\xbf"


class RegistryBomTest(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        tmp = temporary_directory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = make_workspace(self.base)
        self.registry = self.base / "registry.json"
        self.original = self.registry.read_bytes()
        self.addCleanup(install_backend, None, None)
        self.addCleanup(facade.install_transport, None)

    def bom(self):
        self.registry.write_bytes(BOM + self.original)

    def test_loader_and_maintenance_reader_preserve_original_bytes(self):
        expected = load_registry(self.registry)
        self.bom()
        self.assertEqual(load_registry(self.registry), expected)
        document, raw = read_registry_document(self.registry)
        self.assertEqual(document, json.loads(self.original))
        self.assertEqual(raw, BOM + self.original)
        self.assertEqual(self.registry.read_bytes(), raw)

    def test_wizard_generation_main_and_diagnostics_accept_bom(self):
        expected = diagnostics.collect(self.registry, WORKSPACE_ID)
        folder = setup_wizard.generate(self.registry, WORKSPACE_ID)
        packet = (folder / "routine-setup.md").read_bytes()
        self.bom()
        result = diagnostics.collect(self.registry, WORKSPACE_ID)
        self.assertEqual(result["workspace_registered"], expected["workspace_registered"])
        self.assertEqual(result["findings"], expected["findings"])
        setup_wizard.generate(self.registry, WORKSPACE_ID)
        self.assertEqual((folder / "routine-setup.md").read_bytes(), packet)
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=AssertionError("unexpected prompt")):
            code = setup_wizard.main(["--registry", str(self.registry), "--workspace", WORKSPACE_ID, "--generate-only"])
        self.assertEqual(code, 0)
        self.assertEqual(self.registry.read_bytes(), BOM + self.original)

    def test_admin_registry_check_is_identical_and_read_only(self):
        def run():
            with redirect_stdout(io.StringIO()) as output:
                code = admin_cli.main(["registry-check", "--registry", str(self.registry), "--workspace", WORKSPACE_ID])
            self.assertEqual(code, 0)
            return json.loads(output.getvalue())
        expected = run()
        self.bom()
        self.assertEqual(run(), expected)
        self.assertEqual(self.registry.read_bytes(), BOM + self.original)

    def test_prefix_write_preserves_bom_backup_and_writes_without_bom(self):
        self.bom()
        before = self.registry.read_bytes()
        self.assertTrue(registry_prefixes.preview(self.registry, WORKSPACE_ID)["ok"])
        result = registry_prefixes.change(self.registry, WORKSPACE_ID, add=["src/:.py"], apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual((self.base / result["backup"]).read_bytes(), before)
        self.assertFalse(self.registry.read_bytes().startswith(BOM))
        self.assertTrue(load_registry(self.registry)[WORKSPACE_ID].allowed_prefixes)

    def test_prefix_preview_rejects_bom_only_concurrent_change(self):
        self.bom()
        actual = read_registry_document
        def read_and_change(path):
            result = actual(path)
            path.write_bytes(self.original)
            return result
        with patch("grokbot_mcp.registry_prefixes.read_document", side_effect=read_and_change):
            with self.assertRaisesRegex(LocalError, "^input_changed$"):
                registry_prefixes.preview(self.registry, WORKSPACE_ID, write_to=self.base / "copy.json")
        self.assertFalse((self.base / "copy.json").exists())

    def test_migration_hashes_raw_bytes_and_refuses_bom_only_change(self):
        python = fake_install(self.base / "fictional-install")
        self.bom()
        plan = migration.plan(self.registry, WORKSPACE_ID, python)
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(plan["registry_sha256"], digest(BOM + self.original))
        self.registry.write_bytes(self.original)
        changed = migration.plan(self.registry, WORKSPACE_ID, python)
        self.assertNotEqual(plan["plan_sha256"], changed["plan_sha256"])
        with self.assertRaises(LocalError):
            migration.apply_plan(self.registry, WORKSPACE_ID, python, confirm=plan["plan_sha256"])
        self.assertFalse((self.base / "migrations").exists())

    def test_registration_accepts_bom_but_rejects_bom_only_concurrent_edit(self):
        empty = b'{"schema_version":1,"workspaces":[]}'
        answers = [str(self.root), "src/counter.py", "", "src/do_not_touch.py", "python_unittest",
                   "python -B -m unittest", "y", "n"]
        self.registry.write_bytes(BOM + empty)
        with patch("builtins.input", side_effect=answers), redirect_stdout(io.StringIO()):
            setup_wizard.register(self.registry, "fictional-added")
        self.assertFalse(self.registry.read_bytes().startswith(BOM))
        self.assertFalse(load_registry(self.registry)["fictional-added"].writes_enabled)
        self.registry.write_bytes(BOM + empty)
        values = iter(answers)
        def answer(_prompt):
            self.registry.write_bytes(empty)
            return next(values)
        with patch("builtins.input", side_effect=answer), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "^registry_changed_retry$"):
                setup_wizard.register(self.registry, "fictional-added")
        self.assertEqual(self.registry.read_bytes(), empty)

    def test_allow_writes_guard_uses_raw_bytes_with_bom(self):
        raw = json.loads(self.original)
        raw["workspaces"][0]["writes_enabled"] = False
        self.original = json.dumps(raw).encode("utf-8")
        self.bom()
        def change_bom(_prompt):
            self.registry.write_bytes(self.original)
            return True
        with patch("grokbot_mcp.setup_wizard.yes", side_effect=change_bom):
            with self.assertRaisesRegex(ValueError, "^registry_changed_retry$"):
                setup_wizard.allow_writes(self.registry, WORKSPACE_ID)

    def test_bom_does_not_relax_duplicate_nonfinite_or_evidence_checks(self):
        for raw in (b'{"schema_version":1,"schema_version":1,"workspaces":[]}',
                    b'{"schema_version":1,"workspaces":[],"invalid":NaN}',
                    BOM + self.original):
            with self.subTest(raw=raw[:50]):
                self.registry.write_bytes(BOM + raw)
                with self.assertRaises(RegistryError):
                    load_registry(self.registry)
                with self.assertRaises(LocalError):
                    read_registry_document(self.registry)
        with self.assertRaises(LocalError):
            parse_json(BOM + b'{"status":"succeeded"}')

    def test_size_limit_counts_bom_bytes(self):
        raw = b'{"schema_version":1,"workspaces":[]}'
        raw += b" " * (MAX_BYTES - len(raw))
        self.registry.write_bytes(BOM + raw)
        with self.assertRaisesRegex(RegistryError, "^ledger_too_large$"):
            load_registry(self.registry)
        with self.assertRaisesRegex(LocalError, "^ledger_too_large$"):
            read_registry_document(self.registry)
