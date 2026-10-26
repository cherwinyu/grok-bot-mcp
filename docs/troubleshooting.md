# Read-only diagnostics / 只读诊断


## First five checks / 统一排查顺序

**Actual connected identity → local environment/entry → credential presence → existing Routine/binding → reuse or migration → only then recreation.**

**实际连接身份 → 本地环境和入口 → 凭据存在性 → 现有 Routine/绑定 → 复用或迁移 → 最后才考虑重建。**

Use `client_cli inspect --config <explicit.json> --workspace <id>` before the server is running. Missing interpreters/old scripts, unsupported argument conventions, wrong venv/dependency metadata and registry/workspace errors have separate stable codes. It never runs an unknown proxy. `--probe` is an explicit independent import/stdio check, not a client reload. Follow [deployment recovery](deployment.md) / [中文恢复](deployment.zh-CN.md); neither a missing directory nor present credentials proves that another Routine must be created.

`result_diagnosis.category=format_error` identifies incorrect JSON/field types (especially `tests.passed=true`), not a measured test failure. Fix the **unpublished temporary** candidate and run `result_cli`; do not resend a Webhook, repeat an uncertain task or replace frozen proof. `identity_error` requires checking the original task, not substituting IDs. See [result contract](result-contract.md).

`migration_pending` blocks submissions until a reviewed recover/finalize/rollback. `migration_new_evidence_present` refuses old-snapshot rollback over new tasks. `binding_claim_runtime_mismatch` means the claim command ran in a different installation from the explicitly attested interpreter. `agent_prompt_missing` is an optional-integration issue: use its explicit configured path or versioned generator, never search and execute arbitrary files.

[Home](../README.md) · [中文首页](../README.zh-CN.md) · [Advanced maintenance](advanced-setup.md)

Use the absolute installed interpreter printed by setup, not an arbitrary `python` on PATH. In the examples `$BridgePython` is that path and `$Registry` is the selected registry. No command below sends a Webhook, enables switches, synchronizes a ledger, loads a credential key for submission, or repairs state automatically.

