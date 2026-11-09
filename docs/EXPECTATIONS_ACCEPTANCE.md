<!--
GENERATED-BY: full expectations migration audit
LAST-EDIT: 2026-11-09 主链落位重审：把「没有模块」的差额改成「有实际入口 / 接线在途 / 真实部署未验」，109 编号与顺序全部保留
PURPOSE: 保留全部预期编号并作为迁移功能验收依据。
ORIGIN: upstream FRAMEWORK_EXPECTATIONS_2026-10-10.md, current working content
CUMULATIVE-DELTA: 2026-11-09 依据当前源码重写逐项状态：workflow/reporting/monitoring/decisions/progress/progress_host/issues/admission/workspace/host_evidence
已是可调用入口（含 CLI 与专项测试）；新增「接线在途」状态；不恢复旧终点闸；不把局部探针写成完整真实云实验。
-->
# 按行为预期检查迁移完整性

**结论：迁移仍未完成，但「缺模块」的差额已基本不存在。** 现在仓库里有**可调用的主入口**：工作区初始化、专题真实隔离、共享预算、研究执行主链、人读页供料与验收、意图与过程监控、审批与入向、进度采集与受管推进器、宿主侧只读联动、问题台账。剩下的差额分三类：

1. **接线在途**（同一要求里还有一条生产路径没接完，见下）；
2. **真实部署未验**（需要真通道、真云机、完整冷启动——仓库不提供私有凭据与机器）；
3. **语义纪律**（结论对不对、页面是否易读、审查是否有质量，机械检查证明不了）。

操作命令见[怎么跑](<RUNNING.md>)；拆分落点见[迁移说明](<migration.md>)。验收依据是[中性化完整预期](<../kernel/contracts/framework_expectations.md>)，本文逐项保留 109 个编号与其顺序。

源工作文件 SHA256：`dd937baa9254c956613ad9bff80132682140933c54493adce376c798b2623e35`。编号覆盖不代表功能覆盖；本轮重审覆盖 109 个编号，未漏号、未重号。

## 如何读状态

- **本地实现**：有可调用入口（CLI 子命令或库函数）+ 落库产物 + 专项测试支持。只证明该机制，不证明真实宿主部署或完整流程。
- **部分**：同一要求里还有一段没接完（例如判定有、送达未验）。
- **接线在途**：已识别到具体缺口与归属，改动正在并行进行；⛔ 不数完成。
- **缺失**：未找到该要求的可用实现。
- **仅规则 / 语义纪律**：前者是可实现但当前只有契约；后者需 agent 或人判断语义，不能用字段齐全证明。
- **宿主相关 / 未联调 / 未验收**：需要真实宿主、通道或独立会话证据；不计为通过。
- **已退役 / 历史记录**：保留源编号作追溯，不恢复旧私有状态，也不拿历史结论当当前验收。

本轮判断包含读源码、专项测试、跨服务冷启动与完整 `selftest --plugins`：运行时 272 项通过，其他 unittest 741 项中 730 项通过、11 项 POSIX 远端用例在 Windows 跳过，三个插件 221 条断言通过。冷启动 5 项和真实宿主探针分别记录；测试数量不代替功能覆盖。详见[验收记录](<VERIFICATION.md>)。

## 源码证据入口

