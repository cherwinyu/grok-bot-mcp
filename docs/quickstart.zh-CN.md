# 快速开始：先离线体验，再按需配置 Grok Bot

[English](quickstart.md) · [首页](../README.zh-CN.md)

本文对应 **0.1.4b1 实验性 Beta**。首次使用应从离线演示开始，无需账号或密钥；真实配置只在用户明确审核并授权后进行。[支持矩阵](beta-support.zh-CN.md) · [Cursor/Codex 配置](client-compatibility.zh-CN.md) · [常见问题](faq.zh-CN.md)。

## 1. 获取并安装候选源码

先安装 Python 3.12 和 Git for Windows，再打开新的 PowerShell。需要具有仓库访问权限：

```powershell
git clone https://github.com/cherwinyu/grok-bot-mcp.git
Set-Location grok-bot-mcp
.\scripts\setup.ps1 -InstallDir C:\ExampleBridgeCandidate
```

`C:\ExampleBridgeCandidate` 必须是新目录，不是正在使用的安装。脚本用 `python -m pip install <源码目录>` 安装检出的源码，不是假定已有 PyPI 桥发行版；可能下载固定版本 MCP 及其依赖。已有桥安装不会被静默替换。首次依赖安装失败，可用同一命令继续；已有安装过旧时应选择新目录，而不是让向导覆盖更新。

明确指定 Python 时，加 `-Python 'C:\ExamplePython\python.exe'`，替换为真实解释器路径。缺少 Python/Git 会显示修复步骤；不需要激活脚本或全局 MCP 包。如果 PowerShell 策略阻止脚本，使用[高级说明](advanced-setup.md)中的明确源码安装命令，不要关闭整机安全策略。

## 2. 准备小型、已有提交的工作区

另开一个位于源码目录的 PowerShell，复制项目自带计数器示例：

```powershell
Copy-Item -Recurse .\examples\minimal_workspace C:\ExampleWorkspace
git -C C:\ExampleWorkspace init
git -C C:\ExampleWorkspace add .
git -C C:\ExampleWorkspace commit -m 'Initial fictional workspace'
```

目标目录已存在时停止。Git 需要你自己已配置的提交身份，桥不会替你设置。第一次不要使用真实项目，也不要把工作区建在桥源码目录内。

## 3. 通过向导登记

脚本安装后打开向导。可安全退出，然后使用以下命令返回：

```powershell
$BridgePython = 'C:\ExampleBridgeCandidate\Scripts\python.exe'
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace
```

示例中填写根目录 `C:\ExampleWorkspace`；允许修改 `src/counter.py` 和 `tests/test_counter.py`（逐行输入，再输入空行）；保护文件 `README.md`；测试命令 ID 为 `python_unittest`；测试说明为“从登记根目录，用当前安装的 Python 解释器执行 `-m unittest discover -s tests -t .`”。保存前审查展示的登记内容。写许可需要单独明确同意；关闭不表示安装损坏。

自定义登记文件使用 `--registry <绝对路径>`，恢复向导、诊断、领取及取消时须使用同一选择。生成的 MCP 配置已包含该路径。默认位置为 `%LOCALAPPDATA%\grokbot-mcp\registry.json`。

## 4. 集中完成生成的 Grok Bot 清单

向导打印登记文件旁 `setup/demo-workspace` 中的两个本地文件。打开 `routine-setup.md`，在 Grok Bot 一次完成：新建或复用校验／执行 Routine，应用真实工作区、策略和测试映射，核对领取入口，启用 Routine，找到各自不同的 Webhook 凭据。该文件包含私人本机路径，不要附到 Issue。

复用已有两条 Routine 时，重新生成完整说明：

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --routine-mode reuse --generate-only
```

复用保留 Routine/Webhook，但**必须**升级到 `grokbot_mcp.claim_cli`、`claim-evidence-v2`、schema-v2 领取证据及 `claim_sha256`。不支持 `claim_dynamic.py` 原样不变；不能因为存在凭据就宣称升级已验证。

用同一向导命令返回，通过隐藏输入填写各自的 URL/密钥，保存在 Windows Credential Manager；明确确认已核对且启用的每个绑定。已有凭据和绑定会跳过并保留，只有明确指定 `--replace-credentials` 或 `--reconfirm-bindings` 才重新询问，这些选项仍需确认。旧配置没有已保存测试说明时，管理员应在 Grok Bot 提供准确命令映射，向导不会猜测。

## 5. 接入客户端并检查本地状态

把生成的 `mcp-config.json` 复制到客户端 MCP 设置并重启连接。保留其中解释器绝对路径和参数，不要改成通用 `python`。支持包含空格、中文的路径。不同客户端的界面位置可能不同；所需进程配置以生成的 JSON 为准。

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --check
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --check --purpose submit
```

第一条可以在写许可和提交开关关闭时通过；第二条指出明确提交前的阻塞条件。两者都只读。客户端应发现七个工具，并对 `demo-workspace` 调用 `grok_preflight`：必须返回 `webhook_sent=false`、`remote_binding_verified=false`、`qa_accepted=false`。原有 `ready`／`submit_permitted` 仍采用严格提交语义，因此开关关闭时 `ready=false` 是预期结果。

本地配置完成和管理员确认不等于真实远端任务成功。接下来参见[离线／静态示例及人工检查点](offline-demo.zh-CN.md)。安装流程不会发送真实 Webhook。

## Existing installations / 已有实例

Before creating a Routine, identify the actual client connection and inspect its selected configuration. Use [the deployment checklist](deployment.md) / [中文清单](deployment.zh-CN.md) for strict-JSON preview/apply/revert, bounded standalone probe, safe migration and optional agent recovery. A generated config or an independent handshake is not confirmation that the actual client reconnected.

已有 Routine 优先复用；凭据存在不等于绑定正确。真实重连、远端回读声明、真实执行和人工 QA 分开记录；不要为了修复结果格式重发 Webhook 或替换冻结证据。


参见[离线演示](offline-demo.zh-CN.md)和[安全模型](security-model.md)：执行配置、默认禁用的 Canary、凭据维护，以及与真实使用分开的检查。
