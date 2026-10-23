# 路径与鲁棒性加固

[English](hardening.md) · [安全模型](security-model.md) · [开发说明](development.md)

可选目录选择与有审计的恢复见[整仓 advisory 指南](advisory-workspaces.zh-CN.md)。受限 glob 语法比一般策略路径规范化规则更严格。

Advisory 登记的 allowed、forbidden、protected 路径使用同一共享函数：反斜杠转换为 `/`，折叠重复分隔符、前导 `./` 和内部 `..`，拒绝绝对路径及逃出根目录的路径。Advisory 比较一律 casefold，覆盖 Windows 和 `core.ignorecase=true` 的仓库；`private2` 不属于 `private`。准确文件检查仍保留实际拼写。Strict 路径处理及快照 JSON 序列化保留原行为。

Advisory 快照解码 Git C 风格引号路径，将八进制转义先累积为字节，再按 UTF-8 解码。非法序列在解码器中保留原样，参与策略比较时拒绝。Ignored 条目仅保留 watched 路径范围内的内容。执行禁区判定使用同一规范化：登记禁区为 `private` 时，结果报告 `Private/secret.tmp` 仍为 scope_violation。结果 `changed_files` 比较先做 Unicode NFC，不猜测修复乱码字符。这仍是应用层检查，不是 OS 沙箱，也不能归属并发写入者。

落盘 JSON 的读写均检查原有 4 MiB 字节上限。账本、manifest、结果、registry 或快照超限时返回 `ledger_too_large`，写入在替换目标前拒绝。底层 `strict_json.decode` API 保留原 `json_too_large` 错误码。不要截断账本、改写冻结证据、创建替代身份或重发来绕过限制。

`grok_prepare_execution` 继续接受 `workspace_id`、`manifest_task_id`。`grok_get_status` 接受 `workspace_id`、`task_id`、`execution_id`、`observe_only`、`verify_current`；未知参数名返回带 `accepted_arguments` 的 `invalid_argument`。工具或 facade 的意外异常返回 `internal_error`、异常类型及固定安全原因，现有本地日志流仅记录安全错误码和类型，不复制堆栈、被拒值或原始异常消息。内部错误不能证明尚未发送，不得据此重发。

文本文件及子进程文本输出显式使用 UTF-8；子进程解码对非法输出字节采用替换策略。JSON 落盘保持 UTF-8；二进制 I/O 保留二进制模式。测试无需设置 `PYTHONUTF8`：直接运行 `python -m pytest`，由 `testpaths = ["tests"]` 限定顶层测试，独立最小示例的测试保持可用。Installed-wheel 验证也不再强制 Python UTF-8 模式。既有测试仅补编码参数，断言与预期结果不变。

验收仍要求原生 Windows 测试、源码树外 installed-wheel 导入、虚构双安装验收、离线演示及发布扫描。环境或权限失败不能算作通过；验收通过也不授权发布或部署。
