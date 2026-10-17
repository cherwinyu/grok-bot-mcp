"""Safe validation summaries without rewriting frozen evidence or status decisions."""
from __future__ import annotations

_FIELDS = ("manifest_sha256", "workspace_id", "validation_passed",
           "validation_errors", "allowed_files", "code_modified")


def public_validation_fields(data: dict, issue: object, *,
                             manifest_sha256: str | None, workspace_id: str) -> dict:
    """Only trusted identity and field/type diagnostics accompany rejected payloads.

    Both a fresh interpretation and a cached dev4 failure use this presentation
    filter. The input, proof bytes, ledger and historical decision are untouched.
    Valid success/failure summaries keep their established field values.
    """
    if isinstance(issue, dict) and issue.get("category") in {"format_error", "identity_error"}:
        return {"manifest_sha256": manifest_sha256, "workspace_id": workspace_id,
                "validation_passed": None, "validation_errors": None,
                "allowed_files": None, "code_modified": None}
    return {field: data.get(field) for field in _FIELDS}
