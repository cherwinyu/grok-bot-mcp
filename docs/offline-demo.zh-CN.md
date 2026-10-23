> New executable offline entry: `python -I -X utf8 -m grokbot_mcp.safety_cli demo`. It owns a disposable fixture, uses memory-only transport, measures two Python tests and cleans up. The walkthrough below remains a static explanation, not live acceptance.

# 离线／静态演示：为计数函数增加一个功能

[English](offline-demo.md) · [快速开始](quickstart.zh-CN.md)

**这是一份虚构流程说明，不是真实 Grok Bot 成功录像或运行记录。** 制作本页未使用真实 Routine、凭据或 Webhook。下面的输出片段是验收条件，不是实际抓取的结果。`<task_id>`、`<execution_id>` 是占位符：真实操作必须使用客户端返回的原始标识，不能照抄占位符或自行补造标识。

## 准备与人工检查点

按快速开始复制 `examples/minimal_workspace`，登记为 `demo-workspace`。现有 `count_above` 统计严格大于阈值的数。本例要**新增** `count_at_least`，保留旧行为，并补充阈值相等和空列表测试。只允许修改 `src/counter.py`、`tests/test_counter.py`；保护文件是 `README.md`。

运行向导 `--check`，调用 `grok_preflight`。开关关闭时可以是安装完成、提交受阻。**只有准备做真实试验时**，才应审查生成的 Grok Bot 材料、确认实际升级后的 Routine，再按[高级说明](advanced-setup.md)分别明确开启工作区写许可与提交总开关。本页不替你开启。本地就绪和管理员确认绑定都不证明远端结果。

真实试验前审查 `git status --short`、`git diff` 与登记基线。有无法解释的改动就停止，不重置工作区，不自动推进基线。任务参数不得包含凭据。

## 准备任务：只写本地，不发送 Webhook

调用 `grok_prepare_task`：

```json
{
  "workspace_id": "demo-workspace",
  "task_type": "code_change",
  "task_description": "Add count_at_least(values, threshold) without changing count_above. Add unit tests for equality at the boundary and empty input.",
  "allowed_files": ["src/counter.py", "tests/test_counter.py"],
  "acceptance_criteria": [
    "count_above retains strictly-greater behavior",
    "count_at_least includes values equal to threshold",
    "An empty list returns zero",
    "Only the two allowed files change; the protected README remains unchanged",
    "Run the registered python_unittest tests"
  ],
  "test_command_id": "python_unittest"
}
```

预期返回自动生成的 `task_id`、`manifest_sha256`、`validation_status="valid"`、`webhook_sent=false`。这里只写任务清单，不修改代码。**人工检查：**发送前确认范围、验收条件准确。

## 提交一次校验

调用 `grok_submit_validation`，参数为 `{"workspace_id":"demo-workspace","task_id":"<task_id>"}`。

HTTP 接收成功的片段为 `{"accepted":true,"http_status":200,"task_state":"accepted","qa_accepted":false}`。这只表示请求被接收，**不表示校验已执行或通过**。校验 Routine 仍需检查实际任务与工作区，在不改代码的条件下写入 proof。

出现 `unknown_submission`、`submission_started`、超时或禁止重复提交时，不要重发、换新标识重复执行或手工删除账本。到 Grok Bot 检查同一任务，并在本机查询它。因开关关闭而明确从未尝试发送的调用属于另一种情况，应遵循对应诊断，不能凭猜测重试。唯一受支持的账本归档恢复路径是 [ledger-archive](advisory-workspaces.zh-CN.md#故障与有审计的恢复)。

## 查询并同步成功校验到账本

调用 `grok_get_status`：

```json
{"workspace_id":"demo-workspace","task_id":"<task_id>","observe_only":false}
```

`pending`、`running` 或文件尚未写完都不是成功。继续查询原标识；查询不发送 Webhook。要求嵌套结果 `validation.status="succeeded"`、`validation.validation_passed=true`、`validation.code_modified=false`，并已持久化终态决定。正常同步模式是 `query_mode="reconcile"`；终态已持久化后再次查询，`ledger_updated` 不必始终为 true。

`observe_only=true` 可能看到成功，但**没有把决定写到账本**。执行准备会以 `validation_not_verified` 拒绝这种未同步 proof。不能用成功 proof 替换失败 proof，不能补造缺失账本。已经失败的校验仍然失败。

## 准备执行：只产生本地授权，不发送 Webhook

调用 `grok_prepare_execution`，参数为 `{"workspace_id":"demo-workspace","manifest_task_id":"<task_id>"}`。

要求无错误，并返回 `approval_state="granted"`、`submission_state="not_sent"`、`execution_state="prepared"` 和自动生成的 `execution_id`。授权冻结校验证据、基线和绑定，但没有执行任务。**人工检查：**标识与范围必须对应刚刚审查的校验。

尚未使用的授权可通过向导 `--check --execution-id <execution_id>` 检查。只有明确出现 `local_cancellation_available`，才有依据考虑既有本地取消 CLI；该命令还会持锁重新检查。不能通过取消已发送、已领取、提交不确定或已有结果的任务来制造重试机会。

## 提交一次，再查询结果

调用 `grok_submit_execution`，参数为 `{"workspace_id":"demo-workspace","execution_id":"<execution_id>"}`，只提交一次。HTTP 200 仍然只是接收。执行 Routine 必须使用材料里的绝对解释器领取命令；schema-v2 claim 文件与 `claim_sha256` 必须来自真实领取，不能给旧入口补造证据。

随后调用：

```json
{"workspace_id":"demo-workspace","execution_id":"<execution_id>","observe_only":false,"verify_current":true}
```

要求嵌套结果 `execution.status="succeeded"`、`execution.current_integrity=true`，并有当前验证时间和范围。保留 `qa_accepted=false` 的可见性。历史 `succeeded` 搭配 `current_integrity=false`，**不等于当前完整性验证成功**。待定、超时、提交不确定都不能成为自动重发理由。

## 人工验收实际差异

真实试验成功后，在示例根目录独立运行登记的测试，并检查：

```powershell
git -C C:\ExampleWorkspace status --short
git -C C:\ExampleWorkspace diff -- src/counter.py tests/test_counter.py
git -C C:\ExampleWorkspace diff -- README.md
```

确认相等边界与空列表行为、旧 `count_above` 行为、实际改动文件集合，以及保护文件未变。试验结束关闭提交总开关。不自动提交 Git、推进登记基线、删除 `.bridge`，也不自动宣告独立 QA 验收。人工验收是桥外的明确审查；桥的 `qa_accepted` 字段始终为 false。

## 可重现的离线证据

安装包测试使用临时虚构 Git 工作区、内存凭据和 Mock 传输，覆盖：准备 → Mock 校验接收 → 写虚构 proof → 同步 → 授权 → Mock 执行接收 → 真正的本地领取 → 写虚构结果 → 验证，以及失败、篡改和提交不确定分支。这些是**离线协议测试**，不验证你的 Grok 账号、Routine 界面、Bot 对目录的实际访问权限或模型输出质量。见[开发验证说明](development.md)与候选提交对应的 CI 证据。
