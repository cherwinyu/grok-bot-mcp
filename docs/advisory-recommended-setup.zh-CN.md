# advisory 工作区推荐配置

[English](advisory-recommended-setup.md) · [Advisory 工作区](advisory-workspaces.zh-CN.md) · [安全模型](security-model.md)

本文把现有的 advisory 选项组合成一份经审查的起点配置。它不修改任何安装、登记、Routine、客户端、凭据或开关。标注“随同发布”的部分描述与本文同期发布的变更，见[依赖与发布顺序](#依赖与发布顺序)。

## 适用与不适用场景

适用：多名操作者共用一个整仓 checkout，任务较小，且有人审查每一份 diff 并运行独立 QA。

不适用：需要强隔离的场合。桥不是操作系统沙箱；它无法验证云端 Routine 的文本（`remote_binding_verified` 恒为 `false`），看不到范围外的读取和普通被 gitignore 的写入，`qa_accepted` 恒为 `false`。不熟悉的工作应使用一次性或另行受限的工作区。

## 运行安装与开发目录分离

运行用的可编辑安装应指向固定在已发布提交的独立 worktree，开发放在另一目录，避免 MCP 进程加载开发目录中其他会话未经审查的源码改动。此 worktree 存放的是**桥的包源码**，不是登记的任务工作区（链接的任务 worktree 仍受共享对象库拒绝规则约束）。

PowerShell 示例；所有占位符均需替换为已审查的值。以下描述管理员管理安装的操作，本文不会执行这些操作：

```powershell
$BridgePython = '<runtime-venv>\Scripts\python.exe'
$SourceRepo = '<bridge-source-repository>'
$RuntimeSource = '<dedicated-runtime-worktree>'
$PublishedCommit = '<published-commit-sha>'
git -C $SourceRepo worktree add --detach $RuntimeSource $PublishedCommit
& $BridgePython -m pip install --no-deps -e $RuntimeSource
```

所需依赖应另行安装；`--no-deps` 只刷新包安装和可编辑安装元数据。升版时先停止 MCP 进程，把该独立运行 worktree 更新到已审查的已发布提交，再用运行解释器执行 `python -m pip install --no-deps -e <路径>`，随后重启 MCP 进程并核对运行身份。开发改动继续保留在另一目录。

## 推荐组合

| 选项 | 原因 |
|---|---|
| `git_gate: "advisory"` | `local_validation` 的前提；Git 审查发现按 advisory 报告，而非像 strict 那样直接拒绝。 |
| `prefix_ceiling` | 把最宽的目录授权固定在策略指纹中。 |
| `allowed_prefixes` | 上限内的日常授权；用 `admin_cli registry-prefixes` 调整，无需重新粘贴。 |
| `allowed_files: []` | 存在前缀或上限时合法；前缀内的新文件无需重新登记。 |
| `forbidden_files` / `protected_file` | 把代理规则、私有配置、`.git`、`.bridge` 挡在范围外；forbidden 与 protected 路径始终优先。 |
| `local_validation: true` | 桥在本机完成 Validate（仅 advisory）；进入指纹。 |
| 测试命令的 `rerun` | 桥以 `shell=False` 复跑登记的测试并写回执；不进入指纹。 |
| `require_test_output: true` | 缺少实测测试输出的结果记为 `test_evidence_missing`，而不是成功。 |

以下为**虚构**的登记片段，所有值均为占位符，使用前须对照真实项目逐项审查。

**依赖超时上限变更。** 下面片段中 `timeout_cap` 与 `rerun.timeout_seconds` 的 3600 取值，依赖随本文同期发布的超时上限变更（上限 3600）。未包含该变更时，请把 `timeout_cap`、`default_timeout` 改为不超过 600，`rerun.timeout_seconds` 改为不超过 1800，否则登记会被拒绝。

```json
{
  "workspace_id": "example-workspace",
  "root": "<repo-root>",
  "expected_head": "<40-hex-commit>",
  "git_gate": "advisory",
  "local_validation": true,
  "require_test_output": true,
  "allowed_files": [],
  "prefix_ceiling": [
    {"prefix": "src/", "exts": [".py"]},
    {"prefix": "tests/", "exts": [".py"]},
    {"prefix": "docs/", "exts": [".md"]}
  ],
  "allowed_prefixes": [
    {"prefix": "src/example/", "exts": [".py"]},
    {"prefix": "tests/", "exts": [".py"]}
  ],
  "forbidden_files": [".git", ".bridge", "AGENTS.md", "private"],
  "protected_file": "private",
  "test_commands": [
    {"id": "none", "task_types": ["read_only"], "timeout_cap": 180, "default_timeout": 180},
    {"id": "unit", "task_types": ["code_change"], "timeout_cap": 3600, "default_timeout": 1800,
     "argv": ["<bridge-python>", "-B", "-m", "unittest", "tests.test_example"],
     "rerun": {"argv": ["<bridge-python>", "-B", "-m", "unittest", "discover", "-s", "tests"],
               "timeout_seconds": 3600}}
  ],
  "validate_credential_target": "<validate-target>",
  "execute_credential_target": "<execute-target>",
  "writes_enabled": false,
  "expected_machine": "<machine-name>"
}
```

- `prefix_ceiling` 刻意比 `allowed_prefixes` 宽，使授权日后收缩或扩大都不改变指纹。每个生效前缀必须落在某个上限条目内，且扩展名是其子集，否则返回 `prefix_outside_ceiling`。
- `forbidden_files` 显式列出 `.git` 与 `.bridge`；`protected_file` 必须是 forbidden 条目之一。
- `argv` 在 Grok 的环境中运行定向测试；`rerun` 在桥所在机器上运行全量测试，Grok 不必在自己的截止时间内跑完全量。
- `rerun` 只通过对已结算执行调用 `grok_get_status(rerun_tests=True)` 触发；普通查询从不启动测试命令。若给出 `cwd`，必须是根内已存在的相对目录；省略则为登记根。
- 片段中的 3600 仅在含超时上限变更时有效；没有该变更的基线上请使用 `timeout_cap`/`default_timeout` 不超过 600、`rerun.timeout_seconds` 不超过 1800。
- 此处 `writes_enabled` 保持 `false`。开启它以及执行总开关是管理员另行决定的事项。

### 单阶段流程

advisory 加 `local_validation` 时不会向 Grok 提交 Validate，因此只会用到 Execute Routine。`grok_preflight` 的汇总 `ready` 在没有 validate 阶段凭据与绑定时能否为真，未核实；请查看 `phases.execute`。

1. `grok_prepare_task` 写入 manifest（可选传 `execution_timeout`，见下节）。
2. `grok_prepare_execution` 创建执行授权。前提：`writes_enabled` 必须为 `true`；为 `false` 时（如上面的片段）调用会先返回 `workspace_writes_disabled`，到不了本机校验。开启它是管理员在提交窗口内有意进行的操作，不在本文范围内，本文只说明前提与现象。`writes_enabled` 已开启且此前没有 validation 账本和结果时，它会自行完成本机校验。
3. 仅在另行的明确授权及开关就绪后用 `grok_submit_execution` 提交。
4. 轮询 `grok_get_status(execution_id=..., wait_seconds=N)`。`N` 为 1..50 的整数；桥以 1、2、4、5、5… 秒只读轮询，遇到终态或完整已验证结果提前返回。应带退避地重复调用，不要紧循环。
5. 结果为 `test_evidence_missing` 时，调用 `grok_get_status(execution_id=..., rerun_tests=True)`（不能与 `observe_only` 同用）。之后仍需人工审查与独立 QA。

### 轮询预算与兜底复跑窗口

`wait_seconds` 上限为 50 秒，用于保持单次工具调用较短。对应的 MCP 客户端超时限制**未核实**；此上限不能证明客户端存在这样的限制。省略或传零表示不等待。

以五分钟任务、`wait_seconds=50` 为例：每次调用先立即观察，再按 `1、2、4、5、5……` 秒等待，最后一次等待裁剪到剩余预算。忽略观察与调用开销及终态提前返回，`1 + 2 + 4 + 8 × 5 + 3 = 50`：共 12 次等待，在 `0、1、3、7、12、……、47、50` 秒进行 13 次观察。每次新调用都会重新开始退避。因此 `300 / 50 = 6` 次工具调用约覆盖五分钟（只选一个 ID 时约有 `6 × 13 = 78` 次内部观察，含相邻调用边界的观察）。调用间隔与观察开销会改变次数；若结果恰在最后一次观察后到达，还需再调用一次。重复的是只读状态查询，绝不是重新提交。

本机兜底复跑只在**执行完成并结算后、工作区提交（HEAD 前进）之前**有效。HEAD 前进后，冻结证据不再通过当前结算核对，复跑可能返回 `rerun_result_unverified`。应在提交工作区前复跑；保留原结果与账本，不得通过重写证据或重发绕过此窗口。

## 三层超时（随同超时上限变更发布）

| 层级 | 位置 | 限制 |
|---|---|---|
| 全局硬上限 | 桥代码 | 3600 秒（此前为 600） |
| 每条命令 | `timeout_cap`、`default_timeout` | `1 <= default_timeout <= timeout_cap <= 3600`；向导新登记的代码测试命令为 3600/3600，`none` 为 180；现有登记不会自动升级 |
| 每个任务 | `grok_prepare_task(execution_timeout=N)` | 不超过该命令 `timeout_cap` 的正整数；省略则用 `default_timeout` |

`rerun.timeout_seconds` 另计：1..3600，默认 600。

它是什么：**整次远端任务**的截止时间，从已记录的请求尝试（发出 webhook）起算。超过后状态变为 `timeout`，之后到达的结果标记为 `late_result`。它不是 MCP 调用超时：提交只等待 HTTP 接收（60 秒），随即返回。

影响：

- `timeout` 是未决状态，会阻塞冲突的新任务，且绝不授权自动重试或替代身份。
- 上限越高，Grok 无声失败时工作区被占用越久。小任务请显式传较短的 `execution_timeout`。
- Grok 平台单次运行的最长时长未经核实；桥的 3600 秒截止时间不代表平台一定能跑这么久。
- 缺少该字段的旧账本记录仍回退为 600 秒。

## 何时需重新登记并重粘，何时不需要

| 变更 | 指纹是否变化 | 是否需重新登记并重粘 Routine |
|---|---|---|
| `timeout_cap` / `default_timeout`（调高或调低） | 是 | 是 |
| `allowed_files` | 是 | 是 |
| `forbidden_files` / `protected_file` | 是 | 是 |
| `prefix_ceiling` | 是 | 是 |
| `local_validation` 开启或关闭 | 是（该键仅在为 true 时出现，关闭会删除该键） | 是 |
| `require_test_output` 开启或关闭 | 是 | 是 |
| 无上限时的 `allowed_prefixes` | 是 | 是 |
| 上限内通过 `registry-prefixes` 调整 `allowed_prefixes`（有 `prefix_ceiling`） | 否 | 否 |
| 生效前缀下新增文件 | 否 | 否 |
| `rerun`（argv、cwd、env、`timeout_seconds`） | 否 | 否 |
| 任务级 `execution_timeout` | 否（存于 manifest） | 否 |

“重粘”指重新生成 Routine 材料、粘贴到实际 Routine，并在本机确认绑定。策略中的其他字段同样进入指纹，改动时按第一组处理：`git_gate`（非 `strict` 时，因此切换到或离开 `advisory` 都会改变指纹）、`expected_machine`、`execution_profile`（存在时；向导会写入）、`expected_head`、`policy_revision`、凭据目标、`test_selection`、`allow_globs`、`glob_max_files`、`other_prefixes`，以及每条测试命令的 `id`、`task_types`、`argv` 与 `applies_to`。

## 登记后自检

- `python -B -m grokbot_mcp.admin_cli registry-check --registry <registry.json> [--workspace <id>]`（随同 registry-check 变更发布）。只读：对每条 `argv` 与 `rerun.argv`，报告根内 cwd、显式可执行路径、unittest 模块和测试文件为 `ok`、`missing` 或 `unchecked`。裸命令名为 `unchecked`。它不执行任何东西，也不搜索 PATH；只是文件系统层面的提示，不证明测试能运行。
- `grokbot-mcp routine-check --registry <registry.json> --workspace <id>`。只检查本地包，无法验证远端 Routine；加入指纹变更后，粘贴包的 claim 命令缺少 `--expect-fingerprint` 时还会给出警告。
- `grok_preflight`：查看 `runtime_drift`（出现 `process_restart_recommended` 时重启 MCP 进程），以及随 registry-check 变更加入的 `test_command_check`（不改变 `local_ready`）。

## 领取时核对指纹（随同 claim 指纹变更发布）

新的粘贴包会把 Execute 的 claim 命令渲染为带 `--expect-fingerprint <policy_fingerprint from this paste packet>`。当前策略与之不同时，claim 在写入任何内容之前返回 `routine_binding_mismatch`；Routine 必须停止且不改文件，由管理员粘贴更新后的 Routine。这是本地比对，不是对远端 Routine 文本的验证。

## 常见问题

- **`installation_mismatch`**：所选解释器无法加载已安装的桥。不同版本的处理提示不同。当前基线的诊断提示是：另建独立 venv，用该 venv 的 `python -m pip install .` 安装检出的源码，且不要替换正在使用的安装。可编辑安装元数据过期时用该解释器运行 `python -m pip install -e .` 刷新（wheel 安装则重新安装对应 wheel），是 registry-check 变更新增的提示文案（随同发布）；仅在包含该变更时适用。
- **全量测试超过截止时间**：让 Grok 只跑定向测试（`argv`），全量由桥的 `rerun` 复跑。
- **提交身份或 GH007**（因邮箱隐私设置推送被拒）：提交作者使用托管服务提供的 noreply 地址。这是 Git 托管行为，桥不检查，仓库文档也未涉及。

## 依赖与发布顺序

标注“随同发布”的部分描述三项配套变更：3600 秒超时上限、`admin_cli registry-check`（同时带来 `installation_mismatch` 下的 `pip install -e .` 提示）、`claim_cli --expect-fingerprint`。若只合并本文而未合并它们，对应内容不适用：上限仍为 600（`rerun.timeout_seconds` 仍为 1800），因此使用 3600 的示例登记片段会被拒绝，需先把 `timeout_cap` 与 `default_timeout` 降到 600 以内（示例中的 `default_timeout` 为 1800，也必须降低）、`rerun.timeout_seconds` 降到 1800 以内；没有 `registry-check` 与 `test_command_check`；`installation_mismatch` 的提示是基线的 `python -m pip install .`；claim 也不接受 `--expect-fingerprint`。请一并决定合并顺序。
