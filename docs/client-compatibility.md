# Windows MCP client setup: Cursor and Codex CLI

[中文说明](client-compatibility.zh-CN.md) · [Home](../README.md) · [Deployment and recovery](deployment.md)

**Experimental 0.1.4b1 Beta scope.** These instructions generate configuration *snippets*, not actual client connectivity or a Grok Bot account approval. Run the local bridge only on the selected Windows host using a reviewed Python 3.12 venv and an explicitly selected registered workspace. A cloud-hosted Codex session or Grok Bot cloud computer does not inherit local Windows files or Windows Credential Manager.

## Generate snippets, with no client-file writes

Replace these fictional placeholder paths with existing reviewed paths. Keep the execution switch off until an independently authorized real test.

~~~powershell
$BridgePython = 'C:\Example Bridge\Scripts\python.exe'
$Registry = 'C:\Example Config\registry.json'
$Workspace = 'demo-workspace'
# Existing Cursor-compatible stdio JSON, unchanged default behavior:
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli fragment --python $BridgePython --registry $Registry --workspace $Workspace
# Optional Codex CLI config.toml snippet, printed to stdout only:
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli fragment --format codex-toml --python $BridgePython --registry $Registry --workspace $Workspace
~~~

The generator first inspects the *exact installed Python interpreter* and selected registry/workspace using the existing bridge checks. It never scans for an alternative installation, copies credentials, edits a configuration file, launches Codex/Cursor, or sends a Webhook.

Example Codex output **shape** (illustrative, not validated as an actual installation):

~~~toml
[mcp_servers."grokbot-mcp"]
command = "C:\\Example Bridge\\Scripts\\python.exe"
args = ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"]
env = { GROKBOT_MCP_REGISTRY = "C:\\Example Config\\registry.json" }
~~~

The printed TOML is validated using Python 3.12 tomllib and contains only the reviewed command, ordered module arguments, and selected registry environment variable. Windows backslashes and Unicode paths are safely quoted.

Example Cursor `mcp.json` shape (replace both paths; do not keep a path from an older checkout):

~~~json
{
  "mcpServers": {
    "grokbot-mcp": {
      "command": "C:\\Example Bridge\\Scripts\\python.exe",
      "args": ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"],
      "env": {
        "GROKBOT_MCP_REGISTRY": "C:\\Example Config\\registry.json"
      }
    }
  }
}
~~~

Before reconnecting, check the paths in the same PowerShell process. A later background shell does not inherit variables set earlier.

~~~powershell
if (-not (Test-Path -LiteralPath $BridgePython)) { throw 'python_path_missing' }
if (-not (Test-Path -LiteralPath $Registry)) { throw 'registry_path_missing' }
& $BridgePython -I -X utf8 -c "import grokbot_mcp; print(grokbot_mcp.__version__)"
~~~

`grok_list_workspaces` returns `ok: false`, `error: registry_missing`, and `workspaces: null` when that registry file is missing. An empty `workspaces` array is not a successful registration check. The write-then-dispatch script and the failure table are in the [README](../README.md#host-setup).

## Configure each client manually

| Client | Config format | What to do | Evidence level |
|---|---|---|---|
| Cursor Windows | mcpServers JSON in supported mcp.json/settings | Review the JSON fragment, insert via the supported Cursor UI, then manually reconnect the MCP server | Existing native fictional MCP/stdio PASS; actual user Cursor UI NOT_VERIFIED |
| Codex CLI Windows | mcp_servers TOML in ~/.codex/config.toml (or a trusted project-specific config) | Back up/inspect the real configuration, paste only the reviewed snippet; use codex mcp list for registration, then call a read-only bridge tool to confirm runtime | New generated TOML syntax/shape tested; actual user Codex CLI process NOT_VERIFIED |

Do not overwrite unknown user configuration or paste credentials, URLs, task files, backups or diagnostic output into these snippets. The bridge intentionally **does not edit, merge, inspect, or roll back TOML**. Never pass a Codex config.toml to the existing JSON-only client_cli switch/recover/rollback tools.

## Verification hierarchy

1. A parsable TOML or JSON fragment is only local config preparation.
2. A standalone, bounded stdio probe proves imports, initialization and tool discovery, but not that the target client actually reconnected.
3. The real selected Cursor/Codex client must be reloaded and its live grok_preflight identity observed from within that client; codex mcp list is not remote task acceptance.
4. Grok Bot Routine status, separately approved Windows desktop commands, real claim/result evidence and independent QA are separate gates. HTTP 200 is only submission acceptance, not execution success.
5. The app-level advisory_precheck is not an OS sandbox. Unknown submission is never safe to blindly resend.

Sources reviewed on 2026-10-17: [Codex MCP docs](https://learn.chatgpt.com/docs/extend/mcp), [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference), [Cursor MCP documentation](https://cursor.com/docs/mcp). Client releases can change; versions and entitlements must be retested before a stable support claim.
