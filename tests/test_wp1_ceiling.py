"""WP1 contracts use fictional data, memory-only credentials and transport."""
from __future__ import annotations

import hashlib
import io
import json
import os
import unittest
from contextlib import nullcontext, redirect_stdout
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

from tests.support import temporary_directory
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from grokbot_mcp import admin_cli, facade, registry_prefixes
from grokbot_mcp.execution import service
from grokbot_mcp.identity import RuntimeIdentity
from grokbot_mcp.manifest.store import ManifestError
from grokbot_mcp.materials import render_routine
from grokbot_mcp.registry import _workspace, load_registry, policy_fingerprint, routine_profile, RegistryError
from grokbot_mcp.security.baseline import prefix_snapshot_options, watched_paths
from grokbot_mcp.security.prefixes import (active_prefix_fields, manifest_scope_valid, manifest_workspace,
                                         prefix_json, resolve_task_files)
from tests.test_u2a_contract import entry
from tests.test_u3_prefixes import prepare, workspace, PREFIXES, TASK
from tests import test_u3_prefixes as legacy

CEILING = [{"prefix": "docs/", "exts": [".md", ".txt"]}]
EXECUTION = "22222222-2222-4222-8222-222222222222"
NEXT_TASK = "33333333-3333-4333-8333-333333333333"


def ceiling_workspace(**overrides):
    values = dict(prefix_ceiling=CEILING)
    values.update(overrides)
    return workspace(**values)


