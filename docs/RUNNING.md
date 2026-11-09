<!--
GENERATED-BY: operations manual migration, 2026-11-09
LAST-EDIT: 2026-11-09 本批同步：补 review v2、issues exp、questions、assurance、progress preflight/next-round/schedule 与 D2 回复绑定；通用补项与真实部署分别标
PURPOSE: 主入口操作说明 —— 工作区、专题隔离、预算准入、研究执行主链、人读页供料、意图监控、审批入向、通道、远端、进度推进器、问题台账、检查清单、提问
ORIGIN: kernel/autoresearch/{workspace,budget,workflow,reporting,monitoring,decisions,channels,progress,progress_host,remote,issues,assurance,questions}_cli.py 的实际参数
CUMULATIVE-DELTA: 2026-11-09 操作路径随中文布局标记适配，旧工作区不移动； 2026-11-09 固定分层项目模板与默认初始化，保留显式bundle兼容；2026-11-09 复核版。命令取自当前 `--help`；已验/待现网/在补分别写清，⛔ 不把待配置或在补写成已验。
-->

# 怎么跑：主入口操作说明

> 这份文档回答「**现在有哪些入口、怎么调、它写什么、还缺什么**」。
> 契约正文仍以 `kernel/contracts/` 为准；本文不替它们定义规则。
> 每个入口的**参数权威**是它自己的 `--help`，本文只给最短可用形态。

⚠️ **三条读法**：

1. **本仓库是内核 + 目标配置**。真实模型、云机、消息通道、平台提交都需要**部署配置**，仓库不提供私有凭据。
2. **「有入口」≠「真实部署已验」**。凡需要真实通道 / 真实云机 / 完整冷启动的，本文写「待验」，不当完成。
3. **缺席就是缺席**。若某个动作没有入口，本文直说「尚未接线」；⛔ 不把「让 agent 自己补一段代码」当成首次可用的前提。

---

## 0 前置与最短路径

- Python **3.11+**（内核只用标准库）。飞书**加密事件收件**另需可选 `cryptography`（`python -m pip install -r requirements-feishu.txt`）；插件自检需要 Node **20+**，读取真实 DSH 压缩会话日志的可信核验需要支持 Zstandard 的 Node **24+**。
- 源码仓库与研究工作区**分开**：工作区放数据、状态、页面和结果，不进 Git。
- 最短路径（每步都能单独查，失败会明确报错）：

```powershell
python -X utf8 -B research.py doctor                      # 看边界与环境
python -X utf8 -B research.py init --workspace ../my-research
python -X utf8 -B research.py workspace topic --workspace ../my-research --topic demo \
    --objective "验证一个明确的研究问题" --requirements-file 需求原文.md --owner-session session-owner
python -X utf8 -B research.py budget grant --workspace ../my-research --event-file grant.json \
    --proof-json '{"session_id":"session-owner","seq":12}' --request-key grant-1
python -X utf8 -B research.py budget reserve --workspace ../my-research --series-id S1 \
    --exp-id P1 --scope small --units 3 --request-key res-1
python -X utf8 -B research.py workflow prepare --workspace ../my-research --topic demo \
    --plan AutoDL实验/框架/state/plans/P1.json --owner-session session-owner
python -X utf8 -B research.py workflow check --workspace ../my-research --topic demo \
    --plan AutoDL实验/框架/state/plans/P1.json --owner-session session-owner --intent-none -- python -m pytest -q
```

`plans/P1.json` 从 [生产计划模板](<../kernel/templates/workflow.plan.template.json>) 复制后逐项填真实值（见 §4）。

⚠️ `workflow check/launch` 会**同时**核这些：当前审计（要宿主核过的独立性子会话报告）、用户显式启用后的意图监控（§6）、执行准入（§3 的预算预留 + §7 的决策）、实际 argv（§4）、执行落点（专题已 provision 的 cwd/venv）、before 快照收据与前轮复盘状态。
缺任何一样都**拒绝启动且不起子进程**——这是有意的 fail-closed，不是待修的 bug。要让闸真的通过，就得把这几件部署件都接上；接不上的部分本文档直接写成「待验」。

---

## 1 工作区：`research.py init`

```powershell
python -X utf8 -B research.py init --workspace ../my-research
```

- 默认 `--layout layered`：从[固定项目模板](<../project/AGENTS.md>)复制多级目录、分层入口、共同规则、索引与空台账；安装当前内核契约、指令、角色及所选 profile 副本，重写相对链接。`.autoresearch/layout-manifest.json` 留来源与目标指纹，`runtime.sqlite3` 是空库（Store/Governance/Topics/Runtime）。不复制旧数据或启用监控。
- `python research.py layout-check --workspace ../my-research` 只读检查本地链接与组织路由。它不证明宿主真正注入。
- 旧式单根合并材料须显式选 `--layout bundle`，仍生成 `.autoresearch/instruction-manifest.json`。
- 目标目录必须**不存在**（不会覆盖已有工作区）。
- 分层输出测量各入口实际体积；完整材料另由下列命令测量。全量合并可能超过宿主默认预算，按实际采用的加载方式配置，不能裁切已选中的必读正文：

```powershell
python -X utf8 -B research.py instructions --bundle .local/required-instructions.md
python -X utf8 -B research.py instructions --captured 宿主实际请求.txt
```

