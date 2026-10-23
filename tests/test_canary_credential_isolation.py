"""Independent canaries must never inherit another fixture's credential targets.

Native Windows tests; Credential Manager is replaced by an explicit presence-only
fixture when needed. No real key, registry, project or Webhook is accessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.support import temporary_directory
import unittest
from unittest.mock import patch

from grokbot_mcp import canary, safety
from grokbot_mcp.local_safety import LocalError


class CanaryCredentialIsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_directory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def create(self, label):
        directory = self.base / label
        plan = canary.plan(directory)
        with patch('grokbot_mcp.credentials.windows._apis',
                   side_effect=AssertionError('real credential access forbidden')):
            canary.create(directory, confirm=plan['plan_sha256'])
        value = json.loads((directory / 'registry.json').read_bytes())['workspaces'][0]
        return directory, value

    @staticmethod
    def targets(workspace):
        return {workspace['validate_credential_target'], workspace['execute_credential_target']}

    def test_two_new_canaries_do_not_share_credential_targets(self):
        _, first = self.create('Canary A 空格')
        _, second = self.create('Canary B 空格')
        self.assertEqual(len(self.targets(first)), 2)
        self.assertEqual(len(self.targets(second)), 2)
        self.assertTrue(self.targets(first).isdisjoint(self.targets(second)),
                        'independent fixtures reuse the same Credential Manager target names')

    def test_existing_legacy_fixture_credentials_are_not_reused(self):
        directory, _ = self.create('new fixture')
        legacy_targets = {'grokbot-mcp/canary-workspace/validate',
                          'grokbot-mcp/canary-workspace/execute'}
        def presence(target, others):
            return 'present' if target in legacy_targets else 'missing'
        with patch('grokbot_mcp.credentials.backend._status', side_effect=presence), \
             patch('grokbot_mcp.credentials.backend._load',
                   side_effect=AssertionError('must not load keys')), \
             patch('grokbot_mcp.credentials.windows._apis',
                   side_effect=AssertionError('real credential access forbidden')):
            result = safety.credential_plan(directory / 'registry.json', 'canary-workspace',
                                            check_presence=True)
        self.assertEqual(result['phases']['validate']['presence'], 'missing')
        self.assertEqual(result['phases']['execute']['presence'], 'missing')
        self.assertFalse(result['credential_values_read'])
        self.assertFalse(result['credential_written'])
        self.assertFalse(result['webhook_sent'])

    def test_credential_namespace_is_bound_to_fixture_marker(self):
        directory, workspace = self.create('bound namespace')
        marker = json.loads((directory / 'canary.json').read_bytes())
        import uuid
        self.assertEqual(str(uuid.UUID(marker['fixture_id'])), marker['fixture_id'])
        for phase in ('validate', 'execute'):
            self.assertEqual(workspace[phase + '_credential_target'],
                             'grokbot-mcp/canary-' + marker['fixture_id'] + '/' + phase)
        self.assertFalse(workspace['writes_enabled'])
        self.assertFalse(json.loads((directory / 'execution_switch.json').read_bytes())['enabled'])
        self.assertFalse((directory / 'workspace/.bridge').exists())
        self.assertFalse(marker['send_authorized'])

    def test_new_fixture_preserves_previous_fixture_and_preview_is_stable(self):
        first, _ = self.create('existing fixture')
        def snapshot():
            return {p.relative_to(first).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in first.rglob('*') if p.is_file()}
        before = snapshot()
        second = self.base / 'another fixture'
        one, two = canary.plan(second), canary.plan(second)
        self.assertEqual(one, two)
        self.assertFalse(second.exists())
        self.create(second.name)
        self.assertEqual(snapshot(), before)

    def test_failed_fixture_creation_never_allocates_credentials(self):
        directory = self.base / 'failed fixture'
        plan = canary.plan(directory)
        with patch('grokbot_mcp.canary.local_git', side_effect=LocalError('canary_git_failed')), \
             patch('grokbot_mcp.credentials.windows._apis',
                   side_effect=AssertionError('no credential side effect')):
            with self.assertRaisesRegex(LocalError, 'canary_git_failed'):
                canary.create(directory, confirm=plan['plan_sha256'])
        self.assertFalse(directory.exists())
        self.assertEqual(list(self.base.iterdir()), [])
