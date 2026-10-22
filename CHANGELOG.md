# Changelog

## Unreleased

- Add `--from-env <URL_VAR> <KEY_VAR>` to `init_credentials` so webhook credentials can be stored from environment variables without printing them; missing or empty variables print `credential_env_missing` and exit 1.
- When `prepare_execution` returns `validation_missing`, include a `hint` telling the caller to run `grok_submit_validation` first.


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
