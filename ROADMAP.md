# Public Beta roadmap

**Target: 0.1.4b1 experimental Beta; release availability depends on maintainer authorization.** This roadmap describes planned milestones, not a guarantee of compatibility with future Cursor, Codex or Grok Bot releases.

## Beta scope (Windows)
- Windows 11 / supported Windows runner, Python 3.12, Git, MCP stdio.
- Windows-local Grok Bot Routine Bearer webhook protocol with user-configured desktop permissions.
- Seven existing tools, original task IDs, single submission/claim, immutable terminal decisions.
- Cursor JSON / Codex CLI TOML *read-only snippet preparation*; users must connect clients manually.
- Explicit local `advisory_precheck` and read-only policy settings; no OS sandbox.
- Publicly repeatable memory-only offline demo and fictional acceptance tests.

## Beta readiness gates
- Review all public source, commit metadata, workflow logs and exact release archives.
- Complete standalone wheel / sdist / source ZIP version and SHA verification.
- Windows installed-wheel stdio + fictional two-installation tests pass on final commit.
- Privacy/rights review and human publication approval are distinct from a successful CI.
- Public visibility and GitHub prerelease require separate explicit maintainer authorization.

## Stable milestone (not Beta promise)
- Actual client/provider compatibility evidence, defined support matrix, a robust restricted executor for any OS-enforced-write claim, documented release and security response procedure.

## Deferred
PyPI, MCP Registry, additional hosts/platforms, broad GUI, public shell execution, automatic retry after unknown submission and guarantees on third-party subscription quota.