以下命令均使用向导打印的安装解释器；不会发送 Webhook、开启开关、同步账本或自动修复状态。

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --check
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --check --purpose submit
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --check --json
```

Add `--registry $Registry` consistently when using a custom registry. Add `--task-id $TaskId` and/or `--execution-id $ExecutionId` to investigate specific local evidence. This never enumerates all historical tasks. IDs stay local and are not included in the diagnostic report.

自定义登记位置应始终加 `--registry`。按需传入准确任务 ID／执行 ID，可排查未同步、证据变化、未知提交及取消资格；不会遍历全部历史，导出不包含真实 ID。

## Read the result / 读懂结果

Each finding has a stable `code`, ordinary-language `reason`, affected operations in `blocks`, a concrete `next_step`, a `where` field (`local` or `grok_bot_then_local`), and severity. The report separately lists environment/workspace checks, credential presence, local configuration, administrator binding and submission permission.

每条诊断包含稳定错误码、原因、受阻操作、下一步及操作位置；不会用“已绑定”替代“远端验证成功”。`--purpose setup` 将关闭的写许可和总开关视为安全默认状态；`--purpose submit` 将其视为提交阻塞条件。

For `unconfirmed_changes` and `protected_or_forbidden_changed`, the `--check` findings (text, `--json` and `--export`) also carry `includes_ignored` and `top_level_dirs`. `top_level_dirs` lists up to 10 top-level directories with the number of observed Git status entries under each, most entries first; files at the repository root are counted under `(root)`. `includes_ignored` is `true` when the underlying Git status also counted git-ignored files, as the protected/forbidden check does, so a directory such as a virtual environment inside the workspace can account for many entries. These fields appear only in the CLI diagnostics, not in MCP tool results, and do not change any gate or error code.

对 `unconfirmed_changes` 与 `protected_or_forbidden_changed`，`--check` 的诊断（文本、`--json`、`--export`）还会带 `includes_ignored` 和 `top_level_dirs`。`top_level_dirs` 列出最多 10 个顶层目录及其下观测到的 Git 状态条目数，条目多者在前；仓库根目录的文件计入 `(root)`。`includes_ignored` 为 `true` 表示所依据的 Git 状态同时统计了被 Git 忽略的文件（保护/禁止检查即如此），因此工作区内的虚拟环境等目录可能占去大量条目。这些字段只出现在 CLI 诊断中，不出现在 MCP 工具返回里，也不改变任何门或错误码。

| Code or family | What to do / 处理方法 |
|---|---|
| `python_interpreter_invalid`, `python_unsupported`, `installation_mismatch` | Use a real Python 3.12 installation and a separate installed venv; preserve the active installation. 使用准确安装解释器，不覆盖现用环境。 |
| `git_missing`, `git_unavailable` | Install/repair Git, reopen PowerShell and run `git --version`. |
| `registry_missing`, `unknown_workspace` | `grok_list_workspaces` returns `ok: false` and `workspaces: null` when the registry file is missing. Check `Test-Path` on the interpreter and `GROKBOT_MCP_REGISTRY` in the same process, then run the wizard without `--check`. 注册表缺失不是空工作区列表。 |
| `registry_unreadable`, `registry_invalid` | Check access and the selected file. Preserve it; do not replace it with an example. 检查权限／格式，保留原文件。 |
| `git_status_failed`, `workspace_path_invalid`, exact-file errors | Verify the intended committed Git root and exact filenames. 不用相似文件名扩展白名单。 |
| `credentials_missing`, `credentials_unreadable` | Resume hidden input for the indicated phase. Never put keys into JSON, issues or logs. |
| `routine_status_unknown`, `routine_paused` | Check the actual Routine in Grok Bot, then explicitly confirm its complete binding locally. |
| `binding_unknown`, workspace/policy mismatch | Compare the actual workspace/policy with the registration; resolve outstanding work before migration. |
| `binding_claim_entry_mismatch`, `binding_capability_missing` | Keep Routine/Webhook but upgrade the claim entry and `claim-evidence-v2`. Do not synthesize hashes. |
| `execution_switch_disabled`, `workspace_writes_disabled` | Safe defaults for setup. Only explicit reviewed opt-ins permit submission/write operations. |
| `git_head_mismatch`, `baseline_stale` | Inspect HEAD and frozen evidence; never automatically advance the baseline. |
| `validation_not_verified` | Call `grok_get_status` on the same task with `observe_only=false`; require persisted success before authorization. 只读观察不等于账本同步。 |
| `validation_rejected`, `validation_not_succeeded` | Review the failed validation; preserve its terminal evidence. Replacing proof cannot authorize execution. |
| `result_changed`, `validation_hash_mismatch`, `claim_hash_mismatch` | Stop and investigate original evidence. Do not reformat proof, remove ledgers or relax checks. |
| `unknown_submission`, `retry_forbidden`, duplicate IDs | Do not resend or create a replacement identity to repeat the work. Observe the original task and inspect Grok Bot. |
| `incomplete_task_exists`, `concurrent_execution`, `result_timeout` | Work is unresolved. Timeout is not permission to retry or force cancellation. |
| `local_cancellation_available` | A read-only snapshot meets the original cancellation rules. Review before using the local command; it rechecks under lock. |
| cancellation unavailable/evidence exists | Never force cancellation. Retain evidence and investigate the remote outcome. |
| `diagnostic_failed` | The check failed closed; raw exception details were withheld. Check installation, access and options locally. |

## Submission scope / 提交检查的适用范围

`workspace_submit_prerequisites_met` reports the selected workspace's local
installation, configuration, Routine readiness and switches, not permission for
an arbitrary task. `selected_execution_checked` says whether `--execution-id` was
supplied. `selected_execution_submit_permitted` is `null` without that selector;
otherwise it can be true only when workspace prerequisites and this execution's
unused, granted, unchanged-evidence snapshot all pass. Unknown/attempted submissions,
accepted, claimed, completed, failed, cancelled, revoked or incomplete grants do
not pass. The old CLI `submit_permitted` is the effective summary: workspace-only
without an execution selector, and combined with execution checks when selected.
`--purpose submit` cannot pass for a selected execution that cannot submit again.
`--task-id` inspects validation evidence; it does not request or reserve a new send.
None of these flags replaces the actual submit entry's locked rechecks.

`workspace_submit_prerequisites_met` 仅表示工作区级前提；未选择执行时，
`selected_execution_submit_permitted=null`，不能理解成某个任务可提交。
指定 `--execution-id` 后，只允许当前仍有效、从未提交且证据未变的授权通过快照检查；
未知、已提交、已领取、终态、已取消、撤销或不完整授权均不可通过。
`submit_permitted` 是对应选择范围的兼容摘要，`--purpose submit` 的总结果纳入当前执行资格。
绑定确认与 Routine 启用分别判断：暂停阻止提交，但不抹去仍然有效的管理员绑定。
检查不预留发送机会、不授权、不重发；真正提交仍持锁重验。

## Preview and export minimal diagnostic JSON / 预览和导出

```powershell
$Diagnostic = Join-Path $env:TEMP 'grokbot-diagnostic.json'
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --check --export $Diagnostic
```

The command prints the **complete JSON**, displays the output location locally, and asks before creating a new file. Answer `n` or interrupt to save nothing. It never uploads. Existing files, registry/switch targets, `.bridge` and registered workspace destinations are rejected; choose a new filename outside the workspace. The exact output location is displayed locally but is not embedded in the JSON.

命令先展示完整 JSON 和本地输出位置，再询问是否写入。拒绝／中断不会导出；不自动上传，不覆盖已有文件，不写入登记工作区或 `.bridge`。文件路径只在本机显示，不进入诊断 JSON。

**Export does not access Credential Manager at all**, including its presence API. It reports `credentials_checked=false` and `credentials_ready=null`, so it cannot certify local configuration completeness. A normal `--check` may check credential presence using the existing wiped, non-decoding presence API; it does not load URL/key for submission. Do not turn the export's “not checked” into “credential missing.”

导出完全不访问凭据管理器，包括存在性检查；因此不能证明本地配置完成。普通本地检查可使用原有“不解码、读后擦除”的存在性接口，但不加载提交所需 URL／密钥。

The report is built from allowed fields and trusted diagnostic text. It excludes URLs/keys, Credential Manager targets, usernames, machine names, private absolute paths, task IDs/text, source, raw logs, full registry/ledgers and untrusted exception strings. Nested findings are rebuilt from known codes rather than passed through. Only explicitly selected task evidence is inspected for task diagnosis. A diagnostic failure emits a fixed message, never a traceback or nested exception payload.

诊断采用允许字段构建，不是对整份原始配置做字符串替换。未知错误码降级为固定提示，嵌套异常和任意字段不会透传。你分享文件前仍应阅读预览。

Exit codes: `0` means ready for the selected check purpose **or** that an explicitly approved export was saved; `1` means incomplete, invalid or export declined; `130` means interrupted. Read `complete_for_purpose` inside exported JSON rather than interpreting export exit 0 as system readiness. `--json` without export prints machine-readable JSON only.

## Scope and limits / 检查范围与限制

Fictional-marker tests inspect serialized JSON and nested test archives for fake secrets, URLs, identities, paths and task text, including injected exception paths. This tests the diagnostic allow-list and failure behavior; it is **not a complete audit of all private information on a machine**.

`scripts/scan_release.py` is a separate **generic source/package scan**. Its patterns are not an exhaustive privacy audit; optional operator-provided private markers must live outside the public tree. It does not sanitize Git history, Grok Bot chats, copied routine packets or arbitrary external files. The diagnostic exporter never creates an archive or bundles such material. See [development](development.md) for scan commands.

诊断导出测试与源码／wheel 通用扫描是两个不同范围；都不能称为完整私人信息审计。

## Existing protocol checks remain / 保留的协议诊断

HTTP 200 means accepted, not succeeded. HTTP 400 is classified from the response: `routine_paused_suspected`, `authentication_failed`, `invalid_payload`, `rate_limited`, `quota_exhausted_confirmed`, `quota_status_unknown` or `http_400_unknown`. The bridge does not change billing, enable on-demand or retry uncertain sends.

Cached `succeeded` is historical. Use `grok_get_status(..., verify_current=true)` when you need a current evidence/workspace recheck, and inspect `current_integrity`; it still does not approve QA. Incomplete JSON is bounded observation, not permission to replace a proof. A symlink test may skip only when the OS refuses symlink creation; that is not removal of the security check.

## Export protection / 导出保护

Registry loading must succeed and contain at least one workspace. Missing, empty, invalid, corrupt or unreadable registries allow terminal preview only; saving fails with `export_registry_unverified` and creates no file. Protection is checked again at save time. These are snapshot checks on registered roots, not an OS sandbox or a guarantee about unregistered paths and malicious concurrent filesystem changes.

登记无法完整加载或为空时，只允许终端预览，拒绝任何位置的保存；固定码为 `export_registry_unverified`，不创建文件。保存重新读取登记；保护针对登记根目录的检查快照，不是操作系统沙箱，也不保证未登记路径或并发恶意文件系统改动。

## P0 checks before live use

Use `safety_cli preview` with explicit registry/workspace and original task IDs. `webhook_protocol_unsupported`, `execution_topology_unsupported`, `execution_isolation_not_implemented` and `desktop_approval_unsupported` refuse unimplemented configurations. `execution_profile_read_only` refuses execution, not merely a UI display. `target_hardlink_rejected`, `json_duplicate_field`, `json_nonfinite` and `json_depth_exceeded` indicate rejected local input; do not replace frozen proof or resend. For credential changes use `credential-plan`; an on switch or unresolved task blocks maintenance readiness. Follow [the security model](security-model.md) and [the offline demo](offline-demo.md).
