<!--
GENERATED-BY: repository creation, 2026-11-09 +08:00
LAST-EDIT: 2026-11-09 本批同步：补 review v2、issues exp、questions、assurance、progress 跨轮预检/阶段排序与 D2 回复绑定；通用补项与真实部署分开
PURPOSE: 给首次查看项目的人说明用途、边界和可执行检查。
ORIGIN: reusable research mechanisms and target-specific evaluation adapters
CUMULATIVE-DELTA: 2026-11-09 固定分层项目模板与默认初始化，保留显式bundle兼容；2026-11-09 新增 [怎么跑](<docs/RUNNING.md>)（工作区 / 专题隔离 / 预算 / 执行主链 / 人读页 / 监控 / 审批 / 通道 / 远端 / 进度 / 问题台账的实际命令）；
改写迁移状态段：本地独立环境、完整服务链冷启动（含第二轮共享预算与未复盘阻断）及可信宿主探针已验；真实通道租户、真实 SSH/POSIX 停止/机器空闲、远端上传和平台仍待现场验收；
把只登记 Topics 的首次隔离改成真实 worktree + 独立环境命令（claim 保留）；明确 demo 只是底层事务合成、不代科研主链；109 编号验收表继续保留。
依赖口径：Python 内核只用标准库，飞书加密收件另需可选 cryptography（requirements-feishu.txt）；插件 Node 20+，可信 DSH 压缩日志 Node 24+。
本批新增：review v2 交接、issues exp 关联、普通提问、assurance 清单、progress preflight/next-round/schedule 与 D2 回复绑定；assurance 共16个分项，复用真实预算判据；progress CLI 固定只读超期源与远端组合路径均已验。
-->
# AutoResearch：可复用研究内核与 agent 比赛配置

这套项目的目标是让 AI 围绕一个研究目标持续工作：先写假设和判断标准，再安排实验，保存原始证据，核查结果，决定下一步。人负责目标、方向和需要批准的动作。

**迁移状态：本次逐项复核发现的通用功能已补齐，现场验收仍未完成。** 现在可以按 [怎么跑](<docs/RUNNING.md>) 依次调用工作区初始化、专题隔离、共享预算、研究执行主链（事前材料 → 起跑闸 → 真子进程 → 结束 → 复盘）、人读页供料与验收、意图与过程监控、审批与入向、通道配置与收件、受管远端执行、进度采集与受管推进器、问题台账。以下三类仍需核验，逐项写在[迁移功能验收](<docs/EXPECTATIONS_ACCEPTANCE.md>)（109 个编号全部保留）：

- **真实部署未验**：真实消息通道租户送达与回复、真实 SSH / POSIX 停止与机器空闲、远端代码 / 题单上传步骤、真实云机与平台提交。空工作区的本地冷启动（含第二轮共享预算与未复盘阻断）已用合成证据跑通，详见[验收记录](<docs/VERIFICATION.md>)。
- **部分验收**：V1–V3 意图监控语义核只有 3 个合成案例的一次独立核（V1 修订后通过）；真实宿主探针只证明单次绑定，不外推。
- **需要独立判断的事项**：结论是否成立、页面是否易读、审查是否找对了问题，仍需 agent 或人阅读原始证据。

现有离线测试通过只证明被覆盖的机制，不证明整套框架已复原。验收依据见[完整行为预期](<kernel/contracts/framework_expectations.md>)。

仓库有两层。**内核**处理任何研究项目都会遇到的事务、预算、任务接管、证据、审查、报告和部署。**agent 比赛配置**描述“要优化的是一个 agent harness”时才需要的题单、轨迹、工具、工作区、泄漏和评测预算。

真实比赛资料、题库、模型、实验结果、现有结论、凭据、机器清单和个人宿主设置均不随仓库提供。它们放在另外建立的研究工作区。示例全部是合成数据。

## clone 后直接保留项目组织

固定的[项目模板](<project/AGENTS.md>)随 Git 提供，包含根规则、平台线、实验线、框架子项目、分层索引、读取路由、维护清单和空台账。它保留已维护的组织方式，不要求 AI 重新设计文件结构。

```powershell
python -X utf8 -B research.py init --workspace ../my-research
python -X utf8 -B research.py layout-check --workspace ../my-research
```

默认初始化按模板复制目录和正文，安装所选 profile 与内核契约副本，重写本地链接并生成空状态库及来源哈希回执。目标必须尚不存在。工具固定的 `topics/`、`worktrees/`、`environments/` 与 `.autoresearch/` 保持兼容；中文业务目录承担分层入口和资料组织，不复制另一套专题正文。

结构、内容边界及与原组织的对应关系见[组织映射](<project/ORGANIZATION_MAP.md>)；初始化、维护规则恢复及独立读取测试见[项目组织验收](<docs/PROJECT_ORGANIZATION.md>)。宿主是否实际分层加载须另测；链接检查不冒充注入证明。最新监控默认关闭、审计减负及专题文档创建已同步，范围与验证见[框架同步记录](<docs/FRAMEWORK_SYNC.md>)。

