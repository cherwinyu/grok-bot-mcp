# grok-bot-mcp

**让 Windows 上的 AI 编码任务交接更可审查、更可恢复。** 将范围明确的任务交给自行配置的 Grok Bot Routine，单次领取，用 Git 差异与结构化证据核验交付，而不是相信一句“完成了”。

[English](README.md) · [快速开始](docs/quickstart.zh-CN.md) · [Cursor / Codex CLI](docs/client-compatibility.zh-CN.md) · [Beta 支持范围](docs/beta-support.zh-CN.md) · [安全声明](SECURITY.md)

**0.1.4b1 实验性 Beta 系列。** 是否已正式发行请以 GitHub Releases 为准；单独的源码提交不是正式发行。 MIT；不是 Grok、xAI、Cursor 或 OpenAI 官方产品。目标环境 Windows、Python 3.12、Git。Python 分发名为 `grokbot-mcp-bridge`，导入名为 `grokbot_mcp`；不承诺绕过第三方订阅或调用限额。

## 无需账号的安全离线体验

获取已审核源码后，在新的 Windows PowerShell 和独立虚拟环境中运行，勿覆盖正在使用的安装：

~~~powershell
git clone https://github.com/cherwinyu/grok-bot-mcp.git
Set-Location grok-bot-mcp
py -3.12 -m venv C:\ExampleBridgeBeta
$BridgePython = 'C:\ExampleBridgeBeta\Scripts\python.exe'
& $BridgePython -m pip install .
& $BridgePython -I -X utf8 -m grokbot_mcp.safety_cli demo
~~~

预期 JSON 包含 `"mode": "OFFLINE_SIMULATION"` 和 `"real_webhooks_sent": 0`。它在虚构 Git 项目中使用内存 Webhook/凭据替身，实际执行 2 项 Python 小测试，验证重复领取、冻结结果拒绝，再清理临时文件。**不是远端 Grok Bot 现场验收。** [离线说明](docs/offline-demo.zh-CN.md)。

## 现有能力和硬边界

| 能力 | 作用 | 限制 |
|---|---|---|
| 登记工作区 | 固定 Git 根、允许文件和测试命令 | MCP 调用者不能随意指定文件根 |
| 准备与验证 | 绑定任务、manifest 和 Git 基线 | 校验通过不等于执行批准 |
| 单次领取 | 独立开关、提交账本、排他 claim | `unknown_submission` 不自动重发 |
| 交付核验 | Git 改动、身份、冻结结果 | 本地证据不能证明模型所有行为 |
| 维护恢复 | 预览、备份、迁移、回退 | 不自动覆盖冻结证据 |

保留七个 MCP stdio 工具，不向 MCP 暴露任意 shell 命令工具。

## 云端与 Windows 的执行边界

~~~text
Windows Cursor / Codex --stdio--> 本机 Bridge
                                      |
                                  Webhook
                                      |
                            云端 Grok Bot Routine
                                      |
                            独立批准的桌面命令
                                      |
                             登记的本地 Git 项目
~~~

云端与 Windows 磁盘不能天然共享，真实命令需要用户独立审批，HTTP 200 只是接收。**`advisory_precheck` 为应用层白名单，不是 Windows OS 沙箱。** 外部 Agent 可能有更大权限，不建议用敏感项目测试，也不要为通过演示而打开 Always Allow。[威胁模型](docs/security/THREAT_MODEL.md) · [数据边界](docs/data-boundaries.zh-CN.md)。

## 接入和反馈

可只读生成 Cursor JSON 与 Codex CLI TOML 配置片段；必须自行审阅并导入支持的客户端。[双客户端配置](docs/client-compatibility.zh-CN.md) · [快速开始](docs/quickstart.zh-CN.md) · [支持矩阵](docs/beta-support.zh-CN.md) · [FAQ](docs/faq.zh-CN.md)。

公开 CI 证明已安装 wheel、模拟 stdio 和配置语法，**不代表真实客户端 UI 重连或远端执行通过了公开独立验收**。

[Issue 与反馈](https://github.com/cherwinyu/grok-bot-mcp/issues) · [贡献](CONTRIBUTING.md) · [发行流程](docs/release-process.md) · [MIT](LICENSE)

新仓没有导入私人开发历史。是否有正式 Beta 包请以 GitHub Releases 为准，首版不通过 PyPI 分发。
