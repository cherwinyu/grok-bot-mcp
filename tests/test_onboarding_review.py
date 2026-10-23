"""PR #2 review contracts. Fictional fixtures only; native Windows CI must not skip.

BoundaryProbes retains the seven original review assertions. Only its executable
wrapper/installed-wheel hash pin and Linux substitutions have been removed; the
ReliabilityTest reference is qualified to avoid collecting imported test classes.
The four pinned dev1 failures include the originally static PowerShell finding.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support import temporary_directory
from unittest.mock import patch

from grokbot_mcp import diagnostics as doctor, setup_wizard as wizard, facade
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.registry import RegistryError, load_registry
from tests.support import WORKSPACE_ID, make_workspace
from tests import test_reliability

RESULTS = {}

class BoundaryProbes(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ); self.env.start();self.addCleanup(self.env.stop)
        self.tmp=temporary_directory(prefix='fictional-review-'); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.root=make_workspace(self.base);self.registry=self.base/'registry.json'
        self.addCleanup(install_backend,None,None);self.addCleanup(facade.install_transport,None)
    def report(self,**kw):return doctor.collect(self.registry,WORKSPACE_ID,**kw)
    def snapshot(self):
        return {p.relative_to(self.base).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in self.base.rglob('*') if p.is_file()}
    def test_control_export_valid_registry_blocks_workspace(self):
        before=self.snapshot()
        with self.assertRaises(ValueError):wizard.save_export(self.root/'review-diagnostic.json',self.report(export=True),registry=self.registry)
        self.assertEqual(before,self.snapshot())
        RESULTS['export_valid_registry']={'blocked':True,'files_unchanged':True}
    def test_regression_export_invalid_registry_should_still_block_workspace(self):
        data=json.loads(self.registry.read_text(encoding="utf-8"));data['workspaces'][0]['expected_head']='invalid-fixture-head'
        self.registry.write_text(json.dumps(data), encoding="utf-8")
        report=self.report(export=True);self.assertIn('registry_invalid',{f['code'] for f in report['findings']})
        target=self.root/'review-diagnostic.json'
        blocked=False
        try:wizard.save_export(target,report,registry=self.registry)
        except (ValueError,RegistryError):blocked=True
        RESULTS['export_invalid_registry']={'diagnosed_invalid_registry':True,'blocked':blocked,'new_workspace_file_created':target.exists(),'original_registry_unchanged':json.loads(self.registry.read_text(encoding="utf-8"))==data,'overwrote_existing_file':False}
        self.assertTrue(blocked,'save_export writes a NEW file inside a known workspace when registry validation fails')
    def test_control_export_valid_outside_does_not_touch_credentials(self):
        with patch('grokbot_mcp.credentials.backend.status_for',side_effect=AssertionError('no presence access')), \
             patch('grokbot_mcp.credentials.backend.load_for',side_effect=AssertionError('no secret access')), \
             patch('grokbot_mcp.credentials.windows._apis',side_effect=AssertionError('no credential API')):
            report=self.report(export=True)
        self.assertNotIn('diagnostic_failed',{f['code'] for f in report['findings']})
        self.assertFalse(report['credentials_checked']);self.assertIsNone(report['credentials_ready'])
        target=self.base/'review-diagnostic.json';wizard.save_export(target,report,registry=self.registry)
        data=target.read_text(encoding="utf-8");self.assertNotIn(str(self.root),data)
        RESULTS['export_credential_isolation']={'api_calls':0,'saved_outside_workspace':True,'credential_state':'not_checked'}
    def test_control_readonly_snapshot_leaves_git_and_business_files_unchanged(self):
        before=self.snapshot();report=self.report()
        self.assertTrue(report['complete_for_purpose']);self.assertEqual(before,self.snapshot())
        RESULTS['check_files']={'includes_git_index':True,'files_unchanged':True}
    def test_regression_unknown_submission_should_not_display_submission_permitted(self):
        case=test_reliability.ReliabilityTest('runTest');case.setUp()
        try:
            task=case.validated();ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
            case.mutate(case.path('executions',ex['execution_id']),submission_state='unknown_submission',execution_state='unknown_submission',webhook_attempted=True,submission_final=True,error='unknown_submission')
            report=doctor.collect(case.registry,WORKSPACE_ID,purpose='submit',execution_id=ex['execution_id'])
            codes={f['code'] for f in report['findings']};self.assertIn('unknown_submission',codes);self.assertNotIn('local_cancellation_available',codes)
            refused=facade.submit_execution_tool(WORKSPACE_ID,ex['execution_id'])
            self.assertIsNotNone(refused['error'])
            RESULTS['unknown_submission_summary']={'submit_permitted':report['submit_permitted'],'complete_for_purpose':report['complete_for_purpose'],'codes':sorted(codes),'resubmit_service_error':refused['error'],'cancellation_offered':False}
            self.assertFalse(report['submit_permitted'],'summary says DONE: Submission permitted despite known unknown_submission')
        finally:case.doCleanups()
    def test_regression_terminal_execution_should_not_pass_submission_check(self):
        case=test_reliability.ReliabilityTest('runTest');case.setUp()
        try:
            task,ex=case.start();case.change();case.emit(task,ex)
            self.assertEqual(case.status(ex)['status'],'succeeded')
            report=doctor.collect(case.registry,WORKSPACE_ID,purpose='submit',execution_id=ex['execution_id'])
            refused=facade.submit_execution_tool(WORKSPACE_ID,ex['execution_id'])
            self.assertIsNotNone(refused['error'])
            RESULTS['terminal_execution_summary']={'submit_permitted':report['submit_permitted'],'complete_for_purpose':report['complete_for_purpose'],'codes':sorted({f['code'] for f in report['findings']}),'resubmit_service_error':refused['error']}
            self.assertFalse(report['complete_for_purpose'],'--purpose submit passes for an execution whose repeat submission is forbidden')
        finally:case.doCleanups()
    def test_control_paused_binding_is_separate_from_routine_enabled(self):
        from grokbot_mcp.routine import routine_readiness
        path=self.root/'.bridge/execute_routine_status.json';data=json.loads(path.read_text(encoding="utf-8"));data['routine_status']='paused';path.write_text(json.dumps(data), encoding="utf-8")
        ws=load_registry(self.registry)[WORKSPACE_ID];raw=routine_readiness(ws,'execute',self.registry);report=self.report()
        self.assertTrue(raw['binding_confirmed']);self.assertFalse(raw['routine_enabled'])
        RESULTS['paused_binding_summary']={'original_binding_confirmed':raw['binding_confirmed'],'routine_enabled':raw['routine_enabled'],'diagnostic_administrator_binding_confirmed':report['administrator_binding_confirmed']}

class ExportBoundaryTest(unittest.TestCase):
    setUp = BoundaryProbes.setUp
    report = BoundaryProbes.report
    snapshot = BoundaryProbes.snapshot

    def assert_blocked(self):
        # Both inside and outside destinations must fail with no new files.
        before = self.snapshot()
        for destination in (self.root/'diagnostic.json', self.base/'diagnostic.json'):
            with self.subTest(destination='inside' if destination.parent == self.root else 'outside'):
                with self.assertRaisesRegex(ValueError, '^export_registry_unverified$'):
                    wizard.save_export(destination, self.report(export=True), registry=self.registry)
                self.assertFalse(destination.exists())
                self.assertEqual(before, self.snapshot())

    def test_invalid_schema_rejects_every_destination(self):
        self.registry.write_text('{"schema_version":99,"workspaces":[]}', encoding='utf-8')
        self.assert_blocked()

    def test_corrupt_json_rejects_every_destination(self):
        self.registry.write_text('{', encoding='utf-8')
        self.assert_blocked()

    def test_invalid_entry_does_not_hide_valid_workspace(self):
        data=json.loads(self.registry.read_text(encoding='utf-8'))
        data['workspaces'].append({'workspace_id':'broken-entry','root':str(self.base/'other-root')})
        self.registry.write_text(json.dumps(data), encoding='utf-8')
        self.assert_blocked()

    def test_missing_registry_rejects_every_destination(self):
        self.registry.unlink()
        self.assert_blocked()

    def test_empty_registry_does_not_prove_protection_scope(self):
        self.registry.write_text('{"schema_version":1,"workspaces":[]}', encoding='utf-8')
        self.assert_blocked()

    def test_unreadable_registry_rejects_every_destination(self):
        read_text=Path.read_text
        def read(path, *args, **kwargs):
            # Windows may expand a short temp alias before the actual read.
            if path.resolve() == self.registry.resolve():
                raise PermissionError('FictionalPrivatePermissionMarker')
            return read_text(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            self.assert_blocked()

    def test_preview_remains_available_but_confirmed_save_is_refused(self):
        self.registry.write_text('{', encoding='utf-8')
        before=self.snapshot(); destination=self.base/'diagnostic.json'
        with contextlib.redirect_stdout(io.StringIO()) as out, patch('builtins.input', return_value='y'):
            code=wizard.main(['--registry',str(self.registry),'--workspace',WORKSPACE_ID,
                              '--check','--export',str(destination)])
        self.assertEqual(code,1)
        self.assertIn('"schema_version": 1',out.getvalue())
        self.assertIn('export_registry_unverified',out.getvalue())
        self.assertNotIn('DIAGNOSTIC_SAVED',out.getvalue())
        self.assertNotIn('Traceback',out.getvalue())
        self.assertEqual(before,self.snapshot())

    def test_unreadable_preview_never_echoes_sensitive_exception(self):
        marker='FictionalPrivateNestedPermissionMarker'
        with patch.object(wizard,'load_registry', side_effect=RegistryError('registry_unreadable')), \
             patch('grokbot_mcp.registry.load_registry',side_effect=PermissionError({'nested':marker})), \
             contextlib.redirect_stdout(io.StringIO()) as out, patch('builtins.input',return_value='y'):
            code=wizard.main(['--registry',str(self.registry),'--workspace',WORKSPACE_ID,
                '--check','--export',str(self.base/'diagnostic.json')])
        self.assertEqual(code,1)
        self.assertNotIn(marker,out.getvalue())
        self.assertNotIn('Traceback',out.getvalue())
        self.assertFalse((self.base/'diagnostic.json').exists())

    def test_registry_changed_after_preview_is_rechecked_before_save(self):
        report=self.report(export=True)
        self.registry.write_text('{',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'^export_registry_unverified$'):
            wizard.save_export(self.base/'diagnostic.json',report,registry=self.registry)
        self.assertFalse((self.base/'diagnostic.json').exists())


class SelectedExecutionTest(unittest.TestCase):
    def setUp(self):
        self.c=test_reliability.ReliabilityTest('runTest'); self.c.setUp()
        self.addCleanup(self.c.doCleanups)

    def prepared(self):
        task=self.c.validated()
        execution=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        self.assertIsNone(execution['error'])
        return task,execution

    def report(self, execution_id='', **kwargs):
        return doctor.collect(self.c.registry,WORKSPACE_ID,purpose='submit',execution_id=execution_id,**kwargs)

    def assert_unavailable(self, execution_id):
        before={p.relative_to(self.c.root).as_posix():p.read_bytes() for p in self.c.root.rglob('*') if p.is_file()}
        with patch('grokbot_mcp.credentials.backend.load_for',side_effect=AssertionError('no key reads')), \
             patch('grokbot_mcp.execution.service.submit_execution',side_effect=AssertionError('no submission')), \
             patch('grokbot_mcp.transport.webhook.post_once',side_effect=AssertionError('no Webhook')):
            report=self.report(execution_id)
        self.assertTrue(report['workspace_submit_prerequisites_met'])
        self.assertTrue(report['selected_execution_checked'])
        self.assertFalse(report['selected_execution_submit_permitted'])
        self.assertFalse(report['submit_permitted'])
        self.assertFalse(report['complete_for_purpose'])
        serialized=json.loads(doctor.serialize(report))
        self.assertFalse(serialized['submit_permitted'])
        self.assertFalse(serialized['complete_for_purpose'])
        self.assertNotIn(execution_id,doctor.serialize(report))
        self.assertEqual(before,{p.relative_to(self.c.root).as_posix():p.read_bytes() for p in self.c.root.rglob('*') if p.is_file()})

    def test_workspace_prerequisites_without_selected_execution_are_not_task_permission(self):
        report=self.report()
        self.assertTrue(report['workspace_submit_prerequisites_met'])
        self.assertFalse(report['selected_execution_checked'])
        self.assertIsNone(report['selected_execution_submit_permitted'])
        self.assertTrue(report['submit_permitted'])
        self.assertIn('NOT CHECKED: Selected execution',doctor.render(report))

    def test_prepared_unused_grant_passes_readonly_snapshot(self):
        task,ex=self.prepared()
        report=self.report(ex['execution_id'],task_id=task['task_id'])
        self.assertTrue(report['selected_execution_submit_permitted'])
        self.assertTrue(report['complete_for_purpose'])
        self.assertFalse(json.loads(self.c.path('executions',ex['execution_id']).read_text(encoding="utf-8"))['webhook_attempted'])

    def test_unknown_submission_cannot_pass(self):
        _,ex=self.prepared()
        self.c.mutate(self.c.path('executions',ex['execution_id']),submission_state='unknown_submission',
            execution_state='unknown_submission',webhook_attempted=True,submission_final=True,error='unknown_submission')
        self.assert_unavailable(ex['execution_id'])

    def test_accepted_and_claimed_cannot_pass(self):
        _,ex=self.c.start(claim=False)
        self.assert_unavailable(ex['execution_id'])
        self.assertTrue(facade.claim_for(WORKSPACE_ID,ex['execution_id'])['ok'])
        self.assert_unavailable(ex['execution_id'])

    def test_succeeded_cannot_pass(self):
        task,ex=self.c.start();self.c.change();self.c.emit(task,ex)
        self.assertEqual(self.c.status(ex)['status'],'succeeded')
        self.assert_unavailable(ex['execution_id'])

    def test_failed_cannot_pass(self):
        task,ex=self.c.start();self.c.emit(task,ex,status='failed',changed=[])
        self.assertEqual(self.c.status(ex)['status'],'failed')
        self.assert_unavailable(ex['execution_id'])

    def test_cancelled_cannot_pass(self):
        from grokbot_mcp.cancel_execution_cli import cancel_unsubmitted
        _,ex=self.prepared();workspace=load_registry(self.c.registry)[WORKSPACE_ID]
        self.assertTrue(cancel_unsubmitted(workspace,ex['execution_id'],reason='operator_cancelled')['ok'])
        self.assert_unavailable(ex['execution_id'])

    def test_missing_identity_or_ledger_never_inherits_workspace_permission(self):
        for value in ('not-a-uuid','12345678-1234-1234-1234-123456789abc'):
            with self.subTest(kind='invalid' if value=='not-a-uuid' else 'missing'):
                self.assert_unavailable(value)

    def test_revoked_or_incomplete_grant_cannot_pass(self):
        _,ex=self.prepared();path=self.c.path('executions',ex['execution_id']);original=path.read_bytes()
        self.c.mutate(path,approval_state='revoked')
        self.assert_unavailable(ex['execution_id'])
        for key in ('webhook_attempted','request_attempted_at','submission_state','accepted'):
            with self.subTest(key=key):
                data=json.loads(original);data.pop(key);self.c.put(path,data)
                self.assert_unavailable(ex['execution_id'])

    def test_changed_proof_cannot_pass_even_before_first_submission(self):
        task,ex=self.prepared();path=self.c.path('results_v2',task['task_id'])
        path.write_bytes(path.read_bytes()+b'\n')
        self.assert_unavailable(ex['execution_id'])

    def test_export_selection_never_inspects_credentials(self):
        _,ex=self.prepared()
        with patch('grokbot_mcp.credentials.backend.status_for',side_effect=AssertionError('no presence')), \
             patch('grokbot_mcp.credentials.backend.load_for',side_effect=AssertionError('no load')), \
             patch('grokbot_mcp.credentials.windows._apis',side_effect=AssertionError('no API')):
            report=self.report(ex['execution_id'],export=True)
        self.assertFalse(report['credentials_checked'])
        self.assertNotIn('diagnostic_failed',{f['code'] for f in report['findings']})
        self.assertFalse(report['selected_execution_submit_permitted'])

    def test_paused_routine_preserves_binding_milestone_but_blocks_submission(self):
        _,ex=self.prepared();path=self.c.root/'.bridge/execute_routine_status.json'
        self.c.mutate(path,routine_status='paused')
        report=self.report(ex['execution_id'])
        self.assertTrue(report['administrator_binding_confirmed'])
        self.assertFalse(report['workspace_submit_prerequisites_met'])
        self.assertFalse(report['submit_permitted'])
        self.assertFalse(report['complete_for_purpose'])


class BootstrapConflictTest(unittest.TestCase):
    def test_original_windows_probe_rejects_check_target(self):
        root=Path(__file__).resolve().parents[1]
        proc=subprocess.run([sys.executable,str(root/'scripts/probe_bootstrap_check_target.py'),
            '--source',str(root)],capture_output=True,text=True,timeout=75, encoding="utf-8", errors="replace")
        payload=json.loads(proc.stdout)
        self.assertTrue(payload['legacy_control_selected_helper'])
        self.assertFalse(payload['real_credentials_used'])
        self.assertEqual(proc.returncode,0,payload)
        self.assertEqual(payload['status'],'PASS')
        self.assertFalse(payload['legacy_helper_selected_with_check'])
        self.assertEqual(payload['process_exit_code'],1)

    def test_conflict_is_rejected_before_any_interpreter_call(self):
        shell=shutil.which('pwsh')
        self.assertIsNotNone(shell,'Native Windows PowerShell probe is required; do not skip')
        script=Path(__file__).resolve().parents[1]/'scripts/setup.ps1'
        with temporary_directory(prefix='fictional-check-conflict-') as name:
            root=Path(name);fake=root/'fake-python.cmd';marker=root/'called.txt'
            fake.write_text('@echo off\necho CALLED>"%~dp0called.txt"\nexit /b 0\n',encoding='ascii')
            env={**os.environ,'LOCALAPPDATA':str(root)}
            proc=subprocess.run([shell,'-NoProfile','-NonInteractive','-File',str(script),
                '-Python',str(fake),'-Check','-Target','fictional-target'],env=env,
                capture_output=True,text=True,timeout=30, encoding="utf-8", errors="replace")
            self.assertEqual(proc.returncode,1)
            self.assertIn('invalid_arguments',proc.stdout)
            self.assertFalse(marker.exists())
            self.assertEqual(list(root.iterdir()),[fake])


class WheelEvidenceTest(unittest.TestCase):
    def test_verifier_configures_utf8_even_on_ascii_console(self):
        import runpy
        script=Path(__file__).resolve().parents[1]/'scripts/verify_wheel.py'
        buffer=io.BytesIO()
        stream=io.TextIOWrapper(buffer,encoding='ascii')
        try:
            with patch.object(sys,'argv',[str(script),'--help']), patch.object(sys,'stdout',stream):
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(script),run_name='__main__')
                self.assertEqual(stopped.exception.code,0)
                self.assertEqual(stream.encoding,'utf-8')
                print('Fictional failure path: QA 源码')
                stream.flush()
            self.assertIn('QA 源码',buffer.getvalue().decode('utf-8'))
        finally:
            stream.close()
