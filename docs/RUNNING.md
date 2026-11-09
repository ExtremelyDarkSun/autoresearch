<!--
GENERATED-BY: operations manual migration, 2026-11-09
LAST-EDIT: 2026-11-09 首版：把 109 项验收里的「有实际入口」写成可执行命令，并标出未接线与待验项
PURPOSE: 主入口操作说明 —— 工作区、专题隔离、预算准入、研究执行主链、人读页供料、意图监控、审批入向、进度推进器、问题台账
ORIGIN: kernel/autoresearch/{workspace,budget,workflow,reporting,monitoring,decisions,progress,progress_host,issues}_cli.py 的实际参数
CUMULATIVE-DELTA: 首版。所有命令都取自当前 `--help`；未实测的写"未验"，⛔ 不把待配置写成待写代码。
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

- Python **3.11+**（内核只用标准库）；插件自检需要 Node **20+**。
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
    --plan plans/P1.json --owner-session session-owner
python -X utf8 -B research.py workflow check --workspace ../my-research --topic demo \
    --plan plans/P1.json --owner-session session-owner --intent-none -- python -m pytest -q
```

⚠️ `workflow check/launch` 会**同时**核四样东西：当前审计（要宿主核过的独立性子会话报告）、意图监控（§6）、执行准入（§3 的预算预留 + §7 的决策）、实际 argv（§4）。
缺任何一样都**拒绝启动且不起子进程**——这是有意的 fail-closed，不是待修的 bug。要让闸真的通过，就得把这几件部署件都接上；接不上的部分本文档直接写成「待验 / 在途」。

---

## 1 工作区：`research.py init`

```powershell
python -X utf8 -B research.py init --workspace ../my-research
```

- 建 `AGENTS.md`（完整合并指令）、`.autoresearch/instruction-manifest.json`、`.autoresearch/runtime.sqlite3`（空库，含 `Store`/`Governance`/`Topics`/`Runtime` 表）、`.gitignore`。
- 目标目录必须**不存在**（不会覆盖已有工作区）。
- 输出里给完整材料的字节数与建议预算。**完整指令超过部分 DSH 版本默认 65,536 B 总预算**，必须按输出提高宿主预算并核对注入：

```powershell
python -X utf8 -B research.py instructions --bundle .local/required-instructions.md
python -X utf8 -B research.py instructions --captured 宿主实际请求.txt
```

- 第二条只检查「你给的这段宿主请求」是否含完整正文；它**不**证明宿主真注入了。

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
| `topics/<topic>/{TOPIC.md,OBJECTIVE.md,REQUIREMENTS.md,sources.json}` | 研究主文档骨架 + 原始用户要求（逐字保留） |
| `topics/<topic>/provision.json` | 状态 `provisioning` / `provisioned` / `incomplete`（含 `error`）；⛔ 不静默覆盖 |
| `worktrees/<topic>` | 给了 `--code-repo` 就是真实 `git worktree`（分支 `research/<topic>`）；不给就是**独立空 Git 仓库** |
| `environments/<topic>` | `python -m venv --without-pip` 的独立解释器；`--no-environment` 则不建并在回执里写明 |

⚠️ 它**不装依赖**（`dependencies_installed=false`）、**不跑评测**（`ready_for_evaluation=false`）。专题已登记过或代码/环境落点已存在时直接拒，避免把旧研究接到新代码上。

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
- 尚未终态（没写 `wf_finishes`）的 workflow run 会让 `release` / `settle` **被拒**——防「先释放、再启动」。
- **没有受信计量适配器时 `settle` 只输出 pending 人工核账**，不会假装结算完成。

## 4 研究执行主链：`research.py workflow`

一轮 = 一个 `plan_id`。计划是 `kernel/contracts/plan.schema.json` 的形状，另加两个**顶层**字段：
`execution_argv`（显式 argv，或多组）与 `implementation`（五项 + `files`），以及可选的
`authorization = {scope, series_id, reservation_id, decision_id}`。

```powershell
# 事前材料：四锚点 + 实现五项/files + argv → 冻结来源哈希（专题主文档/目标/用户原话/旧复盘来源）
python -X utf8 -B research.py workflow prepare --workspace ../my-research --topic demo \
    --plan plans/P1.json --owner-session session-owner [--source results/prev.jsonl]

# 只读闸（不写盘、不起子进程）：计划未改 → 材料未过期 → argv 命中 → 前轮已复盘 → 审计/监控/准入
python -X utf8 -B research.py workflow check --workspace ../my-research --topic demo \
    --plan plans/P1.json --owner-session session-owner (--intent-id I1 | --intent-none) -- <argv...>

# 真的启动（同一个闸；通过后 sqlite 事务原子占用唯一 run）→ 退出码原值
python -X utf8 -B research.py workflow launch --workspace ../my-research --topic demo \
    --plan plans/P1.json --owner-session session-owner --intent-none -- <argv...>

# 结束 / 复盘 / 状态
python -X utf8 -B research.py workflow finish --workspace ../my-research --run-id RUN-… \
    --exitcode 3 --reason failed [--documentation-status failed --documentation-detail "页面没更新"]
