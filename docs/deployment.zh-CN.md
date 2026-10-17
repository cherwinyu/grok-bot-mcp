# 部署、实例识别与恢复 — 0.1.4.dev9

[English](deployment.md) · [结果契约](result-contract.md) · [快速开始](quickstart.zh-CN.md)

这是包版本 **0.1.4.dev9**，不是对现用环境的自动升级。实例识别与迁移能力继续保留。安装、离线测试、原生 Windows、真实客户端重连、远端 Routine 验收必须分别记录。

## 先识别实际连接，再修复环境

在**用户实际客户端的已有连接中**调用 `grok_preflight` 或 `grok_list_workspaces`，查看新增的 `runtime_identity`。提交开关关闭也可查看。MCP initialize 返回实际包版本；没有身份字段通常说明仍连接旧版或不支持的实例，不能据此要求新建 Routine。七个 MCP 工具和原参数保留；维护功能放在本地 CLI。

| 标识 | 语义与生命周期 |
|---|---|
| `bridge_version` | 当前进程加载的版本；换包并重启后改变。 |
| `installation_id` | 解释器绝对位置与包目录位置的关联标识；同版本不同 venv 可区分。搬移路径改变标识，同位置重装本身不改变标识。 |
| `package_sha256_at_start` | 初始化时 Python 文件和模板字节的摘要；可识别同路径同版本但内容不同。不是签名，也不是操作系统完整进程镜像测量。 |
| `process_instance_id` / `process_id` | 每进程随机 UUID / 系统 PID。重启 UUID 改变；PID 会复用，不证明进程所有者。 |
| `identity_initialized_at` | 身份模块初始化时间，不冒充内核进程启动时间。 |
| `registry_id_at_start`、`registry_sha256_at_start` | 初始化时选中登记的位置标识与字节摘要。 |
| `registry_id`、`registry_sha256`、`workspace_id` | 本次调用使用的登记位置、最新字节和选定工作区。登记编辑（包括格式化）改变摘要；相同字节放在不同位置仍有不同位置标识。 |

普通路径哈希**不是匿名化**。默认 MCP 不输出私人绝对安装、登记、工作区路径，但工作区 ID 和关联标识仍可能识别人。分享时不要直接贴整份 preflight；使用另行重建的 `client_cli --export`，它移除路径、关联标识和任务历史。`--local-details` 明确用于本机。切换备份保留其他服务和未知配置字段，可能含这些字段原本已有的敏感信息；**备份和迁移材料不得分享**。

## 支持哪些客户端配置

只重写用户明确选择的 `.json` 文件：UTF-8 无 BOM、严格 JSON、顶层 `mcpServers`、已存在的 stdio 服务项。JSONC 注释、TOML、重复键、远端 URL、未知代理脚本和参数语法一律拒绝。不会猜测或寻找默认配置；其他格式只提供片段，由管理员使用该客户端的受支持编辑方式处理。

规范入口是专用 Windows venv 的绝对 `Scripts/python.exe`，参数数组 `-I -X utf8 -m grokbot_mcp.server`，以及明确的 `env.GROKBOT_MCP_REGISTRY`。静态检查读取 venv/包/依赖元数据、登记、Git 基线、准确文件范围和绑定，不执行配置中的未知脚本；它不能证明原生依赖可导入。可选代理失败也不等于桥核心依赖失败。

## 管理员一次完成的操作清单

以下为虚构变量示例，必须替换为自己明确选择的私人路径，不是覆盖现用安装的授权：

```powershell
$BridgePython = 'C:\Example Candidate\Scripts\python.exe'
$TargetPython = 'C:\Example New Candidate\Scripts\python.exe'
$Registry = 'C:\Example Config\registry.json'
$Client = 'C:\Example Config\client.json'
$Workspace = 'example-workspace'
$Common = @('--registry', $Registry, '--workspace', $Workspace)
```

**第一步：记录实际连接身份，检查本地入口。** 旧路径消失时，在新目录安装经过哈希核对的候选 wheel；不覆盖旧源码、现用 venv，不修改 PATH，也不据此重建 Routine。

```powershell
py -3.12 -m venv 'C:\Example New Candidate'
& $TargetPython -m pip install 'C:\Example Downloads\grokbot_mcp_bridge-0.1.4.dev9-py3-none-any.whl'
& $TargetPython -I -X utf8 -m grokbot_mcp.client_cli inspect --config $Client --workspace $Workspace --local-details
```

缺失路径返回稳定错误码和恢复入口。可用 `client_cli fragment --python $TargetPython @Common` 生成新入口片段；未知格式不会被冒险重写。检查报旧版本，不会静默升级它。

**第二步：检查凭据存在性，再核对绑定。** 普通 `setup_wizard --check` 使用原有不解码的凭据存在接口；`client_cli inspect` 和分享导出不读取凭据管理器。凭据存在不等于工作区、范围、入口或协议正确，更不等于远端启用。不要把密钥放进 JSON、日志、聊天或报告。

