# Result contract v1 / 结果 JSON 契约

[Deployment](deployment.md) · [部署说明](deployment.zh-CN.md)

`result_contract.py` is the shared checker used by validation/execution interpretation, execution evidence assessment and local result writing. Existing legacy `status`/`error` vocabulary remains; `result_diagnosis` adds a precise category rather than redefining a frozen failure. Local format checking and publishing **never** mean bridge acceptance or human QA.

统一检查器覆盖结果解释、授权证据检查及本地写回。保留旧字段语义；例如布尔计数仍可能保留旧 `test_failed` 错误码，但新增诊断为 `format_error`，不再误导为实际测试失败。已有冻结失败不允许换 proof 来“修复”。

## Common and phase fields / 公共与阶段字段

Common: `protocol_version` integer `1` (legacy string `"1"` remains accepted, boolean is rejected), `action`, canonical lowercase UUID identity, lowercase 64-character `manifest_sha256`, registered `machine`, and `status` from `pending/running/succeeded/failed`. Validation requires `workspace_id`; execute allows old results that omitted it, but verifies it when present. Supplied identities must exactly match the registered task, manifest and ledger, not a path or a human-readable report.

Validation identity is `task_id`, action `validate_manifest`. Terminal results require boolean `validation_passed`, `code_modified`, string-array `validation_errors`, and exact manifest `allowed_files`. Success requires true validation, no code modification and no validation errors. Failed validation uses false, never a forged success proof.

Execution identity is `execution_id` **and** `manifest_task_id`, action `execute_manifest`. Terminal results require boolean `code_modified`, normalized unique repo-relative `changed_files`, and `tests` with the registered `command_id`. Optional `cursor_cloud_agent_used`, when supplied, must be boolean. Existing claim/baseline/scope verification still runs independently; a correctly typed string does not satisfy claim evidence.

公共身份不从文件名或自然语言猜测；校验阶段使用 `task_id`，执行阶段必须同时有 `execution_id`、`manifest_task_id`。结果位置由登记和任务确定，调用者不能传任意写入目标。共享类型校验不会替代原 claim 字节哈希、冻结成功 proof、基线和文件范围检查。

## Counts are measurements, not booleans / 计数必须来自测量

`tests.passed` and `tests.failed` are **non-negative integers**, never `true`, `false`, strings or guessed values. Python's `bool` is a subclass of `int`, so the checker uses `type(value) is int`. `tests.exit_code` is an integer process exit status, not a test count or boolean; negative nonzero failure codes are permitted. Success requires zero failures and exit code zero.

`tests.unit` optionally declares `test_cases` or `assertions`. Keep the same unit for passed/failed. `case_count` and `assertion_count` optionally record separately measured totals; omitted values mean not supplied, not zero. Legacy results without these additive fields remain valid. Do not parse prose to invent totals. A fixture with **6,561 coordinate cases and 19,683 assertions** uses two separate fields; declaring `unit=test_cases` means its passed count is 6,561, not 19,683. The large example is fictional contract data, not a claim that this repository ran that many tests.

`pending`/`running` may omit counters and final-only fields; any supplied counter is still strictly checked. Terminal failure **before tests could run** uses `tests={"command_id":"...","not_run":true}` without fake counters or exit code. Legacy summary counters then remain `null`. `not_run` cannot support success. A process/test failure that really produced counts reports those measured counts and exit code instead.

通过数与失败数必须使用同一明确单位；用例总数、断言总数分开保存。没有运行测试时，不填 `0 passed` 来暗示测量完成，使用 `not_run`，摘要为 `null`。中间结果不伪造终态字段；终态不能回退为 running，已发布终态也不能替换。

