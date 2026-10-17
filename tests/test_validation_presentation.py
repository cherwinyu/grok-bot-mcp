"""Malformed result presentation probes. Fictional loader/ledger, no OS substitutes."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from grokbot_mcp.result_examples import examples
from grokbot_mcp.validation.service import _interpret, get_validation_status

CANARY = 'FICTIONAL_MALFORMED_FIELD_MUST_NOT_BE_ECHOED'


class ValidationPresentationTest(unittest.TestCase):
    def setUp(self):
        self.payload = examples()['validate-succeeded']
        self.workspace = SimpleNamespace(workspace_id=self.payload['workspace_id'],
            expected_machine=self.payload['machine'], expected_head='b'*40,
            root=Path.cwd()/'fictional-unused-workspace')
        self.record = {'task_id': self.payload['task_id'], 'manifest_sha256': self.payload['manifest_sha256']}
        self.store = SimpleNamespace(update=Mock())

    def interpret(self, data):
        document = {'baseline_commit': self.workspace.expected_head, 'allowed_files': ['src/counter.py']}
        with patch('grokbot_mcp.validation.service.load_manifest', return_value=(document, self.record['manifest_sha256'])):
            return _interpret(self.store, self.workspace, self.record, {'qa_accepted': False}, data)

    def assert_withheld(self, result):
        self.assertNotIn(CANARY, json.dumps(result))
        self.assertEqual(result['manifest_sha256'], self.record['manifest_sha256'])
        self.assertEqual(result['workspace_id'], self.workspace.workspace_id)
        for field in ('validation_passed', 'validation_errors', 'allowed_files', 'code_modified'):
            self.assertIsNone(result[field], field)
        self.assertFalse(result['qa_accepted'])

    def test_malformed_boolean_field_does_not_echo_payload(self):
        self.payload['validation_passed'] = {'private': CANARY}
        result = self.interpret(self.payload)
        self.assertEqual(result['result_diagnosis']['category'], 'format_error')
        self.assertEqual(result['result_diagnosis']['field'], 'validation_passed')
        self.assertEqual(result['error'], 'validation_rejected')
        self.assert_withheld(result)

    def test_malformed_error_field_does_not_echo_payload(self):
        self.payload['validation_errors'] = {'private': CANARY}
        result = self.interpret(self.payload)
        self.assertEqual(result['result_diagnosis']['field'], 'validation_errors')
        self.assert_withheld(result)

    def test_malformed_allowed_paths_do_not_echo_payload(self):
        self.payload['allowed_files'] = {'private': CANARY}
        result = self.interpret(self.payload)
        self.assertEqual(result['result_diagnosis']['field'], 'allowed_files')
        self.assert_withheld(result)

    def test_wrong_identity_does_not_echo_untrusted_summary(self):
        self.payload['workspace_id'] = CANARY
        result = self.interpret(self.payload)
        self.assertEqual(result['status'], 'identity_mismatch')
        self.assertEqual(result['result_diagnosis']['category'], 'identity_error')
        self.assert_withheld(result)

    def test_invalid_manifest_digest_is_not_reflected_as_trusted_digest(self):
        self.payload['manifest_sha256'] = CANARY
        result = self.interpret(self.payload)
        self.assertEqual(result['error'], 'identity_mismatch')
        self.assert_withheld(result)

    def test_valid_success_fields_and_decision_are_preserved(self):
        result = self.interpret(self.payload)
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['validation_passed'])
        self.assertFalse(result['code_modified'])
        self.assertEqual(result['validation_errors'], [])
        self.assertEqual(result['allowed_files'], self.payload['allowed_files'])

    def test_valid_validation_failure_keeps_existing_error_semantics(self):
        self.payload.update(status='failed', validation_passed=False, validation_errors=['fictional baseline mismatch'])
        result = self.interpret(self.payload)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], 'validation_rejected')
        self.assertEqual(result['result_diagnosis']['category'], 'validation_failure')
        self.assertEqual(result['validation_errors'], ['fictional baseline mismatch'])
        self.assertFalse(result['validation_passed'])

    def test_cached_malformed_summary_is_withheld_without_rewriting_terminal(self):
        summary = {field: self.payload[field] for field in ('validation_passed','validation_errors','manifest_sha256','workspace_id','allowed_files','code_modified')}
        summary.update(validation_errors={'private':CANARY}, result_diagnosis={
            'category':'format_error','code':'result_format_invalid','field':'validation_errors',
            'expected':'array of strings','payload_included':False}, error='validation_rejected')
        record = {**self.record, 'terminal':True, 'task_state':'failed', 'submission_state':'accepted',
                  'result_sha256':'d'*64, 'historical_status':'failed', 'verified_at':'2026-01-01T00:00:00+00:00',
                  'verified_summary':summary}
        original = copy.deepcopy(record)
        store = SimpleNamespace(read=lambda identity:copy.deepcopy(record), update=Mock(side_effect=AssertionError('must not rewrite frozen ledger')))
        with patch('grokbot_mcp.validation.service._ledger', return_value=store), patch('grokbot_mcp.validation.status.read_result',return_value=(self.payload,'d'*64,'complete')):
            result = get_validation_status(self.workspace, self.record['task_id'], observe_only=True)
        self.assert_withheld(result)
        self.assertEqual(record, original)
        self.assertEqual(result['historical_status'], 'failed')
        self.assertFalse(result['ledger_updated'])
        store.update.assert_not_called()
