## Change and scope

Describe the user problem, the compatibility impact, and whether any of the seven MCP tools, the ledger, claim-once, or frozen results change.

## Required evidence

- [ ] Existing seven tools and their arguments stay compatible, or the break is explicit.
- [ ] New regressions assert the current refusal behavior. They do not depend on another repository's Git history.
- [ ] Native Windows installed-wheel tests, stdio, and the fictional client check were run for behavior changes.
- [ ] First failures, fixes, and reruns are described. Package and wheel hashes are included when a package is built.
- [ ] No real Webhook, credential value, client, registry, ledger, or deployment was used.
- [ ] Logs were reviewed before sharing. Incomplete gates stay explicit.
- [ ] A preview or a CI result is not treated as permission to publish or to change visibility.

## Not verified

State actual client, remote Routine, OS isolation, and publication status separately. Opening this pull request does not publish a Release or change who can see the repository.
