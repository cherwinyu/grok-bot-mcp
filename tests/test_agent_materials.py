from __future__ import annotations
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from importlib.resources import files
from grokbot_mcp import agent_cli, setup_wizard, result_examples
from grokbot_mcp.local_safety import LocalError, current_hash
from tests.deployment_support import DeploymentCase
from tests.support import WORKSPACE_ID


class AgentEntryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "项目 agent 空格"

    def test_generation_defaults_to_preview_and_requires_confirmation(self):
        plan, _ = agent_cli.generation_plan(self.root)
        self.assertFalse(self.root.exists())
        with self.assertRaises(LocalError):
            agent_cli.generate(self.root, confirm="wrong")
        self.assertFalse(self.root.exists())
        result = agent_cli.generate(self.root, confirm=plan["plan_sha256"])
        self.assertTrue(result["check"]["ok"])
        self.assertFalse(result["mandatory_for_bridge"])
        self.assertFalse(result["check"]["prompt_executed"])

    def generate(self):
        return agent_cli.generate(self.root, confirm=agent_cli.generation_plan(self.root)[0]["plan_sha256"])

    def test_config_relative_base_not_process_working_directory(self):
        self.generate()
        prior = Path.cwd()
        try:
            os.chdir(self.root.parent)
            result = agent_cli.check(self.root / "agent.json", local_details=True)
        finally:
            os.chdir(prior)
        self.assertEqual(result["local_paths"]["prompt"], str(self.root / "dispatcher.md"))

    def test_missing_prompt_never_searches_or_downloads(self):
        self.generate()
        (self.root / "dispatcher.md").unlink()
        (self.root.parent / "dispatcher.md").write_text("# wrong file", encoding="utf-8")
        with self.assertRaises(LocalError) as error:
            agent_cli.check(self.root / "agent.json")
        self.assertEqual(error.exception.code, "agent_prompt_missing")
        self.assertFalse((self.root / "dispatcher.md").exists())

    def test_regeneration_recovers_missing_file_but_refuses_customized_file(self):
        self.generate()
        (self.root / "dispatcher.md").unlink()
        self.generate()
        (self.root / "dispatcher.md").write_text("# user customized", encoding="utf-8")
        with self.assertRaises(LocalError) as error:
            self.generate()
        self.assertEqual(error.exception.code, "agent_existing_file_changed")
        self.assertEqual((self.root / "dispatcher.md").read_text(encoding="utf-8"), "# user customized")

    def test_relative_escape_and_non_markdown_rejected(self):
        self.generate()
        config = self.root / "agent.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data["prompt_file"] = "../dispatcher.md"
        config.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(LocalError) as error:
            agent_cli.check(config)
        self.assertEqual(error.exception.code, "agent_relative_escape_rejected")
        data["prompt_file"] = "script.py"
        config.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(LocalError) as error:
            agent_cli.check(config)
        self.assertEqual(error.exception.code, "agent_prompt_format_unsupported")

    def test_optional_fault_does_not_claim_bridge_core_failed(self):
        with redirect_stdout(io.StringIO()) as out:
            code = agent_cli.main(["check", "--config", str(self.root / "missing.json")])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["mandatory_for_bridge"])


class MaterialConsistencyTest(DeploymentCase):
    def test_source_and_packaged_templates_are_identical(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "routines").glob("*.md"):
            packaged = files("grokbot_mcp").joinpath("templates", path.name)
            if packaged.is_file():
                self.assertEqual(path.read_bytes(), packaged.read_bytes(), path.name)

    def test_generated_material_has_resolved_claim_and_result_commands(self):
        for mode in ("reuse", "create", "migrate"):
            with self.subTest(mode=mode):
                output = setup_wizard.generate(self.registry, WORKSPACE_ID, mode)
                text = (output / "routine-setup.md").read_text(encoding="utf-8")
                self.assertIn("grokbot_mcp.result_cli", text)
                self.assertIn("--confirm-sha256", text)
                self.assertIn("--previous-staged-sha256", text)
                self.assertNotIn("{{", text)
                self.assertNotIn("C:\\ExampleWorkspace", text)
                self.assertNotIn("C:\\ExamplePython", text)
                self.assertIn("claim-evidence-v2", text)
                if mode != "create":
                    self.assertIn("claim_dynamic.py", text)

    def test_json_examples_match_executable_source(self):
        root = Path(__file__).resolve().parents[1]
        for name, value in result_examples.examples().items():
            self.assertEqual(json.loads((root / "examples/results" / (name + ".json")).read_text(encoding="utf-8")), value)
