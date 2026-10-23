"""Regression coverage for size-bounded advisory Git snapshots."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from grokbot_mcp import facade
from grokbot_mcp.security.baseline import capture_snapshot, execution_delta, same_snapshot
from grokbot_mcp.strict_json import MAX_BYTES
from tests.support import WORKSPACE_ID, git, make_advisory_workspace, make_workspace, write_proof


class AdvisorySnapshotSizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.addCleanup(facade.install_transport, None)

    def test_advisory_validation_ledger_stays_small_with_many_ignored_files(self) -> None:
        root = make_advisory_workspace(self.base)
        (self.base / "execution_switch.json").write_text('{"enabled": true}\n', encoding="utf-8")
        (root / ".gitignore").write_text("ignored_*.tmp\n", encoding="utf-8")
        git(root, "add", ".gitignore")
        git(root, "commit", "-m", "ignore generated fixtures")
        for index in range(500):
            (root / f"ignored_{index:04d}.tmp").write_text("ignored\n", encoding="utf-8")

        task = facade.prepare_task(
            WORKSPACE_ID,
            "code_change",
            "Add a fictional advisory label.",
            ["src/counter.py"],
            ["The existing test remains green."],
            "python_unittest",
        )
        self.assertIsNone(task.get("error"))
        self.assertEqual(
            facade.submit_validation_tool(WORKSPACE_ID, str(task["task_id"]))["http_status"],
            200,
        )
        write_proof(root, str(task["task_id"]), str(task["manifest_sha256"]), ["src/counter.py"])
        validation = facade.get_status(WORKSPACE_ID, task_id=str(task["task_id"]))["validation"]
        self.assertEqual(validation["status"], "succeeded")

        ledger_path = root / ".bridge" / "tasks_v2" / f"{task['task_id']}.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(ledger["verified_snapshot"]["ignored"], [])
        snapshot_bytes = json.dumps(ledger["verified_snapshot"], sort_keys=True).encode("utf-8")
        self.assertLess(len(snapshot_bytes), 4 * 1024)
        self.assertLess(ledger_path.stat().st_size, MAX_BYTES)

        execution = facade.prepare_execution_tool(WORKSPACE_ID, str(task["task_id"]))
        self.assertIsNone(execution.get("error"))
        self.assertNotEqual(execution.get("error"), "validation_ledger_unreadable")

    def test_strict_snapshot_still_contains_ignored_files_byte_for_byte(self) -> None:
        root = make_workspace(self.base)
        (root / ".gitignore").write_text("strict_ignored.tmp\n", encoding="utf-8")
        (root / "strict_ignored.tmp").write_text("ignored\n", encoding="utf-8")
        watched = ["src/counter.py"]

        default = capture_snapshot(root, watched)
        explicit = capture_snapshot(root, watched, include_ignored=True)

        self.assertIn("strict_ignored.tmp", default["ignored"])
        self.assertEqual(
            json.dumps(default, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8"),
            json.dumps(explicit, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8"),
        )

    def test_advisory_keeps_new_ignored_file_under_watched_directory(self) -> None:
        root = make_workspace(self.base)
        (root / ".gitignore").write_text("private/*.tmp\n", encoding="utf-8")
        (root / "private").mkdir()
        git(root, "add", ".gitignore")
        git(root, "commit", "-m", "ignore private fixtures")

        baseline = capture_snapshot(root, ["private"], include_ignored=False)
        (root / "private" / "secret.tmp").write_text("ignored\n", encoding="utf-8")
        current = capture_snapshot(root, ["private"], include_ignored=False)

        self.assertEqual(current["ignored"], ["private/secret.tmp"])
        self.assertEqual(execution_delta(baseline, current), ["private/secret.tmp"])
        self.assertFalse(same_snapshot(baseline, current))

    def test_missing_ignored_is_compatible_with_empty_but_not_entries(self) -> None:
        snapshot = {
            "git_head_text": "a" * 40,
            "diff_files": [],
            "untracked": [],
            "status_paths": [],
            "file_hashes": {},
        }
        with_empty = {**snapshot, "ignored": []}
        with_entry = {**snapshot, "ignored": ["private/secret.tmp"]}
        self.assertTrue(same_snapshot(snapshot, with_empty))
        self.assertFalse(same_snapshot(snapshot, with_entry))
        self.assertEqual(execution_delta(snapshot, with_empty), [])
        self.assertEqual(execution_delta(snapshot, with_entry), ["private/secret.tmp"])


if __name__ == "__main__":
    unittest.main()
