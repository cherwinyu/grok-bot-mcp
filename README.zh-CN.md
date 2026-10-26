# grok-bot-mcp

**Windows 上的本地 MCP Bridge：把范围明确的编码任务交给你自配的 Grok Bot Routine 单次执行，再用 Git 差异和结构化证据验收，而不是相信一句「完成了」。**

[English](README.md) · [快速开始](docs/quickstart.zh-CN.md) · [Cursor / Codex CLI](docs/client-compatibility.zh-CN.md) · [Beta 支持范围](docs/beta-support.zh-CN.md) · [安全声明](SECURITY.md)

**实验性 Beta 系列。** 实际发布的内容请以 GitHub Releases 为准；单独的源码提交不是正式发行。采用 MIT 许可；不是 Grok、xAI、Cursor 或 OpenAI 的官方产品。目标环境为 Windows、Python 3.12 和 Git。为保持兼容，分发名为 `grokbot-mcp-bridge`，导入名为 `grokbot_mcp`。它不是托管服务，不是 PyPI 包，也不保证能绕过调用额度限制。

## 我们在做什么

这是一个运行在 Windows 本机的 MCP stdio Bridge。Cursor 或 Codex CLI 把范围明确的编码任务交给**你自己配置**的云端 Grok Bot Routine，且只执行一次；随后 Bridge 用 Git 差异和结构化的冻结证据核验结果，而不是轻信一句「完成了」。Bridge 提供七个 MCP 工具，不暴露任意 shell 或任意根目录的工具。

## 核心思路

- **由管理员登记，调用者不能指定。** Git 根、允许文件和测试命令固定在登记表中，MCP 调用者不能任意指定根目录（[安全模型](docs/security-model.md)）。
- **准备/校验与执行分离。** manifest 校验通过不代表允许执行；执行由独立开关控制，默认关闭。
- **只提交一次。** 校验提交与执行提交各有持久账本，各自最多提交一次；排他 claim 保证每个 execution id 只能被领取一次；`unknown_submission` 绝不重发。
- **先核验，再冻结。** 先核验结果 ID、Git 改动和测试证据，再冻结结果；终态结果不会被覆盖（[结果契约](docs/result-contract.md)）。
- **本地证据只能证明 Bridge 在本地看到的内容。** 它无法证明外部模型做过什么；Bridge 在本机的补充复跑也不会把 `qa_accepted` 置为 true。
- **`advisory_precheck` 是应用层白名单，不是 OS 沙箱。**

设计概览：[架构](docs/architecture.md)。

## 完成度

实验性项目，尚非生产就绪。`src/grokbot_mcp/__init__.py` 中的版本是 `0.1.5`。

