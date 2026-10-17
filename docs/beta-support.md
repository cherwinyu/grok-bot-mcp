# Public Beta support and evidence

[中文](beta-support.zh-CN.md) · [Home](../README.md) · [Data boundaries](data-boundaries.md)

## Version and environment
- **0.1.4b1 is the experimental Beta line; verify current availability through GitHub Releases.**
- Windows 11 / native Windows CI, Python 3.12, Git and MCP stdio.
- Dependency baseline: `mcp==2.3.0`. Other SDK versions or platforms are not guaranteed by this Beta.
- External use requires a user-configured bearer-protocol Grok Bot Routine and separately permitted Windows desktop commands.

| Capability | Publicly testable evidence | Important limitation |
|---|---|---|
| Seven MCP stdio tools | Native Windows installed-wheel and fictional stdio | Not independent real provider acceptance |
| Task manifest, claim, result | Negative unit tests; frozen evidence checks | An external model can still misreport actions |
| Offline demo | Two measured fictional Python tests and memory-only transport | No real Grok Bot account or Webhook |
| Cursor JSON | Generated JSON/stdio protocol shape | Actual client UI reconnect needs user verification |
| Codex CLI TOML | TOML roundtrip, Unicode, exact env and argument order | Actual Codex application reconnect needs user verification |
| Backup, migration, recovery | Native fictional two-installation test | Does not modify user config without explicit approval |
| Desktop execution | Declared Windows local topology | Bridge cannot authenticate remote permission |
| `advisory_precheck` | Application-level scope checks | **NOT an OS sandbox** |
| `isolated_write` | Explicitly refused | Unsupported |
| Unknown submission | No automatic resend | No provider-level exactly-once guarantee |
| Other operating systems / cloud-only | Not in Beta scope | Cloud disk does not automatically share local files |

A local CI badge, an administrator-written JSON attestation or HTTP 200 from a Webhook is not independent proof of remote task completion. Public Issues must not contain real task IDs, developer-machine paths or provider credentials.
