"""PR9 follow-ups: synthetic paths, memory doubles and owned temporary fixtures."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from grokbot_mcp import diagnostics, facade
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.manifest.store import ManifestError
from grokbot_mcp.registry import _workspace, policy_fingerprint
from grokbot_mcp.security.baseline import capture_snapshot
from grokbot_mcp.security.gitcheck import git_error, git_snapshot
from grokbot_mcp.security.globs import expand
from grokbot_mcp.security.prefixes import (
    manifest_scope_valid, policy_allows, prefix_excluded, validate_create_files,
)
from tests import test_u3_prefixes as fixtures
from tests.test_u2a_contract import entry

EXCLUDED = ("docs/.GiT/a.md", "docs/.BRIDGE/a.md",
            "docs/.CuRsOr/a.md", "docs/aGeNtS.mD")


def index(names):
    return "".join(f"100644 {'a' * 40} 0\t{name}\0" for name in names)


class DerivedExclusionTest(unittest.TestCase):
    def test_each_creation_exclusion_refuses_before_disk(self):
        for name in EXCLUDED:
            with self.subTest(name=name), patch(
                    "grokbot_mcp.security.prefixes.prospective_file") as disk:
                with self.assertRaises(ManifestError):
                    validate_create_files(fixtures.workspace(), [name])
                disk.assert_not_called()

    def test_each_existing_prefix_selection_and_forged_manifest_refused(self):
        ws = fixtures.workspace()
        for name in EXCLUDED:
            with self.subTest(name=name), self.assertRaisesRegex(ManifestError, "allowed_file_outside_policy"):
                fixtures.prepare(ws, allowed_files=[name])
            self.assertFalse(manifest_scope_valid(ws, {"allowed_files": [name]}))

    def test_each_glob_exclusion_refuses_empty_expansion(self):
        ws = fixtures.workspace(allow_globs=True)
        for name in EXCLUDED:
            with self.subTest(name=name), patch(
                    "grokbot_mcp.security.globs.run_git",
                    return_value=subprocess.CompletedProcess([], 0, index([name]))), patch(
                    "grokbot_mcp.security.globs._require_existing_file") as disk:
                with self.assertRaisesRegex(ManifestError, "allowed_glob_empty"):
                    expand(ws, ["docs/**"])
                disk.assert_not_called()

    def test_segment_casefold_and_normalization_boundaries(self):
        for name in (".cursor/a.md", "./docs//.CURSOR/a.md", "docs\\AGENTS.md",
                     "docs/x/../.git/a.md", "docs/agents.md/child.md"):
            self.assertTrue(prefix_excluded(name), name)
        for name in ("docs/.cursor2/a.md", "docs/AGENTS.md.bak", "docs/myAGENTS.md",
                     "docs/.gitkeep.md", "docs/.bridge2/a.md"):
            self.assertFalse(prefix_excluded(name), name)
            ws = fixtures.workspace(allowed_prefixes=[{"prefix": "docs/", "exts": [".md", ".bak"]}])
            self.assertTrue(policy_allows(ws, name), name)

    def test_explicit_registry_exceptions_still_work_in_both_gates(self):
        names = ["docs/.CuRsOr/a.md", "docs/AGENTS.md"]
        for gate in ("strict", "advisory"):
            for prefixes in ([], fixtures.PREFIXES):
                ws = fixtures.workspace(git_gate=gate, allowed_prefixes=prefixes,
                                        allowed_files=names, allow_globs=True)
                with self.subTest(gate=gate, prefixes=prefixes):
                    _, doc = fixtures.prepare(ws, allowed_files=names)
                    self.assertEqual(doc["allowed_files"], names)
                    self.assertTrue(manifest_scope_valid(ws, doc))
                    with patch("grokbot_mcp.security.globs.run_git",
                               return_value=subprocess.CompletedProcess([], 0, index(names))), patch(
                            "grokbot_mcp.security.globs._require_existing_file"):
                        self.assertEqual(expand(ws, ["docs/**"]), sorted(names))
                if prefixes:
                    # create_files is always prefix-derived, even for an exact exception.
                    for name in names:
                        with self.assertRaisesRegex(ManifestError, "create_file_outside_policy"):
                            validate_create_files(ws, [name], check_disk=False)
        ws = fixtures.workspace(allowed_files=names)
        for name in names:
            denied = replace(ws, forbidden_files=(name,), protected_file=name)
            self.assertFalse(policy_allows(denied, name))

    def snapshot(self, names, watched=(), *, prefixes=("docs/",), advisory=True):
        outputs = {"rev-parse": "a" * 40, "status": "", "diff": "",
                   "ls-files": "\0".join(names) + "\0"}
        with patch("grokbot_mcp.security.baseline._run", side_effect=lambda _r, args, _runner:
                   subprocess.CompletedProcess([], 0, outputs[args[0]])), patch(
                "grokbot_mcp.security.baseline._hash_file", return_value="hash"), patch(
                "grokbot_mcp.security.baseline.is_reparse_point", return_value=False), patch.object(
                Path, "is_file", return_value=False):
            return capture_snapshot(Path("D:/fictional/workspace"), list(watched),
                                    include_ignored=not advisory, prefix_dirs=prefixes)

    def test_prefix_scan_excludes_each_derived_path(self):
        result = self.snapshot([*EXCLUDED, "docs/guide.md"])
        self.assertEqual(result["file_hashes"], {"docs/guide.md": "hash"})

    def test_scan_preserves_explicit_and_forbidden_watches(self):
        names = ["docs/.cursor/a.md", "docs/AGENTS.md"]
        for name in names:
            # The same watched list contains exact grants, forbidden and task paths.
            with self.subTest(name=name):
                result = self.snapshot(names, watched=[name])
                self.assertEqual(result["file_hashes"], {name: "hash"})
        result = self.snapshot(names, watched=["docs/.cursor"])
        self.assertEqual(result["file_hashes"], {
            "docs/.cursor": "hash", "docs/.cursor/a.md": "hash"})

    def test_no_prefix_snapshots_keep_fixed_bytes_in_both_gates(self):
        for advisory in (False, True):
            result = self.snapshot([], watched=["docs/AGENTS.md"], prefixes=(), advisory=advisory)
            expected = {"schema_version": 1, "git_head_text": "a" * 40,
                        "diff_files": [], "untracked": [], "status_paths": [], "ignored": [],
                        "file_hashes": {"docs/AGENTS.md": "hash"}}
            if advisory:
                expected["dirty_hashes"] = {}
            self.assertEqual(json.dumps(result, sort_keys=True).encode("utf-8"),
                             json.dumps(expected, sort_keys=True).encode("utf-8"))

    def test_fixed_policy_fingerprints_unchanged(self):
        self.assertEqual(policy_fingerprint(_workspace(entry())),
                         "e07a777ebdc54e30c0bb19cc46b809a0a6fe0455e5ae0b2bbaa36b740084e6c5")
        self.assertEqual(policy_fingerprint(fixtures.workspace()),
                         "8c70e8c20ad7105804c41f6364228888927a5d0a76fbd8071366e42e0d9c6110")

    def test_existing_only_lists_retain_limit_behavior(self):
        # Investigation evidence: no new cap on existing precise task selections.
        names = ["docs/a.md", "docs/b.md"]
        for explicit in ([], names):
            ws = fixtures.workspace(allowed_files=explicit, glob_max_files=1)
            _, doc = fixtures.prepare(ws, allowed_files=names)
            self.assertEqual(doc["allowed_files"], names)
            self.assertTrue(manifest_scope_valid(ws, doc))
            with self.assertRaisesRegex(ManifestError, "allowed_glob_limit_exceeded"):
                fixtures.prepare(ws, allowed_files=names, create_files=[])


class HardlinkContractTest(unittest.TestCase):
    def test_native_hardlink_retains_distinct_refusal_codes(self):
        with tempfile.TemporaryDirectory(prefix="pr9-hardlink-fictional-") as name:
            root = Path(name)
            (root / "docs").mkdir()
            leaf = root / "docs/a.md"
            leaf.write_text("fictional\n", encoding="utf-8")
            os.link(leaf, root / "docs/alias.md")
            ws = replace(fixtures.workspace(), root=root)
            with self.assertRaisesRegex(ManifestError, "^target_hardlink_rejected$"):
                validate_create_files(ws, ["docs/a.md"])
            outputs = iter(["a" * 40, "", "", "docs/a.md\0"])
            runner = lambda *a, **k: subprocess.CompletedProcess([], 0, next(outputs), "")
            with self.assertRaisesRegex(ValueError, "^reparse$"):
                capture_snapshot(root, [], runner, prefix_dirs=("docs/",))


class NestedRepositoryTest(unittest.TestCase):
    def test_gitlink_untracked_and_ordinary_forbidden_changes_still_fail(self):
        for line in ("?? nested_repo/", " M nested_repo", " M nested_repo/real.md"):
            outputs = iter(["a" * 40, line, "", line])
            runner = lambda *a, **k: subprocess.CompletedProcess([], 0, next(outputs), "")
            report = git_snapshot(Path("D:/fictional/workspace"), (), "a" * 40,
                                  ("nested_repo",), runner=runner)
            for gate in ("strict", "advisory"):
                self.assertEqual(git_error(report, gate), "protected_or_forbidden_changed")
            self.assertEqual(report["protected_or_forbidden_changes"], [line[3:].rstrip("/")])
        finding = diagnostics.finding("protected_or_forbidden_changed")
        self.assertIn("nested repository root", finding["next_step"])
        self.assertIn("keep forbidden protection", finding["next_step"])
        self.assertEqual(finding["severity"], "error")

    def test_native_untracked_nested_repository_is_still_refused(self):
        from tests.support import git, make_workspace
        with tempfile.TemporaryDirectory(prefix="pr9-fictional-") as name, patch.dict(os.environ):
            self.addCleanup(install_backend, None, None)
            self.addCleanup(facade.install_transport, None)
            root = make_workspace(Path(name), writes=False)
            nested = root / "nested_repo"
            nested.mkdir()
            git(nested, "init")
            (nested / "note.md").write_text("fictional\n", encoding="utf-8")
            report = git_snapshot(root, (), git(root, "rev-parse", "HEAD"), ("nested_repo",))
            self.assertEqual(report["protected_or_forbidden_changes"], ["nested_repo"])
            self.assertEqual(git_error(report, "advisory"), "protected_or_forbidden_changed")
            public = diagnostics.public_report({"findings": [{"code": git_error(report, "advisory")}]})
            self.assertIn("gitlink", public["findings"][0]["next_step"])
            self.assertFalse(public["complete_for_purpose"])
            self.assertFalse(public["webhook_sent"])


if __name__ == "__main__":
    unittest.main()
