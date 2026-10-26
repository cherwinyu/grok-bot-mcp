"""First-registration CLI contracts using owned Git fixtures and memory credentials."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from grokbot_mcp import setup_wizard as wizard
from grokbot_mcp.credentials.blob import unpack_blob
from grokbot_mcp.registry import load_registry
from grokbot_mcp.security.prefixes import blocked
from tests.support import git, temporary_directory


class WizardNoninteractiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory(prefix="wizard-fictional-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "workspace"
        (self.root / "src").mkdir(parents=True)
        for name in ("a.py", "protected.py"):
            (self.root / "src" / name).write_text("VALUE = 1\n", encoding="utf-8")
        git(self.root, "init")
        git(self.root, "add", "src")
        git(self.root, "commit", "-m", "fictional fixture")
        self.registry = self.base / "config" / "registry.json"
        self.enterContext(patch.object(wizard, "default_registry_path", return_value=self.registry))
        self.args = ["--registry", str(self.registry), "--workspace", "fictional-workspace"]
        self.fields = ["--root", str(self.root), "--protected-file", "src/protected.py",
                       "--test-command", "unit", "--test-instruction", "python -B -m unittest"]
        self.scope = ["--allowed-prefix", "src/:.py", "--prefix-ceiling", "src/:.py,.md"]
        self.enterContext(patch.object(wizard, "inspect_environment", return_value={"ready": True, "bridge_version": "fictional"}))
        self.enterContext(patch.object(wizard, "status_for", return_value="missing"))
        self.store = self.enterContext(patch.object(wizard.init_credentials, "store_credential"))
        self.enterContext(patch("grokbot_mcp.credentials.windows._apis", side_effect=AssertionError("real credential access")))
        self.enterContext(patch("grokbot_mcp.transport.webhook.post_once", side_effect=AssertionError("network")))
        self.enterContext(patch.object(wizard.init_credentials.getpass, "getpass", side_effect=AssertionError("secret prompt")))

    def invoke(self, extra, *, args=None, answers=None):
        output = io.StringIO()
        effect = AssertionError("unexpected input") if answers is None else answers
        with patch("builtins.input", side_effect=effect) as prompt, redirect_stdout(output):
            code = wizard.main((self.args if args is None else args) + list(extra))
        if answers is None:
            prompt.assert_not_called()
        return code, output.getvalue()

    def register(self, extra=()):
        return self.invoke([*self.fields, *self.scope, "--yes", *extra])

    def item(self):
        return json.loads(self.registry.read_bytes())["workspaces"][0]

    def test_all_options_register_normalized_advisory_policy_without_prompts(self):
        code, output = self.register(["--local-validation", "--allow-writes", "--claim-fingerprint",
            "--allowed-prefix", "src/:.py", "--allowed-prefix", "docs/:.md",
            "--prefix-ceiling", "docs/:.md", "--other-prefix", "other"])
        self.assertEqual(code, 0, output)
        item = self.item()
        ws = load_registry(self.registry)["fictional-workspace"]
        self.assertEqual(item["git_gate"], "advisory")
        self.assertEqual(item["allowed_files"], [])
        self.assertEqual(item["allowed_prefixes"], [{"prefix": "docs/", "exts": [".md"]}, {"prefix": "src/", "exts": [".py"]}])
        self.assertEqual(item["prefix_ceiling"][1], {"prefix": "src/", "exts": [".md", ".py"]})
        self.assertTrue(ws.local_validation)
        self.assertTrue(ws.writes_enabled)
        self.assertTrue(ws.routine_claim_fingerprint)
        self.assertEqual(ws.other_prefixes, ("other",))
        self.assertTrue(blocked(ws, "src/protected.py"))
        self.assertIn('"writes_enabled": true', output)
        self.assertIn("REGISTRATION_SAVED", output)
        self.assertFalse((self.registry.parent / "execution_switch.json").exists())
        self.assertFalse((self.root / ".bridge").exists())
        packet = (self.registry.parent / "setup/fictional-workspace/routine-setup.md").read_text(encoding="utf-8")
        self.assertIn("--expect-fingerprint", packet)
        self.store.assert_not_called()

    def test_exact_file_only_and_safe_defaults(self):
        code, output = self.invoke([*self.fields, "--allow-file", "src/a.py", "--yes"])
        self.assertEqual(code, 0, output)
        self.assertEqual(self.item()["allowed_files"], ["src/a.py"])
        for key in ("allowed_prefixes", "prefix_ceiling", "local_validation", "routine_claim_fingerprint"):
            self.assertNotIn(key, self.item())
        self.assertFalse(self.item()["writes_enabled"])
        packet = (self.registry.parent / "setup/fictional-workspace/routine-setup.md").read_text(encoding="utf-8")
        self.assertNotIn("--expect-fingerprint", packet)

    def test_exact_exceptions_and_repeated_files_remain_validated(self):
        code, output = self.register(["--allow-file", "src/a.py"])
        self.assertEqual(code, 0, output)
        self.assertEqual(load_registry(self.registry)["fictional-workspace"].allowed_files, ("src/a.py",))

    def test_ceiling_without_active_prefixes_grants_no_files(self):
        code, output = self.invoke([*self.fields, "--prefix-ceiling", "src/:.py", "--yes"])
        self.assertEqual(code, 0, output)
        ws = load_registry(self.registry)["fictional-workspace"]
        self.assertEqual(ws.allowed_files, ())
        self.assertEqual(ws.allowed_prefixes, ())

    def test_missing_required_fields_never_write(self):
        for option in ("--root", "--protected-file", "--test-command", "--test-instruction"):
            fields = self.fields.copy()
            pos = fields.index(option)
            del fields[pos:pos + 2]
            with self.subTest(option=option):
                code, output = self.invoke([*fields, *self.scope, "--yes"])
                self.assertEqual(code, 1)
                self.assertIn("invalid_arguments", output)
                self.assertFalse(self.registry.parent.exists())
        for fields, args in ((self.fields, self.args), (self.fields + self.scope, self.args[:2])):
            code, output = self.invoke([*fields, "--yes"], args=args)
            self.assertEqual(code, 1)
            self.assertIn("invalid_arguments", output)
            self.assertFalse(self.registry.parent.exists())

    def test_invalid_prefix_and_ceiling_refuse_before_any_write(self):
        for option in ("--allowed-prefix", "--prefix-ceiling"):
            for value in ("src/", "../src/:.py", "src/:py", "src/:.*", "src/.git/:.py", "src\\sub/:.py", "src/:.py,"):
                with self.subTest(option=option, value=value):
                    code, output = self.invoke([*self.fields, "--allow-file", "src/a.py", option, value, "--yes"])
                    self.assertEqual(code, 1)
                    self.assertTrue("invalid_arguments" in output or "registry_invalid" in output, output)
                    self.assertFalse(self.registry.parent.exists())

    def test_outside_ceiling_and_strict_local_validation_refused(self):
        cases = [(["--allowed-prefix", "docs/:.py", "--prefix-ceiling", "src/:.py"], "prefix_outside_ceiling"),
                 (["--allowed-prefix", "src/:.md", "--prefix-ceiling", "src/:.py"], "prefix_outside_ceiling"),
                 (["--allow-file", "src/a.py", "--git-gate", "strict", "--local-validation"], "local_validation_invalid"),
                 ([*self.scope, "--git-gate", "strict"], "invalid_arguments")]
        for scope, error in cases:
            with self.subTest(error=error):
                code, output = self.invoke([*self.fields, *scope, "--yes"])
                self.assertEqual(code, 1)
                self.assertIn(error, output)
                self.assertFalse(self.registry.parent.exists())

    def test_exact_paths_cannot_bypass_existing_refusals(self):
        cases = [("--root", "relative", "absolute_root_required"),
                 ("--allow-file", "src/protected.py", "forbidden_file"),
                 ("--allow-file", "src/missing.py", "target_file_missing"),
                 ("--allow-file", "../outside.py", "traversal"),
                 ("--protected-file", "src/missing.py", "target_file_missing"),
                 ("--test-command", "none", "test_command_required")]
        for option, value, error in cases:
            with self.subTest(option=option, value=value):
                code, output = self.register([option, value])
                self.assertEqual(code, 1)
                self.assertIn(error, output)
                self.assertFalse(self.registry.parent.exists())

    def new_options(self):
        return [["--allowed-prefix", "src/:.py"], ["--prefix-ceiling", "src/:.py"],
                ["--local-validation"], ["--claim-fingerprint"], ["--yes"],
                ["--root", str(self.root)], ["--allow-file", "src/a.py"],
                ["--protected-file", "src/protected.py"], ["--test-command", "unit"],
                ["--test-instruction", "unit tests"], ["--other-prefix", "other"],
                ["--validate-url-env", "FICTIONAL_URL"], ["--validate-key-env", "FICTIONAL_KEY"],
                ["--execute-url-env", "FICTIONAL_URL"], ["--execute-key-env", "FICTIONAL_KEY"]]

    def test_check_and_generate_only_reject_all_new_options(self):
        for mode in ("--check", "--generate-only"):
            for options in self.new_options():
                with self.subTest(mode=mode, options=options):
                    code, output = self.invoke([mode, *options])
                    self.assertEqual(code, 1)
                    self.assertIn("invalid_arguments", output)
                    self.assertFalse(self.registry.parent.exists())

    def test_existing_registration_rejects_new_options_preserving_bytes(self):
        self.assertEqual(self.register()[0], 0)
        before = self.registry.read_bytes()
        for options in self.new_options():
            with self.subTest(options=options):
                code, output = self.invoke(options)
                self.assertEqual(code, 1)
                self.assertIn("invalid_arguments", output)
                self.assertEqual(self.registry.read_bytes(), before)

    def test_selecting_existing_workspace_interactively_still_rejects_options(self):
        self.assertEqual(self.register()[0], 0)
        before = self.registry.read_bytes()
        code, output = self.invoke(["--local-validation"], args=self.args[:2], answers=["fictional-workspace"])
        self.assertEqual(code, 1)
        self.assertIn("invalid_arguments", output)
        self.assertEqual(before, self.registry.read_bytes())

    def test_explicit_strict_dirty_hint_and_clean_absence(self):
        for dirty in (None, "tracked", "index", "untracked"):
            with self.subTest(dirty=dirty):
                if dirty == "tracked":
                    (self.root / "src/a.py").write_text("VALUE = 2\n", encoding="utf-8")
                if dirty == "index":
                    git(self.root, "add", "src/a.py")
                    (self.root / "src/a.py").write_text("VALUE = 1\n", encoding="utf-8")
                    self.assertEqual(git(self.root, "diff", "HEAD", "--name-only"), "")
                if dirty == "untracked":
                    (self.root / "src/a.py").write_text("VALUE = 1\n", encoding="utf-8")
                    git(self.root, "add", "src/a.py")
                    (self.root / "notes.md").write_text("fictional", encoding="utf-8")
                registry = self.base / str(dirty) / "registry.json"
                code, output = self.invoke([*self.fields, "--allow-file", "src/a.py", "--git-gate", "strict", "--yes"],
                    args=["--registry", str(registry), "--workspace", "fictional-workspace"])
                self.assertEqual(code, 0, output)
                self.assertEqual("建议使用 --git-gate advisory" in output, dirty is not None)
                self.assertEqual(load_registry(registry)["fictional-workspace"].git_gate, "strict")

    def test_partial_interactive_fields_skip_only_supplied_prompts(self):
        with patch.object(wizard, "check", return_value=False):
            code, output = self.invoke(["--root", str(self.root), "--allow-file", "src/a.py",
                "--test-command", "unit", "--allow-writes"],
                answers=["src/protected.py", "python -B -m unittest", "y", "n", "n", "n", "n"])
        self.assertEqual(code, 1, output)  # Binding/credential setup remains incomplete.
        self.assertTrue(self.item()["writes_enabled"])

    def test_no_yes_preserves_original_registration_questions(self):
        answers = [str(self.root), "src/a.py", "", "src/protected.py", "unit", "python -B -m unittest", "y", "n"]
        with patch("builtins.input", side_effect=answers) as prompt, redirect_stdout(io.StringIO()):
            wizard.register(self.registry, "fictional-workspace")
        self.assertEqual(prompt.call_count, 8)
        self.assertFalse(self.item()["writes_enabled"])

    def test_yes_does_not_implicitly_confirm_binding_or_replace_credentials(self):
        for option in ("--reconfirm-bindings", "--replace-credentials"):
            code, output = self.register([option])
            self.assertEqual(code, 1)
            self.assertIn("invalid_arguments", output)
            self.assertFalse(self.registry.parent.exists())

    def test_env_credentials_reuse_reader_and_never_echo_values(self):
        env = {"FICTIONAL_VALIDATE_URL": "https://example.invalid/validate-wizard",
               "FICTIONAL_VALIDATE_KEY": "fictional-validate-key",
               "FICTIONAL_EXECUTE_URL": "https://example.invalid/execute-wizard",
               "FICTIONAL_EXECUTE_KEY": "fictional-execute-key"}
        flags = [part for phase in ("validate", "execute") for part in
                 (f"--{phase}-url-env", f"FICTIONAL_{phase.upper()}_URL", f"--{phase}-key-env", f"FICTIONAL_{phase.upper()}_KEY")]
        with patch.dict(os.environ, env), patch.object(
                wizard.init_credentials, "read_credentials", wraps=wizard.init_credentials.read_credentials) as reader:
            code, output = self.register(flags)
        self.assertEqual(code, 0, output)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(self.store.call_count, 2)
        for phase, call in zip(("validate", "execute"), self.store.call_args_list):
            self.assertEqual(call.args[0], f"grokbot-mcp/fictional-workspace/{phase}")
            self.assertEqual(unpack_blob(call.args[1]), (env[f"FICTIONAL_{phase.upper()}_URL"], env[f"FICTIONAL_{phase.upper()}_KEY"]))
        for secret in env.values():
            self.assertNotIn(secret, output + self.registry.read_text(encoding="utf-8"))
        self.assertFalse((self.root / ".bridge").exists())

    def test_invalid_credential_inputs_refuse_without_writes_or_secret_echo(self):
        cases = [(["--validate-url-env", "FICTIONAL_URL"], {}, "credential_env_pair_required"),
                 (["--validate-url-env", "https://example.invalid/secret", "--validate-key-env", "FICTIONAL_KEY"], {}, "credential_env_name_invalid"),
                 (["--execute-url-env", "FICTIONAL_URL", "--execute-key-env", "FICTIONAL_KEY"], {}, "credential_env_missing"),
                 (["--execute-url-env", "FICTIONAL_URL", "--execute-key-env", "FICTIONAL_KEY"],
                  {"FICTIONAL_URL": "http://example.invalid/secret", "FICTIONAL_KEY": "fictional-private-key"}, "credential_rejected")]
        for flags, env, error in cases:
            with self.subTest(error=error), patch.object(wizard.init_credentials.os, "environ", env):
                code, output = self.register(flags)
                self.assertEqual(code, 1)
                self.assertIn(error, output)
                self.assertNotIn("example.invalid/secret", output)
                self.assertNotIn("fictional-private-key", output)
                self.assertFalse(self.registry.parent.exists())
        self.store.assert_not_called()

    def test_existing_credentials_preserved_and_store_failure_is_redacted(self):
        env = {"FICTIONAL_URL": "https://example.invalid/wizard", "FICTIONAL_KEY": "fictional-secret-key"}
        flags = ["--execute-url-env", "FICTIONAL_URL", "--execute-key-env", "FICTIONAL_KEY"]
        with patch.dict(os.environ, env), patch.object(wizard, "status_for", return_value="present"):
            code, output = self.register(flags)
        self.assertEqual(code, 0, output)
        self.store.assert_not_called()
        other_registry = self.base / "failed-store" / "registry.json"
        with patch.dict(os.environ, env):
            self.store.side_effect = OSError(env["FICTIONAL_URL"] + env["FICTIONAL_KEY"])
            code, output = self.invoke([*self.fields, *self.scope, "--yes", *flags],
                args=["--registry", str(other_registry), "--workspace", "fictional-workspace"])
        self.assertEqual(code, 1)
        self.assertIn("credential_rejected", output)
        for value in env.values():
            self.assertNotIn(value, output)
        self.assertTrue(other_registry.exists())  # Earlier registration remains resumable.
        self.assertFalse((self.root / ".bridge").exists())

    def test_registry_race_still_refused_before_save(self):
        original = b'{"schema_version":1,"workspaces":[]}'
        self.registry.parent.mkdir()
        self.registry.write_bytes(original)

        def answer(_prompt):
            self.registry.write_bytes(original + b"\n")
            return "y"

        with patch("builtins.input", side_effect=answer), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, "registry_changed_retry"):
                wizard.register(self.registry, "fictional-workspace", root=self.root,
                    allowed_files=["src/a.py"], protected_file="src/protected.py",
                    test_command="unit", test_instruction="python -B -m unittest", writes_enabled=True)
        self.assertEqual(self.registry.read_bytes(), original + b"\n")


if __name__ == "__main__":
    unittest.main()
