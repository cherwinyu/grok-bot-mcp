# Roadmap and release gates

Current milestone: private source snapshot `0.1.4.dev9`, not a public stable release.

The current work is local execution safety, disabled canary preparation, operator diagnostics, Windows package verification, and release-surface review. The seven tools and the claim, proof, and terminal protocol stay in place.

Open gates that need a separate environment or an explicit human decision:

- an actual supported client reconnect
- an actual Grok Bot Routine on a selected desktop
- operating-system enforced isolation
- a fresh review of whatever surface would become public
- independent security approval
- explicit permission to change visibility, publish a Release, or upload to PyPI or an MCP registry

Later, only after a separate decision: additional client config formats, optional cross-platform backends, and registry distribution. This milestone does not add a broad GUI, a public remote shell, automatic retry, or a model-cost promise.
