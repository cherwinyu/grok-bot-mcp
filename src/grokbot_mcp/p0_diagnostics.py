"""Additive P0 codes in the original diagnostics catalog; no runtime I/O."""

def register():
    from grokbot_mcp.diagnostics import _add
    # P0 additions retain the same diagnostic contract; never echo arbitrary payloads.
    _add("execution_profile_invalid", "The execution profile has unknown or incorrectly typed fields. / 执行配置格式无效。",
         "Use grokbot-safety profile to inspect the supported schema; do not coerce values or rewrite an active registry.")
    _add("webhook_protocol_unsupported execution_topology_unsupported desktop_approval_unsupported execution_isolation_not_implemented",
         "This declared provider, topology or enforcement mode is unsupported. / 声明的协议、拓扑或隔离方式尚不支持。",
         "Use the documented Windows-local advisory profile and explicit desktop approval. Cloud filesystems and isolated-write enforcement are not implemented.")
    _add("execution_profile_read_only", "This workspace profile does not permit execution. / 当前配置禁止执行。",
         "Keep this read-only profile for validation; any change needs separate administrator review and new matching binding evidence.", blocks="prepare submit claim")
    _add("migration_execution_profile_change_forbidden", "Binding migration cannot change the execution authority profile. / 绑定迁移不得改变执行权限模式。",
         "Resolve original tasks first; review the new registry policy separately. This migration must not widen execution permissions.")
    _add("json_depth_exceeded", "JSON nesting exceeds the bounded evidence contract. / JSON 嵌套超过证据读取上限。",
         "Inspect the original producer and an unpublished candidate locally. Do not replace a frozen result, retry a Webhook or log the rejected value.")
    _add("target_hardlink_rejected", "An allowed file has another filesystem hard link. / 白名单文件存在硬链接。",
         "Use independent regular files in a disposable workspace. Do not remove protection or write through the alias; investigate the original links.")
    _add("original_task_required", "An execution preview requires its original task identity. / 执行预览需要原始任务身份。",
         "Supply the task and execution IDs from the same original ledger; never guess the latest task or allocate a replacement ID.")
    _add("credential_maintenance_switch_on", "Submissions must be disabled before planned credential maintenance. / 凭据维护前必须停止提交。",
         "Quiesce senders and explicitly turn off the local switch, then inspect original pending tasks. Do not rotate a key to force a retry.")
    _add("canary_new_directory_required canary_destination_protected", "A canary needs a new folder outside existing repositories. / 验收夹具需要仓库外全新目录。",
         "Choose a new isolated sibling directory; preview grokbot-safety canary first. No real project, ledger or installation may be reused.")
    _add("canary_git_failed", "The local fictional Git initialization failed. / 本地虚构 Git 初始化失败。",
         "Check Git and write access to the chosen disposable parent directory. No Webhook or credential operation was attempted.")
