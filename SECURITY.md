# Security

## Report a problem

After this repository is published, use the host's private security advisory. Do not send webhook URLs, keys, or credential blobs.

## What this bridge checks

- The workspace must be registered before any tool runs. Callers cannot pass a root.
- Manifest allow-lists are a subset of the registered allow-list.
- Paths are normalized. Absolute paths, `..`, and Windows reparse points are rejected.
- Git HEAD must match the registered commit before validate or execute.
- Untracked files outside `.bridge` block submission.
- Execution freezes a snapshot. Later changes outside the allow-list fail scope review.
- A claim file is created once, with an exclusive create, under a cross-process lock.
- The execution switch defaults to false. While it is false, execute returns before opening a socket.
- Missing credentials fail closed.
- HTTP 400 is not treated as quota exhaustion unless the body says so.
- Uncertain delivery is marked `unknown_submission` and is not retried.
- Returned text is redacted for URLs, bearer tokens, and common key shapes.

## What this bridge does not do

If the executing process can write every file the user can write, the allow-list is advisory. `enforcement: advisory_precheck` is not a sandbox.

The bridge cannot see remaining quota, cannot turn on on-demand billing, and does not promise that a model will follow the routine text.

## Operator rules

- Keep validate and execute credential targets different.
- Turn the execution switch on only for the one submission you intend, then turn it off.
- Do not commit `%LOCALAPPDATA%\grokbot-mcp` or a workspace `.bridge` directory.
- Review `git status` yourself after a reported success.

## P0 scope and disclosure

See docs/security/THREAT_MODEL.md and docs/security/CREDENTIAL_LIFECYCLE.md. Do not report a vulnerability in a public Issue or test a discovered key. Use GitHub private vulnerability reporting only when actually available; if it is not enabled, request a private channel without posting the sensitive details. No maintainer response SLA is claimed.

P0 hardening rejects ambiguous/nonfinite/deep JSON, hardlinked evidence/allowed files and unsafe literal Webhook destinations. It does not authenticate DNS ownership, prevent DNS rebinding, independently measure Bot-reported tests, or confine an external process. A read-only profile blocks execution but is not an OS read-only token; isolated-write profiles are unsupported. Historical results remain frozen.
