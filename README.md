<!--
GENERATED-BY: repository creation, 2026-11-09 +08:00
LAST-EDIT: portable framework synchronization, 2026-11-09
PURPOSE: 给首次查看项目的人说明用途、边界和可执行检查。
ORIGIN: reusable research mechanisms and target-specific evaluation adapters
CUMULATIVE-DELTA: 2026-11-09 新增完整行为预期与109项验收入口，明确功能迁移未完成；保留既有研究规则同步。
-->
# AutoResearch：可复用研究内核与 agent 比赛配置

这套项目的目标是让 AI 围绕一个研究目标持续工作：先写假设和判断标准，再安排实验，保存原始证据，核查结果，决定下一步。人负责目标、方向和需要批准的动作。

**功能迁移尚未完成。** 按原框架行为预期逐项审计，统一起跑检查、完整 AI 事前材料、研究页供料与回执、完整意图监控报告、审批与入向监听、持续推进及通用远端进度探针仍有缺失。现有离线测试通过不能证明整套框架已复原。验收依据见[完整行为预期](<kernel/contracts/framework_expectations.md>)，109 个编号的现状和补齐顺序见[迁移功能验收](<docs/EXPECTATIONS_ACCEPTANCE.md>)。

仓库有两层。**内核**处理任何研究项目都会遇到的事务、预算、任务接管、证据、审查、报告和部署。**agent 比赛配置**描述“要优化的是一个 agent harness”时才需要的题单、轨迹、工具、工作区、泄漏和评测预算。

真实比赛资料、题库、模型、实验结果、现有结论、凭据、机器清单和个人宿主设置均不随仓库提供。它们放在另外建立的研究工作区。示例全部是合成数据。

## 让 AI 带你完成第一次真实运行

克隆仓库后，在仓库目录启动有终端工具的 AI，发送：

> 请完整读取 docs/FIRST_RUN_PROMPT.md，执行“交给 AI 的 prompt”一节。目标是配置我的 Kaggle 与 AutoDL，并完成一轮真实研究循环。先检查现有环境，再问我缺失的信息。不要只给我安装清单，也不要拿合成演示当成完成。

完整指令见 [首次启动 prompt](docs/FIRST_RUN_PROMPT.md)。它引导 AI 检查环境、配置安全凭据引用、补齐真实执行接口、运行对照与候选、分析结果并核查资源停止。你只需提供目标、平台访问和预算等必要决定。

**这不是一键部署脚本。** 当前仓库不自带通用 Kaggle 客户端或完整远端评测启动器。prompt 明确要求 AI 在缺少接线时实际补齐并测试。首次循环需要比赛允许的数据、模型和评测器，不要求 Kaggle 线上提交。本次只核对了指令与仓库接口，尚未在全新机器完成真实平台验收。

## 第一次检查：三条命令

需要 Python 3.11 或更新版本。完整离线验证还需要 Node.js 20 或更新版本。Python 内核只用标准库，不需要安装第三方包。

在本仓库根目录运行：

```powershell
python -X utf8 -B research.py doctor
python -X utf8 -B research.py demo --output .local/demo
python -X utf8 -B research.py selftest --plugins
```

`doctor` 检查配置、完整指令的字节量和部署边界。默认配置有效，但没有真实评测命令，因此会明确显示“不能发起真实评测”。这属于正常初始状态。

`demo` 从一个**尚不存在**的目录开始，走通登记、预算预留、模拟执行、结果取回、资源核对和结案。演示生成 `REPORT.md` 和 `REPORT.json`。它只验证研究流程的机械机制，不能证明任何研究假设成立。第二次运行请换一个新目录。

`selftest --plugins` 运行 Python 回归和三个插件的模拟宿主测试。测试不启动云机，不调用真实模型，不外发消息。

## 目录怎么分

| 位置 | 用途 | 应放什么 |
|---|---|---|
| [kernel/autoresearch/runtime](kernel/autoresearch/runtime/) | 通用事务内核 | 状态、追加事件、预算、任务与资源租约、幂等执行、结果取回、交付和回复台账 |
| [kernel/autoresearch/governance.py](kernel/autoresearch/governance.py) | 通用研究治理 | 计划检查、冻结、独立审查、意图、复盘和经验确认 |
| [kernel/contracts](kernel/contracts/) · [kernel/instructions](kernel/instructions/) | 内核现行契约与读取要求 | 原则、可执行条件、证据边界 |
| [kernel/roles](kernel/roles/) · [kernel/templates](kernel/templates/) | 角色委派与空模板 | 角色材料、事前设计、人读现状页与交接 |
| [kernel/hosts/dsh](kernel/hosts/dsh/) | 首个宿主适配 | 三套项目插件、配置生成和加载检查工具 |
| [kernel/autoresearch/backends](kernel/autoresearch/backends/) | 基础设施适配 | AutoDL 查询、变化事件、负责人绑定与单次监控 |
| [profiles/agent-harness](profiles/agent-harness/) | agent 目标配置 | 题单、结果和轨迹协议；工具、路径、泄漏与预算规则 |
| [kernel/autoresearch/adapters](kernel/autoresearch/adapters/) | 配置层需要的代码适配器 | agent 结果分析、轨迹检查及旧式结果包兼容协议 |
| [tests](tests/) | 合成验证 | 配置、治理和集成测试 |
| [docs](docs/) | 安装、使用和迁移说明 | 有哪些能力、怎么接真实系统、哪些仍需验证 |

