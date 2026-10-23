"""One result contract for readers, preflight and Routine writers. Never invent counts."""
from __future__ import annotations

import re
import hashlib
import unicodedata

from grokbot_mcp.security.paths import is_uuid, normalize_repo_path, PathRejected

CONTRACT_VERSION = 1
PHASES = {"validate", "execute"}
STATES = {"pending", "running", "succeeded", "failed"}
_HASH = re.compile(r"^[0-9a-f]{64}$")
OUTPUT_PART_BYTES = 8192


class ContractError(ValueError):
    def __init__(self, field: str, expected: str, *, category: str = "format_error", code: str = "result_format_invalid"):
        self.field, self.expected, self.category, self.code = field, expected, category, code
        super().__init__(code)

    def report(self) -> dict:
        return {"category": self.category, "code": self.code, "field": self.field,
                "expected": self.expected, "payload_included": False}


def _require(condition: bool, field: str, expected: str):
    if not condition:
        raise ContractError(field, expected)


def _paths(value, field):
    _require(isinstance(value, list) and all(type(x) is str for x in value), field, "array of repo-relative strings")
    if field == "changed_files":
        value = [unicodedata.normalize("NFC", x) for x in value]
    try:
        names = [normalize_repo_path(x) for x in value]
    except PathRejected as exc:
        raise ContractError(field, "exact repo-relative paths without traversal") from exc
    _require(names == value and len(set(names)) == len(names), field, "unique normalized repo-relative strings")


def _counts(tests: dict, *, terminal: bool):
    if tests.get("not_run") is True:
        _require(not any(k in tests for k in ("passed", "failed", "exit_code", "case_count", "assertion_count")),
                 "tests", "not_run without invented counters or exit code")
        return
    _require("not_run" not in tests or tests["not_run"] is False, "tests.not_run", "boolean")
    for key in ("passed", "failed", "case_count", "assertion_count"):
        if key in tests or (terminal and key in {"passed", "failed"}):
            _require(type(tests.get(key)) is int and tests[key] >= 0, "tests." + key, "non-negative integer; boolean is not a count")
    if "exit_code" in tests or terminal:
        _require(type(tests.get("exit_code")) is int, "tests.exit_code", "integer exit status; boolean is not an exit code")
    if "unit" in tests:
        _require(tests["unit"] in ("test_cases", "assertions"), "tests.unit", "test_cases or assertions")


def output_evidence(raw: bytes) -> dict:
    """Build embedded UTF-8 output evidence. Hash/count cover the full byte stream."""
    raw.decode("utf-8")  # Validate the full stream, including bytes omitted below.
    head = raw[:OUTPUT_PART_BYTES].decode("utf-8", errors="ignore")
    remainder = raw[len(head.encode("utf-8")):]
    tail = remainder[-OUTPUT_PART_BYTES:].decode("utf-8", errors="ignore")
    truncated = len(head.encode("utf-8")) + len(tail.encode("utf-8")) < len(raw)
    return {"output": {"head": head, "tail": tail, "truncated": truncated},
            "output_sha256": hashlib.sha256(raw).hexdigest(), "output_bytes": len(raw)}


def _output(tests: dict):
    keys = {"output", "output_sha256", "output_bytes"}
    if not keys.intersection(tests):
        return
    output = tests.get("output")
    _require(type(output) is dict and set(output) == {"head", "tail", "truncated"},
             "tests.output", "object with head, tail and truncated")
    for key in ("head", "tail"):
        part = output[key]
        _require(type(part) is str and len(part) <= OUTPUT_PART_BYTES, "tests.output." + key, "UTF-8 string of at most 8192 bytes")
        try:
            encoded = part.encode("utf-8")
        except UnicodeError:
            raise ContractError("tests.output." + key, "valid UTF-8 string") from None
        _require(len(encoded) <= OUTPUT_PART_BYTES, "tests.output." + key, "UTF-8 string of at most 8192 bytes")
    _require(type(output["truncated"]) is bool, "tests.output.truncated", "boolean")
    _require(type(tests.get("output_bytes")) is int and 0 <= tests["output_bytes"] <= 2**63 - 1,
             "tests.output_bytes", "non-negative 64-bit byte count")
    _require(type(tests.get("output_sha256")) is str and bool(_HASH.fullmatch(tests["output_sha256"])),
             "tests.output_sha256", "64 lowercase hexadecimal characters")
    retained = (output["head"] + output["tail"]).encode("utf-8")
    if output["truncated"]:
        _require(0 < len(retained) < tests["output_bytes"], "tests.output_bytes", "larger than retained truncated output")
    else:
        _require(len(retained) == tests["output_bytes"], "tests.output_bytes", "exact full output byte count")
        _require(hashlib.sha256(retained).hexdigest() == tests["output_sha256"], "tests.output_sha256", "digest of complete output")


