# Frequently asked questions

[中文](faq.zh-CN.md) · [Quickstart](quickstart.md) · [Support matrix](beta-support.md)

**Can I try it without a Grok Bot account?** Yes. The `grokbot_mcp.safety_cli demo` runs with fictional local Git files and memory-only transport.

**Does this bypass API costs or quotas?** No. This is an unofficial bridge to a user-configured Grok Bot Routine. Fees, entitlements, rate limits and provider availability depend on the relevant external products.

**Is CI success real Grok Bot execution evidence?** No. CI proves installed-wheel behavior, MCP stdio discovery and simulated task/result workflows, not actual third-party execution.

**What about Cursor / Codex CLI?** This Beta can generate reviewed Cursor JSON and Codex TOML snippets on Windows. Actual configuration and reconnection are the user's responsibility; never paste a private key into them.

**Is the allow-list an OS sandbox?** No. `advisory_precheck` validates registered scope and evidence; it cannot restrict an external executor's system permissions.

**The Webhook timed out. Should I retry?** No. Preserve the original identity and inspect `unknown_submission`; a new POST might duplicate work.

**Where should bugs go?** File a small fictional reproduction via Issues. Use private vulnerability reporting for security defects. Never attach keys, real task histories, private paths, credential screenshots or raw local exports.

**Why is there no PyPI release?** The first intended release is a separately authorized GitHub Beta prerelease. A checked-in candidate version is not an official distributed release.
