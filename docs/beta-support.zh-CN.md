# 公开 Beta：功能与证据边界

[English](beta-support.md) · [首页](../README.zh-CN.md) · [数据边界](data-boundaries.zh-CN.md)

- **0.1.5 为实验性版本，实际发行状态以 GitHub Releases 为准。**
- 当前目标：Windows 11、Python 3.12、Git、MCP stdio；依赖固定 `mcp==2.3.0`。
- 真实运行还需要用户配置的 Grok Bot Routine Bearer Webhook 与另外批准的 Windows 桌面命令。

| 功能 | 公开可重复验证的证据 | 重要限制 |
|---|---|---|
| 七个 MCP 工具 | Windows 已安装 wheel + 虚构 stdio | 不是独立真实远端验收 |
| Task/claim/result | 安全负向测试、冻结证据检查 | 模型仍可能错误自报 |
| 离线演示 | 实测 2 项虚构 Python 测试、内存模拟传输 | 无真实账号与 Webhook |
| Cursor JSON | 配置结构和模拟 stdio | 真实用户 UI 重连需单独确认 |
| Codex CLI TOML | TOML 解析、Unicode、参数与环境校验 | 真实 Codex 应用重连需用户确认 |
| 配置备份、迁移、恢复 | 双安装虚构验收 | 未授权不修改现用客户端配置 |
| 桌面命令 | Windows 本机授权拓扑 | Bridge 不能自动证明授权状态 |
| `advisory_precheck` | 应用层范围校验 | **不是操作系统沙箱** |
| `isolated_write` | 明确拒绝 | 尚未支持 |
| 未知提交 | 禁止盲目重发 | 不保证云端恰好执行一次 |
| 其他 OS / 纯云端 | 不属于本 Beta | 云盘不是本机 Git 目录 |

公开 CI、管理员确认文件、HTTP 200 都不等于真实远端完成任务。禁止向公开 Issue 提交个人路径、真实任务 ID、私密代码与密钥。
