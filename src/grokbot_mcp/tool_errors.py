"""Small safe tool boundary using the existing logging stream, never payloads."""
from __future__ import annotations

import inspect
import logging
from functools import wraps

from grokbot_mcp.strict_json import LedgerTooLarge


def invalid_argument(accepted: list[str]) -> dict:
    return {"ok": False, "error": "invalid_argument", "accepted_arguments": accepted,
            "reason": "Use the documented parameter names and types."}


def internal_error(exc: Exception) -> dict:
    # Exception messages, causes and tracebacks can contain credentials/paths.
    kind = type(exc).__name__
    kind = kind if kind.isidentifier() and len(kind) <= 80 else "Exception"
    logging.getLogger(__name__).error("internal_error exception_type=%s", kind)
    result = {"ok": False, "error": "internal_error", "exception_type": kind,
              "reason": "Local operation failed. Inspect local diagnostics; do not resend."}
    try:
        # Mixed installed/loaded versions can fail before normal drift reporting.
        # Even the diagnostic import may fail; preserve the original safe error.
        from grokbot_mcp.identity import runtime_drift
        if runtime_drift()["package_changed"]:
            result["warnings"] = ["process_restart_recommended"]
            result["next_step"] = (
                "Restart the MCP client process. Do not resend or retry submissions. / "
                "重启 MCP 客户端进程；不要重发或重试提交。"
            )
    except Exception:
        pass
    return result


def guarded(function):
    signature = inspect.signature(function)

    @wraps(function)
    def invoke(*args, **kwargs):
        try:
            signature.bind(*args, **kwargs)
        except TypeError:
            return invalid_argument(list(signature.parameters))
        try:
            return function(*args, **kwargs)
        except LedgerTooLarge as exc:
            return {"ok": False, "error": exc.code, "size_bytes": exc.size_bytes}
        except Exception as exc:
            return internal_error(exc)

    return invoke
