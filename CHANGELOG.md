# Changelog

## 0.1.5b3

- Explicit registered reruns can supplement an otherwise valid execution frozen
  as `test_evidence_missing`. Append-only bridge receipts report
  `succeeded_bridge_evidence` or `failed_bridge_evidence`, with
  `evidence_source: bridge_rerun`; the original result and ledger stay frozen and
  `qa_accepted` stays false. Supplemental attempts are idempotent.
- Put required output fields and honest `not_run` instructions directly in the
  generated Execute body when `require_test_output` is true. Policy fingerprints
  and disabled/default Routine content are unchanged apart from version banners.
- 补充证据来自桥本机复跑，不证明 Grok 的历史测试行为；不改变原始失败证据，
  不授权重发、真实配置修改或发布。

## 0.1.5b2

- Add advisory-only opt-in local validation, using shared manifest policy checks,
  atomic result publication and frozen evidence. Execution preparation can validate
  an untouched task locally; claim and execution reject changed local-validation policy.
- Add bounded `grok_get_status(wait_seconds=0..50)` with read-only backoff polling,
  elapsed milliseconds, observe-only support and terminal-only reruns after waiting.
- Defaults preserve legacy policy fingerprints, Routine content (except version
  banners), result behavior and the seven MCP tools. No real configuration or
  Routine is changed and no publication is authorized by this source update.

## Unreleased

- Add opt-in local registered test reruns through `grok_get_status(rerun_tests=true)` and `python -m grokbot_mcp.test_run_cli`. Independent append-only receipts compare exit status and output hashes without changing frozen execution evidence or QA state.
- Keep local `rerun` configuration out of policy fingerprints and generated Routine bytes. Validate argv, working-directory boundaries, environment keys and timeouts; preserve legacy status responses when omitted.

- Add opt-in directory authorization (`allowed_prefixes`), exact prospective `create_files`, measured `not_created`, prefix-aware Routine packets and read-only `registry-prefixes` migration previews. Defaults preserve existing bytes; no version change. / 增加可选目录授权、新建文件声明、未创建清单、前缀模板与只读迁移预览；缺省兼容，不升版本。

- Harden advisory path normalization (including Git octal UTF-8 quoting), case-insensitive forbidden checks and NFC result comparisons. Strict snapshot serialization is unchanged. / 加固 advisory 路径、Git 八进制 UTF-8 解码、禁区大小写匹配及结果 NFC 比较；strict 快照序列化不变。
- Refuse oversized persisted JSON before writing and expose `ledger_too_large` on read/write boundaries. Return structured tool argument/internal errors without payloads or tracebacks. / 超限 JSON 写前拒绝并返回明确错误；工具参数及意外异常返回脱敏结构化诊断。
- Declare UTF-8 file/subprocess encodings and collect only top-level tests with bare pytest; Windows CI no longer sets `PYTHONUTF8`. / 明确文件与子进程编码；默认 pytest 仅收集顶层测试；Windows CI 不再设置 `PYTHONUTF8`。 See [hardening notes](docs/hardening.md) / [加固说明](docs/hardening.zh-CN.md).
- Add opt-in `git_gate: advisory` for shared dirty workspaces, with claim-time baselines and non-fatal `out_of_scope_changes`; strict remains the byte-compatible default.
- `grok_list_workspaces` returns `ok: false` and `workspaces: null` when the registry file is missing, instead of an empty workspace list.
- Result candidates must be `%TEMP%\grokbot-mcp-candidates\<phase>\.<id>.result.tmp` for both validate and execute. `result_cli --source` rejects every other location.
- A terminal publish removes that id's staged file and the matching temp candidate, and records the cleanup under `.bridge/protocol_cleanup/` without modifying the submission ledger or the published result. `result_cli --cleanup` repeats that limited deletion.
- New manifests record `constraint_version`. Historical manifests that omit it remain verifiable. Verification does not require constraint-prose equality.
- README and client docs add Cursor and Codex config shapes, path self-checks, and a PowerShell write-then-dispatch script that saves UTF-8 without a BOM.

## 0.1.5b1 (experimental Beta)

- Optional `prefix_ceiling` keeps the Routine policy fingerprint stable while active prefixes change within the ceiling. Manifests freeze active grants; claims expose their canonical SHA-256 and recheck the ceiling. Exact-file exceptions and forbidden/protected checks retain their existing semantics.
- `registry-prefixes --add/--remove` previews by default; explicit `--apply` backs up the registry, atomically replaces it and appends `policy_changes.jsonl`. It never reads credentials or changes bindings.
- Execution preparation reconciles arrived results through the existing status settlement path before rejecting concurrent execution. Read-only preflight reports stale execution IDs; unresolved records return the blocking execution ID.
- Preflight and workspace listing expose runtime package/registry drift. Changed package bytes recommend a process restart without changing blocking reasons.
- 可选前缀上限、manifest 冻结授权、claim 上限复核、带备份审计的前缀管理、执行自动对账及进程漂移提示；保持七个 MCP 接口和失败证据冻结规则。

## 0.1.4b1 (experimental Beta)

- First standalone Public Beta candidate in a clean repository without the private development history.
- Add read-only Cursor JSON and Codex CLI TOML configuration fragments and Unicode/TOML validation.
- Retain seven MCP tools, the original ledger, claim-once and frozen-evidence behavior.
- Preserve the Windows-local authorization topology and explicit advisory (non-OS-sandbox) security boundary.
- Rework first-run documentation and isolated fictional tests.
- Use one selected final release wheel for both Windows CI installation and future prerelease attachments, after a separate maintainer release approval.
- Independently verify that every shipped sdist source file matches the audited source ZIP; reject extra, missing, modified and special members.
- Update client setup documentation to the current direct Cursor MCP documentation page.

Earlier private development iterations are not shipped as history in this repository.
