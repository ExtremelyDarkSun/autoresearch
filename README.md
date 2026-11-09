<!--
GENERATED-BY: repository creation, 2026-11-09 +08:00
LAST-EDIT: 2026-11-09 主入口落位：把「没有模块」的旧表述改成「有实际入口 / 尚缺接线 / 真实部署未验」，并指向 RUNNING.md
PURPOSE: 给首次查看项目的人说明用途、边界和可执行检查。
ORIGIN: reusable research mechanisms and target-specific evaluation adapters
CUMULATIVE-DELTA: 2026-11-09 新增 [怎么跑](<docs/RUNNING.md>)（工作区 / 专题隔离 / 预算 / 执行主链 / 人读页 / 监控 / 审批 / 进度 / 问题台账的实际命令）；
改写迁移状态段：主链入口已存在，但真实通道、真实云机与完整冷启动未验，若干接线仍在进行中；
把只登记 Topics 的首次隔离改成真实 worktree + 独立环境命令（claim 保留）；明确 demo 只是底层事务合成、不代科研主链；109 编号验收表继续保留。
-->
# AutoResearch：可复用研究内核与 agent 比赛配置

这套项目的目标是让 AI 围绕一个研究目标持续工作：先写假设和判断标准，再安排实验，保存原始证据，核查结果，决定下一步。人负责目标、方向和需要批准的动作。

**迁移状态：主链入口已经在仓库里，但不是「全部完成」。** 现在可以按 [怎么跑](<docs/RUNNING.md>) 依次调用工作区初始化、专题隔离、共享预算、研究执行主链（事前材料 → 起跑闸 → 真子进程 → 结束 → 复盘）、人读页供料与验收、意图与过程监控、审批与入向、进度采集与受管推进器、问题台账。以下三类**仍未完成**，逐项写在[迁移功能验收](<docs/EXPECTATIONS_ACCEPTANCE.md>)（109 个编号全部保留）：

- **真实部署未验**：真实消息通道送达与回复、真实云机与平台提交、从 clone 到完整冷启动的全流程。
- **接线进行中**：监控侧旧双账统一与起跑事务内复核、审批侧绑定实际 execution_plan、宿主侧 goal 持久事件与超期实读。
- **本就是语义纪律**：结论是否成立、页面是否易读、审计是否有质量，机械检查证明不了。

现有离线测试通过只证明被覆盖的机制，不证明整套框架已复原。验收依据见[完整行为预期](<kernel/contracts/framework_expectations.md>)。

仓库有两层。**内核**处理任何研究项目都会遇到的事务、预算、任务接管、证据、审查、报告和部署。**agent 比赛配置**描述“要优化的是一个 agent harness”时才需要的题单、轨迹、工具、工作区、泄漏和评测预算。

真实比赛资料、题库、模型、实验结果、现有结论、凭据、机器清单和个人宿主设置均不随仓库提供。它们放在另外建立的研究工作区。示例全部是合成数据。

## 让 AI 带你走一轮

克隆仓库后，在仓库目录启动有终端工具的 AI，发送：

> 请完整读取 docs/RUNNING.md，按里面的主入口命令带我走一轮：先 `doctor`，再问我目标、平台访问与预算这些**只有我能定**的事，然后依次建工作区、建专题隔离、做预算授权与预留、写事前计划并跑起跑闸。凡是要真实通道、云机或我本人授权的动作，先停下来问我；凡是文档标「尚未接线 / 待验」的，直接告诉我缺什么，不要自己编一个入口。

**操作命令以 [怎么跑](<docs/RUNNING.md>) 为准。** 旧的[首次启动 prompt](docs/FIRST_RUN_PROMPT.md) 是更早的 onboarding 脚本：它会把「补齐真实执行接口」交给 AI 自己写。现在研究主链、人读页、监控、审批、进度与问题台账都已有实际入口，**缺的是部署配置与真实验收**；仓库里没有的接线，本文档与验收表直接写成缺口，⛔ 不靠「让 AI 补一段代码」来把首次可用性补圆。

**这不是一键部署脚本。** 当前仓库不自带通用 Kaggle 客户端、完整远端评测启动器或消息通道客户端。真实首轮需要比赛允许的数据、模型与评测器，以及你自己的通道与凭据引用；尚未在全新机器完成真实平台验收。

## 第一次检查：三条命令

需要 Python 3.11 或更新版本。完整离线验证还需要 Node.js 20 或更新版本。Python 内核只用标准库，不需要安装第三方包。

在本仓库根目录运行：

