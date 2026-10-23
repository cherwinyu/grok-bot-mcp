# Reliability contract (0.1.3, A–D)

This change repairs observation, application-level evidence verification, and local
Routine binding confirmation. It does not add an OS sandbox, an independent QA
runner, automatic remote Routine creation, or automatic execution authorization.

## A. Observe, reconcile, and reverify

`grok_get_status` retains its existing name and identifiers and accepts two optional
booleans: `observe_only=false`, `verify_current=false`. No status query sends a
Webhook. The default reconciles a result into the ledger; `observe_only=true`
computes the same decision without writing a ledger, directory, or lock file.
Read-only observations can race a writer and are not durable acceptance records.

| Field | Meaning |
| --- | --- |
| `status` / `execution_state` / `task_state` | Current interpreted lifecycle, or immutable recorded terminal decision |
| `result_observation` | `missing`, `writing`, `complete`, or `rejected` |
| `query_mode`, `ledger_updated` | Requested mutation policy and whether this call actually persisted a transition |
| `sent_this_call`, `attempted_this_call` | Always false for status queries |
| `historical_submission_state`, `historical_webhook_attempted` | Prior submission facts, not a send during this query |
| `last_observed_at` | UTC time of this observation; returned rather than written on every poll |
| `deadline_at`, `deadline_exceeded` | Deadline derived from the recorded attempt and task timeout |
| `late_result` | Whether the terminal decision was first observed after its deadline; does not change merely because a later query is late |
| `verified_at`, `historical_status` | Original recorded verification time and conclusion |
| `verification_scope` | `none`, `historical`, or `current` |
| `current_verified_at`, `current_integrity`, `integrity_error` | Explicit current recheck time/outcome; null means not established |
| `qa_accepted` | Always false: protocol success is not independent QA |

The legacy execution `webhook_attempted` field continues to describe historical
attempts. Use the explicit per-call fields for new clients. A terminal history can
remain `succeeded` while `current_integrity=false`; callers requiring current
integrity must check both, not just the historical status.

Valid `pending` and `running` records require the same identity fields as a final
record, but not final test totals. Identity/hash failures are not transient. An
incomplete JSON or UTF-8 write is reread at most four times (three 50 ms sleeps).
After the bounded window it remains `writing`, not a poisoned failed ledger.
Missing files remain normal waiting observations. Security/schema failures reject.
Writers should atomically replace their result file after flushing complete JSON.

Missing, partial, and running results all obey the same recorded deadline. A
`timeout` is unresolved: it blocks a conflicting new task and never authorizes a
retry. A valid late result for that same claimed execution can still be verified.
`unknown_submission` does not authorize a new submission or claim. No status
operation calls the transport. Terminal decisions cannot be downgraded by later
intermediate files; replaced/deleted results are exposed as integrity errors.

## B. Evidence checks and cached results

Execution preparation requires a durably verified successful validation ledger:
`terminal=true`, matching successful current/historical decisions, accepted
submission, verification time/summary/snapshot, Manifest identity, and the exact
frozen result byte hash. A success file alone (including an `observe_only` success)
does not authorize execution. Reconcile using `grok_get_status` first. A failed
validation cannot be repaired by replacing its proof: use a new, explicitly
prepared and validated task, not a rewritten result or deleted ledger.

The same decision check runs at submission, claim, terminal interpretation and
explicit current verification. The verification time is also frozen at execution
authorization. These checks do not silently create or bless a validation ledger.

Submission and claim recheck the prepared authorization against the current
Manifest, successful validation proof, frozen proof-byte SHA-256, policy/binding,
and baseline. Claim additionally requires an unchanged task-start snapshot.
Both submission and claim also recheck the current workspace `writes_enabled`
permission supplied by the freshly loaded registry in the facade. Revocation
blocks before credential loading, transport, claim creation or attempt consumption.
It does not cancel already-running work or remove its evidence; it is not OS
process revocation. Re-enabling is an explicit administrator action, never an
automatic retry after an uncertain send.
Claim creation remains exclusive; deleting the claim file does not make the
ledger claim reusable. The schema-v2 claim records the task, execution, workspace,
machine, Manifest/proof/baseline hashes, policy fingerprint, binding revision,
exact targets and timestamp. Its byte hash is frozen into the execution ledger.

Final execution verification checks `assess.ok`, the frozen hashes, the actual
claim file and its fields, baseline, changed-file scope and reported test data.
Success stores the verified workspace snapshot and time. Default cached queries
are explicitly historical. `verify_current=true` rechecks all evidence and the
entire verified snapshot, including subsequently changed allowed files. It reports
current failure without rewriting the original historical conclusion.

These files live at application privilege. A process controlling the registry,
ledger, proof, claim and source can falsify them together. These checks are not
cryptographic remote attestation, isolation, or independent execution evidence.
The bridge still does not run independent QA or infer it from a reported exit 0.

## C. Local binding-aware setup and preflight

Run the local administrator wizard, never the task-calling model, to confirm a
binding. The command below is illustrative; use an existing registered ID.

