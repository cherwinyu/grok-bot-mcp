"""CLI-only summaries over fictional Git workspaces; no real credentials or sends."""
import copy
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import diagnostics, facade
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.security.gitcheck import git_error, git_snapshot
from tests.support import WORKSPACE_ID, git, make_workspace, temporary_directory


class ReadinessSummaryTest(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = temporary_directory(prefix="summary-fictional-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(install_backend, None, None)
        self.addCleanup(facade.install_transport, None)
        self.base = Path(self.temp.name)
        self.root = make_workspace(self.base, writes=False)
        self.registry = self.base / "registry.json"
        self.head = git(self.root, "rev-parse", "HEAD")
        self.inspection = patch("grokbot_mcp.setup_environment.inspect_environment", return_value={
            "ready": True, "codes": [], "python_version": "3.12.0", "bridge_version": "0.1.5"})
        self.inspection.start()
        self.addCleanup(self.inspection.stop)

    def collect(self):
        return diagnostics.collect(self.registry, WORKSPACE_ID, export=True)

    def configure(self, **changes):
        document = json.loads(self.registry.read_text(encoding="utf-8"))
        document["workspaces"][0].update(changes)
        self.registry.write_text(json.dumps(document), encoding="utf-8")

    def finding(self, report, code):
        return next(item for item in report["findings"] if item["code"] == code)

    def assert_presentation_only(self, report):
        # Compare the same fixture with summary presentation disabled.
        with patch.object(diagnostics, "_change_summary", return_value={}):
            legacy = self.collect()
        stripped = copy.deepcopy(report)
        for item in stripped["findings"]:
            item.pop("includes_ignored", None)
            item.pop("top_level_dirs", None)
        self.assertEqual(stripped, legacy)
        self.assertEqual(report, json.loads(diagnostics.serialize(report)))
        self.assertNotIn(str(self.root), diagnostics.render(report))
        self.assertNotIn(self.root.as_posix(), diagnostics.serialize(report))
        self.assertFalse(report["webhook_sent"])

    def test_many_ignored_forbidden_files_preserve_refusal_and_samples(self):
        self.configure(git_gate="advisory", forbidden_files=[".git", ".bridge", "src/do_not_touch.py", "tools", "root.tmp"])
        # info/exclude belongs only to this disposable Git fixture.
        (self.root / ".git/info/exclude").write_text("tools/.venv/\n", encoding="utf-8")
        directory = self.root / "tools/.venv"
        directory.mkdir(parents=True)
        for index in range(120):
            (directory / f"file-{index:03}.py").write_text("# fictional\n", encoding="utf-8")
        (self.root / "root.tmp").write_text("fictional", encoding="utf-8")
        snapshot = git_snapshot(self.root, (), self.head, ("tools", "root.tmp"))
        self.assertEqual(git_error(snapshot, "advisory"), "protected_or_forbidden_changed")
        self.assertEqual(len(snapshot["protected_or_forbidden_changes"]), 121)
        report = self.collect()
        item = self.finding(report, "protected_or_forbidden_changed")
        self.assertIs(item["includes_ignored"], True)
        self.assertEqual(item["top_level_dirs"], [{"dir": "tools", "count": 120}, {"dir": "(root)", "count": 1}])
        self.assertEqual(len(item["blocking_paths"]), 10)
        self.assertEqual(item["blocking_paths_omitted"], 111)
        self.assertFalse(report["workspace_ready"])
        text = diagnostics.render(report)
        for expected in ("Includes git-ignored files", "统计 Git 忽略文件", "虚拟环境", "Top-level directories", "顶层目录"):
            self.assertIn(expected, text)
        self.assert_presentation_only(report)

    def test_strict_untracked_root_and_collapsed_directory_exclude_ignored(self):
        (self.root / ".git/info/exclude").write_text("ignored/\n", encoding="utf-8")
        (self.root / "ignored").mkdir()
        (self.root / "ignored/cache.py").write_text("fictional", encoding="utf-8")
        (self.root / "notes").mkdir()
        (self.root / "notes/new.txt").write_text("fictional", encoding="utf-8")
        (self.root / "~$draft.xlsx").write_text("fictional", encoding="utf-8")
        snapshot = git_snapshot(self.root, (), self.head)
        self.assertEqual(git_error(snapshot), "unconfirmed_changes")
        report = self.collect()
        item = self.finding(report, "unconfirmed_changes")
        self.assertIs(item["includes_ignored"], False)
        self.assertEqual(item["top_level_dirs"], [{"dir": "(root)", "count": 1}, {"dir": "notes", "count": 1}])
        self.assertFalse(report["workspace_ready"])
        self.assert_presentation_only(report)

    def test_more_than_ten_directories_sorted_by_count_then_name(self):
        names = [f"dir-{index:02}" for index in range(12)]
        self.configure(git_gate="advisory", forbidden_files=[".git", ".bridge", "src/do_not_touch.py", *names])
        for index, name in enumerate(names):
            directory = self.root / name
            directory.mkdir()
            for number in range(index + 1):
                (directory / f"{number}.txt").write_text("fictional", encoding="utf-8")
        report = self.collect()
        item = self.finding(report, "protected_or_forbidden_changed")
        self.assertEqual(item["top_level_dirs"], [{"dir": f"dir-{index:02}", "count": index + 1} for index in range(11, 1, -1)])
        self.assert_presentation_only(report)

    def test_export_filters_untrusted_absolute_segments_and_counts(self):
        source = {"code": "unconfirmed_changes", "includes_ignored": False,
                  "top_level_dirs": [{"dir": name, "count": 1} for name in
                                     ("D:/private", "/private", "\\\\host\\private", "..", "a/b", "line\nbreak")]
                  + [{"dir": "bool", "count": True}, {"dir": "negative", "count": -1}, {"dir": "safe", "count": 2}]}
        report = diagnostics.public_report({"findings": [source]})
        self.assertEqual(report["findings"][0]["top_level_dirs"], [{"dir": "safe", "count": 2}])
        self.assertEqual(report, json.loads(diagnostics.serialize(report)))

    def test_git_quoted_unicode_paths_and_relative_windows_paths(self):
        summary = diagnostics._change_summary(['"\\344\\270\\255/file.py"', "tools\\file.py", "root.py", "D:/private/file", "../private/file"], includes_ignored=False)
        self.assertEqual(summary["top_level_dirs"], [{"dir": "(root)", "count": 1}, {"dir": "tools", "count": 1}, {"dir": "中", "count": 1}])
        collapsed = diagnostics._change_summary(['"\\344\\270\\255/"'], includes_ignored=False)
        self.assertEqual(collapsed["top_level_dirs"], [{"dir": "中", "count": 1}])

    def test_clean_workspace_keeps_existing_readiness(self):
        report = self.collect()
        self.assertTrue(report["workspace_ready"])
        self.assertNotIn("includes_ignored", diagnostics.serialize(report))
        self.assert_presentation_only(report)


if __name__ == "__main__":
    unittest.main()