```powershell
python -X utf8 -B research.py doctor
python -X utf8 -B research.py demo --output .local/demo
python -X utf8 -B research.py selftest
python -X utf8 -B research.py selftest --plugins     # 需要 Node.js
```

`doctor` 检查配置、完整指令的字节量和部署边界。默认配置有效，但没有真实评测命令，因此会明确显示“不能发起真实评测”。这属于正常初始状态。

`demo` 从一个**尚不存在**的目录开始，走通登记、预算预留、模拟执行、结果取回、资源核对和结案，生成 `REPORT.md` 与 `REPORT.json`。⚠️ **它只是底层事务的合成演示**：没有事前四锚点、没有 argv 起跑闸、没有审计/监控/准入三闸、没有真实子进程、没有复盘与页面供料。**别拿它当统一科研主链已可用的证据**——主链看[怎么跑](<docs/RUNNING.md>)。第二次运行请换一个新目录。

`selftest` 运行 Python 回归；加 `--plugins` 再跑三个插件的模拟宿主测试。测试不启动云机，不调用真实模型，不外发消息。

## 目录怎么分

| 位置 | 用途 | 应放什么 |
|---|---|---|
| [kernel/autoresearch/runtime](kernel/autoresearch/runtime/) | 通用事务内核 | 状态、追加事件、预算、任务与资源租约、幂等执行、结果取回、交付和回复台账 |
| [kernel/autoresearch/governance.py](kernel/autoresearch/governance.py) | 通用研究治理 | 计划检查、冻结、独立审查（宿主绑定）、意图、复盘和经验确认 |
| [kernel/autoresearch/workflow.py](kernel/autoresearch/workflow.py) | 研究执行主链 | 事前材料与来源哈希、起跑闸、真子进程与退出码、结束、复盘、只读状态 |
| [kernel/autoresearch/reporting.py](kernel/autoresearch/reporting.py) | 人读页供料与验收 | 完整供料包、页面检查、scribe 回执、整轮交付、checkpoint 时效诊断 |
| [kernel/autoresearch/monitoring.py](kernel/autoresearch/monitoring.py) | 意图与过程监控 | 四态、三触发、两水位、独立子会话报告核验、意见处置与跨报告阻断 |
| [kernel/autoresearch/decisions.py](kernel/autoresearch/decisions.py) | 审批与入向 | 自包含审批页、先页后卡、双回执重算、两小时超时、文字回复窗口 |
| [kernel/autoresearch/progress.py](kernel/autoresearch/progress.py) | 进度采集与推进器 | 声明式 spec、采集比较、租约、静默窗口、一次唤醒即退出、显式重挂 |
| [kernel/autoresearch/progress_host.py](kernel/autoresearch/progress_host.py) | 宿主侧联动 | DSH goal 重放、超期只读、受管 job 登记台账（只读宿主） |
| [kernel/autoresearch/issues.py](kernel/autoresearch/issues.py) | 问题台账（§9） | 首错登记、查重、负责人、修复、复验（argv + 退出码）、冷件索引 |
| [kernel/autoresearch/{workflow,reporting,monitoring,decisions,progress,progress_host,budget,workspace,issues}_cli.py](kernel/autoresearch/) | 上面各层的命令行入口 | 由 `research.py <名称>` 转发 |
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

开始一个长期课题时，先用**真实隔离命令**建专题（不是只登记一行元数据），再接手负责人：

```powershell
# 1) 建专题：研究文档 + 真实 worktree（或独立空 Git 仓库）+ 独立 Python 环境
python -X utf8 -B research.py workspace topic --workspace ../my-research --topic demo-topic `
    --objective "验证一个明确的研究问题" --requirements-file 需求原文.md `
    --owner-session my-session [--code-repo C:\path\to\repo] [--revision HEAD] [--no-environment]

# 2) 接手负责人（世代号；旧负责人继续写会被拒）
python -X utf8 -B research.py topics --db ../my-research/.autoresearch/runtime.sqlite3 `
    --commit claim --topic demo-topic --session my-session --generation 0