**适配器代码不因放在 Python 包里就成为通用研究规则。** 例如，要求每题都有轨迹与补丁是 agent 结果协议；通用执行器只核指定产物、路径与哈希。原本混在同一执行器里的这两部分已经实际分开。

## 建一个真实研究工作区

源码仓库可以供多个研究项目复用。每个项目单独建工作区：

```powershell
python -X utf8 -B research.py init --workspace ../my-research
```

工作区生成完整的 `AGENTS.md`、读取材料哈希清单和空数据库。运行状态放在该工作区的 `.autoresearch/`，不会导入任何旧实验。**完整指令材料已超过部分 DSH 版本默认的 65,536 B 总预算。开始使用前，必须按命令输出核对并提高总预算和单文件上限，再验证实际注入；`init` 不会修改宿主配置。**

轨迹检查入口是 `python profiles/agent-harness/checks/cli.py trace --help`，完整命令见 [配置与接口契约](docs/profiles.md)。

开始一个长期课题时，先保存目标、独立工作区和分支，再接手负责人：

```powershell
python research.py topics --db ../my-research/.autoresearch/runtime.sqlite3 --commit create --topic demo-topic --title "示例课题" --objective "验证一个明确的研究问题" --workspace ../my-research/worktrees/demo-topic --branch research/demo-topic
python research.py topics --db ../my-research/.autoresearch/runtime.sqlite3 --commit claim --topic demo-topic --session my-session --generation 0
python research.py topics --db ../my-research/.autoresearch/runtime.sqlite3 show --topic demo-topic
```

这些命令登记工作区与分支的归属，**不会替你执行 `git worktree add`、创建隔离环境或证明远端隔离**。真实实验适配器还要核查这些条件。负责人每次接管会改变世代号；旧负责人继续写会被拒绝。断线后的交接保存进展、未完成项、在途工作、证据和下一步。

## 怎么换成自己的比赛

从 [agent-harness 配置说明](profiles/agent-harness/README.md) 开始。先复制这份配置到你的研究工作区，再填写：

1. 评测命令数组、结果路径和评测环境的可核版本。
2. 每题时长、工具次数、轮次、命令超时，以及赛事要求的整批预算。
3. 工具名、可访问工作区、禁止路径和需要查的泄漏内容。
4. 官方规则、题库位置、基线和真实数据的独立引用。

默认配置没有真实命令和预算值。没有配全就不能生成可运行命令。命令使用 argv 数组，不经过 shell。预算写进配置并不保证外部 harness 执行这些预算；接入时必须逐项核对。

```powershell
python research.py profile
python research.py results profiles/agent-harness/examples/results.example.jsonl
```

结果分开记录通过、失败、环境错误和超时。比较前检查题集和测量配置；条件不同就显示不可比。轨迹规则为空时显示“未配置检查”，不会当成干净。详见 [配置与接口契约](docs/profiles.md)。

## 三个插件都随内核提供

- **agent-instruction-filter**：处理子会话指令注入，默认保留规则。只有明确配置后才过滤。
- **dsh-doc-gap**：在回合末检查刚讨论的内容是否需要补文档。判定范围由部署配置提供；文档不完整时不得把“没找到”当成可靠证据。
- **autodl-events**：把机器变化事件送到已绑定的负责人会话。默认没有绑定就保留待处理，不广播。

**随仓库提供源码，不表示已经在当前 DSH 启用。** 本仓库不会自动修改你的 DSH profile。安装方式、生成配置和加载验收见 [DSH 说明](docs/dsh.md)。AutoDL 接入与定时监控见 [AutoDL 说明](docs/autodl.md)。真实宿主、云机和模型的联调需要在部署后另验。

## 指令完整性优先

内核读取义务与目标配置的读取义务必须合并。先测量完整材料，再给宿主设置足够的总预算和单文件预算。不能靠缩短规则或静默过滤解决遗漏。

```powershell
python research.py instructions --bundle .local/required-instructions.md --manifest .local/instruction-manifest.json
python research.py instructions --captured path/to/captured-host-request.txt
```

第一条生成完整文本、逐文件哈希和预算建议。第二条检查你提供的宿主请求是否包含完整正文，以及是否出现预算遗漏提示。**生成成功不表示宿主已经注入；提供的捕获文本也需要有真实来源。** 规则变更后要重新生成，并核查实际生效的会话配置。

## 现在能用什么，部署还要补什么

本地可直接使用事务状态、课题接管、预算与授权证据账本、幂等执行和对账机制、计划检查、配置检查、合成演示、结果分析、轨迹检查以及插件自检。

真实模型、正式评分环境、远端执行和真实授权来源需要部署适配器。需要可信证据的授权、审查豁免或交付确认，在没有可信验证器时默认拒绝；普通审查资料可以登记为本地声明，但不等于宿主已验证其独立性。离线演示中的签名与审查都是临时夹具，不能拿去给真实动作授权。

本项目保留持续研究与可选委派机制，但没有安装一个会自动在生产环境开实验的常驻服务。真实的持续推进，需要宿主接收任务、保存结果并重新挂回下一步。通知送达、进程退出和预算租约到期都不证明实验成功或机器空闲。

- 研究流程与制度边界：[研究说明](docs/research.md)。
- 实际拆分与未搬迁内容：[迁移说明](docs/migration.md)。
- 本次执行过的检查与结果：[验收记录](docs/VERIFICATION.md)。
- 源码来源与许可证状态：[来源说明](NOTICE.md)。

这是独立 Git 项目。最新框架同步内容与能力边界见 [升级记录](docs/UPGRADE_SYNC.md)。
