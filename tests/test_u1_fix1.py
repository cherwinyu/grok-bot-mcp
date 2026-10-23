"""U1 QA regressions using fictional documents; no credentials or transport."""
from contextlib import contextmanager
import io
import json
from pathlib import Path

from tests.support import temporary_directory
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from grokbot_mcp import local_safety, routine, setup_wizard
from grokbot_mcp.registry import load_registry
from grokbot_mcp.strict_json import LedgerTooLarge, MAX_BYTES


def boundary_registry(extra=0):
    value = {"schema_version": 1, "workspaces": [],
             "fictional_lines": ["中"] * 8192, "padding": ""}
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    value["padding"] = "x" * (MAX_BYTES - len(raw) + extra)
    return value


class MemoryRegressionTest(unittest.TestCase):
    def check_windows_newlines(self, writer):
        value = boundary_registry()
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        expected = text.encode("utf-8")
        self.assertEqual(len(expected), MAX_BYTES)
        self.assertGreater(expected.count(b"\n"), 8192)
        self.assertGreater(len(expected.replace(b"\n", b"\r\n")), MAX_BYTES)
        raw_file = io.BytesIO()

        @contextmanager
        def windows_fdopen(descriptor, mode, *, encoding, newline=None):
            # Model Windows translation explicitly, including on supplementary Linux runs.
            stream = io.TextIOWrapper(raw_file, encoding=encoding,
                                      newline="\r\n" if newline is None else newline)
            try:
                yield stream
            finally:
                stream.flush()
                stream.detach()

        path = Mock()
        path.parents = []
        with patch.object(setup_wizard, "safe_path"), \
             patch.object(setup_wizard.tempfile, "mkstemp", return_value=(123, "fictional.tmp")), \
             patch.object(setup_wizard.os, "fdopen", side_effect=windows_fdopen), \
             patch.object(setup_wizard.os, "replace") as replace, \
             patch.object(Path, "unlink"):
            getattr(setup_wizard, writer)(path, value if writer == "write_json" else text)
        replace.assert_called_once_with("fictional.tmp", path)
        raw = raw_file.getvalue()
        self.assertEqual(raw, expected)
        path.is_file.return_value = True
        path.stat.return_value = SimpleNamespace(st_nlink=1, st_size=len(raw))
        path.read_text.return_value = raw.decode("utf-8")
        with patch("grokbot_mcp.security.paths.is_reparse_point", return_value=False):
            self.assertEqual(load_registry(path), {})

    def test_setup_oversize_refused_before_temporary_file_or_replace(self):
        with patch.object(setup_wizard.tempfile, "mkstemp") as create, \
             patch.object(setup_wizard.os, "replace") as replace:
            with self.assertRaisesRegex(LedgerTooLarge, "^ledger_too_large$"):
                setup_wizard.write_json(Mock(), boundary_registry(extra=1))
        create.assert_not_called()
        replace.assert_not_called()

    def test_oversized_routine_preserves_error_before_readiness(self):
        path = Mock()
        path.parents = []
        path.stat.return_value.st_nlink = 1
        with patch("grokbot_mcp.storage.is_reparse_point", return_value=False), \
             patch.object(path, "open", return_value=io.BytesIO(b" " * (MAX_BYTES + 1))):
            with self.assertRaisesRegex(LedgerTooLarge, "^ledger_too_large$"):
                routine.read_routine(path)
        with patch.object(routine, "read_json", side_effect=LedgerTooLarge(MAX_BYTES + 1)), \
             patch.object(routine, "binding_error") as binding:
            with self.assertRaisesRegex(LedgerTooLarge, "^ledger_too_large$"):
                routine.routine_readiness(SimpleNamespace(root=Path.cwd()), "execute")
        binding.assert_not_called()

    def test_oversized_migration_preserves_error_and_blocks_readiness(self):
        workspace = SimpleNamespace(root=Path.cwd())
        status = {"routine_status": "enabled", "routine_status_source": "user_confirmed",
                  "routine_status_verified_at": None, "binding": None}
        with patch.object(local_safety, "safe_path"), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "open", side_effect=lambda *a, **k: io.BytesIO(b" " * (MAX_BYTES + 1))), \
             patch.object(routine, "read_routine", return_value=status), \
             patch.object(routine, "binding_error", return_value=None), \
             patch.object(routine, "public_binding", return_value=None):
            self.assertEqual(routine.migration_gate(workspace), "ledger_too_large")
            ready = routine.routine_readiness(workspace, "execute", Path.cwd() / "fictional.json")
        self.assertEqual(ready["error"], "ledger_too_large")
        self.assertEqual(ready["binding_error"], "ledger_too_large")
        self.assertIs(ready["submit_ready"], False)
        self.assertIs(ready["binding_confirmed"], False)

    def test_other_routine_and_migration_errors_keep_existing_refusals(self):
        with patch.object(routine, "read_json", side_effect=ValueError("json_duplicate_field")):
            status = routine.read_routine(Mock())
        self.assertEqual(status["routine_status_source"], "unreadable")
        self.assertIs(routine.routine_allows_submit(status), False)
        with patch.object(local_safety, "safe_path"), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(local_safety, "read_document", side_effect=local_safety.LocalError("json_duplicate_field")):
            self.assertEqual(routine.migration_gate(SimpleNamespace(root=Path.cwd())), "migration_state_invalid")

    def test_write_json_preserves_checked_bytes_and_registry_readback(self):
        self.check_windows_newlines("write_json")

    def test_write_local_preserves_checked_bytes_and_registry_readback(self):
        self.check_windows_newlines("write_local")