# 3) 查看
python -X utf8 -B research.py topics --db ../my-research/.autoresearch/runtime.sqlite3 show --topic demo-topic
```

`workspace topic` 会真的建 `worktrees/<topic>` 与 `environments/<topic>`，失败时把 `status=incomplete` 与错误写进 `topics/<topic>/provision.json`，**不静默覆盖**。它**不装依赖、不跑评测**（`ready_for_evaluation=false`）。

⚠️ 旧版只有 `topics create`：它只登记目录与分支字符串，**不会**建 worktree、不会建独立环境、也不证明远端隔离。真实实验仍需把这两步都做到；`claim` / 交接命令保留不变。

实验前后各留一次专题主文档原始快照：

```powershell
python -X utf8 -B research.py workspace snapshot --workspace ../my-research --topic demo-topic --exp-id P1 --stage before
```

## 主入口一览（详细命令见[怎么跑](<docs/RUNNING.md>)）

| 要做的事 | 入口 | 写在哪 |
|---|---|---|
| 初始化工作区 | `research.py init` | `<工作区>/.autoresearch/runtime.sqlite3`、`AGENTS.md` |
| 建专题隔离 | `research.py workspace topic` / `snapshot` | `topics/<专题>/`、`worktrees/`、`environments/` |
| 预算授权与预留 | `research.py budget grant/reserve/status/release/settle` | `budget_accounts` / `budget_reservations`（同一个库） |
| 研究执行主链 | `research.py workflow prepare/check/launch/finish/review/status` | `wf_preparations` / `wf_runs` / `wf_finishes` / `wf_reviews` / `wf_events` |
| 人读页供料与验收 | `research.py reporting prepare/check/ack/round-check/diagnose` | `topics/<专题>/STATUS.md` + `.autoresearch/state/reporting/<专题>/` |
| 意图与过程监控 | `research.py monitoring register/sync/event/prepare/record/check` | `<工作区>/.autoresearch/monitoring.sqlite3` |
| 审批与入向 | `research.py decisions prepare/send/status/inbox/overdue/reply/hook` | 决策台账 + 自包含审批页 + 通道回执 |
| 进度采集与推进器 | `research.py progress probe/tick/watch/rearm/outcome/status` | 事件库 + 租约 + `needs_rearm` |
| 宿主侧联动 | `research.py progress-host doctor/goal/overdue/plan/register/status` | 固定 deployment 配置 + append-only job 台账 |
| 问题台账（§9） | `research.py issues record/status/assign/fix/verify/reopen/archive/index` | `<工作区>/.autoresearch/issues.sqlite3` |

三条边界：**起跑闸是 fail-closed 的**（缺审计/监控/准入任一就拒绝启动，且不起来子进程）；**没有可信验证器时授权、豁免、交付确认会拒绝**；**通道、云机、平台凭据属于部署配置**，仓库只放引用。

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

**本地可直接用（有实际入口 + 专项测试）**：事务状态与追加事件、课题接手与世代、专题真实隔离（worktree + 独立环境 + 主文档快照）、共享预算的授权/预留/释放/结算台账、幂等执行与对账、研究执行主链（事前四锚点 → 起跑闸 → 真子进程 → 结束 → 复盘）、人读页完整供料与回收回执、意图与过程监控（四态/三触发/两水位/独立报告核验/意见处置）、审批页与双回执状态机、入向文字回复窗口、通用进度采集与受管推进器（一次唤醒即退出 + 显式重挂）、宿主侧 goal/超期/job 只读联动、问题台账（查重/负责人/修复/复验/冷件索引）、配置与指令测量、插件自检。

**需要部署适配器（缺配置不等于缺功能，但也不等于已验）**：真实消息通道（页 + 卡 + 回执送达）、真实模型与正式评分环境、远端提交与云机、真实授权来源（宿主用户消息）。

**已知未完成**（逐项见[迁移功能验收](<docs/EXPECTATIONS_ACCEPTANCE.md>)）：监控侧旧双账统一与起跑事务内复核、审批侧绑定实际 execution_plan、宿主侧 goal 持久事件与超期实读、从 clone 到完整冷启动的端到端验收。**这些写成缺口，不当已完成。**

需要可信证据的授权、审查豁免、交付确认，在没有可信验证器时**默认拒绝**；普通资料可登记为本地声明（`declared`），但那不等于宿主已验证。离线演示与测试里的签名、审查、通道都是临时夹具，不能拿去给真实动作授权。

本项目**没有**安装一个会自动在生产环境开实验的常驻服务。持续研究靠受管推进器 + 显式重挂：一次唤醒处理完就退出并写 `needs_rearm`，由负责人挂回；通知送达、进程退出、租约到期都不证明实验成功或机器空闲。

- 操作命令与未接线清单：[怎么跑](docs/RUNNING.md)。
- 研究流程与制度边界：[研究说明](docs/research.md)。
- 实际拆分与未搬迁内容：[迁移说明](docs/migration.md)。
- 本次执行过的检查与结果：[验收记录](docs/VERIFICATION.md)。
- 源码来源与许可证状态：[来源说明](NOTICE.md)。

这是独立 Git 项目。最新框架同步内容与能力边界见 [升级记录](docs/UPGRADE_SYNC.md)。
