# grok-bot-mcp

**Local MCP bridge for Windows: hand a scoped coding task to your Grok Bot Routine once, then verify the result with Git diffs and structured evidence—not a "done" chat reply.**

[简体中文](README.zh-CN.md) · [Quickstart](docs/quickstart.md) · [Cursor / Codex CLI](docs/client-compatibility.md) · [Beta support](docs/beta-support.md) · [Security](SECURITY.md)

**0.1.5b1 experimental Beta line.** Check GitHub Releases for actual published assets; a source checkout alone is not an official release. MIT; unofficial and not endorsed by Grok, xAI, Cursor or OpenAI. Requires Windows, Python 3.12 and Git. The distribution is `grokbot-mcp-bridge` for compatibility; import as `grokbot_mcp`. Not a hosted service, PyPI package or guaranteed quota workaround.

## Try it safely without any account

On Windows PowerShell, after obtaining this reviewed source, use a **new** virtual environment:

~~~powershell
git clone https://github.com/cherwinyu/grok-bot-mcp.git
Set-Location grok-bot-mcp
py -3.12 -m venv C:\ExampleBridgeBeta
$BridgePython = 'C:\ExampleBridgeBeta\Scripts\python.exe'
& $BridgePython -m pip install .
& $BridgePython -I -X utf8 -m grokbot_mcp.safety_cli demo
~~~

Expected result includes `"mode": "OFFLINE_SIMULATION"` and `"real_webhooks_sent": 0`. It creates an owned fictional Git workspace, uses memory-only Webhook and credential doubles, actually runs two small Python tests, checks duplicate claims and frozen results, and removes the temporary fixture. It neither logs in nor proves the cloud Routine performed the fictional work. [Walkthrough](docs/offline-demo.md).

## Why this bridge?

| Step | Capability | Boundary |
|---|---|---|
| Register | Administrator fixes Git root, allowed files and test command | MCP caller cannot choose arbitrary roots |
| Prepare/validate | Bind task identity, manifest hash and Git baseline | A valid manifest is not permission to execute |
| Execute once | Separate submission switch, durable ledger and exclusive claim | `unknown_submission` is never blindly resent |
| Reconcile | Check result IDs, Git changes and frozen evidence | Local proof cannot independently attest external model actions |
| Recover | Explicit migration, backup and rollback | Never silently overwrite a terminal result |

Seven stable MCP stdio tools are retained. There is no arbitrary shell/root tool exposed over MCP.

## Optional advisory Git gate

Workspace registrations may set `"git_gate": "advisory"`; omission remains byte-compatible with the existing strict mode. Advisory mode records the claim-time HEAD and pre-existing tracked/untracked changes, then reports newly observed or agent-reported paths outside `allowed_files` as `out_of_scope_changes` without failing solely for those paths. At admission and claim, registered protected/forbidden paths are checked against the current HEAD; after claim, their recorded hashes are compared again. `.git` is not path-compared and the bridge's own untracked `.bridge` runtime directory remains excluded. Git command failures, manifest scope, claim-once, identity, frozen evidence and no-resend checks remain fail-closed.

This trade-off supports multiple agents sharing one checkout, but the bridge cannot determine which concurrent writer owns a change. Operators must review Git history and diffs themselves. Reads outside the declared scope are not detectable, and advisory mode is not an OS sandbox.

[Whole-repository advisory guide](docs/advisory-workspaces.md): configuration and workflow, opt-in `allowed_globs` / `allow_globs`, local `routine-check`, and audited `ledger-archive`. [Test policy and evidence](docs/test-evidence-git-review.md) defines `applies_to`, `argv`, `test_selection`, `require_test_output`, `other_prefixes` and `git_review`; [hardening](docs/hardening.md) defines path normalization, bounded JSON and safe tool errors. All options preserve their existing defaults; the seven MCP tools and package version remain unchanged.

## Where execution actually happens

~~~text
Cursor / Codex (Windows) --stdio--> local bridge
                                       |
                                 reviewed Webhook
                                       |
                                cloud Grok Bot Routine
                                       |
                        separately approved desktop commands
                                       |
                              registered local Git repo
~~~

Cloud Routines do not automatically share the Windows filesystem. Live work requires a reachable host, user-approved desktop commands, provider access, appropriate Routine configuration and credentials. HTTP 200 only means accepted.

**Security: `advisory_precheck` is an application-level allow-list check, NOT an OS sandbox.** External tools may have broader Windows rights. Use disposable or separately restricted workspaces; never enable Always Allow to make a test succeed. [Threat model](docs/security/THREAT_MODEL.md) · [Data boundaries](docs/data-boundaries.md).

## Connect clients deliberately

Generate and review a Cursor JSON or Codex CLI TOML config snippet; put it into the supported client UI yourself. The generator does not edit config or access keys.

