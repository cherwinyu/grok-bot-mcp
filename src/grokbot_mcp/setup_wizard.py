"""Interactive, local-only onboarding. Never creates or triggers remote routines."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path

from grokbot_mcp import init_credentials
from grokbot_mcp import diagnostics
from grokbot_mcp.execution_profile import recommended as recommended_profile
from grokbot_mcp.setup_environment import inspect_environment, interpreter
from grokbot_mcp.credentials.backend import status_for
from grokbot_mcp.registry import (
    MAX_TIMEOUT_SECONDS, RegistryError, _workspace, default_registry_path, execution_switch_enabled,
    load_registry, switch_path,
    routine_profile,
)
from grokbot_mcp.routine import MODES, confirmation, read_routine, routine_readiness
from grokbot_mcp.security.gitcheck import git_error, git_snapshot, workspace_git_rejection
from grokbot_mcp.security.paths import is_reparse_point
from grokbot_mcp.security.targets import TargetRejected, resolve_exact_files
from grokbot_mcp.storage import utc_now, read_json
from grokbot_mcp.strict_json import check_size


def safe_path(path: Path) -> None:
    if any(is_reparse_point(p) for p in (path, *path.parents)):
        raise ValueError("linked_path_rejected")


def write_local(path: Path, text: str) -> None:
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".setup-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def write_json(path: Path, value: dict) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    check_size(text.encode("utf-8"))
    write_local(path, text)


def _configure_console() -> None:
    """Keep the console encoding; escape only characters it cannot represent."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")


def yes(prompt: str) -> bool:
    _configure_console()
    return input(prompt + " [y/N]: ").strip().lower() == "y"


def register(registry: Path, workspace_id: str, *, mode: str = "create",
             git_gate: str = "advisory", other_prefixes: list[str] | None = None) -> None:
    """Add one workspace, preserving all previously registered entries."""
    _configure_console()
    safe_path(registry)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", workspace_id):
        raise ValueError("invalid_workspace_id")
    if git_gate not in {"strict", "advisory"}:
        raise ValueError("invalid_git_gate")
    if other_prefixes and git_gate != "advisory":
        raise ValueError("invalid_git_gate")
    before = registry.read_bytes() if registry.exists() else None
    existing = load_registry(registry) if registry.exists() else {}
    if workspace_id in existing:
        return
    root = Path(input("工作区绝对路径（已有 Git 提交）: ").strip().strip('"'))
    if not root.is_absolute():
        raise ValueError("absolute_root_required")
    safe_path(root)
    root = root.resolve()
    rejection = workspace_git_rejection(root)
    if rejection:
        raise ValueError(rejection)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True, encoding="utf-8", errors="replace").stdout.strip()
    print("逐行输入允许修改的文件（相对工作区路径），空行结束。不支持通配符。")
    allowed = []
    while value := input("文件: ").strip():
        allowed.append(value)
    protected = input("保护文件（已存在、不可修改的相对路径）: ").strip().replace("\\", "/")
    if git_gate == "advisory":
        from grokbot_mcp.security.paths import normalize_policy_path
        allowed = [normalize_policy_path(name) for name in allowed]
        protected = normalize_policy_path(protected)
    resolve_exact_files(root, [protected], (".git", ".bridge"))
    forbidden = [".git", ".bridge", protected]
    allowed = resolve_exact_files(root, allowed, tuple(forbidden))
    command = input("测试命令 ID（例如 python_unittest）: ").strip()
    instruction = input("该 ID 对应的测试运行说明（例如 python -m unittest discover -s tests）: ").strip()
    if not command or command == "none" or not instruction:
        raise ValueError("test_command_required")
    item = {
        "workspace_id": workspace_id, "root": str(root), "expected_head": head,
        "execution_profile": recommended_profile(),
        "allowed_files": allowed, "forbidden_files": forbidden, "protected_file": protected,
        "test_commands": [
            {"id": "none", "task_types": ["read_only"], "timeout_cap": 180, "default_timeout": 180},
            {"id": command, "task_types": ["code_change"], "timeout_cap": MAX_TIMEOUT_SECONDS, "default_timeout": MAX_TIMEOUT_SECONDS},
        ],
        "validate_credential_target": f"grokbot-mcp/{workspace_id}/validate",
        "execute_credential_target": f"grokbot-mcp/{workspace_id}/execute",
        "expected_machine": os.environ.get("COMPUTERNAME", ""), "writes_enabled": False,
        "setup_test_instructions": {"none": "Do not run tests.", command: instruction},
        "setup_routine_mode": mode,
    }
    if git_gate == "advisory":
        item["git_gate"] = "advisory"
    if other_prefixes:
        from grokbot_mcp.security.paths import normalize_policy_path
        item["other_prefixes"] = list(dict.fromkeys(normalize_policy_path(p, casefold=True) for p in other_prefixes))
    _workspace(item)
    if any(w.root.resolve() == root for w in existing.values()):
        raise ValueError("workspace_root_already_registered")
    print(json.dumps(item, ensure_ascii=False, indent=2))
    if not yes("保存以上登记配置？"):
        raise ValueError("registration_cancelled")
    item["writes_enabled"] = yes("允许此工作区准备任务并修改白名单文件？Webhook 总开关仍独立控制")
    # Refuse to overwrite a registry edited while the administrator answered prompts.
    raw = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else {"schema_version": 1, "workspaces": []}
    current = load_registry(registry) if registry.exists() else {}
    if current != existing or (registry.read_bytes() if registry.exists() else None) != before:
        raise ValueError("registry_changed_retry")
    raw["workspaces"].append(item)
    write_json(registry, raw)


