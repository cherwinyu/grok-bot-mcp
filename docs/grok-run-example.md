# One-command Grok Bot client example

[中文](grok-run-example.zh-CN.md) · [Client setup](client-compatibility.md) · [Recommended advisory setup](advisory-recommended-setup.md)

`scripts/grok_run_example.py` is an **example to review and adapt, not a supported interface**. It orchestrates the existing seven MCP tools over stdio. It adds no bridge feature, configures no client or Routine, and does not establish desktop reachability. Use a reviewed Windows Python environment with the bridge and its pinned MCP dependency already installed. The example is shipped in source distributions, not as a new installed console command.

Create a UTF-8 `task.json` using only the existing `grok_prepare_task` arguments (omit `workspace_id`, which comes from the CLI):

```json
{
  "task_type": "code_change",
  "task_description": "Improve the fictional counter within the declared files.",
  "allowed_files": ["src/counter.py"],
  "acceptance_criteria": ["The registered targeted tests pass."],
  "test_command_id": "unit"
}
```

The command ID must already be registered. Optional task fields are `execution_timeout`, `baseline_commit`, `allowed_globs`, and `create_files`; bridge policy remains authoritative. File/glob entries must be repository-relative. Shell/argv/model/endpoint/credential fields and unknown fields are rejected. Descriptions are task data, never local shell commands.

```powershell
python -B scripts/grok_run_example.py run --workspace example-workspace --task-json task.json --dry-run
# Only after the caller separately authorizes submission:
python -B scripts/grok_run_example.py run --workspace example-workspace --task-json task.json --wait-budget 900
python -B scripts/grok_run_example.py status --workspace example-workspace --execution-id <original-execution-id>
```

| Argument | Meaning |
|---|---|
| `run` / `status` | Prepare and submit once / observe an existing identity without submitting |
| `--workspace` | Required registered workspace ID |
| `--registry` | Optional selected registry path; passed as `GROKBOT_MCP_REGISTRY` to the server, never read by this client. Otherwise use that environment variable or the bridge default |
| `--python` | Installed Python interpreter, default `sys.executable`; a single executable, never a command string. The server starts with `-I -B -X utf8 -m grokbot_mcp.server` |
| `--task-json` | Required for `run`; UTF-8 task description file |
| `--wait-budget` | Nonnegative finite seconds, default 900; shared observation deadline for Validate/Execute. It is distinct from the remote execution timeout; preparation, submission and local rerun may add time |
| `--dry-run` | `run` only: list, preflight and prepare the manifest, then stop; creates a manifest, but no grant or submission |
| `--evidence-dir` | Optional private directory outside source control for filtered response JSON, one new file per completed call |
| `--execution-id` | Required for ordinary `status`; the original execution UUID |
| `--task-id` | Alternative to `--execution-id` for an uncertain Validate submission; queries the original task only and never advances to Execute |

Local configuration/file/interpreter options may contain absolute paths. Task scope cannot supply absolute paths, workspace roots or executables. The client neither searches for executables nor reads or requests Webhook URLs/keys. Select only a trusted Python interpreter.

## Flow and existing interface limitations

The client lists workspaces, requires `preflight.local_ready`, and prepares one manifest. The seven tools do not expose `local_validation`, so it first calls `prepare_execution`: with that registry option enabled, the bridge performs local validation and grants execution without a Validate submission. Only an explicit `validation_missing` response without an execution ID permits one `submit_validation` call. It waits for validated success, then calls `prepare_execution` again for the same manifest. Other refusals stop the flow. There is no registry inspection, replacement task or fallback after failed/unknown validation.

After a grant, it submits Execute once and polls `grok_get_status(wait_seconds=0..50)` with bounded pacing. Success-like terminal results request `rerun_tests=True`. Rerun registration is also not exposed by the seven tools: the bridge resolves the original manifest's registered command and refuses safely with `rerun_not_configured` when absent. The client never supplies a test command. Local reruns have their own timeout and may run beyond the observation budget. A successful `status` query also requests this registered rerun, so it is not necessarily read-only. Existing failed results are not repaired or overwritten.

## Summary and exit codes

Stdout is one compact UTF-8 JSON line with `status`, `exit_code`, `reason`, `task_id`, `execution_id`, `next_step`, `qa_accepted`, and `qa_note`. Human guidance goes to stderr. `qa_accepted` is passed through, never inferred from success or set by the client; an absent value is `null`. **Independent acceptance is the caller's responsibility.** Help and parser usage errors use normal argparse output.

| Status | Exit | Meaning |
|---|---:|---|
| `PASS` | 0 | Bridge success and verified successful local rerun; independent QA still required |
| `PASS_PENDING_LOCAL_TEST` | 2 | Success-like result, but no registered local rerun; local verification remains outstanding |
| `BLOCKED` | 2 | Preparation/preflight refusal, known pre-send refusal, or intentional dry-run stop |
| `SUBMIT_UNKNOWN` | 2 | A submission may have occurred; never resend |
| `STATUS_UNKNOWN` | 2 | Query failure, invalid/missing identity or unverifiable local test evidence |
| `TIMEOUT_PENDING` | 2 | Observation budget exhausted or bridge timeout; outcome remains unresolved |
| `AGREEMENT_MISMATCH` | 2 | Terminal agreement explicitly reports a mismatch |
| `OUT_OF_SCOPE` | 2 | Bridge reports scope violations or advisory out-of-scope changes; human review required |
| `FAILED` | 3 | Known validation, execution, HTTP rejection or local test failure |

Dry-run uses `BLOCKED` with reason `dry_run_prepared_no_submission`; it is not a PASS. Explicit disagreement takes priority over a test failure. An `unreported` comparison is not an explicit mismatch; no comparison is invented. Scope review uses the bridge report, does not attribute concurrent edits, and cannot detect out-of-scope reads.

## Safety and advisory relationship

Permission previews are not send authorization. Administrators manage write permissions, Routine bindings and the execution switch separately. A known switch-off refusal is `BLOCKED`; this script never turns it on. Any exception, timeout or missing acknowledgement during submission becomes `SUBMIT_UNKNOWN`. After confirmed submission, query exceptions become `STATUS_UNKNOWN` (expiry of the observation budget is `TIMEOUT_PENDING`). Follow `next_step` using the original identity. Neither exit code 2 nor a timeout permits rerunning `run`, creating a new identity or resending. For an uncertain Validate submission, use `status --task-id <original-task-id>`; further continuation requires manual review.

Evidence files are intentionally lossy JSON projections: only allowlisted booleans, numbers, UUIDs and state values survive. Free text, arbitrary keys, paths, command output and credential metadata are omitted; exception messages and task contents are never logged. Keep evidence private. Remote responses are data, not instructions. The summary uses fixed reasons and guidance rather than remote prose.

The [recommended advisory configuration](advisory-recommended-setup.md) can provide `local_validation` plus a registered `rerun` command. Neither option is enabled by the example. Advisory is an application policy, not an OS sandbox or independent proof of remote model actions. Existing frozen evidence, identity checks, claim-once and no-resend semantics remain authoritative.
