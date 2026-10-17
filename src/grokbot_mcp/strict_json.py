"""Bounded JSON shared by evidence, registry and local maintenance readers.

Incomplete UTF-8/JSON retain their standard exceptions for bounded observation;
ambiguous or excessive data use fixed codes and are never treated as progress.
"""
from __future__ import annotations
import json
import math

MAX_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 64


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("json_duplicate_field")
        result[key] = value
    return result


def _constant(_):
    raise ValueError("json_nonfinite")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("json_nonfinite")
    return result


def decode(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise ValueError("json_too_large")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("encoding_rejected")
    text = raw.decode("utf-8")
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError("json_depth_exceeded")
        elif char in "]}":
            depth -= 1
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    except RecursionError:
        raise ValueError("json_depth_exceeded") from None
    if type(value) is not dict:
        raise ValueError("schema_invalid")
    return value
