"""Timeout policy regressions using only fictional, in-memory registrations."""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import observation, setup_wizard
from grokbot_mcp.manifest.store import ManifestError, prepare_manifest
from grokbot_mcp.registry import (
    MAX_TIMEOUT_SECONDS, RegistryError, _commands, _workspace, policy_fingerprint,
)
from grokbot_mcp.rerun_config import parse_rerun


def registration(cap=600, default=600):
    return dict(
        workspace_id="example-workspace", root=str(Path.cwd() / "fictional-workspace"),
        expected_head="a" * 40, allowed_files=["src/counter.py", "docs/guide.md"],
        forbidden_files=["private"], protected_file="private", writes_enabled=False,
        expected_machine="EXAMPLE-PC", validate_credential_target="fictional/validate",
        execute_credential_target="fictional/execute",
        test_commands=[dict(id="python_unittest", task_types=["code_change"],
                            timeout_cap=cap, default_timeout=default)],
    )


class TimeoutCeilingTest(unittest.TestCase):
    def test_registry_accepts_ceiling_and_smaller_default(self):
        self.assertEqual(MAX_TIMEOUT_SECONDS, 3600)
        for cap, default in ((1, 1), (600, 600), (3600, 600), (3600, 3600)):
            with self.subTest(cap=cap, default=default):
                command = _workspace(registration(cap, default)).test_commands[0]
                self.assertEqual((command.timeout_cap, command.default_timeout), (cap, default))

    def test_registry_rejects_invalid_bounds_and_types(self):
        pairs = [(3601, 600), (600, 601), (3600, 3601), (0, 1), (600, 0)]
        for value in (True, False, None, "3600", 3600.0, -1):
            pairs.extend(((value, 1), (3600, value)))
        for cap, default in pairs:
            with self.subTest(cap=cap, default=default), self.assertRaisesRegex(RegistryError, "registry_invalid"):
                _workspace(registration(cap, default))

    def test_legacy_fingerprint_matches_pre_change_hash(self):
        # Captured from this fixed 600/600 policy before raising the ceiling.
        self.assertEqual(policy_fingerprint(_workspace(registration())),
                         "452d27953e6c776e352a6d7e82f123aacea1a1035d6e1ceca51afab263b6d300")

    def prepare(self, cap, default, timeout):
        workspace = _workspace(registration(cap, default))
        stored = {}
        with patch("grokbot_mcp.ledger_archive.archived", return_value=False), \
             patch("grokbot_mcp.manifest.store.write_json_new", side_effect=lambda path, doc: stored.update(doc)), \
             patch("grokbot_mcp.manifest.store.read_json", side_effect=lambda path: (stored, "b" * 64)):
            result = prepare_manifest(
                workspace, task_type="code_change", task_description="Fictional timeout test.",
                allowed_files=["src/counter.py"], acceptance_criteria=["Timeout is bounded."],
                test_command_id="python_unittest", execution_timeout=timeout, baseline_commit=None,
            )
        self.assertFalse(result["webhook_sent"])
        self.assertFalse(result["execution_enabled"])
        return stored

    def test_task_default_and_explicit_timeout(self):
        for cap, default, supplied, expected in (
            (3600, 3600, None, 3600), (3600, 600, None, 600),
            (3600, 600, 3600, 3600), (600, 600, None, 600), (3600, 3600, 1, 1),
        ):
            with self.subTest(cap=cap, default=default, supplied=supplied):
                self.assertEqual(self.prepare(cap, default, supplied)["execution_timeout"], expected)

    def test_task_refuses_over_command_cap_and_invalid_values(self):
        for cap, value in ((600, 601), (3600, 3601), (3600, True), (3600, False),
                           (3600, "3600"), (3600, 3600.0), (3600, 0), (3600, -1)):
            with self.subTest(cap=cap, value=value), self.assertRaisesRegex(ManifestError, "invalid_timeout"):
                self.prepare(cap, 600, value)

    def test_deadline_uses_task_timeout_and_preserves_legacy_fallback(self):
        manifest = self.prepare(3600, 3600, None)
        record = {"request_attempted_at": "2026-01-01T00:00:00+00:00",
                  "execution_timeout": manifest["execution_timeout"]}
        for minute, exceeded in ((59, False), (60, True)):
            now = datetime(2026, 1, 1, minute // 60, minute % 60, tzinfo=timezone.utc)
            with patch.object(observation, "datetime", wraps=datetime) as clock:
                clock.now.return_value = now
                result = observation.deadline(record)
            self.assertEqual(result, {"deadline_at": "2026-01-01T01:00:00+00:00",
                                      "deadline_exceeded": exceeded})
        self.assertEqual(observation.deadline({"request_attempted_at": record["request_attempted_at"]})["deadline_at"],
                         "2026-01-01T00:10:00+00:00")

    def test_rerun_ceiling_defaults_and_registry_error_path(self):
        config = {"argv": ["python", "-B", "-m", "unittest"]}
        self.assertEqual(parse_rerun(config).timeout_seconds, 600)
        self.assertEqual(parse_rerun({**config, "timeout_seconds": 3600}).timeout_seconds, 3600)
        for value in (3601, True, False, "3600", 3600.0, None, 0, -1):
            invalid = {**config, "timeout_seconds": value}
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "rerun_timeout_invalid"):
                parse_rerun(invalid)
            command = registration()["test_commands"][0]
            command["rerun"] = invalid
            with self.subTest(value=value), self.assertRaisesRegex(RegistryError, "rerun_timeout_invalid"):
                _commands([command])

    def test_wizard_new_code_defaults_and_read_only_unchanged(self):
        root = Path.cwd() / "fictional-workspace"
        answers = [str(root), "src/counter.py", "", "private", "python_unittest",
                   "python -B -m unittest", "y", "n"]
        with redirect_stdout(io.StringIO()), patch("builtins.input", side_effect=answers), \
             patch.object(setup_wizard, "safe_path"), patch.object(Path, "exists", return_value=False), \
             patch.object(setup_wizard, "workspace_git_rejection", return_value=None), \
             patch.object(setup_wizard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "a" * 40)), \
             patch.object(setup_wizard, "resolve_exact_files", side_effect=lambda root, names, forbidden: names), \
             patch.object(setup_wizard, "write_json") as write:
            setup_wizard.register(root / "registry.json", "example-workspace")
        write.assert_called_once()
        workspace = _workspace(write.call_args.args[1]["workspaces"][0])
        self.assertFalse(workspace.writes_enabled)
        self.assertEqual((workspace.command("python_unittest").timeout_cap,
                          workspace.command("python_unittest").default_timeout), (3600, 3600))
        self.assertEqual((workspace.command("none").timeout_cap,
                          workspace.command("none").default_timeout), (180, 180))


if __name__ == "__main__":
    unittest.main()