- 本轮测量（2026-11-09，**仅测量**）：来源合计 110,747 B，合并 bundle 113,166 B，建议总预算 141,457 B，最大单来源 15,948 B。它不是宿主实际注入证据；文档或 profile 一改就要重新测量。
- 第二条只检查「你给的这段宿主请求」是否含完整正文；它**不**证明宿主真注入了。

新分层工作区的目录由`.autoresearch/workspace-layout.json`确定。下文采用中文默认布局，旧无标记工作区保留根topics等路径。旧机的当前实现与记录迁移见[新电脑恢复](<NEW_MACHINE_RECOVERY.md>)，不能用init覆盖。

## 2 专题隔离：`research.py workspace`

旧版只登记 `Topics`（目录 + 分支字符串）。**现在真的建隔离单元**：

```powershell
python -X utf8 -B research.py workspace topic --workspace ../my-research --topic demo \
    --objective "…" --requirements-file 需求原文.md --owner-session session-owner \
    [--code-repo C:\path\to\repo] [--revision HEAD] [--no-environment]
```

它做四件事，并且**任一步失败都留可诊断现场**：

| 产出 | 说明 |
|---|---|
| `AutoDL实验/实验分析/topics/<topic>/{TOPIC.md,EVIDENCE_<日期>.md,STATUS.md,OBJECTIVE.md,REQUIREMENTS.md,sources.json}` | 从版本模板生成三份骨架 + 原始用户要求（逐字保留）；记录模板指纹，骨架不算报告验收 |
| `AutoDL实验/实验分析/topics/<topic>/provision.json` | 状态 `provisioning` / `provisioned` / `incomplete`（含 `error`）；⛔ 不静默覆盖 |
| `AutoDL实验/实验分析/worktrees/<topic>` | 给了 `--code-repo` 就是真实 `git worktree`（分支 `research/<topic>`）；不给就是**独立空 Git 仓库** |
| `AutoDL实验/实验记录/environments/<topic>` | `python -m venv --without-pip` 的独立解释器；`--no-environment` 则不建并在回执里写明 |

⚠️ 它**不装依赖**（`dependencies_installed=false`）、**不跑评测**（`ready_for_evaluation=false`）。专题已登记过或代码/环境落点已存在时直接拒，避免把旧研究接到新代码上。

已有专题缺文档时，可运行 `python research.py workspace documents --workspace ../my-research --topic demo`。只补缺失类别；已有证据页不再另建空页，现有文件不覆盖。scribe完成有来源的正文后删除SCAFFOLD标记，再走reporting验收。

实验前后各留一次主文档原始快照：

```powershell
python -X utf8 -B research.py workspace snapshot --workspace ../my-research --topic demo \
    --exp-id P1 --stage before   # 之后 after
```

登记与接手仍然用 `topics`（保留）：

```powershell
python -X utf8 -B research.py topics --db ../my-research/.autoresearch/runtime.sqlite3 \
    --commit claim --topic demo --session session-owner --generation 0
```

## 3 共享预算：`research.py budget`

预算账本在 `runtime.accounts.Accounts`（`budget_accounts` / `budget_reservations`），**不是**另造的一套数。

```powershell
# 1) 授权：命令必须与一条真实顶层用户消息逐键相等；只能给宿主可核的 proof
python -X utf8 -B research.py budget grant --workspace ../my-research \
    --event-file grant.json --proof-json '{"session_id":"session-owner","seq":12}' --request-key grant-1

# 2) 预留：在已授权系列下给某轮实验留额度
python -X utf8 -B research.py budget reserve --workspace ../my-research \
    --series-id S1 --exp-id P1 --scope small --units 3 --request-key res-1

# 3) 查看（只读，按系列；可带预留号）
python -X utf8 -B research.py budget status --workspace ../my-research \
    --series-id S1 [--reservation-id R1]

# 4) 释放
python -X utf8 -B research.py budget release --workspace ../my-research \
    --reservation-id R1 --request-key rel-1

# 5) 结算：必须带真实成本证据（文件 + 定位）；没有受信计量适配器时只输出 pending
python -X utf8 -B research.py budget settle --workspace ../my-research \
    --reservation-id R1 --series-id S1 --actual-units 2 \
    --evidence-file results/run.log --locator L1-L9 --request-key set-1
```

- `grant` 核的是**真实用户消息**，不是文件内容；`--event-file` 只要与那条消息逐键不等就被拒。
- 授权时序走 `HostBudgetSource.trusted_ordering`：只返回宿主核过的 session / seq / host_time，`Accounts` 在同一个 `BEGIN IMMEDIATE` 里判新旧；跨会话不拿 seq 当全局序，不可判就 fail-closed。迟到的旧 `renew` 不能复活已被较新 `revoke` 撤回的预算。
- 被拒的 `grant` **零副作用**：实验登记也在 `authorize` 的同一事务里、时序预检之后做，拒绝时整体回滚，不留下先写后拒的实验行或事件。
- 远端执行的预算就是计划 `authorization` 的那笔预留，执行层按同一 scope 复核，**不另造**第二笔 `run` 预留。
- 尚未终态（没写 `wf_finishes`）的 workflow run 会让 `release` / `settle` **被拒**——防「先释放、再启动」。
- **没有受信计量适配器时 `settle` 只输出 pending 人工核账**，不会假装结算完成。

## 4 研究执行主链：`research.py workflow`

