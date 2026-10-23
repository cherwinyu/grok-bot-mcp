"""U3 pure-unittest contracts and native fictional fixtures. No live credentials."""
from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from tests.support import temporary_directory
from unittest.mock import Mock, patch

from grokbot_mcp import admin_cli, facade, registry_prefixes
from grokbot_mcp.execution.service import _interpret
from grokbot_mcp.local_safety import LocalError
from grokbot_mcp.manifest.store import ManifestError, prepare_manifest
from grokbot_mcp.materials import render_routine
from grokbot_mcp.registry import (_workspace, RegistryError, load_registry,
                                 policy_fingerprint, workspace_profile, routine_profile)
from grokbot_mcp.result_contract import validate_result, ContractError
from grokbot_mcp.result_examples import examples
from grokbot_mcp.routine import confirmation
from grokbot_mcp.routine_check import inspect_text
from grokbot_mcp.security.baseline import capture_snapshot, execution_delta, watched_paths, prefix_snapshot_options
from grokbot_mcp.security.globs import expand
from grokbot_mcp.security.prefixes import (prefix_json, validate_create_files,
                                         prospective_file, manifest_scope_valid, resolve_task_files)
from grokbot_mcp.security.targets import TargetRejected
from tests.test_u2a_contract import entry

PREFIXES = [{"prefix": "docs/", "exts": [".md"]}]
TASK = "11111111-1111-4111-8111-111111111111"


def workspace(**overrides):
    value = entry(allowed_prefixes=PREFIXES)
    value.update(overrides)
    return _workspace(value)


def prepare(ws, **options):
    saved = {}
    arguments = dict(task_type="code_change", task_description="Fictional task",
                     allowed_files=[], acceptance_criteria=["Measured"],
                     test_command_id="python_unittest", execution_timeout=None, baseline_commit=None)
    arguments.update(options)
    with patch("grokbot_mcp.manifest.store.uuid.uuid4", return_value=TASK), patch(
            "grokbot_mcp.manifest.store.utc_now", return_value="fixed-time"), patch(
            "grokbot_mcp.ledger_archive.archived", return_value=False), patch(
            "grokbot_mcp.manifest.store.write_json_new", side_effect=lambda _p, d: saved.update(d)), patch(
            "grokbot_mcp.manifest.store.read_json", side_effect=lambda _p: (saved, "b" * 64)):
        result = prepare_manifest(ws, **arguments)
    return result, saved


