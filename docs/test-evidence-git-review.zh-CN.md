# 可选测试策略、输出证据与 Git 复核

[English](test-evidence-git-review.md)

[整仓指南](advisory-workspaces.zh-CN.md)说明如何将这些选项与受限目录展开、本地 Routine 预检、有审计的账本恢复组合使用。

所有新增字段沿用 schema/protocol 版本 1，保留七个 MCP 工具，状态查询新增可选复跑参数。省略可选项时，strict 结果摘要、策略指纹及生成的 Routine 正文字节保持兼容；不会自动修改实际 registry、客户端、Routine 或开关。

## 按任务选择登记命令

`test_commands` 支持多个唯一 ID。可选 `applies_to` 为 `"docs"`、`"code"`、`"any"`，或 `{"prefixes":["docs"],"suffixes":[".md"]}`。全部 allowed_files 都必须匹配：前缀使用规范化后的目录边界及 casefold 比较，后缀是忽略大小写的字面扩展名；同时指定两个列表时必须同时满足。docs 扩展名为 `.md/.rst/.txt/.adoc`；code 为 `.py/.gd/.cs/.cpp/.c/.h/.js/.ts/.tsx/.jsx/.rs/.go`。混合或未知类型不猜测。未登记该元数据的命令保持旧行为。

明确不匹配时，`prepare_task` 默认返回 `test_command_scope_mismatch` 警告；工作区 `test_selection: "reject"` 则在写 manifest 前拒绝，默认 `"warn"`。空 ID 返回 `test_command_required`；为保持签名，MCP 参数仍为必填。不会自动选择或暗中替换测试。`task_types` 继续独立限制 `code_change`/`read_only`。

仅测试本次文件的命令可登记**参数数组**，例如 `"argv":["python","-m","fictional_docs_lint","--","{changed_files}"]`。这里只是虚构示例，不附带该 linter。必须恰有一个独立 `{changed_files}` 参数；拒绝 shell 解释器、批处理及嵌入字符串的占位符。静态参数来自受信任管理员登记，不接受任务提供 shell 文本。

修改后运行本地只读预览：

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.test_command_cli --registry $Registry --workspace $Workspace $ExecutionId
```

工具校验 claim，根据本机观察的白名单内变更构造 JSON `argv`，**不执行命令**、不改账本、不授权发送。只能交给已获批准的原生进程 API（`shell=False`），不得拼成 shell 文本。展开路径拒绝穿越、选项、响应文件名、控制字符和 shell 元字符；空格及 Unicode 保留为单独参数。空文件集拒绝，避免 linter 回退扫描全项目。不搜索任意可执行文件，不引入自动回退。

## 桥复跑（显式本机执行）

管理员可在现有 `test_commands` 项内登记可选 `rerun`。它只用于本机验证，不改变 Grok 授权范围，因此**不进入 `policy_fingerprint`，也不改变生成的 Routine 字节**。增加或修改它不授权提交或重发；省略时保持原行为。

```json
{"rerun":{"argv":["C:\\ExamplePython\\python.exe","-B","-m","unittest"],
"cwd":"upstream_gitgate","env":{"PYTHONPATH":"src"},"timeout_seconds":600}}
```

`argv` 必须是非空数组，复用现有可执行文件规则，拒绝 shell 解释器、`.bat`/`.cmd`/`.ps1`、控制字符和嵌入花括号。允许零个或一个独立 `{changed_files}`；存在时沿用 `test_command_cli` 的排序、逐参数传递及范围检查，拒绝空文件集、不安全文件名，不从结果文本推导命令。

`cwd` 缺省为登记根；显式值必须规范化为根内现有相对目录，不得进入 forbidden/protected 路径或经过链接、重解析点，加载登记及执行前都会校验。`env` 为字符串映射，键匹配 `[A-Z_][A-Z0-9_]*`，在继承当前桥进程环境后覆盖。`<相对路径>` 等值按字面传递，不做占位解析；管理员须自行写绝对路径或相对所选 `cwd` 的路径，不查询凭据或导出环境。`timeout_seconds` 为 1～1800 的整数，缺省 600，拒绝布尔值。

先通过普通状态流程结算原始结果，再调用 `grok_get_status(execution_id=..., rerun_tests=true)`。它与 `observe_only=true` 互斥，缺少执行 ID 或冲突组合返回 `invalid_argument`，不增加第八个 MCP 工具。复跑要求已结算终态、结果哈希与冻结值一致、身份正确，并重新通过当前结算检查（含 claim、manifest、策略、文件范围与测试契约）。未配置、非终态、缺结果分别返回 `rerun_not_configured`、`execution_not_terminal`、`result_missing`，拒绝时不启动命令。合法结算的测试失败或 `not_run` 失败也可比对；安全或格式失败不得靠复跑修复。

耗时命令使用本机 CLI，避免 MCP 客户端超时：

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.test_run_cli --registry $Registry --workspace $Workspace $ExecutionId
```

