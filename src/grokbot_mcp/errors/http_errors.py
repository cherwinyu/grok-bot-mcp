"""Classify HTTP failures without storing a raw response body.

Quota is confirmed only from explicit wording. HTTP 400 by itself is not quota exhaustion.
Missing facts stay null or the string "unknown".
"""

from __future__ import annotations

import json
import re

from grokbot_mcp.security.redact import redact

MAX_BODY_BYTES = 4096
MAX_SUMMARY_CHARS = 240
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")

_QUOTA_PHRASES = (
    "weekly usage exhausted",
    "weekly usage is exhausted",
    "weekly usage has been exceeded",
    "weekly usage limit",
    "weekly quota exhausted",
    "weekly quota is exhausted",
    "weekly quota exceeded",
    "usage limit exceeded",
    "usage limit reached",
    "reached your weekly",
    "reached your usage limit",
    "out of weekly usage",
    "quota exhausted",
    "weekly limit reached",
    "included usage exhausted",
)
_RATE_PHRASES = ("rate limit", "too many requests", "automation run rate", "retry-after")
_AUTH_PHRASES = (
    "invalid api key",
    "invalid credential",
    "authentication failed",
    "unauthorized",
    "not authorized",
)
_INVALID_PHRASES = (
    "invalid json",
    "json schema",
    "unexpected field",
    "malformed",
    "additional properties",
    "unknown field",
    "nested json",
)
_PLATFORM_PHRASES = (
    "service unavailable",
    "internal server error",
    "temporarily unavailable",
    "platform error",
)

QUOTA_MESSAGE = (
    "The remote service returned an explicit quota-exhausted error. The task did not finish. "
    "Check the service billing settings yourself. This bridge will not retry and will not enable on-demand."
)
PAUSED_MESSAGE = (
    "The webhook returned HTTP 400 and the body indicates a paused routine. "
    "Confirm the routine is enabled. This bridge will not retry."
)
AUTH_MESSAGE = "Webhook authentication was rejected. Check the credential target, URL, and key. This bridge will not retry."
UNKNOWN_HTTP_MESSAGE = (
    "The remote service rejected the request, but the evidence does not identify the cause. "
    "This error does not prove that a weekly quota is exhausted. This bridge will not retry."
)
RATE_MESSAGE = "The remote service rate-limited the request. That is not the same as weekly quota exhaustion. This bridge will not retry."
INVALID_MESSAGE = "The payload was rejected. Check the JSON fields agreed with the routine. This bridge will not retry."
PLATFORM_MESSAGE = "The remote service was unavailable. This bridge will not retry and cannot infer quota from this error."
QUOTA_UNKNOWN_MESSAGE = (
    "The response mentions usage or billing without an explicit weekly-quota conclusion. "
    "This bridge will not retry."
)
ON_DEMAND_UNKNOWN_MESSAGE = (
    "The response mentions on-demand without saying whether it is enabled. "
    "This bridge will not retry and will not change billing settings."
)
NETWORK_MESSAGE = "A network or timeout error occurred. The bridge cannot confirm delivery. This bridge will not retry."
UNKNOWN_SUBMISSION_MESSAGE = "The bridge cannot confirm that the remote service received the request. This bridge will not retry."


def bounded_read(response: object, limit: int = MAX_BODY_BYTES) -> bytes:
    read = getattr(response, "read", None)
    if not callable(read):
        return b""
    try:
        data = read(limit)
    except TypeError:
        try:
            data = read()
        except Exception:
            return b""
    except Exception:
        return b""
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    if not isinstance(data, (bytes, bytearray)):
        return b""
    return bytes(data[:limit])


def success_usage() -> dict[str, object]:
    return {
        "failure_category": None,
        "remote_error_code": None,
        "sanitized_error_summary": None,
        "remote_request_id": None,
        "operator_message": None,
        "retry_allowed": False,
        "operator_action_required": False,
        "usage_remaining": "unknown",
        "weekly_quota_exhausted": "unknown",
        "on_demand_active": "unknown",
        "evidence_level": "none",
    }


def classify_transport(kind: str) -> dict[str, object]:
    if kind == "timeout":
        return _base("network_error", NETWORK_MESSAGE, "transport", None, None, None)
    return _base("unknown_submission", UNKNOWN_SUBMISSION_MESSAGE, "none", None, None, None)


