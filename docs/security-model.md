# Security model

## Provided controls

- Fixed workspace profiles loaded from an administrator registry.
- Dynamic manifests whose allow-list is a subset of the profile.
- SHA-256 of the manifest file, checked against the validation proof and the execution grant.
- A validation proof that must report `validation_passed: true` and `code_modified: false`.
- A separate execution id, granted once per manifest.
- Exclusive claim, cross-process lock, and terminal states that cannot be claimed again.
- Git HEAD check against `expected_head`.
- Exact path check before claim, including case and reparse points.
- Post-run snapshot diff. New tracked, untracked, or ignored paths outside the allow-list are `scope_violation`.
- HTTP classification that separates rate limits, authentication failure, invalid payloads, paused routines, and explicit quota exhaustion.
- No automatic retry after a possible send.
- Independent review: `qa_accepted` stays false.

## Not provided

- No operating-system sandbox. A routine that shares the user's write access can modify files outside the allow-list. The bridge detects that afterward; it does not prevent it.
- No guarantee that the model obeys the routine text.
- No advance notice when a weekly quota runs out, and no API to read the remaining quota.
- HTTP 200 does not mean the code change is correct.
- No promise that every MCP client or every desktop Grok Bot build behaves the same way.
- No unattended handling of interactive approval dialogs. If a local tool asks for approval and nobody is present, stop.
- `advisory_precheck` is not a sandbox.

## Strict and advisory Git gates

See [path normalization and bounded JSON](hardening.md) / [路径规范化与 JSON 上限](hardening.zh-CN.md) for the advisory comparison rules and safe error codes.

`strict` remains the default and preserves the existing HEAD, dirty-worktree, untracked-file and post-run scope refusals. A workspace may explicitly select `advisory`: the claim freezes the then-current HEAD and existing tracked/untracked change list, while later out-of-scope changes are reported instead of failing solely for scope.

Advisory checks every registered protected/forbidden path against the current HEAD at admission and claim, then compares the recorded hashes after claim. `.git` is not path-compared, and the bridge's own untracked `.bridge` runtime directory remains excluded. Git command failures, manifest allow-list validation, claim-once, identity, frozen-evidence and no-resend rules remain fail-closed. Advisory cannot attribute concurrent writes to an agent, cannot detect out-of-scope reads, and is not an OS sandbox. The operator must review Git history and diffs.

For bounded, opt-in expansion within the existing registered file policy, local Routine consistency checks and the sole supported ledger-archiving recovery path, see the [whole-repository guide](advisory-workspaces.md) / [整仓指南](advisory-workspaces.zh-CN.md). Manual ledger deletion is prohibited; archiving never authorizes retries or weakens frozen evidence. Administrator commands are local interfaces, not proof of OS administrator identity.

## Prefix ceiling and frozen task grants

An optional `prefix_ceiling` uses the same literal prefix/extension normalization
as `allowed_prefixes`. Each active entry must fit inside one ceiling entry: its
prefix equals or descends from that entry and its extensions are a subset.
Exact `allowed_files` exceptions remain independent of the ceiling; all existing
forbidden/protected checks still apply. An empty active list grants no prefixes.

With a ceiling, the fingerprint covers the ceiling instead of active prefixes.
Changing the ceiling, forbidden or protected policy still requires updated Routine
text and explicit binding confirmation. Expanding or shrinking active prefixes
within the ceiling does not require repasting the Routine. Preparation freezes
`active_prefixes` in the manifest. Claims recheck that snapshot against the current
ceiling and return it with `active_prefixes_sha256`; execution evidence uses that
frozen grant even if the registry's active prefixes later change. Shrinking active
prefixes therefore affects future tasks, not already prepared grants. A changed
ceiling can refuse an outstanding claim, and existing policy identity checks remain.

The administrator CLI previews changes by default. `--apply` creates a same-directory
backup, atomically replaces the registry, then appends a policy audit event. The
registry update and audit append are separate filesystem operations: a crash can
leave a backup or a committed update without its audit line. Audit is retrospective
traceability, not a gate or authorization; `policy_audit_write_failed` reports an
already applied registry update. No credentials, bindings or switches are changed.
These application checks are not an OS sandbox.

前缀上限与禁区仍需更新并重粘 Routine、明确确认绑定；上限内的生效前缀扩缩不需重粘。
manifest 冻结当时的授权，claim 再校验其属于当前上限，结果核对仍使用冻结快照。
收缩生效前缀只影响后续任务，不撤销已准备任务；精确文件例外保持原语义且不能绕过禁区。
审计日志是事后追溯手段，不是闸门；注册表替换与审计追加不构成跨文件原子事务。这不是 OS 沙箱。

## Optional local validation

An advisory registry may explicitly enable `local_validation`. This removes the
independent Grok-side Validate pass: the local bridge performs those deterministic
manifest/hash, identity, scope, forbidden-path and advisory Git checks instead.
This is an accepted opt-in trade-off because Execute claim still rechecks the full
manifest, frozen validation decision, policy fingerprint, baseline and exact scope.
The local result carries `validator: "bridge_local"` and its policy fingerprint;
claim and execution acceptance refuse it if that policy is changed or disabled.
Legacy proofs without the marker retain their existing checks. This is local
application consistency, not independent remote attestation or an OS sandbox.

No switch or credentials are needed for local validation because no remote send
occurs. Workspace writes must still be enabled for the existing result/ledger
publication path. Execution retains its switch, binding, claim-once and no-resend
requirements. Failed and uncertain evidence cannot be replaced or retried.
See [local validation](advisory-workspaces.md#local-validation).

本地校验显式移除了 Grok 端独立 Validate 检查。检查本身是确定性的，Execute claim
仍全量复核身份、指纹、冻结证据、基线和范围，因此允许 advisory 工作区自行选择此权衡；
这不构成远端独立证明或 OS 沙箱，也不豁免执行开关和禁止重发规则。

## Credential boundaries

The validate target is never used as the execute key, and the reverse is also refused. The repository contains no credential value. Setup prompts do not echo the key and do not write it to a project file.

## Quota and on-demand

The bridge never enables on-demand billing and never changes a subscription. `quota_exhausted_confirmed` is set only when the response text matches an explicit exhaustion phrase. A bare HTTP 400 stays `http_400_unknown` or a more specific non-quota category.

## Directory authorization

Optional `allowed_prefixes` authorizes literal directory/extension sets; each task and claim still carries an exact file list. Prospective `create_files` cannot override forbidden/protected policy, and `.git`/`.bridge` segments are permanently excluded. Administrators remain responsible for `.cursor`, `AGENTS.md` and private-path exclusions. Existing parents are checked for symlinks, junctions and reparse points, but these application-level checks are not an OS sandbox or protection from concurrent filesystem replacement. Prefix snapshots omit ordinary ignored cache trees; explicit watched protections remain. Newly observed unreported out-of-task additions use the advisory report (strict refuses); agent-reported new out-of-task files are refused. No permission preview, migration copy or prefix grant authorizes a send or retry.
