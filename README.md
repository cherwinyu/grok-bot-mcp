# grok-bot-mcp

**A Windows MCP bridge for narrowly scoped, verifiable Grok Bot tasks.** Prepare one task, validate its policy, claim an execution once, and inspect the resulting code and evidence without blind resubmission.

[简体中文](README.zh-CN.md) · [Start safely](docs/quickstart.md) · [Security](SECURITY.md)

**Package version `0.1.4.dev9`. Not a stable release or a hosted service.** MIT-licensed and unofficial. This is not endorsed by Grok, xAI, or Cursor. The Python distribution name remains `grokbot-mcp-bridge`; import the library as `grokbot_mcp`. This repository does not publish to PyPI.

## Try the complete offline workflow

Use Windows, Python 3.12 and Git. Install into a **new** virtual environment. Do not replace an active bridge.

```powershell
py -3.12 -m venv C:\ExampleBridgeCandidate
$BridgePython = 'C:\ExampleBridgeCandidate\Scripts\python.exe'
& $BridgePython -m pip install .
& $BridgePython -I -X utf8 -m grokbot_mcp.safety_cli demo
```

`OFFLINE_SIMULATION` creates a private temporary Git workspace, exercises the two-phase ledger, claim, and result pipeline, runs two small Python tests, demonstrates duplicate-claim and frozen-result refusal, and removes its fixture. It uses only in-memory transport and credentials. It does not log in, send a real Webhook, change a real client, or certify a remote Routine.

## Where execution really happens

```text
MCP client -> local Windows bridge -> one authorized Routine Webhook
                  |                            |
             registered files             cloud Routine
                  ^                            |
                  +--- separately approved desktop commands
```

The cloud computer and your Windows disk are separate. Live use requires the correct desktop to be reachable and its commands separately approved. If nobody can approve a requested command, stop. Never silently substitute a cloud path or enable Always Allow to make a test pass.

The supported protocol is the Grok Bot Routine Bearer-authenticated POST whose accepted response is HTTP 200. It is not the separate Grok Automation signing/202 protocol. A 200 is acceptance, not completion. See [the protocol sources and architecture decision](docs/adr/0001-execution-topology.md).

## What you get

| Capability | Boundary |
|---|---|
| Seven stdio MCP tools | Existing arguments and ledger; no arbitrary shell or root tool |
| Strict result and claim evidence | JSON types, identities, frozen proof, and baseline checks; not independent human QA |
| Runtime installation identity | Diagnose a connected server; a subprocess probe is not a real client reload |
| Configuration switching and migration | Preview, explicit apply, backup, recovery, and conflict-aware rollback |
| Safety tools | Permission preview, credential-maintenance plan, disabled canary creation, and read-only observation |
| Publication tooling | Bounded Git and ZIP inspection. A clean scan is not permission to publish |

New disposable Canary registrations bind a nonempty, locally declared expected-machine label into the review hash and stored registry. That label is not remote-host authentication. New wizard registrations include an explicit Windows-local advisory execution profile. Older registries stay compatible and report that their profile is absent; they are not silently migrated. A declared read-only profile blocks execution. Cloud-shared-filesystem, unsupported provider, and claimed isolated-write profiles are rejected. Profile changes invalidate existing policy bindings.

## Connect a workspace deliberately

Use the [wizard-first quickstart](docs/quickstart.md), the generated two-Routine packet, and the [deployment and rollback guide](docs/deployment.md). Reuse an existing Routine and Webhook when its binding can be updated. Credential presence is not binding correctness. An old `claim_dynamic.py` entry is not made compatible by inventing a claim hash.

Before a real task, inspect the exact configuration and permission plan. Local observations, actual client reconnection, actual Routine execution, and human approval are four separate gates. Engineering tests use fictional clients and workspaces.

## Safety first

The allow-list is **application-level `advisory_precheck`, not an OS sandbox**. The Bot's actual tools may have wider permissions. Use a disposable or independently restricted environment, keep credentials and unrelated projects outside it, and review the diff yourself. The bridge cannot attest that an external agent stayed within OS permissions or that self-reported tests really ran.

`unknown_submission` never permits resending. No maintenance tool turns on a real switch, manufactures claims, overwrites frozen terminal evidence, or grants human QA. Default outputs are not universally anonymous; share only explicit reconstructed exports.

[Result contract](docs/result-contract.md) · [Troubleshooting](docs/troubleshooting.md) · [Development](docs/development.md) · [Contributing](CONTRIBUTING.md) · [Roadmap](ROADMAP.md)

Pinned SDK: `mcp==2.3.0`. Read [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md). A source commit is not installation, publication, or remote acceptance.

This repository's Git history starts at its initial commit. It does not carry earlier private development history, and its tests do not replay those older trees.