def classify_http_failure(status: int | None, body: bytes | None) -> dict[str, object]:
    text = _decode(body)
    error_code, message, request_id, parsed_text = _extract(text)
    haystack = " ".join(part for part in (parsed_text, text) if part).lower()
    summary = _summary(message or text)
    on_demand = _on_demand_flag(haystack)
    evidence = "explicit_response" if haystack else "status_only"
    if _contains(haystack, _QUOTA_PHRASES):
        found = _base("quota_exhausted_confirmed", QUOTA_MESSAGE, "explicit_response", summary, error_code, request_id)
        found["weekly_quota_exhausted"] = True
        found["on_demand_active"] = on_demand
        return found
    if status == 429 or _contains(haystack, _RATE_PHRASES):
        return _base("rate_limited", RATE_MESSAGE, evidence, summary, error_code, request_id, on_demand)
    if "paused" in haystack and any(word in haystack for word in ("routine", "automation", "workflow")):
        return _base("routine_paused_suspected", PAUSED_MESSAGE, "explicit_response", summary, error_code, request_id, on_demand)
    if status in {401, 403} or _contains(haystack, _AUTH_PHRASES):
        return _base("authentication_failed", AUTH_MESSAGE, evidence, summary, error_code, request_id, on_demand)
    if _contains(haystack, _INVALID_PHRASES):
        return _base("invalid_payload", INVALID_MESSAGE, "explicit_response", summary, error_code, request_id, on_demand)
    if status in {500, 502, 503, 504} or _contains(haystack, _PLATFORM_PHRASES):
        return _base("platform_unavailable", PLATFORM_MESSAGE, evidence, summary, error_code, request_id, on_demand)
    if _billing_without_conclusion(haystack):
        if "on-demand" in haystack or "on demand" in haystack:
            return _base("on_demand_status_unknown", ON_DEMAND_UNKNOWN_MESSAGE, "explicit_response", summary, error_code, request_id, on_demand)
        return _base("quota_status_unknown", QUOTA_UNKNOWN_MESSAGE, "explicit_response", summary, error_code, request_id, on_demand)
    if status == 400:
        return _base("http_400_unknown", UNKNOWN_HTTP_MESSAGE, evidence, summary, error_code, request_id, on_demand)
    return _base("unclassified_http_status", UNKNOWN_HTTP_MESSAGE, evidence, summary, error_code, request_id, on_demand)


def diagnosis_for_outcome(kind: str, status: int | None, diagnosis: dict[str, object] | None) -> dict[str, object]:
    if kind == "status" and status == 200:
        return success_usage()
    if isinstance(diagnosis, dict) and diagnosis.get("failure_category"):
        merged = success_usage()
        merged.update(diagnosis)
        merged["retry_allowed"] = False
        if merged.get("failure_category") != "quota_exhausted_confirmed":
            merged["weekly_quota_exhausted"] = "unknown"
        if merged.get("on_demand_active") not in {True, False, "unknown"}:
            merged["on_demand_active"] = "unknown"
        return merged
    if kind == "status" and status is not None:
        return classify_http_failure(status, b"")
    return classify_transport(kind)


def _base(
    category: str,
    message: str,
    evidence: str,
    summary: str | None,
    error_code: str | None,
    request_id: str | None,
    on_demand: object = "unknown",
) -> dict[str, object]:
    return {
        "failure_category": category,
        "remote_error_code": error_code,
        "sanitized_error_summary": summary,
        "remote_request_id": request_id,
        "operator_message": message,
        "retry_allowed": False,
        "operator_action_required": True,
        "usage_remaining": "unknown",
        "weekly_quota_exhausted": "unknown",
        "on_demand_active": on_demand if on_demand in {True, False, "unknown"} else "unknown",
        "evidence_level": evidence,
    }


def _decode(body: bytes | None) -> str:
    if not body:
        return ""
    try:
        return bytes(body[:MAX_BODY_BYTES]).decode("utf-8")
    except UnicodeError:
        return ""


def _extract(text: str) -> tuple[str | None, str | None, str | None, str]:
    if not text:
        return None, None, None, ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, None, None, ""
    if not isinstance(parsed, dict):
        return None, None, None, ""
    error_code = _clean_id(parsed.get("error_code") or parsed.get("code"))
    message = parsed.get("message")
    if message is None and isinstance(parsed.get("error"), str):
        message = parsed.get("error")
    if not isinstance(message, str):
        message = None
    request_id = _clean_id(parsed.get("request_id") or parsed.get("requestId"))
    pieces = [part for part in (error_code, message) if isinstance(part, str)]
    return error_code, message, request_id, " ".join(pieces)


def _clean_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = redact(value).strip()
    if not cleaned or cleaned.startswith("[redacted") or "http" in cleaned.lower():
        return None
    if not _REQUEST_ID.match(cleaned):
        return None
    return cleaned


def _summary(text: str | None) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return None
    cleaned = " ".join(redact(text).split())
    if not cleaned:
        return None
    if len(cleaned) > MAX_SUMMARY_CHARS:
        return cleaned[: MAX_SUMMARY_CHARS - 1] + "…"
    return cleaned


def _contains(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _on_demand_flag(text: str) -> object:
    if "on-demand is disabled" in text or "on demand is disabled" in text or "on-demand disabled" in text:
        return False
    if "on-demand is enabled" in text or "on demand is enabled" in text or "on-demand enabled" in text:
        return True
    return "unknown"


def _billing_without_conclusion(text: str) -> bool:
    markers = ("on-demand", "on demand", "weekly usage", "usage & billing", "billing", "quota")
    return any(marker in text for marker in markers)
