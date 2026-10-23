# 整仓工作区（advisory）指南

[English](advisory-workspaces.md) · [安全模型](security-model.md)

## 适用场景

适合多个操作员共用 checkout，且有人能逐项复核 diff 并做独立 QA 的项目。整仓工作区是指登记的 Git 根覆盖项目，不意味着所有文件都可写。仍需显式文件白名单及保护路径。对陌生任务，优先使用可丢弃或另行限制权限的工作区。profile 是配置声明，不是桌面可达性证明，也不是 OS 沙箱。

## 配置

管理员在本地审核以下工作区字段；本指南不会修改安装、Routine、客户端、凭据或开关：

- `git_gate: "advisory"` 启用[advisory 检查](security-model.md#strict-and-advisory-git-gates)，省略时仍为 `strict`。
- `allowed_files` 仍为显式登记文件清单；`forbidden_files` 和 `protected_file` 排除指定路径及其后代。将私人配置、凭据、`.git`、`.bridge` 纳入适当保护；glob 始终排除后两者。
- `other_prefixes`、`test_selection`、`test_commands[].applies_to`、`test_commands[].argv`、`require_test_output` 的定义集中在[测试策略、输出证据与 Git 复核](test-evidence-git-review.zh-CN.md)。前缀标签不授予改动权限。建议需要复核的项目要求测试输出，并明确选择测试 ID。
- `allow_globs` 缺省 `false`；`glob_max_files` 缺省 `200`，必须为 `1..2000` 的整数，硬上限为 2000 个文件。非默认值参与策略指纹，需重新审核绑定。

登记路径、测试命令及文件必须对应管理员审核过的实际项目。不要未经审核把示例粘入真实 registry。U1 的路径规范化、编码、安全错误码及 4 MiB JSON 上限集中在[加固说明](hardening.zh-CN.md)。

## 受限目录展开

`grok_prepare_task` 增加可选 `allowed_globs`，例如 `["src/**/*.py", "docs/**"]`。继续传入 `allowed_files`（全部用 glob 选择时传 `[]`）、测试命令 ID 及原有必填参数。仅接受 `dir/**` 和 `dir/**/*.ext`，目录前缀与扩展名必须是字面量。绝对路径、盘符、反斜杠、遍历、空或点路径段、字符类、其它通配符、第二个或位置不合规的 `**` 均拒绝。

展开只读取本地 Git 索引，与 registry 的显式白名单取交集，结果只包含已存在的普通文件。未跟踪、已删除、禁止/保护路径的后代及含 `.git`/`.bridge` 路径段的文件均排除。命中的符号链接、联结点/reparse 路径、硬链接、子模块及未解决的索引阶段会被拒绝。不做文件系统 glob 遍历，也不扩大策略授权。无匹配即报错；展开结果及其与显式文件的最终并集均须在配置上限内，超限整体拒绝，不截断。

manifest 的 `allowed_files` 仍是显式清单。启用展开的 manifest 另记 `scope_expansion.allowed_globs` 和 `scope_expansion.expanded_files`，提交时复制到验证任务账本。prepare 返回完整最终清单和展开审计，并给出 `action_required: "confirm_expanded_files"` 及含文件数的操作提示。显式提交验证前必须复核完整清单；此提示不会建立新的授权机制。既有仅用显式文件的调用行为与返回字段不变。

## 本地 Routine 预检

使用管理员 CLI（不是第八个 MCP 工具）：

```powershell
grokbot-mcp routine-check --registry $Registry --workspace $Workspace
# 使用明确选择的安装时，等价入口为：
& $BridgePython -I -X utf8 -m grokbot_mcp.admin_cli routine-check --registry $Registry --workspace $Workspace
```

只读取 registry 同级已知位置 `setup/<workspace_id>/routine-setup.md`，以及本地迁移标记指向的当前迁移 `routine-update.md`。检查残留 `<ADMIN_*>`/渲染占位符、profile 与测试命令 ID、工作区 ID/根目录、策略指纹、registry/Python 路径及生成模板段落。缺失或被自定义修改的材料需人工复核。Python 对照本地绑定登记的解释器；未记录解释器的旧绑定则对照当前运行解释器。

validate/execute 状态确认必须匹配当前策略及绑定，确认时间有效且不在未来。“当前”仅指策略一致性，不保证远端新鲜度，也没有按时间自动过期的承诺。输出明确声明：**无法验证远端 Routine 文本，只能验证本地包与登记值。** 不读取凭据值，不枚举解释器位置，不发送请求。有本地包时，`grok_preflight` 增加 `routine_check` 摘要，既有 readiness 字段含义不变。超时且未观察到任何结果时，只读 diagnostics 增加 `operator_message`，提示可能未填 Routine 登记值并指向此检查；这不代表已诊断远端账号。

## 基线 → 执行 → 复核 → 独立 QA

1. 记录 HEAD、已跟踪/未跟踪改动及保护路径。审核 registry 策略、所选安装和 `routine-check`，人工对照真实远端 Routine。配置或检查通过都不授权发送。
2. 准备范围窄的 manifest；使用 glob 时逐项确认展开文件及数量。按真实含义选择已登记测试 ID。通过既有独立门槛完成验证、授权、提交，每个身份仅一次。
3. 复核 claim 时基线、结果身份、原生 Git diff、`out_of_scope_changes`、`git_review`、忽略文件写入及实测测试输出。人工分辨其他写入者的贡献。HTTP 200 和本地发布均不代表验收。
4. 在合适环境运行独立 QA。`qa_accepted` 始终为 false。检查全绿不授权提交、发行、公开可见性或部署；各自须有相应授权。

## 故障与有审计的恢复

**禁止手工删除账本；`ledger-archive` 是唯一受支持的账本归档恢复路径。** 既有“可证明从未使用的授权取消”仍是独立操作。归档退休原身份，不重试，不为重复执行未决工作创建替代身份，不改冻结失败字节，不修补结果，不取消远端执行，也不开启开关。

先检查原任务和远端 Routine。超时仍可能有迟到结果。先在本地预览指定任务：

```powershell
grokbot-mcp ledger-archive --registry $Registry --workspace $Workspace --task-id $TaskId --reason '已复核本地绑定故障，退休此任务'
# 仅在复核预览后：
grokbot-mcp ledger-archive --registry $Registry --workspace $Workspace --task-id $TaskId --reason '已复核本地绑定故障，退休此任务' --confirm
# 非终态任务/执行还必须加 --accept-late-result-risk。
```

不带 `--confirm` 时只读，不创建锁，打印计划移动项及需要的确认。保守地将所有非终态记录视为仍可能迟到，包括 timeout 和 unknown submission。额外风险标志不授权重发。任务必须有可读、相互匹配的账本与 task spec；损坏或不可核验的证据直接拒绝，不替其“修复”。

确认后，在既有验证和执行锁内复检，将 task spec、验证账本及全部关联 execution 账本移动到 registry 同级 `archive/<workspace_id>/<UTC 时间>_<task_id>/`。保留各原文件字节及 SHA-256。`audit.json` 排他创建且永不改写，记载原因、本地操作者名、UTC 时间、当时状态、风险接受及原文件哈希。这是应用层不可改写，不是 OS 管理员身份认证，也不能防止管理员手工篡改。归档应保密。结果、claim、基线和清理收据均留在原位置，字节不变。

移动前，先创建不可改写的 `.bridge/ledger_archives/<task_id>.json` 日志，使原 task/execution ID 在 manifest 加载、发布、观察和账本复用时永久拒绝。迟到结果报告 `archived_task`，不予接受。完整完成后，原记录离开阻塞账本集合，并写独立完成收据；随后只可进行另行授权的**新工作**，不能重放已归档提交。

移动中断会留下工作区拒绝屏障。使用同一个 `ledger-archive` 命令，加确认及必要的迟到风险接受，仅续完原哈希绑定计划，不改原审计。证据变动时拒绝续完。保留日志、归档和收据，禁止手工去除屏障。本实现使用 Windows rename，不做复制后删除的回退；registry 归档与工作区必须位于同一卷。跨卷或权限失败会保留证据，可能需排除文件系统问题后续完原计划；不要移动真实 registry 或证据来绕过拒绝。

## 已知限制

越界读取不可检测；普通 gitignored 写入不进入 advisory Git 复核，登记的保护/禁止路径仍受监测。并行改动无法归因，需独立复核。远端结果 JSON 必须为 UTF-8，路径规范化且身份匹配；不要猜测修复乱码路径或替换冻结结果。测试 ID 是管理员映射，不是任意 shell 命令；`test_command_cli` 仅预览批准参数，不执行。本地一致性不能证明远端 Routine 文本、桌面连通性或模型行为。
