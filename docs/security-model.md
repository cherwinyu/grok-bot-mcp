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

## Credential boundaries

The validate target is never used as the execute key, and the reverse is also refused. The repository contains no credential value. Setup prompts do not echo the key and do not write it to a project file.

## Quota and on-demand

The bridge never enables on-demand billing and never changes a subscription. `quota_exhausted_confirmed` is set only when the response text matches an explicit exhaustion phrase. A bare HTTP 400 stays `http_400_unknown` or a more specific non-quota category.

## Directory authorization

Optional `allowed_prefixes` authorizes literal directory/extension sets; each task and claim still carries an exact file list. Prospective `create_files` cannot override forbidden/protected policy, and `.git`/`.bridge` segments are permanently excluded. Administrators remain responsible for `.cursor`, `AGENTS.md` and private-path exclusions. Existing parents are checked for symlinks, junctions and reparse points, but these application-level checks are not an OS sandbox or protection from concurrent filesystem replacement. Prefix snapshots omit ordinary ignored cache trees; explicit watched protections remain. Newly observed unreported out-of-task additions use the advisory report (strict refuses); agent-reported new out-of-task files are refused. No permission preview, migration copy or prefix grant authorizes a send or retry.