def validate_result(value: object, phase: str, *, expected: dict | None = None) -> dict:
    if type(phase) is not str or phase not in PHASES:
        raise ContractError("phase", "validate or execute", code="result_phase_invalid")
    _require(type(value) is dict, "$", "JSON object")
    data = value
    _require((type(data.get("protocol_version")) is int and data["protocol_version"] == 1)
             or (type(data.get("protocol_version")) is str and data["protocol_version"] == "1"),
             "protocol_version", "integer 1 (legacy string 1 accepted); never boolean")
    if data.get("action") != phase + "_manifest":
        raise ContractError("action", phase + "_manifest", category="identity_error", code="result_phase_invalid")
    ids = ("task_id",) if phase == "validate" else ("execution_id", "manifest_task_id")
    for key in ids:
        _require(type(data.get(key)) is str and is_uuid(data[key]), key, "canonical lowercase UUID")
    _require(type(data.get("manifest_sha256")) is str and bool(_HASH.fullmatch(data["manifest_sha256"])),
             "manifest_sha256", "64 lowercase hexadecimal characters")
    _require(type(data.get("machine")) is str, "machine", "registered machine string (may be empty)")
    if phase == "validate" or "workspace_id" in data:
        _require(type(data.get("workspace_id")) is str and bool(data["workspace_id"]), "workspace_id", "registered workspace ID")
    _require(type(data.get("status")) is str and data["status"] in STATES, "status", "pending, running, succeeded or failed")
    for key, actual in (expected or {}).items():
        # Execution workspace_id is an additive field; older valid results omitted it.
        if key == "workspace_id" and phase == "execute" and key not in data:
            continue
        if data.get(key) != actual:
            raise ContractError(key, "value matching registered task evidence", category="identity_error", code="result_identity_mismatch")
    terminal = data["status"] in {"succeeded", "failed"}
    for key in ("code_modified", "cursor_cloud_agent_used"):
        if key in data or (terminal and key == "code_modified"):
            _require(type(data.get(key)) is bool, key, "boolean")
    if phase == "validate":
        if terminal or "validation_passed" in data:
            _require(type(data.get("validation_passed")) is bool or (not terminal and data.get("validation_passed") is None),
                     "validation_passed", "boolean at terminal stage; null permitted during progress")
        if terminal or "validation_errors" in data:
            _require(type(data.get("validation_errors")) is list and all(type(x) is str for x in data["validation_errors"]),
                     "validation_errors", "array of strings")
        if terminal or "allowed_files" in data:
            _paths(data.get("allowed_files"), "allowed_files")
        if data["status"] == "succeeded":
            _require(data["validation_passed"] is True and data["code_modified"] is False and not data["validation_errors"],
                     "validation_passed", "true only after successful validation without source changes or errors")
        if data["status"] == "failed":
            _require(data["validation_passed"] is False, "validation_passed", "false for a failed validation")
    else:
        if terminal or "changed_files" in data:
            _paths(data.get("changed_files"), "changed_files")
        if terminal or "tests" in data:
            tests = data.get("tests")
            _require(type(tests) is dict, "tests", "object")
            _require(type(tests.get("command_id")) is str and bool(tests["command_id"]), "tests.command_id", "registered command ID")
            _counts(tests, terminal=terminal)
            _output(tests)
            if tests.get("not_run") is True:
                _require(data["status"] != "succeeded", "tests.not_run", "not_run cannot support succeeded")
            if data["status"] == "succeeded":
                _require(tests["failed"] == 0 and tests["exit_code"] == 0, "status", "succeeded only when failed=0 and exit_code=0")
    return data


def diagnosis(data: dict, phase: str, *, expected: dict | None = None, require_test_output: bool = False) -> dict:
    try:
        validate_result(data, phase, expected=expected)
    except ContractError as exc:
        return exc.report()
    if data["status"] in {"pending", "running"}:
        category = "intermediate_result"
    elif phase == "execute" and require_test_output and "output" not in data.get("tests", {}):
        category = "test_evidence_missing"
    elif phase == "validate" and data["status"] == "failed":
        category = "validation_failure"
    elif phase == "execute" and data.get("tests", {}).get("not_run") is True:
        category = "code_execution_failure"
    elif phase == "execute" and data["status"] == "failed":
        category = "test_failure" if data["tests"].get("failed", 0) or data["tests"].get("exit_code", 0) else "code_execution_failure"
    else:
        category = "format_valid"
    return {"category": category, "code": category, "contract_version": CONTRACT_VERSION,
            "bridge_accepted": False, "qa_accepted": False}


def result_identity(workspace, phase: str, record: dict) -> dict:
    value = {"action": phase + "_manifest", "manifest_sha256": record.get("manifest_sha256"),
             "machine": workspace.expected_machine, "workspace_id": workspace.workspace_id}
    if phase == "validate":
        value["task_id"] = record.get("task_id")
    else:
        value.update(execution_id=record.get("execution_id"), manifest_task_id=record.get("manifest_task_id"))
    return value
