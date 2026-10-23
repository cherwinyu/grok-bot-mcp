"""Synthetic registration diagnostics; no real registry, credentials or sends."""
import io
import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import admin_cli, facade, registry_check
from grokbot_mcp.registry import TestCommand, Workspace
from grokbot_mcp.rerun_config import RerunConfig


class RegistryCheckTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="registry-check-fictional-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ws = Workspace("fictional", self.root, "a" * 40, (), ("private",), "private",
                            (), "memory-validate", "memory-execute", False, "FICTIONAL")

    def check(self, argv, rerun=None):
        command = TestCommand("fictional-test", ("code",), 60, 30, argv=tuple(argv), rerun=rerun)
        return registry_check.check_workspace(replace(self.ws, test_commands=(command,)))

    def codes(self, report):
        return {item["code"] for item in report["items"]}

    def test_existing_module_and_file(self):
        (self.root / "tests").mkdir()
        (self.root / "tests/test_one.py").write_text("raise AssertionError('must not import')", encoding="utf-8")
        for runner, target in (("unittest", "tests.test_one"), ("pytest", "tests/test_one.py")):
            report = self.check(["python", "-m", runner, target])
            self.assertEqual(report["items"][-1]["status"], "ok")

    def test_missing_module(self):
        self.assertIn("unittest_module_missing", self.codes(self.check(["python", "-m", "unittest", "tests.absent"])))

    def test_missing_test_file(self):
        report = self.check(["python", "-m", "pytest", "tests/absent.py"])
        self.assertEqual(report["items"][-1]["code"], "test_file_missing")

    def test_cwd_must_be_directory(self):
        (self.root / "file").write_bytes(b"fictional")
        self.assertIn("cwd_missing", self.codes(self.check([], RerunConfig(("python",), cwd="file"))))

    def test_missing_cwd(self):
        report = self.check([], RerunConfig(("python",), cwd="absent"))
        self.assertIn("cwd_missing", self.codes(report))

    def test_missing_absolute_executable_inside_root(self):
        self.assertIn("executable_missing", self.codes(self.check([str(self.root / "absent.exe")])))

    def test_bare_executable(self):
        self.assertIn("executable_bare_name", self.codes(self.check(["python"])))

    def test_no_execution_import_or_path_search(self):
        (self.root / "tests").mkdir()
        (self.root / "tests/sentinel.py").write_text("raise AssertionError('imported')", encoding="utf-8")
        with patch("subprocess.Popen") as process, patch(
                "importlib.util.find_spec") as spec, patch("shutil.which") as which, patch("importlib.import_module") as imports:
            self.check(["python", "-m", "unittest", "tests.sentinel"])
        for mock in (process, imports, spec, which):
            mock.assert_not_called()

    def test_lexical_refusals_do_not_probe(self):
        for value in ("../outside.py", "Z:/outside.py", "Z:outside.py", "private/secret.py", "a/.bridge/x.py", "a:stream"):
            with self.subTest(value=value), patch.object(registry_check, "is_reparse_point") as probe:
                result = registry_check._probe(self.ws, self.root, value)
                self.assertEqual(result[0], "unchecked")
                probe.assert_not_called()

    def test_reparse_parent_stops_before_descendant(self):
        link = self.root / "link"
        link.mkdir()
        seen = []
        def reparse(path):
            seen.append(path)
            return path == link
        with patch.object(registry_check, "is_reparse_point", side_effect=reparse):
            self.assertEqual(registry_check._probe(self.ws, self.root, "link/secret.py"), ("unchecked", "reparse"))
        self.assertNotIn(link / "secret.py", seen)

    def test_pythonpath_package_and_rerun(self):
        (self.root / "src/pkg/mod").mkdir(parents=True)
        (self.root / "src/pkg/mod/__init__.py").write_text("", encoding="utf-8")
        report = self.check([], RerunConfig(("python", "-m", "unittest", "pkg.mod"), env=(("PYTHONPATH", "src"),)))
        self.assertEqual(report["items"][-1]["status"], "ok")
        self.assertEqual(report["items"][-1]["source"], "rerun.argv")

    def test_external_pythonpath_is_not_probed(self):
        report = self.check([], RerunConfig(("python", "-m", "unittest", "pkg.absent"),
                                           env=(("PYTHONPATH", "Z:/outside"),)))
        self.assertIn("path_outside_root", self.codes(report))
        self.assertEqual(report["items"][-1]["status"], "unchecked")

    def test_pythonpath_dot_means_cwd(self):
        self.assertEqual(registry_check._probe(self.ws, self.root, ".", directory=True), ("ok", "path_exists"))

    def test_relative_executable(self):
        (self.root / "bin").mkdir()
        (self.root / "bin/python.exe").write_bytes(b"fictional")
        report = self.check(["bin/python.exe"])
        self.assertEqual(report["items"][1]["status"], "ok")

    def test_placeholder_and_options_skipped(self):
        report = self.check(["python", "-m", "pytest", "-q", "{changed_files}"])
        self.assertEqual(len(report["items"]), 2)

    def test_preflight_addition_does_not_change_readiness(self):
        command = TestCommand("missing", ("code",), 60, 30, argv=("python", "-m", "unittest", "tests.absent"))
        ws = replace(self.ws, test_commands=(command,))
        with ExitStack() as stack:
            mocks = {"_workspace": (ws, None, []), "git_snapshot": {}, "git_error": None,
                     "resolve_exact_files": None, "status_for": "missing", "execution_switch_enabled": False,
                     "routine_readiness": {"submit_ready": False, "binding_confirmed": False, "error": "binding_unknown"},
                     "runtime_identity": {}, "runtime_drift": {"package_changed": False}}
            for name, result in mocks.items():
                stack.enter_context(patch.object(facade, name, return_value=result))
            stack.enter_context(patch("grokbot_mcp.execution.service.stale_execution_records", return_value=[]))
            stack.enter_context(patch("grokbot_mcp.routine_check.packets", return_value=[]))
            before = facade.preflight(ws.workspace_id)
            stack.enter_context(patch.object(registry_check, "check_workspace", return_value={"summary": {}}))
            after = facade.preflight(ws.workspace_id)
        self.assertTrue(before["local_ready"])
        self.assertEqual(before["test_command_check"]["missing_count"], 1)
        before.pop("test_command_check")
        after.pop("test_command_check")
        self.assertEqual(before, after)

    def test_cli_cp1252_json_and_all_workspaces(self):
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding="cp1252")
        with patch.object(registry_check, "load_registry", return_value={"fictional": self.ws}), patch("sys.stdout", output):
            status = admin_cli.main(["registry-check", "--registry", str(self.root / "synthetic.json")])
        output.flush()
        report = json.loads(buffer.getvalue().decode("utf-8"))
        self.assertEqual(status, 0)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["workspaces"][0]["workspace_id"], "fictional")

    def test_actual_synthetic_registry_is_unchanged(self):
        from tests.test_u2a_contract import entry
        item = entry()
        item["root"] = str(self.root)
        item["test_commands"][0]["argv"] = ["python", "-m", "unittest", "tests.absent", "{changed_files}"]
        registry = self.root / "synthetic.json"
        original = json.dumps({"schema_version": 1, "workspaces": [item]}).encode("utf-8")
        registry.write_bytes(original)
        result = registry_check.check(registry, item["workspace_id"])
        self.assertEqual(result["workspaces"][0]["summary"]["missing_count"], 1)
        self.assertEqual(registry.read_bytes(), original)
        self.assertEqual(list(self.root.iterdir()), [registry])

    def test_installation_advice_bilingual_and_same_code(self):
        from grokbot_mcp.diagnostics import CATALOG
        advice = str(CATALOG["installation_mismatch"])
        self.assertIn("pip install -e .", advice)
        self.assertIn("wheel", advice)
        self.assertIn("可编辑安装", advice)


if __name__ == "__main__":
    unittest.main()
