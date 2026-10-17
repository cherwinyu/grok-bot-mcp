"""Remove secrets from text that might be returned to a caller."""

from __future__ import annotations

import re

_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_BEARER = re.compile(r"bearer\s+\S+", re.IGNORECASE)
_AUTH = re.compile(r"authorization\s*[:=]\s*\S+", re.IGNORECASE)
_COOKIE = re.compile(r"cookie\s*[:=]\s*\S+", re.IGNORECASE)
_SECRET = re.compile(
    r"(?:api[_-]?key|access[_-]?key|token|secret|password)\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_PREFIXED_KEY = re.compile(r"\b(?:sk|whsec|rk)_[A-Za-z0-9]+", re.IGNORECASE)


def redact(text: str) -> str:
    cleaned = _URL.sub("[redacted-url]", text)
    cleaned = _BEARER.sub("Bearer [redacted]", cleaned)
    cleaned = _AUTH.sub("Authorization: [redacted]", cleaned)
    cleaned = _COOKIE.sub("Cookie: [redacted]", cleaned)
    cleaned = _SECRET.sub("[redacted-secret]", cleaned)
    return _PREFIXED_KEY.sub("[redacted-key]", cleaned)