一轮 = 一个 `plan_id`。计划的主链形状见 [生产计划模板](<../kernel/templates/workflow.plan.template.json>)：除
`kernel/contracts/plan.schema.json` 的治理字段外，还必须有 `execution_argv`（显式 argv，或多组）、
`implementation`（五项 + `files`）、`execution`（`scope` / `resource_kind` / `cwd`）与
`authorization = {scope, series_id, reservation_id, decision_id}`；`budget` **不预填**，要写本轮真实整数。

⚠️ 通用治理模板 [plan.template.json](<../kernel/templates/plan.template.json>) 只是一份治理结构样例：它没有
argv / 实现 / 执行落点 / 本轮授权，`check-plan` 通过**不能**当成主链可起跑。反过来，生产计划模板未填预算时
`check-plan` 会因占位字符串失败——那是有意的，避免默认预算。

⚠️ `workflow check/launch` 的 CLI 固定用 `ExecutionTarget(require_provision=True)`：专题必须先跑过
`workspace topic`（有 `provision.json`、真实代码目录与独立 venv），否则直接拒；`execution.scope` 必须是
`local`、`resource_kind` 必须是 `cpu`，远端与 GPU 落点一律拒；远端执行走 `research.py remote`（默认关闭，协议 v2 + 同一笔预算预留），不要用本地 `workflow` 冒充远端停机。

```powershell
# 事前材料：四锚点 + 实现五项/files + argv → 冻结来源哈希（专题主文档/目标/用户原话/旧复盘来源）
python -X utf8 -B research.py workflow prepare --workspace ../my-research --topic demo \
    --plan AutoDL实验/框架/state/plans/P1.json --owner-session session-owner [--source results/prev.jsonl]

# 只读闸（不写盘、不起子进程）：计划未改 → 材料未过期 → argv 命中 → 前轮已复盘 → 审计/监控/准入
python -X utf8 -B research.py workflow check --workspace ../my-research --topic demo \
    --plan AutoDL实验/框架/state/plans/P1.json --owner-session session-owner (--intent-id I1 | --intent-none) -- <argv...>

# 真的启动（同一个闸；通过后 sqlite 事务原子占用唯一 run）→ 退出码原值
python -X utf8 -B research.py workflow launch --workspace ../my-research --topic demo \
    --plan AutoDL实验/框架/state/plans/P1.json --owner-session session-owner --intent-none -- <argv...>

# 结束 / 复盘 / 状态
python -X utf8 -B research.py workflow finish --workspace ../my-research --run-id RUN-… \
    --exitcode 3 --reason failed [--documentation-status failed --documentation-detail "页面没更新"]
python -X utf8 -B research.py workflow review --workspace ../my-research --run-id RUN-… --file review.json
python -X utf8 -B research.py workflow status --workspace ../my-research --topic demo
```

新复盘用[版本2模板](<../kernel/templates/workflow.review.template.json>)：除实际结果、分支和证据外，填写下一步判据与来源、费用范围、继续推进的负责人、是否需要批准及原因。公开 `workflow review` 默认要求 `schema_version=2`；显式 `--legacy-record` 可导入旧格式，缺失字段仍为 unknown。下一步写「等待批准」是合法研究交接，不能当作执行已经获批。字段进入 `AI_WORK` 和报告供料，执行前仍另核真实预算与批准。

判据（都是实测过的拒绝路径）：

| 情况 | 结果 |
|---|---|
| argv 与计划里声明的不逐项相等 | 拒，**零子进程** |
| 来源文件内容变了（按 sha256，不看 mtime） | 「已过期」，先重新 `prepare` |
| 专题没有可用的 `provision.json` / 独立代码目录 / venv | 拒（先跑 `workspace topic`） |
| `execution.scope` 不是 `local`，或 `resource_kind` 不是 `cpu` | 拒（本地入口不放行远端 / GPU；远端请用 `research.py remote`） |
| before 主文档快照缺失或与 prepare 记录不符 | 拒（不许事后补造历史快照） |
| 同专题前一轮已结束但没 `review` | 拒开新轮 |
| 同一 `plan_id` 已经跑过 | 拒（不许事后补造事前材料），新轮次要新 plan id |
| 审计 / 监控 / 执行准入任一没接线或不通过 | 拒（生产 CLI 固定接 `DSHHost` / `Monitoring` / `Admission`，没有跳过开关） |
| 闸通过后、占用事务里发现计划/来源/审计/监控/准入变了 | 拒，零子进程（事务内再复核） |
| 两个进程同时 `launch` | 只有一个占用成功并起子进程 |
| 子进程超 `plan.budget.max_seconds` | 杀掉并记 `reason=timeout`（CLI 退出码 124） |
| 文档写失败 | 只追加事件；**不改**已记录的退出码、**不阻**安全停 |

⚠️ `launch` 的退出码 = 被启动进程的真实退出码（0/3/-9 原样保留）；闸拒绝是 9；输入错误是 2。

自动投影：`prepare` / `launch` / `review` 会由 `Lifecycle` 自动写
`AutoDL实验/实验分析/topics/<topic>/plans/<plan>/` 下的 `plan-vN.json`、`AI_WORK.md`、`receipts/*.json` 与前后快照，并把
起跑（`running`）、run / round 终态与复盘结论投影成 `reporting` 事件。**投影失败只记诊断，不改决定与退出码**；
页面是否已验收仍看 §5。

### 远端执行：`research.py remote`

