# Contributing

Issues and patches are welcome. This tree is a private source snapshot until the maintainer says otherwise.

- Run the Windows Python 3.12 workflow, including the installed-wheel unittest, before asking for review.
- Do not add a real webhook URL, key, machine name, or private workspace path.
- Do not send a live webhook from tests or from GitHub Actions.
- Do not weaken the allow-list, claim-once rule, or default-off execution switch to make a test pass.
- Keep new source ASCII or UTF-8 without a BOM.
- The pinned MCP SDK is `mcp==2.3.0`. Do not widen that pin without a passing stdio handshake on that version.

Pull requests must not print secrets in logs. `scripts/scan_release.py` should stay clean.

Negative tests must fail for the behavior under test, not because an import or fixture is missing. Do not delete an assertion to make the suite green. Historical development commits are not stored here, so do not add a gate that needs some other repository's Git objects.

Read AGENTS.md before changing authority or protocol. See CODE_OF_CONDUCT.md for community behavior and SECURITY.md for confidential reporting.