| 简称 | 源码与当前能力 | 不能据此宣称什么 |
|---|---|---|
| G | [治理](<../kernel/autoresearch/governance.py>)、[审计状态](<../kernel/autoresearch/audit_state.py>)、[文件绑定](<../kernel/autoresearch/audit_support.py>) | 审查独立性、豁免、关闭策略只认宿主核过的证据；`declared` 不等于 verified |
| W | [研究执行主链](<../kernel/autoresearch/workflow.py>)、[CLI](<../kernel/autoresearch/workflow_cli.py>) | 闸绿只说明登记走了；不证明假设成立，也不证明真实装置成本 |
| R | [人读页供料与验收](<../kernel/autoresearch/reporting.py>)、[CLI](<../kernel/autoresearch/reporting_cli.py>) | 页面八节/四段可校验；结论易读性与科学性仍靠写页者与人 |
| N | [意图与过程监控](<../kernel/autoresearch/monitoring.py>)、[CLI](<../kernel/autoresearch/monitoring_cli.py>) | 没有宿主适配器时会话层不可判；报告形状合格不证明分析正确 |
| D | [审批与入向](<../kernel/autoresearch/decisions.py>)、[CLI](<../kernel/autoresearch/decisions_cli.py>) | 双回执按台账重算；真实送达要有通道，缺通道就拒发 |
| P | [进度采集与推进器](<../kernel/autoresearch/progress.py>)、[宿主联动](<../kernel/autoresearch/progress_host.py>) | spec 与租约可核；远端命令的真实输出与宿主 goal 语义未验 |
| I | [问题台账](<../kernel/autoresearch/issues.py>)、[CLI](<../kernel/autoresearch/issues_cli.py>) | 台账只证明完成记录；命令是否真跑过仍靠 agent / 人核 |
| A | [执行准入](<../kernel/autoresearch/admission.py>) | 复用 `Accounts` 预算账本；`large` / `submission` 要真实 approved 决策 |
| V | [宿主证据](<../kernel/autoresearch/host_evidence.py>)、[专题隔离](<../kernel/autoresearch/workspace.py>)、[Runtime](<../kernel/autoresearch/runtime/service.py>) | 单次探针不等于完整真实链路；隔离单元建好不等于依赖已装、评测已通 |
| T | [专题协调](<../kernel/autoresearch/runtime/topics.py>)、[专题 CLI](<../kernel/autoresearch/runtime/topic_cli.py>) | 登记目录与分支不等于创建 worktree / 独立环境（那一步在 `workspace.py`） |
| C | [统一 CLI](<../kernel/autoresearch/cli.py>)、[治理 CLI](<../kernel/autoresearch/governance_cli.py>) | `init` 只建指令与空库；子命令转发不等于端到端可用 |

关键调用点（读源码）：`workflow.launch` 在**同一个写事务**里复核计划版本与执行准入，通过后才占用 `wf_runs` 并起子进程；`audit_state` / `check_start` / 注入的监控与准入任一不过就拒，且零子进程。`reporting.ack` 只有带 `--proof-json` 才可能出现 `verified`，否则只记 `declared`。`decisions.status` 按台账重算双回执，手写 `success` 不被承认。`issues` 的每次写都在 `BEGIN IMMEDIATE` 里重算哈希链。`admission` 读的是 `budget_accounts` / `budget_reservations`，**没有**第二套预算台账。

## 逐项验收

编号与目标正文逐条对应。缺口栏同时说明现有局部能力；表格记录本轮读源码结果，不是虚构生产实测。

