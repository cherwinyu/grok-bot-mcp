# Data flow, credentials and privacy

[中文](data-boundaries.zh-CN.md) · [Support matrix](beta-support.md) · [Security](../SECURITY.md)

1. **Windows local bridge:** Administrator-selected registry and allowed Git files. Local tasks, claims and result ledgers are stored under the registered workspace's `.bridge` directory.
2. **Credentials:** The two Routine phases have separate targets in Windows Credential Manager. Presence-only checks are not remote validation; never share URLs, bearer keys, hidden-input values or credential blobs in logs, PRs or public Issues.
3. **Network:** Only separately authorized submissions send a bounded bearer-authenticated Webhook request with protocol/action/task identity fields. Other remote tools may access selected source content when separately permitted; their storage/retention is not controlled by this bridge.
4. **Desktop:** Cloud Routine and Windows disk do not automatically share files. The actual executor and every desktop command have separate permissions. An application allow-list cannot enforce operating-system confinement.
5. **Logs/exports:** Runtime identity, registry hashes, task IDs and optional `--local-details` paths may identify a machine or user. Use specifically reconstructed shareable reports; hashing is not anonymity.
6. **CI/demo:** Fictional workspaces and memory-only credentials/transport; there are no real Webhook sends. A green CI is not a claim about a real cloud account.
7. **Retention:** Task, frozen result and migration records remain unless deliberately managed by an administrator. Never erase ledger evidence as a workaround for an uncertain submission. External service retention is a provider issue.

For untrusted prompts use a disposable or independently restricted Windows environment. This Beta makes no claim to full host auditing, real service uptime or subscription quota guarantees.
