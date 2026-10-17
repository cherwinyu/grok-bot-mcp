"""Read-only renderer for the supported Codex CLI stdio MCP configuration.

No existing Codex config is read or written; no client/credential/Webhook runs.
"""
from __future__ import annotations

import json
import tomllib

from grokbot_mcp.local_safety import LocalError


def _quote(value: object) -> str:
    if (type(value) is not str or not value or len(value) > 4096
            or any(ord(ch) < 32 or ord(ch) == 127
                   or 0xD800 <= ord(ch) <= 0xDFFF for ch in value)):
        raise LocalError("invalid_arguments")
    return json.dumps(value, ensure_ascii=False)


def fragment_toml(server: str, entry: dict) -> str:
    """Translate only bridge-owned, known stdio fields; fail closed on extras."""
    label = _quote(server)
    if type(entry) is not dict or set(entry) != {"command", "args", "env"}:
        raise LocalError("invalid_arguments")
    command = _quote(entry["command"])
    args = entry["args"]
    env = entry["env"]
    if (type(args) is not list or not args or
            type(env) is not dict or set(env) != {"GROKBOT_MCP_REGISTRY"}):
        raise LocalError("invalid_arguments")
    values = [_quote(part) for part in args]
    registry = _quote(env["GROKBOT_MCP_REGISTRY"])
    snippet = "\n".join([
        f"[mcp_servers.{label}]",
        f"command = {command}",
        f"args = [{', '.join(values)}]",
        f"env = {{ GROKBOT_MCP_REGISTRY = {registry} }}",
        "",
    ])
    try:
        parsed = tomllib.loads(snippet)
        if parsed != {"mcp_servers": {server: entry}}:
            raise LocalError("invalid_arguments")
    except (tomllib.TOMLDecodeError, ValueError):
        raise LocalError("invalid_arguments") from None
    return snippet
