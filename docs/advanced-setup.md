# Advanced setup / 高级配置

[English quickstart](quickstart.md) · [中文快速开始](quickstart.zh-CN.md)

The wizard is the recommended entry. These commands are for explicit installation selection, administrator maintenance and reference—not a second recommended configuration flow. / 主流程始终是向导；本页仅用于安装选择、管理员维护和手工参考。

Administrator `routine-check` and `ledger-archive`, restricted `allowed_globs` and whole-repository advisory operation are documented in the [English guide](advisory-workspaces.md) / [中文指南](advisory-workspaces.zh-CN.md). These commands never access credential values or authorize sends. / 两个管理员命令不读取凭据值，也不授权发送。

## Install source without the PowerShell bootstrap / 不使用引导脚本安装

From the reviewed candidate checkout, use a **new** environment, not an active MCP installation:

```powershell
py -3.12 -m venv C:\ExampleBridgeCandidate
& 'C:\ExampleBridgeCandidate\Scripts\python.exe' -m pip install .
$BridgePython = 'C:\ExampleBridgeCandidate\Scripts\python.exe'
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace
```

This still uses the same wizard and registry. To install a supplied candidate wheel instead, replace `.` with the actual wheel's local absolute filename and verify its SHA-256 against that candidate's manifest. No PyPI bridge release is assumed. An existing install is not upgraded by the bootstrap; select a new directory to test a different version. / 仍使用相同向导和登记体系；wheel 必须来自实际交付并核对哈希，不覆盖当前安装。

The legacy `scripts/setup.ps1 -Target <credential-target> -Python <installed-python>` interface remains a hidden-input credential helper. New users should use the wizard so they cannot accidentally mix phase targets or interpreters. / 旧 `-Target` 接口保留；新用户应通过向导填写凭据。

## Administrator opt-ins / 管理员明确授权

Normal setup never automatically enables submission. An absent switch means off. Existing values are preserved. A read-only `--check` rejects write-related options before making any changes. / 安装不自动开启提交；缺少开关文件即关闭，只读检查不能混用写选项。

To allow the already reviewed exact workspace scope, use:

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --allow-writes
```

It asks for confirmation, changes only `writes_enabled`, and does not change scope, baseline or the submission switch. Do not combine this with `--check`. / 此操作需要确认，仅变更已登记工作区写许可。

Only when you deliberately intend a real submission, inspect the selected registry and opt into the separate switch. The following is an explicit administrator action, **not an installation or diagnostic step**:

```powershell
$Registry = Join-Path $env:LOCALAPPDATA 'grokbot-mcp\registry.json'
$Switch = Join-Path (Split-Path -Parent $Registry) 'execution_switch.json'
# Confirm this is the same registry used by the wizard/client.
[IO.File]::WriteAllText($Switch, '{"enabled":true}', (New-Object Text.UTF8Encoding $false))
```

It gates **both** validation and execution Webhooks. After the intended submission(s), turn it off:

```powershell
[IO.File]::WriteAllText($Switch, '{"enabled":false}', (New-Object Text.UTF8Encoding $false))
```

For a custom registry, explicitly set `$Registry` to that selected file first. The switch does not cancel work already sent. Do not treat disabling it as remote cancellation. / 自定义登记文件必须使用对应路径；关闭开关不会取消已发送任务。

## Binding and credential maintenance / 绑定与凭据维护

Use `--routine-mode reuse --generate-only` to prepare instructions for retaining an existing Routine/Webhook while upgrading its claim entry. Then run `--routine-mode reuse --reconfirm-bindings` only after checking the actual enabled Routine. Use `--replace-credentials` only for an intentional replacement; each phase still prompts separately using hidden input. / 先升级实际领取入口，再明确重新确认；不要为了消除错误伪造绑定能力。

`migrate` is an explicit binding review mode, not a directory mover or baseline updater. Outstanding work must be resolved first; the existing safety gate rejects binding changes with unresolved tasks. No mode migrates files, deletes ledgers, advances HEAD or verifies a live remote connection.

## Eligible local cancellation / 满足条件的本地取消

First inspect the exact execution without changing it:

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --registry $Registry --workspace demo-workspace --check --execution-id $ExecutionId
```

Only `local_cancellation_available` indicates that this point-in-time snapshot satisfied the **same predicates** used by the original cancellation command: prepared authorization, explicit never-sent fields, no claim, no result, and no contradictory evidence. The preview is not authorization; the command rechecks under lock:

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.cancel_execution_cli --registry $Registry --workspace demo-workspace --execution-id $ExecutionId --reason operator_cancelled --confirm
```

Never apply this to an unknown, submitted or claimed execution. Evidence is retained, a cancelled ID cannot be recycled, and late results cannot revive it. A new task after an eligible cancellation is an explicit operator decision, not an automatic resend. / 未知、已提交、已领取任务不能强行取消；保留全部证据，不复用 ID。

## Manual registry reference / 手工登记参考

For expert maintenance only, [registry.example.json](../examples/registry.example.json) documents the existing schema. The exact root, HEAD, allowed files, forbidden files (including the protected file), test command IDs and distinct Credential Manager targets must match the intended workspace. Keep this file outside the source repository. Existing hand-written registries remain supported; the wizard loads the same schema.

Do not use manual JSON editing to replace a failed validation proof, bless an old claim, resolve uncertain submission, bypass binding checks or advance a baseline without review. Do not copy this example over an existing registry. Ordinary new-user setup does not require manual JSON editing. / 手工示例不是覆盖现有登记文件的指令，更不是绕过安全门禁的途径。

## Selected client, migration and optional agent / 所选客户端、迁移与可选代理

Use the [bilingual deployment checklist](deployment.md) / [中文部署清单](deployment.zh-CN.md) for `client_cli`, `migration_cli` and `agent_cli`. These commands are local administrator capabilities, not extra MCP tools. Every mutation has a default preview and explicit hash confirmation; `--probe` independently spawns only the recognized target and never proves UI reconnection. Strict JSON is the only rewrite format; other formats receive a fragment only.

Prefer `migration_cli plan` to an untracked manual binding edit. It retains existing Routine/Webhooks, records old bindings/profile/packet hashes, keeps submission blocked while remote readback is pending and refuses unsafe rollback after new tasks. It does not move roots, widen policies, edit registry files, read credential values or turn on either permission switch. `claim_dynamic.py` unchanged remains unsupported.

优先审查迁移计划而不是直接改本地确认文件；本地备份/回读不代表远端已更新。仅有管理员声明也不代表真实执行通过。可选代理相对路径以显式配置的父目录为基准，缺失不搜索、不下载、不执行未知文件。结果写回遵循[统一契约](result-contract.md)，禁止从文字报告猜测计数。
