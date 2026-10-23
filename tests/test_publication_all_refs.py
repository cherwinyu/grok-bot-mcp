"""All-ref Git privacy checks stay bounded and never certify platform privacy.

Isolated synthetic repositories, no network, no real credentials/workspaces.
"""
from __future__ import annotations

import os
from pathlib import Path

from tests.support import temporary_directory
import subprocess
import unittest

from scripts.audit_publication import AuditError, inspect_repository


class GitAllRefsPrivacyTest(unittest.TestCase):
    def setUp(self):
        self.scratch = temporary_directory()
        self.addCleanup(self.scratch.cleanup)
        self.repo = Path(self.scratch.name)
        self.git("init", "--initial-branch=main", "--template=")
        (self.repo / "safe.txt").write_text("clean synthetic source\n", encoding="utf-8")
        self.git("add", "--", "safe.txt")
        self.git("commit", "-m", "Initial fictional source")
        self.main = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_ALLOW_PROTOCOL="", GIT_TERMINAL_PROMPT="0",
                   GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
        cmd = ["git", "-c", "core.hooksPath=" + os.devnull,
               "-c", "user.name=Fictional Test", "-c", "user.email=example@example.invalid",
               "-c", "commit.gpgsign=false", "-C", str(self.repo), *args]
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20, shell=False)
        self.assertEqual(proc.returncode, 0, f"fixture_git_failed: {args[0]}")
        return proc.stdout

    def test_branch_only_secret_pattern_found_by_all_ref_scan(self):
        self.git("checkout", "-b", "archived-fixture")
        # Construct an obvious synthetic pattern without shipping a usable key.
        (self.repo / "sample.txt").write_text("sk_" + "A" * 30, encoding="utf-8")
        self.git("add", "--", "sample.txt")
        self.git("commit", "-m", "Synthetic branch-only pattern")
        self.git("checkout", "main")
        selected, selected_report = inspect_repository(self.repo, self.main, history=True)
        all_refs, extended = inspect_repository(self.repo, self.main, history=True, all_refs=True)
        self.assertNotIn("credential_shape", selected_report["finding_categories"])
        self.assertGreaterEqual(extended["finding_categories"].get("credential_shape", 0), 1)
        self.assertFalse(extended["privacy_cleared"])
        self.assertFalse(extended["publication_authorized"])
        self.assertTrue(extended["all_available_local_refs_included"])
        self.assertFalse(extended["complete_remote_ref_inventory_verified"])
        self.assertGreater(extended["enumerated_objects"], selected_report["enumerated_objects"])

    def test_annotated_tag_and_branch_reference_count(self):
        self.git("tag", "-a", "v-fixture", "-m", "Fictional annotated tag")
        self.git("branch", "alternate-fixture")
        audit, report = inspect_repository(self.repo, self.main, history=True, all_refs=True)
        self.assertGreaterEqual(report["local_ref_count"], 3)
        self.assertTrue(report["all_available_local_refs_included"])
        self.assertFalse(report["other_refs_and_platform_surfaces_scanned"])
        self.assertFalse(report["privacy_cleared"])

    def test_all_ref_requires_history_opt_in(self):
        with self.assertRaisesRegex(AuditError, "all_refs_requires_history"):
            inspect_repository(self.repo, self.main, all_refs=True)

    def test_selected_legacy_scan_keeps_previous_semantics(self):
        _, report = inspect_repository(self.repo, self.main, history=True)
        self.assertFalse(report["all_available_local_refs_included"])
        self.assertEqual(report["local_ref_count"], 0)
        self.assertFalse(report["privacy_cleared"])