| 编号 | 状态 | 实际能力与缺口 |
|---|---|---|
| A1 | 部分 | `progress tick/watch/rearm/outcome` 已能跑一轮并在需要时唤醒；「一轮结束后自动开下一轮」仍要靠负责人显式 `rearm`，没有常驻循环。 |
| A2 | 本地实现 | `progress status` 显示阻塞项与恢复命令；缺推进来源时写 `progress_unavailable` 事件，并给出 `rearm` 动作。 |
| A3 | 本地实现 | `watch` 不设固定寿命：叫醒一次就退出并写 `needs_rearm`，不会到点静默退出。 |
| A4 | 部分 | 静默窗口（`spec.quiet.window_sec`）与唤醒事件已实现；「受管作业真的叫醒负责人」要宿主登记（`progress-host register`），宿主回传未验。 |
| A5 | 部分 | `overdue` 维度优先于静默窗口与 goal 抑制；数据来自 `decisions` 台账的只读视图，真实入向通道未验。 |
| A6 | 本地实现 | 唤醒后必须 `outcome` 三选一（推进 / 无事+下一条件 / 登记新问题），并显式 `rearm`；每次唤醒与重挂都追加留痕。 |
| A7 | 宿主相关 | `progress-host register` 先核 exact 命令 + jobId 再落 append-only 台账；宿主真实退出码语义仍需实测。 |
| B1 | 本地实现 | `decisions send` 先页后卡；缺通道直接拒发，不假装送达。 |
| B2 | 本地实现 | `prepare` 的 `readiness` / `page_problems` 在缺背景、上下文或进展时拒绝生成审批页。 |
| B3 | 本地实现 | 自包含 HTML：六块必填、每条方案 13 字段、执行规范绑 `plan_fingerprint`，且禁止外部依赖。 |
| B4 | 本地实现 | `status` 按台账重算：只有卡、没有同决策的页回执 ⇒ 不承认这条审批链。 |
| B5 | 本地实现 | 同上：只有页、没有卡回执 ⇒ 不承认。 |
| B6 | 本地实现 | `reply` 生成带实验编号的文本，编号与决策绑定；真实通道未验。 |
| B7 | 本地实现 | `inbox` 落真实入向事件并校验事件、实验与收件人一致。 |
| B8 | 部分 | 上下文页可作附件发送并留回执；**真实上传与送达**未验（通道在途）。 |
| B9 | 宿主相关 | 附件能力只能用实际上传与送达结果验证；仓库不带通道客户端。 |
| B10 | 本地实现 | 两小时未明确批准自动取消（`APPROVAL_TIMEOUT_SECONDS = 7200`），不消费短码、不自动采纳推荐项。 |
| C1 | 已退役 | 专门联络子会话已取消，不恢复为必做。 |
| C2 | 已退役 | 专门联络子会话已取消。 |
| C3 | 已退役 | 原角色已取消，历史探针不外推。 |
| C4 | 已退役 | 专门联络子会话已取消。 |
| C5 | 已退役 | 专门联络子会话已取消。 |
| C6 | 已退役 | 专门联络子会话已取消。 |
| D1 | 部分 | `decisions overdue` 给 300 s + 4 s 的回复窗口诊断，并可按事件定位；「自动回复」不是本层职责。 |
| D2 | 部分 | 超期入向会让 `progress` 的 `overdue` 维度优先触发唤醒；「持续推进拒绝假绿」在 `hook` 侧只有判词，缺统一入口强制。 |
| D3 | 部分 | `inbox` 台账 + 受管 `watch` 能承载投递；真实通道订阅与叫醒未验。 |
| D4 | 本地实现 | 卡点击按执行/拒绝处理并留回执，不再排队等文本回复（`CARD_CLICK_INTENTS`）。 |
| D5 | 宿主相关 | 群聊触达条件（@、白名单等）属通道适配；须部署实测，仓库不提供客户端。 |
| E1 | 部分 | 页面更新、整轮回执、原始证据保存都有入口（`reporting`）；「聊天结论可单独理解」仍是语义判断。 |
| E2 | 部分 | 运行事件与实验号关联；证据检查的**统一台账汇总**仍未收口。 |
| E3 | 部分 | 计划要求 `baseline`，比较检查同题同条件；缺旧阶段统一入口（旧闸不恢复）。 |
| E4 | 部分 | 事件追加与 `process-status` 投影可用；「当前必需检查清单」尚无统一汇总视图。 |
| E5 | 部分 | `progress outcome` 要求写明下一动作与下一条件，`reporting round-check` 要求整轮终态与负责人判断；成本作用域与「是否需批准」缺统一字段。 |
| E6 | 仅规则 | 计划契约含研究设计要求；「从判据反推要改变的量」没有强制材料。 |
| E7 | 部分 | `reporting input` 登记用户要求与整轮判断，`decisions inbox` 关联回复；主动提问仍缺统一落盘入口。 |
| E8 | 部分 | `decisions prepare` 有 `needs_user` 与 `hook` 判词；显式表态的语义仍靠人或 agent。 |
| F1 | 语义纪律 | 已有正式审批链入口；「不许只在会话界面提问」仍要人/agent 遵守。 |
| F2 | 语义纪律 | 编号校验可做，内容归属仍须读内容。 |
| F3 | 语义纪律 | 答复是否基于原始产物，字符串检查证明不了。 |
| F4 | 语义纪律 | 六块存在不证明人能看懂。 |
| F5 | 部分 | `progress-host register` 核 exact 命令 + jobId 后登记受管作业；宿主是否真的能叫醒会话仍未验。 |
| F6 | 已退役 | 联络角色取消，不恢复为必做。 |
| F8 | 部分 | 完整 bundle 与捕获文本比对可用；「宿主实际读了哪几段」仍无可信宿主读事件。 |
| F7 | 语义纪律 | 没有全会话提问扫描器；靠人/agent 自查。 |
| F9 | 语义纪律 | 可信身份与语义整合仍依赖部署与独立核对。 |
| G1 | 历史记录 | 仅保留编号；不复制历史问题状态，现行范围见 B 组。 |
| G2 | 历史记录 | 旧实测状态不转移；新部署仍须独立端到端验收。 |
| G3 | 历史记录 | 具体比赛提交资料不迁移；通用产物完整性不能冒充官方提交对账。 |
| G4 | 历史记录 | 不复制旧台账；通用要求见 E4 与 §9（问题台账已实现）。 |
| G5 | 历史记录 | 不补造历史页；新审批页由 `decisions prepare` 生成。 |
| G6 | 历史记录 | 真实历史消息不公开迁入，新入向按 D 组验收。 |
| H1 | 部分 | `progress-host goal` 只读重放 DSH goal，`progress` 有 goal 维度；「由宿主 goal 主导推进」的事件持久化在路上。 |
| H2 | 接线在途 | 推进器有租约 fencing，第二个拿不到；「与 goal 时段不重叠」依赖 goal 实读，正在统一。 |
| H3 | 部分 | `spec.collectors` 可跑远端 argv 并解析输出，不只看本地 mtime；真实远端产物未验。 |
| H4 | 本地实现 | `wake` 事件携带变化字段与原始事件引用。 |
| H5 | 本地实现 | 声明式闭集 spec：命令 / 文件 / 目录采集器 + 事件比较，主机与比赛语义都在配置里。 |
| H6 | 本地实现 | 采集失败 fail-closed；连续失败形成可定位的 `progress_unavailable` 事件并触发唤醒。 |
| H7 | 本地实现 | `outcome` 三出口强制三选一：推进 / 无事 + 下一条件 / 登记新问题。 |
| H8 | 本地实现 | 连续无事按 `idle_streak` 加倍静默窗口，并有 `max_window_sec` 上限。 |
| H9 | 本地实现 | 直令轮的识别来自部署配置（`progress` 侧按配置判），不误报「仍需规划派工」。 |
| I1 | 部分 | 页面正文按 `body_sha256` 与来源版本、验收回执绑定，心跳与 mtime 不替代；「来源版本清单」与页面摘要的统一回执仍在收口。 |
| I2 | 本地实现 | `reporting prepare` 生成完整供料包：来源清单 + 正文 + 旧页全文；超限显式标「未内联」并给补读路径。 |
| I3 | 部分 | 八节 + 第 3 节「假设 → 判据 → 设置 → 结果」与最多 3 轮可校验；「计划与启动状态」的关联仍靠事件记录。 |
| I4 | 本地实现 | `round-check` 要求整轮终态、负责人判断与当前页回执；单臂结果不能冒充整轮。 |
| I5 | 本地实现 | `diagnose` / `status` / `check` 只读：不改页面、锁、日志、DB；证据不足写「待核」。 |
| I6 | 本地实现 | `diagnose` 按 checkpoint 龄期与实际存活判定，过期 checkpoint 不能冒充实时运行。 |
| I7 | 本地实现 | `workflow finish` 记真实原因与退出码；文档失败只追加事件、不改退出码、不阻安全停；报告待更新由 `status` 队列给出。 |
| I8 | 本地实现 | 机器流程状态写 `.autoresearch/state/reporting/<专题>/` 与 `process_status/`，与人读页分开；心跳不算研究进展。 |
| I9 | 本地实现 | 八节内容与四段来源由 `validate_status_page` 校验；页面是否真的易读仍靠写页者与人。 |
| I10 | 本地实现 | 旧流程字段不再是新页必填，旧页按 `legacy` 只读兼容。 |
| J1 | 本地实现 | `workflow prepare` 核四锚点（假设/判据出处/实现五项+files/显式 argv），确认性要阈值+理由，探索性要停止条件，占位词一律拒。 |
| J2 | 本地实现 | 来源按 sha256 判过期（专题目标 / 主文档 / 用户原话 / 旧复盘来源）；只 `touch` 不改内容不失效。 |
| J3 | 本地实现 | `check` 与 `launch` 都用实际 argv 与事前声明逐项比对；不符时零子进程。 |
| J4 | 本地实现 | 真实启动记录（`wf_runs`）+ 有证据的复盘（`wf_reviews`）必须存在；旧终点闸不恢复为必跑。 |
| J5 | 本地实现 | 同专题前轮已结束但未复盘 ⇒ 拒开新轮；被否定假设重试必须在计划里写新条件与来源。 |
| J6 | 本地实现 | 同一 `plan_id` 已启动过 ⇒ 拒再 prepare / 再启动（不许事后补造）；新轮次用新 plan id。 |
| J7 | 本地实现 | `reporting check` 是页面验收入口：查研究八节、每块四段与来源；不是只有函数。 |
| J8 | 部分 | `reporting ack --writer-role scribe --writer-session` 才验收；带 `--proof-json` 走 `DSHHost` 核派工与写入。已做一次真实宿主探针（`verified=true`、八节无错），完整链路未验。 |
| K11 | 本地实现 | `legacy_state` / `legacy_exempt` 只认真实过去的 `wf_runs`，且明写「历史豁免 ≠ 审过」。 |
| K1 | 本地实现 | 默认必审；`workflow.launch` 在没有当前审计时拒绝起跑（统一入口已接线）。 |
| K2 | 本地实现 | 关闭审计要宿主核过的用户命令，且必须晚于最后一次 `prepare_audit`；关闭后不能再准备。 |
| K3 | 本地实现 | 只接受 `{session_id, seq}` 的 proof，命令由本层构造并逐键比对；`DSHHost` 读真实日志，48 条绑定正例。真实通道未验。 |
| K4 | 本地实现 | 审计者≠负责人，且必须是宿主证明的新 spawn 子会话；fork / 继承被判拒。 |
| K5 | 本地实现 | `coverage` 恰好七项，各有合法 verdict、说明与非空来源。 |
| K6 | 本地实现 | `unknown` 不再隐式通过：按 K13 写清为何阻断或不阻断。`blocked` 必须带 blocking 问题。 |
| K7 | 本地实现 | 每条意见必须有 accepted / declined / needs_user 及理由来源；小修走 `correct`，硬实质不许授权。 |
| K8 | 本地实现 | 阻断只由后续报告的 `resolves` 或宿主核过的用户 `waive` 关闭；`correct` 不能关阻断。 |
| K9 | 本地实现 | `authorization` 已进入 `KEY_FIELDS`，与执行参数、实现和预算共同绑定版本；任意授权叶子不能当小修。完整计划另由启动主链按规范化内容复核。 |
| K10 | 本地实现 | 审计超时 / 失败 / 损坏 / 不可判都不放行；`workflow` 侧遇不可判直接拒。 |
| K12 | 本地实现 | 每实验默认首审 + 最多一次增量复审；扩额要宿主核过的用户 `extend`，不能自扩。 |
| K13 | 本地实现 | 三态 `proceed / minor_fix / blocked` + 三项审查重点 + 阻断/非阻断理由。 |
| K14 | 本地实现 | 完整报告落文件、短回传恰六键；`record_audit_file` 核字节 SHA256 与声明一致。真实宿主消息可见性另验。 |
| L1 | 部分 | 没登记意图是**显式**状态，未登记会话与未评估原话都拦；旧 gov 双账已从生产启动链移除，统一使用 Monitoring 权威记录，并在占用事务内复核。 |
| L2 | 部分 | 四态与替换 API 有；用户新原话驱动状态变更的自动接入未收口。 |
| L3 | 部分 | `monitoring prepare` 固定截止快照与派工材料，`record` 核 `evidence_reads`；宿主层「先读原话再读结果」的读序核验未端到端。 |
| L4 | 部分 | 报告要求逐轮 trajectory 字段；跨轮偏移定位未做独立验收。 |
| L5 | 本地实现 | `monitoring respond` 逐条处置意见（理由 + 来源），declined 不消除阻断。 |
| L6 | 本地实现 | 仅三类事件触发；心跳会被拒；`prepare` 只在确有待审事项时可用。 |
| L7 | 部分 | 报告绑定 `owner_session`、截止 seq 与证据版本；「新结果哈希失效」的跨报告继承仍在收口。 |
| L8 | 本地实现 | 单轮失败仍可 active；`blocked` 与 `closed` 分开；退出只认四条理由。 |
| L9 | 本地实现 | 完整监控 JSON 校验（prepared_id / intent / trajectory / findings / issues / next_direction / closure / covers / evidence_reads / direction_sha256），并逐字核用户引文。 |
| L10 | 部分 | 安全动作（collect / stop / package / rollback / halt / checkpoint）豁免；缺监控不阻停机已在 `check` 侧体现，端到端未验。 |
| L11 | 语义纪律 | 新证据支持且仍连着目标的合理换方向可推进；判断靠人/agent。 |
| L12 | 语义纪律 | 证据是否支持解释由独立会话判；本层不代替。 |
| L13 | 本地实现 | `monitoring check --action launch` 是起跑闸：遗漏 / 过期 / 未处置意见 / hook 缺口都拦；安全动作豁免。生产启动链已统一权威监控，并在真实起跑前再次核状态。 |
| L14 | 本地实现 | 报告建议闭合 ≠ 已闭合；只有带可信证据的 `close` 改变状态。 |
| V1 | 未验收 | 需要中性合成回放 + 真实独立会话；本轮只有单次真实宿主探针，不含历史回放。 |
| V2 | 未验收 | 需要完整监控报告与真实宿主对同一轮做对抗性核对；未做。 |
| V3 | 未验收 | 需要合理探索的负例（意见为空、证据完整、允许继续）；未做。 |