**第三步：优先复用；需要更新时先做迁移计划。**

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.migration_cli plan @Common --python $TargetPython --local-details
```

计划比较当前/目标根目录、白名单、保护、HEAD、登记选择、领取解释器、绑定版本/能力和阻塞项。`--target-registry` 只能选择另一个已登记、已审查的目标配置，不自动改登记。禁止搬移工作区、推进 HEAD、扩大范围、放松保护、改变写许可；未决/不确定任务和无法判断的账本阻止迁移。

审查后才执行同参数 `apply --apply --confirm <plan_sha256>`。绑定改动前，在登记旁 `migrations/<workspace>/<transaction>/` 完整保存旧/新绑定、已知字段的配置快照、集中更新材料及校验值，不复制凭据管理器明文或未知登记字段。先建立待确认标记，再更新两阶段绑定；中断时仍阻止提交。

打开唯一的 **`routine-update.md`**：保留已有 Routine/Webhook，更新工作区、范围、领取解释器/模块入口和结果写回步骤。执行必须具备 `claim-evidence-v2`；`claim_dynamic.py` 原样不变仍不兼容，不能补造 claim 哈希。新显式安装绑定还检查实际运行领取命令的解释器身份。更新后人工逐项回读两条真实 Routine；本工具没有远端读取 API，不会伪造回读成功。

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.migration_cli finalize @Common --transaction $Transaction
# 只有人工完成远端回读后，才审查并使用本次计划哈希：
& $BridgePython -I -X utf8 -m grokbot_mcp.migration_cli finalize @Common --transaction $Transaction --apply --confirm $FinalizeHash --remote-reviewed
```

`administrator_remote_review_confirmed=true` 仅是人工声明。`remote_binding_verified`、`live_execution_verified`、`qa_accepted` 仍为 false。`--routine-status paused` 允许记录“已审查但暂停”，不会允许提交。所有操作均不启用总开关或工作区写许可；无变化迁移不创建新事务。

**第四步：配置预览 → 明确应用 → 独立启动探测 → 实际客户端重载。**

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli switch --config $Client --python $TargetPython @Common --local-details
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli switch --config $Client --python $TargetPython @Common --apply --confirm $SwitchHash
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli inspect --config $Client --workspace $Workspace --probe
```

计划展示字段差异，本机详细模式展示路径前后值；其他 MCP、未知字段和非登记环境项保持。匹配计划哈希才创建备份并原子替换；目标或输入变化则拒绝。配置已写入不代表目标包可启动，更不代表客户端已经切换。

`--probe` 用参数数组，在有界独立进程中启动已识别的目标候选，核对导入、initialize 版本、七工具和身份；它使用选中配置环境，只检查凭据存在性、不读取值、不发送 Webhook。未知代理脚本不会执行。静态检查和导出不启动桥。

随后使用客户端自己的 MCP 停止/启动或应用重载功能。本项目没有通用 UI 重载 API，不宣称自动完成。在**该客户端中**重新调用 preflight，将仅身份部分保存到私人 JSON，再比较：

```powershell
& $BridgePython -I -X utf8 -m grokbot_mcp.client_cli compare --config $Client --workspace $Workspace --observed 'C:\Example Config\observed-identity.json'
```

匹配只证明提供的数据相同，不证明观察文件来源。因此比较结果仍保持 `client_reconnection_confirmed=false`，由管理员另行记录确实来自实际客户端。Windows 虚构客户端验收会保持旧连接跨越配置替换，先证实不匹配，再重连；它不冒充真实 UI 验收。

**第五步：单独记录真实执行和人工 QA。** 真实任务必须另行明确授权。格式有效、CI 通过、HTTP 200、本地绑定声明、真实 Routine 执行及人工 QA 不是同一件事。

## 中断恢复与回退

`client_cli transactions --config ...` 或 `migration_cli transactions @Common` 可列本地事务 ID，不枚举任务内容。`recover`、`rollback` 默认仅预览，实际应用仍需 `--apply --confirm <新计划哈希>`。客户端备份位于配置旁 `.<文件名>.grokbot-history/<事务>`，保持原选择路径。

恢复检查备份/材料哈希、目标是否仍处于旧状态或该事务产生的状态；后来改过的客户端配置不会被旧备份覆盖。迁移额外检查登记/安装字节、未决任务和业务证据快照；迁移后即使只产生尚未提交的新 manifest，也阻止回退。不会改写登记、账本、claim、proof、HEAD 或权限。回退仅限本地，不能替代对远端 Routine 的人工恢复与回读。

完整事务目录原子发布前发生中断，原目标未改；`.preparing-*` 是私人准备残留，不是已提交事务，可重新运行原计划。完整记录已生成后，可按原事务恢复。迁移待确认标记在恢复/确认/回退前一直阻止提交。只有完整记录了明确人工声明的 finalize 才可恢复为已确认；否则 recover 仍是待确认。备份损坏或丢失应停止调查，不能盲目覆盖。

## 可选代理入口

通用桥不要求任何游戏专用 Agent 或外部提示词。`agent_cli template` 预览包内模板；`generate --directory <明确私人目录>` 只预览两个文件，`--apply --confirm <哈希>` 才创建。`agent.json` 记录 `optional-dispatcher`、生成器版本与模板 `deployment-v1`。相对 `prompt_file` 基准是**配置所在目录**，不是当前终端目录。

`check --config ...` 只检查，不执行提示词；拒绝路径穿越、链接、缺失和不支持的文件，不搜索磁盘，不下载未知替代。再次生成相同模板可恢复缺失文件，但拒绝覆盖已定制提示词。本轮通用实现未读取或修改私人项目专用 Agent。

## 安全范围

这是应用层快照、锁和 CAS 检查，不是对恶意并发管理员的文件系统沙箱。哈希不是身份签名；本机管理员仍可绕过这些命令直接编辑文件。配置、备份和材料应保持私人并使用适当系统 ACL。分享导出、通用扫描和外部私人标记扫描范围不同，都不等于整机或 Git 历史审计。


参见[离线演示](offline-demo.zh-CN.md)和[安全模型](security-model.md)。执行配置、默认禁用的 Canary、凭据维护和真实使用检查彼此分开。
