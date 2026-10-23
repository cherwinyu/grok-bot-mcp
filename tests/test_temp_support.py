"""Fixture placement and cleanup, including failed setup."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.support import temporary_directory


class TempSupportTest(unittest.TestCase):
    def test_default_passes_system_parent_and_keeps_prefix(self):
        with patch.dict(os.environ):
            os.environ.pop("GROKBOT_TEST_TMPDIR", None)
            with patch("tests.support.tempfile.TemporaryDirectory") as create:
                temporary_directory(prefix="fictional-")
            create.assert_called_once_with(prefix="fictional-", dir=None)

    def test_configured_parent_and_exception_cleanup(self):
        with temporary_directory() as parent:
            with patch.dict(os.environ, GROKBOT_TEST_TMPDIR=parent):
                with self.assertRaisesRegex(RuntimeError, "fixture_failure"):
                    with temporary_directory(prefix="fictional-") as name:
                        self.assertEqual(Path(name).parent, Path(parent))
                        self.assertTrue(Path(name).name.startswith("fictional-"))
                        raise RuntimeError("fixture_failure")
            self.assertEqual(list(Path(parent).iterdir()), [])

    def test_addcleanup_runs_after_setup_failure(self):
        with temporary_directory() as parent:
            class BrokenSetup(unittest.TestCase):
                def setUp(self):
                    directory = temporary_directory()
                    self.addCleanup(directory.cleanup)
                    raise RuntimeError("fixture_failure")

                def runTest(self):
                    pass

            with patch.dict(os.environ, GROKBOT_TEST_TMPDIR=parent):
                result = unittest.TestResult()
                BrokenSetup().run(result)
            self.assertEqual(len(result.errors), 1)
            self.assertEqual(list(Path(parent).iterdir()), [])
