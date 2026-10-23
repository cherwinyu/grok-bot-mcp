"""Offline onboarding tests; never access real credentials or webhooks."""
import contextlib
import getpass
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from grokbot_mcp import init_credentials, setup_wizard as wizard
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.facade import install_transport
from grokbot_mcp.registry import load_registry
from tests.support import make_workspace, WORKSPACE_ID


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(install_backend, None, None)
        self.addCleanup(install_transport, None)
        self.base = Path(self.temp.name)
        self.root = make_workspace(self.base)
        self.registry = self.base / "registry.json"
        self.args = ["--registry", str(self.registry), "--workspace", WORKSPACE_ID]
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def test_generate_has_actual_paths_and_no_confirmation_side_effects(self):
        confirmation = self.root / ".bridge/validate_routine_status.json"
        confirmation.unlink()
        self.assertEqual(wizard.main(self.args + ["--generate-only"]), 0)
        output = self.base / "setup" / WORKSPACE_ID
        text = (output / "routine-setup.md").read_text(encoding="utf-8")
        self.assertIn(str(self.root), text)
        self.assertIn("GROKBOT_MCP_REGISTRY", text)
        self.assertIn("<execution_id>", text)
        self.assertNotIn("C:\\ExamplePython", text)
        self.assertNotIn("## Local confirmation", text)
        self.assertFalse(confirmation.exists())
        config = json.loads((output / "mcp-config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["mcpServers"]["grokbot-mcp"]["env"]["GROKBOT_MCP_REGISTRY"], str(self.registry))

    def test_check_is_read_only_and_never_sends(self):
        original = self.registry.read_bytes()
        with patch("grokbot_mcp.transport.webhook.post_once", side_effect=AssertionError("network")):
            self.assertEqual(wizard.main(self.args + ["--check"]), 0)
        self.assertEqual(self.registry.read_bytes(), original)
        self.assertFalse((self.base / "setup").exists())

    def test_missing_confirmation_is_incomplete(self):
        (self.root / ".bridge/execute_routine_status.json").unlink()
        self.assertEqual(wizard.main(self.args + ["--check"]), 1)

    def test_resume_does_not_enable_switch_or_invent_confirmation(self):
        (self.root / ".bridge/validate_routine_status.json").unlink()
        with patch("builtins.input", side_effect=["n"] * 4):
            self.assertEqual(wizard.main(self.args), 1)
        self.assertFalse(json.loads((self.base / "execution_switch.json").read_text(encoding="utf-8"))["enabled"])
        self.assertFalse((self.root / ".bridge/validate_routine_status.json").exists())

    def test_explicit_confirmation_and_existing_switch_preserved(self):
        (self.base / "execution_switch.json").write_text('{"enabled": true}', encoding="utf-8")
        with patch("builtins.input", side_effect=["n", "y", "n", "y"]):
            self.assertEqual(wizard.main(self.args), 0)
        self.assertTrue(json.loads((self.base / "execution_switch.json").read_text(encoding="utf-8"))["enabled"])
        self.assertIn("user_confirmed", (self.root / ".bridge/execute_routine_status.json").read_text(encoding="utf-8"))

    def test_register_and_cancel_preserve_registry(self):
        registry = self.base / "new" / "registry.json"
        answers = [str(self.root), "src/counter.py", "", "src/do_not_touch.py", "python_unittest", "python -m unittest", "y", "n"]
        with patch("builtins.input", side_effect=answers):
            wizard.register(registry, "new-workspace")
        workspace = load_registry(registry)["new-workspace"]
        self.assertFalse(workspace.writes_enabled)
        self.assertEqual(workspace.allowed_files, ("src/counter.py",))
        self.assertFalse((registry.parent / "execution_switch.json").exists())

    def test_invalid_id_does_not_write_or_prompt(self):
        with patch("builtins.input", side_effect=AssertionError("prompt")):
            with self.assertRaises(ValueError):
                wizard.register(self.registry, "../bad")

    def test_missing_target_and_invalid_registry_fail_closed(self):
        (self.root / "src/counter.py").unlink()
        self.assertEqual(wizard.main(self.args + ["--check"]), 1)
        self.registry.write_text("{}", encoding="utf-8")
        before = self.registry.read_bytes()
        self.assertEqual(wizard.main(self.args), 1)
        self.assertEqual(before, self.registry.read_bytes())

    def test_secret_input_never_falls_back_to_echo(self):
        with patch("getpass.getpass", side_effect=getpass.GetPassWarning), patch.object(init_credentials, "store_credential") as store:
            self.assertEqual(init_credentials.main(["--target", "example"]), 1)
            store.assert_not_called()

    def test_packaged_templates_match_documented_templates(self):
        root = Path(__file__).resolve().parents[1]
        from importlib.resources import files
        for phase in ("validate", "execute"):
            self.assertEqual(files("grokbot_mcp").joinpath("templates", phase + ".md").read_text(encoding="utf-8"),
                             (root / "routines" / (phase + ".md")).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