## 无编号要求的迁移差额

| 范围 | 当前状态与缺口 |
|---|---|
| §0 | 事前材料 → 起跑 → 结束 → 分析 → 报告 → 下一步**已有入口**（`workflow` + `reporting` + `progress`）；从 clone 到**完整冷启动**的综合验收未做。 |
| §0.5 | 有 `declared` / `verified` 两级与纪律边界；「代码 / agent / 人三层自动」的自进化闭环仍只有局部工具（问题台账 + 文档冷件 + 经验确认）。 |
| §0.6 | 有预算预留、租约与同题比较；四阶段节奏、并行五边界与单机退让仍靠人判断，没有统一入口。 |
| §8 | 事务停止、起跑核材料、审批页先行都有入口；「304 秒内接住用户」「先 HTML 后卡」的真实送达与 12 小时整批成本仍未端到端验收。 |
| §9 | **问题台账已实现**（首错登记、精确查重、显式负责人、修复、复验 argv+退出码、冷件不覆盖、失败可诊断）；语义判断（这个错算不算同一个、修得对不对）仍由 agent / 人核。 |
| 专题 | 有真实隔离单元（worktree + venv + 文档 + 前后快照）与归属/世代/交接；「远端隔离与依赖装好」仍需实验适配器核查。 |
| 复盘自进化 | 有 `trajectory_review` 契约、经验 REV 确认与文档冷热登记；**完整供料与复盘报告验收**未做，语义纪律需真执行。 |

