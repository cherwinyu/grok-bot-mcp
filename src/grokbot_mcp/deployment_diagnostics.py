"""Shareable deployment diagnostics are reconstructed, not path-hash anonymized."""
from __future__ import annotations

import re
from pathlib import Path
from grokbot_mcp.local_safety import create_new, json_bytes

FLAGS = ("ok", "configuration_updated", "backup_created", "standalone_handshake_passed", "client_reconnection_confirmed",
         "imports_verified", "credentials_checked", "remote_binding_verified", "live_execution_verified",
         "administrator_remote_review_confirmed", "local_configuration_applied", "identity_matches")


def public_report(report: dict) -> dict:
    result = {"schema_version": 1, "scope": "shareable deployment summary; all paths and correlation identifiers omitted",
              "workspace": "workspace-1", "installation": "installation-1", "registry": "registry-1", "qa_accepted": False}
    for key in FLAGS:
        value = report.get(key)
        result[key] = value if type(value) is bool else None
    # Never stringify unknown objects, values or original exception messages.
    version = (report.get("expected_identity") or {}).get("bridge_version") if isinstance(report.get("expected_identity"), dict) else None
    if type(version) is str and re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?", version):
        result["bridge_version"] = version
    from grokbot_mcp.diagnostics import CATALOG
    codes = report.get("codes")
    values = codes if type(codes) is list else []
    result["codes"] = [code for code in values if type(code) is str and code in CATALOG]
    result["unknown_codes_omitted"] = type(codes) is not list or len(result["codes"]) != len(values)
    return result


def export(path: Path, report: dict, registry: Path) -> None:
    from grokbot_mcp.setup_wizard import validate_export_destination
    destination = validate_export_destination(path, registry)
    create_new(destination, json_bytes(public_report(report)))
