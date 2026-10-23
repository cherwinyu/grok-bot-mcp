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

## 三层超时

桥的全局硬上限为 3600 秒。管理员在此范围内为每条命令登记 `timeout_cap`
和 `default_timeout`，满足 `1 <= default_timeout <= timeout_cap <= 3600`。
向导新登记的代码测试命令两者默认均为 3600；`none` / read_only 命令仍为 180。
现有登记不会自动升级。这些登记值参与策略指纹，修改后需要重新审核绑定。

单次任务由 Director 通过 `grok_prepare_task(execution_timeout=N, ...)` 指定，
N 必须是正整数且不超过该命令的 `timeout_cap`；省略时使用 `default_timeout`。
它决定从已记录请求尝试时间起算的整次远端任务截止时间，并非仅测试子进程的超时。
桥本机复跑另用 `timeout_seconds`，允许 1～3600 秒，缺省仍为 600。

`timeout` 仍是未解决状态，工作区继续被占用并阻止冲突任务。超时不授权重发；
同一已 claim 执行的合法迟到结果仍可核验。Grok 平台单次运行时长上限尚未核实，
桥的上限不能证明平台的上限。

## 受限目录展开

`grok_prepare_task` 增加可选 `allowed_globs`，例如 `["src/**/*.py", "docs/**"]`。继续传入 `allowed_files`（全部用 glob 选择时传 `[]`）、测试命令 ID 及原有必填参数。仅接受 `dir/**` 和 `dir/**/*.ext`，目录前缀与扩展名必须是字面量。绝对路径、盘符、反斜杠、遍历、空或点路径段、字符类、其它通配符、第二个或位置不合规的 `**` 均拒绝。

展开只读取本地 Git 索引，与 registry 的精确文件或前缀策略取交集，结果只包含已存在的普通文件。未跟踪、已删除、禁止/保护路径的后代及含 `.git`/`.bridge` 路径段的文件均排除。命中的符号链接、联结点/reparse 路径、硬链接、子模块及未解决的索引阶段会被拒绝。不做文件系统 glob 遍历，也不扩大策略授权。无匹配即报错；展开结果及其与显式文件的最终并集均须在配置上限内，超限整体拒绝，不截断。

manifest 的 `allowed_files` 仍是显式清单。启用展开的 manifest 另记 `scope_expansion.allowed_globs` 和 `scope_expansion.expanded_files`，提交时复制到验证任务账本。prepare 返回完整最终清单和展开审计，并给出 `action_required: "confirm_expanded_files"` 及含文件数的操作提示。显式提交验证前必须复核完整清单；此提示不会建立新的授权机制。既有仅用显式文件的调用行为与返回字段不变。

## 目录级授权

registry 可选配置 `"allowed_prefixes": [{"prefix": "docs/", "exts": [".md"]}]`。
`allowed_files` 和 `allowed_prefixes` 各自可以为空，但不能同时为空。prefix 是以 `/`
结尾的字面量相对目录，exts 是非空、以 `.` 开头的字面量后缀清单。路径做 Unicode NFC
规范化；拒绝遍历、空/点段、盘符、反斜杠、通配符、Windows 设备别名及 `.git`/`.bridge`
路径段，保留已有路径准确拼写和符号链接、联结点/reparse、硬链接检查。`.cursor`、
`AGENTS.md` 及私人路径由管理员配置到 `forbidden_files`。

准备新文件时传入 `grok_prepare_task(..., allowed_files=[], create_files=["docs/new.md"], ...)`。
每个新文件必须同时匹配前缀和扩展名，且不能覆盖 forbidden/protected 禁区。最终任务清单是
调用者所选 `allowed_files`、`create_files`、`allowed_globs` 索引展开结果的并集；glob
匹配现在可由登记文件或前缀授权。收到 `action_required: "confirm_expanded_files"` 时
逐项审核完整返回清单；manifest 在 `scope_expansion.create_files` 记录声明。新建数量和
最终并集均受 `glob_max_files` 限制，超限整体拒绝。

非空前缀集合按规范化排序 JSON 参与 `policy_fingerprint`；缺省空集合不改变旧指纹。
在已授权前缀内新增磁盘文件不改指纹、不要求重新绑定。修改登记的精确文件例外或其它策略
字段仍改变策略身份。启用前缀的 Routine 包只内嵌前缀集合与指纹，不内嵌完整登记文件清单；
具体任务范围及新建声明以成功 claim JSON 为准，`routine-check` 核验一致性。未设置前缀时
保留旧包、manifest 和结果格式。

