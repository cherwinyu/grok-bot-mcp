"""Platform-independent contract tests; no substituted Windows API."""
import copy
import json
import unittest
from types import SimpleNamespace
from dataclasses import asdict
from grokbot_mcp.execution_profile import parse, recommended, report, block
from grokbot_mcp.strict_json import decode, MAX_BYTES
from grokbot_mcp.safety import shareable


class JsonBoundaryTest(unittest.TestCase):
    def test_valid_unicode(self):
        self.assertEqual(decode('{"value":"中文 测试"}'.encode()), {'value':'中文 测试'})
    def test_booleans_do_not_become_numbers(self):
        self.assertIs(decode(b'{"value":true}')['value'], True)
    def test_duplicate_nested_fields(self):
        with self.assertRaisesRegex(ValueError, 'json_duplicate_field'):
            decode(b'{"value":{"a":1,"a":2}}')
    def test_finite_floats(self):
        self.assertEqual(decode(b'{"value":0.01}'), {'value':.01})
    def test_nonfinite_literals(self):
        for v in (b'NaN', b'Infinity', b'-Infinity', b'1e999'):
            with self.subTest(value='nonfinite'), self.assertRaisesRegex(ValueError, 'json_nonfinite'):
                decode(b'{"value":'+v+b'}')
    def test_bom(self):
        with self.assertRaisesRegex(ValueError,'encoding_rejected'):
            decode(b'\xef\xbb\xbf{}')
    def test_size_limit(self):
        with self.assertRaisesRegex(ValueError,'json_too_large'):
            decode(b' '* (MAX_BYTES+1))
    def test_depth_limit(self):
        with self.assertRaisesRegex(ValueError,'json_depth_exceeded'):
            decode(b'{"a":'+b'['*64+b'0'+b']'*64+b'}')
    def test_brackets_in_strings_are_not_depth(self):
        self.assertEqual(decode(json.dumps({'s':'['*80+'\\"}'*80}).encode())['s'], '['*80+'\\"}'*80)
    def test_object_required(self):
        for value in (b'null',b'[]',b'true',b'42'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError,'schema_invalid'):
                decode(value)
    def test_valid_maximal_nested_object(self):
        self.assertIsInstance(decode(b'{"a":'+b'['*62+b'0'+b']'*62+b'}'),dict)


class ProfileTest(unittest.TestCase):
    def test_default_profile_parses(self):
        self.assertEqual(asdict(parse(recommended())), recommended())
    def test_unknown_field_refused(self):
        with self.assertRaisesRegex(ValueError,'execution_profile_invalid'):
            parse({**recommended(),'remote_verified':True})
    def test_missing_field_refused(self):
        value = recommended(); value.pop('mode')
        with self.assertRaises(ValueError):parse(value)
    def test_boolean_version_refused(self):
        with self.assertRaises(ValueError):parse({**recommended(),'schema_version':True})
    def test_wrong_field_types_refused(self):
        for name in ('protocol','topology','mode','desktop_approval'):
            with self.subTest(field=name), self.assertRaisesRegex(ValueError,'execution_profile_invalid'):
                parse({**recommended(),name:{'private':'never echo'}})
    def test_other_protocol_not_autodetected(self):
        with self.assertRaisesRegex(ValueError,'webhook_protocol_unsupported'):
            parse({**recommended(),'protocol':'grok_automation_hmac_202'})
    def test_cloud_shared_fs_refused(self):
        with self.assertRaisesRegex(ValueError,'execution_topology_unsupported'):
            parse({**recommended(),'topology':'cloud_shared_filesystem'})
    def test_os_sandbox_not_claimed(self):
        with self.assertRaisesRegex(ValueError,'execution_isolation_not_implemented'):
            parse({**recommended(),'mode':'isolated_write'})
    def test_always_allow_not_recommended(self):
        with self.assertRaisesRegex(ValueError,'desktop_approval_unsupported'):
            parse({**recommended(),'desktop_approval':'always_allow'})
    def test_readonly_profile_blocks_execution(self):
        workspace = SimpleNamespace(execution_profile=parse({**recommended(),'mode':'read_only'}))
        self.assertEqual(block(workspace,'execute'),'execution_profile_read_only')
        self.assertIsNone(block(workspace,'validate'))
    def test_legacy_compatibility_is_explicit(self):
        workspace=SimpleNamespace(execution_profile=None)
        self.assertIsNone(block(workspace,'execute'))
        result=report(workspace)
        self.assertTrue(result['legacy_behavior_preserved'])
        self.assertFalse(result['configured']);self.assertFalse(result['os_sandbox_verified'])
    def test_declared_profile_never_attests_desktop(self):
        result=report(SimpleNamespace(execution_profile=parse(recommended())))
        self.assertEqual(result['desktop_reachability'],'NOT_VERIFIED')
        self.assertFalse(result['remote_binding_verified']);self.assertFalse(result['shared_filesystem_assumed'])
    def test_sharing_reconstructs_fixed_fields(self):
        value={'ok':True,'workspace_id':'private','plan_sha256':'private','local_details':{'root':'private'},
               'phases':{'x':{'presence':'private'}},'pending_count':True,'send_authorized':True,'qa_accepted':True}
        public=shareable(value)
        self.assertNotIn('private',json.dumps(public));self.assertIsNone(public['pending_count'])
        self.assertFalse(public['send_authorized']);self.assertFalse(public['qa_accepted'])
    def test_sharing_does_not_coerce_untrusted_booleans(self):
        public=shareable({'ok':{'private':'secret'},'pending_count':-1})
        self.assertIsNone(public['ok']);self.assertIsNone(public['pending_count'])
