# Release notes

## 0.1.4.dev9

This tree is an auditable source snapshot for Windows, Cursor or Codex CLI, and Python 3.12. It is not a stable product release.

Install from a new virtual environment and run `python -I -X utf8 -m grokbot_mcp.safety_cli demo` before connecting any real workspace. The demo uses memory-only transport. It does not call a Webhook.

`advisory_precheck` checks the registered file policy inside the bridge. It is not an operating-system sandbox. Desktop command approval stays outside this package.

No GitHub Release, PyPI upload, or MCP Registry submission is part of this version.
