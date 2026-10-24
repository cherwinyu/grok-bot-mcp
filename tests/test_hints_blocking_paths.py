"""Presentation-only regressions with memory doubles; no live configuration."""
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import diagnostics, facade
from grokbot_mcp.execution import service as execution
from grokbot_mcp.registry import Workspace
from grokbot_mcp.validation import service as validation


CODE = "protected_or_forbidden_changed"
FILES = ["private/one.py", "private/二.py"]
TASK_ID = "11111111-1111-4111-8111-111111111111"


def workspace():
    return Workspace("fictional", Path("D:/fictional/not-accessed"), "a" * 40,
                     (), ("private",), "private", (), "memory-validate",
                     "memory-execute", True, "FICTIONAL", git_gate="advisory")


def preflight_doubles(stack, ws, git, error):
    values = {"_workspace": (ws, None, []), "git_snapshot": git, "git_error": error,
              "resolve_exact_files": None, "status_for": "missing", "execution_switch_enabled": False,
              "routine_readiness": {"submit_ready": False, "binding_confirmed": False, "error": "binding_unknown"},
              "public_binding": {}, "environment_report": {},
              "runtime_identity": {}, "runtime_drift": {"package_changed": False},
              "default_registry_path": Path("D:/fictional/not-accessed/registry.json")}
    for name, value in values.items():
        stack.enter_context(patch.object(facade, name, return_value=value))
    stack.enter_context(patch("grokbot_mcp.registry_check.check_workspace", return_value={"summary": {}}))
    stack.enter_context(patch.object(execution, "stale_execution_records", return_value=[]))
    stack.enter_context(patch("grokbot_mcp.routine_check.packets", return_value=[]))