class PrefixPolicyTest(unittest.TestCase):
    def test_old_fixed_fingerprint_and_default_profiles(self):
        ws = _workspace(entry())
        self.assertEqual(policy_fingerprint(ws), "e07a777ebdc54e30c0bb19cc46b809a0a6fe0455e5ae0b2bbaa36b740084e6c5")
        explicit = _workspace(entry(allowed_prefixes=[]))
        self.assertEqual(policy_fingerprint(ws), policy_fingerprint(explicit))
        self.assertEqual(workspace_profile(ws), workspace_profile(explicit))
        self.assertNotIn("allowed_prefixes", workspace_profile(ws))
        self.assertEqual(routine_profile(ws), workspace_profile(ws))

    def test_prefix_only_and_canonical_order_unicode(self):
        ws = workspace(allowed_files=[], allowed_prefixes=[
            {"prefix": "cafe\u0301/", "exts": [".txt", ".md", ".md"]},
            {"prefix": "café/", "exts": [".rst"]}])
        self.assertEqual(prefix_json(ws), [{"prefix": "café/", "exts": [".md", ".rst", ".txt"]}])
        self.assertNotEqual(policy_fingerprint(ws), policy_fingerprint(workspace()))
        reordered = workspace(allowed_files=[], allowed_prefixes=[
            {"prefix": "café/", "exts": [".txt", ".rst", ".md"]}])
        self.assertEqual(policy_fingerprint(ws), policy_fingerprint(reordered))

    def test_load_registry_rejects_every_invalid_field(self):
        invalid = [None, True, {}, ["docs/"], [{}], [{"prefix": "docs/", "exts": []}],
                   [{"prefix": "docs/", "exts": ".md"}], [{"prefix": "docs/", "exts": [None]}]]
        for prefix in ("docs", "", "/docs/", "../docs/", "docs/../x/", "docs//", "./docs/",
                       "C:/docs/", "docs\\sub/", "docs/.git/", "docs/.BRIDGE/", "docs*/", "docs./"):
            invalid.append({"prefix": prefix, "exts": [".md"]})
        for ext in ("", ".", "md", ".*", ".m?", ".md/evil", ".m\\d", ".md:", ".md ", ".m\n"):
            invalid.append({"prefix": "docs/", "exts": [ext]})
        for value in invalid:
            if isinstance(value, dict) and "prefix" in value:
                value = [value]
            raw = json.dumps({"schema_version": 1, "workspaces": [entry(allowed_prefixes=value)]})
            with self.subTest(value=value), patch.object(Path, "is_file", return_value=True), patch.object(
                    Path, "stat", return_value=Mock(st_nlink=1, st_size=len(raw))), patch.object(
                    Path, "read_text", return_value=raw), patch(
                    "grokbot_mcp.security.paths.is_reparse_point", return_value=False), self.assertRaisesRegex(RegistryError, "registry_invalid"):
                load_registry(Path("D:/fictional/registry.json"))
        with self.assertRaises(RegistryError):
            workspace(allowed_files=[], allowed_prefixes=[])

    def test_new_disk_file_does_not_participate_in_fingerprint(self):
        ws = workspace()
        before = policy_fingerprint(ws)
        with patch.object(Path, "iterdir", side_effect=AssertionError("fingerprint touched disk")):
            self.assertEqual(before, policy_fingerprint(ws))
        self.assertNotEqual(before, policy_fingerprint(workspace(allowed_prefixes=[{"prefix": "docs/sub/", "exts": [".md"]}])))
        self.assertNotEqual(before, policy_fingerprint(workspace(allowed_prefixes=[{"prefix": "docs/", "exts": [".txt"]}])))
        # Exact exceptions remain policy, and must still invalidate a binding.
        self.assertNotEqual(before, policy_fingerprint(replace(ws, allowed_files=("new.py",))))


