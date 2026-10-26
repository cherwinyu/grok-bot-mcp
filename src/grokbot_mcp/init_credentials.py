"""Store one webhook credential. The URL and key are never printed."""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
import warnings

from grokbot_mcp.credentials.blob import BlobError, pack_blob
from grokbot_mcp.credentials.windows import CredentialError, store_credential


class _CredentialParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's original message can contain user-supplied secrets.
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: credential_arguments_invalid", file=sys.stderr)
        raise SystemExit(2) from None


class CredentialInputError(ValueError):
    """A safe input error containing only a code and optional variable name."""


def read_credentials(args: argparse.Namespace) -> tuple[str, str]:
    """Read one source without echoing values; leave validation to pack_blob."""
    has_env = args.url_env is not None or args.key_env is not None
    if args.stdin and has_env:
        raise CredentialInputError("credential_input_conflict")
    if has_env:
        if args.url_env is None or args.key_env is None:
            raise CredentialInputError("credential_env_pair_required")
        for name in (args.url_env, args.key_env):
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
                # Invalid names may actually be pasted credential values.
                raise CredentialInputError("credential_env_name_invalid")
        values = []
        for name in (args.url_env, args.key_env):
            value = os.environ.get(name)
            if value is None:
                raise CredentialInputError(f"credential_env_missing: {name}")
            if value == "":
                raise CredentialInputError(f"credential_env_empty: {name}")
            values.append(value)
        return values[0], values[1]
    if args.stdin:
        try:
            url = sys.stdin.readline()
            key = sys.stdin.readline()
        except (OSError, ValueError):
            raise CredentialInputError("credential_input_unavailable") from None
        if not url or not key:
            raise CredentialInputError("credential_stdin_incomplete")
        return url.removesuffix("\n").removesuffix("\r"), key.removesuffix("\n").removesuffix("\r")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        url = getpass.getpass("Webhook URL: ")
        key = getpass.getpass("Webhook key: ")
        confirm = getpass.getpass("Webhook key again: ")
    if key != confirm:
        raise CredentialInputError("credential_mismatch")
    return url, key


def main(argv: list[str] | None = None) -> int:
    parser = _CredentialParser(description="Store one webhook credential in Windows Credential Manager.")
    parser.add_argument("--target", required=True, help="Credential target name, for example grokbot-mcp/example-workspace/validate")
    parser.add_argument("--stdin", action="store_true", help="Read URL then key from two stdin lines without confirmation")
    parser.add_argument("--url-env", metavar="NAME", help="Read URL from this environment variable; requires --key-env")
    parser.add_argument("--key-env", metavar="NAME", help="Read key from this environment variable; requires --url-env")
    args = parser.parse_args(argv)
    try:
        url, key = read_credentials(args)
    except CredentialInputError as exc:
        print(str(exc))
        return 2 if str(exc) in {"credential_input_conflict", "credential_env_pair_required",
                                "credential_env_name_invalid", "credential_mismatch"} else 1
    except getpass.GetPassWarning:
        print("secure_input_unavailable: run in an interactive terminal")
        return 1
    except (EOFError, OSError, ValueError):
        print("credential_input_unavailable")
        return 1
    try:
        store_credential(args.target, pack_blob(url, key))
    except (BlobError, CredentialError, UnicodeError, OSError):
        print("credential_rejected")
        return 1
    finally:
        url = ""
        key = ""
    print("credential_stored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
