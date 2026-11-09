<!--
GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
PURPOSE: 治理层的不变量总表 —— 哪些规矩是机械可判的、哪些仍靠纪律，以及每条落在哪个函数上
ORIGIN: none（新文件）。I1–I12 由上游 CONTRACT.md 的 I1–I20 收敛而成，去掉了与本仓库绑定的编号、渠道与实例事故
CUMULATIVE-DELTA: 2026-11-09 撤销来源逐项硬填表，监控默认关闭，保持授权证据和角色分离。首版。每条都标了「机械判据」或「仍是纪律」，不把纪律写成已验收。
-->

# 治理契约：不变量总表

> 这份文件是**不变量**的唯一住所：别的文档只引用编号，不重述内容（I1）。
> 每条都标清楚：**机械判据**（有函数会拒绝）还是**仍是纪律**（没人能自动判）。

---

## 1 不变量

| # | 不变量 | 机械判据（谁拒） | 仍是纪律的部分 |
|---|---|---|---|
| **I1** | 同一条规矩只写在一个地方，其它地方只写编号 | —— | 写文档时需要人判断「这条是不是已有住所」 |
| **I2** | 编号分配权单一：计划号 / 候选号 / 审计报告号 / 意图号 | `freeze_plan` · `add_lesson_candidate` · `create_intent` 对重复 id 直接拒 | —— |
| **I3** | 判据取自程序产物，不取自 agent 的抄写 | 实际来源与报告内容绑定；判据source可选，审计重点为三项判断 | 那条 `source` 是不是真的支撑门槛 |
| **I4** | 下结论前回读权威产物，不用上一轮结论 | —— | 全程靠纪律（本层只登记，不判断读没读） |
| **I5** | 检测方只读 | 独立审计者的写权限由宿主限制，本层只收报告 | 审计者有没有偷偷改方案 |
| **I6** | 只由授权、安全、执行真实性或重大研究缺陷阻断受影响动作；普通写法与手续提示自查 | 当前审计不可判或存在未关闭重大阻断时，`audit_state`不放行；minor_fix按correct自查，不占审计次数 | 语义是否真的改变核心设计，是否绕过真实起跑入口 |
| **I7** | 授权必须可核：能追到一条真实到达过的用户事件 | `set_policy(mode="off")` · `waive` · `close_intent(withdrawn/replaced)` 都要 verifier | 没有 verifier 时**整条路径不可用**（这是设计，不是缺陷） |
| **I8** | 证据落 append-only 处 | 本层 13 张表有 `BEFORE UPDATE/DELETE` 触发器 | —— |
| **I9** | 闸要有执行台账 | 每次治理动作 `store.emit()` 落一条事件 | 事件有没有被读 |
| **I10** | 关闭一条禁令必须晚于该范围的最后一次准备 | `set_policy` 比较证据时间与最后一次 `prepare_audit` | 证据时间戳本身的真实性（由 verifier 核） |
| **I11** | 要求先于实现：动手前先查有没有人给过要求 | `validate_plan` 要求 `input_files`，不强制每条判据原文背书 | 有没有真去读那份 `input_files` |
| **I12** | 一次授权跨轮存活必须可见，计数不许归零 | 本层不存授权账（那是 runtime 层的事）；跨轮计数由 `runtime` 与 `plan` 的 `baseline` 承担 | 系列授权还剩多少轮 |

⚠️ **I7 是本层最关键的一条**：它让「本地自述批准」在结构上无法冒充「用户批准」。
没有注入 verifier 时，`set_policy(off)` / `waive` / `close_intent(withdrawn|replaced)`
**一定抛错**，而不是降级成「就当批了」。

---

## 2 角色与权限

| 角色 | 能写什么 | 不能写什么 |
|---|---|---|
| researcher（主研究者 / 主会话） | 方案、启用后的意图、触发、处置、经验候选；现状页由scribe写 | 自己的审计报告、自己的意图监控报告 |
| planner | 方案（`freeze_plan` 之前） | 冻结后的方案正文（要改就重新冻结） |
| runner | 运行产物、结果文件 | 方案、审计、意图 |
| scribe | 人读现状页（按别人给的材料） | 结论、判据、方案 |
| independent-reviewer | 只写自己那份审计报告 | 方案、文档、运行、授权 |
| intent-monitor | 只写自己那份监控报告 | 方案、文档、运行、停止实验 |
| trajectory-reviewer | 只写自己那份复盘报告 + 经验候选 | 正式经验文档（那份只收已确认条目） |

**分工的两条硬要求**：① 审计者 ≠ 方案负责人（`record_audit` 拒）；
② 监控报告的作者 ≠ 被监控的负责人（`record_monitor_report` 的 `actor` 由调用方保证，
本层只登记；派工要新起子会话）。

---

## 3 落点登记（写盘即判据的地方）

| 落点 | 谁写 | 形态 | 读它的函数 |
|---|---|---|---|
| `gov_plans` | `freeze_plan` | append-only，一版一行 | `latest_plan` · `audit_state` |
| `gov_audits` | `prepare_audit` | append-only（`covered_by` 之外无更新） | `audit_state` |
| `gov_audit_reports` | `record_audit` | append-only | `audit_state` · `respond_audit` |
| `gov_audit_materials` | `prepare_audit` | append-only，绑定实现文件路径、实际SHA256与字节数 | `record_audit` · `correct_audit` · `audit_state` · `audit_declaration` |
| `gov_audit_responses` | `respond_audit` | append-only | `audit_state` |
| `gov_waivers` | `waive` | append-only，绑定当时的关键字段指纹 | `audit_state` |
| `gov_policy` | `set_policy` | append-only，最后一条生效 | `audit_policy` |
| `gov_intents` · `gov_intent_events` | `create_intent` · `block` / `unblock` / `close` | 状态行可更新，但**不许删**；事件表 append-only | `intent_state` · `check_start` |
| `gov_intent_triggers` | `register_trigger` | 触发点，`covered_by` 由报告填写 | `record_monitor_report` · `intent_state` |
| `gov_user_updates` · `gov_intent_assessments` | `sync_user_update` · `assess_update` | append-only（两个水位） | `check_start` |
| `gov_lessons` · `gov_lesson_confirmations` | `add_lesson_candidate` · `revise_lesson` · `confirm_lesson` | append-only，一版一行 | `lesson_state` · `lessons` |
| `gov_roles` · `gov_delegations` | `register_rolepack` · `record_delegation` | append-only | `record_delegation` |
| `gov_reads` | `record_read` | append-only | `reading_state` |
| `gov_docs` · `gov_doc_rolls` | `register_doc` · `roll_cold` | append-only | `doc_status` |

⚠️ 新增「写盘作为判据」的落点，必须先加到这张表；否则下一个人不知道该去哪读。

---

## 4 三类边界（这套东西不能证明什么）

1. **不能证明语义**：`validate_plan` 能判形状与绑定，判不了「这个假设值不值得做」。
2. **不能证明独立性**：没有 verifier 时，「独立审计」只是 `declared` 的一行记录。
   结构上的独立性（新子会话）由宿主与派工保证，本层看不见。
3. **不能证明读过**：`record_read` 默认 `declared`；宿主读证据要 verifier。

⇒ 报告里出现「已审计」「已确认」「已授权」时，必须同时写清 **来源等级**。
