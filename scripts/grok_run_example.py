"""Illustrative MCP client orchestration, not a supported bridge interface.

Review this example and the registered policy before use. It never manages
credentials or switches. A successful bridge result still needs independent QA.
Only the seven existing tools are used; transport is injectable for offline tests.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import time
from typing import Protocol
import uuid


class Client(Protocol):
    async def call(self, name: str, arguments: dict) -> dict: ...


class StdioClient:
    def __init__(self, session):
        self.session = session

    async def call(self, name, arguments):
        result = await self.session.call_tool(
            name, arguments, read_timeout_seconds=3700)
        payload = getattr(result, "structured_content", None)
        if payload is None:
            payload = json.loads("".join(getattr(block, "text", "") for block in result.content))
        if not isinstance(payload, dict):
            raise ValueError("invalid_response")
        if getattr(result, "is_error", False):
            # An MCP error is not proof that a possible submission was refused.
            raise ValueError("tool_error")
        return payload


@asynccontextmanager
async def connect(python, registry):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    selected = registry or os.environ.get("GROKBOT_MCP_REGISTRY")
    if selected:
        env["GROKBOT_MCP_REGISTRY"] = selected
    params = StdioServerParameters(command=python,
                                  args=["-I", "-B", "-X", "utf8", "-m", "grokbot_mcp.server"],
                                  env=env)
    # Do not forward raw child diagnostics (which may contain local data).
    with open(os.devnull, "w", encoding="utf-8") as errors:
        async with stdio_client(params, errlog=errors) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await asyncio.wait_for(session.initialize(), 30)
                yield StdioClient(session)


def identity(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("invalid_identity")
    return value


def task_spec(value):
    required = {"task_type", "task_description", "allowed_files", "acceptance_criteria", "test_command_id"}
    optional = {"execution_timeout", "baseline_commit", "allowed_globs", "create_files"}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("invalid_task")
    if value["task_type"] not in {"code_change", "read_only"}:
        raise ValueError("invalid_task")
    for key in ("task_description", "test_command_id"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError("invalid_task")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["test_command_id"]):
        raise ValueError("invalid_task")
    for key in ("allowed_files", "acceptance_criteria", "allowed_globs", "create_files"):
        if key not in value:
            continue
        if not isinstance(value[key], list) or not all(isinstance(x, str) and x.strip() for x in value[key]):
            raise ValueError("invalid_task")
        if key != "acceptance_criteria":
            for item in value[key]:
                path = PureWindowsPath(item)
                if (path.drive or path.root or ".." in path.parts or
                        any(char in item for char in ":\x00\r\n;&|`$<>")):
                    raise ValueError("invalid_task_path")
    if "execution_timeout" in value and (type(value["execution_timeout"]) is not int or
                                         not 1 <= value["execution_timeout"] <= 3600):
        raise ValueError("invalid_task")
    if "baseline_commit" in value and not re.fullmatch(r"[0-9a-f]{40}", str(value["baseline_commit"])):
        raise ValueError("invalid_task")
    # No credential or endpoint input is part of this example's task format.
    if re.search(r"https?://|Bearer\s|\b(?:sk|whsec|rk)_[A-Za-z0-9]+|PRIVATE KEY", json.dumps(value), re.I):
        raise ValueError("invalid_task")
    return dict(value)


# Evidence is a deliberately lossy projection, never a raw response dump.
# Unknown keys, free text, paths, test output and credential metadata are omitted.
STATES = frozenset({"accepted", "not_sent", "submission_started", "unknown_submission",
                   "pending", "running", "prepared", "incomplete", "succeeded", "failed",
                   "cancelled", "timeout", "identity_mismatch", "hash_mismatch", "scope_violation",
                   "result_inconsistent", "external_agent_rejected", "rejected", "archived",
                   "test_evidence_missing", "succeeded_bridge_evidence", "failed_bridge_evidence",
                   "execution_failed", "granted", "denied", "match", "mismatch", "unreported",
                   "validation_missing", "rerun_not_configured", "execution_switch_disabled",
                   "workspace_writes_disabled", "webhook_disabled"})
EVIDENCE_KEYS = frozenset({"ok", "status", "error", "task_id", "execution_id", "manifest_task_id",
                         "accepted", "submission_state", "task_state", "execution_state", "approval_state",
                         "local_ready", "qa_accepted", "webhook_attempted", "webhook_sent",
                         "execution_enabled", "writes_enabled", "validation", "execution", "bridge_tests",
                         "agreement", "exit_code", "output_sha256", "executed", "timed_out",
                         "source_evidence_unchanged", "current_integrity", "integrity_error"})


def evidence_view(value):
    if isinstance(value, dict):
        return {key: evidence_view(item) for key, item in value.items() if key in EVIDENCE_KEYS}
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, str):
        if value in STATES:
            return value
        try:
            return identity(value)
        except (ValueError, TypeError, AttributeError):
            pass
    return "[omitted]"


class Runner:
    def __init__(self, client: Client, workspace, evidence_dir=None, *, clock=time.monotonic, sleep=asyncio.sleep):
        self.client, self.workspace = client, workspace
        self.evidence_dir = evidence_dir
        self.clock, self.sleep = clock, sleep
        self.task_id = self.execution_id = self.qa_accepted = None
        self.phase = "prepare"
        self.command_id = None

    def finish(self, status, reason):
        next_step = "Review evidence and obtain independent QA; never automatically resubmit."
        if status in {"SUBMIT_UNKNOWN", "STATUS_UNKNOWN", "TIMEOUT_PENDING"}:
            option, value = (("--execution-id", self.execution_id) if self.execution_id else
                             ("--task-id", self.task_id))
            next_step = f"Use status --workspace {self.workspace} {option} {value}; never resubmit."
        return dict(status=status, exit_code=0 if status == "PASS" else 3 if status == "FAILED" else 2,
                    reason=reason, task_id=self.task_id, execution_id=self.execution_id,
                    next_step=next_step, qa_accepted=self.qa_accepted,
                    qa_note="独立验收由调用方负责 / Independent acceptance is the caller's responsibility.")

    async def call(self, name, *, timeout=120, **arguments):
        args = arguments if name == "grok_list_workspaces" else {"workspace_id": self.workspace, **arguments}
        result = await asyncio.wait_for(self.client.call(name, args), timeout)
        if not isinstance(result, dict) or not result:
            raise ValueError("invalid_response")
        if "qa_accepted" in result:
            if result["qa_accepted"] is not None and type(result["qa_accepted"]) is not bool:
                raise ValueError("invalid_qa_field")
            self.qa_accepted = result["qa_accepted"]
        if self.evidence_dir is not None:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            with (self.evidence_dir / (uuid.uuid4().hex + ".json")).open("x", encoding="utf-8") as stream:
                json.dump({"tool": name, "response": evidence_view(result)}, stream, ensure_ascii=False)
        return result

    async def submit(self, validation=False):
        self.phase = "submit"
        args = {"task_id": self.task_id} if validation else {"execution_id": self.execution_id}
        result = await self.call("grok_submit_validation" if validation else "grok_submit_execution", **args)
        state = result.get("task_state") if validation else result.get("submission_state")
        if state in {"unknown_submission", "submission_started"}:
            return self.finish("SUBMIT_UNKNOWN", "submission_not_confirmed")
        if (result.get("accepted") is True and state == "accepted" and
                result.get("ok") is not False and not result.get("error")):
            self.phase = "status"
            return None
        if result.get("accepted") is False and result.get("webhook_attempted") is False:
            return self.finish("BLOCKED", "submission_refused_before_send")
        if result.get("accepted") is False and state == "rejected" and type(result.get("http_status")) is int:
            return self.finish("FAILED", "submission_rejected")
        return self.finish("SUBMIT_UNKNOWN", "submission_not_confirmed")

    def section(self, payload, validation=False):
        if payload.get("ok") is False or payload.get("error"):
            raise ValueError("status_error")
        section = payload.get("validation" if validation else "execution")
        key, expected = ("task_id", self.task_id) if validation else ("execution_id", self.execution_id)
        if not isinstance(section, dict) or section.get(key) != expected:
            raise ValueError("status_identity_mismatch")
        if not validation:
            task_id = identity(section.get("manifest_task_id"))
            if self.task_id is not None and self.task_id != task_id:
                raise ValueError("status_identity_mismatch")
            self.task_id = task_id
        if section.get("current_integrity") is False or section.get("integrity_error"):
            raise ValueError("status_integrity_error")
        return section

    async def wait(self, deadline, validation=False):
        self.phase = "status"
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                return self.finish("TIMEOUT_PENDING", "wait_budget_exhausted")
            before = self.clock()
            args = {"task_id": self.task_id} if validation else {"execution_id": self.execution_id}
            try:
                payload = await self.call("grok_get_status", timeout=remaining,
                                          wait_seconds=min(50, int(remaining)), **args)
            except TimeoutError:
                if self.clock() >= deadline:
                    return self.finish("TIMEOUT_PENDING", "wait_budget_exhausted")
                raise
            section = self.section(payload, validation)
            state = section.get("status")
            if state == "unknown_submission":
                return self.finish("SUBMIT_UNKNOWN", "submission_not_confirmed")
            if state == "timeout":
                return self.finish("TIMEOUT_PENDING", "bridge_deadline_elapsed")
            if state in {"pending", "running", "accepted", "prepared", "incomplete"}:
                if section.get("error"):
                    return self.finish("STATUS_UNKNOWN", "status_error")
                await self.sleep(max(0, min(1 - (self.clock() - before), deadline - self.clock())))
                continue
            if state not in STATES:
                return self.finish("STATUS_UNKNOWN", "unrecognized_status")
            if validation:
                if state == "succeeded" and not section.get("error") and section.get("validation_passed") is True:
                    return None
                return self.finish("FAILED", "validation_failed")
            return await self.settle(payload)

    async def settle(self, payload):
        section = self.section(payload)
        state = section.get("status")
        if mismatch(payload):
            return self.finish("AGREEMENT_MISMATCH", "local_test_disagreement")
        if section.get("out_of_scope_changes") or state == "scope_violation":
            return self.finish("OUT_OF_SCOPE", "scope_review_required")
        if state not in {"succeeded", "succeeded_bridge_evidence", "test_evidence_missing"}:
            return self.finish("FAILED", "execution_failed")
        if section.get("error") and state != "test_evidence_missing":
            return self.finish("FAILED", "execution_error")
        # The seven tools do not expose rerun registration. The bridge resolves
        # only the manifest's registered command and safely refuses if absent.
        rerun = await self.call("grok_get_status", execution_id=self.execution_id, rerun_tests=True, timeout=3700)
        repeated = self.section(rerun)
        tests = rerun.get("bridge_tests")
        if mismatch(rerun):
            return self.finish("AGREEMENT_MISMATCH", "local_test_disagreement")
        if repeated.get("out_of_scope_changes") or repeated.get("status") == "scope_violation":
            return self.finish("OUT_OF_SCOPE", "scope_review_required")
        if repeated.get("status") not in {"succeeded", "succeeded_bridge_evidence", "test_evidence_missing"}:
            return self.finish("FAILED", "execution_failed")
        if not isinstance(tests, dict):
            return self.finish("STATUS_UNKNOWN", "local_test_response_missing")
        if tests.get("error") == "rerun_not_configured" and tests.get("executed") is False:
            return self.finish("PASS_PENDING_LOCAL_TEST", "local_rerun_not_registered")
        if (tests.get("execution_id") != self.execution_id or tests.get("source_evidence_unchanged") is not True or
                tests.get("executed") is not True or type(tests.get("exit_code")) is not int):
            return self.finish("STATUS_UNKNOWN", "local_test_evidence_unverified")
        command = self.command_id or section.get("tests", {}).get("command_id")
        if not command or tests.get("command_id") != command:
            return self.finish("STATUS_UNKNOWN", "local_test_command_mismatch")
        if tests["exit_code"] != 0 or tests.get("timed_out") is True:
            return self.finish("FAILED", "local_tests_failed")
        if tests.get("ok") is not True or tests.get("error"):
            return self.finish("STATUS_UNKNOWN", "local_test_evidence_unverified")
        if repeated.get("status") not in {"succeeded", "succeeded_bridge_evidence"} or repeated.get("error"):
            return self.finish("FAILED", "execution_not_verified")
        return self.finish("PASS", "bridge_and_local_tests_passed")

    async def run(self, spec=None, *, wait_budget=900, dry_run=False, execution_id=None, task_id=None):
        try:
            if isinstance(wait_budget, bool) or not math.isfinite(wait_budget) or wait_budget < 0:
                raise ValueError("invalid_wait_budget")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.workspace):
                raise ValueError("invalid_workspace")
            if execution_id or task_id:
                self.phase = "status"
                self.execution_id = identity(execution_id) if execution_id else None
                self.task_id = identity(task_id) if task_id else None
                result = await self.wait(self.clock() + wait_budget, validation=not bool(execution_id))
                return result or self.finish("BLOCKED", "validation_settled_manual_continuation_required")
            spec = task_spec(spec)
            self.command_id = spec["test_command_id"]
            listed = await self.call("grok_list_workspaces")
            if listed.get("ok") is not True or not any(
                    isinstance(x, dict) and x.get("workspace_id") == self.workspace for x in listed.get("workspaces") or []):
                return self.finish("BLOCKED", "workspace_unavailable")
            preflight = await self.call("grok_preflight")
            if preflight.get("local_ready") is not True or preflight.get("error"):
                return self.finish("BLOCKED", "local_preflight_not_ready")
            prepared = await self.call("grok_prepare_task", **spec)
            if prepared.get("error") or prepared.get("ok") is False:
                return self.finish("BLOCKED", "task_preparation_refused")
            self.task_id = identity(prepared.get("task_id"))
            if dry_run:
                return self.finish("BLOCKED", "dry_run_prepared_no_submission")
            grant = await self.call("grok_prepare_execution", manifest_task_id=self.task_id)
            deadline = self.clock() + wait_budget
            if grant.get("error") == "validation_missing" and not grant.get("execution_id"):
                refusal = await self.submit(validation=True)
                if refusal:
                    return refusal
                result = await self.wait(deadline, validation=True)
                if result:
                    return result
                self.phase = "prepare"
                grant = await self.call("grok_prepare_execution", manifest_task_id=self.task_id)
            if (grant.get("error") or grant.get("ok") is False or grant.get("approval_state") != "granted"
                    or grant.get("manifest_task_id") != self.task_id):
                return self.finish("BLOCKED", "execution_preparation_refused")
            self.execution_id = identity(grant.get("execution_id"))
            refusal = await self.submit()
            return refusal or await self.wait(deadline)
        except Exception:
            status = {"submit": "SUBMIT_UNKNOWN", "status": "STATUS_UNKNOWN"}.get(self.phase, "BLOCKED")
            return self.finish(status, "operation_unconfirmed")


def mismatch(payload):
    for part in (payload, payload.get("execution", {}), payload.get("bridge_tests", {})):
        if not isinstance(part, dict):
            continue
        agreement = part.get("agreement")
        if agreement == "mismatch" or (isinstance(agreement, dict) and "mismatch" in agreement.values()):
            return True
    return False


async def execute(args):
    runner = Runner(None, args.workspace, args.evidence_dir)
    result = None
    try:
        if not re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", PureWindowsPath(args.python).name, re.I):
            raise ValueError("python_interpreter_required")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", args.workspace):
            raise ValueError("invalid_workspace")
        if not math.isfinite(args.wait_budget) or args.wait_budget < 0:
            raise ValueError("invalid_wait_budget")
        if args.command == "status":
            runner.execution_id = identity(args.execution_id) if args.execution_id else None
            runner.task_id = identity(args.task_id) if args.task_id else None
        spec = None
        if args.command == "run":
            with args.task_json.open("r", encoding="utf-8") as stream:
                spec = task_spec(json.load(stream))
        async with connect(args.python, args.registry) as client:
            runner.client = client
            result = await runner.run(spec, wait_budget=args.wait_budget,
                                      dry_run=getattr(args, "dry_run", False),
                                      execution_id=getattr(args, "execution_id", None),
                                      task_id=getattr(args, "task_id", None))
    except Exception:
        if result is None:
            result = runner.finish("STATUS_UNKNOWN" if args.command == "status" else "BLOCKED", "client_setup_failed")
    return result


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Prepare and submit once, then observe")
    run.add_argument("--task-json", type=Path, required=True)
    run.add_argument("--dry-run", action="store_true")
    status = commands.add_parser("status", help="Observe an existing identity; never submit")
    ids = status.add_mutually_exclusive_group(required=True)
    ids.add_argument("--execution-id")
    ids.add_argument("--task-id", help="Observe an uncertain Validate submission")
    for command in (run, status):
        command.add_argument("--workspace", required=True)
        command.add_argument("--registry", help="Passed to the server environment only")
        command.add_argument("--python", default=sys.executable)
        command.add_argument("--wait-budget", type=float, default=900)
        command.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args(argv)
    result = asyncio.run(execute(args))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    print(result["next_step"] + " " + result["qa_note"], file=sys.stderr)
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
