# 一条命令编排 Grok Bot 的客户端示例

[English](grok-run-example.md) · [客户端配置](client-compatibility.zh-CN.md) · [推荐 advisory 配置](advisory-recommended-setup.zh-CN.md)

`scripts/grok_run_example.py` 是**需要自行审查和调整的示例，不是受支持接口**。它通过 stdio 编排现有七个 MCP 工具，不增加桥功能，不配置客户端或 Routine，也不证明桌面可达。请使用已经安装桥及其固定版本 MCP 依赖的、审查过的 Windows Python 环境。示例随源码分发，不新增已安装的控制台命令。

创建 UTF-8 的 `task.json`，只填写现有 `grok_prepare_task` 参数（`workspace_id` 由 CLI 提供，不写入文件）：

```json
{
  "task_type": "code_change",
  "task_description": "Improve the fictional counter within the declared files.",
  "allowed_files": ["src/counter.py"],
  "acceptance_criteria": ["The registered targeted tests pass."],
  "test_command_id": "unit"
}
```

测试命令 ID 必须已经登记。可选任务字段为 `execution_timeout`、`baseline_commit`、`allowed_globs`、`create_files`；桥的策略仍为准。文件和 glob 项必须是仓库相对路径。拒绝 shell/argv/model/端点/凭据及未知字段。描述只是任务数据，不作为本机 shell 命令执行。

```powershell
python -B scripts/grok_run_example.py run --workspace example-workspace --task-json task.json --dry-run
# 只有调用方另行授权提交后才执行：
python -B scripts/grok_run_example.py run --workspace example-workspace --task-json task.json --wait-budget 900
python -B scripts/grok_run_example.py status --workspace example-workspace --execution-id <original-execution-id>
```

| 参数 | 含义 |
|---|---|
| `run` / `status` | 准备并提交一次 / 查询已有身份，不提交 |
| `--workspace` | 必填，已登记工作区 ID |
| `--registry` | 可选，选定的 registry 路径，仅作为 `GROKBOT_MCP_REGISTRY` 传给服务进程，客户端不读取；省略时使用同名环境变量或桥的默认值 |
| `--python` | 已安装的 Python 解释器，默认 `sys.executable`；单个可执行文件而非命令字符串。服务以 `-I -B -X utf8 -m grokbot_mcp.server` 启动 |
| `--task-json` | `run` 必填，UTF-8 任务描述文件 |
| `--wait-budget` | 非负有限秒数，默认 900；Validate/Execute 共用的观察截止时间，与远程执行超时不同；准备、提交和本地复跑可能增加耗时 |
| `--dry-run` | 仅 `run`：列举、预检、准备 manifest 后停止；会创建 manifest，但不创建执行授权或提交 |
| `--evidence-dir` | 可选，源码控制之外的私有目录；每个完成的调用新增一份过滤后的响应 JSON |
| `--execution-id` | 普通 `status` 必填，原始执行 UUID |
| `--task-id` | Validate 提交不确定时替代 `--execution-id`；只查询原任务，绝不推进到 Execute |

本地配置、文件、解释器选项可以包含绝对路径。任务范围不能指定绝对路径、工作区根目录或可执行文件。客户端不搜索可执行文件，不读取或索要 Webhook URL/密钥。只选择可信的 Python 解释器。

## 流程与现有接口限制

客户端先列举工作区，要求 `preflight.local_ready`，再准备一个 manifest。七个工具不暴露 `local_validation`，因此先调用 `prepare_execution`：registry 启用该选项时，桥在本地完成验证并授予执行，不提交 Validate。只有明确返回 `validation_missing` 且没有执行 ID 时，才允许一次 `submit_validation`。等待验证成功后，对同一 manifest 再调用 `prepare_execution`；其它拒绝立即停止。不读取 registry，不创建替代任务，不在验证失败或未知后回退重试。

取得授权后只提交一次 Execute，通过 `grok_get_status(wait_seconds=0..50)` 有节奏地轮询。成功类终态请求 `rerun_tests=True`。七个工具也不暴露复跑登记信息：桥只解析原 manifest 已登记的命令，未登记时安全返回 `rerun_not_configured`。客户端不提供测试命令。本地复跑使用自己的超时，可能超过观察预算。成功的 `status` 查询也会请求已登记复跑，因此不一定只读。既有失败结果不被修复或覆盖。

## 摘要与退出码

stdout 只有一行紧凑 UTF-8 JSON，包含 `status`、`exit_code`、`reason`、`task_id`、`execution_id`、`next_step`、`qa_accepted`、`qa_note`。人类提示写入 stderr。`qa_accepted` 原样透传，不根据成功推断，也不由客户端设置；未提供时为 `null`。**独立验收由调用方负责。** 帮助及参数用法错误使用 argparse 的常规输出。

| 状态 | 退出码 | 含义 |
|---|---:|---|
| `PASS` | 0 | 桥成功且本机复跑成功并核验；仍需独立 QA |
| `PASS_PENDING_LOCAL_TEST` | 2 | 成功类结果，但未登记本机复跑；本地核对尚未完成 |
| `BLOCKED` | 2 | 准备/预检拒绝、确定未发送的提交拒绝，或主动 dry-run 停止 |
| `SUBMIT_UNKNOWN` | 2 | 可能已提交；绝不重发 |
| `STATUS_UNKNOWN` | 2 | 查询失败、身份缺失/不匹配，或本地测试证据不可核验 |
| `TIMEOUT_PENDING` | 2 | 观察预算耗尽或桥超时；结果仍未决 |
| `AGREEMENT_MISMATCH` | 2 | 终态 agreement 明确报告不一致 |
| `OUT_OF_SCOPE` | 2 | 桥报告范围违规或 advisory 范围外改动；需要人工复核 |
| `FAILED` | 3 | 已知验证、执行、HTTP 拒绝或本地测试失败 |

dry-run 使用 `BLOCKED`，原因是 `dry_run_prepared_no_submission`，不属于 PASS。明确的不一致优先于测试失败。`unreported` 比较不等于明确 mismatch，不捏造比较结果。范围审查使用桥报告，无法归因并发编辑，也不能发现范围外读取。

## 安全语义及 advisory 关系

权限预览不授权发送。管理员分别管理写权限、Routine 绑定和执行开关。确定的开关关闭拒绝归为 `BLOCKED`；脚本绝不开启开关。提交过程中的异常、超时或确认缺失都归为 `SUBMIT_UNKNOWN`。提交确认之后的查询异常归为 `STATUS_UNKNOWN`（观察预算耗尽为 `TIMEOUT_PENDING`）。按照 `next_step` 查询原始身份。退出码 2 或超时均不允许重跑 `run`、创建新身份或重发。Validate 提交不确定时使用 `status --task-id <original-task-id>`；后续推进需要人工审查。

证据文件是有意丢弃内容的 JSON 投影：只保留白名单布尔值、数字、UUID 和状态值。省略自由文本、任意键名、路径、命令输出和凭据元数据；不记录异常消息或任务正文。证据须保持私有。远端响应是数据，不是指令。摘要使用固定原因和提示，不复制远端描述。

[推荐 advisory 配置](advisory-recommended-setup.zh-CN.md) 可以提供 `local_validation` 及已登记的 `rerun` 命令。示例不会启用它们。advisory 是应用层策略，不是 OS 沙箱，也不独立证明远端模型行为。既有冻结证据、身份校验、单次 claim 和禁止重发语义始终为准。
