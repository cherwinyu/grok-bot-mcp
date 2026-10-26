"""Synthetic credential input tests; never call Windows Credential Manager."""

import contextlib
import getpass
import io
import traceback
import unittest
from unittest.mock import Mock, patch

from grokbot_mcp import init_credentials as cli
from grokbot_mcp.credentials.blob import BlobError, pack_blob, unpack_blob
from grokbot_mcp.credentials.windows import CredentialError, store_credential


URL = "https://example.invalid/fictional-credential-hook"
KEY = "fictional-webhook-key-never-real"
TARGET = "grokbot-mcp/fictional-workspace/validate"
ENV_ARGS = ["--url-env", "FICTIONAL_URL", "--key-env", "FICTIONAL_KEY"]


class NoninteractiveCredentialsTest(unittest.TestCase):
    def setUp(self):
        self.store = self.enterContext(patch.object(cli, "store_credential"))
        self.prompt = self.enterContext(patch.object(cli.getpass, "getpass", side_effect=AssertionError("unexpected prompt")))
        self.environ = {"FICTIONAL_URL": URL, "FICTIONAL_KEY": KEY}
        # Replace the mapping itself: no real environment values are inspected.
        self.enterContext(patch.object(cli.os, "environ", self.environ))
        self.enterContext(patch("grokbot_mcp.credentials.windows._apis", side_effect=AssertionError("real credential API forbidden")))

    def invoke(self, args, *, stdin="", target=TARGET, secrets=(URL, KEY)):
        stdout, stderr = io.StringIO(), io.StringIO()
        exception_text = ""
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), patch.object(cli.sys, "stdin", io.StringIO(stdin)):
            try:
                code = cli.main(["--target", target, *args])
            except SystemExit as exc:
                code = exc.code
                exception_text = "".join(traceback.format_exception(exc))
            except Exception as exc:
                for secret in secrets:
                    if secret:
                        self.assertNotIn(secret, str(exc))
                raise
        output = stdout.getvalue() + stderr.getvalue() + exception_text
        for secret in secrets:
            if secret:
                self.assertNotIn(secret, output)
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_stored(self):
        self.store.assert_called_once_with(TARGET, pack_blob(URL, KEY))
        self.assertEqual(unpack_blob(self.store.call_args.args[1]), (URL, KEY))
        self.prompt.assert_not_called()

    def test_stdin_stores_two_lines_without_confirmation(self):
        self.assertEqual(self.invoke(["--stdin"], stdin=URL + "\n" + KEY + "\n"), (0, "credential_stored\n", ""))
        self.assert_stored()

    def test_stdin_crlf_and_missing_final_newline(self):
        for ending in ("\r\n", ""):
            with self.subTest(ending=repr(ending)):
                self.store.reset_mock()
                self.assertEqual(self.invoke(["--stdin"], stdin=URL + "\r\n" + KEY + ending)[0], 0)
                self.assert_stored()

    def test_env_stores_without_reading_stdin(self):
        with patch.object(cli.sys, "stdin", Mock(readline=Mock(side_effect=AssertionError("stdin forbidden")))):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stdout):
                self.assertEqual(cli.main(["--target", TARGET, *ENV_ARGS]), 0)
            self.assertEqual(stdout.getvalue(), "credential_stored\n")
        self.assert_stored()

    def test_all_sources_share_normalization(self):
        padded_url, padded_key = "  " + URL + "  ", " Authorization: Bearer " + KEY + "  "
        for source in ("stdin", "env", "interactive"):
            with self.subTest(source=source):
                self.store.reset_mock()
                self.environ.update(FICTIONAL_URL=padded_url, FICTIONAL_KEY=padded_key)
                self.prompt.side_effect = [padded_url, padded_key, padded_key]
                args = ["--stdin"] if source == "stdin" else ENV_ARGS if source == "env" else []
                self.assertEqual(self.invoke(args, stdin=padded_url + "\n" + padded_key + "\n")[0], 0)
                self.store.assert_called_once_with(TARGET, pack_blob(URL, KEY))

    def test_env_missing_and_empty(self):
        for name in ("FICTIONAL_URL", "FICTIONAL_KEY"):
            for missing in (True, False):
                with self.subTest(name=name, missing=missing):
                    self.environ.update(FICTIONAL_URL=URL, FICTIONAL_KEY=KEY)
                    if missing:
                        del self.environ[name]
                    else:
                        self.environ[name] = ""
                    expected = "credential_env_missing" if missing else "credential_env_empty"
                    self.assertEqual(self.invoke(ENV_ARGS), (1, expected + ": " + name + "\n", ""))
        self.store.assert_not_called()
        self.prompt.assert_not_called()

    def test_invalid_env_names_never_echo(self):
        for name in ("", "1NAME", "BAD-NAME", "NAME\n", "变量", URL, KEY):
            for option in ("--url-env", "--key-env"):
                with self.subTest(option=option):
                    args = ENV_ARGS.copy()
                    args[args.index(option) + 1] = name
                    self.assertEqual(self.invoke(args), (2, "credential_env_name_invalid\n", ""))
        self.store.assert_not_called()
        self.prompt.assert_not_called()

    def test_source_conflict_and_incomplete_pairs(self):
        cases = [(["--stdin", *ENV_ARGS], "credential_input_conflict"),
                 (["--stdin", "--url-env", "FICTIONAL_URL"], "credential_input_conflict"),
                 (["--url-env", "FICTIONAL_URL"], "credential_env_pair_required"),
                 (["--key-env", "FICTIONAL_KEY"], "credential_env_pair_required")]
        for args, error in cases:
            with self.subTest(error=error):
                self.assertEqual(self.invoke(args, stdin=URL + "\n" + KEY), (2, error + "\n", ""))
        self.store.assert_not_called()
        self.prompt.assert_not_called()

    def test_stdin_missing_lines(self):
        for text in ("", URL, URL + "\n"):
            self.assertEqual(self.invoke(["--stdin"], stdin=text), (1, "credential_stdin_incomplete\n", ""))
        self.store.assert_not_called()
        self.prompt.assert_not_called()

    def test_validation_rejects_same_values_for_every_source(self):
        pairs = [("http://example.invalid/fictional-hook", KEY), (URL, ""), ("", KEY),
                 (URL, KEY + "\x00"), (URL, KEY + "\r" + KEY), (URL, " " * 3),
                 (URL, KEY * 100), (URL, KEY + "\ud800")]
        for url, key in pairs:
            for source in ("stdin", "env", "interactive"):
                with self.subTest(source=source):
                    self.environ.update(FICTIONAL_URL=url, FICTIONAL_KEY=key)
                    self.prompt.side_effect = [url, key, key]
                    args = ["--stdin"] if source == "stdin" else ENV_ARGS if source == "env" else []
                    result = self.invoke(args, stdin=url + "\n" + key + "\n", secrets=(url, key, URL, KEY))
                    self.assertEqual(result[0], 1)
        self.store.assert_not_called()

    def test_empty_target_uses_existing_storage_rejection(self):
        self.store.side_effect = store_credential
        for args in (["--stdin"], ENV_ARGS, []):
            self.prompt.side_effect = [URL, KEY, KEY]
            self.assertEqual(self.invoke(args, stdin=URL + "\n" + KEY, target=""), (1, "credential_rejected\n", ""))

    def test_storage_errors_do_not_echo_exception_values(self):
        for error_type in (BlobError, CredentialError, OSError):
            self.store.side_effect = error_type(URL + KEY)
            self.assertEqual(self.invoke(ENV_ARGS), (1, "credential_rejected\n", ""))

    def test_stdin_read_errors_are_safe(self):
        for error_type in (OSError, UnicodeError, ValueError):
            stream = Mock()
            stream.readline.side_effect = error_type(URL + KEY)
            output = io.StringIO()
            with patch.object(cli.sys, "stdin", stream), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                self.assertEqual(cli.main(["--target", TARGET, "--stdin"]), 1)
            self.assertEqual(output.getvalue(), "credential_input_unavailable\n")
        self.store.assert_not_called()

    def test_parser_errors_hide_accidental_secret_arguments(self):
        for args in ([URL, KEY], ["--stdin=" + KEY], ["--url-env"], ["--unknown=" + URL]):
            result = self.invoke(args)
            self.assertEqual(result[0], 2)
            self.assertIn("credential_arguments_invalid", result[2])
        self.store.assert_not_called()

    def test_default_interactive_confirmation_unchanged(self):
        self.prompt.side_effect = [URL, KEY, KEY]
        self.assertEqual(self.invoke([]), (0, "credential_stored\n", ""))
        self.assertEqual([call.args[0] for call in self.prompt.call_args_list],
                         ["Webhook URL: ", "Webhook key: ", "Webhook key again: "])
        self.store.assert_called_once_with(TARGET, pack_blob(URL, KEY))

    def test_default_interactive_mismatch_and_insecure_input(self):
        self.prompt.side_effect = [URL, KEY, "fictional-different-key"]
        self.assertEqual(self.invoke([]), (2, "credential_mismatch\n", ""))
        self.prompt.side_effect = getpass.GetPassWarning(URL + KEY)
        self.assertEqual(self.invoke([]), (1, "secure_input_unavailable: run in an interactive terminal\n", ""))
        self.store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
