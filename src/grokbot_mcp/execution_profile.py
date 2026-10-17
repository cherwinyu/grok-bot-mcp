"""Declarative execution boundary, NOT remote reachability or OS attestation.

Omission preserves legacy behavior. Explicit unsupported settings fail closed.
Profiles form part of policy_fingerprint: a change invalidates old bindings/grants.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict

PROTOCOL = "grok_bot_routine_bearer_v1"
FIELDS = {"schema_version", "protocol", "topology", "mode", "desktop_approval"}


@dataclass(frozen=True)
class ExecutionProfile:
    schema_version: int = 1
    protocol: str = PROTOCOL
    topology: str = "windows_local"
    mode: str = "advisory_write"
    desktop_approval: str = "per_command"


def parse(value: object) -> ExecutionProfile:
    if type(value) is not dict or set(value) != FIELDS:
        raise ValueError("execution_profile_invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("execution_profile_invalid")
    if not all(type(value[name]) is str for name in FIELDS - {"schema_version"}):
        raise ValueError("execution_profile_invalid")
    # Unsupported providers/modes are not silently converted to a supported one.
    if value["protocol"] != PROTOCOL:
        raise ValueError("webhook_protocol_unsupported")
    if value["topology"] != "windows_local":
        raise ValueError("execution_topology_unsupported")
    if value["mode"] not in {"read_only", "advisory_write"}:
        raise ValueError("execution_isolation_not_implemented")
    if value["desktop_approval"] != "per_command":
        raise ValueError("desktop_approval_unsupported")
    return ExecutionProfile(**value)


def recommended() -> dict:
    return asdict(ExecutionProfile())


def block(workspace, phase: str) -> str | None:
    profile = workspace.execution_profile
    if profile and profile.mode == "read_only" and phase == "execute":
        return "execution_profile_read_only"
    return None


def report(workspace) -> dict:
    profile = workspace.execution_profile
    return {
        "configured": profile is not None,
        "profile": asdict(profile) if profile else None,
        "legacy_behavior_preserved": profile is None,
        "scope_enforcement": "advisory_precheck", "os_sandbox_verified": False,
        "routine_location": "cloud", "workspace_location": "registered_windows_host",
        "shared_filesystem_assumed": False, "desktop_reachability": "NOT_VERIFIED",
        "desktop_permission": "NOT_VERIFIED", "remote_binding_verified": False,
        "human_approval_required_for_live_test": True, "automatic_resend": False,
        "source": "administrator-declared-policy; not a remote observation",
    }


# Extend the existing catalog rather than introduce a second error vocabulary.
from grokbot_mcp.p0_diagnostics import register as _register_diagnostics
_register_diagnostics()
