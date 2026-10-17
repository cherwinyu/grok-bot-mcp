"""Executable offline demonstration. Own temporary data and memory-only transport.

No existing registry/ledger is an input. This does not exercise a Grok Bot account,
read Credential Manager, attest a remote execution, or grant user-project access.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from grokbot_mcp import __version__, canary, facade
from grokbot_mcp.credentials import backend
from grokbot_mcp.identity import digest
from grokbot_mcp.local_safety import LocalError, json_bytes
from grokbot_mcp.registry import load_registry
from grokbot_mcp.routine import confirmation
from grokbot_mcp.storage import utc_now
from grokbot_mcp.result_cli import stage_candidate, precheck, publish
from grokbot_mcp.transport.webhook import HttpOutcome


def _need(value, code):
    if not value:
        raise LocalError(code)


def run_demo() -> dict:
    if sys.platform != "win32":
        raise LocalError("windows_required")
    calls = []
    def memory_transport(url, key, payload, timeout):
        calls.append(dict(payload))
        return HttpOutcome("status", 200)
    def memory_load(target, others):
        if target in others:
            raise LocalError("target_rejected")
        return "https://example.invalid/fictional", "fixture-only"
    old_status, old_load, old_transport = backend._status, backend._load, facade._TRANSPORT
    old_registry = os.environ.get("GROKBOT_MCP_REGISTRY")
    try:
        with tempfile.TemporaryDirectory(prefix="grokbot-offline-") as temp:
            directory = Path(temp) / "fictional acceptance"
            canary.create(directory, confirm=canary.plan(directory)["plan_sha256"])
            registry = directory / "registry.json"
            data = json.loads(registry.read_bytes())
            data["workspaces"][0]["writes_enabled"] = True  # Only this owned fictional fixture.
            registry.write_bytes(json_bytes(data))
            (directory / "execution_switch.json").write_bytes(json_bytes({"enabled": True}))
            os.environ["GROKBOT_MCP_REGISTRY"] = str(registry)
            backend.install_backend(lambda target, others: "present", memory_load)
            facade.install_transport(memory_transport)
            # Independent fail-fast tripwires in addition to the memory transport.
            with patch("grokbot_mcp.facade.post_once", side_effect=AssertionError("offline_network_forbidden")), \
                 patch("grokbot_mcp.credentials.windows._apis", side_effect=AssertionError("offline_credentials_forbidden")):
                workspace = load_registry(registry)["canary-workspace"]
                root = workspace.root
                bridge = root / ".bridge"
                bridge.mkdir()
                for phase in ("validate", "execute"):
                    value = confirmation(workspace, phase, registry, mode="reuse", revision=1,
                                         verified_at=utc_now(), legacy=False)
                    (bridge / (phase + "_routine_status.json")).write_bytes(json_bytes(value))
                before = (root / "README.md").read_bytes()
                task = facade.prepare_task("canary-workspace", "code_change", "Add count_at_least.",
                         ["src/counter.py"], ["Keep count_above and include equal values in count_at_least."], "python_unittest")
                _need(task.get("task_id"), "offline_prepare_failed")
                _need(facade.submit_validation_tool("canary-workspace", task["task_id"])["accepted"], "offline_submit_failed")
                proof = {"protocol_version": 1, "action": "validate_manifest", "task_id": task["task_id"],
                    "manifest_sha256": task["manifest_sha256"], "machine": workspace.expected_machine, "workspace_id": "canary-workspace",
                    "status": "succeeded", "validation_passed": True, "validation_errors": [],
                    "code_modified": False, "allowed_files": ["src/counter.py"]}
                def deliver(phase, identity, value):
                    source = directory / (phase + "-candidate.json")
                    source.write_bytes(json_bytes(value))
                    staged = stage_candidate(registry, "canary-workspace", phase, identity, source,
                               apply=True, confirm_sha256=digest(source.read_bytes()))
                    path = root / staged["staging_path"]
                    checked = precheck(registry, "canary-workspace", phase, identity, path)
                    published = publish(registry, "canary-workspace", phase, identity, path,
                                         confirm_sha256=checked["staged_sha256"])
                    _need(published["published"] and not published["bridge_accepted"], "offline_publish_failed")
                    return path
                deliver("validate", task["task_id"], proof)
                _need(facade.get_status("canary-workspace", task_id=task["task_id"])["validation"]["status"] == "succeeded", "offline_validation_failed")
                ex = facade.prepare_execution_tool("canary-workspace", task["task_id"])
                _need(ex.get("execution_id"), "offline_grant_failed")
                _need(facade.submit_execution_tool("canary-workspace", ex["execution_id"])["accepted"], "offline_submit_failed")
                _need(facade.claim_for("canary-workspace", ex["execution_id"])["ok"], "offline_claim_failed")
                _need(not facade.claim_for("canary-workspace", ex["execution_id"])["ok"], "offline_duplicate_claim_accepted")
                with (root / "src/counter.py").open("a", encoding="utf-8") as handle:
                    handle.write("\ndef count_at_least(values, threshold):\n    return sum(1 for value in values if value >= threshold)\n")
                runner = ("import unittest,io,json,sys; from src.counter import count_at_least; "
                          "case=unittest.FunctionTestCase(lambda: unittest.TestCase().assertEqual(count_at_least([1,3,5],3),2)); "
                          "suite=unittest.defaultTestLoader.discover('tests'); suite.addTest(case); "
                          "r=unittest.TextTestRunner(stream=io.StringIO()).run(suite); "
                          "print(json.dumps({'passed':r.testsRun-len(r.failures)-len(r.errors),'failed':len(r.failures)+len(r.errors),"
                          "'case_count':r.testsRun,'exit_code':int(not r.wasSuccessful())})); sys.exit(not r.wasSuccessful())")
                result = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", "-c",
                                         "import sys; sys.path.insert(0,sys.argv[1]); " + runner, str(root)], cwd=root,
                                        capture_output=True, text=True, encoding="utf-8", timeout=30, shell=False)
                _need(result.returncode == 0, "offline_fixture_test_failed")
                measured = json.loads(result.stdout)
                output = {"protocol_version": 1, "action": "execute_manifest", "execution_id": ex["execution_id"],
                    "manifest_task_id": task["task_id"], "manifest_sha256": task["manifest_sha256"], "machine": workspace.expected_machine,
                    "status": "succeeded", "changed_files": ["src/counter.py"], "code_modified": True,
                    "tests": {"command_id": "python_unittest", "unit": "test_cases", **measured}}
                final = deliver("execute", ex["execution_id"], output)
                verified = facade.get_status("canary-workspace", execution_id=ex["execution_id"])["execution"]
                _need(verified["status"] == "succeeded", "offline_execution_failed")
                try:
                    precheck(registry, "canary-workspace", "execute", ex["execution_id"], final)
                except LocalError as exc:
                    _need(exc.code == "result_terminal_frozen", "offline_wrong_refusal")
                else:
                    raise LocalError("offline_terminal_not_frozen")
                _need((root / "README.md").read_bytes() == before, "offline_protected_file_changed")
        return {"ok": True, "mode": "OFFLINE_SIMULATION", "version": __version__, "measured_tests": measured,
            "scenarios": ["disabled fixture creation", "memory-only validate and execute", "original claim once",
                          "measured Python tests", "staged result publication", "frozen terminal refusal"],
            "mock_submissions": len(calls), "real_webhooks_sent": 0, "credential_values_read": False,
            "real_environment_modified": False, "temporary_fixture_removed": True,
            "remote_routine_verified": False, "client_reconnection_confirmed": False, "qa_accepted": False}
    finally:
        backend.install_backend(old_status, old_load)
        facade.install_transport(old_transport)
        if old_registry is None:
            os.environ.pop("GROKBOT_MCP_REGISTRY", None)
        else:
            os.environ["GROKBOT_MCP_REGISTRY"] = old_registry
