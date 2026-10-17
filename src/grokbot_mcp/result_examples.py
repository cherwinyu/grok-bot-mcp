"""Executable fictional examples: contract checks only, no files/claims/ledger/network."""
from __future__ import annotations
import copy
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.result_contract import validate_result, diagnosis

TASK = "00000000-0000-4000-8000-000000000001"
EXECUTION = "00000000-0000-4000-8000-000000000002"


def examples() -> dict[str, dict]:
    common = {"protocol_version": 1, "manifest_sha256": "a" * 64, "machine": "EXAMPLE-PC",
              "workspace_id": "example-workspace", "cursor_cloud_agent_used": False}
    result = {}
    for phase in ("validate", "execute"):
        for status in ("pending", "running", "succeeded", "failed"):
            value = {**common, "action": phase + "_manifest", "status": status}
            if phase == "validate":
                value["task_id"] = TASK
            else:
                value.update(execution_id=EXECUTION, manifest_task_id=TASK)
            if status in {"succeeded", "failed"}:
                value["code_modified"] = phase == "execute"
                if phase == "validate":
                    value.update(validation_passed=status == "succeeded", allowed_files=["src/counter.py"],
                                 validation_errors=[] if status == "succeeded" else ["fictional fixture: baseline does not match"])
                else:
                    value.update(changed_files=["src/counter.py"], tests={"command_id": "python_unittest",
                        "unit": "test_cases", "passed": 3 if status == "succeeded" else 2,
                        "failed": 0 if status == "succeeded" else 1, "exit_code": 0 if status == "succeeded" else 1,
                        "case_count": 3, "assertion_count": 9})
            result[phase + "-" + status] = value
    failed = copy.deepcopy(result["execute-failed"])
    failed.update(code_modified=False, changed_files=[], tests={"command_id": "python_unittest", "not_run": True})
    result["execute-failed-before-tests"] = failed
    # Fictional measurements only, NEVER inferred from a natural-language report.
    large = copy.deepcopy(result["execute-succeeded"])
    large["tests"].update(passed=6561, failed=0, case_count=6561, assertion_count=19683)
    result["execute-distinct-case-and-assertion-counts"] = large
    return result


def main(argv=None) -> int:
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--example", choices=tuple(examples()))
        args = parser.parse_args(argv)
        values = examples()
        for name, value in values.items():
            validate_result(value, name.split("-")[0])
        emit(values[args.example] if args.example else {"ok": True, "examples_checked": len(values),
            "scope": "fictional contract examples only", "bridge_accepted": False, "qa_accepted": False,
            "diagnoses": {name: diagnosis(value, name.split("-")[0]) for name, value in values.items()}})
        return 0
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