## 补齐顺序与完成判据

1. **继续验收部署接线**：统一监控、事务内复核、实际执行规范审批绑定、宿主目标重放、独立执行目录与报告投影已接通。通道配置/常驻收件入口、远端受管执行与独立监控语义回放仍需继续验证，不计为完成。
2. **真实通道送达**：页 + 卡 + 双回执 + 附件，先用假通道验状态机（已可跑），再接配置后的真通道验送达与回复；通道适配器离线待验。
3. **真实执行验收**：把 `workflow` 的闸与 `admission` 的预算预留接到真实评测器，用一次小样本确认真子进程、真实成本与停机对账；不能用合成演示代替。
4. **完整冷启动**：从 clone + 空工作区开始，跑完专题隔离、事前材料、起跑、结果取回、复盘、scribe 写页、下一轮与用户停止，并区分合成离线 / 真实宿主 / 真实远端 / 真实通道四种证据。
5. **语义纪律的执行记录**：结论条件、页面可读性、审查质量由 agent 主动完成并留原始材料；不强制全改脚本，但必须有完成记录。

**「功能迁移完整」的判据**：全部适用要求都有证据。个人配置（通道、机器、比赛资料）缺失本身合理；**通用功能缺失不能归为「只需填配置」**，也不能靠「让 agent 补一段代码」把首次可用性补圆。