class ConsoleEncodingTest(unittest.TestCase):
    def register_in_memory(self, stdout):
        root = Path.cwd() / "fictional-workspace-中文"
        answers = "\n".join((str(root), "src/counter.py", "", "src/protected.py",
                             "python_unittest", "python -m unittest", "y", "n")) + "\n"
        # Real input() writes the prompts too; only filesystem/Git operations are doubles.
        with patch.object(setup_wizard.sys, "stdout", stdout), \
             patch.object(setup_wizard.sys, "stderr", io.StringIO()), \
             patch.object(setup_wizard.sys, "stdin", io.StringIO(answers)), \
             patch.object(setup_wizard, "safe_path"), \
             patch.object(Path, "exists", return_value=False), \
             patch.object(setup_wizard, "workspace_git_rejection", return_value=None), \
             patch.object(setup_wizard.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "a" * 40)), \
             patch.object(setup_wizard, "resolve_exact_files", side_effect=lambda root, names, forbidden: names), \
             patch.object(setup_wizard, "write_json") as write:
            setup_wizard.register(root / "registry.json", "fictional-workspace", git_gate="advisory")
        write.assert_called_once()
        entry = write.call_args.args[1]["workspaces"][0]
        self.assertEqual(entry["git_gate"], "advisory")
        self.assertFalse(entry["writes_enabled"])

    def test_registration_on_cp1252_console(self):
        reference = io.StringIO()
        self.register_in_memory(reference)
        raw = io.BytesIO()
        with io.TextIOWrapper(raw, encoding="cp1252", newline="\n") as stdout:
            self.register_in_memory(stdout)
            stdout.flush()
            self.assertEqual(stdout.encoding, "cp1252")
            self.assertEqual(raw.getvalue(), reference.getvalue().encode("cp1252", errors="backslashreplace"))

    def test_registration_keeps_utf8_bytes(self):
        reference = io.StringIO()
        self.register_in_memory(reference)
        raw = io.BytesIO()
        with io.TextIOWrapper(raw, encoding="utf-8", newline="\n") as stdout:
            self.register_in_memory(stdout)
            stdout.flush()
            self.assertEqual(raw.getvalue(), reference.getvalue().encode("utf-8"))

    def test_main_help_keeps_console_encoding_and_handles_stderr(self):
        with io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as stdout, \
             io.TextIOWrapper(io.BytesIO(), encoding="cp1252") as stderr, \
             patch.object(setup_wizard.sys, "stdout", stdout), \
             patch.object(setup_wizard.sys, "stderr", stderr):
            with self.assertRaises(SystemExit) as stopped:
                setup_wizard.main(["--help"])
            self.assertEqual(stopped.exception.code, 0)
            self.assertEqual(stdout.encoding, "cp1252")
            self.assertEqual(stderr.encoding, "cp1252")
            stderr.write("中文诊断")


class NativeRegistryRegressionTest(unittest.TestCase):
    def check_registry_roundtrip_disk_at_limit(self, tmp_path, extra):
        # Requires working native temporary-directory permissions; Director runs this gate.
        path = tmp_path / "fictional-registry.json"
        original = b'{"schema_version":1,"workspaces":[]}'
        path.write_bytes(original)
        value = boundary_registry(extra)
        if extra:
            with self.assertRaisesRegex(LedgerTooLarge, "^ledger_too_large$"):
                setup_wizard.write_json(path, value)
            self.assertEqual(path.read_bytes(), original)
        else:
            setup_wizard.write_json(path, value)
            expected = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            self.assertEqual(path.stat().st_size, MAX_BYTES)
            self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(load_registry(path), {})
        self.assertEqual(list(tmp_path.iterdir()), [path])

    def test_registry_roundtrip_disk_at_limit(self):
        with temporary_directory() as name:
            self.check_registry_roundtrip_disk_at_limit(Path(name), 0)

    def test_registry_roundtrip_disk_over_limit(self):
        with temporary_directory() as name:
            self.check_registry_roundtrip_disk_at_limit(Path(name), 1)


if __name__ == "__main__":
    unittest.main()