远端走的不是本地 `workflow`，而是受管远端 CLI（部署默认关闭，配置在 `.autoresearch/remote/deployment.json`）：

```powershell
python -X utf8 -B research.py remote doctor --workspace ../my-research
python -X utf8 -B research.py remote check --workspace ../my-research --topic demo --plan AutoDL实验/框架/state/plans/P1.json \
    --owner-session session-owner --intent-none --resource <资源租约键> -- <argv...>
python -X utf8 -B research.py remote provision --workspace ../my-research [--force]
python -X utf8 -B research.py remote start （同 check） -- <argv...>
python -X utf8 -B research.py remote status / query / cancel / collect / release --workspace ../my-research (--run-id …)
```

- 默认 `SSHDeployment(enabled=False)`：没有部署配置时除 `doctor` / `status` 外一律拒绝，**一个远端请求都不发**。
- 执行预算就是计划 `authorization` 的**同一笔**预留（`Runtime(budget_scopes=(scope,))`），不另造第二笔。
- 协议 v2：停止必须有 `stop_verified=True` + 带绑定的 `stop_evidence`，并核 request / token / attempt；`argv_sha256` 对冻结命令；时间戳或 `state=completed` 标签**不算**停止。
- 本地 `start`、远端 `claim` 与紧邻 `send` 用同一套事务内判据；`claim` 提交后、`send` 前调同一 `_lifecycle_call sync`，`running` / reporting / `AI_WORK` 立即生成；`unknown` 也保留投影，投影失败不伪终态、不阻 `cancel`。
- 查询 `unknown` 时预算与资源都保留，不写结束记录。
- ⚠️ **停止边界**：worker 只核本 job 的进程组（TERM→KILL 后重新核 pid / birth），**不覆盖**子进程主动 `setsid` 逃逸；更强隔离要部署 cgroup / namespace。Windows worker 无可靠组枚举 ⇒ `unknown`；不能把 group-stop 当成整台云机停止。
- 代码 / 题单 scp 上传到远端 `unit/code|input` 仍是**独立部署步骤**，本模块不自动上传；真实 SSH / POSIX / 平台未验。

## 5 人读页与供料：`research.py reporting`

人读页在 `AutoDL实验/实验分析/topics/<topic>/STATUS.md`；机器供料与回执在 `.autoresearch/state/reporting/<topic>/`。

```powershell
python -X utf8 -B research.py reporting --workspace ../my-research prepare --topic demo
python -X utf8 -B research.py reporting --workspace ../my-research check --topic demo
# scribe 写完后验收（只有 scribe 能验收；给 --proof-json 才走真实宿主核验）
python -X utf8 -B research.py reporting --workspace ../my-research ack --topic demo \
    --writer-role scribe --writer-session session-scribe \
    [--proof-json '{"session_id":"session-scribe","seq":31}' --owner-session session-owner]
python -X utf8 -B research.py reporting --workspace ../my-research round-check --topic demo --exp P1
python -X utf8 -B research.py reporting --workspace ../my-research diagnose --topic demo
python -X utf8 -B research.py reporting --workspace ../my-research process-status --topic demo
```

- `prepare` 生成**完整供料包**：来源清单 + 来源正文 + 旧页全文 + 缺项补读路径（超内联上限的来源显式标「未内联」并给路径，⛔ 不静默截断）。
- `ack` 不声明 `--proof-json` 时回执只记 `declared`（调用方自述）；给了才可能出现 `verified`。
- `RECEIPT.json` 同时带 `sources` / `source_fingerprint` / `body_sha256`：来源改了或页面字节变了，`check` 就不再算「已验收」。
- `workflow` 的 `Lifecycle` 会自动写 `AI_WORK.md`、收据、`plan-vN.json` 与 before/after 快照，并把起跑 / 终态 / 复盘投影成 `reporting` 事件；`FEED.md` 与供料包仍由 `reporting prepare` 生成。人读页正文仍只由 scribe 会话写，工具不代写。
- `diagnose` / `status` / `check` 是**只读的**（不改页面、锁、日志、DB）。
- 八节内容与第 3 节四段（假设 → 判据 → 设置 → 结果）由 `governance.validate_status_page` 机械校验；**通过校验不证明结论成立**。

## 6 意图与过程监控：`research.py monitoring`

默认关闭，缺开关不要求register、sync、逐条问用户或派监控，历史active/blocked账保留且不拦起跑。只有用户明确要求才按[监控契约](<../kernel/contracts/intent_monitor.md>)启用；下面命令用于已启用监控的项目。只读开关查询用 `python research.py monitoring --root ../my-research enabled`。

```powershell
# 有宿主适配器才可能"可判"：--adapter 指向部署方写法（见 --help），--log-root 是真实证据根
python -X utf8 -B research.py monitoring --root ../my-research --adapter 部署适配器.py --log-root <真实日志根> \
    register --state none --reason "本轮没有跨轮专项意图" --session session-owner
python -X utf8 -B research.py monitoring --root ../my-research --adapter … sync --session session-owner
python -X utf8 -B research.py monitoring --root ../my-research --adapter … \
    event --kind result_analysis --exp P1 --stage evidence
python -X utf8 -B research.py monitoring --root ../my-research --adapter … \
    check --exp P1 --action launch        # 起跑闸：launch 必须过，安全动作豁免
python -X utf8 -B research.py monitoring --root ../my-research status --session session-owner

# close：旧 proof 路径与自然语言原话路径互斥
python -X utf8 -B research.py monitoring --root ../my-research close --session session-owner \
    --reason withdrawn --to closed --report-id R1 --proof-seq 9
python -X utf8 -B research.py monitoring --root ../my-research close --session session-owner \
    --reason withdrawn --to closed --report-id R1 \
    --user-quote '{"session":"session-owner","seq":6,"text":"逐字原话"}'   # 也可 @文件 或 --user-quote-file
```