class BlockingPathsTest(unittest.TestCase):
    def test_multiple_files_and_owner_advice(self):
        hint = diagnostics.protected_change_hint(CODE, FILES)["next_step"]
        for path in FILES:
            self.assertIn(path, hint)
        self.assertIn("其他会话", hint)
        self.assertIn("由改动所有者提交或处理后再提交任务", hint)
        self.assertNotIn("还原文件", hint)
        self.assertNotIn("advisory", hint)

    def test_ten_file_limit_and_remaining_count(self):
        paths = [f"private/file-{i:02}.py" for i in range(13)]
        hint = diagnostics.protected_change_hint(CODE, paths)["next_step"]
        for path in paths[:10]:
            self.assertIn(path, hint)
        for path in paths[10:]:
            self.assertNotIn(path, hint)
        self.assertIn("…及另外 3 个", hint)
        self.assertEqual(len(paths), 13)

    def test_missing_or_invalid_field_preserves_generic_advice(self):
        for paths in (None, [], "private/one.py", {}):
            self.assertEqual(diagnostics.protected_change_hint(CODE, paths),
                             {"next_step": diagnostics.CATALOG[CODE]["next_step"]})
        self.assertEqual(diagnostics.protected_change_hint("git_status_failed", FILES), {})

    def test_paths_are_relative_redacted_and_bounded(self):
        paths = ["D:/outside/marker.py", "../outside/marker.py", "\\\\host\\share\\marker.py",
                 "/outside/marker.py", "private/line\nmarker.py", "private/token=fictional-secret",
                 "private/" + "x" * 500, None, "private/中文.py"]
        report = diagnostics.public_report({"findings": [{"code": CODE}],
                                            "protected_or_forbidden_changes": paths})
        item = report["findings"][0]
        text = json.dumps(item, ensure_ascii=False)
        for marker in ("outside", "marker.py", "fictional-secret", "host", "share"):
            self.assertNotIn(marker, text)
        self.assertIn("[redacted-secret]", text)
        self.assertIn("private/中文.py", text)
        self.assertTrue(all(len(path) <= 240 for path in item["blocking_paths"]))

    def test_export_and_render_rebuild_hints_and_preserve_bounded_paths(self):
        paths = [f"private/file-{i:02}.py" for i in range(12)]
        raw = {"findings": [{"code": CODE, "next_step": "untrusted instruction"}],
               "protected_or_forbidden_changes": paths}
        public = diagnostics.public_report(raw)
        self.assertEqual(public, json.loads(diagnostics.serialize(public)))
        self.assertEqual(len(public["findings"][0]["blocking_paths"]), 10)
        self.assertNotIn("untrusted instruction", diagnostics.render(raw))
        self.assertIn("private/file-00.py", diagnostics.render(public))
        self.assertIn("…及另外 2 个", diagnostics.render(public))
        self.assertFalse(public["complete_for_purpose"])

    def test_preflight_appends_only_hint_preserving_blockers_and_readiness(self):
        for fields in ({"protected_or_forbidden_changes": FILES}, {}):
            with ExitStack() as stack:
                preflight_doubles(stack, workspace(), fields, CODE)
                actual = facade.preflight("fictional")
                with patch.object(facade, "protected_change_hint", return_value={}):
                    legacy = facade.preflight("fictional")
            hint = actual.pop("next_step")
            self.assertEqual(actual, legacy)
            self.assertIn(CODE, actual["blocking_reasons"])
            self.assertFalse(actual["local_ready"])
            if fields:
                self.assertEqual(actual["protected_or_forbidden_changes"], FILES)
                self.assertIn(FILES[0], hint)

    def test_prepare_task_preserves_original_fields(self):
        with ExitStack() as stack:
            preflight_doubles(stack, workspace(), {"protected_or_forbidden_changes": FILES}, CODE)
            result = facade.prepare_task("fictional", "code_change", "fictional", [], [], "unit")
        self.assertEqual(result["error"], CODE)
        self.assertEqual(result["protected_or_forbidden_changes"], FILES)
        self.assertIn(FILES[1], result["next_step"])
        self.assertFalse(result["webhook_sent"])

    def test_validation_refusal_never_loads_credentials_or_sends(self):
        with patch.object(validation, "routine_readiness", return_value={"submit_ready": True}), patch.object(
                validation, "load_manifest", return_value=({"workspace_id": "fictional", "lifecycle": "prepared"}, "a" * 64)), patch.object(
                validation, "git_snapshot", return_value={"protected_or_forbidden_changes": FILES}), patch.object(
                validation, "git_error", return_value=CODE):
            def forbidden():
                self.fail("refusal must not load credentials or send")
            result = validation.submit_validation(workspace(), TASK_ID, status_fn=lambda: "present",
                                                  load_fn=forbidden, transport=forbidden, webhook_enabled=True)
        self.assertEqual(result["error"], CODE)
        self.assertEqual(result["protected_or_forbidden_changes"], FILES)
        self.assertIn(FILES[0], result["next_step"])
        self.assertFalse(result["webhook_attempted"])

    def test_execution_refusal_and_missing_field(self):
        for fields in ({"protected_or_forbidden_changes": FILES}, {}):
            with patch.object(execution, "assess", return_value={"ok": False, "error": CODE, "git": fields}):
                result = execution.prepare_execution(workspace(), TASK_ID, [])
            self.assertEqual(result["error"], CODE)
            self.assertIn("其他会话", result["next_step"])
            self.assertFalse(result["webhook_attempted"])
            if fields:
                self.assertEqual(result["protected_or_forbidden_changes"], FILES)
                self.assertIn(FILES[0], result["next_step"])

    def test_unrelated_default_response_has_no_addition(self):
        with ExitStack() as stack:
            preflight_doubles(stack, workspace(), {}, None)
            result = facade.preflight("fictional")
        self.assertTrue(result["local_ready"])
        self.assertNotIn("next_step", result)
        report = diagnostics.public_report({"findings": [{"code": "git_status_failed"}],
                                            "protected_or_forbidden_changes": FILES})
        self.assertNotIn("blocking_paths", report["findings"][0])
        self.assertEqual(execution._protected_fields({}), {})

    def test_rerun_window_hint_bilingual(self):
        hint = diagnostics.finding("rerun_result_unverified")["next_step"]
        for text in ("before committing", "advancing HEAD", "执行完成后", "提交工作区前复跑", "不替换冻结证据"):
            self.assertIn(text, hint)


if __name__ == "__main__":
    unittest.main()
