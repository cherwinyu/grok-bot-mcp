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

## Credential boundaries

The validate target is never used as the execute key, and the reverse is also refused. The repository contains no credential value. Setup prompts do not echo the key and do not write it to a project file.

## Quota and on-demand

The bridge never enables on-demand billing and never changes a subscription. `quota_exhausted_confirmed` is set only when the response text matches an explicit exhaustion phrase. A bare HTTP 400 stays `http_400_unknown` or a more specific non-quota category.