python -X utf8 -B research.py workflow review --workspace ../my-research --run-id RUN-… --file review.json
python -X utf8 -B research.py workflow status --workspace ../my-research --topic demo
```

判据（都是实测过的拒绝路径）：

| 情况 | 结果 |
|---|---|
| argv 与计划里声明的不逐项相等 | 拒，**零子进程** |
| 来源文件内容变了（按 sha256，不看 mtime） | 「已过期」，先重新 `prepare` |
| 同专题前一轮已结束但没 `review` | 拒开新轮 |
| 同一 `plan_id` 已经跑过 | 拒（不许事后补造事前材料），新轮次要新 plan id |
| 审计 / 监控 / 执行准入任一没接线或不通过 | 拒 |
| 两个进程同时 `launch` | 只有一个占用成功并起子进程 |
| 子进程超 `plan.budget.max_seconds` | 杀掉并记 `reason=timeout`（CLI 退出码 124） |
| 文档写失败 | 只追加事件；**不改**已记录的退出码、**不阻**安全停 |

⚠️ `launch` 的退出码 = 被启动进程的真实退出码（0/3/-9 原样保留）；闸拒绝是 9；输入错误是 2。

## 5 人读页与供料：`research.py reporting`

人读页在 `topics/<topic>/STATUS.md`；机器供料与回执在 `.autoresearch/state/reporting/<topic>/`。

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
- `diagnose` / `status` / `check` 是**只读的**（不改页面、锁、日志、DB）。
- 八节内容与第 3 节四段（假设 → 判据 → 设置 → 结果）由 `governance.validate_status_page` 机械校验；**通过校验不证明结论成立**。

## 6 意图与过程监控：`research.py monitoring`

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
```

- 三触发（结果分析 / 用户更新 / 重大偏差）才派监控；**心跳不是触发**（会被拒）。
- 没有适配器时「会话层」判不了 ⇒ 写命令与 `launch` 闸**不放行**；`--allow-foreign` 只放宽只读诊断。
- `record` 要独立子会话报告：fork / 自造 JSON 一律拒；`close` 只认四条理由 + 仍有效报告 + 全部意见已处置。

## 7 审批与入向：`research.py decisions`

```powershell
python -X utf8 -B research.py decisions --db ../my-research/.autoresearch/runtime.sqlite3 \
    prepare --exp P1 --decision-id D1 --plan 方案集.json [--scope large] [--out .autoresearch/web]
python -X utf8 -B research.py decisions --db … --channel 部署通道:factory send --decision-id D1
python -X utf8 -B research.py decisions --db … status --decision-id D1
python -X utf8 -B research.py decisions --db … overdue
python -X utf8 -B research.py decisions --db … hook [--exp P1]
```

- `prepare` 生成**自包含**审批页（六块：背景 / 上下文 / 进展 / 方案集 / 待决定事项 / 证据索引；每条方案 13 个字段；执行规范绑 `plan_fingerprint`）。
- `send` 是**先页后卡**，且必须有真实通道（`--channel module:factory`）；没有通道 ⇒ 拒绝发送，不假装送达。
- `status` 按台账**重算双回执**：手写 `success` 不被承认（B4/B5）。
- 两小时未明确批准自动取消（`APPROVAL_TIMEOUT_SECONDS = 7200`）；入向文字回复窗口 300 s + 4 s 取整（`overdue` 可查）。
- `hook` 给推进器/主链**单一判词**（零配置可跑）。

## 8 进度采集与受管推进器：`research.py progress`

```powershell
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json probe
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json tick
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json watch --poll 60
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json rearm --note "处理完"
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json outcome --kind advance --action "做了X"
python -X utf8 -B research.py progress --root ../my-research --spec progress.spec.json status
```

- `spec` 是**闭集** JSON（`version/name/owner/state/poll_sec/quiet/round/goal/overdue/collectors/events`）；不认识的键、非 argv 命令、缺 `round.kind` 一律拒。
- `watch` 是受管后台作业本体：**叫醒一次就退出并写 `needs_rearm`**，不设固定寿命（A3/A4/A6）；重挂必须显式 `rearm`。
- `outcome` 是唤醒出口三选一：`advance` / `nothing`（必须给下一条件）/ `problem`（登记新问题）。
- 采集失败 fail-closed：连续失败会形成 `progress_unavailable` 事件；租约带 fencing，第二个推进器拿不到。

⚠️ 已知缺陷（写在这里避免你以为是用法错）：当前 `progress <子命令> --help` 会以 `KeyError: 'default'` 退出 1
（`progress_cli.py` 的 `%(default)s` / `%(choices)s` 帮助占位符与 `argparse.SUPPRESS` 默认值冲突）。
顶层 `progress --help` 正常；子命令参数以本文与源码为准，已报给 progress 侧修。

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
- 它**只读宿主、不改宿主**：不写 goal、不改 profile、不叫醒会话。

## 10 问题台账：`research.py issues`

