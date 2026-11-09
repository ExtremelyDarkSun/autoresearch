<!--
GENERATED-BY: repository bootstrap, 2026-11-09 +08:00
LAST-EDIT: 2026-11-09 主链复核：补 lifecycle / execution_target / channels / SSH 后端落点；把已接线的三项移出在途表，保留远端、资源租约与通道入口的待验边界
PURPOSE: 说明实际拆分落点、保留机制和运行边界。
ORIGIN: extraction inventory and direct source inspection
CUMULATIVE-DELTA: 2026-11-09 补主链新模块（workflow/reporting/monitoring/decisions/progress/progress_host/issues/host_evidence/workspace/admission/lifecycle/execution_target/channels/backends）
的落点与真实边界；不再用「在途」表述已接通的接线；只剩真实部署待验：通道租户、SSH/POSIX/机器空闲/上传、云机/平台、真实冷启动；保留实例内容不进仓库的口径。
-->
# 迁移与拆分说明

这次交付是独立的新 Git 项目。原项目继续保留，新项目不读取原项目的数据库、实验目录、当前比赛配置或凭据文件。

**按完整行为预期验收，功能迁移仍未完成。** 本地执行、预算、审计、监控、报告、消息通道和受管远端已形成实际入口，并有专项测试。但统一检查汇总、成本与审批复盘字段、普通主动提问记录、下一轮统一检查和跨专题阶段排序仍需补齐。它们属于通用实现缺口，不能算作填配置即可解决。真实部署、语义判断和单次探针的局限也单独保留。逐项见[109 项功能迁移验收](<EXPECTATIONS_ACCEPTANCE.md>)；验收依据见[中性化完整预期](<../kernel/contracts/framework_expectations.md>)。

## 内核、agent 比赛配置、其他