- 启用后，三触发（结果分析 / 用户更新 / 重大偏差）才派监控；**心跳不是触发**（会被拒）。
- 启用后没有适配器时「会话层」判不了 ⇒ 写命令与 `launch` 闸**不放行**；`--allow-foreign` 只放宽只读诊断。
- `record` 要独立子会话报告：fork / 自造 JSON 一律拒；`close` 只认四条理由 + 仍有效报告 + 全部意见已处置。
- `close` 的自然语言路径已可用：`--user-quote`（内联 JSON 或 `@文件`）/ `--user-quote-file`，格式 `{session,seq,text}`，与旧 `--proof` / `--proof-seq` **互斥**；独立报告必须是 current + 新 spawn，`closure.user_command` 与用户原话同条绑定。`withdrawn` / `replaced` 可用，`achieved` / `unsatisfiable` 不能靠原话绕过。
- `workflow launch` 的意图闸**只认** `Monitoring.check_start`（生产 CLI 固定 `Monitoring(workspace, host=DSHHost())`），旧 gov 意图账不参与生产拦截；占用事务里会再核一次。
- V1–V3 语义核已做过一次：3 个合成案例、新 spawn 独立会话；V1 首次报告提到截止后数据被拒、修订后才通过。它**不是**生产 JSON 端到端。

## 7 审批、入向与通道

```powershell
python -X utf8 -B research.py decisions --db ../my-research/.autoresearch/runtime.sqlite3 \
    prepare --exp P1 --decision-id D1 --plan 方案集.json [--scope large] [--out .autoresearch/web]
python -X utf8 -B research.py decisions --db … --channel 部署通道:factory send --decision-id D1
python -X utf8 -B research.py decisions --db … --channel-config '{"app_id_ref":"env:FEISHU_APP_ID",…}' send --decision-id D1
python -X utf8 -B research.py decisions --db … status --decision-id D1
python -X utf8 -B research.py decisions --db … overdue
python -X utf8 -B research.py decisions --db … authorize-check --exp P1 --owner-session session-owner
python -X utf8 -B research.py decisions --db … hook [--exp P1]
```

- `prepare` 生成**自包含**审批页（六块：背景 / 上下文 / 进展 / 方案集 / 待决定事项 / 证据索引；每条方案 13 个字段；执行规范绑 `execution_fingerprint` 的完整执行规范）。
- `send` 是**先页后卡**，且必须有真实通道（`--channel module:factory` 或 `--channel-config <JSON / @文件>`）；没有通道 ⇒ 拒绝发送，不假装送达。
- `status` 按台账**重算双回执**：手写 `success` 不被承认（B4/B5）。
- `status` / `overdue` / `authorize-check` 走 `DecisionService.readonly`：**构造时不建表、不建目录、不装通道台账**；缺库 / 旧 schema 判「不可判」，不冒充通过。`prepare` / `send` / `hook` / `inbox` / `reply` 仍会写库（`hook` 会 tick 过期决策）。
- 两小时未明确批准自动取消（`APPROVAL_TIMEOUT_SECONDS = 7200`）；入向文字回复窗口 300 s + 4 s 取整（`overdue` 可查）。
- `hook` 给推进器/主链**单一判词**（零配置可跑）。

### 通道：`research.py channels`

```powershell
python -X utf8 -B research.py channels --config channel.json doctor
python -X utf8 -B research.py channels --db ../my-research/.autoresearch/runtime.sqlite3 \
    --config channel.json listen [--host 127.0.0.1] [--port 8080]
python -X utf8 -B research.py channels --db ../my-research/.autoresearch/runtime.sqlite3 \
    --config channel.json poll [--cursor 12] [--limit 50]
```

- `doctor` **只读**：不构造 `FeishuChannel`、不建库 / 目录 / 台账、不联网、不打印凭据值；台账不存在就报「尚未初始化」。
- `listen` 默认只绑 `127.0.0.1`；非回环地址要显式 `--allow-remote`。**没装 `cryptography`（加密事件解密后端）时在起服务前就 exit 2，且不建任何库 / 台账**——配置 Encrypt Key 后飞书会加密推送全部事件（含 challenge），挂一个必然 501 的服务没有意义。
- 加密事件用 `python -m pip install -r requirements-feishu.txt`（只含 `cryptography`）；解密是 AES-256-CBC，解密后仍要把 token / 身份 / 重放全部再核一遍。
- `listen` / `poll` 只把 `handle_webhook` 验签通过的事件交给 `DecisionService.ingest`；验签失败、重放、未订阅类型、`url_verification` 一律不进；核心还会再核一次来源，不存在「自动批准」路径。
- 它不发消息（发送仍走 `decisions send`），也不做守护 / 重试；`poll` 的游标由调用方保存。真实租户送达、群 @ 条件与真实订阅未验。

## 8 进度采集与受管推进器：`research.py progress`

