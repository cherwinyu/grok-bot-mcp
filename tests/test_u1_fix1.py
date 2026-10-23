"""U1 QA regressions using fictional documents; no credentials or transport."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from grokbot_mcp import local_safety, routine, setup_wizard
from grokbot_mcp.registry import load_registry
from grokbot_mcp.strict_json import LedgerTooLarge, MAX_BYTES


def boundary_registry(extra=0):
    value = {"schema_version": 1, "workspaces": [],
             "fictional_lines": ["中"] * 8192, "padding": ""}
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    value["padding"] = "x" * (MAX_BYTES - len(raw) + extra)
    return value


@pytest.mark.parametrize("writer", ["write_json", "write_local"])
def test_windows_newlines_preserve_checked_bytes_and_registry_readback(writer):
    value = boundary_registry()
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    expected = text.encode("utf-8")
    assert len(expected) == MAX_BYTES
    assert expected.count(b"\n") > 8192
    assert len(expected.replace(b"\n", b"\r\n")) > MAX_BYTES
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
    assert raw == expected
    path.is_file.return_value = True
    path.stat.return_value = SimpleNamespace(st_nlink=1, st_size=len(raw))
    path.read_text.return_value = raw.decode("utf-8")
    with patch("grokbot_mcp.security.paths.is_reparse_point", return_value=False):
        assert load_registry(path) == {}


def test_setup_oversize_refused_before_temporary_file_or_replace():
    with patch.object(setup_wizard.tempfile, "mkstemp") as create, \
         patch.object(setup_wizard.os, "replace") as replace:
        with pytest.raises(LedgerTooLarge, match="^ledger_too_large$"):
            setup_wizard.write_json(Mock(), boundary_registry(extra=1))
    create.assert_not_called()
    replace.assert_not_called()


@pytest.mark.parametrize("extra", [0, 1])
def test_registry_roundtrip_disk_at_limit(tmp_path, extra):
    # Requires working native temporary-directory permissions; Director runs this gate.
    path = tmp_path / "fictional-registry.json"
    original = b'{"schema_version":1,"workspaces":[]}'
    path.write_bytes(original)
    value = boundary_registry(extra)
    if extra:
        with pytest.raises(LedgerTooLarge, match="^ledger_too_large$"):
            setup_wizard.write_json(path, value)
        assert path.read_bytes() == original
    else:
        setup_wizard.write_json(path, value)
        expected = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        assert path.stat().st_size == MAX_BYTES
        assert path.read_bytes() == expected
    assert load_registry(path) == {}
    assert list(tmp_path.iterdir()) == [path]


def test_oversized_routine_preserves_error_before_readiness():
    path = Mock()
    path.parents = []
    path.stat.return_value.st_nlink = 1
    with patch("grokbot_mcp.storage.is_reparse_point", return_value=False), \
         patch.object(path, "open", return_value=io.BytesIO(b" " * (MAX_BYTES + 1))):
        with pytest.raises(LedgerTooLarge, match="^ledger_too_large$"):
            routine.read_routine(path)
    with patch.object(routine, "read_json", side_effect=LedgerTooLarge(MAX_BYTES + 1)), \
         patch.object(routine, "binding_error") as binding:
        with pytest.raises(LedgerTooLarge, match="^ledger_too_large$"):
            routine.routine_readiness(SimpleNamespace(root=Path.cwd()), "execute")
    binding.assert_not_called()


def test_oversized_migration_preserves_error_and_blocks_readiness():
    workspace = SimpleNamespace(root=Path.cwd())
    status = {"routine_status": "enabled", "routine_status_source": "user_confirmed",
              "routine_status_verified_at": None, "binding": None}
    with patch.object(local_safety, "safe_path"), \
         patch.object(Path, "exists", return_value=True), \
         patch.object(Path, "open", side_effect=lambda *a, **k: io.BytesIO(b" " * (MAX_BYTES + 1))), \
         patch.object(routine, "read_routine", return_value=status), \
         patch.object(routine, "binding_error", return_value=None), \
         patch.object(routine, "public_binding", return_value=None):
        assert routine.migration_gate(workspace) == "ledger_too_large"
        ready = routine.routine_readiness(workspace, "execute", Path.cwd() / "fictional.json")
    assert ready["error"] == "ledger_too_large"
    assert ready["binding_error"] == "ledger_too_large"
    assert ready["submit_ready"] is False
    assert ready["binding_confirmed"] is False


def test_other_routine_and_migration_errors_keep_existing_refusals():
    with patch.object(routine, "read_json", side_effect=ValueError("json_duplicate_field")):
        status = routine.read_routine(Mock())
    assert status["routine_status_source"] == "unreadable"
    assert routine.routine_allows_submit(status) is False
    with patch.object(local_safety, "safe_path"), \
         patch.object(Path, "exists", return_value=True), \
         patch.object(local_safety, "read_document", side_effect=local_safety.LocalError("json_duplicate_field")):
        assert routine.migration_gate(SimpleNamespace(root=Path.cwd())) == "migration_state_invalid"
