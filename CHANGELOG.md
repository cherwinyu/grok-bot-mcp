# Changelog

## Unreleased

- `grok_list_workspaces` returns `ok: false` and `workspaces: null` when the registry file is missing, instead of an empty workspace list.
- Result candidates must be `%TEMP%\grokbot-mcp-candidates\<phase>\.<id>.result.tmp` for both validate and execute. `result_cli --source` rejects every other location.
- A terminal publish removes that id's staged file and the matching temp candidate, and records the cleanup under `.bridge/protocol_cleanup/` without modifying the submission ledger or the published result. `result_cli --cleanup` repeats that limited deletion.
- New manifests record `constraint_version`. Historical manifests that omit it remain verifiable. Verification does not require constraint-prose equality.
- README and client docs add Cursor and Codex config shapes, path self-checks, and a PowerShell write-then-dispatch script that saves UTF-8 without a BOM.

## 0.1.4b1 (experimental Beta)

- First standalone Public Beta candidate in a clean repository without the private development history.
- Add read-only Cursor JSON and Codex CLI TOML configuration fragments and Unicode/TOML validation.
- Retain seven MCP tools, the original ledger, claim-once and frozen-evidence behavior.
- Preserve the Windows-local authorization topology and explicit advisory (non-OS-sandbox) security boundary.
- Rework first-run documentation and isolated fictional tests.
- Use one selected final release wheel for both Windows CI installation and future prerelease attachments, after a separate maintainer release approval.
- Independently verify that every shipped sdist source file matches the audited source ZIP; reject extra, missing, modified and special members.
- Update client setup documentation to the current direct Cursor MCP documentation page.

Earlier private development iterations are not shipped as history in this repository.
