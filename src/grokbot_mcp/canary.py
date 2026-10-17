"""Prepare an isolated, disabled canary workspace. Never sends, claims or grants.

An unsigned fixture marker is provenance for this helper, not live-send authority.
Real acceptance is a separately authorized operator procedure.
"""
from __future__ import annotations
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import tempfile
import uuid

from grokbot_mcp import __version__
from grokbot_mcp.execution_profile import recommended
from grokbot_mcp.identity import digest, path_id
from grokbot_mcp.local_safety import LocalError, safe_path, create_new, json_bytes

FILES = {
    "README.md": "# Disposable acceptance workspace\nNo real project data. Protect this file.\n",
    "src/__init__.py": "",
    "src/counter.py": "def count_above(values, threshold):\n    return sum(1 for value in values if value > threshold)\n",
    "tests/__init__.py": "",
    "tests/test_counter.py": "import unittest\nfrom src.counter import count_above\n\nclass CounterTest(unittest.TestCase):\n    def test_count(self):\n        self.assertEqual(count_above([1, 3, 5], 2), 2)\n",
}


def local_git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL="",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
               "-c", "user.name=Example", "-c", "user.email=dev@example.invalid",
               "-c", "commit.gpgsign=false", "-C", str(root), *args]
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=15, shell=False)
    except (OSError, subprocess.SubprocessError):
        raise LocalError("canary_git_failed") from None
    if result.returncode != 0:
        raise LocalError("canary_git_failed")
    return result.stdout.strip()


def current_machine() -> str:
    """Select a local *declared* host identity; not an attestation of remote tools."""
    try:
        name = os.environ.get("COMPUTERNAME", "").strip() or socket.gethostname().strip()
    except OSError:
        raise LocalError("canary_machine_unavailable") from None
    if not name or len(name) > 255 or any(ord(char) <= 32 or ord(char) == 127 for char in name):
        raise LocalError("canary_machine_unavailable")
    return name


def plan(directory: Path) -> dict:
    safe_path(directory)
    if ".." in directory.parts:
        raise LocalError("canary_destination_protected")
    if not directory.parent.is_dir() or directory.exists():
        raise LocalError("canary_new_directory_required")
    # Explicit CLI input cannot point into another repository or business state.
    if any((p / ".git").exists() or p.name.casefold() == ".bridge" for p in directory.parents):
        raise LocalError("canary_destination_protected")
    basis = {"schema_version": 1, "version": __version__, "directory_id": path_id("canary", directory),
             "expected_machine": current_machine(),
             "files": {name: digest(text.encode("utf-8")) for name, text in FILES.items()},
             "execution_profile": recommended(),
             "credential_namespace_strategy": "random-per-fixture; no lookup or credential creation"}
    return {**basis, "plan_sha256": digest(json_bytes(basis)), "created": False,
            "writes_enabled": False, "execution_switch_enabled": False,
            "credential_values_read": False, "webhook_sent": False, "qa_accepted": False,
            "send_authorized": False, "next_step": "Review this fixture plan; --apply --confirm is explicit local creation only."}


def create(directory: Path, *, confirm: str) -> dict:
    reviewed = plan(directory)
    if confirm != reviewed["plan_sha256"]:
        raise LocalError("plan_confirmation_mismatch")
    if sys.platform != "win32":
        raise LocalError("windows_required")
    # Prepare a private sibling before publishing a complete fixture directory.
    with tempfile.TemporaryDirectory(prefix=".grokbot-canary-", dir=directory.parent) as name:
        stage = Path(name) / "fixture"
        workspace = stage / "workspace"
        workspace.mkdir(parents=True)
        for relative, text in FILES.items():
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            create_new(path, text.encode("utf-8"))
        local_git(workspace, "init", "--template=")
        local_git(workspace, "add", "--", *FILES)
        local_git(workspace, "commit", "-m", "Initial fictional acceptance workspace")
        head = local_git(workspace, "rev-parse", "HEAD")
        # Reuse this one generated identity in the local marker and both target
        # names. Credential Manager is keyed by target, not by registry directory.
        # Independent canaries must not inherit another fixture's saved keys.
        fixture_id = str(uuid.uuid4())
        credential_prefix = "grokbot-mcp/canary-" + fixture_id
        registration = {"schema_version": 1, "workspaces": [{
            "workspace_id": "canary-workspace", "root": str(directory / "workspace"),
            "expected_head": head, "allowed_files": ["src/counter.py"],
            "forbidden_files": [".git", ".bridge", "README.md"], "protected_file": "README.md",
            "test_commands": [{"id": "python_unittest", "task_types": ["code_change"], "timeout_cap": 600, "default_timeout": 180}],
            "validate_credential_target": credential_prefix + "/validate",
            "execute_credential_target": credential_prefix + "/execute",
            "writes_enabled": False, "expected_machine": reviewed["expected_machine"], "execution_profile": recommended()}]}
        create_new(stage / "registry.json", json_bytes(registration))
        create_new(stage / "execution_switch.json", json_bytes({"enabled": False}))
        create_new(stage / "canary.json", json_bytes({"schema_version": 1, "fixture_id": fixture_id,
                   "plan_sha256": confirm, "generator_version": __version__, "origin": "fictional_disabled_workspace",
                   "send_authorized": False, "qa_accepted": False}))
        # Recheck both the chosen location and reviewed plan before directory rename.
        if plan(directory)["plan_sha256"] != confirm:
            raise LocalError("input_changed")
        os.rename(stage, directory)
    return {**reviewed, "created": True, "baseline_sha": head,
            "next_step": "Run the wizard with this explicit registry. No credentials, bindings, task IDs or write grants were created."}
