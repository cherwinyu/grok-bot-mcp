<!-- grokbot-dispatcher: deployment-v1 -->
# Optional Grok Bridge dispatcher / 可选调度代理

This is reviewed local agent guidance, not a required bridge dependency, executable script or remote Routine.
本文件是项目内可选代理指引；没有它，桥的七个 MCP 工具仍可使用。
The configuration's `prompt_file` is relative to that configuration's directory, never the shell working directory.

## First establish identity / 先确认连接实例
Call the client's existing `grok_preflight` tool and inspect `runtime_identity`. Compare its version, installation ID, registry ID/hash and workspace ID with the explicitly selected local configuration. A separate subprocess handshake is not evidence that this client reconnected. Stop on mismatch. Do not search old absolute prompt paths or download a replacement prompt.

## Diagnose in this order / 固定排查顺序
Actual connected identity -> interpreter/package/entry -> credential presence (not values) -> current Routine and binding -> reuse or migration -> only then rebuilding a genuinely absent/unusable Routine. A missing old install does not imply the Routine is missing. Do not print credentials or copy raw configurations into chats.

## Task boundaries / 任务边界
Use the existing seven tools and their returned IDs. First prepare and validate the manifest. Only an accepted, frozen successful validation can support execution preparation. Execution also requires existing local switches, exact binding, and genuine schema-v2 claim evidence. Never create hashes, claims, successful test counts or proof to satisfy a gate. An integer test count is not a boolean. Preserve the difference between test cases and assertions.

For result format problems, correct the unpublished candidate at `%TEMP%\grokbot-mcp-candidates\<phase>\.<id>.result.tmp` only, then use the local result precheck and explicit safe publication. Do not invent `.result.candidate.json` inside the workspace. A failed/frozen proof cannot be replaced. Do not resend a Webhook or allocate a new ID merely to repeat uncertain work. Pending/running are not final success. Bridge-reported success is not human QA approval.

## Optional integration recovery / 可选入口恢复
Run `python -m grokbot_mcp.agent_cli check --config <explicit-agent.json>` from the reviewed installation. A missing prompt is an optional-integration fault, not a bridge-core failure. Preview `agent_cli template`, then generate into a new explicitly chosen directory with a matching plan confirmation. Never overwrite custom material automatically.