claim 快照包含前缀内已跟踪及未忽略的未跟踪文件。不会仅因目录授权就把普通 gitignored
缓存树写入账本；显式监测的禁止/保护/任务路径仍保留检查。执行后观察到、但未由代理报告
的任务外新增文件，advisory 记为 `out_of_scope_changes`，strict 拒绝。代理在 `changed_files`
报告的新建文件必须在任务清单内。声明后仍未创建的文件由桥接器在执行摘要列为 `not_created`，
仅声明了 `create_files` 的任务出现此字段，缺失本身不判失败。claim-once、冻结证据、禁止
重发的规则不变。

管理员先只读预览目录折叠建议：

```powershell
grokbot-mcp registry-prefixes --registry $Registry --workspace $Workspace
# 审核扩大的目录授权后，可在登记工作区外指定一个尚不存在的副本：
grokbot-mcp registry-prefixes --registry $Registry --workspace $Workspace --write-to $NewRegistryCopy
```

辅助命令按直接父目录和扩展名分组，根目录文件及无扩展名文件保留为精确例外，打印新旧
指纹。缺省只读；`--write-to` 仅排他新建副本，不覆盖原 registry 或已有目标、不读凭据、
不发送请求、不改绑定或开关。采用副本及确认受影响的 Routine 绑定属于另行的管理员操作。
目录授权扩大的是应用层策略，不是 OS 权限，也不是 OS 沙箱。

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

## 前缀上限

可选 `prefix_ceiling` 与 `allowed_prefixes` 的格式和规范化规则相同，例如
`[{"prefix":"docs/","exts":[".md",".txt"]}]`。每个生效前缀必须等于或位于某个上限
前缀之下，扩展名集合必须是同一个上限条目的子集。越界报 `prefix_outside_ceiling`，
且不会写入任何文件。精确 `allowed_files` 例外保持原语义；禁区和保护路径仍优先。
有上限时允许空生效前缀集合，表示没有目录授权。

有上限时，策略指纹和生成的 Routine profile 嵌入上限，生效前缀不进入指纹。
修改上限、禁区或保护策略仍需更新并重粘 Routine、明确确认绑定；上限内扩缩不需重粘。
没有上限时保留原有指纹和 Routine 策略正文行为，生成材料的版本标记随版本升级。

准备任务时把当时生效的前缀冻结为 manifest 的 `active_prefixes`。claim 和结果核对都用
该快照，不用 registry 最新生效值。claim 再检查「快照属于当前上限」，不满足时报
`active_prefixes_outside_ceiling`，成功时返回 `active_prefixes` 和 `active_prefixes_sha256`。
哈希为 UTF-8 JSON 的 SHA-256：键排序、ASCII 转义、紧凑逗号和冒号分隔。
Routine 发现 claim 前缀超出其嵌入上限、或哈希不符时必须停止。收缩生效前缀只影响
后续任务，不撤销已准备任务的冻结授权。