def generate(registry: Path, workspace_id: str, mode: str = "reuse") -> Path:
    if mode not in MODES:
        raise ValueError("invalid_binding_mode")
    registry = registry.absolute()
    workspace = load_registry(registry)[workspace_id]
    safe_path(workspace.root)
    safe_path(registry)
    profile = routine_profile(workspace)
    if workspace.git_gate == "strict":
        profile.pop("git_gate")
    profile["root"] = str(workspace.root)
    profile.pop("validate_credential_target")
    profile.pop("execute_credential_target")
    raw = json.loads(registry.read_text(encoding="utf-8"))
    entry = next(w for w in raw["workspaces"] if w["workspace_id"] == workspace_id)
    instructions = entry.get("setup_test_instructions", {})
    from grokbot_mcp.routine import claim_command
    from grokbot_mcp.registry import policy_fingerprint
    claim = "& '" + interpreter().replace("'", "''") + "' -I -X utf8 -m " + claim_command(workspace_id, policy_fingerprint(workspace))
    guidance = {
        "reuse": "复用旧 Routine/Webhook，更新领取入口：reuse_scope=retain-routine-update-claim。"
                 "保留原 Routine、Webhook 和凭据，不创建重复项；先核对目录、工作区 ID、基线和白名单。"
                 "旧入口完全不变：不支持。若原来使用 claim_dynamic.py，必须先把执行 Routine 的领取步骤"
                 "更新为下方 grokbot_mcp.claim_cli，并核对已安装的是本候选包。"
                 "必须实际使用 schema-v2 claim，执行账本保存 claim_sha256；不能为旧 claim 补造哈希，"
                 "也不能取消新证据检查。未完成入口更新前不要确认绑定；其余旧模板仍未验证。",
        "create": "创建新 Routine：分别创建校验与执行两条长期使用的 Webhook Routine；按下方受控配置绑定。",
        "migrate": "迁移绑定：先停止旧 Routine 的新任务并确认没有未决任务，再由管理员核对新绑定。此向导不移动目录、不修改登记或 HEAD、不覆盖旧成果。",
    }
    text = (
        "# Grok Bridge Routine 安装说明\n\n"
        + "安装路径 / routine mode: " + mode + "\n\n" + guidance[mode] + "\n"
        + "下面的模板是核对材料，不是远端已配置或执行能力已验证的证明。"

        "不要立即测试或运行；不要把桌面控制失败解释成 Webhook 不可用。\n"
        "下面是本机管理员配置，仅用于本机；不能放到公开模板。Webhook 输入不能覆盖这些配置。"
        "必须能访问以下 Windows 本机目录；不能替换成云端目录。无法访问时停止。\n\n"
        "```json\n" + json.dumps(profile, ensure_ascii=False, indent=2) + "\n```\n\n"
        + "绑定必须记录工作区、策略指纹、领取入口、协议能力与管理员验证时间；凭据存在不代表绑定正确。\n\n"
        + "纯 Webhook 流程不要求桌面控制。远端探测需要另行授权，本向导不发送探测任务。\n\n"
        + "待核对契约（verified_at 仅在确认后填写）：\n```json\n"
        + json.dumps({phase: confirmation(workspace, phase, registry, mode=mode, revision=1,
                                  verified_at="not-confirmed", legacy=mode != "create")["binding"]
                      for phase in ("validate", "execute")}, ensure_ascii=False, indent=2) + "\n```\n\n"
        + "测试命令映射（缺少映射时询问管理员，不能猜测）：\n"
        + json.dumps(instructions, ensure_ascii=False, indent=2) + "\n\n"
        "领取前在 PowerShell 设置注册表位置：\n\n```powershell\n"
        + "$env:GROKBOT_MCP_REGISTRY = '" + str(registry).replace("'", "''") + "'\n"
        + claim + "\n```\n\n"
    )
    from grokbot_mcp.materials import render_routine
    for phase in ("validate", "execute"):
        text += f"## Routine: grok-bridge-{workspace_id}-{phase}\n\n" + render_routine(
            phase, workspace_root=workspace.root, workspace_id=workspace_id, registry=registry,
            python=Path(interpreter()), mode=mode, test_options=profile,
            expected_fingerprint=policy_fingerprint(workspace)) + "\n"
    text += "\n核对、创建或迁移完成后，告知我在哪里查看各自的 Webhook URL 和密钥。不要把密钥写入聊天或文件。由我回到本地向导填写，并确认是否已启用。\n"
    output = registry.parent / "setup" / workspace_id
    checklist = (
        "# Manual Grok Bot checklist / Grok Bot 人工操作清单\n\n"
        "1. Create the two Routines, or reuse the existing pair (do not duplicate them). / 新建或复用两条 Routine。\n"
        "2. Apply this packet's workspace, policy, test mapping and claim entry. / 核对目录、策略、测试说明和领取入口。\n"
        "3. For reuse, upgrade the execute entry to grokbot_mcp.claim_cli with claim-evidence-v2; unchanged claim_dynamic.py is unsupported.\n"
        "4. Enable each intended Routine in Grok Bot and find its own Webhook URL/key. Do not paste secrets into chat.\n"
        "5. Return once to this wizard for hidden credential input and explicit binding confirmation. No remote probe has run.\n\n"
    )
    text = checklist + text
    write_local(output / "routine-setup.md", text)
    write_json(output / "mcp-config.json", {"mcpServers": {"grokbot-mcp": {
        "command": interpreter(), "args": ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"],
        "env": {"GROKBOT_MCP_REGISTRY": str(registry)},
    }}})
    return output


