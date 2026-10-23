"""Shared CLI presentation: stable codes, no raw exception or arbitrary payload echo."""
from __future__ import annotations
import argparse
import codecs
import json
import sys
from grokbot_mcp.local_safety import LocalError


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise LocalError("invalid_arguments")


def emit(value: dict) -> None:
    try:
        utf8 = codecs.lookup(getattr(sys.stdout, "encoding", None) or "ascii").name == "utf-8"
    except LookupError:
        utf8 = False
    print(json.dumps(value, ensure_ascii=not utf8, indent=2))


def failure(exc: Exception) -> dict:
    # Codes originate in this package, never from repr/str of external exceptions.
    from grokbot_mcp.result_contract import ContractError
    from grokbot_mcp.registry import RegistryError
    from grokbot_mcp.manifest.store import ManifestError
    from grokbot_mcp.diagnostics import CATALOG
    if isinstance(exc, ContractError):
        info = exc.report()
    elif isinstance(exc, (LocalError, RegistryError, ManifestError)):
        info = {"code": exc.code}
    elif type(exc) is ValueError and str(exc) in CATALOG:
        info = {"code": str(exc)}
    else:
        info = {"code": "local_operation_failed"}
    from grokbot_mcp.security.paths import is_uuid
    transaction = getattr(exc, "transaction_id", None)
    extra = {"transaction_id": transaction} if type(transaction) is str and is_uuid(transaction) else {}
    advice = CATALOG.get(info["code"], CATALOG["local_operation_failed"])
    return {**extra, "ok": False, "diagnostic": {**advice, **info}, "webhook_sent": False,
            "qa_accepted": False, "next_step": advice["next_step"]}
