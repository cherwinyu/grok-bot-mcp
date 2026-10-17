## Result writing: {{TEMPLATE_VERSION}} / 结果写回契约

Bridge candidate: {{BRIDGE_VERSION}}. This section is expanded by the local wizard/migration generator. Do not run the unexpanded template.
The only valid integer measurements are actual observed counts. JSON `true`/`false`, strings and negative counts are invalid. `tests.passed` and `tests.failed` use the same `tests.unit` (`test_cases` by default for legacy results, or `assertions`). `case_count` and `assertion_count` are separate optional actual measurements. For example, 6,561 coordinate cases can execute 19,683 assertions; these are NOT interchangeable. Do not infer numbers from prose or copy fictional examples into a real result. `exit_code` is the actual integer process exit status, not a count or a boolean. A negative actual exit status is permitted for a genuine failed run.

`pending` and `running` require complete identity and need not contain counters. Do not state that unrun tests passed. A code failure before testing uses `status: failed` and `tests: {command_id: <registered ID>, not_run: true}`, with no invented counts/exit code. A real failed test uses measured counts and its real exit code. `succeeded` requires zero failed tests and exit code zero. Validation success also requires no source modification, no validation errors and `validation_passed: true`.

First write the intended UTF-8, no-BOM result JSON to a private candidate file, NOT the final result. The candidate file is only input. The helper derives the same-directory staging path and final result path from the registry and accepted task identity; it never accepts an arbitrary destination. Review the measured fields before running these commands.

```powershell
# Use the {{ID_FIELD}} received and verified for THIS accepted task; never reuse an old ID.
$id = '<received {{ID_FIELD}}>'
$candidate = '<absolute private candidate JSON file>'
$python = {{PYTHON_PS}}
$registry = {{REGISTRY_PS}}
$workspace = {{WORKSPACE_PS}}
$root = {{ROOT_PS}}
$base = @('-I','-X','utf8','-m','grokbot_mcp.result_cli',
          '--registry',$registry,'--workspace',$workspace,'--phase','{{PHASE}}','--id',$id)

# Default: JSON/types/stage/identity/evidence checks only; no files or ledger changes.
$plan = & $python @base --source $candidate | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $plan.ok -ne $true) { throw 'result_precheck_failed' }

# Explicit, hash-confirmed staging. Overwriting our own temporary file needs its old hash too.
$stageArgs = @('--source',$candidate,'--stage','--confirm-sha256',$plan.staged_sha256)
if ($plan.previous_staged_sha256) { $stageArgs += @('--previous-staged-sha256',$plan.previous_staged_sha256) }
$stagedReport = & $python @base @stageArgs | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $stagedReport.staged_written -ne $true) { throw 'result_staging_failed' }
$staged = Join-Path $root $stagedReport.staging_path

# Recheck the actual staged bytes before explicit atomic publication.
$checked = & $python @base --staged $staged | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $checked.ok -ne $true) { throw 'result_staged_precheck_failed' }
$published = & $python @base --staged $staged --publish --confirm-sha256 $checked.staged_sha256 | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $published.published -ne $true) { throw 'result_publication_failed' }
```

`format_valid` / `published` is NOT bridge acceptance, actual testing or human QA. Existing claims, original claim hashes and validation proof remain authoritative. The helper neither creates a claim nor changes a ledger/permission. Intermediate -> terminal is allowed; running -> pending and ANY terminal overwrite are refused, including an unobserved terminal file. A frozen failed proof cannot be repaired by replacement. Fix an unpublished candidate only; never respond to format errors by resending a Webhook, allocating a retry ID or rerunning the entire task.

Executable fictional examples (read-only): `python -m grokbot_mcp.result_examples`. Select any example with `--example validate-succeeded`, `validate-failed`, `execute-succeeded`, `execute-failed`, `validate-pending`, `validate-running`, `execute-pending`, `execute-running`, `execute-failed-before-tests` or `execute-distinct-case-and-assertion-counts`.
