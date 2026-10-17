from __future__ import annotations
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from grokbot_mcp.result_examples import examples, main
from grokbot_mcp.result_contract import ContractError, validate_result, diagnosis
from grokbot_mcp.local_safety import LocalError, parse_json
from grokbot_mcp.local_cli import failure


class ResultContractTest(unittest.TestCase):
    def setUp(self):
        self.examples = examples()
        self.good = self.examples["execute-succeeded"]

    def test_all_ten_fictional_examples_validate(self):
        self.assertEqual(len(self.examples), 10)
        for name, value in self.examples.items():
            with self.subTest(name=name):
                self.assertEqual(validate_result(value, name.split("-")[0]), value)

    def test_example_cli_checks_without_writes(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main([]), 0)
        self.assertEqual(json.loads(out.getvalue())["examples_checked"], 10)

    def test_boolean_string_negative_and_missing_counters(self):
        for field in ("passed", "failed", "case_count", "assertion_count"):
            for value in (True, False, "1", -1, 0.5, None, [], {}):
                with self.subTest(field=field, value=value):
                    data = copy.deepcopy(self.good)
                    data["tests"][field] = value
                    issue = diagnosis(data, "execute")
                    self.assertEqual(issue["category"], "format_error")
                    self.assertEqual(issue["field"], "tests." + field)
        for field in ("passed", "failed", "exit_code"):
            data = copy.deepcopy(self.good)
            data["tests"].pop(field)
            self.assertEqual(diagnosis(data, "execute")["category"], "format_error")

    def test_exit_status_requires_integer_but_negative_real_failure_is_valid(self):
        for bad in (True, False, "0", None):
            data = copy.deepcopy(self.good)
            data["tests"]["exit_code"] = bad
            self.assertEqual(diagnosis(data, "execute")["field"], "tests.exit_code")
        data = copy.deepcopy(self.examples["execute-failed"])
        data["tests"]["exit_code"] = -1
        self.assertEqual(diagnosis(data, "execute")["category"], "test_failure")

    def test_cases_and_assertions_have_different_fields(self):
        data = self.examples["execute-distinct-case-and-assertion-counts"]
        self.assertEqual(data["tests"]["passed"], 6561)
        self.assertEqual(data["tests"]["case_count"], 6561)
        self.assertEqual(data["tests"]["assertion_count"], 19683)
        self.assertEqual(data["tests"]["unit"], "test_cases")
        self.assertIs(validate_result(data, "execute"), data)

    def test_progress_may_omit_counts_without_becoming_success(self):
        for phase in ("validate", "execute"):
            for state in ("pending", "running"):
                self.assertEqual(diagnosis(self.examples[phase + "-" + state], phase)["category"], "intermediate_result")

    def test_supplied_progress_counts_still_checked(self):
        value = copy.deepcopy(self.examples["execute-running"])
        value["tests"] = {"command_id": "python_unittest", "passed": True}
        self.assertEqual(diagnosis(value, "execute")["field"], "tests.passed")

    def test_wrong_phase_and_identity_are_distinct(self):
        self.assertEqual(diagnosis(self.good, "validate")["code"], "result_phase_invalid")
        issue = diagnosis(self.good, "execute", expected={"execution_id": "00000000-0000-4000-8000-000000000099"})
        self.assertEqual(issue["category"], "identity_error")
        self.assertEqual(issue["field"], "execution_id")

    def test_protocol_bool_is_not_integer_one(self):
        for value in (True, False, 2, 1.0, "2"):
            data = copy.deepcopy(self.good)
            data["protocol_version"] = value
            self.assertEqual(diagnosis(data, "execute")["field"], "protocol_version")
        data["protocol_version"] = "1"
        self.assertEqual(diagnosis(data, "execute")["category"], "format_valid")

    def test_terminal_required_fields_and_invalid_status(self):
        for field in ("tests", "changed_files", "code_modified", "manifest_sha256", "execution_id", "machine", "status"):
            with self.subTest(field=field):
                data = copy.deepcopy(self.good)
                data.pop(field)
                self.assertEqual(diagnosis(data, "execute")["category"], "format_error")
        data = copy.deepcopy(self.good)
        data["status"] = "unknown"
        self.assertEqual(diagnosis(data, "execute")["field"], "status")

    def test_not_run_requires_no_invented_evidence(self):
        data = self.examples["execute-failed-before-tests"]
        self.assertEqual(diagnosis(data, "execute")["category"], "code_execution_failure")
        data["tests"]["passed"] = 0
        self.assertEqual(diagnosis(data, "execute")["category"], "format_error")

    def test_false_success_cannot_hide_failed_tests(self):
        data = copy.deepcopy(self.good)
        data["tests"]["failed"] = 1
        self.assertEqual(diagnosis(data, "execute")["category"], "format_error")
        data["status"] = "failed"
        self.assertEqual(diagnosis(data, "execute")["category"], "test_failure")

    def test_validation_failure_is_not_execution_failure(self):
        self.assertEqual(diagnosis(self.examples["validate-failed"], "validate")["category"], "validation_failure")
        data = self.examples["validate-succeeded"]
        data["validation_passed"] = 1
        self.assertEqual(diagnosis(data, "validate")["field"], "validation_passed")

    def test_paths_reject_traversal_absolute_and_duplicates(self):
        for paths in (["../outside"], ["C:/fictional/file"], ["/fictional/file"], ["src/counter.py", "src/counter.py"]):
            with self.subTest(paths=paths):
                data = copy.deepcopy(self.good)
                data["changed_files"] = paths
                self.assertEqual(diagnosis(data, "execute")["category"], "format_error")

    def test_parse_partial_json_utf8_bom_duplicates_and_nonfinite(self):
        for raw, code in ((b'{"status":', "json_incomplete_or_invalid"), (b'{"x":"\xe4\xb8', "json_encoding_invalid"),
                          (b'\xef\xbb\xbf{}', "json_encoding_invalid"), (b'{"x":1,"x":2}', "json_duplicate_field"),
                          (b'{"x":NaN}', "json_nonfinite"), (b'[]', "json_object_required")):
            with self.subTest(code=code), self.assertRaises(LocalError) as error:
                parse_json(raw)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(parse_json('{"x":"中文 ✓"}'.encode("utf-8")), {"x": "中文 ✓"})

    def test_errors_withhold_payload_and_exception_text(self):
        data = copy.deepcopy(self.good)
        data["tests"]["passed"] = "fictional-sensitive-marker"
        issue = diagnosis(data, "execute")
        self.assertNotIn("fictional-sensitive-marker", json.dumps(issue))
        self.assertIn("integer", issue["expected"])
        self.assertNotIn("fictional-sensitive-marker", json.dumps(failure(ValueError("fictional-sensitive-marker"))))