**发布情况：** 0.1.5 是本实验线第一个非预发行版本；实际已发布内容以 [GitHub Releases](https://github.com/cherwinyu/grok-bot-mcp/releases) 为准；此前的预发行版为 `v0.1.4b1`。没有 PyPI 包。

**0.1.5 相对 `v0.1.4b1` 的主要变化**（见 [CHANGELOG](CHANGELOG.md)）：
- 可选的目录授权与前缀上限，前缀授权默认排除治理路径；
- advisory `git_gate` 与 advisory 本地校验；
- 有上限的状态等待；
- 针对 `test_evidence_missing` 的 Bridge 本机补充复跑证据（`succeeded_bridge_evidence` / `failed_bridge_evidence`，原结果保持冻结）；
- 执行超时上限 3600 秒；
- 只读的 `admin_cli registry-check` 与 `grok_preflight.test_command_check`；
- `claim_cli --expect-fingerprint`；
- 运行时漂移提示，并建议重启进程；
- `protected_or_forbidden_changed` 会指出所涉及的保护路径或禁止路径；
- 仅作示例的脚本 `scripts/grok_run_example.py`（不是受支持的接口）。

**未完成或未证明：**
- 每次发布的检查证据写在对应 GitHub Release 说明中。
- [ROADMAP](ROADMAP.md) 中的 Stable 里程碑尚未开始：真实客户端/provider 兼容证据、支持矩阵、由 OS 强制的受限执行器、安全响应流程。
- 公开 CI 会检查已安装的 wheel、配置片段格式和虚构的 stdio，但不能证明真实客户端 UI 重连，也不能证明真实 Grok Bot 的结果。

**明确推迟：** PyPI、MCP Registry、其他平台、GUI、公开 shell 执行，以及未知提交后的自动重试。

范围与支持：[Beta 支持范围](docs/beta-support.zh-CN.md)。

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

预期输出的 JSON 包含 `"mode": "OFFLINE_SIMULATION"` 和 `"real_webhooks_sent": 0`。该演示会创建自有的虚构 Git 工作区，使用仅存在于内存中的 Webhook 与凭据替身，实际运行两个小型 Python 测试，检查重复领取和冻结结果，最后清理临时夹具。它不会登录任何账号，也不能证明云端 Routine 完成了这些虚构的工作。[离线说明](docs/offline-demo.zh-CN.md)。

## 能力一览

| 能力 | 作用 | 限制 |
|---|---|---|
| 登记工作区 | 固定 Git 根、允许文件和测试命令 | MCP 调用者不能任意指定根目录 |
| 准备与验证 | 绑定任务身份、manifest 和 Git 基线 | 校验通过不代表批准执行 |
| 单次领取 | 独立开关、提交账本、排他 claim | `unknown_submission` 不会盲目重发 |
| 交付核验 | 核验结果 ID、Git 改动和冻结证据 | 本地证据无法独立证明外部模型的行为 |
| 维护恢复 | 显式迁移、备份、回退 | 不会静默覆盖终态结果 |

## 可选 advisory Git 门

工作区登记可设置 `"git_gate": "advisory"`；省略该字段的已有登记仍是 strict 模式，旧登记文件不会因代码升级而被改写；向导新登记的工作区默认为 `advisory`（用 `--git-gate strict` 改回）。advisory 模式会记录 claim 时的 HEAD，以及此前已有的已跟踪/未跟踪改动；执行后，超出 `allowed_files` 的新增路径或 Agent 上报的路径会记入 `out_of_scope_changes`，但不会仅因此把结果判为失败。准入和 claim 时，会对照当前 HEAD 检查已登记的保护/禁止路径；claim 之后再比对其已记录的哈希。`.git` 不做路径比对，Bridge 自身未跟踪的 `.bridge` 运行目录仍被排除。Git 命令失败、manifest 范围、claim-once、身份校验、冻结证据和不重发检查仍保持 fail-closed。

这种取舍使多个 Agent 可以共用一个 checkout，但 Bridge 无法判断并发改动属于哪个写入方，使用方必须自行复核 Git 历史与 diff。越出声明范围的读取无法被检测；advisory 模式也不是 OS 沙箱。

[整仓 advisory 指南](docs/advisory-workspaces.zh-CN.md)集中说明配置与流程、可选的 `allowed_globs` / `allow_globs`、本地 `routine-check`，以及带审计的 `ledger-archive` 恢复。[测试策略与证据](docs/test-evidence-git-review.zh-CN.md)定义了 `applies_to`、`argv`、`test_selection`、`require_test_output`、`other_prefixes` 和 `git_review`；[加固说明](docs/hardening.zh-CN.md)定义了路径规范化、JSON 大小上限和安全的工具错误。所有选项都保留原有默认值，MCP 工具仍为七个。禁止手工删除账本；唯一受支持的账本归档恢复途径是指南中的 `ledger-archive`。

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

云端 Routine 与 Windows 磁盘并不会自动共享；真实命令需要用户另行批准，HTTP 200 只表示请求已被接收。**`advisory_precheck` 是应用层白名单，不是 Windows OS 沙箱。** 外部工具可能拥有更大的权限，请不要用敏感项目测试，也不要为了让演示通过而打开 Always Allow。[威胁模型](docs/security/THREAT_MODEL.md) · [数据边界](docs/data-boundaries.zh-CN.md)。

## 宿主配置

<a id="host-setup"></a>

用 `client_cli fragment` 生成配置片段（见 [Cursor / Codex CLI](docs/client-compatibility.zh-CN.md)）。下面的路径自检必须与后续命令在**同一个** PowerShell 进程中运行；之后新开的后台 shell 不会继承先前命令设置的变量。

~~~powershell
$BridgePython = 'C:\Example Bridge\Scripts\python.exe'
$Registry = 'C:\Example Config\registry.json'
if (-not (Test-Path -LiteralPath $BridgePython)) { throw 'python_path_missing' }
if (-not (Test-Path -LiteralPath $Registry)) { throw 'registry_path_missing' }
& $BridgePython -I -X utf8 -c "import grokbot_mcp; print(grokbot_mcp.__version__)"
~~~

Cursor `mcp.json`：

~~~json
{
  "mcpServers": {
    "grokbot-mcp": {
      "command": "C:\\Example Bridge\\Scripts\\python.exe",
      "args": ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"],
      "env": { "GROKBOT_MCP_REGISTRY": "C:\\Example Config\\registry.json" }
    }
  }
}
~~~

Codex CLI `config.toml`：

~~~toml
[mcp_servers."grokbot-mcp"]
command = "C:\\Example Bridge\\Scripts\\python.exe"
args = ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"]
env = { GROKBOT_MCP_REGISTRY = "C:\\Example Config\\registry.json" }
~~~

登记文件不存在时，`grok_list_workspaces` 返回 `ok: false`、`error: registry_missing`、`workspaces: null`。这不等于「已登记但没有工作区」。

先把结果写入固定的临时候选文件，再调用 `result_cli`。不要把 JSON 直接传给 `python -c`。两个阶段的文件名都是 `.<id>.result.tmp`：validate 阶段位于 `%TEMP%\grokbot-mcp-candidates\validate\`，execute 阶段位于 `...\execute\`。辅助命令随后会暂存为 `.bridge/results_v2/.<task-id>.result.tmp` 或 `.bridge/results_execute/.<execution-id>.result.tmp`。`.result.candidate.json` 不是协议规定的文件名。

~~~powershell
$Phase = 'validate'   # 或 execute
$Id = '<收到的 task_id 或 execution_id>'
$candidateDir = Join-Path $env:TEMP ("grokbot-mcp-candidates\" + $Phase)
New-Item -ItemType Directory -Force -Path $candidateDir | Out-Null
$candidate = Join-Path $candidateDir ('.' + $Id + '.result.tmp')
$json = @'
{"protocol_version": 1}
'@
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($candidate, $json, $utf8)
& $BridgePython -I -X utf8 -m grokbot_mcp.result_cli --registry $Registry --workspace demo-workspace --phase $Phase --id $Id --source $candidate
~~~

把 here-string 换成实测结果，再按[结果写回](routines/result-writing.md)执行 `--stage` 和 `--publish`。新的 manifest 包含 `constraint_version`；缺少该字段的历史 manifest 仍可核验。不要要求 manifest 与当前的约束正文完全一致。

本仓库不提供宿主侧的范围纠正框架。失败收据会保留：不要手工删除，也不要换一个新 ID 重发同一次提交。

| 失败 | Bridge 的行为 | 本仓库中的入口 |
|---|---|---|
| `registry_missing` | `grok_list_workspaces` 返回 `ok: false`，`workspaces` 为 null | 在当前进程中把 `GROKBOT_MCP_REGISTRY` 指向一个存在的登记文件 |
| 发布前的格式或身份错误 | 拒绝发布，最终结果和提交账本保持不变 | 修改临时候选文件后，对同一个 ID 再次运行 `result_cli`，不要重发 |
| 终态或已冻结的结果 | 拒绝替换，失败收据保留 | 此处没有纠正工具，不要删除或改写收据 |
| `unknown_submission` | 不会重发 | 没有纠正入口，请观察原任务 |
| 终态发布后残留的暂存或临时文件 | 只删除该 ID 的 `.<id>.result.tmp` 暂存文件，以及字节一致的临时候选文件，并写入 `.bridge/protocol_cleanup/<phase>/<id>.json` | `result_cli --phase validate --id <task-id> --cleanup`，execute 阶段则用 `--phase execute` 加 execution id。不会删除已发布的 JSON、提交账本、其他 ID 的文件或 `.result.candidate.json` |

## 接入和反馈

可以只读方式生成 Cursor JSON 与 Codex CLI TOML 配置片段；请自行审阅，并导入受支持的客户端。[双客户端配置](docs/client-compatibility.zh-CN.md) · [快速开始](docs/quickstart.zh-CN.md) · [支持矩阵](docs/beta-support.zh-CN.md) · [FAQ](docs/faq.zh-CN.md)。

CI 覆盖范围的限制见[完成度](#完成度)。

[Issue 与反馈](https://github.com/cherwinyu/grok-bot-mcp/issues) · [贡献](CONTRIBUTING.md) · [发行流程](docs/release-process.md) · [MIT](LICENSE)

本仓库不包含私有的开发 Git 历史及内部任务日志。已发布的内容见[完成度](#完成度)。
