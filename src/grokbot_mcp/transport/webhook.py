"""Send one JSON webhook. Callers pass the URL and key; this module stores neither."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from http.client import HTTPSConnection
from typing import Callable
from urllib.parse import urlsplit

from grokbot_mcp.credentials.blob import BlobError, validate_url
from grokbot_mcp.errors.http_errors import bounded_read, classify_http_failure

ConnectionFactory = Callable[[str, int, float], object]


@dataclass(frozen=True)
class HttpOutcome:
    kind: str
    status: int | None
    diagnosis: dict[str, object] | None = None


def build_payload(action: str, **fields: str) -> dict[str, object]:
    payload: dict[str, object] = {"protocol_version": 1, "action": action}
    payload.update(fields)
    if payload.get("action") != action:
        raise ValueError("payload_rejected")
    return payload


def post_once(
    url: str,
    key: str,
    payload: dict[str, object],
    timeout: float,
    *,
    enabled: bool,
    connection_factory: ConnectionFactory | None = None,
) -> HttpOutcome:
    """Return before any connection while enabled is false."""
    if not enabled:
        return HttpOutcome(kind="disabled", status=None)
    if not key or not isinstance(payload.get("action"), str):
        return HttpOutcome(kind="unknown", status=None)
    try:
        validate_url(url)
    except BlobError:
        return HttpOutcome(kind="unknown", status=None)
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or 443
    path = parts.path or "/"
    if parts.query:
        path = path + "?" + parts.query
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    factory = connection_factory or (lambda item_host, item_port, item_timeout: HTTPSConnection(item_host, item_port, timeout=item_timeout))
    conn = factory(host, port, timeout)
    try:
        conn.request("POST", path, body=body, headers=headers)  # type: ignore[attr-defined]
        response = conn.getresponse()  # type: ignore[attr-defined]
        status = int(response.status)
        raw = bounded_read(response)
        diagnosis = None if status == 200 else classify_http_failure(status, raw)
        return HttpOutcome(kind="status", status=status, diagnosis=diagnosis)
    except (TimeoutError, socket.timeout):
        return HttpOutcome(kind="timeout", status=None)
    except Exception:
        return HttpOutcome(kind="unknown", status=None)
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()
        key = ""
        url = ""
