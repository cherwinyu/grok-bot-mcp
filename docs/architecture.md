# Architecture

The bridge is a single stdio process. It loads a registry file, then serves seven tools. It does not choose a workspace root from tool arguments.

## Registry

`GROKBOT_MCP_REGISTRY` points at a JSON file. If unset, the default is `%LOCALAPPDATA%\grokbot-mcp\registry.json`. That path is computed at runtime. It is not stored in this repository.

Each workspace records:

- `workspace_id`
- absolute `root`
- `expected_head`
- `allowed_files` and `forbidden_files`
- `protected_file`, which must also be forbidden
- `test_commands`
- distinct `validate_credential_target` and `execute_credential_target`
- `writes_enabled`
- `expected_machine`

`writes_enabled` defaults to false in the example. Prepare and submit refuse the workspace until an administrator explicitly permits it through the wizard (initial review or `--allow-writes`). The registry remains the single source of policy.

## Switch

`execution_switch.json` sits next to the registry. Missing file or any value other than `{"enabled": true}` means off. No MCP tool can flip it.

The switch gates both validation and execution submissions. A blocked validation returns `validate_manifest_not_enabled` before credential access or ledger creation, so the same prepared task can be submitted after the switch is enabled. It does not cancel a request already sent.

## Ledgers

Inside the workspace, under `.bridge`:

- `task_specs/` manifests
- `tasks_v2/` validation submissions
- `results_v2/` validation proofs written by the validate routine
- `executions/` execution grants
- `execution_baselines/` snapshots taken before execute
- `execution_claims/` one exclusive claim per execution id
- `results_execute/` execution reports written by the execute routine

These directories are local state. Do not publish them.

## Claim

`python -m grokbot_mcp.claim_cli --workspace <id> --expect-fingerprint <policy_fingerprint from this paste packet> <execution_id>` locks the ledger, checks the grant, and creates the claim file with `O_EXCL`. The returned `allowed_files` are the exact paths from the manifest after an on-disk name check. A nearby filename is rejected.

`enforcement` is `advisory_precheck`. The claim does not confine the process with an OS sandbox.

## Network

Validate and execute each build a small JSON body: protocol version, action, and ids. The URL and key are read from Credential Manager for that call and cleared afterward. Both submissions check the switch before loading the URL and key for a POST.

## Explicit execution environment (P0)

New wizard entries include optional `execution_profile` with a fixed supported provider/topology, advisory/read-only mode and per-command approval declaration. Profiles enter the existing policy fingerprint; absence preserves legacy behavior and is reported as unconfigured. They never establish actual desktop permission or cloud/local filesystem sharing. See the architecture ADR and P0 guide.
