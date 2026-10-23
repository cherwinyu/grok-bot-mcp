"""Bounded inspection of known local packets; no remote text or credentials."""
from __future__ import annotations

import re
import json
from pathlib import Path

from grokbot_mcp.local_safety import LocalError, safe_path, read_document
from grokbot_mcp.materials import render_routine
from grokbot_mcp.registry import load_registry, policy_fingerprint, routine_profile
from grokbot_mcp.routine import routine_readiness, MODES
from grokbot_mcp.setup_environment import interpreter
from grokbot_mcp.strict_json import decode, MAX_BYTES
from grokbot_mcp.security.paths import is_uuid

SCOPE = "Cannot verify remote Routine text; only local packets and registration. / 无法验证远端 Routine 文本，只能验证本地包与登记值。"


def packets(registry: Path, workspace) -> list[tuple[Path, str]]:
    found = [(registry.parent / "setup" / workspace.workspace_id / "routine-setup.md", "setup")]
    marker = workspace.root / ".bridge/deployment_migration.json"
    safe_path(marker)
    if marker.exists():
        data, _ = read_document(marker)
        identity = data.get("transaction_id")
        if type(identity) is not str or not is_uuid(identity):
            raise LocalError("migration_state_invalid")
        found.append((registry.parent / "migrations" / workspace.workspace_id / identity / "routine-update.md", "migration"))
    return found


def inspect_text(text: str, workspace, registry: Path, python: Path, kind: str) -> list[str]:
    """Compare data and rendered templates; never execute Markdown instructions."""
    errors = []
    if re.search(r"<ADMIN_[^>\r\n]*>|\{\{[^}\r\n]+\}\}|<(?:WORKSPACE|REGISTRY|PYTHON|POLICY|TEST_COMMAND)[A-Z_]*>", text, re.I):
        errors.append("routine_placeholders_remaining")
    for value in re.findall(r"(?m)^& '((?:[^']|'')*)' -I -X utf8 -m grokbot_mcp\.", text):
        if value.replace("''", "'") != str(python):
            errors.append("routine_interpreter_mismatch")
    for value in re.findall(r"(?m)^\$env:GROKBOT_MCP_REGISTRY = '((?:[^']|'')*)'", text):
        if value.replace("''", "'") != str(registry):
            errors.append("routine_binding_mismatch")
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.S)
    try:
        profile, binding = [decode(block.encode("utf-8")) for block in blocks[:2]]
    except (ValueError, UnicodeError):
        return sorted(set(errors + ["routine_packet_invalid"]))
    expected = routine_profile(workspace)
    expected["root"] = str(workspace.root)
    for key in ("validate_credential_target", "execute_credential_target"):
        expected.pop(key)
    if workspace.git_gate == "strict":
        expected.pop("git_gate")
    expected = json.loads(json.dumps(expected))
    if any(profile.get(key) != value for key, value in expected.items()):
        errors.append("routine_profile_mismatch")
    if workspace.allowed_prefixes:
        if (profile.get("allowed_prefixes") != expected["allowed_prefixes"]
                or profile.get("policy_fingerprint") != policy_fingerprint(workspace)
                or "allowed_files" in profile):
            errors.append("routine_prefix_policy_mismatch")
    elif profile.get("allowed_prefixes"):
        errors.append("routine_prefix_policy_mismatch")
    # Explicit defaults must not conceal a stale non-default packet value.
    for key, value in (("git_gate", workspace.git_gate), ("allow_globs", workspace.allow_globs),
                       ("glob_max_files", workspace.glob_max_files), ("test_selection", workspace.test_selection),
                       ("require_test_output", workspace.require_test_output), ("other_prefixes", list(workspace.other_prefixes))):
        if key in profile and profile[key] != value:
            errors.append("routine_profile_mismatch")
    if kind == "setup":
        modes = []
        for phase in ("validate", "execute"):
            item = binding.get(phase, {})
            if not isinstance(item, dict):
                errors.append("routine_binding_mismatch")
                continue
            modes.append(item.get("mode"))
            required = {"workspace_id": workspace.workspace_id, "workspace_root": str(workspace.root.resolve()),
                        "policy_fingerprint": policy_fingerprint(workspace), "registry_path": str(registry.resolve()),
                        "action": phase + "_manifest"}
            if any(item.get(key) != value for key, value in required.items()):
                errors.append("routine_binding_mismatch")
        mode = modes[0] if len(modes) == 2 and modes[0] == modes[1] and modes[0] in MODES else None
    else:
        mode = "migrate"
        if (binding != {"python": str(python), "registry": str(registry)}
                or profile.get("policy_fingerprint") != policy_fingerprint(workspace)):
            errors.append("routine_binding_mismatch")
    if mode is None:
        errors.append("routine_binding_mismatch")
    else:
        for phase in ("validate", "execute"):
            expected_text = render_routine(phase, workspace_root=workspace.root,
                workspace_id=workspace.workspace_id, registry=registry, python=python, mode=mode, test_options=expected)
            if expected_text.strip() not in text:
                errors.append("routine_template_mismatch")
    return sorted(set(errors))


def check(registry: Path, workspace_id: str) -> dict:
    registry = registry.absolute()
    workspace = load_registry(registry).get(workspace_id)
    if workspace is None:
        raise LocalError("unknown_workspace")
    issues, phases = [], {}
    interpreters = set()
    for phase in ("validate", "execute"):
        state = routine_readiness(workspace, phase, registry)
        binding = state.get("binding") or {}
        if isinstance(binding, dict) and isinstance(binding.get("claim_interpreter"), str):
            interpreters.add(binding["claim_interpreter"])
        phases[phase] = {"binding_current": state["binding_confirmed"],
                         "verified_at": state.get("binding_verified_at"),
                         "routine_enabled": state["routine_enabled"]}
        if not state["binding_confirmed"]:
            issues.append("routine_attestation_stale")
    if len(interpreters) > 1:
        issues.append("routine_interpreter_mismatch")
    python = Path(next(iter(interpreters))) if len(interpreters) == 1 else Path(interpreter())
    checked = []
    for path, kind in packets(registry, workspace):
        safe_path(path)
        if not path.exists():
            if kind == "migration":
                issues.append("routine_packet_missing")
            continue
        safe_path(path, regular=True)
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise LocalError("ledger_too_large")
        try:
            codes = inspect_text(raw.decode("utf-8"), workspace, registry, python, kind)
        except UnicodeError:
            codes = ["routine_packet_invalid"]
        issues.extend(codes)
        checked.append({"kind": kind, "ok": not codes, "issues": codes})
    if not checked:
        issues.append("routine_packet_missing")
    return {"ok": not issues, "scope": SCOPE, "remote_binding_verified": False,
            "policy_fingerprint": policy_fingerprint(workspace), "phases": phases,
            "packets": checked, "issues": sorted(set(issues)), "webhook_sent": False,
            "credential_values_read": False, "send_authorized": False}
