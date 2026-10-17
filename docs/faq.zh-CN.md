# 常见问题

[English](faq.md) · [快速开始](quickstart.zh-CN.md) · [Beta 支持范围](beta-support.zh-CN.md)

**没账号也能试用吗？** 可以。`grokbot_mcp.safety_cli demo` 使用虚构 Git 文件和内存中的模拟传输，不需要 Grok Bot 账号。

**可以绕过 API 或订阅额度吗？** 不可以。实际收费、权限、限流与服务稳定性由第三方提供商决定，本项目不做费用或无限使用承诺。

**CI 通过就是 Grok Bot 实际执行成功吗？** 不是。CI 检查 wheel 安装、MCP stdio 和模拟任务链路，不是第三方真实现场验收。

**支持 Cursor/Codex CLI 吗？** 本 Beta 可以在 Windows 上生成经校验的 JSON/TOML 片段；真实客户端的配置、重连和远端执行需要用户自行确认。

**白名单是 OS 沙箱吗？** 不是。`advisory_precheck` 是应用层范围检查，不能约束外部执行器的系统权限。

**Webhook 超时要再提交吗？** 不能盲目重发。保留 `unknown_submission` 和原任务身份，先观察原始状态。

**如何反馈？** Issue 只能放虚构最小复现；密钥、私人路径、真实任务和安全漏洞细节不要公开。安全问题使用私密渠道。

**为何没有 PyPI？** 首次计划发布渠道是另行授权的 GitHub Beta Release；私有候选不等于正式发行。
