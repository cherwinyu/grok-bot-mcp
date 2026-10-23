"""Native Windows, installed-wheel, fictional-client deployment acceptance.

Two real isolated Python installations; no real client, remote Routine or Webhook.
The only submitted tasks use explicitly installed in-memory credential/HTTP fixtures.
"""
from __future__ import annotations
import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path

import platform
import shutil
import subprocess
import sys
import tempfile
import venv


def run(command, *, cwd=None, expected=0, env=None, timeout=90):
    result = subprocess.run([str(x) for x in command], cwd=cwd, env=env, capture_output=True,
                            text=True, encoding="utf-8", timeout=timeout, shell=False, errors="replace")
    if result.returncode != expected:
        # Evidence is private CI output, not the public diagnostic API. No credentials
        # are printed by tested CLIs; all config and paths belong to temporary fixtures.
        raise AssertionError(f"command exit {result.returncode}, expected {expected}\n{result.stdout}\n{result.stderr}")
    return result


def cli(python, module, *args, expected=0):
    result = run([python, "-I", "-X", "utf8", "-m", "grokbot_mcp."+module, *args], expected=expected)
    return json.loads(result.stdout)


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and p.name != ".submit.lock"}


def acceptance_candidate(phase, identity):
    """The only --source result_cli accepts: the private temp candidate."""
    from grokbot_mcp.result_cli import private_candidate_path
    source = private_candidate_path(phase, identity)
    source.parent.mkdir(parents=True, exist_ok=True)
    return source


@asynccontextmanager
async def connect(config):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["grokbot-mcp"]
    async with stdio_client(StdioServerParameters(command=entry["command"], args=entry["args"],
                                                 env={**os.environ, **entry["env"]})) as streams:
        async with ClientSession(*streams) as session:
            initialized = await session.initialize()
            yield session, initialized


async def observe(session):
    from tests.support import WORKSPACE_ID
    result = await session.call_tool("grok_preflight", {"workspace_id": WORKSPACE_ID})
    return json.loads("".join(part.text for part in result.content if hasattr(part, "text")))


async def client_scenario(config, python_b, registry, records):
    from grokbot_mcp import __version__, client_config
    from grokbot_mcp.identity import compare_identity
    from grokbot_mcp.startup_probe import TOOL_NAMES
    from tests.support import WORKSPACE_ID
    old_bytes = config.read_bytes()
    expected_a = client_config.inspect_config(config, "grokbot-mcp", WORKSPACE_ID)["expected_identity"]
    before = snapshot(config.parent)
    plan = client_config.switch_plan(config, "grokbot-mcp", python_b, registry, WORKSPACE_ID, local_details=True)[0]
    assert snapshot(config.parent) == before
    assert "local_path_diff" in plan
    async with connect(config) as (session_a, init_a):
        assert init_a.server_info.version == __version__
        assert sorted(t.name for t in (await session_a.list_tools()).tools) == TOOL_NAMES
        old = (await observe(session_a))["runtime_identity"]
        assert compare_identity(expected_a, old)["identity_matches"]
        switched = client_config.switch(config, "grokbot-mcp", python_b, registry, WORKSPACE_ID, confirm=plan["plan_sha256"])
        assert switched["configuration_updated"] and not switched["client_reconnection_confirmed"]
        still_old = (await observe(session_a))["runtime_identity"]
        assert still_old["process_instance_id"] == old["process_instance_id"]
        assert not compare_identity(plan["expected_identity"], still_old)["identity_matches"]
        records.append("live fictional old connection detected after config update")
    async with connect(config) as (session_b, init_b):
        observed_b = (await observe(session_b))["runtime_identity"]
        assert init_b.server_info.version == __version__
        assert compare_identity(plan["expected_identity"], observed_b)["identity_matches"]
        assert old["installation_id"] != observed_b["installation_id"]
        assert json.loads(config.read_text(encoding="utf-8"))["unknown"] == {"keep": "未知"}
        records.append("fictional harness reload uses distinct installation B, same version")
        rollback = client_config.transaction_plan(config, switched["transaction_id"], rollback=True)
        client_config.recover(config, switched["transaction_id"], rollback=True, confirm=rollback["plan_sha256"])
        assert config.read_bytes() == old_bytes
        still_b = (await observe(session_b))["runtime_identity"]
        assert not compare_identity(expected_a, still_b)["identity_matches"]
    async with connect(config) as (session_a2, _):
        new_a = (await observe(session_a2))["runtime_identity"]
        assert compare_identity(expected_a, new_a)["identity_matches"]
        assert new_a["process_instance_id"] != old["process_instance_id"]
        assert new_a["installation_id"] == old["installation_id"]
        records.append("exact config rollback and second fictional reconnect to A")
    another_registry = registry.parent / "第二份 登记.json"
    another_registry.write_bytes(registry.read_bytes())
    another_config = config.parent / "other registry.json"
    data = json.loads(old_bytes)
    data["mcpServers"]["grokbot-mcp"]["env"]["GROKBOT_MCP_REGISTRY"] = str(another_registry)
    another_config.write_text(json.dumps(data), encoding="utf-8")
    async with connect(another_config) as (session, _):
        other = (await observe(session))["runtime_identity"]
        assert other["registry_id"] != old["registry_id"]
        assert other["registry_sha256"] == old["registry_sha256"]
        assert not compare_identity(expected_a, other)["identity_matches"]
        records.append("identical registry bytes at distinct locations distinguishable over real stdio")


