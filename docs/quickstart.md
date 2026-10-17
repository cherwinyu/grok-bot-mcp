# Quickstart: one wizard, one manual Grok Bot visit

[中文](quickstart.zh-CN.md) · [Home](../README.md)

This is package version **0.1.4.dev9**. Use a new installation and a fictional workspace for the first run.

## 1. Obtain and install this candidate

Install Python 3.12 and Git for Windows first. Open a new PowerShell. Repository access is required:

```powershell
git clone https://github.com/cherwinyu/grok-bot-mcp.git
Set-Location grok-bot-mcp
.\scripts\setup.ps1 -InstallDir C:\ExampleBridgeCandidate
```

`C:\ExampleBridgeCandidate` must be a new directory, not your active installation. The bootstrap installs the checked-out source with `python -m pip install <source>`, not a supposed PyPI bridge release. It may download the pinned MCP dependency and its dependencies. Existing bridge installations are never silently replaced. A failed first dependency install can be resumed with the same command. If an existing installation is stale, choose a new directory rather than asking the wizard to upgrade it.

To select Python explicitly, add `-Python 'C:\ExamplePython\python.exe'` using a real installed interpreter. Missing Python/Git produces a repair message. No activation script or global MCP package is needed. If PowerShell policy prevents running scripts, use the explicit source-install commands in [advanced setup](advanced-setup.md); do not disable machine-wide policy.

## 2. Prepare a small committed workspace

In another PowerShell opened at this source checkout, copy the shipped counter example:

```powershell
Copy-Item -Recurse .\examples\minimal_workspace C:\ExampleWorkspace
git -C C:\ExampleWorkspace init
git -C C:\ExampleWorkspace add .
git -C C:\ExampleWorkspace commit -m 'Initial fictional workspace'
```

Stop if the target already exists. Git requires your own configured author identity; the bridge does not set it. Do not use your real project for the first run. Do not create this workspace inside the bridge source checkout.

## 3. Register through the wizard

The bootstrap opens the wizard. It is safe to exit and return with:

```powershell
$BridgePython = 'C:\ExampleBridgeCandidate\Scripts\python.exe'
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace
```

For this fictional example, enter root `C:\ExampleWorkspace`; allowed files `src/counter.py` and `tests/test_counter.py` (one per line, then an empty line); protected file `README.md`; command ID `python_unittest`; and the test instruction `Run the installed Python interpreter -m unittest discover -s tests -t . from the registered root`. Review the displayed registration before saving. Write permission is a separate explicit choice; leaving it off does not mean installation is broken.

A custom registry may be selected with `--registry <absolute-path>`. Use that same selection for resume, diagnostics, claim and cancellation. The generated client config already carries it. The default is `%LOCALAPPDATA%\grokbot-mcp\registry.json`.

## 4. Complete the generated Grok Bot packet

The wizard prints two local paths under `setup/demo-workspace` beside the registry. Open `routine-setup.md` and follow its single checklist in Grok Bot: create or reuse the validate/execute Routines, apply the actual workspace/policy/test mapping, check the claim entry, enable the Routines and locate their distinct Webhook credentials. The file contains private local paths; do not attach it to issues.

For an existing pair, regenerate the full reuse packet with:

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --routine-mode reuse --generate-only
```

Reuse retains the Routine/Webhook; it **requires** updating execution to `grokbot_mcp.claim_cli`, `claim-evidence-v2`, schema-v2 claim evidence and `claim_sha256`. Keeping `claim_dynamic.py` unchanged is unsupported. Never claim the upgrade is verified merely because a credential exists.

Return with the same wizard command. Enter each URL and key through hidden input; they stay in Windows Credential Manager. Explicitly confirm each checked, enabled binding. Existing credentials/bindings are skipped and preserved unless you deliberately request `--replace-credentials` or `--reconfirm-bindings`. These options still ask for confirmation. Older profiles without saved test instructions need the administrator's exact command mapping in Grok Bot; the wizard does not invent one.

## 5. Connect the MCP client and inspect readiness

Copy the generated `mcp-config.json` into your client's MCP settings and restart its MCP connection. Use the generated absolute executable and arguments; do not replace them with `python`. Paths containing spaces or Chinese characters are supported. Client-specific UI locations may differ; the required process configuration is the generated JSON.

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --check
& $BridgePython -I -X utf8 -m grokbot_mcp.setup_wizard --workspace demo-workspace --check --purpose submit
```

The first command can pass with write/submission switches off. The second reports what blocks a deliberate submission. Both are read-only. In the client, discover the seven tools and call `grok_preflight` for `demo-workspace`; it must report `webhook_sent=false`, `remote_binding_verified=false` and `qa_accepted=false`. Its existing `ready`/`submit_permitted` semantics remain strict, so `ready=false` while switches are off is expected.

Local configuration and administrator confirmation do not prove a real remote task worked. Continue with the [offline/static walkthrough and human checkpoints](offline-demo.md). No real Webhook is part of setup.

## Existing installations / 已有实例

Before creating a Routine, identify the actual client connection and inspect its selected configuration. Use [the deployment checklist](deployment.md) / [中文清单](deployment.zh-CN.md) for strict-JSON preview/apply/revert, bounded standalone probe, safe migration and optional agent recovery. A generated config or an independent handshake is not confirmation that the actual client reconnected.

已有 Routine 优先复用；凭据存在不等于绑定正确。真实重连、远端回读声明、真实执行和人工 QA 分开记录；不要为了修复结果格式重发 Webhook 或替换冻结证据。


See [the offline demo](offline-demo.md) and [the security model](security-model.md) for the execution profile, disabled canary preparation, credential maintenance, and the separate live-use gates.