| 原来混在一起的功能 | 新位置 | 实际处理 |
|---|---|---|
| 实验状态、追加事件、版本冲突、请求幂等 | `kernel/autoresearch/runtime/store.py` | 抽取事务实现；不设旧状态路径 |
| 授权来源验证、跨轮系列额度、预留与结算 | `runtime/accounts.py` | 保留机制；真实来源由可信适配器注入；无适配器不授权 |
| 任务所有权、世代、任务令牌、资源占用与暂停 | `runtime/scheduler.py` | 保留机制；租约过期不等同机器空闲 |
| 远端提交、按原请求查作业、取回与重试 | `runtime/execution.py`、`runtime/service.py`、`backends/ssh.py`、`backends/ssh_worker.py`、`remote_workflow.py`、`remote_cli.py` | 保留幂等生命周期；SSH 适配器（默认关闭）真的 `provision/submit/query/collect/cancel` 并核真实字节；远端 CLI 已有，执行预算用计划 `authorization` 的同一笔预留；协议 v2 停止证据（`stop_verified` + `stop_evidence` + request/token/attempt + `argv_sha256`）与事务内 TOCTOU 复核已落地；`claim` 后 `send` 前调同一 `lifecycle sync` 生成 running / reporting / `AI_WORK` 投影（`unknown` 保留，投影失败不伪终态、不阻 cancel）。真实 SSH / POSIX / 平台未验，代码 / 题单上传是独立部署步骤 |
| 每题轨迹、补丁、结果与 sweep 的特定要求 | `adapters/execution_contract.py` | 从同一执行器内部拆出 `AgentRunSpec`、`AgentExecution` 和可选 `AgentRuntime` |
| 结果里的 agent 成败字段与评测参数 | `profiles.py`、`adapters/agent_harness.py`、`profiles/agent-harness/` | 提炼成中性配置与结果协议；默认不连真实评测 |
| 轨迹工具名、工作区、泄漏判据 | `profiles/agent-harness/` 与适配器 | 规则参数化；空规则不算检查通过 |
| 持久专题、负责人交接、checkpoint、工作区与分支绑定 | `runtime/topics.py`、`runtime/topic_cli.py` | 抽取协调机制；登记不代替真实隔离验证 |
| **专题真实隔离（代码 / 环境 / 文档 / 快照）** | `workspace.py`、`workspace_cli.py` | **新实现**：真 `git worktree`（或独立空 Git 仓库）+ `venv --without-pip` + 专题文档与原始要求；失败留 `provision.json(status=incomplete)`；⛔ 不装依赖、不跑评测 |
| **研究执行主链（事前材料 → 起跑闸 → 真子进程 → 结束 → 复盘）** | `workflow.py`、`workflow_cli.py` | **新实现**：四锚点 + 实现五项/files + 显式 argv；来源按 sha256 判过期；同专题/同计划原子占用；退出码原值保留；文档失败不改退出码；生产 CLI 固定接 `Monitoring` / `DSHHost` / `Admission` / `ExecutionTarget` |
| **执行落点（真实 cwd / 独立 venv）** | `execution_target.py` | **新实现**：只认已 provision 的专题代码目录与独立解释器；拒绝 `ssh` 直连、非本地 scope 与 GPU 落点（防止把本地超时当远端停机） |
| **执行 ↔ 交付投影** | `lifecycle.py` | **新实现**：从权威 `wf_*` / `gov_plans` 生成 `plan-vN.json`、`AI_WORK.md`、`receipts/*.json`、before/after 快照与 `reporting` 事件；只读、失败只记诊断 |
| **执行准入（预算预留 + 决策）** | `admission.py` | **新实现**：复用 `budget_accounts` / `budget_reservations`，不另造预算账本；`large` / `submission` 另要 approved 决策；可信时序 `trusted_ordering` 在同一事务里判新旧，被拒零副作用；资源租约由调度器承担（远端模块接线） |
| **只读服务构造** | `runtime/store.py`、`decisions.py`、`decisions_cli.py` | **新实现**：`DecisionService.readonly(db)` / `ReadonlyStore` 用 `mode=ro` + `query_only`，不建表、不建目录、不装通道台账；缺库 / 旧 schema 判不可判，不冒充通过 |
| **人读页供料与验收** | `reporting.py`、`reporting_cli.py` | **新实现**：完整供料包（来源正文 + 旧页全文 + 缺项补读）、八节/四段校验、scribe 回执、整轮交付、checkpoint 时效、流程状态投影；回执分 `declared` / `verified` |
| **意图与过程监控（完整报告形状）** | `monitoring.py`、`monitoring_cli.py` | **新实现**：四态、三触发、两水位、独立子会话报告核验（fork / 自造 JSON 拒）、意见逐条处置、跨报告阻断、四理由闭合、启动前只读检查；close 两路（旧 proof / 自然语言 `--user-quote`）都可用，原话与独立报告同条绑定，`achieved` / `unsatisfiable` 不能靠原话绕过 |
| **审批与入向** | `decisions.py`、`decisions_cli.py` | **新实现**：自包含审批页（六块 + 每方案 13 字段 + 执行规范全对象绑定）、先页后卡、双回执按台账重算、两小时未批准取消、文字回复 300 s 窗口、入向 `inbox`/`reply`/`hook`；`status`/`overdue`/`authorize-check` 只读 |
| **真实 HTTP 通道 + 命令行** | `channels/feishu.py`、`channels/crypto.py`、`channels_cli.py`、`requirements-feishu.txt`、`decisions_cli.py --channel-config` | **新实现**：飞书发送 / 回执 GET 复核 / 验签 / 入向标准化；`doctor` 只读不建库、`listen` 缺解密后端时起服务前 exit 2 且不建库、`poll` 一次性；AES-256-CBC 解密是可选依赖（`cryptography`），缺它不降级；**真实租户未验** |
| **进度采集与受管推进器** | `progress.py`、`progress_cli.py` | **新实现**：声明式闭集 spec、采集/比较、租约 fencing、静默窗口、一次唤醒即退出并写 `needs_rearm`、三出口 `outcome`、采集失败 fail-closed |
| **宿主侧联动（goal / 超期 / job）** | `progress_host.py`、`progress_host_cli.py` | **新实现**：DSH goal 重放、超期只读库、受管 job 登记（先核 exact 命令 + jobId）；固定 deployment 配置；只读宿主 |
| **问题台账（§9）** | `issues.py`、`issues_cli.py` | **新实现**：哈希链 append-only、`command+scope` 精确查重（可选显式 `dedupe_key`，⛔ 不模糊合并）、显式 owner、修复/复验（argv + 退出码）、冷件不覆盖、失败留诊断 |
| **宿主证据核验（DSH 会话日志）** | `host_evidence.py` | **新实现**：只读真实日志根（默认 `~/.dsh`，⛔ 不接受调用方自带路径）、核用户命令逐键相等、核独立子会话 spawn/父子谱系、拒绝 fork 继承 |
| 交付重试、回执、入向来源和回复任务 | `runtime/delivery.py`、`runtime/inbox.py` | 保留通用账本；真实消息通道默认关闭 |
| 可选委派、真实宿主验证接口 | `runtime/delegation.py`、`orchestration/delegation.py` | 保留注册、派出、结束与验收的区别；无可信宿主不冒充已派出 |
| 计划、判据出处、回归集合、分支、冻结、独立审查 | `governance.py`、`kernel/contracts/` | 实际重新实现通用治理；审查独立性绑定宿主证据，未核过只记 `declared` |
| 专项意图、节点监控、复盘、经验确认与文档维护 | 同上，以及 `kernel/roles/`、`kernel/templates/` | 保留通用协议与本地验证；声明和可信来源分开 |
| 指令过滤、文档遗漏、AutoDL 变化通知 | `kernel/hosts/dsh/plugins/` | 三套源码与自检均随内核；部署配置单独生成 |
| AutoDL 查询、变化/冷却/首轮基线、目标会话绑定 | `kernel/autoresearch/backends/autodl/` | 独立后端；机器、状态根和凭据引用参数化 |
| 当前题库、模型、真实轨迹、结果、历史报告 | 不迁移 | 属于研究实例；不得作为公开示例 |
| 当前宿主 profile、个人插件、机器名单、会话身份 | 不迁移 | 属于个人部署；新项目只给空部署配置 |