## Runnable complete examples / 完整可运行示例

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.result_examples
& $BridgePython -I -X utf8 -m grokbot_mcp.result_examples --example execute-failed-before-tests
```

This checks all ten examples without writing files or loading credentials: validation and execution each have pending/running/succeeded/failed; additional examples cover failure-before-tests and distinct case/assertion counts. The files under `examples/results/` are checked against the executable example source. Routine source templates and package templates are byte-equal and share one `result-writing.md` section rendered by `materials.py`.

十个示例由可执行源生成，并有文件一致性回归；模板源、包内模板、向导与迁移生成材料共用同一写回片段，避免多处维护契约漂移。

## Safe publication / 安全预检与写回

Create a private candidate JSON outside the business result location. Precheck it first:

```powershell
$ResultArgs = @('--registry', $Registry, '--workspace', $Workspace, '--phase', 'execute', '--id', $ExecutionId)
& $BridgePython -I -X utf8 -m grokbot_mcp.result_cli @ResultArgs --source $CandidateJson
# Review returned staged_sha256, identity and expected relative staging path.
& $BridgePython -I -X utf8 -m grokbot_mcp.result_cli @ResultArgs --source $CandidateJson --stage --confirm-sha256 $CandidateHash
& $BridgePython -I -X utf8 -m grokbot_mcp.result_cli @ResultArgs --staged $StagedJson
& $BridgePython -I -X utf8 -m grokbot_mcp.result_cli @ResultArgs --staged $StagedJson --publish --confirm-sha256 $CandidateHash
```

The registered staging path is `.bridge/results_execute/.<execution-id>.result.tmp`, or `.bridge/results_v2/.<task-id>.result.tmp`. The final name is `<id>.json` in that same directory. The helper derives both; it refuses traversal, links/reparse points/hardlinks, arbitrary output paths, mismatched identity, unaccepted/uncertain submissions, absent or mismatched required claims, frozen ledger evidence and existing terminal results even before observation. JSON must be complete UTF-8 without BOM, duplicate keys or NaN. Incomplete/encoding faults report safe codes, not raw payloads.

To replace a previous **temporary** stage after reviewing corrected content, also pass the exact `--previous-staged-sha256` returned by a new preview. This is not permission to replace final evidence. The helper rechecks under the existing ledger lock, verifies snapshot hashes and uses same-directory atomic replacement. It creates no claim, authorization or ledger transition, enables no switch and sends no Webhook. Pending→running→terminal is allowed; pending→terminal is allowed with complete matching evidence; running→pending and terminal replacement are refused. A final format error bypassing this helper may already be frozen by bridge observation; do not resend or replace it.

先预检私人候选，再显式写入任务约定的同目录暂存文件；暂存校验通过后才显式发布到派生目标。修改暂存也需旧字节哈希确认。发布器不造 claim、不授予授权、不改账本、不解锁执行。半写 JSON/UTF-8 边界只返回稳定代码，不泄漏原文。

Read the result through the original `grok_get_status` workflow afterwards. `published=true`, `format_valid=true`, `identity_valid=true` do not imply `bridge_accepted` or `qa_accepted`. Diagnostic categories distinguish `format_error`, `identity_error`, `intermediate_result`, `validation_failure`, `test_failure`, and `code_execution_failure`. A diagnosis provides field/expected type only, never copies the sensitive bad value.

## Rejected validation presentation / 被拒校验的安全呈现

A validation `format_error` or `identity_error` returns trusted ledger/registry identity and field/type diagnostics only. `validation_passed`, `validation_errors`, `allowed_files`, and `code_modified` are `null` rather than untrusted payload values. This also filters cached earlier error summaries on read, without modifying their ledger, proof hashes, historical status or verified timestamp. Valid, well-typed validation successes and failures keep their existing values.

格式错误或身份错误时，不仅诊断字段安全，外层摘要也不得回显错误载荷。摘要保留可信的任务摘要哈希/工作区身份，其余未可信的四个值为 `null`。更早冻结的失败仅在读取时隐藏载荷，不改写历史证据或把失败变成成功。

## P0 evidence reader alignment

Evidence and local staging now share bounded JSON decoding: duplicate keys, NaN/Infinity (including overflowing exponent notation), excessive nesting and oversized payloads are refused. Incomplete UTF-8/JSON retains the existing short observation window; it is not a reason to reissue a Webhook. Hardlinked evidence is rejected before interpretation. Endpoint and parser refusal codes contain no rejected value.
