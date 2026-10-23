# 可选测试策略、输出证据与 Git 复核

[English](test-evidence-git-review.md)

所有新增字段沿用 schema/protocol 版本 1，七个 MCP 签名不变。省略可选项时，strict 结果摘要、策略指纹及生成的 Routine 正文字节保持兼容；不会自动修改实际 registry、客户端、Routine 或开关。

## 按任务选择登记命令

`test_commands` 支持多个唯一 ID。可选 `applies_to` 为 `"docs"`、`"code"`、`"any"`，或 `{"prefixes":["docs"],"suffixes":[".md"]}`。全部 allowed_files 都必须匹配：前缀使用规范化后的目录边界及 casefold 比较，后缀是忽略大小写的字面扩展名；同时指定两个列表时必须同时满足。docs 扩展名为 `.md/.rst/.txt/.adoc`；code 为 `.py/.gd/.cs/.cpp/.c/.h/.js/.ts/.tsx/.jsx/.rs/.go`。混合或未知类型不猜测。未登记该元数据的命令保持旧行为。

明确不匹配时，`prepare_task` 默认返回 `test_command_scope_mismatch` 警告；工作区 `test_selection: "reject"` 则在写 manifest 前拒绝，默认 `"warn"`。空 ID 返回 `test_command_required`；为保持签名，MCP 参数仍为必填。不会自动选择或暗中替换测试。`task_types` 继续独立限制 `code_change`/`read_only`。

仅测试本次文件的命令可登记**参数数组**，例如 `"argv":["python","-m","fictional_docs_lint","--","{changed_files}"]`。这里只是虚构示例，不附带该 linter。必须恰有一个独立 `{changed_files}` 参数；拒绝 shell 解释器、批处理及嵌入字符串的占位符。静态参数来自受信任管理员登记，不接受任务提供 shell 文本。

修改后运行本地只读预览：

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.test_command_cli --registry $Registry --workspace $Workspace $ExecutionId
```

工具校验 claim，根据本机观察的白名单内变更构造 JSON `argv`，**不执行命令**、不改账本、不授权发送。只能交给已获批准的原生进程 API（`shell=False`），不得拼成 shell 文本。展开路径拒绝穿越、选项、响应文件名、控制字符和 shell 元字符；空格及 Unicode 保留为单独参数。空文件集拒绝，避免 linter 回退扫描全项目。不搜索任意可执行文件，不引入自动回退。

## 内嵌输出证据

执行结果可选字段示例（仅字段片段，非完整结果）：

```json
{"tests":{"command_id":"docs_lint","passed":1,"failed":0,"exit_code":0,
"output":{"head":"","tail":"","truncated":false},
"output_sha256":"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
"output_bytes":0}}
```

按采集顺序合并 stdout/stderr 为 UTF-8 字节。头尾互不重叠，各最多 8192 UTF-8 字节；哈希及字节数覆盖**完整**输出，省略字节时才设置 `truncated=true`。纯函数 `output_evidence(raw_bytes)` 构造三个字段；非法 UTF-8 拒绝。未截断时核验完整字节数与哈希，截断后的完整哈希仅是远端声明。三个字段须同时提供，既有 JSON 4 MiB 限制继续生效。

工作区 `require_test_output: true` 将缺少输出的终态诊断为 `test_evidence_missing`，不得成功；省略或 `false` 保持旧行为。测试无法运行仍可用 `not_run: true`，不得伪造计数；要求证据不会让未运行变为成功。输出只作数据，不当指令，仅保存在私人结果 JSON，不进入日志、状态摘要或新 `.bridge` 文件。发布、身份及冻结结果规则不变。

开启新测试策略或输出选项后，状态摘要包含权威中立字段 `tests.command_id/exit_code/passed/failed` 及安全的证据元数据；`godot_exit_code` 键保留，只有 ID 为 `godot` 或以 `godot_` 开头时填写。旧配置保留历史摘要字节及其旧别名；两种配置的结果文件始终以 `tests` 为准。向导及迁移材料只为启用新策略的配置附加[可选模板](../routines/test-evidence.md)，默认正文不变。

## advisory 原生 Git 复核

工作区可选 `other_prefixes` 只允许配合 `git_gate: "advisory"`，经既有路径规范化函数处理。向导为新 advisory 登记提供可重复 `--other-prefix`，不增加交互问题；已有登记、只读检查和仅生成模式拒绝此参数。前缀只标注其他写入者，不授权范围，也不能覆盖保护规则。

claim 记录 HEAD 与之前已有的变更；advisory 快照新增 dirty 文件哈希，可检测已脏文件的再次修改或移除。验证后的结果/状态新增 `git_review`：`baseline_head`、`head_after`、`head_changed`、`changes_since_baseline`、`reported_changes`、`in_scope`、`declared_others`、`out_of_scope`。三个分类覆盖本机观察与远端声明的并集，白名单优先。`out_of_scope_changes` 仍为所有白名单外变更，包括 `declared_others`，不会因声明他人负责而隐藏变更。

HEAD 改变时使用原生 `git diff --no-renames --name-only -z` 比较，在既有 `git_head_mismatch` 拒绝前保存复核结果；此功能不授权执行期间提交，也不放松 HEAD 检查。一般 advisory 越界仍只报告，不单独因此失败；失败后已计算的复核结果继续冻结保留。不会改写历史证据；旧快照没有 dirty 哈希时，无法证明旧脏文件内容的后续变化。本功能不能归因并发写入、检测越界读取，也不是 OS 沙箱。