## 本轮验证

- 【实测】`python -X utf8 -B tools/check_repository.py`：192 个公开文件，UTF-8、语法、已知私人值签名与文档链接检查**无错误**。它不证明不存在未列入规则的私人信息。
- 【实测】本轮文档改动后自查：验收表 109 个编号、顺序与预期表一致，无漏号/重号。
- 【父 agent 汇总】各模块专项测试规模：workflow 55 · reporting 73 · monitoring 32 · decisions 42 · progress 32 · issues 25 · admission 6；治理 48 条宿主绑定正例。本表**不**把这些数字当功能覆盖证明，数字由父 agent 统一刷新。
- 【已实测的真实宿主探针】① 独立子会话：固定 JSON 报告的 spawn / 父子绑定 / 原文逐字相等均 `verified`；② 实际 scribe 从空工作区经 init + provision + prepare 写到 `functions.write` 整页，`DSHHost.ack` 回 `verified=true`、`session_verified=true`、`delivery_verified=false`，八节校验无错。**单次探针不等于完整真实云实验。**
- 【未验】真实消息通道送达、真实云机与平台提交、完整冷启动、`progress-host` 的 activation 语义（进程内仍标 unknown）。本轮没有宣称这些通过。

## 本轮范围

本轮是**按当前源码重审验收表 + 补操作文档**：新增[怎么跑](<RUNNING.md>)，更新 README 的迁移状态与首次隔离命令，更新[迁移说明](<migration.md>)的落点与在途清单，并把本表从「没有模块」改写成「有实际入口 / 接线在途 / 真实部署未验」。没有把在途或待验项伪装成完成，也没有恢复旧终点闸为必跑条件。源码与真实工作区、云机、个人配置均未改动。