```powershell
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json probe
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json tick
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json watch --poll 60
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json rearm --note "处理完"
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json outcome --kind advance --action "做了X"
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json status

# 下一轮只读预检：全过才给真实 launch 命令；不写盘、不起进程
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json preflight \
    --workspace ../my-research --next-plan plans/P2.json --topic demo \
    --owner-session session-owner --intent-none -- <argv...>
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json next-round \
    --workspace ../my-research --next-plan plans/P2.json --topic demo \
    --owner-session session-owner --intent-none -- <argv...>

# 跨专题排序：候选显式给出；stage 取显式或冻结计划字段
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json schedule \
    --entries schedule.json [--stage-order mechanism,composition,large,paired] [--user-stop]
```

- `spec` 是**闭集** JSON（`version/name/owner/state/poll_sec/quiet/round/goal/overdue/collectors/events`）；不认识的键、非 argv 命令、缺 `round.kind` 一律拒。
- `watch` 是受管后台作业本体：**叫醒一次就退出并写 `needs_rearm`**，不设固定寿命（A3/A4/A6）；重挂必须显式 `rearm`。⛔ 不新增常驻 daemon。
- `outcome` 是唤醒出口三选一：`advance` / `nothing`（必须给下一条件）/ `problem`（登记新问题）。`advance` 在落账前核**当前真实**入向超期（D2）：超期或不可判即拒并指明先回复用户；安全收尾用显式 `--safe-action`，但它不能声明下一轮。
- D2 的超期源在 `preflight` / `next-round` / `schedule` / `outcome advance` 固定读 workspace 库的只读 Inbox（`read_overdue`）；`--overdue-json` / adapter 的 `overdue()` 只作 probe/tick/status 诊断，**静态快照不能当 D2 放行依据**。回复按 `source + event_id` 配对，同号异源不串；未指定 source 的旧回执不算已回复；`event_time/now/grace` 非有限或非正判不可判。公开 CLI 静态空快照不能绕过真实超期记录的本地测试已通过。
- `preflight` / `next-round` **只读**：复用 `Workflow.check` 同判据，返回真实 launch 命令；**不执行、不起进程、不自动开下一轮**，由主会话/宿主执行。真实宿主唤醒与推进激活未验。
- `schedule` 只排序**显式候选**（`--entries` / `--entry-json`），stage 取显式 `--stage` 或冻结计划里的 `research.stage` / `stage` / `execution.stage`；**不扫描生成计划、不猜科学计划**。每个候选只读核审计/监控/准入/预算/资源租约，按阶段与优先级稳定排序；`--user-stop` 时不排序、不给命令。
- 普通提问（我们→用户）默认 `blocking=false`，不算 D2 超期；只有明确 `blocking=true` 且关联本实验的问题才挡依赖它的下一步（见 §10）。
- 采集失败 fail-closed：连续失败会形成 `progress_unavailable` 事件；租约带 fencing，第二个推进器拿不到。
- goal 维度来自 `progress-host` 对 DSH 会话 `goal/change` 事件的只读重放；没有 goal / paused / blocked / complete 可判成 coverage `none`，但 `phase=active` 且没有部署可信模块时 `activation` 不可判 ⇒ coverage 是 `unknown`（不冒充 full / none）。
- 子命令 `--help` 与顶层 `--help` 都正常（此前 `KeyError: 'default'` 的格式化缺陷已修）；参数仍以 `--help` 为准。

## 9 宿主侧联动：`research.py progress-host`

```powershell
python -X utf8 -B research.py progress-host --deployment deployment.json doctor
python -X utf8 -B research.py progress-host --deployment deployment.json goal
python -X utf8 -B research.py progress-host --deployment deployment.json overdue
python -X utf8 -B research.py progress-host --deployment deployment.json plan --poll 60
python -X utf8 -B research.py progress-host --deployment deployment.json \
    register --job-id job-7 [--command "确切启动命令"] [--argv-json '["…","…"]']
python -X utf8 -B research.py progress-host --deployment deployment.json status
```

- `--deployment` 是**固定**配置（`version/name/dsh_home/session_id/workspace/progress_spec/decisions_db/state_root/owner/overdue_grace_sec/trusted_module/host_module` 等闭集键）；证据路径只能来自它，**不听 CLI 临时参数**。
- `plan` 生成受管 `watch` 的确切命令与登记命令；`register` 先核「exact 命令 + jobId」再落 append-only 台账（省略 `--command` 时用 `watch` 写的 pending 标记）；`verify` 只核不登记。
- 超期/回复判定按 `source + event_id` 配对：同号异源不串；未指定 source 的旧回执**不算已回复**；`event_time`、`now`、`grace` 非有限或非正一律判不可判（fail-closed，不记 0 超期）。
- 它**只读宿主、不改宿主**：不写 goal、不改 profile、不叫醒会话。

## 10 问题台账：`research.py issues`

```powershell
python -X utf8 -B research.py issues --workspace ../my-research record \
    --summary "…" --owner session-owner --scope "单题评测" --exp P1 --exitcode 1 \
    --source results/run.log [--dedupe-key 逻辑名] -- python -m pytest tests -q
python -X utf8 -B research.py issues --workspace ../my-research status [--issue ISSUE-000001] [--exp P1]
python -X utf8 -B research.py issues --workspace ../my-research link --issue ISSUE-000001 --exp P2 \
    --text "为什么也算这个实验" --source results/run.log
python -X utf8 -B research.py issues --workspace ../my-research assign --issue ISSUE-000001 \
    --owner session-b --text "换人负责" --source results/run.log
python -X utf8 -B research.py issues --workspace ../my-research fix --issue ISSUE-000001 \
    --text "改了什么" --source results/fix.log
python -X utf8 -B research.py issues --workspace ../my-research verify --issue ISSUE-000001 \
    --text "复验了什么" --source results/run.log --exitcode 0 -- python -m pytest tests -q
python -X utf8 -B research.py issues --workspace ../my-research archive --issue ISSUE-000001
python -X utf8 -B research.py issues --workspace ../my-research index
```

