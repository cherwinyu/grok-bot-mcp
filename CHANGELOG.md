# Changelog

## 0.1.5

- First non-prerelease version of this experimental line; it is not the
  ROADMAP Stable milestone. It includes every change since `v0.1.4b1`: the
  [0.1.5b5](#015b5), [0.1.5b4](#015b4), [0.1.5b3](#015b3) and
  [0.1.5b2](#015b2) entries, the [Unreleased](#unreleased) entry merged before
  0.1.5b2, and [0.1.5b1](#015b1-experimental-beta).
- When a tool returns `internal_error` and the installed package bytes have
  changed since the MCP process started, the result adds the warning
  `process_restart_recommended` and a `next_step` to restart the MCP client
  process without resending or retrying submissions. The error code, `reason`
  and existing fields are unchanged; if the drift check itself fails, the
  original error is returned as before.
- The example-only `scripts/grok_run_example.py` now reports `TIMEOUT_PENDING`
  (`wait_budget_exhausted`) when its local wait budget cancels a
  `grok_get_status` call before the monotonic deadline, as Windows timers can
  do; it previously reported `STATUS_UNKNOWN`. It remains an example, not a
  supported interface.
- README adds overview, core idea and status sections, and the Chinese README
  wording is polished.
- Apart from the version banner in generated Routine material, this update does
  not change `policy_fingerprint` semantics, the seven MCP tools or frozen
  evidence, and it does not authorize PyPI or any operation beyond the
  maintainer-approved GitHub Release.

## 0.1.5b5

- `protected_or_forbidden_changed` diagnostics now name up to 10 offending
  repo-relative paths (sanitized, "... and N more") and note that the changes
  may come from another session sharing the repository. Blocking decisions,
  error codes and existing fields are unchanged; the hint does not suggest
  reverting files or switching to `advisory`.
- `rerun_result_unverified` guidance states that the bridge fallback rerun is
  only valid after execution completes and before the workspace is committed.
- Extend the recommended advisory setup guide (English and Chinese) with the
  runtime-install versus development-checkout split, `wait_seconds` polling
  arithmetic and the rerun time window.
- Add `scripts/grok_run_example.py` and bilingual docs: an example-only client
  that orchestrates the existing seven MCP tools in one command. It is not a
  supported interface, adds no MCP tool and never resends an unknown submission.
- Apart from the version banner in generated Routine material, this update does
  not change `policy_fingerprint` semantics and does not authorize publication.

## 0.1.5b4

- Raise the global execution timeout ceiling from 600 to 3600 seconds and the
  `rerun.timeout_seconds` ceiling from 1800 to 3600. The setup wizard defaults
  new code-test commands to `timeout_cap`/`default_timeout` of 3600/3600;
  per-task `execution_timeout` and per-command `timeout_cap` may still be lower.
  Existing registrations are not automatically upgraded, and old ledger records
  without a timeout field retain the 600-second fallback.
- Add read-only `admin_cli registry-check` and the additive
  `grok_preflight.test_command_check` field without changing `local_ready`.
  Clarify the recovery guidance for `installation_mismatch`.
- Add `claim_cli --expect-fingerprint` and include it in generated Routine text.
  After upgrading, regenerate and repaste the Routine text; calls that omit the
  option retain their existing behavior.
- Exclude governance paths such as `.cursor` and `AGENTS.md` by default when
  deriving prefix authorizations, and add nested-repository diagnostics.
  Improve test temporary-directory support and JSON encoding on non-UTF-8
  consoles. Use the package version as the single source for version readers.
- Add the recommended advisory setup guide in
  [English](docs/advisory-recommended-setup.md) and
  [Chinese](docs/advisory-recommended-setup.zh-CN.md).
- Apart from the changes described above, this update does not change
  `policy_fingerprint` semantics and does not authorize publication.

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
