"""Onboarding/diagnosis contracts on fictional workspaces; never real credentials."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

from tests.support import temporary_directory
from unittest.mock import patch

from grokbot_mcp import __version__, diagnostics as doctor, setup_environment, setup_wizard as wizard
from grokbot_mcp import facade
from grokbot_mcp.cancel_execution_cli import preview_cancellation, cancel_unsubmitted
from grokbot_mcp.credentials.backend import install_backend
from grokbot_mcp.registry import load_registry
from tests import test_reliability
from tests.support import WORKSPACE_ID, make_workspace, git, write_proof


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file() and '.git' not in p.parts}


class OnboardingTest(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = make_workspace(self.base)
        self.registry = self.base / 'registry.json'
        self.args = ['--registry', str(self.registry), '--workspace', WORKSPACE_ID]
        self.addCleanup(install_backend, None, None)
        self.addCleanup(facade.install_transport, None)

    def run_wizard(self, extra=(), answers=()):
        with contextlib.redirect_stdout(io.StringIO()) as out, patch('builtins.input', side_effect=list(answers)):
            code = wizard.main(self.args + list(extra))
        return code, out.getvalue()

    def report(self, **kwargs):
        return doctor.collect(self.registry, WORKSPACE_ID, **kwargs)

    def codes(self, report):
        return {item['code'] for item in report['findings']}

    def mutate_registry(self, **fields):
        data = json.loads(self.registry.read_text(encoding='utf-8'))
        data['workspaces'][0].update(fields)
        self.registry.write_text(json.dumps(data), encoding='utf-8')

    def test_repeat_preserves_every_registration_binding_credential_and_switch(self):
        before = contents(self.base)
        with patch.object(wizard.init_credentials, 'main', side_effect=AssertionError('credential write')):
            for _ in range(2):
                self.assertEqual(self.run_wizard()[0], 0)
        for name, raw in before.items():
            self.assertEqual((self.base / name).read_bytes(), raw, name)

    def test_first_registration_and_resume_do_not_duplicate(self):
        self.registry.unlink()
        (self.base / 'execution_switch.json').unlink()
        for phase in ('validate', 'execute'):
            (self.root / '.bridge' / (phase + '_routine_status.json')).unlink()
        answers = [str(self.root), 'src/counter.py', '', 'src/do_not_touch.py', 'python_unittest',
                   'python -m unittest discover -s tests', 'y', 'n', 'n', 'n']
        self.assertEqual(self.run_wizard(answers=answers)[0], 1)
        before = self.registry.read_bytes()
        self.assertEqual(self.run_wizard(answers=['n', 'n'])[0], 1)
        self.assertEqual(self.registry.read_bytes(), before)
        self.assertEqual(len(load_registry(self.registry)), 1)
        self.assertFalse((self.base / 'execution_switch.json').exists())
        item = json.loads(before)['workspaces'][0]
        self.assertEqual(item['setup_routine_mode'], 'create')
        self.assertFalse(item['writes_enabled'])

    def test_interrupt_after_registration_keeps_completed_stage(self):
        self.registry.unlink()
        for phase in ('validate', 'execute'):
            (self.root / '.bridge' / (phase + '_routine_status.json')).unlink()
        answers = [str(self.root), 'src/counter.py', '', 'src/do_not_touch.py', 'python_unittest',
                   'python -m unittest', 'y', 'n', KeyboardInterrupt()]
        self.assertEqual(self.run_wizard(answers=answers)[0], 130)
        before = self.registry.read_bytes()
        self.assertEqual(self.run_wizard(answers=['n', 'n'])[0], 1)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_interrupt_before_save_leaves_registry_unchanged(self):
        before = self.registry.read_bytes()
        with patch('builtins.input', side_effect=[KeyboardInterrupt()]):
            with self.assertRaises(KeyboardInterrupt):
                wizard.register(self.registry, 'another-workspace')
        self.assertEqual(self.registry.read_bytes(), before)

    def test_readonly_snapshot_no_creates_no_transport_no_credential_load(self):
        before = contents(self.base)
        with patch('grokbot_mcp.transport.webhook.post_once', side_effect=AssertionError('network')), \
             patch('grokbot_mcp.credentials.backend.load_for', side_effect=AssertionError('secret load')):
            self.assertEqual(self.run_wizard(['--check'])[0], 0)
        self.assertEqual(contents(self.base), before)

    def test_safe_defaults_are_not_installation_failure(self):
        self.mutate_registry(writes_enabled=False)
        report = self.report()
        self.assertTrue(report['local_configuration_complete'])
        self.assertTrue(report['administrator_binding_confirmed'])
        self.assertTrue(report['complete_for_purpose'])
        self.assertFalse(report['submit_permitted'])
        for item in report['findings']:
            if item['code'] in doctor.SAFE_DEFAULTS:
                self.assertEqual(item['severity'], 'warning')
        self.assertFalse(self.report(purpose='submit')['complete_for_purpose'])

    def test_check_rejects_every_write_option_before_input(self):
        before = contents(self.base)
        for flags in (['--allow-writes'], ['--replace-credentials'], ['--reconfirm-bindings'],
                      ['--generate-only'], ['--routine-mode', 'reuse']):
            with self.subTest(flags=flags):
                code, text = self.run_wizard(['--check', *flags])
                self.assertEqual(code, 1)
                self.assertIn('invalid_arguments', text)
        self.assertEqual(contents(self.base), before)

    def test_explicit_write_opt_in_changes_only_that_field(self):
        self.mutate_registry(writes_enabled=False)
        before = json.loads(self.registry.read_text(encoding="utf-8"))
        switch = (self.base / 'execution_switch.json').read_bytes()
        bindings = [(self.root / '.bridge' / (phase + '_routine_status.json')).read_bytes() for phase in ('validate','execute')]
        self.assertEqual(self.run_wizard(['--allow-writes'], ['y'])[0], 0)
        expected = before
        expected['workspaces'][0]['writes_enabled'] = True
        self.assertEqual(json.loads(self.registry.read_text(encoding="utf-8")), expected)
        self.assertEqual((self.base / 'execution_switch.json').read_bytes(), switch)
        self.assertEqual(bindings, [(self.root / '.bridge' / (phase + '_routine_status.json')).read_bytes() for phase in ('validate','execute')])

    def test_allow_writes_decline_is_unchanged(self):
        self.mutate_registry(writes_enabled=False)
        before = self.registry.read_bytes()
        self.assertEqual(self.run_wizard(['--allow-writes'], ['n'])[0], 0)
        self.assertEqual(self.registry.read_bytes(), before)

    def test_export_rejects_traversal_into_business_workspace(self):
        outside = self.base / 'outside'
        outside.mkdir()
        target = outside / '..' / self.root.name / 'new-diagnostic.json'
        before = contents(self.base)
        with self.assertRaises(ValueError):
            wizard.save_export(target, self.report(export=True), registry=self.registry)
        self.assertEqual(contents(self.base), before)

    def test_old_test_mapping_is_reported_without_rewriting_registration(self):
        self.mutate_registry(setup_test_instructions={})
        before = self.registry.read_bytes()
        self.assertIn('test_instructions_missing', self.codes(self.report()))
        self.assertEqual(self.registry.read_bytes(), before)

    def test_readonly_disables_git_index_refresh_and_restores_environment_on_error(self):
        before=dict(os.environ)
        def fail():
            self.assertEqual(os.environ['GIT_OPTIONAL_LOCKS'],'0')
            self.assertEqual(os.environ['GROKBOT_MCP_REGISTRY'],str(self.registry))
            raise RuntimeError('fictional hidden exception')
        with patch.object(setup_environment,'inspect_environment',side_effect=fail):
            self.assertIn('diagnostic_failed',self.codes(self.report()))
        self.assertEqual(dict(os.environ),before)

    def test_missing_registry_has_actionable_error(self):
        self.registry.unlink()
        report = self.report()
        self.assertIn('registry_missing', self.codes(report))
        self.assertIn('without --check', doctor.render(report))
        self.assertFalse(self.registry.exists())

    def test_unreadable_and_invalid_registry_have_distinct_codes(self):
        for raw, expected in ((b'{', 'registry_unreadable'), (b'{}', 'registry_invalid')):
            with self.subTest(expected=expected):
                self.registry.write_bytes(raw)
                report = self.report()
                self.assertIn(expected, self.codes(report))
                self.assertEqual(self.registry.read_bytes(), raw)

    def test_unknown_workspace_has_no_path_echo(self):
        marker = 'fictional-unknown-private-workspace'
        report = doctor.collect(self.registry, marker)
        self.assertIn('unknown_workspace', self.codes(report))
        self.assertNotIn(marker, doctor.serialize(report))

    def test_invalid_workspace_path_is_readable(self):
        self.mutate_registry(root=str(self.base / 'absent'))
        report = self.report()
        self.assertFalse(report['workspace_ready'])
        self.assertTrue({'workspace_path_invalid', 'target_file_missing'} & self.codes(report))

    def test_missing_credential_identifies_phase_without_target(self):
        with patch('grokbot_mcp.credentials.backend.status_for', return_value='missing'):
            report = self.report()
        self.assertFalse(report['credentials_ready'])
        self.assertEqual({x['phase'] for x in report['findings'] if x['code']=='credentials_missing'}, {'validate','execute'})
        self.assertNotIn('grokbot-mcp/example-workspace/validate', doctor.serialize(report))

    def test_routine_paused_unknown_and_binding_failures_are_distinct(self):
        path = self.root / '.bridge/execute_routine_status.json'
        original = path.read_bytes()
        variants = [('routine_paused', lambda d: d.update(routine_status='paused')),
                    ('binding_unknown', lambda d: d.pop('binding')),
                    ('binding_workspace_mismatch', lambda d: d['binding'].update(workspace_id='another-workspace')),
                    ('binding_policy_mismatch', lambda d: d['binding'].update(policy_fingerprint='0'*64)),
                    ('binding_claim_entry_mismatch', lambda d: d['binding'].update(claim_entry='claim_dynamic.py')),
                    ('binding_capability_missing', lambda d: d['binding'].update(protocol_capabilities=['manifest-v1','claim-once-v1']))]
        for code, change in variants:
            with self.subTest(code=code):
                data=json.loads(original); change(data); path.write_text(json.dumps(data), encoding="utf-8")
                self.assertIn(code, self.codes(self.report()))
        path.unlink()
        self.assertIn('routine_status_unknown', self.codes(self.report()))

    def test_head_mismatch_never_recommends_advancing(self):
        self.mutate_registry(expected_head='0'*40)
        report=self.report()
        self.assertIn('git_head_mismatch', self.codes(report))
        self.assertIn('Do not auto-advance', doctor.render(report))
        self.assertEqual(load_registry(self.registry)[WORKSPACE_ID].expected_head,'0'*40)

    def test_spaces_chinese_roots_and_config(self):
        moved=self.base/'演示 工作区'
        self.root.rename(moved); self.root=moved
        self.mutate_registry(root=str(moved))
        for phase in ('validate','execute'):
            wizard.confirm_binding(self.registry, WORKSPACE_ID, phase, 'reuse')
        self.assertTrue(self.report()['complete_for_purpose'])
        output=wizard.generate(self.registry, WORKSPACE_ID)
        config=json.loads((output/'mcp-config.json').read_text(encoding='utf-8'))['mcpServers']['grokbot-mcp']
        self.assertEqual(config['command'], str(Path(sys.executable).absolute()))
        self.assertTrue(Path(config['command']).is_absolute())
        self.assertEqual(config['env']['GROKBOT_MCP_REGISTRY'],str(self.registry))
        self.assertIn(str(moved),(output/'routine-setup.md').read_text(encoding='utf-8'))

    def test_generated_reuse_packet_does_not_create_duplicate_routines(self):
        output=wizard.generate(self.registry, WORKSPACE_ID, 'reuse')
        text=(output/'routine-setup.md').read_text(encoding='utf-8')
        for expected in ('claim-evidence-v2','claim_dynamic.py','retain-routine-update-claim','grokbot_mcp.claim_cli','<execution_id>','validate_manifest','execute_manifest'):
            self.assertIn(expected,text)
        self.assertIn('do not duplicate',text)
        self.assertNotIn('Create a second long-lived routine.',text)

    def test_new_packet_and_reuse_have_distinct_modes(self):
        output=wizard.generate(self.registry, WORKSPACE_ID, 'create')
        create=(output/'routine-setup.md').read_bytes()
        wizard.generate(self.registry, WORKSPACE_ID, 'reuse')
        self.assertNotEqual(create,(output/'routine-setup.md').read_bytes())

    def test_export_skips_every_credential_api(self):
        with patch('grokbot_mcp.credentials.backend.status_for',side_effect=AssertionError('presence read')), \
             patch('grokbot_mcp.credentials.backend.load_for',side_effect=AssertionError('secret read')), \
             patch('grokbot_mcp.credentials.windows._apis',side_effect=AssertionError('Windows credential API')):
            report=self.report(export=True)
        self.assertIn('credentials_not_inspected',self.codes(report))
        self.assertNotIn('diagnostic_failed',self.codes(report))
        self.assertFalse(report['credentials_checked'])
        self.assertIsNone(report['credentials_ready'])
        self.assertFalse(report['complete_for_purpose'])

    def test_export_decline_writes_nothing(self):
        before=contents(self.base)
        path=self.base/'diagnostic.json'
        code,text=self.run_wizard(['--check','--export',str(path)],['n'])
        self.assertEqual(code,1)
        self.assertIn('EXPORT_NOT_SAVED',text)
        self.assertEqual(contents(self.base),before)

    def test_export_preview_matches_saved_serialization(self):
        path=self.base/'diagnostic.json'
        code,text=self.run_wizard(['--check','--export',str(path)],['y'])
        self.assertEqual(code,0)
        saved=path.read_text(encoding='utf-8')
        self.assertTrue(text.startswith(saved))
        self.assertIn(str(path),text)
        self.assertFalse(json.loads(saved)['remote_binding_verified'])
        self.assertNotIn(str(self.base),saved)

    def test_export_never_overwrites_or_writes_business_state(self):
        report=self.report(export=True)
        for path in (self.registry,self.base/'execution_switch.json',self.root/'new.json',self.root/'.bridge/new.json'):
            with self.subTest(path=path.name), self.assertRaises((ValueError,FileExistsError)):
                wizard.save_export(path,report,registry=self.registry)
        destination=self.base/'diagnostic.json'
        destination.write_text('existing', encoding="utf-8")
        with self.assertRaises(FileExistsError):
            wizard.save_export(destination,report,registry=self.registry)
        self.assertEqual(destination.read_text(encoding="utf-8"),'existing')

    def test_diagnostic_serialization_ignores_nested_private_fields(self):
        key='sk_'+('z'*30)
        user='FictionalPrivateUser72'; machine='FictionalMachine99'; task='12345678-1234-1234-1234-123456789abc'
        private_path='/'.join(['C:','Users',user,'private workspace'])
        hook='https://example.invalid/private-webhook-72'
        body='Fictional secret task body 72'
        markers=[key,user,machine,task,private_path,hook,body,str(self.root)]
        report=self.report(export=True)
        report.update(registry={'root':private_path,'machine':machine},task_id=task,task_body=body,
                      log=[{'exception':repr(RuntimeError(key,hook))}],python_version=key)
        report['findings'].append({'code':'unknown_'+key,'phase':{'nested':hook},'reason':body,'next_step':private_path})
        report['findings'].append({'code':'registry_missing','phase':'registry','reason':user,'extra':machine})
        destination=self.base/'diagnostic.json'
        wizard.save_export(destination,report,registry=self.registry)
        data=destination.read_bytes()
        archive=self.base/'diagnostic-test.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('nested/diagnostic.json',data)
        with zipfile.ZipFile(archive) as z:
            serialized=data.decode('utf-8')+''.join(z.read(n).decode('utf-8') for n in z.namelist())
        for marker in markers:
            self.assertNotIn(marker,serialized)
        self.assertEqual(json.loads(data)['python_version'],'unknown')

    def test_nested_exception_never_leaks_and_export_never_loads_secret(self):
        marker='FictionalPrivateException72'
        with patch('grokbot_mcp.registry.load_registry',side_effect=RuntimeError({'nested':[marker]})):
            report=self.report(export=True)
        self.assertIn('diagnostic_failed',self.codes(report))
        self.assertNotIn(marker,doctor.serialize(report))
        self.assertNotIn(marker,doctor.render(report))

    def test_invalid_arguments_do_not_echo_private_values(self):
        marker='FictionalPrivateArgument72'
        code,text=self.run_wizard(['--routine-mode',marker])
        self.assertEqual(code,1)
        self.assertIn('invalid_arguments',text)
        self.assertNotIn(marker,text)

    def test_invalid_export_does_not_emit_exception(self):
        marker='FictionalPrivateException72'
        with patch.object(wizard,'save_export',side_effect=OSError(marker)):
            code,text=self.run_wizard(['--check','--export',str(self.base/'diagnostic.json')],['y'])
        self.assertEqual(code,1)
        self.assertNotIn(marker,text)
        self.assertNotIn('Traceback',text)

    def test_catalog_has_machine_codes_location_blocks_and_specific_next_steps(self):
        required={'python_unsupported','git_missing','registry_missing','registry_unreadable','unknown_workspace',
                  'credentials_missing','routine_paused','binding_unknown','binding_claim_entry_mismatch',
                  'binding_capability_missing','execution_switch_disabled','workspace_writes_disabled',
                  'git_head_mismatch','validation_not_verified','validation_not_succeeded','result_changed',
                  'incomplete_task_exists','unknown_submission','retry_forbidden','local_cancellation_available'}
        self.assertTrue(required.issubset(doctor.CATALOG))
        for code in required:
            row=doctor.finding(code)
            self.assertEqual(row['code'],code)
            self.assertGreater(len(row['reason']),15)
            self.assertGreater(len(row['next_step']),40)
            self.assertIn(row['where'],{'local','grok_bot_then_local'})
            self.assertIsInstance(row['blocks'],list)
        self.assertIn('Do not resend',doctor.finding('unknown_submission')['next_step'])


class TaskDiagnosisTest(unittest.TestCase):
    def setUp(self):
        self.c=test_reliability.ReliabilityTest('runTest'); self.c.setUp()
        self.addCleanup(self.c.doCleanups)

    def report(self, **kw):
        return doctor.collect(self.c.registry,WORKSPACE_ID,export=True,**kw)

    def codes(self,report):
        return {item['code'] for item in report['findings']}

    def test_success_proof_not_reconciled_is_actionable_without_reconciliation(self):
        c=self.c; task=c.prepare(); facade.submit_validation_tool(WORKSPACE_ID,task['task_id'])
        write_proof(c.root,task['task_id'],task['manifest_sha256'],['src/counter.py'])
        before=contents(c.root)
        self.assertIn('validation_not_verified',self.codes(self.report(task_id=task['task_id'])))
        self.assertEqual(contents(c.root),before)

    def test_failed_validation_does_not_suggest_authorization(self):
        c=self.c; task=c.prepare(); facade.submit_validation_tool(WORKSPACE_ID,task['task_id'])
        write_proof(c.root,task['task_id'],task['manifest_sha256'],['src/counter.py'])
        c.mutate(c.path('results_v2',task['task_id']),status='failed',validation_passed=False)
        facade.get_status(WORKSPACE_ID,task_id=task['task_id'])
        self.assertIn('validation_rejected',self.codes(self.report(task_id=task['task_id'])))

    def test_changed_proof_is_reported_without_rewriting_ledger(self):
        c=self.c; task=c.validated(); path=c.path('results_v2',task['task_id'])
        path.write_text(path.read_text(encoding="utf-8")+' ', encoding="utf-8")
        before=contents(c.root)
        codes=self.codes(self.report(task_id=task['task_id']))
        self.assertIn('result_changed',codes)
        self.assertIn('validation_hash_mismatch',codes)
        self.assertEqual(contents(c.root),before)

    def test_explicit_registry_controls_current_evidence_check_not_ambient_registry(self):
        c=self.c; task,ex=c.start(); c.change(); c.emit(task,ex)
        self.assertEqual(c.status(ex)['status'],'succeeded')
        with patch.dict(os.environ, {'GROKBOT_MCP_REGISTRY':str(c.root/'unrelated.json'),'GIT_OPTIONAL_LOCKS':'1'}):
            before=dict(os.environ)
            report=doctor.collect(c.registry,WORKSPACE_ID,execution_id=ex['execution_id'])
            self.assertNotIn('binding_claim_entry_mismatch',self.codes(report))
            self.assertEqual(dict(os.environ),before)
        self.assertTrue(report['complete_for_purpose'])

    def test_changed_claim_after_success_is_found_by_readonly_current_check(self):
        c=self.c; task,ex=c.start(); c.change(); c.emit(task,ex)
        self.assertEqual(c.status(ex)['status'],'succeeded')
        claim=c.path('execution_claims',ex['execution_id'])
        claim.write_text(claim.read_text(encoding='utf-8')+'\n',encoding='utf-8')
        before=contents(c.root)
        self.assertIn('claim_hash_mismatch',self.codes(self.report(execution_id=ex['execution_id'])))
        self.assertEqual(contents(c.root),before)

    def test_unknown_submission_cannot_get_cancel_or_resend_advice(self):
        c=self.c; task=c.validated(); ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        c.mutate(c.path('executions',ex['execution_id']),submission_state='unknown_submission',
                 execution_state='unknown_submission',webhook_attempted=True)
        before=contents(c.root)
        report=self.report(execution_id=ex['execution_id'])
        self.assertIn('unknown_submission',self.codes(report))
        self.assertNotIn('local_cancellation_available',self.codes(report))
        self.assertIn('Do not resend',doctor.render(report))
        self.assertEqual(contents(c.root),before)

    def test_prepared_unused_authorization_can_preview_then_cancel(self):
        c=self.c; task=c.validated(); ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        ws=load_registry(c.registry)[WORKSPACE_ID]; before=contents(c.root)
        self.assertTrue(preview_cancellation(ws,ex['execution_id'])['eligible'])
        self.assertIn('local_cancellation_available',self.codes(self.report(execution_id=ex['execution_id'])))
        self.assertEqual(contents(c.root),before)
        self.assertTrue(cancel_unsubmitted(ws,ex['execution_id'],reason='operator_cancelled')['ok'])

    def test_preview_missing_fields_never_proves_safe(self):
        c=self.c; task=c.validated(); ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        path=c.path('executions',ex['execution_id']); original=path.read_bytes()
        for field in ('request_attempted_at','webhook_attempted','submission_final','claimed_at','http_status','accepted'):
            with self.subTest(field=field):
                data=json.loads(original); data.pop(field); c.put(path,data)
                self.assertFalse(preview_cancellation(load_registry(c.registry)[WORKSPACE_ID],ex['execution_id'])['eligible'])

    def test_partial_claim_or_result_does_not_offer_cancellation(self):
        c=self.c; task=c.validated(); ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        for kind in ('execution_claims','results_execute'):
            with self.subTest(kind=kind):
                path=c.path(kind,ex['execution_id']); path.parent.mkdir(exist_ok=True); path.write_text('{', encoding="utf-8")
                self.assertNotIn('local_cancellation_available',self.codes(self.report(execution_id=ex['execution_id'])))
                path.unlink()

    def test_submitted_and_claimed_authorizations_cannot_preview_cancel(self):
        c=self.c; task,ex=c.start()
        self.assertNotIn('local_cancellation_available',self.codes(self.report(execution_id=ex['execution_id'])))

    def test_payload_text_and_task_ids_are_absent_from_export(self):
        c=self.c; task=c.validated(); ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        payload=doctor.serialize(self.report(task_id=task['task_id'],execution_id=ex['execution_id']))
        for marker in (task['task_id'],ex['execution_id'],str(c.root),'Add a fictional label.'):
            self.assertNotIn(marker,payload)


class EnvironmentTest(unittest.TestCase):
    def test_real_installed_runtime_probe(self):
        result=setup_environment.inspect_environment()
        self.assertTrue(result['ready'],result['codes'])
        self.assertEqual(result['bridge_version'],__version__)
        self.assertEqual(result['python'],str(Path(sys.executable).absolute()))

    def test_missing_git_has_specific_code(self):
        with patch.object(setup_environment.shutil,'which',return_value=None):
            self.assertIn('git_missing',setup_environment.inspect_environment()['codes'])

    def test_old_python_is_reported(self):
        with patch.object(setup_environment.sys,'version_info',(3,11,9)):
            self.assertIn('python_unsupported',setup_environment.inspect_environment()['codes'])

    def test_non_windows_is_reported(self):
        with patch.object(setup_environment.sys,'platform','linux'):
            self.assertIn('windows_required',setup_environment.inspect_environment()['codes'])

    def test_missing_interpreter_is_reported(self):
        with patch.object(setup_environment.sys,'executable','not-an-absolute-python'):
            self.assertIn('python_interpreter_invalid',setup_environment.inspect_environment()['codes'])

    def test_probe_failure_does_not_echo_subprocess_stderr(self):
        response=subprocess.CompletedProcess([],1,stdout='',stderr='FictionalPrivatePath72')
        with patch.object(setup_environment.subprocess,'run',return_value=response):
            result=setup_environment.inspect_environment()
        self.assertIn('installation_mismatch',result['codes'])
        self.assertNotIn('FictionalPrivatePath72',str(result))

    def test_interpreter_does_not_resolve_venv_symlink(self):
        with temporary_directory() as tmp:
            python=Path(tmp)/'venv python.exe'; python.write_text('fictional', encoding="utf-8")
            with patch.object(setup_environment.sys,'executable',str(python)), \
                 patch.object(Path,'resolve',side_effect=AssertionError('must preserve venv entry')):
                self.assertEqual(setup_environment.interpreter(),str(python))


class BootstrapTest(unittest.TestCase):
    def script(self,*args,env=None):
        script=Path(__file__).resolve().parents[1]/'scripts/setup.ps1'
        pwsh=shutil.which('pwsh')
        self.assertIsNotNone(pwsh,'Windows CI must provide PowerShell; do not skip bootstrap verification')
        return subprocess.run([pwsh,'-NoProfile','-NonInteractive','-File',str(script),*args],
                              capture_output=True,text=True,env=env,timeout=60, encoding="utf-8", errors="replace")

    def test_missing_python_is_actionable(self):
        with temporary_directory() as tmp:
            result=self.script('-Python',str(Path(tmp)/'missing-python.exe'))
        self.assertEqual(result.returncode,1)
        self.assertIn('python_interpreter_invalid',result.stdout)

    def test_missing_git_is_actionable_without_installation(self):
        env={**os.environ,'PATH':''}
        result=self.script('-Python',sys.executable,env=env)
        self.assertEqual(result.returncode,1)
        self.assertIn('git_missing',result.stdout)

    def test_existing_non_venv_directory_is_preserved(self):
        with temporary_directory() as tmp:
            marker=Path(tmp)/'keep.txt'; marker.write_text('preserve', encoding="utf-8")
            result=self.script('-Python',sys.executable,'-InstallDir',tmp)
            self.assertEqual(result.returncode,1)
            self.assertIn('Existing directory is not a recognized venv',result.stdout)
            self.assertEqual(marker.read_text(encoding="utf-8"),'preserve')
            self.assertEqual(len(list(Path(tmp).iterdir())),1)

    def test_stale_installation_is_not_silently_replaced(self):
        with temporary_directory() as tmp:
            root=Path(tmp); scripts=root/'scripts'; scripts.mkdir()
            source=Path(__file__).resolve().parents[1]/'scripts/setup.ps1'
            shutil.copyfile(source,scripts/'setup.ps1')
            (root/'pyproject.toml').write_text('[project]\nversion="0.0.0"\n', encoding="utf-8")
            result=subprocess.run([shutil.which('pwsh'),'-NoProfile','-NonInteractive','-File',str(scripts/'setup.ps1'),
                '-Python',sys.executable,'-InstallDir',sys.prefix,'-Check','-Workspace',WORKSPACE_ID],
                capture_output=True,text=True,timeout=60, encoding="utf-8", errors="replace")
            self.assertEqual(result.returncode,1)
            self.assertIn('installation_mismatch',result.stdout)
            import importlib.metadata
            self.assertEqual(importlib.metadata.version('grokbot-mcp-bridge'),__version__)

    def test_readonly_bootstrap_preserves_existing_installation(self):
        with temporary_directory() as tmp:
            result=self.script('-Python',sys.executable,'-InstallDir',sys.prefix,
                               '-Check','-Registry',str(Path(tmp)/'missing.json'),'-Workspace',WORKSPACE_ID)
        self.assertEqual(result.returncode,1)
        self.assertIn('Existing bridge installation preserved',result.stdout)
        self.assertIn('registry_missing',result.stdout)

    def test_readonly_bootstrap_does_not_create_venv(self):
        with temporary_directory() as tmp:
            target=Path(tmp)/'new venv'
            result=self.script('-Python',sys.executable,'-InstallDir',str(target),'-Check','-Workspace',WORKSPACE_ID)
            self.assertFalse(target.exists())
        self.assertEqual(result.returncode,1)
        self.assertIn('installation_unavailable',result.stdout)


class ConfiguredStdioTest(unittest.TestCase):
    def test_generated_config_launches_current_installed_package_outside_source(self):
        import asyncio
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        with temporary_directory() as temporary, patch.dict(os.environ):
            base=Path(temporary); root=make_workspace(base)
            try:
                registry=base/'registry.json'
                folder=wizard.generate(registry,WORKSPACE_ID)
                config=json.loads((folder/'mcp-config.json').read_text(encoding='utf-8'))['mcpServers']['grokbot-mcp']
                self.assertEqual(config['command'],str(Path(sys.executable).absolute()))
                # Deliberately put a fake package in cwd: generated -I must ignore it.
                outside=base/'客户端 空间'; outside.mkdir()
                fake=outside/'grokbot_mcp'; fake.mkdir()
                (fake/'__init__.py').write_text("raise RuntimeError('wrong package from cwd')", encoding="utf-8")
                async def run():
                    parameters=StdioServerParameters(command=config['command'],args=config['args'],
                        env={**os.environ,**config['env']},cwd=str(outside))
                    async with stdio_client(parameters) as (read,write):
                        async with ClientSession(read,write) as session:
                            await session.initialize()
                            tools=await session.list_tools()
                            self.assertEqual(len(tools.tools),7)
                            result=await session.call_tool('grok_preflight',{'workspace_id':WORKSPACE_ID})
                            text=''.join(getattr(block,'text','') for block in result.content)
                            self.assertIn('"webhook_sent":false',text.replace(' ',''))
                            self.assertIn('"remote_binding_verified":false',text.replace(' ',''))
                            self.assertNotIn('https://',text)
                asyncio.run(run())
            finally:
                install_backend(None,None); facade.install_transport(None)


if __name__ == '__main__':
    unittest.main()