- 同一 `{command, scope, exp_id}`（或同一显式 `--dedupe-key` + scope + exp）只算一个 issue；⛔ 不做模糊合并。`--exp` 进查重键：同一命令在两个实验各崩一次是两个 issue。
- 旧记录没有 `exp_id` 键 ⇒ `exp_link=unknown`，显式不给 ⇒ `unlinked`；`link` 追加 `linked_exp` 事件，不改写旧行。
- `verify` 必须给**可核 argv + 真实退出码 + 证据**；`passed` 由退出码推出，不一致会被拒。
- 归档只认**这次结案**一次；同名不同内容换名、⛔ 不覆盖；写失败留 `archive_failed` 诊断。
- 台账只证明**完成记录**；命令是否真跑过、是否针对这个错，仍由 agent / 人核。

### 检查清单：`research.py assurance`

```powershell
python -X utf8 -B research.py assurance --workspace ../my-research --topic demo --exp P1 \
    --owner-session session-owner checklist
```

- 只读聚合 workflow / monitoring / decisions / issues / questions / reporting / review.continuation，每项给 `ok|blocked|unknown`、`required`、`applicable`、`exp_link`、来源与证据；`unknown` 永不记绿，未关联项列在 `unlinked_items`。
- 检查共16项，含 `workflow.audit`、`budget.reservation`、`remote.lease`。未启动预算直接复用 `Admission.check_budget`，必须匹配冻结计划的账号、预留、作用域、金额与有效期；已启动但实际费用未知时只列诊断，不假结算、不阻安全收尾。远端未启动核声明资源的本地占用/隔离状态，不要求提前 claim；启动后核实际绑定，结束后仍持有/隔离保持 unknown。检查不签发批准、不占资源，执行时仍走原事务闸。
- 它不写文件、不建库、不执行任何动作；语义结论仍由人/主会话判断。

### 普通提问：`research.py questions`

```powershell
python -X utf8 -B research.py questions --workspace ../my-research ask \
    --exp P1 --body "这个方向要不要继续？" --owner session-owner [--blocking]
python -X utf8 -B research.py questions --workspace ../my-research open [--exp P1]
python -X utf8 -B research.py questions --workspace ../my-research answer Q-000001 \
    --source feishu --event-id <入向事件> --channel-config channel.json
python -X utf8 -B research.py questions --workspace ../my-research declare Q-000001 \
    --body "用户口头说继续" --actor session-owner
```

- 普通提问独立于审批：不生成审批页、不发选择卡、不消费短码、不自动批准执行。
- 答复只认 Inbox 已认证内容中明确指向本问题的编号（如 `[question:Q-000001]`）或已送达消息线程；事件时间须有限且不早于发问。无编号、错编号或多个编号歧义均拒。`declare` 只记声明，不改状态。
- 外发先 `ask --recipient <收件人>`，再 `questions enqueue Q-000001` 排入现有 Delivery 队列，由部署发送端实际发送；入队不代表送达。正文带可复制的问题编号，便于用户回复。
- 默认 `blocking=false`：问用户不等于实验停等；只有明确依赖答复才 `--blocking`。
- 等待用户答复**不是** D2（D2 是用户→我们超期）；问题超期只进诊断，除非 `blocking=true` 且关联本实验。真实租户与真实用户回复未验。

---

## 11 配置与私有值放哪

| 需要的东西 | 放哪 | ⛔ 不要 |
|---|---|---|
| 工作区状态、页面、结果 | `<工作区>/.autoresearch/`、`<工作区>/AutoDL实验/实验分析/topics/` | 不进源码仓库 |
| 临时件、生成物 | `.local/`（已 gitignore） | 不放仓库根 |
| 用户原始要求 | `AutoDL实验/实验分析/topics/<topic>/REQUIREMENTS.md`（`workspace topic` 逐字写入） | 不改写成摘要 |
| 宿主日志根 | 由部署方选定（`DSHHost` 默认 `~/.dsh`，**不接受调用方自带路径**） | 不用工作区记录挑证据根 |
| 消息通道 | 部署配置：`decisions --channel module:factory` 或 `--channel-config <JSON / @文件>`；收件用 `channels --config … listen/poll` | 不把凭据写进仓库 |
| 监控宿主适配器 | `monitoring --adapter <file.py|module:attr> --log-root <根>` | 不把日志内容抄进仓库 |
| 进度采集主机/路径 | `progress.spec.json`（含 ssh/fetch argv 与超时） | 不写死在代码里 |
| 宿主联动配置 | `progress-host --deployment deployment.json` | 不用 CLI 临时参数改证据路径 |
| 云机/平台凭据 | 部署方自己的引用（见 [AutoDL 说明](autodl.md)） | 不提交 token/密码 |

凭据只以**引用**形式出现：命令里给的是「哪个部署模块 / 哪份配置」，不是密钥本身。

