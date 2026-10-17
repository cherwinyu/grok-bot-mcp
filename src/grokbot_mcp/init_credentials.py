"""Store one webhook credential. The URL and key are never printed."""

from __future__ import annotations

import argparse
import getpass
import sys
import warnings

from grokbot_mcp.credentials.blob import BlobError, pack_blob
from grokbot_mcp.credentials.windows import CredentialError, store_credential


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Store one webhook credential in Windows Credential Manager.")
    parser.add_argument("--target", required=True, help="Credential target name, for example grokbot-mcp/example-workspace/validate")
    args = parser.parse_args(argv)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            url = getpass.getpass("Webhook URL: ")
            key = getpass.getpass("Webhook key: ")
            confirm = getpass.getpass("Webhook key again: ")
    except getpass.GetPassWarning:
        print("secure_input_unavailable: run in an interactive terminal")
        return 1
    if key != confirm:
        print("credential_mismatch")
        return 2
    try:
        store_credential(args.target, pack_blob(url, key))
    except (BlobError, CredentialError):
        print("credential_rejected")
        return 1
    finally:
        url = ""
        key = ""
        confirm = ""
    print("credential_stored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
