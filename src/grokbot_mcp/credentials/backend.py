"""Credential backend selection.

The default backend is Windows Credential Manager. Tests may install an
in-memory backend. The MCP tools have no parameter that selects a backend.
"""

from __future__ import annotations

from collections.abc import Callable

from grokbot_mcp.credentials.windows import credential_status, load_credential

StatusFn = Callable[[str, tuple[str, ...]], str]
LoadFn = Callable[[str, tuple[str, ...]], tuple[str, str]]

_status: StatusFn | None = None
_load: LoadFn | None = None


def install_backend(status: StatusFn | None, load: LoadFn | None) -> None:
    global _status, _load
    _status = status
    _load = load


def status_for(target: str, others: tuple[str, ...]) -> str:
    if _status is not None:
        return _status(target, others)
    return credential_status(target, others=others)


def load_for(target: str, others: tuple[str, ...]) -> tuple[str, str]:
    if _load is not None:
        return _load(target, others)
    return load_credential(target, others=others)