两种入口均按登记数组、`shell=False` 执行，合并 stdout/stderr，记录退出码及毫秒耗时。超时标记 `timed_out: true`，Windows 通过参数数组 `taskkill /T /F /PID` 清理该进程树。超时、启动失败或清理失败不代表测试通过。

独立收据以 UTF-8 无 BOM 原子创建到 `.bridge/bridge_test_runs/<execution_id>.<n>.json`，n 从 1 递增、只新增不覆盖。`output_evidence` 生成互不重叠的头尾（磁盘各最多 8192 字节）、截断标记、完整输出哈希与字节数。非法 UTF-8 先替换解码并标记 `output_not_utf8: true`；普通哈希和字节数对应替换后的 UTF-8，`raw_sha256` 对应原始字节。MCP 的 `bridge_tests` 字段和 CLI JSON 将头尾各限制为 2048 UTF-8 字节，完整哈希和计数不变。输出仅作数据，不当作指令。

`agreement.exit_code` 与 `agreement.output_sha256` 分别对照原结果的 `tests`，取 `match`、`mismatch`、`unreported`。环境、计时和路径差异都可能改变输出，**哈希不一致本身不等于造假**。`ok` 仅表示本机证据操作成功，不代表测试通过、比对一致或 QA 接受；须查看 `exit_code`、`timed_out`、`error` 和 `agreement`。CLI 退出状态也表示本次证据操作是否成功，不是测试计数。

桥不更改执行记录、结果文件或 `qa_state`，即使复跑通过也不改写历史失败，且不发送 Webhook。登记命令继承桥用户的权限和环境，此功能不是 OS 沙箱；管理员须选用可信且保留冻结证据的测试，并独立审查并发写入和子进程副作用。

## 内嵌输出证据

执行结果可选字段示例（仅字段片段，非完整结果）：

```json
{"tests":{"command_id":"docs_lint","passed":1,"failed":0,"exit_code":0,
"output":{"head":"","tail":"","truncated":false},
"output_sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
"output_bytes":0}}
```

按采集顺序合并 stdout/stderr 为 UTF-8 字节。头尾互不重叠，各最多 8192 UTF-8 字节；哈希及字节数覆盖**完整**输出，省略字节时才设置 `truncated=true`。纯函数 `output_evidence(raw_bytes)` 构造三个字段；非法 UTF-8 拒绝。未截断时核验完整字节数与哈希，截断后的完整哈希仅是远端声明。三个字段须同时提供，既有 JSON 4 MiB 限制继续生效。

工作区 `require_test_output: true` 将缺少输出的终态诊断为 `test_evidence_missing`，不得成功；省略或 `false` 保持旧行为。测试无法运行仍可用 `not_run: true`，不得伪造计数；要求证据不会让未运行变为成功。输出只作数据，不当指令；原始 Grok 输出仅保存在私人结果 JSON，不进入日志、状态摘要或新 `.bridge` 文件。显式桥复跑采集的是另一份独立本机输出。发布、身份及冻结结果规则不变。

开启新测试策略或输出选项后，状态摘要包含权威中立字段 `tests.command_id/exit_code/passed/failed` 及安全的证据元数据；`godot_exit_code` 键保留，只有 ID 为 `godot` 或以 `godot_` 开头时填写。旧配置保留历史摘要字节及其旧别名；两种配置的结果文件始终以 `tests` 为准。向导及迁移材料只为启用新策略的配置附加[可选模板](../routines/test-evidence.md)，默认正文不变。

## advisory 原生 Git 复核

工作区可选 `other_prefixes` 只允许配合 `git_gate: "advisory"`，经既有路径规范化函数处理。向导为新 advisory 登记提供可重复 `--other-prefix`，不增加交互问题；已有登记、只读检查和仅生成模式拒绝此参数。前缀只标注其他写入者，不授权范围，也不能覆盖保护规则。

claim 记录 HEAD 与之前已有的变更；advisory 快照新增 dirty 文件哈希，可检测已脏文件的再次修改或移除。验证后的结果/状态新增 `git_review`：`baseline_head`、`head_after`、`head_changed`、`changes_since_baseline`、`reported_changes`、`in_scope`、`declared_others`、`out_of_scope`。三个分类覆盖本机观察与远端声明的并集，白名单优先。`out_of_scope_changes` 仍为所有白名单外变更，包括 `declared_others`，不会因声明他人负责而隐藏变更。

HEAD 改变时使用原生 `git diff --no-renames --name-only -z` 比较，在既有 `git_head_mismatch` 拒绝前保存复核结果；此功能不授权执行期间提交，也不放松 HEAD 检查。一般 advisory 越界仍只报告，不单独因此失败；失败后已计算的复核结果继续冻结保留。不会改写历史证据；旧快照没有 dirty 哈希时，无法证明旧脏文件内容的后续变化。本功能不能归因并发写入、检测越界读取，也不是 OS 沙箱。
