# Maintainer Public Beta release checklist

[Home](../README.md) · [Support scope](beta-support.md)

This document is **not** authority to change visibility, create a Git tag, publish a Release or upload to PyPI.

## Source and privacy freeze
- Record an exact candidate commit, Git tree, source file inventory, version, Python/Windows CI run ID and baseline.
- Keep the original private development repo and its Git objects, CI evidence, PRs and task ledgers OUT of this clean-public-history repository.
- Review the public candidate's complete Git refs, commit author metadata, Issues, PRs, workflow logs, attachments and every prepared distribution archive.
- Classify any scanner findings and explicitly review third-party licenses or media. A clean static regex scan is not human privacy approval.
- Freeze accurate Beta claims: no verified OS sandbox, no automatic unknown retry and no claims of remote acceptance from fictional CI.

## One final wheel, not two candidates
- Ensure the same version appears in pyproject.toml and the Python package.
- Build the public source ZIP, sdist, wheel and SHA256SUMS via the reviewed source-scanning builder.
- Stage the **same byte-identical release wheel** in the isolated wheelhouse used for native Windows installed-package, stdio and fictional two-installation tests. A second pip-built wheel is not a substitute.
- Verify METADATA, RECORD, source-tree package correspondence, the sdist's full source-file identity (allowing only reviewed generated metadata), and exact SHA256SUMS entries; preserve clean release artifacts separately from dependency wheels and private logs.
- Archive member bytes and the exact source tree must remain unchanged after timestamp canonicalization. The canonical metadata epoch is 1980-01-01 UTC for ZIP/TAR members and zero gzip-header mtime; **these dates are not commit/authorship evidence**.
- The isolated Python build backend is explicitly fixed at `setuptools==84.0.0` (CI frontend uses `build==1.2.2`) so routine upstream backend upgrades cannot silently change archival contents.
- The native Windows gate rebuilds the exact release after a wall-clock delay and compares full `SHA256SUMS.txt` manifests, refusing any nondeterminism before installed-wheel tests.
- PR and squash-merged `main` builds with the same source tree must produce the same three archive hashes; differing hashes block release.
- Any source or release-note change after QA creates a NEW candidate and must rerun the complete gate.

## Platform, governance and explicit approval
- Verify real GitHub branch/ruleset enforcement; CODEOWNERS alone is not protection. Account-plan limitations must be recorded.
- Enable private vulnerability reporting and supported dependency/secret-security features through the repository Settings as permitted.
- Record a private, independently reviewed GO/NO-GO statement with any gaps.
- **STOP** before converting to Public, creating `v0.1.4b1` and publishing GitHub prerelease; all require the owner's separate affirmative release authorization.
- After authorization, attach only the reviewed wheel/sdist/source ZIP/SHA256SUMS and check public downloads, hashes, refs, logs and actual security settings.

Do not include real Grok Bot account keys, internal acceptance reports, screenshots of credential-bearing screens, dependency wheelhouse, personal audit markers or original development history in a public release.