def check(registry: Path, workspace_id: str) -> bool:
    _configure_console()
    report = diagnostics.collect(registry, workspace_id)
    print(diagnostics.render(report))
    return report["complete_for_purpose"]


def confirm_binding(registry: Path, workspace_id: str, phase: str, mode: str) -> None:
    workspace = load_registry(registry)[workspace_id]
    from grokbot_mcp.routine import migration_gate
    if migration_gate(workspace):
        raise ValueError("migration_pending")
    path = workspace.root / ".bridge" / (phase + "_routine_status.json")
    safe_path(path)
    old = read_routine(path).get("binding")
    previous = old if isinstance(old, dict) else {}
    revision = previous.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ValueError("binding_invalid")
    candidate = confirmation(workspace, phase, registry, mode=mode, revision=revision or 1,
                             verified_at=utc_now(), legacy=mode != "create")
    compared = {key: value for key, value in candidate["binding"].items() if key not in {"verified_at", "revision"}}
    changed = any(previous.get(key) != value for key, value in compared.items())
    if changed:
        from grokbot_mcp.migration import unresolved_tasks
        if unresolved_tasks(workspace):
            raise ValueError("binding_change_has_unresolved_tasks")
        candidate["binding"]["revision"] = revision + 1
    write_json(path, candidate)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's normal message can echo a secret supplied as an invalid argument.
        raise ValueError("invalid_arguments")


def allow_writes(registry: Path, workspace_id: str) -> None:
    """Explicit local opt-in only; scope, baseline and both switches are not inferred."""
    safe_path(registry)
    before = registry.read_bytes()
    load_registry(registry)
    raw = json.loads(before)
    entry = next(item for item in raw["workspaces"] if item["workspace_id"] == workspace_id)
    if entry["writes_enabled"]:
        return
    if not yes("已审查准确白名单；现在允许此工作区写入？/ Allow reviewed workspace writes?"):
        return
    if registry.read_bytes() != before:
        raise ValueError("registry_changed_retry")
    entry["writes_enabled"] = True
    write_json(registry, raw)