class CreateFilesTest(unittest.TestCase):
    def test_lexical_and_policy_rejections_before_disk(self):
        ws = workspace(forbidden_files=["private", "docs/private", ".cursor", "AGENTS.md"], protected_file="private")
        for name in ("docs/../a.md", "docs/./a.md", "docs//a.md", "C:/docs/a.md", "docs/C:a.md",
                     "docs\\a.md", "/docs/a.md", "docs/.git/a.md", "docs/.BRIDGE/a.md", "docs/a.md/",
                     "docs/a*.md", "docs/NUL.md", "docs/private/a.md", "docs/private", "docs2/a.md",
                     "other/a.md", "docs/a.txt", "", None, "docs/a.md ", "docs/dir./a.md"):
            with self.subTest(name=name), patch("grokbot_mcp.security.prefixes.prospective_file") as disk, self.assertRaises(ManifestError):
                validate_create_files(ws, [name])
            disk.assert_not_called()
        with self.assertRaises(ManifestError):
            validate_create_files(ws, "docs/a.md")
        with self.assertRaises(ManifestError):
            validate_create_files(_workspace(entry()), ["docs/a.md"])

    def test_protected_and_caller_owned_exclusions(self):
        ws = workspace(allowed_prefixes=[{"prefix": "docs/", "exts": [".md"]}],
                       forbidden_files=["docs/locked.md", "docs/.cursor", "docs/AGENTS.md"], protected_file="docs/locked.md")
        for name in ("docs/locked.md", "DOCS/locked.md", "docs/.cursor/rule.md", "docs/AGENTS.md"):
            with self.subTest(name=name), self.assertRaises(ManifestError):
                validate_create_files(ws, [name], check_disk=False)
        self.assertEqual(validate_create_files(workspace(), ["docs/.cursor/rule.md", "docs/AGENTS.md"], check_disk=False),
                         ["docs/.cursor/rule.md", "docs/AGENTS.md"])

    def test_limits_reject_whole_request_and_union(self):
        ws = replace(workspace(), glob_max_files=1)
        with patch("grokbot_mcp.security.prefixes.prospective_file") as disk:
            with self.assertRaisesRegex(ManifestError, "limit"):
                validate_create_files(ws, ["docs/a.md", "docs/b.md"])
            disk.assert_not_called()
            with self.assertRaisesRegex(ManifestError, "limit"):
                prepare(ws, allowed_files=["src/counter.py"], create_files=["docs/a.md"])

    def test_normalized_union_and_expansion_confirmation(self):
        ws = replace(workspace(), allow_globs=True)
        with patch("grokbot_mcp.security.prefixes.prospective_file"), patch(
                "grokbot_mcp.security.globs.expand", return_value=["docs/guide.md"]):
            result, doc = prepare(ws, allowed_files=["src/counter.py"], allowed_globs=["docs/**"],
                                  create_files=["docs/cafe\u0301.md", "docs/café.md"])
        self.assertEqual(doc["allowed_files"], ["src/counter.py", "docs/guide.md", "docs/café.md"])
        self.assertEqual(doc["scope_expansion"], {"allowed_globs": ["docs/**"], "expanded_files": ["docs/guide.md"],
                                                  "create_files": ["docs/café.md"]})
        self.assertEqual(result["allowed_files"], doc["allowed_files"])
        self.assertEqual(result["action_required"], "confirm_expanded_files")

    def test_glob_index_accepts_prefix_but_keeps_exclusions(self):
        names = ["docs/new.md", "docs/a.txt", "docs/.git/a.md", "docs/private/a.md", "docs2/a.md"]
        listing = "".join(f"100644 {'a' * 40} 0\t{n}\0" for n in names)
        ws = workspace(allow_globs=True, allowed_files=[], forbidden_files=["private", "docs/private"])
        with patch("grokbot_mcp.security.globs.run_git", return_value=subprocess.CompletedProcess([], 0, listing)), patch(
                "grokbot_mcp.security.globs._require_existing_file"):
            self.assertEqual(expand(ws, ["docs/**"]), ["docs/new.md"])

    def test_missing_paths_allowed_but_root_parent_and_leaf_reparse_rejected(self):
        root = Path("D:/fictional/workspace")
        docs = root / "docs"
        leaf = docs / "a.md"
        entries = {root: [docs], docs: [leaf]}
        for linked in (root.parent, root, docs, leaf):
            with self.subTest(linked=linked), patch("grokbot_mcp.security.prefixes.is_reparse_point", side_effect=lambda p: p == linked), patch.object(
                    Path, "is_dir", return_value=True), patch.object(Path, "iterdir", lambda p: iter(entries.get(p, []))), self.assertRaisesRegex(TargetRejected, "reparse"):
                prospective_file(root, "docs/a.md")
        with patch("grokbot_mcp.security.prefixes.is_reparse_point", return_value=False), patch.object(
                Path, "is_dir", return_value=True), patch.object(Path, "iterdir", return_value=iter([])):
            prospective_file(root, "docs/new/sub/a.md")

    def test_case_hardlink_and_directory_leaf_refused(self):
        root = Path("D:/fictional/workspace")
        docs, leaf = root / "docs", root / "docs/a.md"
        entries = {root: [docs], docs: [leaf]}
        with patch("grokbot_mcp.security.prefixes.is_reparse_point", return_value=False), patch.object(
                Path, "is_dir", return_value=True), patch.object(Path, "iterdir", lambda p: iter(entries.get(p, []))):
            with self.assertRaisesRegex(TargetRejected, "case_mismatch"):
                prospective_file(root, "Docs/a.md")
            with patch.object(Path, "is_file", return_value=False), self.assertRaisesRegex(TargetRejected, "not_file"):
                prospective_file(root, "docs/a.md")
            with patch.object(Path, "is_file", return_value=True), patch.object(Path, "stat", return_value=Mock(st_nlink=2)), self.assertRaisesRegex(TargetRejected, "hardlink"):
                prospective_file(root, "docs/a.md")

    def test_claim_rechecks_creations_and_preserves_exact_existing_check(self):
        doc = {"allowed_files": ["src/counter.py", "docs/new.md"], "scope_expansion": {"create_files": ["docs/new.md"]}}
        with patch("grokbot_mcp.security.prefixes.prospective_file") as prospective, patch(
                "grokbot_mcp.security.targets.resolve_exact_files") as exact:
            self.assertEqual(resolve_task_files(workspace(), doc), doc["allowed_files"])
            prospective.assert_called_once()
            self.assertEqual(exact.call_args.args[1], ["src/counter.py"])
        with patch("grokbot_mcp.security.prefixes.prospective_file", side_effect=TargetRejected("reparse")), self.assertRaisesRegex(TargetRejected, "reparse"):
            resolve_task_files(workspace(), doc)

    def test_forged_manifest_create_declaration_rejected(self):
        for creates in (["docs/outside.txt"], ["docs/not-selected.md"], ["docs/a.md", "docs/a.md"], "docs/a.md"):
            self.assertFalse(manifest_scope_valid(workspace(), {"allowed_files": ["docs/a.md"], "scope_expansion": {"create_files": creates}}))


