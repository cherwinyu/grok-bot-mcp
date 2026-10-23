# Recommended setup for advisory workspaces

[简体中文](advisory-recommended-setup.zh-CN.md) · [Advisory workspaces](advisory-workspaces.md) · [Security model](security-model.md)

This guide combines the existing advisory options into one reviewed starting point. It changes no installation, registry, Routine, client, credential or switch. Sections marked *ships with* describe changes released together with this guide; see [Dependencies](#dependencies-and-release-order).

## When it fits, and when it does not

Fits: several operators share one whole-repository checkout, tasks are small, and a person reviews every diff and runs independent QA.

Does not fit: anything that needs strong isolation. The bridge is not an OS sandbox. It cannot verify the text of the cloud Routine (`remote_binding_verified` is always `false`), it cannot see out-of-scope reads or ordinary gitignored writes, and `qa_accepted` is always `false`. Use a disposable or separately restricted workspace for unfamiliar work.

## Recommended combination

| Option | Why |
|---|---|
| `git_gate: "advisory"` | Required for `local_validation`; Git review findings are reported as advisory rather than rejected as in strict mode. |
| `prefix_ceiling` | Fixes the widest directory grant in the policy fingerprint. |
| `allowed_prefixes` | Day-to-day grant inside the ceiling; adjust with `admin_cli registry-prefixes`, no repaste needed. |
| `allowed_files: []` | Valid when prefixes or a ceiling are present; new files inside a prefix need no re-registration. |
| `forbidden_files` / `protected_file` | Keep agent rules, private configuration, `.git` and `.bridge` out of reach; forbidden and protected paths always win. |
| `local_validation: true` | The bridge performs Validate locally (advisory only); it is part of the fingerprint. |
| `rerun` on the test command | The bridge reruns the registered test with `shell=False` and writes receipts; not part of the fingerprint. |
| `require_test_output: true` | A result without measured test output becomes `test_evidence_missing` instead of success. |

A fictional registry fragment. Every value is a placeholder; review each one against the real project before use.

**Depends on the timeout-ceiling change.** The `timeout_cap` and `rerun.timeout_seconds` values of 3600 below rely on the companion timeout-ceiling change (ceiling 3600), released together with this guide. Without it, set `timeout_cap` and `default_timeout` to 600 or less and `rerun.timeout_seconds` to 1800 or less, otherwise registration is rejected.

```json
{
  "workspace_id": "example-workspace",
  "root": "<repo-root>",
  "expected_head": "<40-hex-commit>",
  "git_gate": "advisory",
  "local_validation": true,
  "require_test_output": true,
  "allowed_files": [],
  "prefix_ceiling": [
    {"prefix": "src/", "exts": [".py"]},
    {"prefix": "tests/", "exts": [".py"]},
    {"prefix": "docs/", "exts": [".md"]}
  ],
  "allowed_prefixes": [
    {"prefix": "src/example/", "exts": [".py"]},
    {"prefix": "tests/", "exts": [".py"]}
  ],
  "forbidden_files": [".git", ".bridge", "AGENTS.md", "private"],
  "protected_file": "private",
  "test_commands": [
    {"id": "none", "task_types": ["read_only"], "timeout_cap": 180, "default_timeout": 180},
    {"id": "unit", "task_types": ["code_change"], "timeout_cap": 3600, "default_timeout": 1800,
     "argv": ["<bridge-python>", "-B", "-m", "unittest", "tests.test_example"],
     "rerun": {"argv": ["<bridge-python>", "-B", "-m", "unittest", "discover", "-s", "tests"],
               "timeout_seconds": 3600}}
  ],
  "validate_credential_target": "<validate-target>",
  "execute_credential_target": "<execute-target>",
  "writes_enabled": false,
  "expected_machine": "<machine-name>"
}
```

- `prefix_ceiling` is deliberately wider than `allowed_prefixes`, so the grant can shrink or grow later without changing the fingerprint. Every active prefix must sit inside one ceiling entry with a subset of its extensions, otherwise `prefix_outside_ceiling`.
- `forbidden_files` lists `.git` and `.bridge` explicitly; `protected_file` must be one of the forbidden entries.
- `argv` runs the targeted test in Grok's environment. `rerun` runs the full suite on the bridge machine, so Grok does not need to fit it into its own deadline.
- `rerun` is attempted only through `grok_get_status(rerun_tests=True)` for a settled execution; ordinary queries never launch test commands. If `cwd` is given it must be an existing relative directory inside the root; omitted means the root.
- The 3600 values in the fragment are valid only with the timeout-ceiling change; on a baseline without it use `timeout_cap`/`default_timeout` <= 600 and `rerun.timeout_seconds` <= 1800.
- `writes_enabled` stays `false` here. Enabling it and the execution switch are separate administrator decisions.

### Single-stage flow

With advisory plus `local_validation`, no Validate submission to Grok is made, so only the Execute Routine is exercised. Whether `grok_preflight` aggregate `ready` can be true without a validate-phase credential and binding is not verified; read `phases.execute` for the phase that matters.

1. `grok_prepare_task` writes the manifest (optionally with `execution_timeout`, see below).
2. `grok_prepare_execution` creates the execution authorization. Precondition: `writes_enabled` must be `true`; while it is `false` (as in the fragment above) the call returns `workspace_writes_disabled` before anything else, and the local validation is never reached. Turning it on is an administrator action taken deliberately for the submission window, outside this guide, which only describes the precondition and the symptom. With `writes_enabled` on and no earlier validation ledger or result, it runs the local validation itself.
3. Submit with `grok_submit_execution` only under the separate, explicit authorization and switches.
4. Poll `grok_get_status(execution_id=..., wait_seconds=N)`. `N` is an integer 1..50; the bridge polls read-only at 1, 2, 4, 5, 5… seconds and returns early on a terminal state or a complete validated result. Call it repeatedly with backoff rather than in a tight loop.
5. If the result is `test_evidence_missing`, call `grok_get_status(execution_id=..., rerun_tests=True)` (not with `observe_only`). Human review and independent QA still follow.

## Three timeout levels (ships with the timeout-ceiling change)

| Level | Where | Limit |
|---|---|---|
| Global hard ceiling | bridge code | 3600 seconds (previously 600) |
| Per command | `timeout_cap`, `default_timeout` | `1 <= default_timeout <= timeout_cap <= 3600`; the setup wizard registers new code-test commands at 3600/3600, `none` at 180; existing registrations are not upgraded |
| Per task | `grok_prepare_task(execution_timeout=N)` | positive integer up to the command's `timeout_cap`; omitted means `default_timeout` |

`rerun.timeout_seconds` is separate: 1..3600, default 600.

What it is: the deadline for the **whole remote task**, counted from the recorded request attempt (the webhook send). After it passes the status becomes `timeout`, and a result arriving afterwards is marked `late_result`. It is not an MCP call timeout: submission waits only for the HTTP acceptance (60 seconds) and returns at once.

Consequences:

- `timeout` is an unresolved state. It blocks conflicting new tasks and never authorizes an automatic retry or replacement identity.
- A higher limit means a silently failed Grok run occupies the workspace for longer. For small tasks, pass a short `execution_timeout` explicitly.
- Grok's own maximum run time per execution has not been verified. A 3600-second bridge deadline does not show the platform can run that long.
- An old ledger record without the field falls back to 600 seconds.

## Re-register and repaste, or not

| Change | Fingerprint changes? | Re-register and repaste Routine? |
|---|---|---|
| `timeout_cap` / `default_timeout` (either direction) | yes | yes |
| `allowed_files` | yes | yes |
| `forbidden_files` / `protected_file` | yes | yes |
| `prefix_ceiling` | yes | yes |
| `local_validation` turned on or off | yes (the key is present only when true, so turning it off removes it) | yes |
| `require_test_output` turned on or off | yes | yes |
| `allowed_prefixes` without a ceiling | yes | yes |
| `allowed_prefixes` within `prefix_ceiling`, via `registry-prefixes` | no | no |
| New files under an active prefix | no | no |
| `rerun` (argv, cwd, env, `timeout_seconds`) | no | no |
| Task-level `execution_timeout` | no (stored in the manifest) | no |

"Repaste" means regenerating the Routine material, pasting it into the actual Routine, and confirming the binding locally. Other fields that appear in the policy also feed the fingerprint; treat any change to them like the first group: `git_gate` (when not `strict`, so switching to or from `advisory` changes it), `expected_machine`, `execution_profile` (when present; the wizard writes it), `expected_head`, `policy_revision`, credential targets, `test_selection`, `allow_globs`, `glob_max_files`, `other_prefixes`, and for each test command its `id`, `task_types`, `argv` and `applies_to`.

## Checks after registering

- `python -B -m grokbot_mcp.admin_cli registry-check --registry <registry.json> [--workspace <id>]` (ships with the registry-check change). Read-only: reports `ok`, `missing` or `unchecked` for each `argv` and `rerun.argv` cwd, explicit executable path, unittest module and test file inside the root. Bare executable names are `unchecked`. It executes nothing and searches no PATH; it is a filesystem hint, not proof the tests run.
- `grokbot-mcp routine-check --registry <registry.json> --workspace <id>`. Local packets only; it cannot verify the remote Routine. With the fingerprint change it also warns when the pasted claim command lacks `--expect-fingerprint`.
- `grok_preflight`: read `runtime_drift` (restart the MCP process if `process_restart_recommended` appears) and, with the registry-check change, `test_command_check`, which does not change `local_ready`.

## Fingerprint at claim (ships with the claim-fingerprint change)

New packets render the Execute claim command with `--expect-fingerprint <policy_fingerprint from this paste packet>`. If the current policy differs, claim returns `routine_binding_mismatch` before writing anything; the Routine must stop without editing files and the administrator pastes an updated Routine. This is a local comparison, not verification of the remote Routine text.

## Common problems

- **`installation_mismatch`**: the selected interpreter cannot load the installed bridge. The remedy text differs by release. The current baseline diagnostic says to create a separate venv and install the checked-out source with that venv's `python -m pip install .`, without replacing an active installation. The `python -m pip install -e .` refresh for stale editable metadata (and reinstalling the matching wheel for a wheel installation) is the wording added by the registry-check change (*ships with*); apply it only if that change is included.
- **Full test suite exceeds the deadline**: have Grok run only targeted tests (`argv`) and let the bridge `rerun` the full suite.
- **Commit identity or GH007** (push rejected because of private email settings): use the hosting service's noreply address for the commit author. This is Git hosting behavior, not something the bridge checks; no repository doc covers it.

## Dependencies and release order

Sections marked *ships with* describe three companion changes: the 3600-second timeout ceiling, `admin_cli registry-check` (which also brings the `pip install -e .` hint under `installation_mismatch`), and `claim_cli --expect-fingerprint`. If this guide is merged without them, the matching parts do not apply: the ceiling stays 600 (and `rerun.timeout_seconds` 1800), so the example registry fragment, which uses 3600, is rejected until `timeout_cap` and `default_timeout` (the example's `default_timeout` is 1800, so it must be lowered too) are 600 or less and `rerun.timeout_seconds` is 1800 or less; `registry-check` and `test_command_check` are absent; the `installation_mismatch` hint is the baseline `python -m pip install .` one; and claim does not accept `--expect-fingerprint`. Decide the merge order together.
