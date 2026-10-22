"""Tests for init_credentials --from-env. Never touches real Credential Manager."""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import patch

from grokbot_mcp.credentials.blob import pack_blob, unpack_blob
from grokbot_mcp.init_credentials import main


class InitCredentialsEnvTest(unittest.TestCase):
    TARGET = "grokbot-mcp/example-workspace/validate"
    URL = "https://example.invalid/hook"
    KEY = "example-secret-key"
    URL_VAR = "GROKBOT_TEST_WEBHOOK_URL"
    KEY_VAR = "GROKBOT_TEST_WEBHOOK_KEY"

    def tearDown(self) -> None:
        os.environ.pop(self.URL_VAR, None)
        os.environ.pop(self.KEY_VAR, None)

    def test_from_env_stores_clears_and_hides_values(self) -> None:
        stored: list[tuple[str, bytes]] = []

        def fake_store(target: str, blob: bytes) -> None:
            stored.append((target, blob))

        os.environ[self.URL_VAR] = self.URL
        os.environ[self.KEY_VAR] = self.KEY
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("grokbot_mcp.init_credentials.store_credential", side_effect=fake_store), patch(
            "sys.stdout", stdout
        ), patch("sys.stderr", stderr):
            code = main(["--target", self.TARGET, "--from-env", self.URL_VAR, self.KEY_VAR])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), "credential_stored")
        self.assertNotIn(self.URL, stdout.getvalue())
        self.assertNotIn(self.KEY, stdout.getvalue())
        self.assertNotIn(self.URL, stderr.getvalue())
        self.assertNotIn(self.KEY, stderr.getvalue())
        self.assertNotIn(self.URL_VAR, os.environ)
        self.assertNotIn(self.KEY_VAR, os.environ)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0][0], self.TARGET)
        self.assertEqual(unpack_blob(stored[0][1]), (self.URL, self.KEY))
        self.assertEqual(stored[0][1], pack_blob(self.URL, self.KEY))

    def test_missing_or_empty_env_vars(self) -> None:
        cases = [
            {},
            {self.URL_VAR: self.URL},
            {self.KEY_VAR: self.KEY},
            {self.URL_VAR: "", self.KEY_VAR: self.KEY},
            {self.URL_VAR: self.URL, self.KEY_VAR: ""},
            {self.URL_VAR: "", self.KEY_VAR: ""},
        ]
        for env in cases:
            with self.subTest(env=env):
                os.environ.pop(self.URL_VAR, None)
                os.environ.pop(self.KEY_VAR, None)
                os.environ.update(env)
                stdout = io.StringIO()
                with patch("grokbot_mcp.init_credentials.store_credential") as store, patch(
                    "sys.stdout", stdout
                ):
                    code = main(["--target", self.TARGET, "--from-env", self.URL_VAR, self.KEY_VAR])
                self.assertEqual(code, 1)
                self.assertEqual(stdout.getvalue().strip(), "credential_env_missing")
                store.assert_not_called()

    def test_interactive_path_unchanged_without_from_env(self) -> None:
        prompts: list[str] = []

        def fake_getpass(prompt: str = "") -> str:
            prompts.append(prompt)
            if prompt.startswith("Webhook URL"):
                return self.URL
            if prompt.startswith("Webhook key again"):
                return self.KEY
            return self.KEY

        stored: list[tuple[str, bytes]] = []

        def fake_store(target: str, blob: bytes) -> None:
            stored.append((target, blob))

        stdout = io.StringIO()
        with patch("grokbot_mcp.init_credentials.getpass.getpass", side_effect=fake_getpass), patch(
            "grokbot_mcp.init_credentials.store_credential", side_effect=fake_store
        ), patch("sys.stdout", stdout):
            code = main(["--target", self.TARGET])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), "credential_stored")
        self.assertEqual(
            prompts,
            ["Webhook URL: ", "Webhook key: ", "Webhook key again: "],
        )
        self.assertEqual(len(stored), 1)
        self.assertEqual(unpack_blob(stored[0][1]), (self.URL, self.KEY))

    def test_interactive_mismatch_unchanged(self) -> None:
        def fake_getpass(prompt: str = "") -> str:
            if prompt.startswith("Webhook URL"):
                return self.URL
            if prompt.startswith("Webhook key again"):
                return "other-key"
            return self.KEY

        stdout = io.StringIO()
        with patch("grokbot_mcp.init_credentials.getpass.getpass", side_effect=fake_getpass), patch(
            "grokbot_mcp.init_credentials.store_credential"
        ) as store, patch("sys.stdout", stdout):
            code = main(["--target", self.TARGET])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue().strip(), "credential_mismatch")
        store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
