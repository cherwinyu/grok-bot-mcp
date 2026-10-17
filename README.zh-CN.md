# grok-bot-mcp

**把范围明确的任务交给 Grok Bot，并在 Windows MCP 客户端核验结果。** 核心流程是准备任务、校验策略、单次领取、检查代码与证据；不是任意命令执行器，也不靠盲目重发恢复任务。

[English](README.md) · [快速开始](docs/quickstart.zh-CN.md) · [安全边界](SECURITY.md)

当前包版本为 **`0.1.4.dev9`**，不是稳定版或在线服务。许可证为 MIT。本项目不是 Grok、xAI 或 Cursor 的官方产品。Python 发行名仍是 `grokbot-mcp-bridge`，导入名是 `grokbot_mcp`。本仓库不发布到 PyPI。

## 先体验一遍完整离线流程

需要 Windows、Python 3.12 和 Git。在新的虚拟环境中安装，不能覆盖现用安装：

```powershell
py -3.12 -m venv C:\ExampleBridgeCandidate
$BridgePython = 'C:\ExampleBridgeCandidate\Scripts\python.exe'
& $BridgePython -m pip install .
& $BridgePython -I -X utf8 -m grokbot_mcp.safety_cli demo
```

`OFFLINE_SIMULATION` 在自己创建的临时 Git 工作区运行双阶段账本、claim、结果暂存与发布；实际运行两个小型 Python 测试，验证重复领取和冻结终态被拒绝，然后删除夹具。凭据和提交仅在内存里模拟，不登录、不发真实 Webhook、不切换真实客户端，也不证明真实 Routine 已验收。

## 先分清云电脑和本机

MCP 客户端调用本地桥；桥发送经授权的 Routine Webhook；Routine 在云端接收，再通过**另外批准的本机命令**访问登记的 Windows 工作区。两台机器不天然共享磁盘。本机不可达、审批被拒绝或无人批准时必须停止，不能换成云目录，也不能默认开启 Always Allow。

支持的是 Grok Bot Routine 的 Bearer/HTTP 200 协议，不是另一种 Grok Automation 签名/202 协议。200 只表示接受，不表示任务正确完成。来源和决策见[架构 ADR](docs/adr/0001-execution-topology.md)。

## 已有能力

保留七个 stdio MCP 工具、精确文件策略、冻结 proof、原始 claim 哈希和不可改写终态。安装身份、配置预览、备份、切换、回退、Routine 迁移和错误诊断继续可用。

新建独立 Canary 会将本机声明的机器名称纳入预览哈希与登记，但它不证明云端 Bot 真的连到了该机器。权限预览、凭据维护计划、默认禁用的独立验收夹具和只读验收观察仍然可用。新登记写入明确执行配置；未配置的旧登记保持兼容，但不会伪装成已验证。只读配置禁止执行。未实现的云共享磁盘、其他协议和 OS 隔离执行配置会被明确拒绝。修改执行配置会改变策略指纹，旧绑定不会自动继续有效。

## 再接入真实环境

按照[向导](docs/quickstart.zh-CN.md)、生成的双 Routine 材料和[部署恢复指南](docs/deployment.zh-CN.md)操作。优先确认实际连接实例，再检查本地入口、凭据是否存在、现有绑定和迁移需要。凭据存在不等于绑定正确。`claim_dynamic.py` 原样兼容没有实现，不得补造哈希。

本地检查、实际客户端重连、真实 Routine 执行和人工 QA 必须分开记录。工程测试使用虚构客户端和工作区，不宣称真实账号链路已经通过。

## 必须理解的限制

`advisory_precheck` 是应用层检查，**不是操作系统沙箱**。Bot 的其他工具可能拥有更大权限；请使用可丢弃或另行受限的环境，不把私人项目和密钥放进去。桥不能认证外部进程的全部行为，也不能将模型自报计数变成独立实测。

未知提交不能重发；维护工具不启用真实开关，不授予 QA，不覆盖冻结失败证据。普通本机输出并非匿名数据，分享请使用明确的脱敏导出。

[结果契约](docs/result-contract.md) · [排错](docs/troubleshooting.md) · [开发测试](docs/development.md) · [路线图](ROADMAP.md) · [贡献](CONTRIBUTING.md)

固定 SDK `mcp==2.3.0`。见 [LICENSE](LICENSE)、[NOTICE](NOTICE.md)。源码提交不等于现用部署、公开发布或真实验收。

本仓库的 Git 历史从初始提交开始，不包含更早的私人开发历史，测试也不会回放那些旧源码树。