扩展现有本地 `registry-prefixes` 命令，支持重复传入 `--add`、`--remove`。
先移除再添加；添加已有前缀会合并扩展名。要替换扩展名集合，可在同一命令里先移除
该前缀再添加。以下均为虚构路径，先预览，再明确应用同一操作：

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.admin_cli registry-prefixes --registry 'C:\ExampleBridge\registry.json' --workspace example-workspace --add 'docs/:.md,.txt' --remove 'docs/old/'
# 审查预览后，明确应用：
& $BridgePython -I -X utf8 -m grokbot_mcp.admin_cli registry-prefixes --registry 'C:\ExampleBridge\registry.json' --workspace example-workspace --add 'docs/:.md,.txt' --remove 'docs/old/' --apply
```

不带 `--apply` 不写文件。应用时先按原字节备份为同目录的
`registry.json.bak_<UTC时间戳>`，原子替换 registry，再追加同目录 `policy_changes.jsonl`。
审计包含 UTC 时间、工作区、前后前缀集合和前后指纹；ceiling 模式下指纹相同。
命令不读取凭据、不修改绑定或开关。审计日志是事后追溯手段，不是闸门；注册表替换与
审计追加不是跨文件原子事务，进程中断可能使已应用操作缺少审计行。
`policy_audit_write_failed` 且 `applied: true` 表示注册表已经提交。
不带编辑参数时仍保留旧建议/副本模式；编辑参数不能与 `--write-to` 合用。这不是 OS 沙箱。

预检与工作区列表展示上限和当前生效前缀。预检只读展示 `stale_execution_records`，不落盘。
准备新执行时，已到结果通过普通状态查询的同一结算路径落盘；终态失败不再阻塞新任务。
仍在途则返回 `concurrent_execution`、`blocking_execution_id` 与中英文提示；未知提交仍禁止重发。
`runtime_drift` 比较启动时和当前的包、注册表哈希；包文件变化只在 `warnings` 中增加
`process_restart_recommended`，不改变 `blocking_reasons`。安装更新后应重启 MCP 进程并核对身份；
这些提示不授予执行权限，也不证明远端可达。
## 本地校验

管理员可为 advisory 工作区登记 `"local_validation": true`；缺省为 false，
strict 模式不允许启用。只有 true 进入策略指纹；省略或 false 保持原指纹及
Routine 内容（版本横幅除外）。启用会改变策略身份，需重新审核并确认 execute 绑定。

启用后，`grok_submit_validation` 在本机检查 manifest，不读取凭据、不发 webhook，
因此不需要执行开关；发布证据仍要求 `writes_enabled`。`grok_prepare_execution`
仅在该任务既无校验账本又无结果时自动校验，已有结果、未知提交和冻结失败沿用原处理，不能重发。

本机复用 admission 的身份、哈希、禁区、精确范围及冻结前缀上限检查。HEAD 保持 advisory
语义：与登记 HEAD 不同本身不拒绝；Git 无法读取、无效 manifest 基线和保护路径变化仍拒绝。
claim 继续全量核对基线和范围。本地接纳时记录哈希，并在 assessment 重读比较；这不是
对更早文件字节的外部证明。

结果写入 `.bridge/results_v2/<task_id>.json`，复用原子发布及普通读取、校验、冻结流程。
结果附 `validator: "bridge_local"` 和策略指纹；失败写 `status: "failed"` 与
`validation_errors`。提交返回 `webhook_sent: false`、`validated_locally: true`。
claim 和执行核对要求当前仍启用本地校验且指纹一致，否则返回
`local_validation_policy_mismatch`；无 validator 的旧结果保持原语义。

`grok_get_status` 可传整数 `wait_seconds`（0～50），默认 0 保持原返回。
非零时以 1、2、4、5、5… 秒退避只读轮询，最后一次等待不超过剩余时间；遇到终态或
完整且通过校验的结果立即返回，并附 `waited_ms`。等待后按原选项执行一次普通查询。
`observe_only` 全程只读；与 `rerun_tests` 合用时先等待，再结算查询，仅终态执行本机复跑。
`rerun_tests` 与 `observe_only` 仍不能合用。

## 缺失测试输出与桥复跑补证据

执行被冻结为 `test_evidence_missing` 时，只有显式调用
`grok_get_status(rerun_tests=True)` 才可能运行该任务登记的 `rerun` 命令。
桥重新核验身份、claim、策略、范围、changed_files 和 git_review，并继续检查原来
缺输出检查之后的全部成功条件。Grok 原结果必须为成功、失败数为零、退出码为零。
未登记 rerun 时仍保持缺证据状态。其他拒绝不能借此转为补证据成功；WP2 已有的
诊断复跑规则不变，包括对已结算测试失败的诊断比较。

复跑沿用 argv 白名单、`shell=False`、超时和
`.bridge/bridge_test_runs/<execution_id>.<序号>.json` 追加收据机制。
通过且退出码一致时，返回 `succeeded_bridge_evidence`，并标注
`evidence_source: "bridge_rerun"`；不一致、超时或工作区漂移记为
`failed_bridge_evidence`。同一补证据执行复用第一份收据，失败收据也不会被覆盖或
再次运行。普通查询、`observe_only`、`verify_current` 不会启动测试命令。

原 `.bridge/results_execute/<id>.json` 和冻结执行账本均不改写，
`historical_status` 仍为 `test_evidence_missing`。新结算是桥在本机观察到的证据，
不证明 Grok 曾产生输出或曾运行历史测试。`reported_success` 保持原决策，
`qa_accepted` 恒为 false。`verify_current` 核验补证据快照；普通查询仅呈现历史证据。
仍需人工审查，不允许重发或创建替代身份。

开启 `require_test_output` 后，生成的 Execute 正文直接列出 `tests.output`、
`tests.output_sha256`、`tests.output_bytes`，说明使用
`grokbot_mcp.result_contract.output_evidence` 构造，以及未运行时诚实填写
`not_run=true` 的失败方式。需人工重新粘贴 **Execute** Routine；本次变化不要求
修改 Validate 正文，也不会自动修改真实 Routine。模板文本不参与
`policy_fingerprint`；绑定核验策略和能力而非正文哈希，因此模板修正不改变指纹。
false/省略时，在相同版本横幅下字节保持一致；升级到 0.1.5b3 会更新生成材料的版本横幅。
