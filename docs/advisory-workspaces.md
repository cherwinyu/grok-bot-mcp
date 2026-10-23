# Whole-repository workspaces (advisory)

[简体中文](advisory-workspaces.zh-CN.md) · [Security model](security-model.md)

## When to use it

Use an explicitly registered advisory workspace when several operators share a checkout and a person can review every diff and run independent QA. A whole-repository workspace means the registered Git root covers the project; it does not make every file writable. Keep an explicit file allow-list and protected paths. A disposable or separately restricted workspace is preferable for unfamiliar work. A profile is configuration, not desktop reachability or an OS sandbox.

## Configuration

An administrator reviews these workspace fields locally; this guide does not modify an installation, Routine, client, credential or switch:

- `git_gate: "advisory"` opts into the [advisory checks](security-model.md#strict-and-advisory-git-gates). Omission stays `strict`.
- `allowed_files` remains an explicit registered file list. `forbidden_files` and `protected_file` exclude paths and descendants. Include private configuration, credentials, `.git` and `.bridge` in the appropriate protections; glob expansion always excludes the last two.
- `other_prefixes`, `test_selection`, `test_commands[].applies_to`, `test_commands[].argv` and `require_test_output` are defined in [test policy, output evidence and Git review](test-evidence-git-review.md). Prefix labels do not authorize changes. Recommended reviewable projects use required test output and a deliberately chosen test ID.
- `allow_globs` defaults to `false`. `glob_max_files` defaults to `200`, must be an integer in `1..2000`, and never permits more than the hard maximum of 2000 files. Non-default values participate in the policy fingerprint and require binding review.

Registry paths, test commands and files must describe the actual administrator-reviewed project. Do not paste example configuration into a live registry without review. U1 normalization, encoding, safe error codes and the 4 MiB JSON limit are specified in [hardening](hardening.md).

## Restricted directory expansion

`grok_prepare_task` adds optional `allowed_globs`, for example `["src/**/*.py", "docs/**"]`. Continue supplying `allowed_files` (use `[]` when all selection comes from globs), the test command ID and the existing required arguments. Only `dir/**` and `dir/**/*.ext` are accepted, with a literal directory prefix and extension. Absolute paths, drives, backslashes, traversal, empty/dot components, character classes, other wildcards and a second/misplaced `**` are refused.

Expansion reads the local Git index, intersects it with the registry's exact-file or prefix policy, and returns only existing ordinary files. Untracked, deleted, forbidden/protected descendants and `.git`/`.bridge` components are excluded. Selected symlinks, junction/reparse paths, hardlinks, submodules and unresolved index stages are refused. No filesystem glob walk or policy extension occurs. No matches is an error. Both expansion and the final union with explicit files must fit the configured limit; exceeding it refuses the entire operation without truncation.

The manifest still contains explicit `allowed_files`. Opted-in manifests additionally record `scope_expansion.allowed_globs` and `scope_expansion.expanded_files`, copied into the validation task ledger on submission. Prepare returns both the complete final file list and that audit, with `action_required: "confirm_expanded_files"` and an operator message containing the file count. Review the full list before explicitly submitting validation; this message does not create a new authorization mechanism. Existing explicit-only calls retain their behavior and response fields.

## Directory authorization

A registry may opt into `"allowed_prefixes": [{"prefix": "docs/", "exts": [".md"]}]`.
`allowed_files` and `allowed_prefixes` may each be empty, but not both. Prefixes are
literal relative directories ending in `/`; extension lists are nonempty literal
suffixes starting with `.`. Paths normalize to Unicode NFC. No traversal, empty/dot
segments, drives, backslashes, wildcards, Windows device aliases or `.git`/`.bridge`
segments are accepted. Exact existing component spelling and reparse/hardlink checks
remain enforced. Configure `.cursor`, `AGENTS.md` and private paths in `forbidden_files`.

Declare prospective exact files with `grok_prepare_task(..., allowed_files=[],
create_files=["docs/new.md"], ...)`. Each creation must match a prefix and extension,
without overriding forbidden/protected paths. The task scope is the union of caller
selected `allowed_files`, `create_files` and index-expanded `allowed_globs`; glob
matches may now be authorized by exact registry files or prefixes. Review the complete
returned `allowed_files` when `action_required` is `confirm_expanded_files`. Creations
are audited in `scope_expansion.create_files`. The creation count and final union
must fit `glob_max_files`; rejection is whole-request, never truncation.

The normalized, sorted prefix policy participates in `policy_fingerprint` only when
nonempty. Adding files on disk within that policy does not change the fingerprint or
require rebinding. Changes to registered exact-file exceptions or other policy fields
still change policy identity. Prefix-enabled Routine packets embed the prefixes and
fingerprint instead of the full registry file list; the successful claim JSON supplies
the exact task list and creation declarations. `routine-check` checks this consistency.
Absent prefix configuration retains the previous packet, manifest and result format.

Claim snapshots include tracked and nonignored untracked prefix files. Ordinary ignored
cache trees are not enumerated into the ledger merely because a prefix covers them;
explicitly watched forbidden/protected/task files retain their checks. Newly observed
unreported out-of-task files remain advisory `out_of_scope_changes`; strict mode rejects
them. An agent-reported newly created file outside the task list is refused. Optional
creations left absent are measured as `not_created` in the verified execution summary,
only for tasks declaring `create_files`; that absence alone does not fail the task.
This does not relax claim-once, frozen evidence or no-resend behavior.

Preview migration locally without reading credentials or sending requests:

```powershell
grokbot-mcp registry-prefixes --registry $Registry --workspace $Workspace
# After reviewing the broader grants, create a NEW copy outside registered workspaces:
grokbot-mcp registry-prefixes --registry $Registry --workspace $Workspace --write-to $NewRegistryCopy
```

The helper groups exact files by their immediate directory and extension, retains
root-level or extensionless files as exact exceptions, and prints old/new fingerprints.
Preview is read-only. `--write-to` exclusively creates a new registry copy, never
replaces the original or an existing destination, changes a binding or enables a switch.
The copy is a proposal; adopting it and confirming affected Routine bindings is a
separate administrator operation. Directory grants broaden policy, not OS permissions.

## Local Routine check

Use the administrator CLI (not an eighth MCP tool):

```powershell
grokbot-mcp routine-check --registry $Registry --workspace $Workspace
# Equivalent with an explicitly selected installation:
& $BridgePython -I -X utf8 -m grokbot_mcp.admin_cli routine-check --registry $Registry --workspace $Workspace
```

It reads only the known `setup/<workspace_id>/routine-setup.md` packet beside the registry and, when referenced by the local migration marker, the current migration's `routine-update.md`. It checks unresolved `<ADMIN_*>` / rendering placeholders, profile and test-command IDs, workspace ID/root, policy fingerprint, registry/interpreter paths and generated template sections. Missing or edited/customized material is reported for manual review. Interpreter comparison uses the locally attested interpreter, or the running interpreter for legacy bindings that do not record one.

The validate/execute status attestations must match the current policy and binding, with valid non-future confirmation timestamps. “Current” means policy consistency; it is not a remote freshness guarantee or a time-based expiry promise. The output explicitly states: **Cannot verify remote Routine text; only local packets and registration.** It reads no credential values, does not enumerate executable locations and sends nothing. `grok_preflight` includes an additive `routine_check` summary when a local packet exists; existing readiness fields keep their meanings. Read-only diagnostics for a timeout with no observed result add an `operator_message` suggesting unfilled Routine registration values and pointing to this check. That suggestion is not a diagnosis of the remote account.

## Baseline → execute → review → independent QA

1. Record HEAD, tracked/untracked changes and protected paths. Review registry policy, the selected installation and `routine-check`; compare the actual remote Routine manually. Configuration or a successful check does not authorize a send.
2. Prepare a narrow manifest; if using globs, confirm every expanded file and the count. Select the registered test ID by its real meaning. Validate, grant and submit through the existing separate gates, once per identity.
3. Review claim-time baseline, result identity, native Git diff, `out_of_scope_changes`, `git_review`, ignored writes and the measured test output. Resolve other writers' contributions manually. HTTP 200 and local publication are not acceptance.
4. Run independent QA in a suitable environment. `qa_accepted` remains false. A green check does not authorize a commit, release, public visibility or deployment; each requires its applicable authorization.

## Failure and audited recovery

**Manual ledger deletion is prohibited. `ledger-archive` is the only supported ledger-archiving recovery path.** Existing cancellation of a provably unused authorization remains a separate operation. Archiving retires an identity; it never retries, creates a replacement identity to repeat uncertain work, changes frozen failure bytes, repairs a result, cancels remote execution or enables a switch.

First inspect the original task and remote Routine. A timeout may still produce a late result. Preview the exact task locally:

```powershell
grokbot-mcp ledger-archive --registry $Registry --workspace $Workspace --task-id $TaskId --reason 'Reviewed local binding failure; retire this task'
# Only after reviewing the preview:
grokbot-mcp ledger-archive --registry $Registry --workspace $Workspace --task-id $TaskId --reason 'Reviewed local binding failure; retire this task' --confirm
# Nonterminal tasks/executions additionally require --accept-late-result-risk.
```

Without `--confirm` the command is read-only, creates no locks and prints the planned moves and required confirmations. Every nonterminal record is conservatively treated as capable of a late result, including timeout and unknown submission. The extra risk flag does not authorize resending it. The task must have a readable matching task ledger and task spec; corrupt/unverifiable evidence is refused rather than “repaired.”

With confirmation, the command rechecks under the existing validation and execution locks. It moves the task spec, validation ledger and all associated execution ledgers to `archive/<workspace_id>/<UTC timestamp>_<task_id>/` beside the registry. Each original file's bytes and SHA-256 are retained. `audit.json` is created exclusively and never rewritten; it records the reason, local operator name, UTC time, states, risk acceptance and source hashes. This is application-level immutability, not an OS administrator attestation or protection from an administrator manually tampering with files. Keep the archive private. Results, claims, baselines and cleanup receipts remain untouched in their original locations.

Before moving evidence, an immutable `.bridge/ledger_archives/<task_id>.json` journal permanently refuses that task and its execution IDs in manifest loading, publication, observation and ledger reuse. A late result is reported as `archived_task`, never accepted. Successful completion removes the old records from the blocking ledger set and writes separate completion receipts. It permits separately authorized **new work**, not replay of the archived submission.

An interrupted move leaves a workspace refusal barrier. Rerun the same `ledger-archive` command with confirmation (and risk acceptance when required) to resume only the original hash-bound plan; the original audit is never rewritten. Changed evidence refuses resumption. Keep journal, archive and receipts together; never remove the barrier manually. This implementation uses Windows rename without a copy/delete fallback: the registry archive and workspace must be on the same volume. Cross-volume or permission failures leave evidence preserved and may require the same plan to be resumed after the filesystem issue is resolved. Do not relocate a live registry or evidence to bypass refusal.

## Known limits

Out-of-scope reads are undetectable; ordinary gitignored writes are not visible to advisory Git review. Registered protected/forbidden paths remain watched. Concurrent writes cannot be attributed to an agent. Review them independently. Remote result JSON must be UTF-8 with exact normalized paths and matching identity; do not guess how to repair corrupted path encoding or replace a frozen result. Test IDs are administrator mappings, not arbitrary shell commands; the `test_command_cli` helper previews approved arguments and does not execute them. Local consistency cannot prove remote Routine contents, desktop connectivity or model behavior.

## Prefix ceiling

Optional `prefix_ceiling` has the same syntax and normalization as
`allowed_prefixes`, for example `[{"prefix":"docs/","exts":[".md",".txt"]}]`.
Each active prefix must equal or descend from one ceiling entry, and its extension
set must be a subset of that same entry. Invalid expansion returns
`prefix_outside_ceiling` before any file is written. Exact `allowed_files`
exceptions keep their existing semantics; forbidden and protected paths still win.
An empty active list is allowed with a ceiling and grants no directory access.

The ceiling replaces active prefixes in the policy fingerprint and generated Routine
profile. Changing the ceiling or forbidden/protected policy still requires updating
and repasting the Routine and confirming its binding. Within-ceiling expansion or
contraction needs no repaste. Without a ceiling, policy hashing and Routine policy
content retain their previous behavior (the generated release banner carries the new version).

Task preparation freezes the current grant as manifest `active_prefixes`. Claim and
result verification use this snapshot, not the latest registry active list. Claim
checks the frozen grant against the current ceiling again, refusing with
`active_prefixes_outside_ceiling` if invalid, and returns `active_prefixes` plus
`active_prefixes_sha256`. The hash uses SHA-256 of UTF-8 JSON with sorted keys,
ASCII escaping and compact comma/colon separators. A Routine must stop when claim
prefixes exceed its embedded ceiling or their hash differs. Shrinking active
prefixes affects future tasks; it does not revoke an already prepared grant.

The existing local `registry-prefixes` command now supports repeated `--add` and
`--remove`. Removal runs first; adding an existing prefix merges extensions. To
replace its extension set, remove and then add that prefix in the same command.
These fictional paths illustrate preview and explicit apply:

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.admin_cli registry-prefixes --registry 'C:\ExampleBridge\registry.json' --workspace example-workspace --add 'docs/:.md,.txt' --remove 'docs/old/'
# After reviewing the preview, explicitly apply the same operation:
& $BridgePython -I -X utf8 -m grokbot_mcp.admin_cli registry-prefixes --registry 'C:\ExampleBridge\registry.json' --workspace example-workspace --add 'docs/:.md,.txt' --remove 'docs/old/' --apply
```

Without `--apply`, no files are written. Apply first saves the exact original bytes
as `registry.json.bak_<UTC timestamp>`, atomically replaces the registry, and appends
`policy_changes.jsonl` beside it. Audit records contain UTC time, workspace, old/new
prefix sets and fingerprints; fingerprints match in ceiling mode. No credentials,
bindings or switches are changed. Audit is retrospective traceability, not a gate;
a crash between registry replacement and audit append can leave a missing event.
`policy_audit_write_failed` with `applied: true` means the registry was committed.
The old suggestion/copy mode remains available without edit flags; `--write-to`
cannot be combined with edit flags. This is not an OS sandbox.

Preflight and workspace listing expose the ceiling and current active prefixes.
Preflight also reports `stale_execution_records` without writing the ledger. Before
preparing a new execution, arrived results are settled by the ordinary status-query
path. Terminal failures cease to block; unresolved records still return
`concurrent_execution` with `blocking_execution_id`. Unknown submissions cannot
be resent. `runtime_drift` compares startup/current package and registry hashes;
changed package bytes add `process_restart_recommended` to `warnings`, not
`blocking_reasons`. Restart the MCP process and check its identity after installation
changes; these hints do not authorize execution or establish remote reachability.
## Local validation

An administrator may opt an advisory workspace into `"local_validation": true`.
It defaults to false and is rejected with a strict Git gate. Only true is added
to the policy fingerprint; omitted/false retains existing policy and Routine
content (apart from the release version banner). Enabling it changes the policy
identity, so the execute Routine binding must be explicitly reviewed again.

`grok_submit_validation` then checks the manifest locally without credentials or
a webhook. No execution switch is needed for this check; `writes_enabled` is
still required to publish evidence. `grok_prepare_execution` performs this check
automatically only when neither a validation ledger nor a result exists. Existing
and uncertain submissions keep their original handling and cannot be retried.

The bridge reuses admission checks for manifest identity, digest, forbidden paths,
exact scope and frozen prefix ceilings. Advisory HEAD semantics remain: divergence
from the registered HEAD alone is not a failure; unreadable Git state, invalid
manifest baselines and protected changes still refuse. Claim retains its full
baseline and scope checks. The hash is captured at local admission and checked
again during assessment; it is not an external attestation of earlier file bytes.

Results use `.bridge/results_v2/<task_id>.json`, the shared atomic publisher and
the normal observation/freeze path. They include `validator: "bridge_local"` and
the policy fingerprint; failures contain `status: "failed"` and `validation_errors`.
The submission response includes `webhook_sent: false` and `validated_locally: true`.
Claim and execution verification accept these proofs only while local validation
remains enabled under the same fingerprint. Frozen failures are never replaced.

`grok_get_status(wait_seconds=0)` keeps the existing response. An integer from 1
through 50 enables read-only polling at 1, 2, 4, 5, 5… seconds (bounded by the
remaining time). A terminal state or a complete validated result returns early;
the response adds `waited_ms`. After polling, one normal query uses the original
options. `observe_only` remains read-only. With `rerun_tests`, waiting comes first,
then a normal reconciliation and a local rerun only for a terminal execution;
`rerun_tests` still cannot be combined with `observe_only`.

