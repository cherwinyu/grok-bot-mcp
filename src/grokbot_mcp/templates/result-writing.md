## Result writing: {{TEMPLATE_VERSION}} / 结果写回契约

Bridge candidate: {{BRIDGE_VERSION}}. This section is expanded by the local wizard/migration generator. Do not run the unexpanded template.
The only valid integer measurements are actual observed counts. JSON `true`/`false`, strings and negative counts are invalid. `tests.passed` and `tests.failed` use the same `tests.unit` (`test_cases` by default for legacy results, or `assertions`). `case_count` and `assertion_count` are separate optional actual measurements. For example, 6,561 coordinate cases can execute 19,683 assertions; these are NOT interchangeable. Do not infer numbers from prose or copy fictional examples into a real result. `exit_code` is the actual integer process exit status, not a count or a boolean. A negative actual exit status is permitted for a genuine failed run.

`pending` and `running` require complete identity and need not contain counters. Do not state that unrun tests passed. A code failure before testing uses `status: failed` and `tests: {command_id: <registered ID>, not_run: true}`, with no invented counts/exit code. A real failed test uses measured counts and its real exit code. `succeeded` requires zero failed tests and exit code zero. Validation success also requires no source modification, no validation errors and `validation_passed: true`.

{{REQUIRED_TEST_OUTPUT}}First write the intended UTF-8, no-BOM result JSON to the one private candidate path, NOT the final result and NOT a name invented inside the workspace. The private candidate is `$env:TEMP\grokbot-mcp-candidates\{{PHASE}}\.<id>.result.tmp`. That filename is the same `.<id>.result.tmp` name as the official staged file. Validate and execute use the same filename rule; the phase directory and the later staged directory differ. `result_cli --source` accepts only that temp path and rejects every other location, including any path inside the registered workspace. `.result.candidate.json` and `.candidate_<id>.json` are not protocol names. Do not create them.

The helper then writes the staged file `.bridge/results_v2/.<task-id>.result.tmp` or `.bridge/results_execute/.<execution-id>.result.tmp` and publishes `<id>.json` in that same directory. It never accepts an arbitrary destination. Review the measured fields before running these commands.

After a terminal publish (`succeeded` or `failed`), the helper deletes that id's staged file and, when the temp candidate bytes are exactly the published bytes, the temp candidate. It records the cleanup in `.bridge/protocol_cleanup/{{PHASE}}/<id>.json`. It does not modify the submission ledger or the published result, and it does not delete another id or a non-contract name. Do not delete `.bridge` files yourself. If a contract temp file remains, rerun the same command with `--cleanup`. Pending and running publishes leave the staged file in place.

```powershell
# Use the {{ID_FIELD}} received and verified for THIS accepted task; never reuse an old ID.
# Set these in THIS process. A later background shell does not inherit them.
$id = '<received {{ID_FIELD}}>'
$python = {{PYTHON_PS}}
$registry = {{REGISTRY_PS}}
$workspace = {{WORKSPACE_PS}}
$root = {{ROOT_PS}}
$candidateDir = Join-Path $env:TEMP 'grokbot-mcp-candidates\{{PHASE}}'
New-Item -ItemType Directory -Force -Path $candidateDir | Out-Null
$candidate = Join-Path $candidateDir ('.' + $id + '.result.tmp')
# Replace the here-string with the measured result for THIS id. Do not pass JSON to python -c.
$json = @'
<measured UTF-8 result JSON for this id>
'@
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($candidate, $json, $utf8)
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
