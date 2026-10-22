"""Read-only, allow-list-built diagnostics. Never send, reconcile, repair, or retry.

Exports deliberately skip ALL credential access (including presence checks).
No arbitrary exception, registry, proof, ledger or nested tool payload is emitted.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CATALOG: dict[str, dict] = {}


def _add(codes, reason, next_step, where="local", blocks="setup submit"):
    for code in codes.split():
        CATALOG[code] = {"code": code, "reason": reason, "next_step": next_step,
                         "where": where, "blocks": blocks.split()}


_add("windows_required", "This bridge requires Windows. / 此桥仅支持 Windows。",
     "Run setup.ps1 in Windows PowerShell; Linux checks do not establish Windows readiness.")
_add("python_unsupported", "Python 3.12 or newer is required. / Python 版本过旧。",
     "Install Python 3.12, then rerun scripts/setup.ps1 with its -Python absolute path.")
_add("python_interpreter_invalid", "The running interpreter path is unavailable. / 当前解释器路径不可用。",
     "Use the installation's Scripts/python.exe absolute path; do not copy an activated shell's generic python command.")
_add("installation_unavailable installation_mismatch", "The isolated interpreter cannot load this installed bridge and pinned MCP dependency. / 安装或解释器不匹配。",
     "Create a separate venv with Python 3.12 and install the checked-out source using that venv's python -m pip install .; do not replace an active installation.")
_add("git_missing git_unavailable", "Git is missing or cannot run. / Git 缺失或无法运行。",
     "Install Git for Windows, open a new PowerShell and check git --version; rerun the same setup command.")
_add("registry_missing", "No registry was found. grok_list_workspaces returns ok=false rather than an empty workspace list. / 尚未建立登记配置；列出工作区不会假装成空列表。",
     "Check that GROKBOT_MCP_REGISTRY points at an existing registry.json in this same process. A background shell does not inherit an earlier session's variables. Run the setup wizard without --check to register an existing Git workspace; use the same --registry path on resume.")
_add("registry_unreadable registry_invalid", "The registry cannot be read or has an invalid schema. / 登记文件不可读或格式错误。",
     "Check access and the --registry selection locally. Preserve the file; compare it with the advanced registry example instead of deleting or replacing it.")
_add("unknown_workspace", "This workspace ID is not registered. / 工作区 ID 尚未登记。",
     "Rerun the wizard without --check and select or register the intended ID; a folder path is not a workspace ID.")
_add("workspace_path_invalid git_status_failed", "The registered folder is missing or is not a committed Git workspace. / 工作区路径或 Git 提交无效。",
     "Check the selected folder with git -C <workspace> rev-parse HEAD. For a new demo, initialize and commit it before registration; do not reset an existing workspace.")
_add("shared_object_store_rejected", "This workspace shares a Git object store outside its root. / 工作区共享外部 Git 对象库。",
     "Use a separate normal clone for the target workspace, not a linked worktree. Keep the original workspace unchanged.")
_add("reparse linked_path_rejected", "A linked path or Windows reparse point was rejected. / 路径链接被安全检查拒绝。",
     "Select a real local directory and exact files without links; do not weaken reparse-point checks.")
_add("target_file_missing target_path_rejected target_case_mismatch target_not_file forbidden_file absolute_path traversal empty_allowed_files", "An allowed or protected file failed the exact-path check. / 白名单或保护文件不符合准确路径要求。",
     "Compare spelling and case against the registered files. Restore a missing protected file from your reviewed source; do not expand scope to a similar filename.")
_add("git_head_mismatch baseline_stale", "The current Git baseline differs from the registered or frozen baseline. / 当前基线与登记或冻结基线不一致。",
     "Inspect git status and git log locally and review the frozen baseline. Do not auto-advance HEAD or delete the authorization.", blocks="prepare submit claim")
_add("unexpected_changes unconfirmed_changes scope_violation workspace_changed_since_verification", "The workspace has changes that require review. / 工作区有待审查或越界改动。",
     "Inspect git status and git diff in the workspace. Review every difference; do not reset files or widen the allow-list to make the check pass.", blocks="prepare submit claim acceptance")
_add("credentials_missing credentials_unreadable credential_unreadable", "One phase's credential is absent or unreadable. / 对应阶段凭据缺失或不可读。",
     "Resume setup in an interactive PowerShell. Enter that phase's URL/key only through hidden input; use --replace-credentials only for an intentional replacement.")
_add("credentials_not_inspected", "Credential Manager was not accessed for this export. / 诊断导出未访问凭据管理器。",
     "Run --check without --export locally to check presence. Never attach a URL, key or Credential Manager screenshot.", blocks="")
_add("routine_status_unknown routine_paused", "The local Routine attestation is missing or paused, not a live account check. / Routine 本地确认缺失或暂停。",
     "Check the correct Routine in Grok Bot, its enabled state and binding; then resume the wizard. Use --reconfirm-bindings for an intentional recheck.", "grok_bot_then_local")
_add("binding_unknown binding_invalid", "The administrator has not confirmed a valid complete binding. / 管理员尚未确认有效完整绑定。",
     "Check workspace, policy, registry and claim entry in Grok Bot; explicitly confirm them in the wizard. Credentials alone are not a binding.", "grok_bot_then_local")
_add("binding_workspace_mismatch binding_policy_mismatch", "The attested workspace or policy differs from the current registration. / 绑定工作区或策略不一致。",
     "Stop submissions and compare the actual Routine with the registered policy. Resolve old work first; use explicit --routine-mode migrate only for a reviewed migration.", "grok_bot_then_local")
_add("binding_claim_entry_mismatch binding_capability_missing", "The claim entry or required protocol capability is outdated. / 领取入口或协议能力过旧。",
     "Use --routine-mode reuse --generate-only. Keep the Routine/Webhook but update its entry to grokbot_mcp.claim_cli with claim-evidence-v2; never synthesize old claim hashes. Reconfirm only after the update.", "grok_bot_then_local")
_add("execution_switch_disabled validate_manifest_not_enabled execute_manifest_not_enabled", "The submission switch is off: a safe default, not a broken installation. / 提交总开关关闭是安全默认状态。",
     "Leave it off for setup. For a deliberate real submission, follow the local opt-in in advanced setup, then turn it off again; no diagnostic command enables it.", blocks="submit")
_add("workspace_writes_disabled", "Workspace write permission is off: a safe default. / 工作区写许可关闭是安全默认状态。",
     "Leave it off until scope is reviewed. To opt in, run the wizard with --allow-writes and explicitly confirm; the submission switch remains separate.", blocks="prepare submit claim")
_add("validation_not_verified", "A success observation has not become a durable ledger decision. / 校验结果尚未同步为账本终态。",
     "Call grok_get_status with the same task_id and observe_only=false. Require persisted succeeded before preparing execution; do not edit the ledger.", blocks="prepare_execution")
_add("validation_missing missing_task validation_ledger_missing", "A required validation result or submission ledger is missing. / 缺少校验结果或提交账本。",
     "Check the original task in Grok Bot and query that task locally. An orphan proof cannot authorize execution; do not invent or restore a ledger by hand.", "grok_bot_then_local", "prepare_execution")
_add("validation_rejected validation_not_succeeded validation_failed", "Validation did not pass. / 校验未通过。",
     "Review the original validation and task scope. Preserve the failed proof and ledger; replacing the proof cannot authorize execution.", "grok_bot_then_local", "prepare_execution")
_add("result_changed validation_hash_mismatch claim_hash_mismatch hash_mismatch validation_decision_mismatch validation_ledger_identity_mismatch identity_mismatch claim_missing", "Frozen evidence is missing, changed or belongs to another identity. / 冻结证据缺失、变化或身份不符。",
     "Stop and inspect the original evidence locally. Do not reformat proof, synthesize claim hashes, delete ledgers or relax binding checks.", blocks="prepare_execution submit claim acceptance")
_add("validation_unreadable validation_ledger_unreadable result_unreadable encoding_rejected schema_invalid snapshot_unavailable", "Local evidence cannot be safely read or verified. / 本地证据不可读或无法校验。",
     "Check local permissions and whether the Routine finished atomic writing. Keep the original evidence; query again without sending.", blocks="prepare_execution acceptance")
_add("unknown_submission submission_started retry_forbidden duplicate_task_id duplicate_execution result_already_exists", "Submission was attempted, is uncertain, or this identity is already used. / 提交状态不确定或标识已使用。",
     "Do not resend or create a replacement identity to repeat the work. Observe the same task and inspect Grok Bot; do not cancel uncertain submissions or delete ledgers.", "grok_bot_then_local", "submit prepare_execution")
_add("incomplete_task_exists concurrent_execution binding_change_has_unresolved_tasks task_pending result_timeout", "A task remains unresolved, running or timed out. / 仍有未决、运行中或超时任务。",
     "Observe the original identity and check Grok Bot. Timeout is not permission to retry. Local cancellation is available only when its read-only eligibility check passes.", "grok_bot_then_local", "submit prepare_execution binding_change")
_add("local_cancellation_available", "The selected authorization is provably unused in this read-only snapshot. / 本次快照证明该授权未提交、未领取且无结果。",
     "Review locally, then use cancel_execution_cli with the same registry/workspace/execution-id, --reason operator_cancelled --confirm. The command rechecks under lock, preserves evidence and never recycles the ID.", blocks="")
_add("cancellation_requires_unsubmitted_authorization cancellation_evidence_exists missing_or_unreadable_execution execution_identity_mismatch", "Local cancellation is not proven safe for this authorization. / 无法证明此授权可安全取消。",
     "Do not force cancellation, remove files or resend. Inspect the existing task in Grok Bot and retain all local evidence.", "grok_bot_then_local", "cancel")
_add("authorization_cancelled result_after_cancellation", "This authorization was cancelled; a late result cannot revive it. / 已取消授权不会被迟到结果复活。",
     "Keep the cancelled identity and its evidence. Review any late result separately; do not reuse the identity.", blocks="submit claim")
_add("test_instructions_missing", "This older profile has no saved test instructions. / 旧配置未保存测试运行说明。",
     "Supply the exact registered test-command mapping to Grok Bot from your local project documentation; do not let it guess. Existing scope and bindings are preserved.", "grok_bot_then_local", "")
_add("invalid_task_id invalid_execution_id invalid_workspace_id", "The supplied identity is invalid. / 标识格式无效。",
     "Use the workspace ID selected in setup and the actual task/execution ID returned by the bridge, not an example or a directory.")
_add("registry_changed_retry registration_cancelled absolute_root_required test_command_required workspace_root_already_registered", "Registration was cancelled or its inputs changed or need correction. / 登记已取消、输入不完整或配置发生变化。",
     "Rerun the wizard with the same registry. Select the existing registration when present; supply an absolute committed Git root, exact files and test instructions.")
_add("export_registry_unverified", "Export protection scope could not be verified. / 无法可靠确认导出的受保护路径。",
     "Use the terminal preview only. Check the selected registry and its access/schema locally, then rerun; no diagnostic file was created.", blocks="export")
_add("execution_submission_unavailable", "This execution is not a granted, unused submission. / 当前执行不具备已授权且从未提交的条件。",
     "Inspect the original authorization and evidence. Do not resend, delete ledgers or reset attempts; only an unused granted execution can pass this local snapshot check.", blocks="submit")
_add("diagnostic_failed export_failed invalid_arguments", "The operation stopped safely; raw exception details were withheld. / 操作安全停止，未输出敏感异常原文。",
     "Check --help, local access and the chosen installation. Rerun --check; share only a previewed diagnostic file, not raw logs or configuration.")

# Deployment/local-writer diagnostics use stable public constants, never exception text.
_add("interpreter_missing interpreter_base_missing entrypoint_missing dedicated_windows_venv_required venv_configuration_invalid",
     "The selected interpreter, its base Python or supported entry is missing/invalid. / 所选解释器、基础 Python 或受支持入口不可用。",
     "Keep old files. Install the reviewed wheel in a NEW Python 3.12+ venv; use its Scripts/python.exe with client_cli fragment/switch. Regenerate optional material with agent_cli, not a new Routine.")
_add("installation_metadata_missing_or_ambiguous installation_metadata_unreadable installation_version_mismatch candidate_version_mismatch core_dependency_mismatch",
     "Installed package/source metadata or pinned MCP dependency does not match this candidate. / 包元数据、版本或核心依赖不匹配。",
     "Inspect the explicit venv; install the matching reviewed wheel into a new venv. Static metadata is not import validation; use inspect --probe explicitly after review.")
_add("client_format_unsupported client_transport_unsupported client_server_missing_or_unsupported client_launch_invalid client_arguments_unsupported client_environment_invalid registry_selection_required absolute_interpreter_required",
     "This is not the supported selected strict-JSON stdio launch configuration. / 当前所选配置不是受支持的严格 JSON/stdin-stdout 启动格式。",
     "Use a .json object with mcpServers and an explicit selected server. Supported args are -I -X utf8 -m grokbot_mcp.server and an explicit GROKBOT_MCP_REGISTRY. For other formats generate a fragment only; do not execute an unknown old launcher.")
_add("workspace_missing target_workspace_not_ready client_destination_protected",
     "Target workspace checks failed or the proposed client configuration is protected business state. / 目标工作区检查失败，或客户端写入目标属于受保护业务文件。",
     "Inspect the selected registry, workspace, exact baseline and binding. Use a client config outside registered roots and .bridge. Preserve policy, switches and evidence.")
_add("plan_confirmation_mismatch input_changed result_confirmation_mismatch result_staging_changed",
     "Explicit confirmation does not match the current reviewed bytes. / 显式确认与当前已审查的字节不匹配。",
     "Run the same preview again and review the new plan/hash. Only then apply with its exact confirmation. Never overwrite a concurrent edit.")
_add("client_apply_interrupted atomic_write_failed transaction_readback_failed",
     "A local file transaction was interrupted or could not complete its atomic write/readback. / 本地事务中断，或原子写入、回读失败。",
     "Keep the backup and receipt private. List the selected configuration's transactions; preview recover or rollback with that ID, then explicitly confirm. Do not delete transaction evidence.")
_add("transaction_exists transaction_invalid transaction_context_mismatch transaction_backup_changed transaction_target_changed transaction_target_invalid transaction_id_invalid backup_already_exists operation_locked",
     "A transaction is busy, changed or does not match the selected target. / 事务被占用、已变化或与所选目标不符。",
     "Do not force restore. Inspect the local receipt/checksums and current target, preserving all files; re-preview only after reconciling the conflict.")
_add("absolute_path_required file_missing file_unreadable parent_missing json_too_large json_encoding_invalid json_duplicate_field json_nonfinite json_incomplete_or_invalid json_object_required",
     "The selected local file/path is missing or is not complete strict UTF-8 JSON. / 所选本地路径缺失，或 JSON 不是完整、严格 UTF-8 数据。",
     "Check the explicit path locally. Finish/correct an unpublished candidate with UTF-8 and no BOM/duplicate keys. Keep existing final evidence unchanged; do not resend a Webhook.")
_add("result_source_location_invalid",
     "The private result candidate is not the fixed temp path for this phase and id. / 私人候选不在该阶段和 ID 的固定临时路径。",
     "Write UTF-8 without BOM only to %TEMP%\\grokbot-mcp-candidates\\<phase>\\.<id>.result.tmp. Do not invent .result.candidate.json inside the workspace. Pass that temp file as result_cli --source.")
_add("result_cleanup_not_terminal result_cleanup_path_invalid result_cleanup_failed result_cleanup_record_invalid result_cleanup_conflict",
     "Protocol-file cleanup did not run, or it refused to change frozen evidence. / 协议临时文件清理未执行，或拒绝改动已冻结证据。",
     "Cleanup deletes only this id's staged .<id>.result.tmp and the matching temp candidate after the published result is succeeded or failed. It does not delete the published JSON, the submission ledger, another id, or a non-contract name.")
_add("constraint_version_unsupported",
     "The manifest constraint_version is outside the supported range. / manifest 的 constraint_version 不在支持范围内。",
     "Historical manifests omit constraint_version and remain verifiable. Accept integers from 1 through the current version. Do not require the manifest to equal the current constraint prose, and do not rewrite the manifest or its id.")
_add("result_format_invalid result_phase_invalid result_identity_invalid result_identity_mismatch result_ledger_identity_mismatch result_manifest_mismatch result_staging_path_invalid result_staging_already_exists result_transition_invalid result_submission_not_accepted result_readback_mismatch",
     "Result format, identity, stage or accepted-task evidence did not match. / 结果格式、身份、阶段或已接受任务证据不匹配。",
     "Read the diagnostic field/expected type. Use the registered task's IDs and measured values; precheck and explicitly stage/publish only that candidate. Format validity is not test success or QA. Do not resend or invent evidence.")
_add("result_terminal_frozen result_existing_evidence_frozen",
     "Terminal or previously frozen result evidence cannot be replaced. / 终态或已冻结证据不可替换。",
     "Keep the exact original result and ledger. Review the failure independently; do not replace proof, retrofit claim hashes, reuse IDs or replay the task.")
_add("migration_pending migration_state_invalid migration_blocked migration_remote_review_required migration_superseded migration_already_rolled_back",
     "Migration is incomplete, blocked or lacks the required explicit administrator readback. / 迁移未完成、已阻塞，或缺少管理员显式回读确认。",
     "Use migration_cli transactions/plan/recover for the selected registry. Update the existing remote Routine manually using the packet; finalize only after actual readback. Neither finalize nor offline checks are remote API/live acceptance.")
_add("migration_workspace_move_forbidden migration_head_change_forbidden migration_scope_expansion_forbidden migration_protection_change_forbidden migration_write_permission_change_forbidden migration_backup_inside_workspace",
     "The proposed migration changes a protected boundary or unsafe backup destination. / 迁移改变了受保护边界，或备份位置不安全。",
     "Keep the workspace, HEAD, write permission and protected files unchanged. Do not broaden the allow-list or place backups in a registered workspace; prepare a separately reviewed target registry.")
_add("migration_inputs_changed migration_new_evidence_present migration_installation_changed migration_binding_snapshot_unsupported",
     "Inputs, installation or task evidence changed, or a snapshot could copy unsupported private fields. / 输入、安装或任务证据变化，或快照包含不支持的私密字段。",
     "Stop and retain all new evidence. Do not restore an old binding over new tasks. Review the exact changed fields locally and prepare a new migration only when no tasks are unresolved.")
_add("binding_claim_interpreter_invalid binding_installation_mismatch",
     "The binding's declared claim interpreter or installation no longer matches. / 绑定中声明的领取解释器或安装不匹配。",
     "Credential presence is insufficient. Use migration_cli to review/update the existing Routine's interpreter, registry and claim-evidence-v2; do not synthesize a claim or simply copy credentials.")
_add("stdio_startup_timeout stdio_startup_failed stdio_tools_mismatch stdio_identity_mismatch runtime_identity_missing",
     "The explicit independent server probe could not complete the expected handshake/identity check. / 独立服务探测未完成预期握手或身份核对。",
     "Check the selected venv/core dependencies and canonical args, then probe again locally. A standalone probe never reloads the user's client: restart/reload it separately and compare its own grok_preflight identity.")
_add("agent_configuration_unsupported agent_prompt_selection_required agent_relative_escape_rejected agent_prompt_format_unsupported agent_prompt_missing agent_prompt_too_large agent_prompt_encoding_invalid agent_existing_file_changed",
     "Only the optional dispatcher configuration/prompt is unavailable or changed. / 仅可选调度代理配置或提示词缺失、变化。",
     "The bridge core does not require this agent. Resolve prompt_file relative to its explicit config; preview agent_cli template and generate into a new directory with confirmation. Never auto-search, download or execute an unknown prompt.")
_add("local_operation_failed", "The local operation stopped without exposing exception details. / 本地操作停止，未暴露异常原文。",
     "Inspect the chosen installation and explicit input paths locally; retain files. Use the versioned CLI --help and share only the redacted summary.")

SAFE_DEFAULTS = {"execution_switch_disabled", "validate_manifest_not_enabled", "execute_manifest_not_enabled", "workspace_writes_disabled"}
INFO_CODES = {"credentials_not_inspected", "local_cancellation_available",
              "cancellation_requires_unsubmitted_authorization", "cancellation_evidence_exists"}
WARNING_CODES = {"test_instructions_missing"}
PHASES = {"environment", "registry", "workspace", "validate", "execute", "validation", "execution"}
FLAGS = ("environment_ready", "workspace_registered", "workspace_ready", "credentials_checked",
         "credentials_ready", "local_configuration_complete", "administrator_binding_confirmed", "submit_permitted",
         "workspace_submit_prerequisites_met", "selected_execution_checked", "selected_execution_submit_permitted")


def finding(code: object, phase: str = "workspace", purpose: str = "setup") -> dict:
    code = code if isinstance(code, str) and code in CATALOG else "diagnostic_failed"
    severity = "info" if code in INFO_CODES else ("warning" if code in WARNING_CODES or (purpose == "setup" and code in SAFE_DEFAULTS) else "error")
    return {**CATALOG[code], "phase": phase if isinstance(phase, str) and phase in PHASES else "workspace", "severity": severity}


def public_report(report: dict) -> dict:
    """Rebuild even nested descriptions from trusted constants. Never stringify input."""
    purpose = report.get("purpose") if report.get("purpose") in ("setup", "submit") else "setup"
    result = {"schema_version": 1, "scope": "local-read-only; not remote verification or QA",
              "workspace": "workspace-1", "purpose": purpose,
              "remote_binding_verified": False, "qa_accepted": False,
              "webhook_sent": False, "credential_values_read": False}
    for key in FLAGS:
        value = report.get(key)
        result[key] = value if type(value) is bool else None
    for key in ("python_version", "bridge_version"):
        value = report.get(key)
        result[key] = value if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?", value) else "unknown"
    items = report.get("findings")
    result["findings"] = [finding(item.get("code"), item.get("phase"), purpose)
                          for item in (items if isinstance(items, list) else []) if isinstance(item, dict)]
    # Keep the legacy CLI summary, but make its scope explicit. No MCP fields change.
    # Selection defaults closed, including early errors, and survives re-serialization.
    result["submit_permitted"] = result["workspace_submit_prerequisites_met"]
    if result["selected_execution_checked"] is True:
        result["submit_permitted"] = (result["workspace_submit_prerequisites_met"] is True
            and result["selected_execution_submit_permitted"] is True)
    result["complete_for_purpose"] = (result.get("local_configuration_complete") is True
        and result.get("administrator_binding_confirmed") is True
        and (purpose != "submit" or result.get("submit_permitted") is True)
        and not any(item["severity"] == "error" for item in result["findings"]))
    return result


def serialize(report: dict) -> str:
    return json.dumps(public_report(report), ensure_ascii=False, indent=2) + "\n"


def collect(registry: Path, workspace_id: str, *, purpose="setup", export=False,
            task_id="", execution_id="") -> dict:
    """Single local CLI invocation; restore environment selection even after failure.

    Existing evidence readers use the process registry selection. Bind that to
    --registry, not an unrelated ambient client, and disable Git's optional index
    refresh writes. No persistent environment or business configuration is changed.
    This helper is not installed into the concurrent MCP server.
    """
    values = {"GROKBOT_MCP_REGISTRY": str(registry.absolute()), "GIT_OPTIONAL_LOCKS": "0"}
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        return _collect(registry, workspace_id, purpose=purpose, export=export,
                        task_id=task_id, execution_id=execution_id)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _collect(registry: Path, workspace_id: str, *, purpose="setup", export=False,
             task_id="", execution_id="") -> dict:
    from grokbot_mcp.setup_environment import inspect_environment
    report = {"purpose": purpose, "findings": [], "credentials_checked": False,
              "selected_execution_checked": bool(execution_id),
              "selected_execution_submit_permitted": False if execution_id else None}

    def add(code, phase="workspace"):
        item = finding(code, phase, purpose)
        if item not in report["findings"]:
            report["findings"].append(item)

    try:
        environment = inspect_environment()
        report.update(environment_ready=environment["ready"],
                      python_version=environment["python_version"], bridge_version=environment["bridge_version"])
        for code in environment["codes"]:
            add(code, "environment")
        if "windows_required" in environment["codes"]:
            return public_report(report)
        from grokbot_mcp.registry import load_registry, execution_switch_enabled, RegistryError
        from grokbot_mcp.setup_wizard import safe_path
        from grokbot_mcp.security.gitcheck import workspace_git_rejection, git_error, git_snapshot
        from grokbot_mcp.security.targets import resolve_exact_files, TargetRejected
        from grokbot_mcp.routine import routine_readiness
        safe_path(registry)
        try:
            workspaces = load_registry(registry)
        except RegistryError as exc:
            add(exc.code, "registry")
            return public_report(report)
        workspace = workspaces.get(workspace_id)
        report["workspace_registered"] = workspace is not None
        if workspace is None:
            add("unknown_workspace", "registry")
            return public_report(report)
        raw = json.loads(registry.read_text(encoding="utf-8"))
        entry = next(item for item in raw["workspaces"] if item["workspace_id"] == workspace_id)
        if not entry.get("setup_test_instructions"):
            add("test_instructions_missing")
        safe_path(workspace.root)
        safe_path(workspace.root / ".bridge")
        safe_path(registry.parent / "execution_switch.json")
        problem = None
        if not workspace.root.is_dir():
            problem = "workspace_path_invalid"
        elif any(c in environment["codes"] for c in ("git_missing", "git_unavailable")):
            problem = "git_unavailable"
        else:
            problem = workspace_git_rejection(workspace.root) or git_error(git_snapshot(workspace.root, workspace.allowed_files, workspace.expected_head))
        try:
            resolve_exact_files(workspace.root, list(workspace.allowed_files), workspace.forbidden_files)
            resolve_exact_files(workspace.root, [workspace.protected_file], (".git", ".bridge"))
        except TargetRejected as exc:
            problem = exc.code
        if problem:
            add(problem)
        report["workspace_ready"] = problem is None
        switch = execution_switch_enabled(registry)
        if not switch:
            add("execution_switch_disabled")
        if not workspace.writes_enabled:
            add("workspace_writes_disabled")
        # Do not use facade.preflight here: even its presence API reads a credential blob.
        credentials_ready = None if export else True
        bindings_ready = True
        routines_ready = True
        for phase, target, other in (("validate", workspace.validate_credential_target, workspace.execute_credential_target),
                                      ("execute", workspace.execute_credential_target, workspace.validate_credential_target)):
            if not export:
                from grokbot_mcp.credentials.backend import status_for
                status = status_for(target, (other,))
                report["credentials_checked"] = True
                if status != "present":
                    credentials_ready = False
                    add("credentials_missing" if status == "missing" else "credentials_unreadable", phase)
            routine = routine_readiness(workspace, phase, registry)
            bindings_ready = bindings_ready and routine["binding_confirmed"]
            routines_ready = routines_ready and routine["submit_ready"]
            for key in ("error", "binding_error"):
                if routine.get(key):
                    add(routine[key], phase)
        if export:
            add("credentials_not_inspected")
        report["credentials_ready"] = credentials_ready
        report["local_configuration_complete"] = (environment["ready"] and problem is None and credentials_ready is True)
        report["administrator_binding_confirmed"] = bindings_ready
        report["workspace_submit_prerequisites_met"] = (report["local_configuration_complete"]
            and routines_ready and switch and workspace.writes_enabled)
        # Optional exact-ID investigation only; never enumerate all task history.
        if task_id:
            from grokbot_mcp.security.paths import is_uuid
            from grokbot_mcp.execution.service import assess
            from grokbot_mcp.validation.service import get_validation_status
            if not is_uuid(task_id):
                add("invalid_task_id", "validation")
            else:
                status = get_validation_status(workspace, task_id, observe_only=True)
                for key in ("error", "integrity_error"):
                    if status.get(key):
                        add(status[key], "validation")
                if status.get("status") in {"pending", "running", "accepted", "timeout"}:
                    add("task_pending", "validation")
                assessment = assess(workspace, task_id)
                if assessment.get("error"):
                    add(assessment["error"], "validation")
        if execution_id:
            from grokbot_mcp.execution.service import get_execution_status
            from grokbot_mcp.cancel_execution_cli import preview_cancellation
            status = get_execution_status(workspace, execution_id, observe_only=True, verify_current=True)
            for key in ("error", "integrity_error"):
                if status.get(key):
                    add(status[key], "execution")
            if status.get("status") in {"pending", "running", "timeout"}:
                add("task_pending", "execution")
            blocker = execution_submission_blocker(workspace, execution_id)
            report["selected_execution_submit_permitted"] = (
                report["workspace_submit_prerequisites_met"] and blocker is None
                and not status.get("error") and not status.get("integrity_error"))
            if purpose == "submit" and blocker:
                add(blocker, "execution")
            preview = preview_cancellation(workspace, execution_id)
            add("local_cancellation_available" if preview["eligible"] else preview["error"], "execution")
        return public_report(report)
    except Exception as exc:
        # No repr/str/traceback, not even for nested exception causes.
        code = getattr(exc, "code", None)
        add(code if isinstance(code, str) and code in CATALOG else "diagnostic_failed")
        return public_report(report)


def execution_submission_blocker(workspace, execution_id: str) -> str | None:
    """Read-only snapshot, not a reservation or authority; submit rechecks under lock.

    Never call submit_execution here: even a rejected submit can mutate a ledger.
    Require an unused grant, then reuse its existing evidence checks without writes,
    credential access, recovery, or new attempts.
    """
    from grokbot_mcp.security.paths import is_uuid
    from grokbot_mcp.storage import JsonLedger
    from grokbot_mcp.execution.service import assess
    from grokbot_mcp.execution.evidence import verify_evidence
    if not is_uuid(execution_id):
        return "invalid_execution_id"
    try:
        record = JsonLedger(workspace.root / ".bridge/executions").read(execution_id)
        if record.get("execution_id") != execution_id:
            return "execution_identity_mismatch"
        if record.get("execution_state") == "cancelled":
            return "authorization_cancelled"
        unused = {"submission_state": "not_sent", "request_attempted_at": None,
                  "webhook_attempted": False, "submission_final": False,
                  "execution_claim_state": "unclaimed", "claimed_at": None,
                  "http_status": None, "accepted": None}
        if any(key not in record or type(record[key]) is not type(value) or record[key] != value
               for key, value in unused.items()):
            return "retry_forbidden"
        if (record.get("execution_state") != "prepared" or record.get("approval_state") != "granted"
                or record.get("terminal") or record.get("claim_sha256") or record.get("result_sha256")):
            return "execution_submission_unavailable"
        for kind in ("execution_claims", "results_execute"):
            path = workspace.root / ".bridge" / kind / (execution_id + ".json")
            from grokbot_mcp.setup_wizard import safe_path
            safe_path(path)
            if path.exists():
                return "execution_submission_unavailable"
        assessment = assess(workspace, str(record.get("manifest_task_id", "")))
        verify_evidence(workspace, record, assessment, require_claim=False, unchanged=True)
        return None
    except (OSError, ValueError) as exc:
        # Only known symbolic codes can escape; nested/private exceptions never do.
        code = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else None
        return code if code in CATALOG else "execution_submission_unavailable"


def render(report: dict) -> str:
    safe = public_report(report)
    lines = ["Local readiness / 本地就绪状态（不会发送 Webhook）"]
    labels = (("environment_ready", "Environment / 安装环境"),
              ("workspace_registered", "Workspace registered / 工作区已登记"),
              ("workspace_ready", "Workspace checks / 工作区检查"),
              ("credentials_ready", "Credential presence / 凭据存在"),
              ("local_configuration_complete", "Local configuration complete / 本地配置完成"),
              ("administrator_binding_confirmed", "Administrator binding confirmed / 管理员确认绑定"),
              ("workspace_submit_prerequisites_met", "Workspace submission prerequisites / 工作区提交前提"),
              ("selected_execution_submit_permitted", "Selected execution submission checks / 当前执行提交检查（非授权）"),
              ("submit_permitted", "Local submission summary / 本地提交检查摘要（非授权）"))
    for key, label in labels:
        state = safe[key]
        lines.append(f"{'DONE' if state is True else 'NOT CHECKED' if state is None else 'TODO'}: {label}")
    lines.append("NOT VERIFIED: real remote execution / 未验证真实远端执行；qa_accepted=false")
    for item in safe["findings"]:
        lines.append(f"[{item['severity']}:{item['code']}] ({item['phase']}; {item['where']}) {item['reason']}\n  Next / 下一步: {item['next_step']}")
    return "\n".join(lines)

_add("installation_package_unreadable", "Installed package files are unreadable or linked.",
     "Use a new isolated venv and reinstall the exact reviewed wheel; do not overwrite the existing installation.", "core")
_add("binding_claim_runtime_mismatch", "The running claim interpreter is not the attested installation.",
     "Use the interpreter and module entry in the reviewed migration packet. Do not retrofit a claim hash.", "binding")