## 让 AI 带你走一轮

克隆仓库后，在仓库目录启动有终端工具的 AI，发送：

> 请完整读取 docs/RUNNING.md，按里面的主入口命令带我走一轮：先 `doctor`，再问我目标、平台访问与预算这些**只有我能定**的事，然后依次建工作区、建专题隔离、做预算授权与预留、写事前计划并跑起跑闸。凡是要真实通道、云机或我本人授权的动作，先停下来问我；凡是文档标「尚未接线 / 待验」的，直接告诉我缺什么，不要自己编一个入口。

操作命令以 [怎么跑](<docs/RUNNING.md>) 为准。[首次启动提示词](<docs/FIRST_RUN_PROMPT.md>)已按当前实现更新，可交给新会话使用。遇到尚待接线或未验项目，应说明具体缺口。

当前仓库有默认关闭的飞书 HTTP 适配层（`channels doctor/listen/poll` 与 `decisions --channel-config`）和默认关闭的受管远端 CLI（`remote doctor/check/provision/start/query/cancel/collect/release`）。真实首轮仍需比赛允许的数据、模型、评测器与凭据引用；通用 Kaggle 客户端、真实云机和租户消息往返尚未在全新机器验收。

## 第一次检查：三条命令

需要 Python 3.11 或更新版本。Python 内核与通用工具只用标准库；飞书**加密事件收件**另需可选 `cryptography`（`python -m pip install -r requirements-feishu.txt`）。三个插件的离线测试需要 Node.js 20 或更新版本；读取真实 DSH 压缩会话日志的可信核验需要支持 Zstandard 的 Node.js 24 或更新版本。

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
| [kernel/autoresearch/monitoring.py](kernel/autoresearch/monitoring.py) | 意图与过程监控 | 默认关闭，显式启用后核四态、三触发、两水位与独立子会话报告、意见处置与跨报告阻断 |
| [kernel/autoresearch/decisions.py](kernel/autoresearch/decisions.py) | 审批与入向 | 自包含审批页、先页后卡、双回执重算、两小时超时、文字回复窗口 |
| [kernel/autoresearch/progress.py](kernel/autoresearch/progress.py) | 进度采集与推进器 | 声明式 spec、采集比较、租约、静默窗口、一次唤醒即退出、显式重挂 |
| [kernel/autoresearch/progress_host.py](kernel/autoresearch/progress_host.py) | 宿主侧联动 | DSH goal 重放、超期只读、受管 job 登记台账（只读宿主） |
| [kernel/autoresearch/issues.py](kernel/autoresearch/issues.py) | 问题台账（§9） | 首错登记、查重（含 exp）、负责人、修复、复验（argv + 退出码）、冷件索引 |
| [kernel/autoresearch/assurance.py](kernel/autoresearch/assurance.py) | 统一检查清单（只读） | 16项只读聚合；含审计、共享预算与远端租约，区分未启动与历史记录 |
| [kernel/autoresearch/questions.py](kernel/autoresearch/questions.py) | 普通提问台账（E7） | 待答 / 已认证回复 / 默认非 blocking；不建审批页 |
| [kernel/autoresearch/review_continuation.py](kernel/autoresearch/review_continuation.py) | review v2 交接 | 下一步判据与证据、成本范围、负责人、是否需批准；旧记录 unknown |
| [kernel/autoresearch/{workflow,reporting,monitoring,decisions,channels,progress,progress_host,remote,budget,workspace,issues}_cli.py](kernel/autoresearch/) | 上面各层的命令行入口 | 由 `research.py <名称>` 转发 |
| [kernel/autoresearch/channels](kernel/autoresearch/channels/) | 真实通道适配层 | 飞书发送 / 回执复核 / 验签 / AES-256-CBC 解密（`cryptography` 可选） |
| [kernel/autoresearch/remote_workflow.py](kernel/autoresearch/remote_workflow.py) · [remote_cli.py](kernel/autoresearch/remote_cli.py) | 受管远端执行 | Workflow 闸 → Runtime 预算/租约 → SSHAdapter；默认关闭 |
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

工作区默认生成固定的多级目录、分层 `AGENTS.md`、本地规则与配置、来源清单和空数据库。运行状态放在 `.autoresearch/`，不会导入旧实验。`init --layout bundle` 显式保留旧式完整合并根指令；全量 `instructions` 仍供材料完整性诊断，不能将其体积当作分层开局注入量。两种方式都不修改宿主预算或插件，实际加载仍须验证。

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

