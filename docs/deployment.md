# Deployment, identity and recovery — 0.1.4.dev9

[中文](deployment.zh-CN.md) · [Results contract](result-contract.md) · [Quickstart](quickstart.md)

This is package version **0.1.4.dev9**, not an installer for an existing live environment. Identity and migration features are retained. Keep the installed wheel and its external SHA-256 together. Installation, application, Windows tests, client reconnection, and remote acceptance are separate claims.

## Identity before repair

Ask the **actual client's existing connection** to call `grok_preflight` (or `grok_list_workspaces`). The new additive `runtime_identity` is available even when submission is disabled. The actual initialize handshake also announces the installed package version. A missing identity means an older or unsupported instance, not a reason to create another Routine. Keep all seven tools and their existing arguments; local maintenance is not a new MCP tool.

| Field | Meaning and lifetime |
|---|---|
| `bridge_version` | Package version loaded by this process; install another version and restart to change it. |
| `installation_id` | Correlation ID from absolute interpreter **and** package location. Same version in two venvs differs. Moving an installation changes it. Reinstalling at the same location does not, by itself, change it. |
| `package_sha256_at_start` | Digest of Python/template bytes when the identity module initializes. Same-location changed content is distinguishable. Not a signature or a complete OS/process-image measurement. |
| `process_instance_id` / `process_id` | Random per-process UUID / OS PID. Restart changes the UUID; PIDs can be reused. Neither proves process ownership. |
| `identity_initialized_at` | Identity initialization timestamp, **not** a claim to know the kernel process start time. |
| `registry_id_at_start`, `registry_sha256_at_start` | Selected registry location ID and byte digest captured at initialization. |
| `registry_id`, `registry_sha256`, `workspace_id` | Current selected registry location, current bytes and requested workspace. Re-read on calls; file edits change its digest, formatting included. Same bytes at another path have a different ID. |

Path-derived hashes are **not anonymization**. Default MCP output omits private absolute installation/registry/root paths; workspace IDs and correlation identifiers can still be identifying. Do not share a whole preflight. Use separately reconstructed `client_cli --export` diagnostics, which omit all correlation IDs, paths and task history. `--local-details` is explicitly local and is not a shareable report. Private backups can contain other clients' unknown configuration fields, including credentials already present there: **never publish them**.

## Supported selected-client configuration

Only a user-selected UTF-8, no-BOM, strict JSON `.json` file containing a top-level `mcpServers` object and an existing stdio service entry is rewritten. Comments/JSONC, TOML, duplicate keys, remote URL transports, ambiguous launchers, arbitrary scripts and unknown argument conventions are refused. No default client file is discovered or edited. Generate a fragment for another format and use that client's documented editor yourself; the bridge does not guess its grammar.

Canonical launch uses an absolute **dedicated Windows venv** `Scripts/python.exe`, arguments `-I -X utf8 -m grokbot_mcp.server`, and explicit `env.GROKBOT_MCP_REGISTRY`. Static checks inspect venv/package/dependency metadata, selected registry, Git baseline/exact paths and Routine bindings without executing that configured command. They do not prove native imports. Scripts at vanished historical locations are diagnosed as such, never executed or silently recreated. Optional agent failure is not a core dependency failure.

## One administrator checklist

Use explicit private paths. The following variables are fictional placeholders, not automatic discovery or instructions to overwrite an installation:

```powershell
$BridgePython = 'C:\Example Candidate\Scripts\python.exe'
$TargetPython = 'C:\Example New Candidate\Scripts\python.exe'
$Registry = 'C:\Example Config\registry.json'
$Client = 'C:\Example Config\client.json'
$Workspace = 'example-workspace'
$Common = @('--registry', $Registry, '--workspace', $Workspace)
```

**1. Record the identity returned inside the actual client.** Check local launch/registry before credentials. Install the reviewed wheel into a *new* venv if the old path disappeared:

```powershell
py -3.12 -m venv 'C:\Example New Candidate'
& $TargetPython -m pip install 'C:\Example Downloads\grokbot_mcp_bridge-0.1.4.dev9-py3-none-any.whl'
& $TargetPython -I -X utf8 -m grokbot_mcp.client_cli inspect --config $Client --workspace $Workspace --local-details
```

A missing old path is recoverable via a new installation plus a reviewed fragment/switch; it does not mean the existing Webhook/Routine is unusable. Never change PATH, copy over an active venv or overwrite user source to make a check pass.

**2. Check presence, then existing binding.** Normal `setup_wizard --check` uses the existing non-decoding credential presence API. `client_cli inspect` and share export do not access Credential Manager. Presence does not validate scope, entry, protocol capability, or actual remote enablement. The remote Routine must be reviewed separately.

