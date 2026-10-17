# grok-bot-mcp

**Auditable AI coding task handoffs for Windows.** Prepare one narrowly scoped task for a user-configured Grok Bot Routine, claim execution once, and review Git changes and structured evidence rather than trusting a "done" message.

[简体中文](README.zh-CN.md) · [Quickstart](docs/quickstart.md) · [Cursor / Codex CLI](docs/client-compatibility.md) · [Beta support](docs/beta-support.md) · [Security](SECURITY.md)

**0.1.4b1 experimental Beta line.** Check GitHub Releases for actual published assets; a source checkout alone is not an official release. MIT; unofficial and not endorsed by Grok, xAI, Cursor or OpenAI. Requires Windows, Python 3.12 and Git. The distribution is `grokbot-mcp-bridge` for compatibility; import as `grokbot_mcp`. Not a hosted service, PyPI package or guaranteed quota workaround.

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

## Community and release

[Issues](https://github.com/cherwinyu/grok-bot-mcp/issues) · [Contribute](CONTRIBUTING.md) · [Release procedure](docs/release-process.md) · [MIT](LICENSE) · [NOTICE](NOTICE.md) · [Changelog](CHANGELOG.md).

GitHub Beta Release is the intended distribution channel; check Releases for availability. This repository does not include the private development Git history or its internal task logs. No PyPI distribution is provided as part of this Beta.