class CeilingContractTest(unittest.TestCase):
    def test_legacy_workspace_without_ceiling_keeps_identity_and_claim_fields(self):
        ws = SimpleNamespace(allowed_files=("src/counter.py",))
        document = {"allowed_files": ["src/counter.py"]}
        self.assertIs(manifest_workspace(ws, document), ws)
        self.assertEqual(active_prefix_fields(ws, document), {})

    def test_legacy_workspace_cannot_accept_a_frozen_prefix_grant(self):
        ws = SimpleNamespace(allowed_files=("src/counter.py",))
        for active in ([], PREFIXES, None):
            with self.subTest(active=active), self.assertRaisesRegex(
                    ManifestError, "^active_prefixes_outside_ceiling$"):
                manifest_workspace(ws, {"active_prefixes": active})

    def load(self, value):
        raw = json.dumps({"schema_version": 1, "workspaces": [value]})
        with patch.object(Path, "is_file", return_value=True), patch.object(
                Path, "stat", return_value=Mock(st_nlink=1, st_size=len(raw))), patch.object(
                Path, "read_text", return_value=raw), patch(
                "grokbot_mcp.security.paths.is_reparse_point", return_value=False):
            return load_registry(Path("D:/fictional/registry.json"))

    def test_load_rejects_malformed_ceiling_and_outside_prefixes(self):
        invalid = [None, True, {}, ["docs/"], [{}], [{"prefix": "docs/", "exts": []}],
                   [{"prefix": "docs/", "exts": ".md"}], [{"prefix": "docs/", "exts": [None]}]]
        for prefix in ("docs", "", "/docs/", "../docs/", "docs/../x/", "docs//", "./docs/",
                       "C:/docs/", "docs\\sub/", "docs/.git/", "docs/.BRIDGE/", "docs*/", "docs./"):
            invalid.append([{"prefix": prefix, "exts": [".md"]}])
        for ext in ("", ".", "md", ".*", ".m?", ".md/evil", ".m\\d", ".md:", ".md ", ".m\n"):
            invalid.append([{"prefix": "docs/", "exts": [ext]}])
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(RegistryError, "registry_invalid"):
                self.load(entry(allowed_prefixes=PREFIXES, prefix_ceiling=value))
        for active in ([{"prefix": "docs2/", "exts": [".md"]}],
                       [{"prefix": "Docs/", "exts": [".md"]}],
                       [{"prefix": "docs/", "exts": [".py"]}],
                       [{"prefix": "docs/", "exts": [".md", ".txt"]}]):
            # Two separate ceiling entries cannot pool their extensions.
            ceiling = [{"prefix": "docs/", "exts": [".md"]},
                       {"prefix": "docs/sub/", "exts": [".txt"]}]
            with self.subTest(active=active), self.assertRaisesRegex(RegistryError, "prefix_outside_ceiling"):
                self.load(entry(allowed_prefixes=active, prefix_ceiling=ceiling))
        with self.assertRaisesRegex(RegistryError, "prefix_outside_ceiling"):
            self.load(entry(allowed_prefixes=PREFIXES, prefix_ceiling=[]))

    def test_normalization_empty_active_and_explicit_file_exceptions(self):
        ws = ceiling_workspace(prefix_ceiling=[{"prefix": "cafe\u0301/", "exts": [".txt", ".md", ".md"]}],
                               allowed_prefixes=[{"prefix": "café/sub/", "exts": [".md"]}])
        self.assertEqual(prefix_json(ws, ceiling=True), [{"prefix": "café/", "exts": [".md", ".txt"]}])
        ws = ceiling_workspace(allowed_prefixes=[], allowed_files=[])
        self.assertEqual(prefix_json(ws), [])
        empty = ceiling_workspace(prefix_ceiling=[], allowed_prefixes=[])
        _, doc = prepare(empty, allowed_files=["src/counter.py"])
        self.assertTrue(manifest_scope_valid(empty, doc))
        self.assertEqual(doc["active_prefixes"], [])

    def test_fingerprint_ceiling_stable_active_and_fixed_legacy(self):
        ws = ceiling_workspace()
        for active in ([], [{"prefix": "docs/sub/", "exts": [".txt"]}], CEILING):
            self.assertEqual(policy_fingerprint(ws), policy_fingerprint(ceiling_workspace(allowed_prefixes=active)))
        self.assertNotEqual(policy_fingerprint(ws), policy_fingerprint(ceiling_workspace(prefix_ceiling=PREFIXES)))
        self.assertNotEqual(policy_fingerprint(ws), policy_fingerprint(replace(ws, forbidden_files=("private", "docs/secret"))))
        self.assertNotEqual(policy_fingerprint(ws), policy_fingerprint(replace(ws, protected_file="docs/secret")))
        self.assertNotEqual(policy_fingerprint(ws), policy_fingerprint(replace(ws, allowed_files=("other.py",))))
        self.assertEqual(policy_fingerprint(workspace()), "8c70e8c20ad7105804c41f6364228888927a5d0a76fbd8071366e42e0d9c6110")
        self.assertEqual(policy_fingerprint(_workspace(entry())), "e07a777ebdc54e30c0bb19cc46b809a0a6fe0455e5ae0b2bbaa36b740084e6c5")

    def test_legacy_routine_fixed_bytes_at_fixed_generator_version(self):
        expected = {"validate": "91c6d72f05f6c65a0282dcda05fb6321ce321f4dcb4b6fbec4fe30c80dc7a9cd",
                    "execute": "ec6bd97b5b0ff4f3a704d26e6b153c8ca0d2f20fcc904311b0711477b20abbfe"}
        # Only the generated version banner changes with the release number.
        with patch("grokbot_mcp.materials.__version__", "0.1.4b1"):
            for phase, digest in expected.items():
                text = render_routine(phase, workspace_root=Path("D:/fictional/workspace"),
                    workspace_id="example-workspace", registry=Path("D:/fictional/registry.json"),
                    python=Path("D:/fictional/python.exe"), mode="reuse", test_options=routine_profile(workspace()))
                from grokbot_mcp.routine import CLAIM_GUIDANCE, claim_command
                self.assertIn(CLAIM_GUIDANCE, text)
                if phase == "execute":
                    self.assertIn(claim_command("example-workspace", policy_fingerprint(workspace())), text)
                # Only the new claim option/guidance is excluded from the
                # pinned legacy bytes; all other template text stays frozen.
                text = text.replace(" --expect-fingerprint " + policy_fingerprint(workspace()), "")
                text = text.replace(CLAIM_GUIDANCE + "\n\n", "")
                self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), digest)
        self.assertIn("active_prefixes_sha256", files("grokbot_mcp").joinpath(
            "templates", "prefix-policy.md").read_text(encoding="utf-8"))

    def test_routine_stable_under_active_changes_and_checks_ceiling(self):
        ws = ceiling_workspace()
        text, registry, python = legacy.MaterialsAndMigrationTest().packet(ws)
        changed = ceiling_workspace(allowed_prefixes=[])
        self.assertEqual(legacy.inspect_text(text, changed, registry, python, "setup"), [])
        self.assertNotIn('"allowed_prefixes":', text)
        self.assertIn('"prefix_ceiling":', text)
        self.assertIn("STOP", text)
        for altered in (text.replace('"docs/"', '"wrong/"'), text.replace("STOP", "GO")):
            self.assertTrue(legacy.inspect_text(altered, changed, registry, python, "setup"))

    def test_manifest_snapshot_is_frozen_for_claim_result_and_baseline(self):
        ws = ceiling_workspace()
        with patch("grokbot_mcp.security.prefixes.prospective_file"):
            _, doc = prepare(ws, create_files=["docs/new.md"])
        changed = ceiling_workspace(allowed_prefixes=[])
        self.assertEqual(doc["active_prefixes"], PREFIXES)
        self.assertTrue(manifest_scope_valid(changed, doc))
        self.assertEqual(active_prefix_fields(ws, doc), active_prefix_fields(changed, doc))
        expected_hash = hashlib.sha256(b'[{"exts":[".md"],"prefix":"docs/"}]').hexdigest()
        self.assertEqual(active_prefix_fields(changed, doc)["active_prefixes_sha256"], expected_hash)
        self.assertEqual(prefix_snapshot_options(changed, doc), {"prefix_dirs": ("docs/",)})
        self.assertIn("docs/new.md", watched_paths(changed, doc))
        with patch("grokbot_mcp.security.prefixes.prospective_file"):
            self.assertEqual(resolve_task_files(changed, doc), ["docs/new.md"])
        expanded_doc = {**doc, "allowed_files": ["docs/new.txt"],
                        "scope_expansion": {"create_files": ["docs/new.txt"]}}
        self.assertFalse(manifest_scope_valid(ceiling_workspace(allowed_prefixes=CEILING), expanded_doc))
        with self.assertRaisesRegex(ManifestError, "create_file_outside_policy"):
            prepare(ws, create_files=["docs/new.txt"])

    def test_missing_invalid_or_shrunk_snapshot_refused_at_claim(self):
        ws = ceiling_workspace(writes_enabled=True, prefix_ceiling=[{"prefix": "docs/", "exts": [".txt"]}],
                               allowed_prefixes=[])
        _, doc = prepare(ceiling_workspace(), allowed_files=["src/counter.py"])
        for active in (None, True, [{"prefix": "docs/", "exts": [".md"]}],
                       [{"prefix": "elsewhere/", "exts": [".txt"]}]):
            candidate = {**doc, "active_prefixes": active}
            self.assertFalse(manifest_scope_valid(ws, candidate))
            with self.assertRaisesRegex(ManifestError, "active_prefixes_outside_ceiling"):
                manifest_workspace(ws, candidate)
        store = Mock()
        store.locked.side_effect = nullcontext
        store.read.return_value = dict(execution_id=EXECUTION, manifest_task_id=TASK,
            submission_state="accepted", execution_state="pending", approval_state="granted")
        with patch.object(service, "_ledger", return_value=store), patch.object(Path, "exists", return_value=False), patch(
                "grokbot_mcp.routine.read_routine", return_value={}), patch.object(
                service, "workspace_git_rejection", return_value=None), patch.object(
                service, "load_manifest", return_value=(doc, "a" * 64)):
            result = service.claim_execution(ws, EXECUTION, [])
        self.assertEqual(result["error"], "active_prefixes_outside_ceiling")
        store.update.assert_not_called()

    def test_successful_claim_emits_and_persists_frozen_prefix_fields(self):
        ws = ceiling_workspace(writes_enabled=True, allowed_prefixes=[])
        with patch("grokbot_mcp.security.prefixes.prospective_file"):
            _, doc = prepare(ceiling_workspace(), create_files=["docs/new.md"])
        store = Mock()
        store.locked.side_effect = nullcontext
        store.read.return_value = dict(execution_id=EXECUTION, manifest_task_id=TASK,
            submission_state="accepted", execution_state="pending", approval_state="granted",
            baseline_sha256="a" * 64, policy_fingerprint=policy_fingerprint(ws), binding_revision=1,
            manifest_sha256="b" * 64, validation_result_sha256="c" * 64)
        handle = MagicMock()
        handle.__enter__.return_value = handle
        with patch.object(service, "_ledger", return_value=store), patch.object(Path, "exists", return_value=False), patch.object(
                Path, "mkdir"), patch("grokbot_mcp.routine.read_routine", return_value={}), patch.object(
                service, "assess", return_value={"ok": True, "document": doc}), patch.object(
                service, "verify_evidence", return_value=({}, {})), patch(
                "grokbot_mcp.security.prefixes.prospective_file"), patch.object(service, "is_reparse_point", return_value=False), patch(
                "os.open", return_value=123), patch("os.fdopen", return_value=handle), patch("os.close"), patch("os.fsync"):
            result = service.claim_execution(ws, EXECUTION, [])
        self.assertTrue(result["ok"])
        saved = json.loads(handle.write.call_args.args[0])
        self.assertEqual(saved["active_prefixes"], PREFIXES)
        self.assertEqual(result["active_prefixes"], saved["active_prefixes"])
        self.assertEqual(result["active_prefixes_sha256"], saved["active_prefixes_sha256"])
        self.assertEqual(store.update.call_args.kwargs["claim_sha256"], hashlib.sha256(handle.write.call_args.args[0]).hexdigest())