[Client setup](docs/client-compatibility.md) · [Wizard quickstart](docs/quickstart.md) · [FAQ](docs/faq.md).

Native Windows CI checks installed wheel, snippet formats and fictional stdio. It **does not establish actual user-client UI reconnect or live Grok Bot results**. [Support matrix](docs/beta-support.md).

## Host setup

Generate the snippet with `client_cli fragment` (see [Cursor / Codex CLI](docs/client-compatibility.md)), then check the two paths in the **same** PowerShell process. A later background shell does not inherit variables set by an earlier command.

~~~powershell
$BridgePython = 'C:\Example Bridge\Scripts\python.exe'
$Registry = 'C:\Example Config\registry.json'
if (-not (Test-Path -LiteralPath $BridgePython)) { throw 'python_path_missing' }
if (-not (Test-Path -LiteralPath $Registry)) { throw 'registry_path_missing' }
& $BridgePython -I -X utf8 -c "import grokbot_mcp; print(grokbot_mcp.__version__)"
~~~

Cursor `mcp.json`:

~~~json
{
  "mcpServers": {
    "grokbot-mcp": {
      "command": "C:\\Example Bridge\\Scripts\\python.exe",
      "args": ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"],
      "env": { "GROKBOT_MCP_REGISTRY": "C:\\Example Config\\registry.json" }
    }
  }
}
~~~

Codex CLI `config.toml`:

~~~toml
[mcp_servers."grokbot-mcp"]
command = "C:\\Example Bridge\\Scripts\\python.exe"
args = ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"]
env = { GROKBOT_MCP_REGISTRY = "C:\\Example Config\\registry.json" }
~~~

If the registry file is missing, `grok_list_workspaces` returns `ok: false`, `error: registry_missing`, and `workspaces: null`. That is not an empty registration.

Write a result to the fixed temp candidate, then call `result_cli`. Do not pass the JSON to `python -c`. The filename is `.<id>.result.tmp` for both phases. Validate uses `%TEMP%\grokbot-mcp-candidates\validate\`; execute uses `...\execute\`. The helper stages `.bridge/results_v2/.<task-id>.result.tmp` or `.bridge/results_execute/.<execution-id>.result.tmp`. `.result.candidate.json` is not a protocol name.

~~~powershell
$Phase = 'validate'   # or 'execute'
$Id = '<received task_id or execution_id>'
$candidateDir = Join-Path $env:TEMP ("grokbot-mcp-candidates\" + $Phase)
New-Item -ItemType Directory -Force -Path $candidateDir | Out-Null
$candidate = Join-Path $candidateDir ('.' + $Id + '.result.tmp')
$json = @'
{"protocol_version": 1}
'@
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($candidate, $json, $utf8)
& $BridgePython -I -X utf8 -m grokbot_mcp.result_cli --registry $Registry --workspace demo-workspace --phase $Phase --id $Id --source $candidate
~~~

Replace the here-string with the measured result. Continue with `--stage` and `--publish` as in [result writing](routines/result-writing.md). New manifests include `constraint_version`. Historical manifests that omit it stay verifiable. Do not require the manifest to equal the current constraint prose.

This repository does not host a host-side scope-correction framework. Failed receipts stay. Do not manually delete them or allocate a new id to resend the same submission. The only supported ledger-archiving recovery path is [ledger-archive](docs/advisory-workspaces.md#failure-and-audited-recovery).

| Failure | What this bridge does | Entry in this repository |
|---|---|---|
| `registry_missing` | `grok_list_workspaces` returns `ok: false` and `workspaces: null` | Point `GROKBOT_MCP_REGISTRY` at an existing registry file in this process |
| Result format or identity error before publish | Publication is refused. The final result and submission ledger stay unchanged | Edit the temp candidate and run `result_cli` again for the same id. Do not resend |
| Terminal or frozen result | Replacement is refused. The failed receipt stays | No correction tool here. Do not delete or rewrite the receipt |
| `unknown_submission` | The bridge does not resend | No correction entry. Observe the original task |
| Staged or temp files after a terminal publish | Removes only that id's `.<id>.result.tmp` staged file and the matching temp candidate when its bytes match, then writes `.bridge/protocol_cleanup/<phase>/<id>.json` | `result_cli --phase validate --id <task-id> --cleanup`, or `--phase execute` with the execution id. It does not delete the published JSON, the submission ledger, another id, or `.result.candidate.json` |

## Community and release

[Issues](https://github.com/cherwinyu/grok-bot-mcp/issues) · [Contribute](CONTRIBUTING.md) · [Release procedure](docs/release-process.md) · [MIT](LICENSE) · [NOTICE](NOTICE.md) · [Changelog](CHANGELOG.md).

GitHub Beta Release is the intended distribution channel; check Releases for availability. This repository does not include the private development Git history or its internal task logs. No PyPI distribution is provided as part of this Beta.