def validate_export_destination(path: Path, registry: Path) -> Path:
    """Resolve a safe export destination only when the full protection scope is known."""
    safe_path(path)
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError("export_failed")
    # Normalize traversal only AFTER rejecting linked ancestry. This is not the
    # interpreter path (where resolving a venv symlink would select base Python).
    path = path.resolve(strict=False)
    safe_path(registry)
    registry = registry.resolve(strict=False)
    if (path == registry or path == switch_path(registry)
            or any(part.rstrip(" .").casefold() == ".bridge" for part in path.parts)
            or any(":" in part for part in path.parts[1:])):
        raise ValueError("export_failed")
    try:
        workspaces = load_registry(registry)
    except RegistryError:
        # Unknown protection scope is not an empty scope. Preview stays available.
        raise ValueError("export_registry_unverified") from None
    if not workspaces:
        raise ValueError("export_registry_unverified")
    if any(path.is_relative_to(workspace.root.resolve()) for workspace in workspaces.values()):
        raise ValueError("export_failed")
    return path


def save_export(path: Path, report: dict, *, registry: Path) -> None:
    """Preview elsewhere, then create a NEW file; never overwrite business state."""
    path = validate_export_destination(path, registry)
    text = diagnostics.serialize(report)
    # Exclusive creation prevents overwriting a registry, ledger, or source file.
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = SafeParser(description="Local setup / 本地安装向导；不控制桌面，不发送 Webhook。")
    parser.add_argument("--registry", type=Path, default=default_registry_path())
    parser.add_argument("--workspace", help="Registered or new workspace ID / 工作区 ID")
    parser.add_argument("--check", action="store_true", help="Read-only readiness / 只读检查")
    parser.add_argument("--purpose", choices=("setup", "submit"), default="setup")
    parser.add_argument("--json", action="store_true", help="Structured diagnostics, with --check")
    parser.add_argument("--task-id", default="", help="Optional exact validation ID; never enumerate history")
    parser.add_argument("--execution-id", default="", help="Optional exact execution ID for observation/cancel eligibility")
    parser.add_argument("--export", type=Path, help="With --check: preview minimal JSON, then ask before creating this file")
    parser.add_argument("--generate-only", action="store_true", help="Regenerate local instruction packet; no binding changes")
    parser.add_argument("--routine-mode", choices=MODES, help="reuse keeps Routine/Webhook but upgrades the claim entry")
    parser.add_argument("--git-gate", choices=("strict", "advisory"), default=None,
                        help="Git gate for a newly registered workspace; defaults to advisory. "
                             "Existing registrations without git_gate stay strict")
    parser.add_argument("--other-prefix", action="append", default=[],
                        help="Other writers' relative directory prefix; new advisory registrations only / 其他会话目录前缀")
    parser.add_argument("--replace-credentials", action="store_true", help="Explicit replacement prompts, never automatic")
    parser.add_argument("--reconfirm-bindings", action="store_true", help="Explicit administrator recheck of existing bindings")
    parser.add_argument("--allow-writes", action="store_true", help="Explicit reviewed workspace-write opt-in; never enables submission")
    try:
        args = parser.parse_args(argv)
        readonly_details = args.json or args.export or args.task_id or args.execution_id or args.purpose != "setup"
        writes = (args.generate_only or args.replace_credentials or args.reconfirm_bindings
                  or args.allow_writes or args.routine_mode or args.git_gate is not None or args.other_prefix)
        git_gate = args.git_gate or "advisory"
        if args.other_prefix and (git_gate != "advisory" or args.generate_only):
            raise ValueError("invalid_arguments")
        if (readonly_details and not args.check) or (args.check and writes):
            raise ValueError("invalid_arguments")
        if args.generate_only and (args.allow_writes or args.replace_credentials or args.reconfirm_bindings):
            raise ValueError("invalid_arguments")
        registry = args.registry.absolute()
        workspace_id = args.workspace
        if args.check:
            if not workspace_id:
                raise ValueError("invalid_arguments")
            report = diagnostics.collect(registry, workspace_id, purpose=args.purpose,
                                         export=bool(args.export), task_id=args.task_id, execution_id=args.execution_id)
            print(diagnostics.serialize(report) if args.json or args.export else diagnostics.render(report))
            if args.export:
                destination = args.export.absolute()
                # This location is displayed locally only, never embedded in the JSON.
                print("Local output / 本地输出: " + str(destination))
                if yes("写入上方已预览内容？不自动上传 / Save this preview? No upload"):
                    save_export(destination, report, registry=registry)
                    print("DIAGNOSTIC_SAVED (credentials not inspected; no upload)")
                    return 0
                print("EXPORT_NOT_SAVED")
            return 0 if report["complete_for_purpose"] else 1
        environment = inspect_environment()
        if not environment["ready"]:
            print(diagnostics.render({**environment, "environment_ready": False,
                                      "findings": [{"code": c, "phase": "environment"} for c in environment["codes"]]}))
            return 1
        print("Installed bridge / 当前安装: " + environment["bridge_version"])
        print("Python (absolute, no activation needed): " + interpreter())
        safe_path(registry)
        existing = load_registry(registry) if registry.exists() else {}
        if args.other_prefix and workspace_id in existing:
            raise ValueError("invalid_arguments")
        if not workspace_id:
            print("已登记工作区 / Registered: " + ", ".join(existing))
            workspace_id = input("工作区 ID / Workspace ID: ").strip()
        saved_mode = None
        if workspace_id in existing:
            raw = json.loads(registry.read_text(encoding="utf-8"))
            entry = next(item for item in raw["workspaces"] if item["workspace_id"] == workspace_id)
            saved_mode = entry.get("setup_routine_mode")
        mode = args.routine_mode or (saved_mode if saved_mode in MODES else ("reuse" if workspace_id in existing else "create"))
        if args.generate_only:
            print(generate(registry, workspace_id, mode))
            return 0
        if workspace_id not in existing:
            register(registry, workspace_id, mode=mode, git_gate=git_gate,
                     **({"other_prefixes": args.other_prefix} if args.other_prefix else {}))
        if args.allow_writes:
            allow_writes(registry, workspace_id)
        workspace = load_registry(registry)[workspace_id]
        # Never create or rewrite the submission switch. Missing means safe/off.
        print("总开关 / Submission switch: " + ("ON (preserved)" if execution_switch_enabled(registry) else "OFF (safe default)"))
        print("DONE: workspace registered; scope and baseline preserved / 已登记；保留范围与基线")
        output = generate(registry, workspace_id, mode)
        print("Grok Bot: copy this complete packet once / 集中复制说明: " + str(output / "routine-setup.md"))
        print("MCP client: copy the absolute-interpreter config / 客户端配置: " + str(output / "mcp-config.json"))
        print("可现在退出，完成 Grok Bot 清单后用同一命令继续。/ Exit now and resume with the same command.")
        for phase, target, other in (("validate", workspace.validate_credential_target, workspace.execute_credential_target),
                                      ("execute", workspace.execute_credential_target, workspace.validate_credential_target)):
            present = status_for(target, (other,)) == "present"
            if present and not args.replace_credentials:
                print("DONE: " + phase + " credential present; preserved / 已有凭据，保留")
            elif yes("现在隐藏输入 " + phase + " 凭据？/ Enter credential now?"):
                if init_credentials.main(["--target", target]) != 0:
                    print("Credential not saved / 凭据未保存，可稍后继续")
            routine = routine_readiness(workspace, phase, registry)
            if routine["submit_ready"] and not args.reconfirm_bindings:
                print("DONE: " + phase + " administrator binding; preserved / 已确认绑定，保留")
                continue
            print("请在 Grok Bot 核对 " + phase + " 的目录、工作区、策略、注册表和领取入口。")
            if phase == "execute":
                print("必须更新领取入口：grokbot_mcp.claim_cli / claim-evidence-v2 / schema-v2 / claim_sha256；claim_dynamic.py 原样不支持。")
            if yes("已逐项核对实际绑定并启用此 Routine？/ Confirm the checked, enabled Routine?"):
                confirm_binding(registry, workspace_id, phase, mode)
        return 0 if check(registry, workspace_id) else 1
    except (EOFError, KeyboardInterrupt):
        print("\n已退出；已保存步骤保留，未保存的输入需重填。/ Resume with the same command.")
        return 130
    except Exception as exc:
        candidate = getattr(exc, "code", None)
        if candidate is None and type(exc) is ValueError and len(exc.args) == 1:
            candidate = exc.args[0]
        print(diagnostics.render({"findings": [{"code": candidate}]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
