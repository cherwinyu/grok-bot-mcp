"""Behavioral probes against existing dev5 APIs; never missing-new-API failures."""
import json
import os
from pathlib import Path

from tests.support import temporary_directory
import struct
import unittest
from unittest.mock import Mock
from grokbot_mcp.credentials.blob import validate_url, pack_blob, unpack_blob, BlobError
from grokbot_mcp.storage import read_json
from grokbot_mcp.security.targets import resolve_exact_files, TargetRejected
from grokbot_mcp.transport.webhook import post_once


class HardeningTest(unittest.TestCase):
    def test_url_userinfo_rejected(self):
        with self.assertRaises(BlobError):
            validate_url('https://user:example@example.invalid/hook')

    def test_url_ipv6_loopback_rejected(self):
        with self.assertRaises(BlobError):
            validate_url('https://[::1]/fixture')

    def test_url_fragment_rejected(self):
        with self.assertRaises(BlobError):
            validate_url('https://example.invalid/hook#not-sent')

    def test_url_numeric_alias_rejected(self):
        with self.assertRaises(BlobError):
            validate_url('https://2130706433/fixture')

    def test_url_private_network_rejected(self):
        with self.assertRaises(BlobError):
            validate_url('https://10.0.0.1/fixture')

    def test_malformed_credential_utf8_is_safe_blob_error(self):
        data = b'GWH1' + struct.pack('<I', 1) + b'\xff' + struct.pack('<I', 1) + b'x'
        try:
            unpack_blob(data)
        except Exception as exc:
            self.assertIsInstance(exc, BlobError, 'invalid blob escaped the safe credential error contract')
            self.assertEqual(str(exc), 'invalid credential blob')
        else:
            self.fail('invalid UTF-8 credential was accepted')

    def document(self, raw):
        with temporary_directory() as name:
            p = Path(name) / 'evidence.json'
            p.write_bytes(raw)
            return read_json(p)

    def test_duplicate_evidence_field_rejected(self):
        with self.assertRaisesRegex(ValueError, 'json_duplicate_field'):
            self.document(b'{"status":"failed","status":"succeeded"}')

    def test_nonfinite_evidence_rejected(self):
        with self.assertRaisesRegex(ValueError, 'json_nonfinite'):
            self.document(b'{"value":1e999}')

    def test_deep_evidence_rejected(self):
        with self.assertRaisesRegex(ValueError, 'json_depth_exceeded'):
            self.document(b'{"value":' + b'['*96 + b'0' + b']'*96 + b'}')

    def test_hardlinked_evidence_rejected(self):
        with temporary_directory() as name:
            p = Path(name) / 'evidence.json'; p.write_bytes(b'{}')
            os.link(p, Path(name) / 'another.json')
            with self.assertRaisesRegex(ValueError, 'linked_path_rejected'):
                read_json(p)

    def test_hardlinked_allowed_file_rejected(self):
        with temporary_directory() as name:
            root = Path(name) / 'workspace'; root.mkdir()
            p = root / 'allowed.py'; p.write_bytes(b'# fixture')
            os.link(p, Path(name) / 'outside.py')
            with self.assertRaises(TargetRejected) as error:
                resolve_exact_files(root, ['allowed.py'], ('.bridge', '.git'))
            self.assertEqual(error.exception.code, 'target_hardlink_rejected')

    def test_valid_endpoint_and_blob_round_trip(self):
        for url in ('https://example.invalid/hook', 'https://example.invalid:8443/hook?q=fixture'):
            with self.subTest(url=url):
                self.assertEqual(unpack_blob(pack_blob(url, 'example-key')), (url, 'example-key'))

    def test_incomplete_evidence_remains_transient(self):
        with self.assertRaises(json.JSONDecodeError):
            self.document(b'{"status":')
        with self.assertRaises(UnicodeDecodeError):
            self.document(b'{"value":"\xe4')

    def test_bad_endpoint_does_not_open_socket(self):
        factory = Mock(side_effect=AssertionError('network forbidden'))
        result = post_once('https://[::1]/fixture', 'example-key', {'action':'execute_manifest'}, 1,
                           enabled=True, connection_factory=factory)
        self.assertEqual(result.kind, 'unknown'); factory.assert_not_called()

    def test_private_endpoint_variants_are_not_echoed(self):
        for url in ('https://127.1/fixture', 'https://127.0.0.2/fixture', 'https://0x7f000001/fixture',
                    'https://localhost./fixture', 'https://sub.localhost/fixture',
                    'https://[fe80::1%25eth0]/fixture', 'https://example.invalid:99999/fixture',
                    'https://example.invalid\\evil/fixture', 'https://example.invalid/with space'):
            with self.subTest(case='synthetic endpoint'), self.assertRaises(BlobError) as error:
                validate_url(url)
            self.assertEqual(str(error.exception), 'webhook url rejected')

    def test_migration_claim_packet_selects_registry_before_claim(self):
        from grokbot_mcp.materials import render_routine
        root = Path.cwd() / 'fixture workspace'
        registry = Path.cwd() / 'fixture registry.json'
        text = render_routine('execute', workspace_root=root, workspace_id='example-workspace',
                              registry=registry, python=Path.cwd()/'python.exe', mode='migrate')
        prefix = "$env:GROKBOT_MCP_REGISTRY = '" + str(registry).replace("'", "''") + "'"
        claim = ' -m grokbot_mcp.claim_cli '
        self.assertIn(prefix, text)
        self.assertLess(text.index(prefix), text.index(claim))

    def test_duplicate_switch_cannot_enable_submission(self):
        from grokbot_mcp.registry import execution_switch_enabled
        with temporary_directory() as name:
            root=Path(name)
            (root/'execution_switch.json').write_bytes(b'{"enabled":false,"enabled":true}')
            self.assertFalse(execution_switch_enabled(root/'registry.json'))

    def test_hardlinked_switch_cannot_enable_submission(self):
        from grokbot_mcp.registry import execution_switch_enabled
        with temporary_directory() as name:
            root=Path(name)
            path=root/'execution_switch.json';path.write_bytes(b'{"enabled":true}')
            os.link(path,root/'alias.json')
            self.assertFalse(execution_switch_enabled(root/'registry.json'))