```powershell
python -X utf8 -B research.py issues --workspace ../my-research record \
    --summary "…" --owner session-owner --scope "单题评测" --exitcode 1 \
    --source results/run.log [--dedupe-key 逻辑名] -- python -m pytest tests -q
python -X utf8 -B research.py issues --workspace ../my-research status [--issue ISSUE-000001]
python -X utf8 -B research.py issues --workspace ../my-research assign --issue ISSUE-000001 \
    --owner session-b --text "换人负责" --source results/run.log
python -X utf8 -B research.py issues --workspace ../my-research fix --issue ISSUE-000001 \
    --text "改了什么" --source results/fix.log
python -X utf8 -B research.py issues --workspace ../my-research verify --issue ISSUE-000001 \
    --text "复验了什么" --source results/run.log --exitcode 0 -- python -m pytest tests -q
python -X utf8 -B research.py issues --workspace ../my-research archive --issue ISSUE-000001
python -X utf8 -B research.py issues --workspace ../my-research index
```

- 同一 `command + scope`（或同一显式 `--dedupe-key`）只算一个 issue；⛔ 不做模糊合并。
- `verify` 必须给**可核 argv + 真实退出码 + 证据**；`passed` 由退出码推出，不一致会被拒。
- 归档只认**这次结案**一次；同名不同内容换名、⛔ 不覆盖；写失败留 `archive_failed` 诊断。
- 台账只证明**完成记录**；命令是否真跑过、是否针对这个错，仍由 agent / 人核。

---

## 11 配置与私有值放哪

| 需要的东西 | 放哪 | ⛔ 不要 |
|---|---|---|
| 工作区状态、页面、结果 | `<工作区>/.autoresearch/`、`<工作区>/topics/` | 不进源码仓库 |
| 临时件、生成物 | `.local/`（已 gitignore） | 不放仓库根 |
| 用户原始要求 | `topics/<topic>/REQUIREMENTS.md`（`workspace topic` 逐字写入） | 不改写成摘要 |
| 宿主日志根 | 由部署方选定（`DSHHost` 默认 `~/.dsh`，**不接受调用方自带路径**） | 不用工作区记录挑证据根 |
| 消息通道 | 部署模块：`decisions --channel module:factory` | 不把凭据写进仓库 |
| 监控宿主适配器 | `monitoring --adapter <file.py|module:attr> --log-root <根>` | 不把日志内容抄进仓库 |
| 进度采集主机/路径 | `progress.spec.json`（含 ssh/fetch argv 与超时） | 不写死在代码里 |
| 宿主联动配置 | `progress-host --deployment deployment.json` | 不用 CLI 临时参数改证据路径 |
| 云机/平台凭据 | 部署方自己的引用（见 [AutoDL 说明](autodl.md)） | 不提交 token/密码 |

凭据只以**引用**形式出现：命令里给的是「哪个部署模块 / 哪份配置」，不是密钥本身。

---

## 12 现在还没接线 / 还没验（别读成已完成）

| 项 | 现状 | 归属 |
|---|---|---|
| 真实消息通道（页 + 卡 + 双回执实际送达） | `decisions` 侧状态机与页面可跑，**通道适配器离线待验** | 通道适配 + 部署 |
| 真实云机 / 平台提交 | 仓库只有后端查询与参数化 argv；**没有**提交动作 | 部署 |
| 意图与监控的**旧双账统一** + 起跑事务内复核 | 进行中 | monitoring |
| `decisions` 绑定**实际 execution_plan**（防 research 子对象丢 argv） | 进行中 | decisions |
| `progress-host` 的 DSH goal 持久事件 / overdue 实际读 | 进行中；**激活（activation）在进程内仍标 unknown** | progress-host |
| 从 clone + 空工作区到分析、scribe、下一轮的**完整冷启动** | 待综合验收 | 父级验收 |
| 单次真实宿主探针 | 已实测：独立子会话 spawn 绑定 / 固定 JSON 原文相等 / scribe 从空工作区写到整页（`ack` 回 `verified=true`、`session_verified=true`、`delivery_verified=false`）、八节校验无错。**这不等于完整真实云实验。** | 已记录，不外推 |

## 13 `research.py demo` 的定位

`demo` 只在**空目录**里合成一次事务生命周期（登记 → 预算预留 → 假执行 → 取回 → 对账 → 结案）。
它证明底层事务与幂等机制能跑通，**不**等于统一科研主链：没有事前四锚点、没有 argv 闸、没有审计/监控/准入三闸、没有真实子进程、没有复盘与页面供料。别拿它当「这套框架已经能用」的证据。

## 14 每条命令的权威参数表

`--help` 优先于本文。本文刻意不抄全量参数，避免两份漂移：

```powershell
python -X utf8 -B research.py <entry> --help
python -X utf8 -B research.py <entry> <subcommand> --help
```

`<entry>` ∈ `workflow · reporting · monitoring · decisions · progress · progress-host · budget · workspace · issues · governance · topics · runtime`。

唯一的例外是第 8 节提到的 `progress` 子命令 `--help`（当前有格式化缺陷）；它的参数请看那里的说明与源码。
