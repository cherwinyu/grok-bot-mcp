"""Cross-stage review regressions, fictional data and Mock transport only.

These probes exercise the public workflow, not just isolated check functions.
The pinned review baseline must fail by assertion, not by missing imports/APIs.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import replace

from grokbot_mcp import facade
from grokbot_mcp.registry import load_registry
from grokbot_mcp.routine import confirmation, routine_readiness
from grokbot_mcp.setup_wizard import confirm_binding, generate
from grokbot_mcp.storage import utc_now
from tests.support import WORKSPACE_ID, proof, write_proof
from tests import test_reliability


class ReviewFixTest(unittest.TestCase):
    def setUp(self):
        self.c = test_reliability.ReliabilityTest('runTest')
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)

    def writes(self, enabled):
        c = self.c
        data = json.loads(c.registry.read_text(encoding='utf-8'))
        data['workspaces'][0]['writes_enabled'] = enabled
        c.put(c.registry, data)

    def raw_success(self, *, submit=True):
        c = self.c
        task = c.prepare()
        if submit:
            self.assertEqual(facade.submit_validation_tool(WORKSPACE_ID, task['task_id'])['http_status'], 200)
        write_proof(c.root, task['task_id'], task['manifest_sha256'], ['src/counter.py'])
        return task

    def assert_no_authorization(self, task):
        c = self.c
        count = len(c.transport.calls)
        result = facade.prepare_execution_tool(WORKSPACE_ID, task['task_id'])
        self.assertNotEqual(result['approval_state'], 'granted')
        self.assertIsNone(result['execution_id'])
        self.assertTrue(result['error'])
        self.assertEqual(len(c.transport.calls), count)
        for kind in ('executions', 'execution_baselines'):
            self.assertEqual(list((c.root / '.bridge' / kind).glob('*.json')), [])
        return result

    def test_failed_validation_cannot_be_replaced_to_authorize(self):
        c = self.c
        task = self.raw_success()
        path = c.path('results_v2', task['task_id'])
        c.mutate(path, status='failed', validation_passed=False, validation_errors=['fictional rejection'])
        failed = facade.get_status(WORKSPACE_ID, task_id=task['task_id'])['validation']
        self.assertEqual(failed['status'], 'failed')
        ledger = c.path('tasks_v2', task['task_id'])
        before = ledger.read_bytes()
        write_proof(c.root, task['task_id'], task['manifest_sha256'], ['src/counter.py'])
        status = facade.get_status(WORKSPACE_ID, task_id=task['task_id'])['validation']
        self.assertEqual(status['status'], 'failed')
        self.assertEqual(status['integrity_error'], 'result_changed')
        self.assert_no_authorization(task)
        self.assertEqual(ledger.read_bytes(), before)

    def test_unobserved_success_cannot_authorize(self):
        task = self.raw_success()
        self.assert_no_authorization(task)
        status = facade.get_status(WORKSPACE_ID, task_id=task['task_id'])['validation']
        self.assertEqual(status['status'], 'succeeded')
        self.assertEqual(facade.prepare_execution_tool(WORKSPACE_ID, task['task_id'])['approval_state'], 'granted')

    def test_observe_only_success_cannot_authorize(self):
        task = self.raw_success()
        status = facade.get_status(WORKSPACE_ID, task_id=task['task_id'], observe_only=True)['validation']
        self.assertEqual(status['status'], 'succeeded')
        self.assertFalse(status['ledger_updated'])
        self.assert_no_authorization(task)

    def test_orphan_proof_cannot_authorize(self):
        self.assert_no_authorization(self.raw_success(submit=False))

    def test_deleted_validation_ledger_cannot_authorize(self):
        c = self.c
        task = c.validated()
        c.path('tasks_v2', task['task_id']).unlink()
        self.assert_no_authorization(task)

    def test_reformatted_proof_before_authorization_is_rejected(self):
        c = self.c
        task = c.validated()
        path = c.path('results_v2', task['task_id'])
        path.write_bytes(path.read_bytes() + b'\n')
        self.assert_no_authorization(task)

    def test_wrong_validation_ledger_identity_is_rejected(self):
        c = self.c
        task = c.validated()
        c.mutate(c.path('tasks_v2', task['task_id']), task_id='00000000-0000-4000-8000-000000000099')
        self.assert_no_authorization(task)

    def test_validation_ledger_hash_rechecked_at_submit(self):
        c = self.c
        task = c.validated()
        ex = facade.prepare_execution_tool(WORKSPACE_ID, task['task_id'])
        c.mutate(c.path('tasks_v2', task['task_id']), result_sha256='0' * 64)
        count = len(c.transport.calls)
        result = facade.submit_execution_tool(WORKSPACE_ID, ex['execution_id'])
        self.assertTrue(result['error'])
        self.assertFalse(result['webhook_sent'])
        self.assertEqual(len(c.transport.calls), count)

    def test_validation_ledger_rechecked_at_claim(self):
        c = self.c
        task, ex = c.start(claim=False)
        c.path('tasks_v2', task['task_id']).unlink()
        result = facade.claim_for(WORKSPACE_ID, ex['execution_id'])
        self.assertFalse(result['ok'])
        self.assertFalse(c.path('execution_claims', ex['execution_id']).exists())

    def test_validation_decision_rechecked_at_terminal(self):
        c = self.c
        task, ex = c.start()
        c.mutate(c.path('tasks_v2', task['task_id']), task_state='failed', historical_status='failed')
        c.change()
        c.emit(task, ex)
        self.assertNotEqual(c.status(ex)['status'], 'succeeded')

    def test_validation_decision_rechecked_at_current_verification(self):
        c = self.c
        task, ex = c.start()
        c.change()
        c.emit(task, ex)
        first = c.status(ex)
        self.assertEqual(first['status'], 'succeeded')
        c.path('tasks_v2', task['task_id']).unlink()
        current = c.status(ex, verify_current=True)
        self.assertEqual(current['status'], 'succeeded')
        self.assertEqual(current['verified_at'], first['verified_at'])
        self.assertFalse(current['current_integrity'])

    def test_revoked_writes_prevents_submission_without_consuming_attempt(self):
        c = self.c
        task = c.validated()
        ex = facade.prepare_execution_tool(WORKSPACE_ID, task['task_id'])
        ledger = c.path('executions', ex['execution_id'])
        before = ledger.read_bytes()
        count = len(c.transport.calls)
        self.writes(False)
        self.assertFalse(facade.preflight(WORKSPACE_ID)['submit_permitted'])
        result = facade.submit_execution_tool(WORKSPACE_ID, ex['execution_id'])
        self.assertEqual(result['error'], 'workspace_writes_disabled')
        self.assertFalse(result['webhook_sent'])
        self.assertFalse(result['attempted_this_call'])
        self.assertEqual(len(c.transport.calls), count)
        self.assertEqual(ledger.read_bytes(), before)
        self.writes(True)  # Explicit administrator action, not automatic retry.
        self.assertEqual(facade.submit_execution_tool(WORKSPACE_ID, ex['execution_id'])['http_status'], 200)
        self.assertEqual(len(c.transport.calls), count + 1)

    def test_revoked_writes_prevents_claim_after_acceptance(self):
        c = self.c
        _, ex = c.start(claim=False)
        ledger = c.path('executions', ex['execution_id'])
        before = ledger.read_bytes()
        self.writes(False)
        result = facade.claim_for(WORKSPACE_ID, ex['execution_id'])
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'workspace_writes_disabled')
        self.assertEqual(ledger.read_bytes(), before)
        self.assertFalse(c.path('execution_claims', ex['execution_id']).exists())
        self.writes(True)
        self.assertTrue(facade.claim_for(WORKSPACE_ID, ex['execution_id'])['ok'])
        self.assertEqual(facade.claim_for(WORKSPACE_ID, ex['execution_id'])['error'], 'duplicate_claim')

    def test_direct_service_submission_respects_writes(self):
        from grokbot_mcp.execution.service import submit_execution
        c = self.c
        task = c.validated()
        ex = facade.prepare_execution_tool(WORKSPACE_ID, task['task_id'])
        ws = replace(load_registry(c.registry)[WORKSPACE_ID], writes_enabled=False)
        count = len(c.transport.calls)
        result = submit_execution(ws, ex['execution_id'], switch_enabled=True,
                                  status_fn=lambda: 'present', load_fn=lambda: ('https://example.invalid', 'example'),
                                  transport=c.transport)
        self.assertEqual(result['error'], 'workspace_writes_disabled')
        self.assertEqual(len(c.transport.calls), count)

    def test_direct_service_claim_respects_writes(self):
        from grokbot_mcp.execution.service import claim_execution
        c = self.c
        _, ex = c.start(claim=False)
        ws = replace(load_registry(c.registry)[WORKSPACE_ID], writes_enabled=False)
        self.assertEqual(claim_execution(ws, ex['execution_id'], [])['error'], 'workspace_writes_disabled')

    def test_missing_binding_does_not_create_deadlocked_authorization(self):
        c = self.c
        task = c.validated()
        path = c.root / '.bridge/execute_routine_status.json'
        path.unlink()
        self.assert_no_authorization(task)
        confirm_binding(c.registry, WORKSPACE_ID, 'execute', 'reuse')
        self.assertEqual(facade.prepare_execution_tool(WORKSPACE_ID, task['task_id'])['approval_state'], 'granted')

    def test_wrong_binding_does_not_freeze_authorization(self):
        c = self.c
        task = c.validated()
        path = c.root / '.bridge/execute_routine_status.json'
        data = json.loads(path.read_text(encoding="utf-8"))
        data['binding']['workspace_id'] = 'other-workspace'
        c.put(path, data)
        self.assert_no_authorization(task)

    def test_legacy_claim_entry_cannot_freeze_authorization(self):
        c = self.c
        task = c.validated()
        path = c.root / '.bridge/execute_routine_status.json'
        data = json.loads(path.read_text(encoding="utf-8"))
        data['binding']['claim_entry'] = 'claim_dynamic.py'
        c.put(path, data)
        self.assert_no_authorization(task)

    def test_reuse_instructions_explicitly_require_claim_upgrade(self):
        c = self.c
        path = generate(c.registry, WORKSPACE_ID, 'reuse') / 'routine-setup.md'
        text = path.read_text(encoding='utf-8')
        for required in ('reuse_scope=retain-routine-update-claim', 'claim_dynamic.py',
                         'schema-v2', 'claim_sha256', '旧入口完全不变：不支持'):
            self.assertIn(required, text)
        self.assertIn('grokbot_mcp.claim_cli', text)
        self.assertEqual(c.transport.calls, [])

    def test_confirmation_declares_claim_evidence_v2(self):
        c = self.c
        ws = load_registry(c.registry)[WORKSPACE_ID]
        result = confirmation(ws, 'execute', c.registry, mode='reuse', revision=2, verified_at=utc_now())
        self.assertIn('claim-evidence-v2', result['binding']['protocol_capabilities'])
        self.assertEqual(result['binding']['reuse_scope'], 'retain-routine-update-claim')
        self.assertEqual(result['binding']['template_version'], 'legacy-unverified')

    def test_claim_v1_only_capabilities_do_not_prove_compatibility(self):
        c = self.c
        path = c.root / '.bridge/execute_routine_status.json'
        data = json.loads(path.read_text(encoding="utf-8"))
        data['binding']['protocol_capabilities'] = ['manifest-v1', 'claim-once-v1']
        c.put(path, data)
        ws = load_registry(c.registry)[WORKSPACE_ID]
        self.assertFalse(routine_readiness(ws, 'execute')['binding_confirmed'])

    def test_schema_v1_claim_is_not_silently_blessed(self):
        c = self.c
        task, ex = c.start()
        claim = c.path('execution_claims', ex['execution_id'])
        c.mutate(claim, schema_version=1)
        ledger = c.path('executions', ex['execution_id'])
        data = json.loads(ledger.read_text(encoding="utf-8"))
        data.pop('claim_sha256')
        c.put(ledger, data)
        c.change()
        c.emit(task, ex)
        self.assertEqual(c.status(ex)['error'], 'claim_hash_mismatch')

    def test_bound_paused_routine_can_prepare_but_not_submit(self):
        c = self.c
        task = c.validated()
        c.mutate(c.root / '.bridge/execute_routine_status.json', routine_status='paused')
        ex = facade.prepare_execution_tool(WORKSPACE_ID, task['task_id'])
        self.assertEqual(ex['approval_state'], 'granted')
        self.assertEqual(facade.submit_execution_tool(WORKSPACE_ID, ex['execution_id'])['error'], 'routine_paused')


if __name__ == '__main__':
    unittest.main()
