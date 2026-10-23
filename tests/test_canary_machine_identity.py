"""Canary host identity is a reviewed local declaration, never remote attestation."""
from __future__ import annotations

import json
import os
from pathlib import Path

from tests.support import temporary_directory
import sys
import unittest
from unittest.mock import patch

from grokbot_mcp import canary
from grokbot_mcp.local_safety import LocalError


@unittest.skipUnless(sys.platform == "win32", "Native Windows fixture required")
class CanaryMachineIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "FICTIONAL-canary"

    def test_canary_registry_binds_reviewed_host_name(self):
        with patch.dict(os.environ, {"COMPUTERNAME": "FICTIONAL-CANARY-HOST"}):
            preview = canary.plan(self.folder)
            canary.create(self.folder, confirm=preview["plan_sha256"])
        registry = json.loads((self.folder / "registry.json").read_bytes())
        workspace = registry["workspaces"][0]
        self.assertEqual(workspace["expected_machine"], "FICTIONAL-CANARY-HOST")
        self.assertFalse(workspace["writes_enabled"])
        self.assertFalse(json.loads((self.folder / "execution_switch.json").read_bytes())["enabled"])

    def test_host_changed_after_preview_refuses_without_creation(self):
        with patch.dict(os.environ, {"COMPUTERNAME": "FICTIONAL-HOST-A"}):
            reviewed = canary.plan(self.folder)
        with patch.dict(os.environ, {"COMPUTERNAME": "FICTIONAL-HOST-B"}):
            with self.assertRaisesRegex(LocalError, "plan_confirmation_mismatch"):
                canary.create(self.folder, confirm=reviewed["plan_sha256"])
        self.assertFalse(self.folder.exists())

    def test_missing_host_refused_without_files(self):
        with patch.dict(os.environ, {"COMPUTERNAME": ""}), patch("grokbot_mcp.canary.socket.gethostname", return_value=""):
            with self.assertRaisesRegex(LocalError, "canary_machine_unavailable"):
                canary.plan(self.folder)
        self.assertFalse(self.folder.exists())