class SnapshotTest(unittest.TestCase):
    def snapshot(self, ignored_count=0, added=()):
        def run(_root, args, _runner):
            if args[0] == "rev-parse":
                out = "a" * 40
            elif args[0] == "status":
                out = "".join(f"!! docs/cache/{i}.tmp\n" for i in range(ignored_count))
                out += "".join(f"?? {n}\n" for n in added)
            elif args[0] == "ls-files":
                out = "docs/guide.md\0" + "".join(n + "\0" for n in added)
            else:
                out = ""
            return subprocess.CompletedProcess([], 0, out)
        ws = workspace(git_gate="advisory", allowed_files=[])
        with patch("grokbot_mcp.security.baseline._run", side_effect=run), patch(
                "grokbot_mcp.security.baseline._hash_file", return_value="hash"), patch(
                "grokbot_mcp.security.baseline.is_reparse_point", return_value=False), patch.object(
                Path, "is_dir", return_value=True), patch.object(Path, "is_file", return_value=False):
            return capture_snapshot(ws.root, watched_paths(ws), include_ignored=False, **prefix_snapshot_options(ws))

    def test_large_ignored_prefix_tree_not_persisted(self):
        baseline = self.snapshot(20000)
        raw = json.dumps(baseline).encode("utf-8")
        self.assertLess(len(raw), 2048)
        self.assertEqual(baseline["ignored"], [])
        self.assertNotIn("cache", raw.decode("utf-8"))

    def test_prefix_untracked_new_and_preexisting_compared(self):
        before = self.snapshot(added=["docs/preexisting.md"])
        current = self.snapshot(added=["docs/preexisting.md", "docs/new.md"])
        self.assertEqual(execution_delta(before, current), ["docs/new.md"])
        self.assertIn("docs/preexisting.md", before["dirty_hashes"])
        self.assertIn("docs/preexisting.md", before["file_hashes"])

    def test_prefix_index_failure_fail_closed(self):
        ws = workspace()
        with patch("grokbot_mcp.security.baseline._run", side_effect=[
                subprocess.CompletedProcess([], 0, "a" * 40), subprocess.CompletedProcess([], 0, ""),
                subprocess.CompletedProcess([], 0, ""), subprocess.CompletedProcess([], 1, "")]), self.assertRaisesRegex(ValueError, "git_status_failed"):
            capture_snapshot(ws.root, [], **prefix_snapshot_options(ws))