def worker(args):
    # The copied QA directory supplies fictional fixtures, never package source.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import grokbot_mcp
    import importlib.metadata
    from grokbot_mcp import client_config, migration, facade, setup_wizard
    from grokbot_mcp.registry import load_registry
    from grokbot_mcp.routine import routine_readiness
    from grokbot_mcp.local_safety import LocalError, current_hash
    from grokbot_mcp.deployment_diagnostics import public_report
    from tests.support import make_workspace, WORKSPACE_ID, proof, execution_result, MockTransport
    assert os.name == "nt"
    assert Path(grokbot_mcp.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    records = []
    base = Path(args.fixture)
    base.mkdir()
    workspace_root = make_workspace(base)
    registry = base / "registry.json"
    python_a, python_b = Path(sys.executable), Path(args.python_b)
    registry_bytes = registry.read_bytes()
    switch_bytes = (base / "execution_switch.json").read_bytes()
    config = base / "客户端 配置.json"
    value = {"unknown": {"keep": "未知"}, "mcpServers": {
        "other": {"command": "DO_NOT_EXECUTE", "unknown": True},
        "grokbot-mcp": {**client_config.fragment(python_a, registry, WORKSPACE_ID),
                        "future": {"keep": [1, 2, 3]}}}}
    value["mcpServers"]["grokbot-mcp"]["env"]["FICTIONAL_EXTRA"] = "keep-value"
    config.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    before = snapshot(workspace_root)
    checked = cli(python_a, "client_cli", "inspect", "--config", config, "--workspace", WORKSPACE_ID)
    assert checked["ok"] and not checked["imports_verified"]
    assert snapshot(workspace_root) == before
    records.append("static CLI before server startup: no business-state mutation")
    asyncio.run(client_scenario(config, python_b, registry, records))
    probed = cli(python_a, "client_cli", "inspect", "--config", config, "--workspace", WORKSPACE_ID, "--probe")
    assert probed["standalone_handshake_passed"] and probed["imports_verified"]
    assert not probed["client_reconnection_confirmed"] and not probed["credential_values_read"]
    records.append("separate bounded CLI stdio probe, identity and seven-tool check")
    share = base / "shareable.json"
    exported = cli(python_a, "client_cli", "inspect", "--config", config, "--workspace", WORKSPACE_ID,
                   "--registry", registry, "--export", share)
    assert json.loads(share.read_text(encoding="utf-8")) == exported
    assert str(base).replace("\\", "\\\\") not in share.read_text(encoding="utf-8")
    assert checked["expected_identity"]["installation_id"] not in share.read_text(encoding="utf-8")
    records.append("separately reconstructed shareable export removes paths and correlation IDs")
    generated = run([python_a, "-I", "-X", "utf8", "-m", "grokbot_mcp.setup_wizard", "--registry", registry,
                     "--workspace", WORKSPACE_ID, "--generate-only", "--routine-mode", "reuse"])
    # Use the established generator's returned directory rather than infer a layout.
    packet = setup_wizard.generate(registry, WORKSPACE_ID, "reuse") / "routine-setup.md"
    assert "grokbot_mcp.result_cli" in packet.read_text(encoding="utf-8")
    assert registry.read_bytes() == registry_bytes
    records.append("installed wizard packet recovery reuses existing Routine; no registry rewrite")
    common = ["--registry", registry, "--workspace", WORKSPACE_ID]
    mplan = cli(python_a, "migration_cli", "plan", *common, "--python", python_b)
    applied = cli(python_a, "migration_cli", "apply", *common, "--python", python_b, "--apply", "--confirm", mplan["plan_sha256"])
    txn = applied["transaction_id"]
    assert not applied["remote_binding_verified"]
    assert not routine_readiness(load_registry(registry)[WORKSPACE_ID], "execute", registry)["submit_ready"]
    fplan = cli(python_a, "migration_cli", "finalize", *common, "--transaction", txn)
    blocked = cli(python_a, "migration_cli", "finalize", *common, "--transaction", txn,
                  "--apply", "--confirm", fplan["plan_sha256"], expected=1)
    assert blocked["diagnostic"]["code"] == "migration_remote_review_required"
    finalized = cli(python_a, "migration_cli", "finalize", *common, "--transaction", txn, "--apply",
                    "--confirm", fplan["plan_sha256"], "--remote-reviewed")
    assert finalized["administrator_remote_review_confirmed"] and not finalized["remote_binding_verified"]
    assert cli(python_a, "migration_cli", "plan", *common, "--python", python_b)["no_change"]
    rplan = cli(python_a, "migration_cli", "rollback", *common, "--transaction", txn)
    assert cli(python_a, "migration_cli", "rollback", *common, "--transaction", txn, "--apply",
               "--confirm", rplan["plan_sha256"])["state"] == "rolled_back"
    assert registry.read_bytes() == registry_bytes
    assert (base / "execution_switch.json").read_bytes() == switch_bytes
    records.append("native migration CLI plan/apply/manual-fixture-attestation/idempotence/rollback")
    agent_dir = base / "可选 agent"
    aplan = cli(python_a, "agent_cli", "generate", "--directory", agent_dir)
    assert not agent_dir.exists()
    cli(python_a, "agent_cli", "generate", "--directory", agent_dir, "--apply", "--confirm", aplan["plan_sha256"])
    (agent_dir / "dispatcher.md").unlink()  # Only this explicitly generated fictional prompt.
    missing = cli(python_a, "agent_cli", "check", "--config", agent_dir / "agent.json", expected=1)
    assert missing["diagnostic"]["code"] == "agent_prompt_missing"
    aplan = cli(python_a, "agent_cli", "generate", "--directory", agent_dir)
    cli(python_a, "agent_cli", "generate", "--directory", agent_dir, "--apply", "--confirm", aplan["plan_sha256"])
    assert cli(python_a, "agent_cli", "check", "--config", agent_dir / "agent.json")["ok"]
    records.append("optional versioned agent missing-prompt recovery, no execution")
    # Real Windows junction rejection, not an API substitute.
    junction = base / "linked config"
    destination = base / "junction target"
    destination.mkdir()
    (destination / "client.json").write_bytes(config.read_bytes())
    run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "mklink", "/J", junction, destination])
    linked = cli(python_a, "client_cli", "inspect", "--config", junction / "client.json", "--workspace", WORKSPACE_ID, expected=1)
    assert linked["diagnostic"]["code"] == "linked_path_rejected"
    junction.rmdir()  # Remove only the fictional link; preserve its target.
    records.append("native Windows junction refusal")
    # Explicitly enable ONLY the isolated fixture switch to exercise MockTransport.
    (base / "execution_switch.json").write_text('{"enabled":true}', encoding="utf-8")
    transport = MockTransport()
    facade.install_transport(transport)
    task = facade.prepare_task(WORKSPACE_ID, "code_change", "Fictional measured counter test", ["src/counter.py"],
                               ["Keep tests passing"], "python_unittest")
    assert facade.submit_validation_tool(WORKSPACE_ID, task["task_id"])["accepted"]
    vdata = proof(task["task_id"], task["manifest_sha256"], ["src/counter.py"])
    def publish_result(phase, identity, data):
        source = acceptance_candidate(phase, identity)
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        source.write_bytes(raw)
        fields = [*common, "--phase", phase, "--id", identity]
        ledger = "tasks_v2" if phase == "validate" else "executions"
        before_ledger = snapshot(workspace_root / ".bridge" / ledger)
        checked = cli(python_a, "result_cli", *fields, "--source", source)
        staged = cli(python_a, "result_cli", *fields, "--source", source, "--stage", "--confirm-sha256", checked["staged_sha256"])
        stage = workspace_root / staged["staging_path"]
        checked2 = cli(python_a, "result_cli", *fields, "--staged", stage)
        output = cli(python_a, "result_cli", *fields, "--staged", stage, "--publish", "--confirm-sha256", checked2["staged_sha256"])
        assert output["published"] and not output["bridge_accepted"] and not output["qa_accepted"]
        assert snapshot(workspace_root / ".bridge" / ledger) == before_ledger
        return fields, stage, raw
    publish_result("validate", task["task_id"], vdata)
    assert facade.get_status(WORKSPACE_ID, task_id=task["task_id"])["validation"]["status"] == "succeeded"
    execution = facade.prepare_execution_tool(WORKSPACE_ID, task["task_id"])
    assert facade.submit_execution_tool(WORKSPACE_ID, execution["execution_id"])["accepted"]
    claimed = cli(python_a, "claim_cli", "--workspace", WORKSPACE_ID, execution["execution_id"])
    assert claimed["ok"]
    with (workspace_root / "src/counter.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# fictional native acceptance\n")
    runner = ("import unittest,json,io,sys; "
              "r=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.defaultTestLoader.discover('tests')); "
              "print(json.dumps({'case_count':r.testsRun,'failed':len(r.failures)+len(r.errors),"
              "'passed':r.testsRun-len(r.failures)-len(r.errors),'exit_code':0 if r.wasSuccessful() else 1})); "
              "sys.exit(not r.wasSuccessful())")
    measured = json.loads(run([python_a, "-B", "-X", "utf8", "-c", runner], cwd=workspace_root).stdout)
    assert measured["case_count"] == 1 and measured["passed"] == 1
    edata = execution_result(execution["execution_id"], task["task_id"], task["manifest_sha256"], ["src/counter.py"])
    edata["tests"].update(measured, unit="test_cases")
    bad_source = acceptance_candidate("execute", execution["execution_id"])
    bad = json.loads(json.dumps(edata))
    bad["tests"]["passed"] = True
    bad_source.write_bytes(json.dumps(bad, ensure_ascii=False).encode("utf-8"))
    refused = cli(python_a, "result_cli", *common, "--phase", "execute", "--id", execution["execution_id"], "--source", bad_source, expected=1)
    assert refused["diagnostic"]["category"] == "format_error"
    fields, stage, raw = publish_result("execute", execution["execution_id"], edata)
    assert facade.get_status(WORKSPACE_ID, execution_id=execution["execution_id"])["execution"]["status"] == "succeeded"
    published = workspace_root / ".bridge/results_execute" / (execution["execution_id"]+".json")
    frozen = current_hash(published)
    # Terminal publication removes the staged file. Rewriting it must still be refused.
    stage.write_bytes(raw)
    refused = cli(python_a, "result_cli", *fields, "--staged", stage, expected=1)
    assert refused["diagnostic"]["code"] == "result_terminal_frozen"
    assert frozen == current_hash(published)
    assert len(transport.calls) == 2
    records.append("measured fictional test -> actual claim CLI -> stage/precheck/publish -> frozen terminal refusal")
    records.append("all submissions were in-memory MockTransport, zero external Webhooks")
    result = {"native_windows": True, "version": grokbot_mcp.__version__, "python": platform.python_version(),
              "platform": platform.platform(), "mcp": importlib.metadata.version("mcp"), "exit_code": 0,
              "installed_outside_source": True, "real_isolated_installations": 2, "fictional_client_reconnected": True,
              "actual_user_client_reconnected": False, "remote_routine_verified": False, "real_webhooks_sent": 0,
              "credential_values_read": False, "real_environment_changed": False, "qa_accepted": False,
              "scenario_steps": records, "measured_fictional_test": measured}
    Path(args.worker_output).write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--python-b")
    parser.add_argument("--fixture")
    parser.add_argument("--worker-output")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("native Windows required; no substituted acceptance")
    if args.worker:
        return worker(args)
    # The isolated worker copy has no source tree or version helper.
    if __package__:
        from .source_version import source_version
    else:
        try:
            from source_version import source_version
        except ModuleNotFoundError:
            from scripts.source_version import source_version
    root = Path(__file__).resolve().parents[1]
    wheelhouse = args.wheelhouse.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    version = source_version(root)
    wheel = next(wheelhouse.glob("grokbot_mcp_bridge-"+version+"-*.whl"))
    with tempfile.TemporaryDirectory(prefix="bridge-native-") as name:
        base = Path(name)
        interpreters = []
        logs = []
        for label in ("安装 A", "安装 B"):
            target = base / label
            venv.EnvBuilder(with_pip=True).create(target)
            python = target / "Scripts/python.exe"
            interpreters.append(python)
            installed = run([python, "-m", "pip", "install", "--no-index", "--find-links", wheelhouse, wheel], timeout=180)
            logs.append(installed.stdout+installed.stderr)
        qa = base / "QA outside 源码"
        (qa / "scripts").mkdir(parents=True)
        shutil.copytree(root / "tests", qa / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copyfile(__file__, qa / "scripts/native_deployment_acceptance.py")
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "GROKBOT_MCP_REGISTRY"}}
        env.pop("PYTHONUTF8", None)
        env.update(PYTHONIOENCODING="utf-8")
        result = subprocess.run([str(interpreters[0]), "-I", "-X", "utf8", str(qa / "scripts/native_deployment_acceptance.py"),
                                 "--worker", "--python-b", str(interpreters[1]), "--fixture", str(base / "虚构 集成"),
                                 "--worker-output", str(output / "deployment-native-summary.json")],
                                cwd=qa, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600, errors="replace")
        logs.append(result.stdout+result.stderr)
        (output / "deployment-native.log").write_text("\n".join(logs), encoding="utf-8")
        print(result.stdout+result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
