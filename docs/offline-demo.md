> New executable offline entry: `python -I -X utf8 -m grokbot_mcp.safety_cli demo`. It owns a disposable fixture, uses memory-only transport, measures two Python tests and cleans up. The walkthrough below remains a static explanation, not live acceptance.

# OFFLINE / STATIC demonstration: a small counter change

[中文](offline-demo.zh-CN.md) · [Quickstart](quickstart.md)

**This page is a fictional walkthrough, not a recording of Grok Bot success.** No live Routine, credential or Webhook was used to produce it. Response fragments below describe the required checks, not captured outputs. `<task_id>` and `<execution_id>` are placeholders: use the exact IDs returned by your client, never the literal placeholders or made-up replacements.

## Preparation and human checkpoint

Complete the quickstart on `demo-workspace`, using the shipped `examples/minimal_workspace` copy. The existing function `count_above` counts strictly above a threshold. Our task will **add** `count_at_least`, retain the existing behavior, and add boundary/empty-input tests. Only `src/counter.py` and `tests/test_counter.py` may change; `README.md` is protected.

Run the wizard's `--check` and call `grok_preflight`. With switches off, installation may be complete while submission is blocked. **For a real trial only**, review the generated Grok Bot packet, confirm the actual upgraded Routines, then explicitly enable workspace writes and the separate submission switch as documented in [advanced setup](advanced-setup.md). This tutorial does not do so. Neither local readiness nor administrator confirmation proves the remote result.

Before the real trial, review `git status --short`, `git diff` and the registered baseline. Stop on unexplained changes. Do not reset the workspace or auto-advance the baseline. Keep credentials out of task arguments.

## Prepare the task (local; no Webhook)

Call `grok_prepare_task` with:

```json
{
  "workspace_id": "demo-workspace",
  "task_type": "code_change",
  "task_description": "Add count_at_least(values, threshold) without changing count_above. Add unit tests for equality at the boundary and empty input.",
  "allowed_files": ["src/counter.py", "tests/test_counter.py"],
  "acceptance_criteria": [
    "count_above retains strictly-greater behavior",
    "count_at_least includes values equal to threshold",
    "An empty list returns zero",
    "Only the two allowed files change; the protected README remains unchanged",
    "Run the registered python_unittest tests"
  ],
  "test_command_id": "python_unittest"
}
```

Expect a generated `task_id`, `manifest_sha256`, `validation_status="valid"`, and `webhook_sent=false`. This writes a local manifest, not code. **Human check:** inspect the requested scope and criteria before sending.

## Submit validation once

Call `grok_submit_validation` with `{"workspace_id":"demo-workspace","task_id":"<task_id>"}`.

A successful HTTP acceptance fragment is `{"accepted":true,"http_status":200,"task_state":"accepted","qa_accepted":false}`. It means the request was accepted, **not** that validation ran or passed. The validate Routine must check the actual manifest/workspace and write its proof without modifying code.

If the response says `unknown_submission`, `submission_started`, timeout, or retry forbidden, do not submit again, manufacture a new ID, or manually delete the ledger. Check the same task in Grok Bot and query it locally. A switch-blocked call that explicitly never attempted submission is different; follow its exact diagnostic rather than guessing. The only supported ledger-archiving recovery path is [ledger-archive](advisory-workspaces.md#failure-and-audited-recovery).

## Observe and synchronize the successful validation

Call `grok_get_status` with:

```json
{"workspace_id":"demo-workspace","task_id":"<task_id>","observe_only":false}
```

`pending`, `running`, or a partial file are not success. Wait by querying the same ID; queries send no Webhook. Require the nested `validation.status="succeeded"`, `validation.validation_passed=true`, `validation.code_modified=false`, and a persisted terminal decision. `query_mode="reconcile"` is the normal synchronization mode; `ledger_updated` need not stay true on later queries when the terminal decision is already persisted.

`observe_only=true` can show a successful observation **without writing the decision to the ledger**. Execution preparation rejects such an unsynchronized proof with `validation_not_verified`. Never replace a failed proof with a successful one or construct a missing ledger. A failed validation remains failed.

## Prepare execution (local; no Webhook)

Call `grok_prepare_execution` with `{"workspace_id":"demo-workspace","manifest_task_id":"<task_id>"}`.

Require `approval_state="granted"`, `submission_state="not_sent"`, `execution_state="prepared"`, a generated `execution_id`, and no error. The authorization freezes validation evidence, baseline and binding. It does not run the task. **Human check:** the IDs and scope must correspond to the validation you just reviewed.

A provably unused grant can be checked with the wizard's `--check --execution-id <execution_id>`. Only an explicit `local_cancellation_available` snapshot justifies considering the existing local cancel CLI; that command rechecks under lock. Never cancel a sent, claimed, uncertain, or result-bearing task to make it retryable.

## Submit once, then query

Call `grok_submit_execution` with `{"workspace_id":"demo-workspace","execution_id":"<execution_id>"}` exactly once. HTTP 200 is still acceptance only. The execute Routine must use the generated absolute-interpreter claim command; schema-v2 claim bytes and `claim_sha256` must be real, not synthesized for a legacy entry.

Then call:

```json
{"workspace_id":"demo-workspace","execution_id":"<execution_id>","observe_only":false,"verify_current":true}
```

Require the nested `execution.status="succeeded"` and `execution.current_integrity=true` with current verification metadata. Keep `qa_accepted=false` visible. A historical `succeeded` with `current_integrity=false` is **not** current verified success. Pending, timeout, or uncertain submission never permits an automatic retry.

## Human acceptance of the actual difference

After a real successful trial, independently run the registered tests from the demo root and inspect:

```powershell
git -C C:\ExampleWorkspace status --short
git -C C:\ExampleWorkspace diff -- src/counter.py tests/test_counter.py
git -C C:\ExampleWorkspace diff -- README.md
```

Verify equality and empty-input behavior, preservation of `count_above`, the actual changed-file set, and the unchanged protected file. Turn the submission switch off when the trial is over. Do not automatically commit, advance the registered baseline, delete `.bridge`, or label the result independently accepted. Human acceptance is an explicit review outside the bridge's permanently false `qa_accepted` field.

## Reproducible offline evidence

The installed-wheel suite uses temporary fictional Git workspaces, in-memory credentials and Mock transport. It exercises prepare → mock validation acceptance → write fictional proof → reconcile → grant → mock execution acceptance → real local claim → write fictional result → verify. It also tests failure/tampering/unknown-submission branches. These are **offline protocol tests**, not validation of your Grok account, Routine UI, filesystem permissions of the Bot, or model output quality. See [development](development.md) and the candidate's CI evidence.