---

## 12 还没验 / 待现场验收（别读成已完成）

| 项 | 现状 | 归属 |
|---|---|---|
| 真实消息通道租户送达与收件 | `channels doctor/listen/poll` 与 `decisions --channel-config` 已提供；`doctor` 只读，`listen` 缺 `cryptography` 时起服务前 exit 2 且不建库；加密 challenge / 文字 / 卡点击可离线走到认证入账与真实 `DecisionService` 批准。**真实租户**上传 / 送达 / 收件 / 群 @ 条件未验 | 通道适配 + 部署 |
| 真实 SSH / POSIX / 平台验收 | 受管远端代码侧已完成：同一笔 `authorization` 预算、协议 v2（`stop_verified` + `stop_evidence` + request/token/attempt 绑定 + `argv_sha256` 核冻结命令）、本地 `start` / remote `claim` / 紧邻 `send` 同一事务判据与 TOCTOU 负例零 adapter 调用；查询不可判时保预算/资源，`release` 核 attempt/lease/request。**真实 SSH / POSIX 停止、机器空闲与平台未验**；代码 / 题单 scp 上传到 `unit/code|input` 是独立部署步骤（模块不自动上传） | 远端 + 部署 |
| 真实 POSIX 停止 | 远端 worker 的进程组停止 / 硬超时只在 POSIX 上验证；Windows worker 无可靠组枚举 ⇒ `unknown`，Windows 本机安全用例是假 process + 真跨进程锁，真实 POSIX 仍 11 项 skip。⚠️ **只覆盖本 job 进程组**：子进程主动 `setsid` 逃逸不在内，更强隔离要部署 cgroup / namespace；group-stop ≠ 整台云机停止 | 远端 + 部署 |
| 真实云机 / 平台提交 | 仓库只有 AutoDL 查询、事件与参数化 argv；**没有**提交动作 | 部署 |
| 真实机器空闲 | 资源租约走调度器；默认 `idle_verifier` 只证明受管作业停了，不证明整台机器空闲；远端明确拒启动时保持 quarantine，需人工核 | 远端 + 部署 |
| `progress-host` 的 goal `activation` | `goal/change` 持久事件已实读并可重放；**activation 进程内仍标 unknown**（宿主不落盘），coverage 不能冒充 full | progress-host |
| 受管 job 的来源绑定 / 叫醒 | `verify_job` 已用真实宿主持久日志核过一次精确命令 + jobId 绑定（`verified=true`）；**watch 是否真的叫醒负责人、作业是否仍存活**未验 | progress-host + 部署 |
| 完整冷启动（真实版） | 合成冷启动 6 项已过：同一空工作区、公开 init、Git/venv、真实 Monitoring、Accounts/Admission、DSHHost 解析合成日志、真实子进程、复盘、快照/报告投影、scribe 字节回演 `ack verified`；第二轮同一 `series-small` 另预留 3、真实运行后 `reserved_units=6`/`spent_units=0`、两笔 unknown 成本保持 `reserved`，第三轮因未复盘被拒。真实云机 / 平台 / 通道版本未做 | 父级验收 |
| V1–V3 语义验收 | 3 个合成案例、新 spawn 独立会话：V2 识别 7/10 写成 9/10 并阻断、V3 合理探索放行；V1 **修订后**通过。不是生产 Monitoring JSON 端到端 | 父级验收 + monitoring |
| 真实远端资源状态 | `assurance` / `progress` 预检只读本地台账；已启动租约与终态仍需 `remote query/release` 及真实机器现场核验 | 受管远端部署 |
| progress 超期源 | `preflight` / `next-round` / `schedule` / `outcome advance` 已固定接 workspace 只读 Inbox（`read_overdue`）；`--overdue-json`/adapter 只作诊断。CLI 本地组合已验，真实通道入向与宿主激活未验 | progress + 部署 |
| 真实用户输入注入 / goal 激活 | 当前模型请求是否真收到完整指令、宿主 goal 的 activation 是否真在推进，均未验；不凭配置生成宣称已注入或已激活 | 部署 |
| AutoDL / 比赛现场 | 仓库只有查询、事件与参数化 argv；真实 AutoDL、正式评分器与平台提交未验 | 部署 |
| 真实宿主探针 | 已实测：独立子会话 spawn 绑定 / 固定 JSON 原文相等 / scribe 从空工作区写到整页（`ack` 回 `verified=true`、`session_verified=true`、`delivery_verified=false`）、八节校验无错。**这不等于完整真实云实验。** | 已记录，不外推 |

## 13 `research.py demo` 的定位

`demo` 只在**空目录**里合成一次事务生命周期（登记 → 预算预留 → 假执行 → 取回 → 对账 → 结案）。
它证明底层事务与幂等机制能跑通，**不**等于统一科研主链：没有事前四锚点、没有 argv 闸、没有审计/监控/准入三闸、没有真实子进程、没有复盘与页面供料。别拿它当「这套框架已经能用」的证据。

## 14 每条命令的权威参数表

`--help` 优先于本文。本文刻意不抄全量参数，避免两份漂移：

```powershell
python -X utf8 -B research.py <entry> --help
python -X utf8 -B research.py <entry> <subcommand> --help
```

`<entry>` ∈ `workflow · reporting · monitoring · decisions · channels · progress · progress-host · remote · budget · workspace · issues · assurance · questions · governance · topics · runtime`。
