# Windows Cursor / Codex CLI MCP 配置指南

[English](client-compatibility.md) · [中文首页](../README.zh-CN.md) · [安全部署与恢复](deployment.zh-CN.md)

当前适用 **0.1.4b1 实验性 Beta**。这里生成的只是只读配置片段，不代表真实客户端重连成功，也不表示获得 Grok Bot Routine 或 Windows 命令批准。Codex 云端执行环境和 Grok Bot 云电脑**不会自动共享用户的 Windows 文件或 Credential Manager**。

## 一次生成两类配置

在 Windows 上使用经过审核的独立 Python 3.12 venv、明确选择的登记文件和虚构工作区。下列路径是示例，必须用自己的合法目标替换；提交开关保持关闭：

~~~powershell
$BridgePython = 'C:\Example Bridge\Scripts\python.exe'
$Registry = 'C:\Example Config\registry.json'
$Workspace = 'demo-workspace'
# Cursor mcpServers JSON，兼容原有默认接口
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli fragment --python $BridgePython --registry $Registry --workspace $Workspace
# Codex CLI config.toml：仅在终端打印，不写入实际文件
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli fragment --format codex-toml --python $BridgePython --registry $Registry --workspace $Workspace
~~~

生成前会复用原有的安装解释器身份和登记工作区检查，不搜索其他路径、不运行未知脚本、不解码凭据、不发送真实 Webhook。

Codex TOML 片段的示例形状：

~~~toml
[mcp_servers."grokbot-mcp"]
command = "C:\\Example Bridge\\Scripts\\python.exe"
args = ["-I", "-X", "utf8", "-m", "grokbot_mcp.server"]
env = { GROKBOT_MCP_REGISTRY = "C:\\Example Config\\registry.json" }
~~~

片段采用 Python 3.12 tomllib 解析复核，特殊字符、反斜杠和中文路径均按 TOML 基础字符串规则转义。不能在片段中增加任意凭据或执行器环境字段。

## 客户端各自操作

| 客户端 | 配置 | 操作 | 验收状态 |
|---|---|---|---|
| Cursor Windows | mcpServers JSON | 使用官方界面审查、手工配置、重连，然后在该客户端调用 grok_preflight 检查实际身份 | 虚构 stdio 通过；真实 UI 尚未验收 |
| Codex CLI Windows | ~/.codex/config.toml 中的 mcp_servers TOML | 备份并手动加入片段；codex mcp list 只确认登记，仍需真实工具调用核验 | TOML 语法／字段可测试；真实 Codex 进程尚未验收 |

不要使用现有 client_cli switch/inspect/recover/rollback 修改 TOML，这些命令只支持严格 JSON。工具不会触碰用户已有的 Codex 配置、密钥或其他 MCP 服务器。

**必须区分四层证据：** 生成正确配置、独立 stdio 连接、真实客户端重连、真实 Grok Bot Routine 和人工批准。HTTP 200 只表示接收；advisory_precheck 不是 OS 沙箱；未知提交不可重发。

参考：[Codex MCP 官方说明](https://learn.chatgpt.com/docs/extend/mcp)、[Codex 配置规范](https://learn.chatgpt.com/docs/config-file/config-reference)、[Cursor MCP 文档](https://cursor.com/docs/mcp)。核查日期：2026-10-17。