class AdminContractTest(unittest.TestCase):
    def inputs(self):
        source = entry(allowed_prefixes=PREFIXES, prefix_ceiling=CEILING)
        raw = {"schema_version": 1, "workspaces": [source]}
        return raw, json.dumps(raw).encode("utf-8"), _workspace(source)

    def test_preview_apply_backup_audit_and_outside_no_writes(self):
        raw, before, ws = self.inputs()
        target = Path("D:/fictional/registry.json")
        log = Mock()
        log.__enter__ = Mock(return_value=log)
        log.__exit__ = Mock(return_value=False)
        events = []
        with patch.object(registry_prefixes, "load_registry", return_value={ws.workspace_id: ws}), patch.object(
                registry_prefixes, "read_document", return_value=(raw, before)), patch(
                "grokbot_mcp.local_safety.safe_path"), patch("grokbot_mcp.local_safety.local_lock", side_effect=nullcontext) as lock, patch(
                "grokbot_mcp.local_safety.atomic_replace", side_effect=lambda *a, **k: events.append("replace")) as commit, patch.object(
                registry_prefixes, "create_new", side_effect=lambda *a: events.append("backup")) as backup, patch.object(
                Path, "read_bytes", return_value=before), patch.object(Path, "exists", return_value=False), patch.object(
                Path, "open", return_value=log) as opened, patch("os.fsync"):
            result = registry_prefixes.change(target, ws.workspace_id, add=["docs/sub/:.txt"])
            self.assertTrue(result["dry_run"])
            lock.assert_not_called()
            opened.assert_not_called()
            self.assertEqual(result["old_policy_fingerprint"], result["new_policy_fingerprint"])
            for add in (["docs/:.py"], ["other/:.md"], ["docs/../bad/:.md"]):
                with self.assertRaises((RegistryError, ValueError)):
                    registry_prefixes.change(target, ws.workspace_id, add=add, apply=True)
            backup.assert_not_called()
            commit.assert_not_called()
            lock.assert_not_called()
            result = registry_prefixes.change(target, ws.workspace_id, add=["docs/sub/:.txt"], remove=["docs/"], apply=True)
            self.assertTrue(result["applied"])
            self.assertEqual(events, ["backup", "replace"])
            self.assertEqual(backup.call_args.args[1], before)
            self.assertTrue(backup.call_args.args[0].name.startswith("registry.json.bak_"))
            self.assertEqual(commit.call_args.kwargs["expected"], hashlib.sha256(before).hexdigest())
            saved = json.loads(commit.call_args.args[1])["workspaces"][0]
            self.assertEqual(saved["allowed_files"], raw["workspaces"][0]["allowed_files"])
            self.assertFalse(saved["writes_enabled"])
            audit = json.loads(log.write.call_args.args[0])
            self.assertEqual(audit["before_prefixes"], PREFIXES)
            self.assertEqual(audit["after_prefixes"], [{"prefix": "docs/sub/", "exts": [".txt"]}])
            self.assertEqual(audit["before_fingerprint"], audit["after_fingerprint"])
            self.assertEqual(audit["workspace_id"], ws.workspace_id)
            self.assertIn("+00:00", audit["timestamp"])

    def test_cli_cp1252_add_remove_apply(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252")
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(stream), patch.object(
                registry_prefixes, "change", return_value={"ok": True, "message": "授权上限"}) as change:
            self.assertEqual(admin_cli.main(["registry-prefixes", "--registry", "D:/fictional/registry.json",
                "--workspace", "fictional", "--add", "docs/:.md,.txt", "--remove", "docs/", "--apply"]), 0)
        stream.flush()
        self.assertIn(b'"ok": true', buffer.getvalue())
        self.assertEqual(change.call_args.kwargs, {"add": ["docs/:.md,.txt"], "remove": ["docs/"], "apply": True})


class ReconcileContractTest(unittest.TestCase):
    def run_prepare(self, *, result=None, uncertain=False):
        from grokbot_mcp.result_examples import examples
        old = dict(execution_id=EXECUTION, manifest_task_id=TASK, manifest_sha256="a" * 64,
                   submission_state="unknown_submission" if uncertain else "accepted", execution_state="pending")
        records = [old]
        store = Mock()
        store.locked.side_effect = nullcontext
        store.list_records.side_effect = lambda: [dict(r) for r in records]
        store.read.side_effect = lambda i: dict(next(r for r in records if r["execution_id"] == i))
        def update(i, **fields):
            record = next(r for r in records if r["execution_id"] == i)
            record.update(fields)
            return dict(record)
        store.update.side_effect = update
        store.create.side_effect = lambda i, r: records.append(r)
        doc = {"allowed_files": ["src/counter.py"], "execution_timeout": 30,
               "test_command_id": "python_unittest", "task_type": "read_only"}
        report = {"ok": True, "document": doc, "manifest_sha256": "b" * 64,
                  "validation_sha256": "c" * 64, "validation_verified_at": "fixed"}
        parsed = examples()["execute-succeeded"]
        parsed.update(execution_id=EXECUTION, manifest_task_id=TASK, manifest_sha256="a" * 64,
                      machine="EXAMPLE-PC", status="running")
        if result == "bad_identity":
            parsed["machine"] = "OTHER-PC"
        if result in {"succeeded", "inconsistent"}:
            parsed.update(status="succeeded", changed_files=[] if result == "succeeded" else ["src/counter.py"],
                          code_modified=result == "inconsistent",
                          tests={"command_id": "python_unittest", "passed": 1, "failed": 0, "exit_code": 0})
        with patch.object(service, "_ledger", return_value=store), patch.object(service, "assess", return_value=report), patch.object(
                service, "routine_readiness", return_value={"binding_confirmed": True, "binding_revision": 1}), patch.object(
                service, "capture_snapshot", return_value={}), patch.object(service, "write_snapshot", return_value="d" * 64), patch.object(
                Path, "exists", return_value=result is not None), patch("grokbot_mcp.ledger_archive.archived", return_value=False), patch(
                "grokbot_mcp.execution.status.read_result", return_value=(parsed, "e" * 64, "complete")), patch(
                "grokbot_mcp.execution.status.verify_evidence", return_value=({}, {})), patch.object(
                service, "verify_evidence", return_value=({}, {})), patch.object(service, "execution_delta", return_value=[]):
            answer = service.prepare_execution(replace(workspace(), writes_enabled=True), NEXT_TASK, [])
        return answer, old, store

    def test_arrived_failure_settles_and_unblocks_with_same_observer(self):
        answer, old, store = self.run_prepare(result="bad_identity")
        self.assertIsNone(answer["error"])
        self.assertTrue(old["terminal"])
        self.assertEqual(old["execution_state"], "identity_mismatch")
        self.assertTrue(old["result_sha256"])
        store.create.assert_called_once()

    def test_arrived_success_and_inconsistent_terminal_both_unblock(self):
        for result, state in (("succeeded", "succeeded"), ("inconsistent", "result_inconsistent")):
            with self.subTest(result=result):
                answer, old, store = self.run_prepare(result=result)
                self.assertIsNone(answer["error"])
                self.assertTrue(old["terminal"])
                self.assertEqual(old["execution_state"], state)
                store.create.assert_called_once()

    def test_missing_running_and_unknown_submissions_still_block(self):
        for result, uncertain in ((None, False), ("running", False), (None, True)):
            with self.subTest(result=result, uncertain=uncertain):
                answer, old, store = self.run_prepare(result=result, uncertain=uncertain)
                self.assertEqual(answer["error"], "concurrent_execution")
                self.assertEqual(answer["blocking_execution_id"], EXECUTION)
                self.assertIn("不要重发", answer["operator_message"])
                store.create.assert_not_called()
        self.assertFalse(service._in_flight({"terminal": True, "submission_state": "unknown_submission"}))


class DriftContractTest(unittest.TestCase):
    def test_package_and_registry_hashes_and_path_changes(self):
        registry = Path("D:/fictional/registry.json")
        with patch("grokbot_mcp.identity.package_digest", return_value="package-before") as package, patch(
                "grokbot_mcp.identity.registry_digest", return_value="registry-before") as registry_hash:
            identity = RuntimeIdentity(package=Path("D:/fictional/package"), registry=registry)
            self.assertFalse(identity.drift(registry=registry)["package_changed"])
            self.assertFalse(identity.drift(registry=registry)["registry_changed_since_start"])
            package.return_value = "package-after"
            registry_hash.return_value = "registry-after"
            result = identity.drift(registry=registry)
            self.assertTrue(result["package_changed"])
            self.assertTrue(result["registry_changed_since_start"])
            self.assertEqual(result["package_sha256_at_start"], "package-before")
            self.assertEqual(result["package_sha256_current"], "package-after")
            self.assertEqual(result["registry_sha256_at_start"], "registry-before")
            self.assertEqual(result["registry_sha256_current"], "registry-after")
            registry_hash.return_value = "registry-before"
            self.assertTrue(identity.drift(registry=Path("D:/fictional/other.json"))["registry_changed_since_start"])
            package.return_value = None
            self.assertTrue(identity.drift(registry=registry)["package_changed"])

    def test_preflight_read_only_stale_hint_warnings_and_list_scope(self):
        ws = ceiling_workspace(allowed_prefixes=[])
        binding = dict(binding_confirmed=True, submit_ready=True, error=None)
        store = Mock()
        store.list_records.return_value = [dict(execution_id=EXECUTION, execution_state="pending")]
        with patch.object(facade, "_workspace", return_value=(ws, None, [])), patch.object(
                facade, "git_snapshot", return_value={}), patch.object(facade, "git_error", return_value=None), patch.object(
                facade, "resolve_exact_files"), patch.object(facade, "status_for", return_value="present"), patch.object(
                facade, "routine_readiness", return_value=binding), patch.object(facade, "execution_switch_enabled", return_value=False), patch.object(
                facade, "runtime_drift", return_value={"package_changed": True, "registry_changed_since_start": True}), patch.object(
                service, "_ledger", return_value=store), patch.object(Path, "exists", return_value=True), patch(
                "grokbot_mcp.routine_check.packets", return_value=[]), patch.object(facade, "load_registry", return_value={ws.workspace_id: ws}):
            result = facade.preflight(ws.workspace_id)
            listed = facade.list_workspaces()
        self.assertEqual(result["stale_execution_records"], [EXECUTION])
        self.assertEqual(result["blocking_reasons"], ["execution_switch_disabled", "workspace_writes_disabled"])
        self.assertEqual(result["warnings"], ["process_restart_recommended"])
        self.assertEqual(result["prefix_ceiling"], CEILING)
        self.assertEqual(result["allowed_prefixes"], [])
        self.assertEqual(listed["runtime_drift"], result["runtime_drift"])
        self.assertEqual(listed["workspaces"][0]["prefix_ceiling"], CEILING)
        store.update.assert_not_called()
        store.locked.assert_not_called()


class NativeCeilingTest(unittest.TestCase):
    """Requires native Windows Git, locks and writable tempfile fixtures."""
    def setUp(self):
        from tests.support import make_workspace
        from grokbot_mcp.credentials.backend import install_backend
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(install_backend, None, None)
        self.addCleanup(facade.install_transport, None)
        temporary = temporary_directory(prefix="wp1-fictional-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = make_workspace(self.base)
        self.registry = self.base / "registry.json"
        data = json.loads(self.registry.read_text(encoding="utf-8"))
        data["workspaces"][0].update(prefix_ceiling=CEILING, allowed_prefixes=PREFIXES, git_gate="advisory")
        self.registry.write_text(json.dumps(data), encoding="utf-8")
        self.ws = load_registry(self.registry)["example-workspace"]
        for phase in ("validate", "execute"):
            path = self.root / ".bridge" / (phase + "_routine_status.json")
            binding = json.loads(path.read_text(encoding="utf-8"))
            binding["binding"]["policy_fingerprint"] = policy_fingerprint(self.ws)
            path.write_text(json.dumps(binding), encoding="utf-8")
        (self.base / "execution_switch.json").write_text('{"enabled": true}', encoding="utf-8")

    def validated(self):
        from tests.support import write_proof
        task = facade.prepare_task(self.ws.workspace_id, "code_change", "Fictional ceiling document", [],
            ["Measured"], "python_unittest", create_files=["docs/new.md"])
        self.assertIsNone(task.get("error"), task)
        self.assertTrue(facade.submit_validation_tool(self.ws.workspace_id, task["task_id"])["accepted"])
        write_proof(self.root, task["task_id"], task["manifest_sha256"], task["allowed_files"])
        self.assertEqual(facade.get_status(self.ws.workspace_id, task_id=task["task_id"])["validation"]["status"], "succeeded")
        return task

    def start(self):
        task = self.validated()
        execution = facade.prepare_execution_tool(self.ws.workspace_id, task["task_id"])
        self.assertIsNone(execution["error"], execution)
        self.assertTrue(facade.submit_execution_tool(self.ws.workspace_id, execution["execution_id"])["accepted"])
        return task, execution

    def publish(self, task, execution, changed):
        from tests.support import execution_result
        path = self.root / ".bridge/results_execute" / (execution["execution_id"] + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(execution_result(execution["execution_id"], task["task_id"],
                                                   task["manifest_sha256"], changed)), encoding="utf-8")

    def test_admin_actual_preview_backup_audit_and_rejection(self):
        before = self.registry.read_bytes()
        entries = set(self.base.iterdir())
        preview = registry_prefixes.change(self.registry, self.ws.workspace_id, add=["docs/:.txt"])
        self.assertTrue(preview["dry_run"])
        self.assertEqual(set(self.base.iterdir()), entries)
        with self.assertRaisesRegex(RegistryError, "prefix_outside_ceiling"):
            registry_prefixes.change(self.registry, self.ws.workspace_id, add=["other/:.md"], apply=True)
        self.assertEqual(set(self.base.iterdir()), entries)
        result = registry_prefixes.change(self.registry, self.ws.workspace_id, add=["docs/:.txt"], apply=True)
        self.assertEqual((self.base / result["backup"]).read_bytes(), before)
        event = json.loads((self.base / "policy_changes.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(event["before_fingerprint"], event["after_fingerprint"])
        self.assertEqual(prefix_json(load_registry(self.registry)[self.ws.workspace_id]), CEILING)

    def test_claim_and_result_use_frozen_snapshot_after_active_removed(self):
        task, execution = self.start()
        registry_prefixes.change(self.registry, self.ws.workspace_id, remove=["docs/"], apply=True)
        claim = facade.claim_for(self.ws.workspace_id, execution["execution_id"])
        self.assertTrue(claim["ok"], claim)
        self.assertEqual(claim["active_prefixes"], PREFIXES)
        self.assertEqual(len(claim["active_prefixes_sha256"]), 64)
        (self.root / "docs").mkdir()
        (self.root / "docs/new.md").write_text("Fictional\n", encoding="utf-8")
        self.publish(task, execution, ["docs/new.md"])
        status = facade.get_status(self.ws.workspace_id, execution_id=execution["execution_id"])["execution"]
        self.assertEqual(status["status"], "succeeded", status)

    def test_pending_arrival_reconciled_success_and_inconsistent_failure(self):
        for changed in ([], ["docs/new.md"]):
            task, execution = self.start()
            self.assertTrue(facade.claim_for(self.ws.workspace_id, execution["execution_id"])["ok"])
            self.publish(task, execution, changed)
            ledger = self.root / ".bridge/executions" / (execution["execution_id"] + ".json")
            before = ledger.read_bytes()
            facade.get_status(self.ws.workspace_id, execution_id=execution["execution_id"], observe_only=True)
            self.assertEqual(before, ledger.read_bytes())
            preflight = facade.preflight(self.ws.workspace_id)
            self.assertIn(execution["execution_id"], preflight["stale_execution_records"])
            self.assertEqual(before, ledger.read_bytes())
            next_task = self.validated()
            next_execution = facade.prepare_execution_tool(self.ws.workspace_id, next_task["task_id"])
            self.assertIsNone(next_execution["error"], next_execution)
            settled = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertTrue(settled["terminal"])
            self.assertEqual(settled["execution_state"], "succeeded" if not changed else "result_inconsistent")
            # Settle the next grant through normal submit/claim/result APIs too.
            self.assertTrue(facade.submit_execution_tool(self.ws.workspace_id, next_execution["execution_id"])["accepted"])
            self.assertTrue(facade.claim_for(self.ws.workspace_id, next_execution["execution_id"])["ok"])
            self.publish(next_task, next_execution, [])
            facade.get_status(self.ws.workspace_id, execution_id=next_execution["execution_id"])
