# 0.1.4b1 — Windows MCP bridge Public Beta

**Experimental Beta, not stable or hosted.** A source commit or passing CI does not authorize publication; check the GitHub Releases page for actual distribution assets and verify their published hashes.

## What this Beta includes
- Unofficial Windows-local Grok Bot Routine bridge, Python 3.12, Git and MCP stdio.
- Seven existing MCP tools, task-bound allow-lists, stable task identity, single claim, frozen result checks and no blind retry after an uncertain submission.
- Cursor JSON and Codex CLI TOML configuration snippets produced read-only; users review and install them using the clients' supported settings.
- Clean offline demonstration using a temporary fictional Git repository and in-memory transport; this never sends a real Webhook.
- Dependency declaration for mcp==2.3.0; the final distribution wheel is installed into a separate venv during CI.

## Experimental limits
- The bridge's advisory_precheck is NOT a Windows filesystem sandbox or a verification of an external process's OS permissions.
- A successful HTTP 200 indicates acceptance, not the correctness or completion of remote execution.
- Actual local desktop access requires user-granted permissions, a reachable supported client/host and a configured Routine. These are outside automated public CI.
- Cursor/Codex syntax and fictional stdio tests are not proof of the real app's UI reconnect.
- No PyPI, MCP Registry or other hosting service is implied.

## Installation and verification
Follow README.md and docs/quickstart.md, install from the reviewed Release wheel after one is separately published, then run:
~~~powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.safety_cli demo
~~~

Source archive and package SHA-256 will accompany an authorized GitHub Beta prerelease. A release must not be created from an unreviewed branch or a different wheel.
