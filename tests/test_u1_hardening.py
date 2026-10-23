"""U1 current-refusal regressions with synthetic data and memory-only doubles."""
from __future__ import annotations

import asyncio
import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from grokbot_mcp import facade, local_safety
from grokbot_mcp.execution.service import _blocked, _interpret, _reported_files
from grokbot_mcp.registry import _workspace, RegistryError, load_registry
from grokbot_mcp.security.baseline import capture_snapshot, _under_watched, write_snapshot, replace_snapshot, load_snapshot
from grokbot_mcp.security.paths import decode_git_path, normalize_policy_path, PathRejected
from grokbot_mcp.storage import JsonLedger, read_json, write_json_new
from grokbot_mcp.strict_json import LedgerTooLarge, MAX_BYTES

ID = "12345678-1234-4234-8234-123456789abc"


class PolicyPathTest(unittest.TestCase):
    def test_dot_and_repeated_separator(self):
        for name in (".//private", "./\\private", "scope/../private", "././private///"):
            with self.subTest(name=name):
                self.assertEqual(normalize_policy_path(name), "private")
                self.assertTrue(_under_watched("Private/secret.tmp", [name]))

    def test_absolute_and_root_escape_refused(self):
        for name in ("/private", "//host/private", "C:/private", "C:private", "../private", "a/../../private", ".//C:/private"):
            with self.subTest(name=name), self.assertRaises(PathRejected):
                normalize_policy_path(name)

    def test_private2_is_not_a_child(self):
        self.assertFalse(_blocked("private2/secret.tmp", {"private"}, advisory=True))
        self.assertFalse(_under_watched("private2/secret.tmp", ["private"]))

    def test_casefold_is_advisory_only(self):
        self.assertTrue(_blocked("Private/secret.tmp", {"private"}, advisory=True))
        self.assertFalse(_blocked("Private/secret.tmp", {"private"}))
        self.assertTrue(_blocked("STRASSE/file", {"straße"}, advisory=True))

    def test_quoted_spaces(self):
        self.assertEqual(normalize_policy_path('"Private/a b.tmp"', git_quoted=True), "Private/a b.tmp")

    def test_octal_utf8(self):
        self.assertEqual(decode_git_path(r'"private/\344\270\255.tmp"'), "private/中.tmp")
        self.assertEqual(normalize_policy_path(r'"\344\270\255/a b.tmp"', git_quoted=True), "中/a b.tmp")

    def test_invalid_octal_utf8_preserved_without_crash(self):
        for raw in (r'"private/\377.tmp"', r'"private/\777.tmp"', r'"private/\q.tmp"'):
            self.assertEqual(decode_git_path(raw), raw)
            with self.assertRaises(PathRejected):
                normalize_policy_path(raw, git_quoted=True)

    def test_c_escapes_and_literal_unicode(self):
        self.assertEqual(decode_git_path(r'"中/a\tb\n\"c\\d"'), '中/a\tb\n"c\\d')

    def test_unicode_nfc(self):
        self.assertEqual(normalize_policy_path("cafe\u0301/a"), "café/a")
        workspace = SimpleNamespace(allowed_files=("café/a",), forbidden_files=("private",))
        self.assertEqual(_reported_files(["cafe\u0301/a"], workspace), ["café/a"])

    def test_advisory_snapshot_decodes_and_filters(self):
        values = iter(("a" * 40, '!! "Private/\\344\\270\\255.tmp"\n!! private2/a.tmp\n!! other.tmp\n', ""))
        runner = lambda *a, **k: subprocess.CompletedProcess([], 0, next(values), "")
        with patch("grokbot_mcp.security.baseline._hash_file", return_value=None):
            snapshot = capture_snapshot(Path.cwd(), [".//private"], runner, include_ignored=False)
        self.assertEqual(snapshot["ignored"], ["Private/中.tmp"])

    def test_strict_snapshot_matches_fixed_prechange_bytes(self):
        values = iter(("a" * 40 + "\n", ' M src/a.py\n?? notes.txt\n!! "Private/a b.tmp"\n!! .bridge/tmp\n', "src/a.py\n"))
        runner = lambda *a, **k: subprocess.CompletedProcess([], 0, next(values), "")
        with patch("grokbot_mcp.security.baseline._hash_file", return_value="fixed-hash"):
            snapshot = capture_snapshot(Path.cwd(), ["src/a.py"], runner)
        expected = (b'{\n  "diff_files": [\n    "src/a.py"\n  ],\n  "file_hashes": {\n'
                    b'    "src/a.py": "fixed-hash"\n  },\n  "git_head_text": "' + b'a' * 40 +
                    b'",\n  "ignored": [\n    "\\"Private/a b.tmp\\""\n  ],\n  "schema_version": 1,\n'
                    b'  "status_paths": [\n    "notes.txt",\n    "src/a.py"\n  ],\n'
                    b'  "untracked": [\n    "notes.txt"\n  ]\n}')
        self.assertEqual(json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8"), expected)

    def entry(self):
        return dict(workspace_id="fictional-workspace", root=str(Path.cwd()), expected_head="a" * 40,
                    allowed_files=["src/a.py"], forbidden_files=[".//private", "./\\locked"],
                    protected_file="scope/../PRIVATE", git_gate="advisory", writes_enabled=False,
                    validate_credential_target="fictional/validate", execute_credential_target="fictional/execute",
                    test_commands=[dict(id="unit", task_types=["code_change"], timeout_cap=10, default_timeout=10)])

    def test_registration_normalizes_paths(self):
        workspace = _workspace(self.entry())
        self.assertEqual(workspace.forbidden_files, ("private", "locked"))
        self.assertEqual(workspace.protected_file, "private")

    def test_registration_rejects_casefold_overlap(self):
        entry = self.entry()
        entry["allowed_files"] = ["Private/a.py"]
        with self.assertRaises(RegistryError):
            _workspace(entry)

    def test_registration_rejects_escape(self):
        entry = self.entry()
        entry["forbidden_files"] = ["../private"]
        with self.assertRaises(RegistryError):
            _workspace(entry)

    def test_reported_protected_case_becomes_scope_violation(self):
        workspace = SimpleNamespace(git_gate="advisory", forbidden_files=("private",),
                                    expected_machine="fictional", workspace_id="fictional-workspace")
        record = dict(execution_id=ID, manifest_task_id=ID, manifest_sha256="a" * 64)
        parsed = dict(protocol_version=1, execution_id=ID, manifest_task_id=ID,
                      manifest_sha256="a" * 64, machine="fictional", action="execute_manifest",
                      changed_files=["Private/secret.tmp"])
        report = {"document": {"allowed_files": ["src/a.py"]}}
        with patch("grokbot_mcp.execution.service.assess", return_value=report), \
             patch("grokbot_mcp.execution.service.verify_evidence", return_value=({}, {})), \
             patch("grokbot_mcp.execution.service._fail", side_effect=lambda s, i, r, c, h: {"error": c}):
            result = _interpret(Mock(), workspace, record, {}, parsed, "b" * 64)
        self.assertEqual(result["error"], "scope_violation")


class SizeBoundaryTest(unittest.TestCase):
    def test_storage_read_reports_size(self):
        path = Mock()
        path.parents = []
        path.stat.return_value.st_nlink = 1
        path.open.return_value.__enter__ = Mock(return_value=io.BytesIO(b" " * (MAX_BYTES + 1)))
        path.open.return_value.__exit__ = Mock(return_value=False)
        with patch("grokbot_mcp.storage.is_reparse_point", return_value=False):
            with self.assertRaisesRegex(LedgerTooLarge, "^ledger_too_large$"):
                read_json(path)

    def test_new_ledger_write_refused_before_open(self):
        path = Mock()
        path.exists.return_value = False
        with patch("grokbot_mcp.storage.is_reparse_point", return_value=False):
            with self.assertRaises(LedgerTooLarge):
                write_json_new(path, {"oversized": "中" * MAX_BYTES})
        path.open.assert_not_called()

    def test_ledger_update_does_not_replace_old_record(self):
        store = JsonLedger(Path.cwd())
        with patch.object(store, "read", return_value={"original": True}), \
             patch("grokbot_mcp.storage.os.replace") as replace:
            with self.assertRaises(LedgerTooLarge):
                store.update(ID, oversized="x" * MAX_BYTES)
        replace.assert_not_called()

    def test_snapshot_create_and_replace_refuse_large_write(self):
        for function in (write_snapshot, replace_snapshot):
            with patch.object(Path, "mkdir"), patch.object(Path, "exists", return_value=False), \
                 patch.object(Path, "is_file", return_value=True), \
                 patch("grokbot_mcp.security.baseline.is_reparse_point", return_value=False), \
                 patch.object(Path, "open") as opened:
                with self.assertRaises(LedgerTooLarge):
                    function(Path.cwd(), ID, {"ignored": ["x" * MAX_BYTES]})
                opened.assert_not_called()

    def test_snapshot_read_reports_limit_before_hash(self):
        with patch.object(Path, "is_file", return_value=True), \
             patch("grokbot_mcp.security.baseline.is_reparse_point", return_value=False), \
             patch.object(Path, "open", return_value=io.BytesIO(b" " * (MAX_BYTES + 1))):
            with self.assertRaises(LedgerTooLarge):
                load_snapshot(Path.cwd(), ID, "a" * 64)

    def test_registry_read_preserves_specific_error(self):
        path = Mock()
        path.parents = []
        path.is_file.return_value = True
        path.stat.return_value = SimpleNamespace(st_nlink=1, st_size=MAX_BYTES + 1)
        with patch("grokbot_mcp.security.paths.is_reparse_point", return_value=False):
            with self.assertRaisesRegex(RegistryError, "ledger_too_large"):
                load_registry(path)
        path.read_text.assert_not_called()

    def test_validation_ledger_error_is_not_masked(self):
        from grokbot_mcp.validation.evidence import verify_validation_decision
        with patch("grokbot_mcp.validation.evidence.read_json", side_effect=LedgerTooLarge(MAX_BYTES + 1)):
            with self.assertRaises(LedgerTooLarge):
                verify_validation_decision(SimpleNamespace(root=Path.cwd()), ID, "a" * 64, "b" * 64, {})

    def test_local_json_read_and_write_same_error(self):
        with self.assertRaisesRegex(local_safety.LocalError, "ledger_too_large"):
            local_safety.parse_json(b" " * (MAX_BYTES + 1))
        with self.assertRaisesRegex(local_safety.LocalError, "ledger_too_large"):
            local_safety.json_bytes({"value": "x" * MAX_BYTES})

    def test_diagnostics_are_bilingual(self):
        from grokbot_mcp.diagnostics import CATALOG
        self.assertIn("4 MiB", CATALOG["ledger_too_large"]["reason"])
        self.assertIn("拒绝", CATALOG["ledger_too_large"]["reason"])


class ToolBoundaryTest(unittest.TestCase):
    def test_seven_tool_schemas_stay_stable(self):
        from grokbot_mcp.server import mcp, TOOL_NAMES
        listed = asyncio.run(mcp.list_tools())
        self.assertEqual({tool.name for tool in listed}, TOOL_NAMES)
        self.assertEqual(len(listed), 7)
        prepared = next(tool for tool in listed if tool.name == "grok_prepare_execution")
        self.assertEqual(set(prepared.input_schema["properties"]), {"workspace_id", "manifest_task_id"})

    def test_stdio_unknown_argument_returns_error_without_exception_group(self):
        # Exercise actual stdio frames with ordinary native pipes. No registered
        # workspace or credentials are opened; the SDK connection gate is separate.
        env = {**os.environ, "GROKBOT_MCP_REGISTRY": str(Path.cwd() / "fictional-absent-u1-registry.json")}
        process = subprocess.Popen([sys.executable, "-m", "grokbot_mcp.server"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env)
        timer = threading.Timer(20, process.kill)
        timer.start()
        try:
            def send(value):
                process.stdin.write(json.dumps(value) + "\n")
                process.stdin.flush()
            send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "fictional-u1", "version": "1"}}})
            self.assertIn("result", json.loads(process.stdout.readline()))
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "grok_get_status", "arguments": {"workspace_id": "fictional", "manifest_task_id": ID}}})
            result = json.loads(process.stdout.readline())["result"]
            self.assertTrue(result["isError"])
            self.assertEqual(result["structuredContent"]["error"], "invalid_argument")
            self.assertIn("execution_id", result["structuredContent"]["accepted_arguments"])
        finally:
            timer.cancel()
            process.terminate()
            process.communicate(timeout=10)

    def test_facade_unknown_keyword_has_accepted_names(self):
        result = facade.get_status(workspace_id="fictional", manifest_task_id=ID)
        self.assertEqual(result["error"], "invalid_argument")
        self.assertIn("task_id", result["accepted_arguments"])
        self.assertNotIn("manifest_task_id", result["accepted_arguments"])

    def test_sdk_unknown_keyword_is_structured(self):
        from grokbot_mcp.server import mcp
        result = asyncio.run(mcp.call_tool("grok_get_status", {"workspace_id": "fictional", "manifest_task_id": ID}))
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["error"], "invalid_argument")

    def test_sdk_wrong_boolean_type_is_structured(self):
        from grokbot_mcp.server import mcp
        result = asyncio.run(mcp.call_tool("grok_get_status", {"workspace_id": "fictional", "observe_only": "false"}))
        self.assertEqual(result.structured_content["error"], "invalid_argument")

    def test_prepare_execution_keeps_manifest_task_id(self):
        from grokbot_mcp.server import mcp
        with patch.object(facade, "prepare_execution_tool", return_value={"ok": True}) as prepare:
            result = asyncio.run(mcp.call_tool("grok_prepare_execution", {"workspace_id": "fictional", "manifest_task_id": ID}))
        self.assertFalse(result.is_error)
        prepare.assert_called_once_with("fictional", ID)

    def test_facade_exception_message_and_log_are_redacted(self):
        with patch.object(facade, "_workspace", side_effect=RuntimeError("fictional-private-key-and-path")), \
             self.assertLogs("grokbot_mcp.tool_errors") as logs:
            result = facade.get_status("fictional", task_id=ID)
        self.assertEqual(result["error"], "internal_error")
        self.assertEqual(result["exception_type"], "RuntimeError")
        self.assertNotIn("fictional-private", json.dumps(result) + str(logs.output))
        self.assertNotIn("Traceback", str(logs.output))

    def test_server_exception_does_not_escape_sdk(self):
        from grokbot_mcp.server import mcp
        with patch.object(facade, "get_status", side_effect=RuntimeError("fictional-private-key")):
            result = asyncio.run(mcp.call_tool("grok_get_status", {"workspace_id": "fictional"}))
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content["error"], "internal_error")
        self.assertNotIn("fictional-private", str(result))

    def test_facade_size_error_is_structured(self):
        with patch.object(facade, "_workspace", side_effect=LedgerTooLarge(MAX_BYTES + 1)):
            result = facade.get_status("fictional", task_id=ID)
        self.assertEqual(result["error"], "ledger_too_large")
        self.assertEqual(result["size_bytes"], MAX_BYTES + 1)


class EncodingAndCollectionTest(unittest.TestCase):
    def test_testpaths_excludes_example_package(self):
        import tomllib
        root = Path(__file__).resolve().parents[1]
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["tool"]["pytest"]["ini_options"]["testpaths"], ["tests"])
        self.assertTrue((root / "examples/minimal_workspace/tests").is_dir())

    def test_text_file_io_and_subprocesses_declare_encodings(self):
        root = Path(__file__).resolve().parents[1]
        for folder in ("src", "scripts", "tests", "examples"):
            for path in (root / folder).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    name = getattr(node.func, "attr", "")
                    keywords = {k.arg: k.value for k in node.keywords}
                    if name in {"read_text", "write_text"}:
                        self.assertIn("encoding", keywords, f"{path.name}:{node.lineno}")
                    if name in {"run", "check_output", "Popen"} and isinstance(keywords.get("text"), ast.Constant) and keywords["text"].value is True:
                        self.assertIn("encoding", keywords, f"{path.name}:{node.lineno}")
                        self.assertIn("errors", keywords, f"{path.name}:{node.lineno}")


if __name__ == "__main__":
    unittest.main()
