"""New local cancellation contract; no invented red test on an absent API."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from grokbot_mcp import facade
from grokbot_mcp.cancel_execution_cli import main
from grokbot_mcp.setup_wizard import confirm_binding
from tests import test_reliability
from tests.support import WORKSPACE_ID


class CancellationTest(unittest.TestCase):
    def setUp(self):
        self.c = test_reliability.ReliabilityTest('runTest')
        self.c.setUp()
        self.addCleanup(self.c.doCleanups)
        self.task = self.c.validated()
        self.ex = facade.prepare_execution_tool(WORKSPACE_ID, self.task['task_id'])
        self.args = ['--registry', str(self.c.registry), '--workspace', WORKSPACE_ID,
                     '--execution-id', self.ex['execution_id'], '--reason', 'binding_repair']

    def cancel(self, *, confirm=True):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = main(self.args + (['--confirm'] if confirm else []))
        return code, json.loads(out.getvalue())

    def test_unsubmitted_cancellation_unblocks_binding_without_deleting_evidence(self):
        c = self.c
        c.mutate(c.path('executions', self.ex['execution_id']), binding_revision=None)
        binding_path = c.root / '.bridge/execute_routine_status.json'
        data = json.loads(binding_path.read_text(encoding="utf-8")); data.pop('binding'); c.put(binding_path, data)
        original = {p: p.read_bytes() for p in (c.path('execution_baselines', self.ex['execution_id']),
                    c.path('results_v2', self.task['task_id']), c.path('tasks_v2', self.task['task_id']),
                    c.root / 'src/counter.py')}
        with self.assertRaisesRegex(ValueError, 'unresolved_tasks'):
            confirm_binding(c.registry, WORKSPACE_ID, 'execute', 'reuse')
        count = len(c.transport.calls)
        self.assertEqual(self.cancel()[0], 0)
        confirm_binding(c.registry, WORKSPACE_ID, 'execute', 'reuse')
        for p, raw in original.items():
            self.assertEqual(p.read_bytes(), raw)
        record = json.loads(c.path('executions', self.ex['execution_id']).read_text(encoding="utf-8"))
        self.assertEqual(record['execution_state'], 'cancelled')
        self.assertEqual(record['submission_state'], 'not_sent')
        self.assertIsNone(record['request_attempted_at'])
        self.assertEqual(facade.submit_execution_tool(WORKSPACE_ID, self.ex['execution_id'])['error'], 'not_granted')
        self.assertFalse(facade.claim_for(WORKSPACE_ID, self.ex['execution_id'])['ok'])
        self.assertEqual(len(c.transport.calls), count)
        # A cancelled identity is never recycled; a fresh task is explicit.
        self.assertEqual(facade.prepare_execution_tool(WORKSPACE_ID, self.task['task_id'])['error'], 'duplicate_execution')
        task2 = c.validated()
        self.assertEqual(facade.prepare_execution_tool(WORKSPACE_ID, task2['task_id'])['approval_state'], 'granted')

    def test_missing_confirmation_is_read_only(self):
        ledger = self.c.path('executions', self.ex['execution_id'])
        before = ledger.read_bytes()
        self.assertEqual(self.cancel(confirm=False)[0], 1)
        self.assertEqual(ledger.read_bytes(), before)

    def test_submitted_cannot_be_cancelled(self):
        facade.submit_execution_tool(WORKSPACE_ID, self.ex['execution_id'])
        ledger = self.c.path('executions', self.ex['execution_id'])
        before = ledger.read_bytes()
        self.assertEqual(self.cancel()[0], 1)
        self.assertEqual(ledger.read_bytes(), before)

    def test_unknown_submission_cannot_be_cancelled(self):
        c = self.c
        c.mutate(c.path('executions', self.ex['execution_id']), submission_state='unknown_submission',
                 execution_state='unknown_submission', webhook_attempted=True)
        self.assertEqual(self.cancel()[0], 1)
        self.assertEqual(facade.submit_execution_tool(WORKSPACE_ID, self.ex['execution_id'])['error'], 'retry_forbidden')

    def test_partial_claim_or_result_blocks_cancellation(self):
        c = self.c
        for kind in ('execution_claims', 'results_execute'):
            with self.subTest(kind=kind):
                path = c.path(kind, self.ex['execution_id']); path.parent.mkdir(exist_ok=True)
                path.write_text('{', encoding="utf-8")
                self.assertEqual(self.cancel()[0], 1)
                path.unlink()  # Fixture cleanup only; never an operator recovery procedure.

    def test_cancellation_is_idempotent_and_ignores_late_result(self):
        c = self.c
        self.assertEqual(self.cancel()[0], 0)
        ledger = c.path('executions', self.ex['execution_id']); before = ledger.read_bytes()
        self.assertTrue(self.cancel()[1]['already_cancelled'])
        c.emit(self.task, self.ex)
        for kwargs in ({}, {'verify_current': True}, {'observe_only': True}):
            status = c.status(self.ex, **kwargs)
            self.assertEqual(status['status'], 'cancelled')
            self.assertFalse(status['reported_success'])
            self.assertEqual(status['integrity_error'], 'result_after_cancellation')
        self.assertEqual(ledger.read_bytes(), before)

    def test_incomplete_ledger_does_not_prove_unused_attempt(self):
        c = self.c
        path = c.path('executions', self.ex['execution_id'])
        data = json.loads(path.read_text(encoding="utf-8")); data.pop('request_attempted_at'); c.put(path, data)
        self.assertEqual(self.cancel()[0], 1)

    def test_cancellation_needs_no_credentials_or_open_switch(self):
        c = self.c
        data = json.loads(c.registry.read_text(encoding="utf-8")); data['workspaces'][0]['writes_enabled'] = False; c.put(c.registry, data)
        (c.registry.parent / 'execution_switch.json').write_text('{"enabled":false}', encoding="utf-8")
        with patch('grokbot_mcp.credentials.backend.load_for', side_effect=AssertionError('credentials')):
            self.assertEqual(self.cancel()[0], 0)