class ResultTest(unittest.TestCase):
    def interpret(self, *, gate="advisory", added=(), reported=(), creates=("docs/a.md",), existing=()):
        ws = workspace(git_gate=gate)
        value = examples()["execute-succeeded"]
        value["changed_files"] = list(reported)
        value["code_modified"] = bool(reported)
        doc = dict(allowed_files=list(creates), scope_expansion={"create_files": list(creates)},
                   task_type="code_change", test_command_id="python_unittest")
        before = dict(git_head_text="a" * 40, file_hashes={n: None for n in creates}, untracked=list(existing))
        after = dict(git_head_text="a" * 40, file_hashes={n: ("new" if n in added else None) for n in creates},
                     untracked=list(existing) + list(added))
        record = {k: value[k] for k in ("execution_id", "manifest_task_id", "manifest_sha256")}
        store = Mock()
        store.read.return_value = record
        with patch("grokbot_mcp.execution.service.assess", return_value={"document": doc}), patch(
                "grokbot_mcp.execution.service.verify_evidence", return_value=(before, after)), patch(
                "grokbot_mcp.security.prefixes.prospective_file"), patch(
                "grokbot_mcp.security.gitread.run_git", return_value=subprocess.CompletedProcess([], 0, "")):
            result = _interpret(store, ws, record, {}, value, "b" * 64)
        return result, store.update.call_args.kwargs

    def test_not_created_is_measured_optional_and_frozen(self):
        result, saved = self.interpret()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["not_created"], ["docs/a.md"])
        self.assertEqual(saved["verified_summary"]["not_created"], ["docs/a.md"])
        self.assertEqual(self.interpret(added=["docs/a.md"], reported=["docs/a.md"])[0]["not_created"], [])

    def test_advisory_unreported_new_outside_task_is_reported(self):
        result, _ = self.interpret(added=["docs/concurrent.md"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["out_of_scope_changes"], ["docs/concurrent.md"])

    def test_agent_reported_new_outside_task_is_rejected(self):
        for name in ("docs/outside.md", "other/outside.md"):
            result, _ = self.interpret(added=[name], reported=[name])
            self.assertEqual(result["error"], "scope_violation")
            self.assertEqual(result["out_of_scope_changes"], [name])

    def test_existing_outside_report_keeps_advisory_behavior(self):
        result, _ = self.interpret(reported=["other/existing.md"], existing=["other/existing.md"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["out_of_scope_changes"], ["other/existing.md"])

    def test_strict_new_scope_violation_and_optional_creation(self):
        result, _ = self.interpret(gate="strict", added=["docs/other.md"])
        self.assertEqual(result["error"], "scope_violation")
        result, _ = self.interpret(gate="strict")
        self.assertEqual(result["status"], "succeeded")
        self.assertNotIn("out_of_scope_changes", result)

    def test_not_created_contract_and_old_result_bytes(self):
        value = examples()["execute-succeeded"]
        value["not_created"] = [True]
        with self.assertRaises(ContractError):
            validate_result(value, "execute")
        # Reuse the literal legacy vector without weakening its assertion.
        from tests.test_u2a_contract import InterpretationTest
        InterpretationTest("test_strict_result_json_matches_fixed_legacy_bytes").test_strict_result_json_matches_fixed_legacy_bytes()


class MaterialsAndMigrationTest(unittest.TestCase):
    def test_seven_tools_and_optional_create_parameter_forwarded(self):
        import asyncio
        from grokbot_mcp import server
        listed = asyncio.run(server.mcp.list_tools())
        self.assertEqual({t.name for t in listed}, server.TOOL_NAMES)
        self.assertEqual(len(listed), 7)
        schema = next(t.input_schema for t in listed if t.name == "grok_prepare_task")
        self.assertIn("create_files", schema["properties"])
        self.assertNotIn("create_files", schema["required"])
        with patch.object(facade, "prepare_task", return_value={"ok": True}) as call:
            server.grok_prepare_task("fictional", "code_change", "Fictional", [], ["Measured"],
                                     "python_unittest", create_files=["docs/a.md"])
        self.assertEqual(call.call_args.kwargs["create_files"], ["docs/a.md"])

    def test_public_cli_routes_to_admin_without_starting_stdio(self):
        from grokbot_mcp import server
        args = ["grokbot-mcp", "registry-prefixes", "--registry", "D:/fictional/registry.json", "--workspace", "fictional"]
        with patch("sys.argv", args), patch.object(admin_cli, "main", return_value=0) as main, patch.object(
                server.mcp, "run", side_effect=AssertionError("stdio started")), self.assertRaises(SystemExit) as result:
            server.main()
        self.assertEqual(result.exception.code, 0)
        main.assert_called_once_with(args[1:])

    def packet(self, ws):
        registry, python = Path("D:/fictional/registry.json"), Path("D:/fictional/python.exe")
        profile = routine_profile(ws)
        profile["root"] = str(ws.root)
        for key in ("git_gate", "validate_credential_target", "execute_credential_target"):
            profile.pop(key)
        binding = {p: confirmation(ws, p, registry, mode="create", revision=1, verified_at="not-confirmed")["binding"] for p in ("validate", "execute")}
        text = "```json\n" + json.dumps(profile) + "\n```\n```json\n" + json.dumps(binding) + "\n```\n"
        for phase in ("validate", "execute"):
            text += render_routine(phase, workspace_root=ws.root, workspace_id=ws.workspace_id,
                                   registry=registry, python=python, mode="create", test_options=profile)
        return text, registry, python

    def test_prefix_packet_omits_full_list_and_detects_drift(self):
        ws = workspace()
        text, registry, python = self.packet(ws)
        self.assertNotIn('"allowed_files":', text)
        self.assertIn('"allowed_prefixes":', text)
        self.assertEqual(inspect_text(text, ws, registry, python, "setup"), [])
        for old in ('"docs/"', policy_fingerprint(ws)):
            replacement = '"wrong/"' if old.startswith('"') else "0" * 64
            self.assertIn("routine_prefix_policy_mismatch", inspect_text(text.replace(old, replacement), ws, registry, python, "setup"))

    def test_legacy_templates_and_manifest_bytes(self):
        ws = _workspace(entry())
        plain = prepare(ws, allowed_files=["src/counter.py"])
        explicit = prepare(replace(ws, allowed_prefixes=()), allowed_files=["src/counter.py"], create_files=None)
        self.assertEqual(json.dumps(plain, sort_keys=True), json.dumps(explicit, sort_keys=True))
        expected_doc = {"acceptance_criteria": ["Measured"], "allowed_files": ["src/counter.py"],
            "baseline_commit": "a" * 40, "constraint_version": 1, "created_at": "fixed-time", "execution_timeout": 30,
            "forbidden_files": ["private"], "lifecycle": "prepared", "manifest_task_id": TASK, "schema_version": 1,
            "task_description": "Fictional task", "task_id": TASK, "task_type": "code_change",
            "test_command_id": "python_unittest", "test_policy": {"command_id": "python_unittest"}, "workspace_id": "example-workspace"}
        self.assertEqual(json.dumps(plain[1], sort_keys=True).encode("utf-8"), json.dumps(expected_doc, sort_keys=True).encode("utf-8"))
        old, _, _ = self.packet(ws)
        self.assertNotIn("## Directory authorization", old)
        resource = files("grokbot_mcp").joinpath("templates", "prefix-policy.md").read_text(encoding="utf-8")
        self.assertIn("not_created", resource)

    def test_migration_preview_is_readonly_and_copy_exclusive(self):
        registry = Path("D:/fictional/registry.json")
        source = entry()
        raw = {"schema_version": 1, "workspaces": [source]}
        before = json.dumps(raw).encode("utf-8")
        ws = _workspace(source)
        with patch.object(registry_prefixes, "load_registry", return_value={ws.workspace_id: ws}), patch.object(
                registry_prefixes, "read_document", return_value=(raw, before)), patch.object(
                registry_prefixes, "create_new") as write, patch(
                "grokbot_mcp.setup_wizard.validate_export_destination", side_effect=lambda p, _r: p), patch.object(
                Path, "read_bytes", return_value=before):
            report = registry_prefixes.preview(registry, ws.workspace_id)
            write.assert_not_called()
            self.assertEqual(raw, json.loads(before))
            self.assertEqual(report["allowed_files"], [])
            self.assertEqual(report["allowed_prefixes"], [{"prefix": "docs/", "exts": [".md"]}, {"prefix": "src/", "exts": [".py"]}])
            self.assertNotEqual(report["old_policy_fingerprint"], report["new_policy_fingerprint"])
            registry_prefixes.preview(registry, ws.workspace_id, write_to=registry.parent / "new.json")
            written = json.loads(write.call_args.args[1])
            self.assertEqual(written["workspaces"][0]["forbidden_files"], source["forbidden_files"])
            self.assertFalse(written["workspaces"][0]["writes_enabled"])
            self.assertEqual(raw, json.loads(before))
            write.side_effect = LocalError("backup_already_exists")
            with self.assertRaisesRegex(LocalError, "backup_already_exists"):
                registry_prefixes.preview(registry, ws.workspace_id, write_to=registry.parent / "new.json")

    def test_cli_cp1252_no_utf8_env_or_credentials(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252")
        with patch.dict(os.environ, {}, clear=True), patch.object(registry_prefixes, "preview", return_value={"ok": True, "message": "目录授权"}) as preview, patch(
                "grokbot_mcp.credentials.backend.load_for", side_effect=AssertionError("credential read")), redirect_stdout(stream):
            self.assertEqual(admin_cli.main(["registry-prefixes", "--registry", "D:/fictional/registry.json", "--workspace", "fictional"]), 0)
        stream.flush()
        self.assertIn(b'"ok": true', buffer.getvalue())
        self.assertIsNone(preview.call_args.kwargs["write_to"])


class NativePrefixTest(unittest.TestCase):
    """Director native gate: Git/lock/tempfile support is required, never mocked."""
    def setUp(self):
        from tests.support import make_workspace, WORKSPACE_ID
        from grokbot_mcp.credentials.backend import install_backend
        self.old_registry = os.environ.get("GROKBOT_MCP_REGISTRY")
        self.addCleanup(self.restore_environment)
        self.addCleanup(install_backend, None, None)
        self.addCleanup(facade.install_transport, None)
        self.temp = temporary_directory(prefix="u3-fictional-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = make_workspace(self.base)
        self.registry = self.base / "registry.json"
        self.workspace_id = WORKSPACE_ID
        self.configure("advisory")

    def restore_environment(self):
        if self.old_registry is None:
            os.environ.pop("GROKBOT_MCP_REGISTRY", None)
        else:
            os.environ["GROKBOT_MCP_REGISTRY"] = self.old_registry

    def configure(self, gate):
        raw = json.loads(self.registry.read_text(encoding="utf-8"))
        raw["workspaces"][0].update(allowed_prefixes=PREFIXES, git_gate=gate, allowed_files=[])
        self.registry.write_text(json.dumps(raw), encoding="utf-8")
        self.ws = load_registry(self.registry)[self.workspace_id]
        for phase in ("validate", "execute"):
            path = self.root / ".bridge" / (phase + "_routine_status.json")
            data = json.loads(path.read_text(encoding="utf-8"))
            data["binding"]["policy_fingerprint"] = policy_fingerprint(self.ws)
            path.write_text(json.dumps(data), encoding="utf-8")
        (self.base / "execution_switch.json").write_text('{"enabled": true}', encoding="utf-8")

    def start(self):
        from tests.support import write_proof
        task = facade.prepare_task(self.workspace_id, "code_change", "Fictional new document", [], ["Measured"],
                                   "python_unittest", create_files=["docs/a.md", "docs/optional.md"])
        self.assertIsNone(task.get("error"), task)
        self.assertTrue(facade.submit_validation_tool(self.workspace_id, task["task_id"])["accepted"])
        write_proof(self.root, task["task_id"], task["manifest_sha256"], task["allowed_files"])
        self.assertEqual(facade.get_status(self.workspace_id, task_id=task["task_id"])["validation"]["status"], "succeeded")
        execution = facade.prepare_execution_tool(self.workspace_id, task["task_id"])
        self.assertIsNone(execution.get("error"), execution)
        self.assertTrue(facade.submit_execution_tool(self.workspace_id, execution["execution_id"])["accepted"])
        claim = facade.claim_for(self.workspace_id, execution["execution_id"])
        self.assertTrue(claim["ok"], claim)
        self.assertEqual(claim["allowed_files"], task["allowed_files"])
        self.assertEqual(claim["target_check"], "prospective")
        self.assertEqual(facade.claim_for(self.workspace_id, execution["execution_id"])["error"], "duplicate_claim")
        return task, execution

    def result(self, task, execution, changed):
        from tests.support import execution_result
        data = execution_result(execution["execution_id"], task["task_id"], task["manifest_sha256"], changed)
        folder = self.root / ".bridge/results_execute"
        folder.mkdir(exist_ok=True)
        (folder / (execution["execution_id"] + ".json")).write_text(json.dumps(data), encoding="utf-8")
        return facade.get_status(self.workspace_id, execution_id=execution["execution_id"])["execution"]

    def test_end_to_end_create_and_optional_missing(self):
        before = policy_fingerprint(self.ws)
        task, execution = self.start()
        (self.root / "docs").mkdir()
        (self.root / "docs/a.md").write_text("Fictional\n", encoding="utf-8")
        result = self.result(task, execution, ["docs/a.md"])
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["not_created"], ["docs/optional.md"])
        self.assertEqual(before, policy_fingerprint(load_registry(self.registry)[self.workspace_id]))
        frozen = facade.get_status(self.workspace_id, execution_id=execution["execution_id"])["execution"]
        self.assertEqual(frozen["not_created"], ["docs/optional.md"])

    def test_strict_end_to_end_missing_creations_are_optional(self):
        self.configure("strict")
        task, execution = self.start()
        result = self.result(task, execution, [])
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["not_created"], ["docs/a.md", "docs/optional.md"])

    def test_concurrent_untracked_under_prefix_is_advisory_only(self):
        task, execution = self.start()
        (self.root / "docs").mkdir()
        (self.root / "docs/concurrent.md").write_text("Fictional\n", encoding="utf-8")
        result = self.result(task, execution, [])
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(result["out_of_scope_changes"], ["docs/concurrent.md"])

    def test_new_reported_outside_task_refused(self):
        task, execution = self.start()
        (self.root / "docs").mkdir()
        (self.root / "docs/outside.md").write_text("Fictional\n", encoding="utf-8")
        self.assertEqual(self.result(task, execution, ["docs/outside.md"])["error"], "scope_violation")

    def test_actual_registry_preview_and_copy_preserve_original(self):
        before = self.registry.read_bytes()
        preview = registry_prefixes.preview(self.registry, self.workspace_id)
        self.assertTrue(preview["dry_run"])
        target = self.base / "prefix-copy.json"
        registry_prefixes.preview(self.registry, self.workspace_id, write_to=target)
        self.assertEqual(before, self.registry.read_bytes())
        self.assertEqual(load_registry(target)[self.workspace_id].allowed_prefixes, self.ws.allowed_prefixes)
        with self.assertRaises(LocalError):
            registry_prefixes.preview(self.registry, self.workspace_id, write_to=target)
        self.assertEqual(before, self.registry.read_bytes())

    def test_native_symlink_or_junction_parent_is_refused(self):
        (self.root / "docs").mkdir()
        outside = self.base / "outside"
        outside.mkdir()
        link = self.root / "docs/linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Windows directory symlink privilege unavailable; reparse refusal covered by memory tests")
        self.addCleanup(link.unlink)
        with self.assertRaisesRegex(ManifestError, "reparse"):
            validate_create_files(self.ws, ["docs/linked/a.md"])

    def test_native_hardlink_leaf_is_refused(self):
        (self.root / "docs").mkdir()
        target = self.root / "docs/a.md"
        target.write_text("Fictional\n", encoding="utf-8")
        os.link(target, self.root / "docs/alias.md")
        with self.assertRaisesRegex(ManifestError, "hardlink"):
            validate_create_files(self.ws, ["docs/a.md"])


if __name__ == "__main__":
    unittest.main()