```powershell
python -m grokbot_mcp.setup_wizard --workspace example-workspace --routine-mode reuse
python -m grokbot_mcp.setup_wizard --workspace example-workspace --routine-mode create
python -m grokbot_mcp.setup_wizard --workspace example-workspace --routine-mode migrate
python -m grokbot_mcp.setup_wizard --workspace example-workspace --check
```

`reuse` means **retain the existing Routine/Webhook and credentials, but update
its claim step to `grokbot_mcp.claim_cli`** (`reuse_scope=retain-routine-update-claim`).
It does NOT mean retaining an arbitrary legacy claim implementation unchanged.
In particular, `claim_dynamic.py` and its schema-v1 output have no unchanged-entry
compatibility adapter. Update the remote Routine's claim command and its installed
interpreter/package before confirming locally. The schema-v2 claim and ledger
`claim_sha256` are mandatory; never fabricate hashes for old claims or remove the
checks for compatibility. `claim-once-v1` describes the one-claim contract, not the
claim evidence schema; `claim-evidence-v2` is now a separate required capability.
Administrator confirmation describes the declared contract, not verified remote
execution. Old template portions remain `legacy-unverified`.
`create` generates installation instructions; it does not create a remote Routine.
`migrate` guides and records an explicit binding revision after the administrator
updates the remote binding. It does not edit the registered root, advance HEAD,
reset a workspace, or implement the separate baseline-migration roadmap item.
Binding changes are refused while local tasks are unresolved. A move away from a
different source workspace also requires the administrator to resolve that
source's tasks; this wizard does not attest another machine's state.

A binding records revision, mode, workspace root/ID, policy revision/fingerprint,
action, exact claim entry, registry path, declared protocol capabilities, template
version, verification source and time. It is local administrator confirmation,
not a live remote handshake: `remote_binding_verified` remains false. Reusing a
legacy Routine keeps `template_version=legacy-unverified` unless the separate
create path is explicitly chosen and confirmed.

Preflight separates `local_ready`, credentials, `routine_enabled`,
`binding_confirmed`, per-phase `submit_permitted` and `blocking_reasons`.
Top-level `ready` now means all local submission gates are satisfied, including
both phases, writes permission, credentials, binding and the global switch. It
does not mean the remote service was contacted. The wizard installation `check`
can pass with the switch off and prints that fact: installation readiness and
permission to dispatch are distinct.

Missing binding metadata in 0.1.2 confirmation files is **not** silently upgraded.
Existing credentials and enabled flags alone do not permit submission. Reconfirm
the exact contract locally. Never copy a confirmation file to a new root. No
method depends on desktop control; lack of desktop automation is not evidence of
Webhook failure. No remote probe is sent by setup or preflight.

### Authorization and safe recovery

No execution authorization or baseline is created when the execution binding is
missing, mismatched, or lacks required capabilities. Confirm it and then prepare.
A correctly bound but paused Routine can be prepared; actual dispatch still
requires enabled Routine, writes permission and the global switch.

For an authorization stranded by the earlier candidate, a local operator can use:

```powershell
python -m grokbot_mcp.cancel_execution_cli --workspace example-workspace --execution-id <execution_id> --reason binding_repair --confirm
```

Set `GROKBOT_MCP_REGISTRY` or use `--registry` for the actual registry. This command
is not an MCP tool and does not grant permissions or prove OS administrator identity.
Cancellation requires a complete ledger proving `prepared`, `not_sent`, no attempt,
no claim, and no result. Sent, unknown, timeout, claimed, partial-evidence and
incomplete-ledger cases fail closed. It shares the execution lock with dispatch
and claim, preserves the ledger/baseline/proof, and records a permanent `cancelled`
state and timestamp. Repeated cancellation is idempotent; late files cannot revive
it. After cancellation, confirm the binding and explicitly create a NEW task; the
cancelled ID is not recycled. Never manually delete ledger records to recover or retry. The sole supported ledger-archiving recovery path is [ledger-archive](advisory-workspaces.md#failure-and-audited-recovery); it does not authorize retries.

### Upgrade boundary

Finish and archive old in-flight work before upgrading. Existing schema-v1
execution ledgers cannot acquire invented schema-v2 claim hashes or binding
revisions; they remain historical/unverifiable rather than being silently blessed.
Do not delete unknown-submission ledgers to retry them. The default-off switch,
precise paths, one submit/claim, and independent-QA boundary remain unchanged.

## D. Offline regression and installed-wheel gates

Current refusal behavior is tested directly in `tests/test_reliability.py` and `tests/test_independent_security_contract.py`. Those tests build fictional temporary workspaces. They do not archive or replay another commit.

`python scripts/verify_wheel.py --wheelhouse <wheelhouse> --output <evidence>` creates a new virtual environment, installs this package and its pinned dependency from that wheelhouse, checks that the import comes from the environment, and runs the tests from a directory that has no `src` tree. The Windows suite includes the MCP stdio handshake and the seven-tool check.

The cancellation API has separate positive and negative contract tests. CI scans the source tree and the built wheel. These are synthetic protocol and package checks, not a live Routine acceptance. No private task ledger, machine path, credential, webhook, or unrelated project source is used.