`docs/runtime-extraction.json` 记录抽取源代码的哈希与新目标。它不包含任何源运行状态。各 profile 与插件另有具体抽取说明。

## 待验的部署接线（不数完成）

| 事项 | 当前状态 | 影响 |
|---|---|---|
| 真实 SSH / POSIX / 平台验收 | 代码侧已完成（协议 v2、同一笔预算、事务内 TOCTOU、`argv_sha256`、claim 后 send 前 lifecycle 投影）；真实 SSH / POSIX 停止、机器空闲与平台未验；代码 / 题单 scp 上传是独立部署步骤。⚠️ 停止只覆盖本 job 进程组，不覆盖子进程 `setsid` 逃逸，需 cgroup / namespace 才能更强隔离；Windows 无组枚举判 `unknown` | H3 的「真实远端产物」与 §0.6 的机器空闲不能算完成；group-stop ≠ 整机停止 |
| 真实通道租户 | `doctor/listen/poll`、`--channel-config`、可选 AES 解密均已接；离线 challenge / 文字 / 卡点击可走到认证入账与真实批准 | B9 / D3 / D5 只能记「待验」；真实租户送达、收件与群 @ 未验 |
| 真实机器空闲 | 未验 | 资源租约走调度器，但默认 `idle_verifier` 只证明受管作业停了；远端明确拒启动时保持 quarantine 需人工核 |
| 真实云机 / 平台提交 | 未验 | 仓库只有 AutoDL 查询、事件与参数化 argv；没有提交动作 |
| 完整真实冷启动 | 未验 | 合成冷启动 6 项已过；真实云机 / 平台 / 通道版本未做；V1–V3 只是 3 个合成语义案例 |

以下接线已有源码与本地夹具 / 专项测试证据：① 监控统一为生产唯一意图权威，并在占用事务内复核；② 审批侧用 `execution_core` 绑定完整实际执行规范（`authorization` 已进 `KEY_FIELDS`）；③ 宿主 goal 已从 `goal/change` 持久事件只读重放（`activation` 仍 unknown，coverage 不冒充 full）；④ 通道 `doctor/listen/poll` 与 `decisions --channel-config` 已可用；⑤ 远端执行预算已统一为计划 `authorization` 的同一笔预留，远端 CLI 已存在，协议 v2 停止证据与事务内 TOCTOU 复核已落地；⑥ 自然语言 close 的两路（旧 proof / `--user-quote`）都可用，契约 4.1 已同步。

## 为什么没有逐文件复制旧框架

旧文件有些包含通用机制、目标约束和实例事实三种内容。整份复制会把当前比赛绑进新框架。因此此次按函数、常量、协议、模板和规则段落拆分，并用独立测试检查接口。

旧框架里仍有一些历史工具没有逐字迁入：旧站点推进器、旧全套终点闸、已填满的分析与请求台账、赛事采集脚本、特定模型启动器、官方环境打包器，以及当前渠道的外发命令。对应的通用需求通过新事务内核、治理契约和显式适配接口承接。**这不表示旧工具的所有细节已逐项复刻，也不恢复旧终点闸为必跑条件。**

agent profile 的缺口逐项列在 `profiles/agent-harness/EXTRACTION.md`。例如，依赖真实 gold 补丁的判据、官方沙箱环境和特定评分钩子仍须比赛接入时提供；默认仓库不具备这些资料。

## 机械检查和宿主接线的区别

