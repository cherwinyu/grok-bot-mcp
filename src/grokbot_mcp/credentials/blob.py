"""Packed webhook blob. This module does not read Credential Manager."""

from __future__ import annotations

import struct
import ipaddress
import re
from urllib.parse import urlsplit

MAGIC = b"GWH1"
MAX_FIELD = 1024


class BlobError(ValueError):
    """Invalid blob. The message never includes credential bytes."""


def normalize_secret(value: str) -> str:
    text = value.strip()
    if text.lower().startswith("authorization:"):
        text = text.split(":", 1)[1].strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    return text


def validate_url(url: str) -> None:
    """Reject unsafe literal destinations before credentials reach a socket.

    Does not attest ownership of a DNS domain or defend against DNS rebinding.
    Endpoint provenance still requires the operator's official Routine screen.
    """
    def reject():
        raise BlobError("webhook url rejected")
    if type(url) is not str or not url or any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        reject()
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.netloc or parts.username is not None
                or parts.password is not None or parts.fragment or "\\" in parts.netloc):
            reject()
        host = (parts.hostname or "").rstrip(".").lower()
        if not host or "%" in host or parts.port is not None and not 1 <= parts.port <= 65535:
            reject()
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local")):
            reject()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Reject inet_aton-style numeric aliases, not just 127.0.0.1.
            if all(re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)", label) for label in host.split(".")):
                reject()
            ascii_host = host.encode("idna").decode("ascii")
            if len(ascii_host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                           for label in ascii_host.split(".")):
                reject()
        else:
            if not address.is_global:
                reject()
    except (ValueError, UnicodeError):
        reject()


def validate_key(key: str) -> None:
    if not key or any(ch in key for ch in "\r\n\x00"):
        raise BlobError("webhook key rejected")


def pack_blob(url: str, key: str) -> bytes:
    url = normalize_secret(url)
    key = normalize_secret(key)
    validate_url(url)
    validate_key(key)
    url_bytes = url.encode("utf-8")
    key_bytes = key.encode("utf-8")
    if len(url_bytes) > MAX_FIELD or len(key_bytes) > MAX_FIELD:
        raise BlobError("credential field too long")
    return MAGIC + struct.pack("<I", len(url_bytes)) + url_bytes + struct.pack("<I", len(key_bytes)) + key_bytes


def unpack_blob(blob: bytes) -> tuple[str, str]:
    if len(blob) < 12 or not blob.startswith(MAGIC):
        raise BlobError("invalid credential blob")
    offset = 4
    try:
        (url_len,) = struct.unpack_from("<I", blob, offset)
    except struct.error as exc:
        raise BlobError("invalid credential blob") from exc
    offset += 4
    if url_len > MAX_FIELD or offset + url_len + 4 > len(blob):
        raise BlobError("invalid credential blob")
    try:
        url = blob[offset : offset + url_len].decode("utf-8")
    except UnicodeError:
        raise BlobError("invalid credential blob") from None
    offset += url_len
    (key_len,) = struct.unpack_from("<I", blob, offset)
    offset += 4
    if key_len > MAX_FIELD or offset + key_len != len(blob):
        raise BlobError("invalid credential blob")
    try:
        key = blob[offset : offset + key_len].decode("utf-8")
    except UnicodeError:
        raise BlobError("invalid credential blob") from None
    validate_url(url)
    validate_key(key)
    return url, key
