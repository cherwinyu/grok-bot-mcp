"""Windows fictional workflows; existing credentials, clients and projects untouched."""
from __future__ import annotations
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from grokbot_mcp import safety, safety_cli, facade, canary
from grokbot_mcp.execution_profile import recommended, parse
from grokbot_mcp.local_safety import LocalError, json_bytes
from grokbot_mcp.registry import load_registry, policy_fingerprint, RegistryError
from grokbot_mcp.routine import confirmation, routine_readiness
from grokbot_mcp.storage import utc_now
from tests.deployment_support import DeploymentCase, fake_install
from tests.support import WORKSPACE_ID


class SafetyWorkflowTest(DeploymentCase):
    def set_profile(self, value):
        data=json.loads(self.registry.read_bytes());data['workspaces'][0]['execution_profile']=value
        self.write_json(self.registry,data)
        return load_registry(self.registry)[WORKSPACE_ID]
    def rebind(self, workspace):
        for phase in ('validate','execute'):
            self.write_json(self.root/'.bridge'/f'{phase}_routine_status.json',confirmation(workspace,phase,self.registry,
                         mode='reuse',revision=2,verified_at=utc_now(),legacy=False))
    def test_preview_leaves_all_business_files_unchanged(self):
        before=self.files(self.base)
        with patch('grokbot_mcp.credentials.windows._apis',side_effect=AssertionError('no credentials')):
            result=safety.preview(self.registry,WORKSPACE_ID)
        self.assertTrue(result['ok']);self.assertFalse(result['send_authorized'])
        self.assertFalse(result['ledger_modified']);self.assertEqual(before,self.files(self.base))
        self.assertNotIn('local_details',result)
    def test_preview_explicit_details_do_not_include_keys(self):
        result=safety.preview(self.registry,WORKSPACE_ID,local_details=True)
        self.assertEqual(result['local_details']['root'],str(self.root))
        self.assertNotIn('credential_target',json.dumps(result))
    def test_original_task_scope_matches_manifest(self):
        task=self.case.prepare()
        before=self.files()
        result=safety.preview(self.registry,WORKSPACE_ID,task_id=task['task_id'])
        self.assertEqual(result['task']['allowed_files'],['src/counter.py'])
        self.assertEqual(result['selected_allowed_file_count'],1)
        self.assertNotIn('task_description',json.dumps(result))
        self.assertEqual(before,self.files())
    def test_mismatched_execution_identity_refused(self):
        task=self.case.validated();ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        self.case.mutate(self.case.path('executions',ex['execution_id']),manifest_task_id='00000000-0000-4000-8000-000000000099')
        with self.assertRaisesRegex(LocalError,'result_ledger_identity_mismatch'):
            safety.preview(self.registry,WORKSPACE_ID,task_id=task['task_id'],execution_id=ex['execution_id'])
    def test_execution_without_original_task_refused(self):
        with self.assertRaisesRegex(LocalError,'original_task_required'):
            safety.preview(self.registry,WORKSPACE_ID,execution_id='00000000-0000-4000-8000-000000000099')
    def test_path_traversal_identity_refused(self):
        with self.assertRaisesRegex(LocalError,'result_identity_invalid'):
            safety.preview(self.registry,WORKSPACE_ID,task_id='../escape')
    def test_profile_changes_invalidate_legacy_binding(self):
        old=load_registry(self.registry)[WORKSPACE_ID]
        current=self.set_profile(recommended())
        self.assertNotEqual(policy_fingerprint(old),policy_fingerprint(current))
        self.assertFalse(routine_readiness(current,'validate',self.registry)['binding_confirmed'])
        self.assertFalse(facade.preflight(WORKSPACE_ID)['submit_permitted'])
    def test_read_only_profile_refuses_execution_grant(self):
        workspace=self.set_profile({**recommended(),'mode':'read_only'});self.rebind(workspace)
        task=self.case.validated();ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        self.assertEqual(ex['error'],'execution_profile_read_only')
        self.assertIsNone(ex['execution_id'])
    def test_profile_metadata_is_not_reachability(self):
        workspace=self.set_profile(recommended());self.rebind(workspace)
        result=facade.preflight(WORKSPACE_ID)
        self.assertEqual(result['execution_environment']['desktop_reachability'],'NOT_VERIFIED')
        self.assertFalse(result['execution_environment']['os_sandbox_verified'])
    def test_unsupported_provider_blocks_before_transport(self):
        data=json.loads(self.registry.read_bytes());data['workspaces'][0]['execution_profile']={**recommended(),'protocol':'unsupported'}
        self.write_json(self.registry,data)
        result=facade.preflight(WORKSPACE_ID)
        self.assertEqual(result['error'],'webhook_protocol_unsupported');self.assertFalse(result['webhook_sent'])
        self.assertEqual(self.case.transport.calls,[])
    def test_registry_bool_schema_is_not_int(self):
        data=json.loads(self.registry.read_bytes());data['schema_version']=True;self.write_json(self.registry,data)
        with self.assertRaises(RegistryError):load_registry(self.registry)
    def test_registry_duplicate_json_key_refused(self):
        self.registry.write_bytes(b'{"schema_version":1,"workspaces":[],"workspaces":[]}')
        with self.assertRaises(RegistryError):load_registry(self.registry)
    def test_credentials_plan_no_presence_by_default(self):
        (self.base/'execution_switch.json').write_bytes(b'{"enabled":false}')
        before=self.files()
        with patch('grokbot_mcp.credentials.backend.status_for',side_effect=AssertionError('must not check')):
            result=safety.credential_plan(self.registry,WORKSPACE_ID)
        self.assertTrue(result['ok']);self.assertFalse(result['presence_checked'])
        self.assertEqual(result['phases']['execute']['presence'],'NOT_CHECKED')
        self.assertEqual(before,self.files())
    def test_credential_plan_presence_does_not_validate_binding_or_key(self):
        (self.base/'execution_switch.json').write_bytes(b'{"enabled":false}')
        path=self.root/'.bridge/execute_routine_status.json';value=json.loads(path.read_bytes());value.pop('binding');self.write_json(path,value)
        with patch('grokbot_mcp.credentials.backend.status_for',return_value='present') as status, \
             patch('grokbot_mcp.credentials.backend.load_for',side_effect=AssertionError('no key load')):
            result=safety.credential_plan(self.registry,WORKSPACE_ID,check_presence=True)
        self.assertEqual(status.call_count,2)
        self.assertFalse(result['phases']['execute']['binding_confirmed'])
        self.assertEqual(result['phases']['execute']['credential_validity'],'NOT_VERIFIED')
    def test_credential_maintenance_requires_switch_off(self):
        result=safety.credential_plan(self.registry,WORKSPACE_ID)
        self.assertFalse(result['ok']);self.assertIn('credential_maintenance_switch_on',result['blocking_reasons'])
    def test_credential_maintenance_blocks_unknown_submission(self):
        task=self.case.validated();ex=facade.prepare_execution_tool(WORKSPACE_ID,task['task_id'])
        self.case.mutate(self.case.path('executions',ex['execution_id']),submission_state='unknown_submission',execution_state='unknown_submission')
        before=self.files()
        result=safety.credential_plan(self.registry,WORKSPACE_ID)
        self.assertGreater(result['pending_count'],0);self.assertFalse(result['ok'])
        self.assertEqual(before,self.files())
    def test_acceptance_observation_cannot_certify_remote_test(self):
        task,ex=self.case.start();self.case.change();self.case.emit(task,ex)
        self.assertEqual(self.case.status(ex)['status'],'succeeded')
        before=self.files()
        result=safety.acceptance(self.registry,WORKSPACE_ID,task['task_id'],ex['execution_id'])
        self.assertTrue(result['stored_evidence_success']);self.assertFalse(result['remote_routine_verified'])
        self.assertFalse(result['tests_independently_measured']);self.assertFalse(result['qa_accepted'])
        self.assertEqual(before,self.files())
    def test_invalid_observation_identity_never_proves_client_reconnect(self):
        task,ex=self.case.start();self.case.change();self.case.emit(task,ex);self.case.status(ex)
        result=safety.acceptance(self.registry,WORKSPACE_ID,task['task_id'],ex['execution_id'],observed_identity={'private':'canary'})
        self.assertFalse(result['supplied_client_identity_comparison']['identity_matches'])
        self.assertFalse(result['client_reconnection_confirmed'])
        self.assertNotIn('canary',json.dumps(result))
    def test_export_is_separate_share_safe_reconstruction(self):
        destination=self.base/'share.json'
        with redirect_stdout(io.StringIO()) as output:
            code=safety_cli.main(['preview','--registry',str(self.registry),'--workspace',WORKSPACE_ID,'--local-details','--export',str(destination)])
        self.assertEqual(code,0)
        data=json.loads(destination.read_bytes());self.assertEqual(data,json.loads(output.getvalue()))
        self.assertNotIn(WORKSPACE_ID,json.dumps(data));self.assertNotIn(str(self.root),json.dumps(data))
    def test_export_refuses_business_workspace(self):
        with redirect_stdout(io.StringIO()):
            code=safety_cli.main(['preview','--registry',str(self.registry),'--workspace',WORKSPACE_ID,'--export',str(self.root/'share.json')])
        self.assertEqual(code,1);self.assertFalse((self.root/'share.json').exists())
    def test_export_refuses_unknown_protection_scope(self):
        self.registry.write_bytes(b'{')
        with redirect_stdout(io.StringIO()):
            code=safety_cli.main(['preview','--registry',str(self.registry),'--workspace',WORKSPACE_ID,'--export',str(self.base/'share.json')])
        self.assertEqual(code,1);self.assertFalse((self.base/'share.json').exists())
    def test_cli_apply_conflict_refused_before_any_io(self):
        before=self.files(self.base)
        with redirect_stdout(io.StringIO()) as out:
            code=safety_cli.main(['preview','--registry',str(self.registry),'--workspace',WORKSPACE_ID,'--apply'])
        self.assertEqual(code,1);self.assertEqual(json.loads(out.getvalue())['diagnostic']['code'],'invalid_arguments')
        self.assertEqual(before,self.files(self.base))
    def test_binding_migration_cannot_change_execution_profile(self):
        from grokbot_mcp import migration
        target = self.base / 'target registry.json'
        data = json.loads(self.registry.read_bytes())
        data['workspaces'][0]['execution_profile'] = recommended()
        self.write_json(target, data)
        python = fake_install(self.base / 'candidate installation')
        before = self.files(self.base)
        report = migration.plan(self.registry, WORKSPACE_ID, python, target_registry=target)
        self.assertIn('migration_execution_profile_change_forbidden', report['blocking_reasons'])
        self.assertFalse(report['ok'])
        self.assertEqual(before, self.files(self.base))

    def test_unknown_argument_does_not_echo_payload(self):
        with redirect_stdout(io.StringIO()) as out:
            code=safety_cli.main(['profile','--private-canary'])
        self.assertEqual(code,1);self.assertNotIn('private-canary',out.getvalue())


class CanaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'新建 Canary'
    def test_preview_never_creates_directory(self):
        report=canary.plan(self.root)
        self.assertFalse(self.root.exists());self.assertFalse(report['created'])
        self.assertFalse(report['send_authorized']);self.assertFalse(report['writes_enabled'])
    def test_wrong_confirmation_has_no_side_effect(self):
        with self.assertRaisesRegex(LocalError,'plan_confirmation_mismatch'):canary.create(self.root,confirm='wrong')
        self.assertFalse(self.root.exists())
    def test_create_keeps_switch_and_writes_off(self):
        report=canary.create(self.root,confirm=canary.plan(self.root)['plan_sha256'])
        self.assertTrue(report['created'])
        workspace=load_registry(self.root/'registry.json')['canary-workspace']
        self.assertFalse(workspace.writes_enabled)
        self.assertFalse(json.loads((self.root/'execution_switch.json').read_bytes())['enabled'])
        self.assertFalse((workspace.root/'.bridge').exists())
        self.assertFalse(json.loads((self.root/'canary.json').read_bytes())['send_authorized'])
    def test_existing_folder_is_never_overwritten(self):
        self.root.mkdir()
        with self.assertRaisesRegex(LocalError,'canary_new_directory_required'):canary.plan(self.root)
    def test_refuses_inside_existing_repository(self):
        self.root.mkdir();(self.root/'.git').mkdir()
        with self.assertRaisesRegex(LocalError,'canary_destination_protected'):canary.plan(self.root/'child')
    def test_git_failure_leaves_no_published_fixture(self):
        plan=canary.plan(self.root)
        with patch('grokbot_mcp.canary.local_git',side_effect=LocalError('canary_git_failed')):
            with self.assertRaisesRegex(LocalError,'canary_git_failed'):canary.create(self.root,confirm=plan['plan_sha256'])
        self.assertFalse(self.root.exists())
        self.assertEqual(list(self.root.parent.iterdir()),[])
    def test_real_profile_has_no_synthetic_binding(self):
        canary.create(self.root,confirm=canary.plan(self.root)['plan_sha256'])
        workspace=load_registry(self.root/'registry.json')['canary-workspace']
        self.assertFalse(routine_readiness(workspace,'execute',self.root/'registry.json')['binding_confirmed'])
    def test_offline_demo_actual_tests_and_safety_restored(self):
        from grokbot_mcp.offline_demo import run_demo
        from grokbot_mcp.credentials import backend
        before=(backend._status,backend._load,facade._TRANSPORT,os.environ.get('GROKBOT_MCP_REGISTRY'))
        report=run_demo()
        self.assertTrue(report['ok']);self.assertEqual(report['mode'],'OFFLINE_SIMULATION')
        self.assertEqual(report['measured_tests']['case_count'],2);self.assertEqual(report['measured_tests']['failed'],0)
        self.assertEqual(report['real_webhooks_sent'],0);self.assertFalse(report['remote_routine_verified'])
        self.assertTrue(report['temporary_fixture_removed'])
        self.assertEqual(before,(backend._status,backend._load,facade._TRANSPORT,os.environ.get('GROKBOT_MCP_REGISTRY')))
