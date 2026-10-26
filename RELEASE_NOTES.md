# 0.1.5 — Windows MCP bridge

**Experimental, not stable or hosted.** 0.1.5 is the first non-prerelease version of this experimental line; it is not the ROADMAP Stable milestone. A source commit or passing CI does not authorize publication; check the GitHub Releases page for actual distribution assets and verify their published hashes.

## Runtime
- Unofficial Windows-local Grok Bot Routine bridge, Python 3.12, Git and MCP stdio.
- Seven existing MCP tools, task-bound allow-lists, stable task identity, single claim, frozen result checks and no blind retry after an uncertain submission.
- Cursor JSON and Codex CLI TOML configuration snippets produced read-only; users review and install them using the clients' supported settings.
- Clean offline demonstration using a temporary fictional Git repository and in-memory transport; this never sends a real Webhook.
- Dependency declaration for mcp==2.3.0; the final distribution wheel is installed into a separate venv during CI.

## What changed since v0.1.4b1
See [CHANGELOG.md](CHANGELOG.md) entries 0.1.5 through 0.1.5b1 for details. Highlights:
- Opt-in directory authorization (`allowed_prefixes`, `create_files`) with an optional `prefix_ceiling`, previewed-by-default `registry-prefixes` management and governance paths excluded by default.
- Opt-in `git_gate: advisory` for shared dirty workspaces and advisory-only local validation; strict remains the default.
- Bounded `grok_get_status(wait_seconds=0..50)`, opt-in registered test reruns and supplemental bridge-rerun evidence for `test_evidence_missing`; original results stay frozen and `qa_accepted` stays false.
- Execution timeout ceiling raised to 3600 seconds; read-only `admin_cli registry-check` and `grok_preflight.test_command_check`; `claim_cli --expect-fingerprint`.
- Runtime package/registry drift reporting, including a process-restart recommendation on `internal_error` when package bytes changed.
- Clearer `protected_or_forbidden_changed` diagnostics, pinned result-candidate paths, `result_cli --cleanup` and `constraint_version` in new manifests.
- The example-only `scripts/grok_run_example.py`, which is not a supported interface.

Generated Routine material carries the new version banner, and existing Routines are never edited automatically. When upgrading from v0.1.4b1, regenerate and repaste the Routine text to pick up `claim_cli --expect-fingerprint` (see 0.1.5b4 in the CHANGELOG).

## What is checked
Before the release tag is created, the release candidate is subject to:
- Windows CI full test suite against the installed wheel.
- Fictional two-installation acceptance.
- The offline demo.
- The release scan.
- A reproducible-build comparison of the archive SHA-256 manifests.

The evidence for this release is recorded in its GitHub Release description, not in this file. These checks use fictional workspaces and in-memory transport; they do not prove real client UI reconnect or real Grok Bot results.

## Limits
- The bridge's advisory_precheck is NOT a Windows filesystem sandbox or a verification of an external process's OS permissions.
- A successful HTTP 200 indicates acceptance, not the correctness or completion of remote execution.
- Actual local desktop access requires user-granted permissions, a reachable supported client/host and a configured Routine. These are outside automated public CI.
- Cursor/Codex syntax and fictional stdio tests are not proof of the real app's UI reconnect.
- No PyPI, MCP Registry or other hosting service is implied.

## Installation and assets
Follow README.md and docs/quickstart.md, install from the reviewed Release wheel after one is separately published, then run:
~~~powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.safety_cli demo
~~~

The GitHub Release attaches only the reviewed wheel, sdist, source ZIP and `SHA256SUMS.txt`. The sdist's source members are byte-checked against the reviewed source ZIP; reviewed generated setuptools metadata is the only exception. A release must not be created from an unreviewed branch or a different wheel.