`workspace topic`从版本模板建立TOPIC、日期EVIDENCE及STATUS骨架，骨架不算已交付报告。它会真的建 `worktrees/<topic>` 与 `environments/<topic>`，失败时把 `status=incomplete` 与错误写进 `topics/<topic>/provision.json`，**不静默覆盖**。它**不装依赖、不跑评测**（`ready_for_evaluation=false`）。

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
| 审批与入向 | `research.py decisions prepare/send/status/inbox/overdue/reply/hook/authorize-check` | 决策台账 + 自包含审批页 + 通道回执 |
| 通道配置与收件 | `research.py channels doctor/listen/poll` + `decisions --channel-config` | 通道台账 + 决策库；`doctor` 只读 |
| 受管远端执行 | `research.py remote doctor/check/provision/start/query/cancel/collect/release` | Runtime 预算/租约 + 远端 worker 状态；默认关闭 |
| 进度采集与推进器 | `research.py progress probe/tick/watch/rearm/outcome/status` + `preflight/next-round/schedule` | 事件库 + 租约 + `needs_rearm`；下一轮只读给命令 |
| 宿主侧联动 | `research.py progress-host doctor/goal/overdue/plan/register/status` | 固定 deployment 配置 + append-only job 台账 |
| 问题台账（§9） | `research.py issues record/status/assign/fix/verify/reopen/archive/index` | `<工作区>/.autoresearch/issues.sqlite3`；`--exp` 进入查重与关联 |
| 统一检查清单 | `research.py assurance checklist` | 16项只读聚合，复用审计、预算与远端租约判据 |
| 普通提问 | `research.py questions ask/open/answer/declare/link/expire/cancel` | `<工作区>/.autoresearch/runtime.sqlite3` 的提问台账 |

三条边界：**起跑闸是 fail-closed 的**（缺当前审计或准入就拒绝启动；监控仅在用户显式启用后参与，且不起来子进程）；**没有可信验证器时授权、豁免、交付确认会拒绝**；**通道、云机、平台凭据属于部署配置**，仓库只放引用。

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

**本地可直接用（有实际入口 + 专项测试）**：事务状态与追加事件、课题接手与世代、专题真实隔离（worktree + 独立环境 + 主文档快照）、共享预算的授权/预留/释放/结算台账（含可信时序 / fence）、幂等执行与对账、研究执行主链（事前四锚点 → 起跑闸 → 真子进程 → 结束 → **review v2** → 自动快照 / 报告投影）、人读页完整供料与回收回执、意图与过程监控（四态/三触发/两水位/独立报告核验/意见处置）、**自然语言 close（旧 proof 与 `--user-quote` 两路）**、审批页与双回执状态机、入向文字回复窗口、**通道 `doctor/listen/poll` 与 `decisions --channel-config`（含可选 AES 解密）**、**受管远端 CLI（默认关闭，预算用计划授权那笔同一预留）**、只读服务（`DecisionService.readonly`，不建表/目录/通道台账）、通用进度采集与受管推进器（一次唤醒即退出 + 显式重挂 + **preflight/next-round/schedule 只读给命令**）、**D2 入向超期闸（回复按 source+event_id）**、宿主侧 goal/超期/job 只读联动、问题台账（查重/负责人/修复/复验/冷件索引 + **exp 关联**）、**普通提问台账（默认非 blocking）**、**统一检查清单（含审计、共享预算与远端租约）**、配置与指令测量、插件自检。

**需要部署适配器（缺配置不等于缺功能，但也不等于已验）**：真实租户的消息送达与收件、真实模型与正式评分环境、真实远端机器与 POSIX 停止 / 机器空闲、真实授权来源（宿主用户消息）。

**已知未完成**（逐项见[迁移功能验收](<docs/EXPECTATIONS_ACCEPTANCE.md>)）：真实通道租户验收；真实 SSH / POSIX 停止与机器空闲；真实云机 / 平台提交；当前模型请求实际注入与宿主 goal activation；V1–V3 只有合成语义核。⚠️ 远端停止只覆盖本 job 的进程组，不覆盖子进程 `setsid` 逃逸，更强隔离要部署 cgroup / namespace；group-stop 不等于整台机器停止。**这些写成缺口，不当已完成。**

需要可信证据的授权、审查豁免、交付确认，在没有可信验证器时**默认拒绝**；普通资料可登记为本地声明（`declared`），但那不等于宿主已验证。离线演示与测试里的签名、审查、通道都是临时夹具，不能拿去给真实动作授权。

本项目**没有**安装一个会自动在生产环境开实验的常驻服务。持续研究靠受管推进器 + 显式重挂：一次唤醒处理完就退出并写 `needs_rearm`，由负责人挂回；通知送达、进程退出、租约到期都不证明实验成功或机器空闲。

- 操作命令与待验清单：[怎么跑](docs/RUNNING.md)。
- 研究流程与制度边界：[研究说明](docs/research.md)。
- 实际拆分与未搬迁内容：[迁移说明](docs/migration.md)。
- 本次执行过的检查与结果：[验收记录](docs/VERIFICATION.md)。
- 源码来源与许可证状态：[来源说明](NOTICE.md)。

这是独立 Git 项目。最新框架同步内容与能力边界见 [升级记录](docs/UPGRADE_SYNC.md)。
