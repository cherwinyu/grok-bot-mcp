"""Explicit bounded standalone stdio probe. Never evidence of client UI reconnection."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from grokbot_mcp.client_config import _launch, inspect_config, read_config
from grokbot_mcp.identity import compare_identity, digest
from grokbot_mcp.local_cli import Parser, emit, failure
from grokbot_mcp.local_safety import LocalError, parse_json

TOOL_NAMES = sorted(("grok_list_workspaces", "grok_preflight", "grok_prepare_task", "grok_submit_validation",
                     "grok_prepare_execution", "grok_submit_execution", "grok_get_status"))


async def _handshake(python: str, environment: dict[str, str], workspace: str) -> dict:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command=python,
        args=["-I", "-X", "utf8", "-m", "grokbot_mcp.server"],
        env={**os.environ, **environment})
    async with asyncio.timeout(15):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                if sorted(x.name for x in listed.tools) != TOOL_NAMES:
                    raise LocalError("stdio_tools_mismatch")
                result = await session.call_tool("grok_preflight", {"workspace_id": workspace})
                text = "".join(getattr(x, "text", "") for x in result.content)
                data = parse_json(text.encode("utf-8"))
                identity = data.get("runtime_identity")
                if not isinstance(identity, dict):
                    raise LocalError("runtime_identity_missing")
                return {"server_version": initialized.server_info.version, "runtime_identity": identity,
                        "tool_names": TOOL_NAMES, "standalone_handshake_passed": True,
                        "client_reconnection_confirmed": False, "credentials_loaded": False,
                        "credentials_checked": True, "credential_presence_checked": True, "credential_values_read": False,
                        "webhook_sent": False, "qa_accepted": False}


def probe_config(config: Path, name: str, workspace: str) -> dict:
    checked = inspect_config(config, name, workspace)
    if "candidate_version_mismatch" in checked["codes"]:
        raise LocalError("candidate_version_mismatch")
    _, config_raw, entry = read_config(config, name)
    _launch(entry)
    # Run our own fixed probe module, not the arbitrary script named by a client config.
    # File-backed stdout prevents inherited pipe handles from hanging communicate().
    with tempfile.TemporaryDirectory(prefix="grokbot-probe-") as tmp:
        output = Path(tmp) / "probe.json"
        with output.open("wb") as handle:
            proc = subprocess.Popen([sys.executable, "-I", "-X", "utf8", "-m", "grokbot_mcp.startup_probe",
                                     "--worker", "--config", str(config), "--server", name,
                                     "--config-sha256", digest(config_raw), "--workspace", workspace],
                                    stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.DEVNULL,
                                    shell=False, close_fds=True)
            try:
                proc.wait(timeout=25)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    try:
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=5, shell=False, check=False)
                    except (OSError, subprocess.SubprocessError):
                        pass
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                raise LocalError("stdio_startup_timeout") from None
        if proc.returncode != 0:
            raise LocalError("stdio_startup_failed")
        result = parse_json(output.read_bytes())
    compared = compare_identity(checked["expected_identity"], result["runtime_identity"])
    if result["server_version"] != checked["expected_identity"]["bridge_version"] or not compared["identity_matches"]:
        raise LocalError("stdio_identity_mismatch")
    return {**checked, **result, "imports_verified": True, "identity_comparison": compared,
            "scope": "independently spawned server only; reload and query the actual client separately"}


def main(argv=None):
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--worker", action="store_true", required=True)
        parser.add_argument("--config", type=Path, required=True)
        parser.add_argument("--server", required=True)
        parser.add_argument("--config-sha256", required=True)
        parser.add_argument("--workspace", required=True)
        args = parser.parse_args(argv)
        # Worker is internal but still refuses unregistered/unsupported launch inputs.
        from grokbot_mcp.client_config import inspect_installation
        _, raw, entry = read_config(args.config, args.server)
        if digest(raw) != args.config_sha256:
            raise LocalError("input_changed")
        python, _ = _launch(entry)
        inspect_installation(python)
        emit(asyncio.run(_handshake(str(python), entry["env"], args.workspace)))
        return 0
    except Exception as exc:
        emit(failure(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
