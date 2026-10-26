"""Temporary fictional workspaces for offline tests."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import textwrap
import tempfile
from pathlib import Path

from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.credentials.windows import CredentialError
from grokbot_mcp.facade import install_transport
from grokbot_mcp.transport.webhook import HttpOutcome

WORKSPACE_ID = "example-workspace"
MACHINE = "EXAMPLE-PC"
VALIDATE = "grokbot-mcp/example-workspace/validate"
EXECUTE = "grokbot-mcp/example-workspace/execute"


def temporary_directory(*, prefix: str = "tmp") -> tempfile.TemporaryDirectory:
    """Own a disposable fixture; use with or register cleanup with addCleanup.

    The optional parent must already exist. Keep tempfile's default permissions
    and system-temp behavior; never relax permissions to make a test pass.
    """
    return tempfile.TemporaryDirectory(prefix=prefix, dir=os.environ.get("GROKBOT_TEST_TMPDIR") or None)


class MemoryCredentials:
    def __init__(self) -> None:
        self.data: dict[str, tuple[str, str]] = {
            VALIDATE: ("https://example.invalid/validate", "validate-example"),
            EXECUTE: ("https://example.invalid/execute", "execute-example"),
        }

    def status(self, target: str, others: tuple[str, ...]) -> str:
        if not target or target in others:
            return "unreadable"
        return "present" if target in self.data else "missing"

    def load(self, target: str, others: tuple[str, ...]) -> tuple[str, str]:
        if not target or target in others or target not in self.data:
            raise CredentialError("missing")
        return self.data[target]


class MockTransport:
    def __init__(self, status: int = 200, body: bytes = b"") -> None:
        self.calls: list[dict[str, object]] = []
        self.status = status
        self.body = body

    def __call__(self, url: str, key: str, payload: dict[str, object], timeout: float) -> HttpOutcome:
        self.calls.append({"payload": dict(payload), "timeout": timeout, "saw_key": bool(key), "saw_url": bool(url)})
        from grokbot_mcp.errors.http_errors import classify_http_failure

        diagnosis = None if self.status == 200 else classify_http_failure(self.status, self.body)
        return HttpOutcome(kind="status", status=self.status, diagnosis=diagnosis)


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Example",
            "-c",
            "user.email=dev@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "-C",
            str(root),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return (proc.stdout or "").strip()


def make_workspace(tmp: Path, *, writes: bool = True) -> Path:
    root = tmp / "workspace"
    root.mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "counter.py").write_text("def count_above(values, threshold):\n    return sum(1 for item in values if item > threshold)\n", encoding="utf-8")
    (root / "tests" / "test_counter.py").write_text("import unittest\nfrom src.counter import count_above\n\nclass CounterTest(unittest.TestCase):\n    def test_count(self):\n        self.assertEqual(count_above([1, 3, 5], 2), 2)\n", encoding="utf-8")
    (root / "src" / "do_not_touch.py").write_text("VALUE = 1\n", encoding="utf-8")
    git(root, "init")
    git(root, "add", "src/counter.py", "tests/test_counter.py", "src/do_not_touch.py")
    git(root, "commit", "-m", "example")
    head = git(root, "rev-parse", "HEAD")
    registry = {
        "schema_version": 1,
        "workspaces": [
            {
                "workspace_id": WORKSPACE_ID,
                "root": str(root),
                "expected_head": head,
                "allowed_files": ["src/counter.py", "tests/test_counter.py"],
                "forbidden_files": [".git", ".bridge", "src/do_not_touch.py"],
                "protected_file": "src/do_not_touch.py",
                "test_commands": [
                    {"id": "none", "task_types": ["read_only"], "timeout_cap": 180, "default_timeout": 180},
                    {"id": "python_unittest", "task_types": ["code_change"], "timeout_cap": 600, "default_timeout": 600},
                ],
                "validate_credential_target": VALIDATE,
                "execute_credential_target": EXECUTE,
                "writes_enabled": writes,
                "expected_machine": MACHINE,
            }
        ],
    }
    registry_path = tmp / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    (tmp / "execution_switch.json").write_text('{"enabled": false}\n', encoding="utf-8")
    os.environ["GROKBOT_MCP_REGISTRY"] = str(registry_path)
    install_backend(MemoryCredentials().status, MemoryCredentials().load)
    install_transport(MockTransport())
    bridge = root / ".bridge"
    bridge.mkdir()
    confirmed = {
        "routine_status": "enabled",
        "routine_status_source": "user_confirmed",
        "routine_status_verified_at": "2026-01-01T00:00:00+00:00",
    }
    # Versioned fictional binding, independently assembled from the fixture policy.
    policy = {key: registry["workspaces"][0][key] for key in (
        "workspace_id", "expected_head", "allowed_files", "forbidden_files",
        "protected_file", "test_commands", "expected_machine",
        "validate_credential_target", "execute_credential_target",
    )}
    policy["policy_revision"] = 1
    fingerprint = hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    for phase in ("validate", "execute"):
        data = dict(confirmed)
        data["binding"] = {
            "schema_version": 1, "revision": 1, "mode": "reuse",
            "workspace_id": WORKSPACE_ID, "workspace_root": str(root.resolve()),
            "policy_revision": 1, "policy_fingerprint": fingerprint,
            "action": phase + "_manifest",
            "claim_entry": "grokbot_mcp.claim_cli --workspace " + WORKSPACE_ID,
            "registry_path": str(registry_path.resolve()),
            "protocol_capabilities": ["manifest-v1", "claim-once-v1", "claim-evidence-v2"],
            "template_version": "legacy-unverified",
            "verified_at": "2026-01-01T00:00:00+00:00",
            "verification_source": "administrator_confirmed",
        }
        (bridge / (phase + "_routine_status.json")).write_text(json.dumps(data), encoding="utf-8")
    return root


def make_advisory_workspace(
    tmp: Path, *, writes: bool = True, extra_forbidden: tuple[str, ...] = (),
) -> Path:
    """Create the standard fictional workspace, then opt its registry into advisory."""
    root = make_workspace(tmp, writes=writes)
    registry_path = tmp / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    item = registry["workspaces"][0]
    item["git_gate"] = "advisory"
    item["forbidden_files"].extend(extra_forbidden)
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    policy = {key: item[key] for key in (
        "workspace_id", "expected_head", "allowed_files", "forbidden_files",
        "protected_file", "test_commands", "expected_machine",
        "validate_credential_target", "execute_credential_target", "git_gate",
    )}
    policy.pop("expected_head")
    policy["policy_revision"] = 1
    fingerprint = hashlib.sha256(json.dumps(
        policy, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    for phase in ("validate", "execute"):
        path = root / ".bridge" / (phase + "_routine_status.json")
        document = json.loads(path.read_text(encoding="utf-8"))
        document["binding"]["policy_fingerprint"] = fingerprint
        path.write_text(json.dumps(document), encoding="utf-8")
    return root


def proof(task_id: str, digest: str, allowed: list[str]) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "action": "validate_manifest",
        "task_id": task_id,
        "manifest_sha256": digest,
        "status": "succeeded",
        "validation_passed": True,
        "validation_errors": [],
        "machine": MACHINE,
        "workspace_id": WORKSPACE_ID,
        "code_modified": False,
        "error": None,
        "allowed_files": allowed,
    }


def write_proof(root: Path, task_id: str, digest: str, allowed: list[str]) -> None:
    directory = root / ".bridge" / "results_v2"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{task_id}.json").write_text(json.dumps(proof(task_id, digest, allowed)), encoding="utf-8")


def execution_result(execution_id: str, task_id: str, digest: str, changed: list[str], *, failed: int = 0) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "action": "execute_manifest",
        "execution_id": execution_id,
        "manifest_task_id": task_id,
        "manifest_sha256": digest,
        "machine": MACHINE,
        "status": "succeeded" if failed == 0 else "failed",
        "changed_files": changed,
        "code_modified": bool(changed),
        "cursor_cloud_agent_used": False,
        "tests": {"command_id": "python_unittest", "passed": 1, "failed": failed, "exit_code": 0 if failed == 0 else 1},
    }