| 能由本地代码检查 | 仍需真实部署验证 |
|---|---|
| 数据库事务、版本冲突、追加事件 | 远端实际隔离、权限和运行环境 |
| 同请求幂等、结果字节与哈希 | 真实执行器是否支持按 request_key 去重及查询 |
| 计划结构、回归集合、审查版本绑定 | 审查者确实独立、原始宿主读证据真实到达 |
| 用户来源字段及触发节点是否齐全 | 来源事件的真实性与用户权限 |
| 完整指令文本的测量与包含核对 | 当前模型请求确实收到该文本 |
| 事件负责人绑定与模拟投递 | DSH 当前 profile 已加载、对应会话能被唤醒 |
| 模板八节、经验确认版本 | 科学结论是否成立、报告是否易读 |
| 事前 argv 与声明逐项相等、闸拒绝时零子进程 | 被启动的评测器是否真的按声明跑、真实成本多少 |
| 来源哈希过期、前轮未复盘阻断、原子占用 | 同机并行时真实资源冲突与远端机器空闲 |
| 事务内再核计划 / 来源 / 审计 / 监控 / 准入 | 真实宿主在这段时间里是否真的没有变化 |
| 生命周期投影：AI_WORK、收据、快照、报告事件 | 真实远端 run 的投影与 scribe 是否真的写了页 |
| 页面八节/四段、回执字节绑定 | scribe 会话是否真的独立、正文是否写对 |
| 监控报告形状、意见处置、阻断继承 | 独立子会话是否真新起（需宿主证据）、语义核只做过合成案例 |
| 审批页六块与双回执重算 | 通道是否真的把页与卡送达并收到点击 |
| 通道适配层的状态机、验签、幂等与未知不重发 | 真实租户送达、权限、群 @ 与加密事件 |
| SSH 适配器的命令构造、指纹与未知不冒终态 | 真实 SSH / POSIX 停止、真实网络与远端机器状态；进程组停止不覆盖 `setsid` 逃逸，也不是整机停止 |
| 远端协议 v2 停止证据（`stop_verified` + `stop_evidence` + request/token/attempt + `argv_sha256`） | 真实远端是否真的停了；时间戳与 `state=completed` 标签都不算停止 |
| 本地 start / remote claim / 紧邻 send 的同一事务判据与 TOCTOU 负例 | 真实部署中的并发与远端副作用 |
| 远端代码 / 题单上传（scp 到 `unit/code|input`） | 上传是独立部署步骤，本模块不自动做 |
| 进度 spec 与采集比较、租约 fencing | 远端命令的实际输出与失败原因 |
| goal 从 `goal/change` 事件重放 | `activation` 只存在于宿主进程内，coverage 不能冒充 full |
| 受管 job 的 exact 命令 + jobId 绑定（`verify_job`） | watch 是否真的叫醒负责人、作业当前是否存活 |
| 只读服务构造（`DecisionService.readonly` / `ReadonlyStore`） | 只读连接之外的并发写、真实部署的权限与文件锁 |
| 通道加密解密与验签顺序（先验原 body 再解密） | 真实租户的加密推送、权限与群 @ |
| 预算可信时序 / fence、拒绝时零副作用 | 真实宿主日志的跨会话序与时钟 |
| 远端环境指纹（`importlib.metadata` 真实包清单） | 真实远端机器上装了什么、是否与声明一致 |

治理与主链已可调用，且**主链入口真的会调它们**：`workflow.launch` 先核 `audit_state`、`Monitoring.check_start` 与 `Admission.check`，再在 sqlite 写事务里复核计划版本、来源哈希、审计状态、监控状态、执行准入与 before 快照收据，通过后才占用 run 并起子进程；执行落点由 `ExecutionTarget` 解析到专题代码目录与独立 venv，`Lifecycle` 自动写投影与报告事件。但这些检查**不会**自动截获绕过入口的别的程序；README 也没有把「模块存在」当成「真实部署已验证」。

## 空状态与公开检查

新项目以空状态开始。演示和测试只用合成数据。工作区、数据库、日志和部署生成物应放在 `.autoresearch/` 或 `.local/`，由 Git 忽略。不要把整个私人备份或原研究目录复制进来。

本次验收记录只描述执行过的离线检查、合成冷启动 6 项、V1–V3 合成语义核、自然语言 close 的合成宿主核验与少量真实宿主探针；真实宿主完整链路、真实远端、AutoDL 与正式评测环境仍应在完成部署后另留实测记录。selftest 的具体计数以[验收记录](<VERIFICATION.md>)为准，本文不写快照数字。