**3. Plan migration when necessary; do not recreate by default.**

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.migration_cli plan @Common --python $TargetPython --local-details
```

Compare current/target root, allow-list, protection, baseline, registry selection, claim interpreter, revision/capabilities and blockers. `--target-registry` selects another **already registered**, reviewed profile; no registry is rewritten. This tool refuses moving a workspace, advancing HEAD, expanding scope, relaxing protection or changing write permission. It reuses the original unresolved-task predicates; unknown submissions, pending work and unreviewable ledgers block migration. No task IDs, proof or claim hashes are replaced.

After reviewing the plan, explicitly repeat `apply --apply --confirm <plan_sha256>` with the same inputs. Before any binding changes, it saves a complete local transaction under `registry-parent/migrations/<workspace>/<transaction>/`: exact old/new binding bytes, sanitized-known-field profile snapshots, packet and checksums. No Credential Manager values or arbitrary registry fields are copied. A pending marker is set before changing phase files; submission remains blocked even after a partial write.

Open its **single `routine-update.md`**. Keep the existing pair of Routine/Webhooks and update their workspace, scope, claim interpreter/entry and result-writing instructions. Execution requires `claim-evidence-v2`. An unchanged `claim_dynamic.py` is still unsupported. For new explicit installation bindings, the running claim interpreter must match the attested installation. Then manually read back both remote Routines against the packet; there is no remote read API here.

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.migration_cli finalize @Common --transaction $Transaction
# Only after actual administrator readback, review the new plan hash:
& $BridgePython -I -X utf8 -m grokbot_mcp.migration_cli finalize @Common --transaction $Transaction --apply --confirm $FinalizeHash --remote-reviewed
```

`administrator_remote_review_confirmed=true` is only an attestation. `remote_binding_verified=false`, `live_execution_verified=false`, `qa_accepted=false` remain. `--routine-status paused` records reviewed-but-paused; it does not permit submission. None of this enables the global switch or workspace writes. A same-input no-change migration creates no new transaction.

**4. Preview, apply, independently probe, then reload the client.**

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli switch --config $Client --python $TargetPython @Common --local-details
# Review plan and path diff; approval must match that exact input snapshot.
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli switch --config $Client --python $TargetPython @Common --apply --confirm $SwitchHash
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli inspect --config $Client --workspace $Workspace --probe
```

Other services, unknown fields and non-registry environment values are retained. Only command/args/selected registry are changed. Atomic same-directory replacement avoids half-written JSON; receipts and exact backups support rollback. Config writes are separate from the installed package and business authorization.

`--probe` is explicit: it verifies the **recognized** candidate in an independent, bounded process using an argument array and the selected configuration environment. It never executes an unknown proxy script. It can prove imports, initialize version, seven-tool discovery and returned identity; it checks credential **presence**, not values, and sends no Webhook. It cannot reconnect the client UI. The static inspect/export route does not spawn the bridge.

Use the client's MCP stop/restart or application reload control. This project has no generic UI reload API. Query `grok_preflight` **there**, save only its identity to a private JSON file, then compare:

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli compare --config $Client --workspace $Workspace --observed 'C:\Example Config\observed-identity.json'
```

A match proves equality of supplied identities, not the provenance of that file. Comparison always keeps `client_reconnection_confirmed=false`; the administrator separately records that the observation really came from the client. The Windows fictional client harness specifically holds the old connection alive across config replacement and proves its mismatch before reconnecting. It is **not** real-user UI acceptance.

**5. Record live acceptance separately.** Only separately authorized real validation/execution can establish that the account, Routine and workspace actually interoperate. A format-valid result, CI success, HTTP 200, local binding attestation and human QA are not interchangeable.

## Recovery and rollback

`client_cli transactions --config <file>` and `migration_cli transactions @Common` list local transaction IDs, not task history. `recover`/`rollback` preview by default; apply requires `--apply --confirm <fresh-plan-hash>`. Keep the same selected files, workspace and registry. Client receipts live beside the selected config in `.<filename>.grokbot-history/<transaction>`.

Recovery checks receipt/snapshot hashes and that each target is still an original or transaction-produced state. New client edits cause a conflict instead of being overwritten. Migration also verifies selected registry/install bytes, unresolved tasks and a business-state snapshot. A new task or evidence file after migration blocks rollback even if it is not currently pending. Rollback never rewrites the registry, business ledger, claim, proof, HEAD or permissions. It cannot undo remote Routine edits; revert those manually and review them separately.

A crash during preparation, before a complete transaction directory is atomically published, changes no original target. Private `.preparing-*` scratch is not a committed transaction; retry the original plan. A crash after preparation can be resumed from the receipt. A pending migration blocks submission until recover/finalize/rollback. A prepared finalization can resume only an already recorded explicit attestation; otherwise recover stays pending. Tampered/missing backups fail closed and require investigation, not blind replacement.

## Optional dispatcher

The bridge does not require a game-specific agent or external prompt. `agent_cli template` previews the packaged version; `agent_cli generate --directory <explicit-private-folder>` previews two files, and explicit `--apply --confirm <hash>` creates them. `agent.json` identifies `optional-dispatcher`, generator version and template `deployment-v1`; relative `prompt_file` resolves from **that config's parent**, never the shell working directory. `agent_cli check --config ...` never executes a prompt. It refuses traversal/reparse points and missing/unsupported files; it never searches the disk or downloads replacements. Generating the same exact template can restore a missing file but will not overwrite a customized prompt. Private project-specific agents were not inspected or modified by this implementation.

## Limits

These are application-level snapshot/lock/CAS checks, not a hostile-user filesystem sandbox. Local administrator edits can still tamper with files outside these commands; hashes are not authentication signatures. Keep configs/receipts private and restrict their OS ACLs. Export allow-lists and generic/private-marker scans have explicitly different scopes; neither audits the whole machine or Git history.


See [the offline demo](offline-demo.md) and [the security model](security-model.md) for execution profiles, disabled canary preparation, credential maintenance, and separate live-use gates.
