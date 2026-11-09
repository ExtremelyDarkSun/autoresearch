<!--
GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
LAST-EDIT: 2026-11-09 边界复核：仓库已带只读 DSH 宿主核验器与合成冷启动/V1–V3 语义记录；改掉「没有任何 verifier 实现」的旧说法
PURPOSE: 研究治理层的说明书 —— 每个能力对应哪个机械判据、装在哪、真实边界是什么（不声称生产宿主已验证）
ORIGIN: none（新文件）
CUMULATIVE-DELTA: 2026-11-09 同步 host_evidence 的 verified/declared 边界、合成冷启动与语义核的记录口径。
-->

# 研究治理层

> 一句话：**研究的每一步都可以自述做过；这一层让「自述」与「可核」分开记。**
> 代码：`kernel/autoresearch/governance.py`。契约：`kernel/contracts/`。
> 角色与模板：`kernel/roles/` · `kernel/templates/` · `kernel/instructions/`。

---

## 1 它解决什么

| 没有这一层会发生什么 | 这一层怎么拦 |
|---|---|
| 方案是事后补的，判据是跑完再定的 | `freeze_plan` 要求 必需字段齐全，探索性写观察边界，确认性门槛有出处 |
| 一轮里同时改三件事，读数分不开 | `validate_plan` 拒绝多个 `hypothesis_id` |
| 本来能过的题被悄悄删掉 | `previous_passes ⊂ case_sets.regression` |
| 自己审自己，结论必然通过 | `record_audit` 拒绝「审计者 = 负责人」；独立性只认宿主日志里的新 spawn 子会话 |
| 改一句动机就让审计作废，于是没人敢改 | 指纹只绑关键字段，改叙述不用重审 |
| 「差不多做完了」就关掉用户的意图 | 闭合只认四条理由，其中一条要独立报告判 `unachievable` |
| 经验改过之后，旧确认还罩着新正文 | `REV` + `method` 双绑定，一改就失效 |
| 本地自述「用户批准了」冒充授权 | 没有 verifier 时该路径**直接抛错**；仓库自带只读 DSH 核验器，但只有真读过宿主日志才 `verified` |

---

## 2 能力 → 机械判据 → 真实边界

| 能力 | 机械判据（会拒绝的函数） | 真实边界 |
|---|---|---|
| 事前假设与判据出处 | `validate_plan`（探索性观察/条件/边界，确认性阈值与出处） | 判不了「这个假设值不值得做」 |
| 一次一组同假设改动 | `validate_plan`（多个 `hypothesis_id` ⇒ 拒） | 判不了两项改动是不是真属于同一假设 |
| 三类对照 + 历史已通过项不丢 | `validate_plan`（`target` 非空、三类不重叠、类内不重复、`previous_passes ⊂ regression`） | 判不了那些题号是不是真的在同一个测量装置上通过过 |
| 结果分支 | `validate_plan`（三支都要 `action`，且不许完全相同） | 判不了某一支的行动是不是合理 |
| 计划冻结 / 指纹 | `freeze_plan` + `plan_fingerprint`（关键字段 vs 叙述字段） | 叙述字段可以在不重审的情况下变（这是明确取舍） |
| 预算政策可配 | `validate_plan`（`max_cost_units` ≥0 整数、`max_seconds` >0 整数） | 预算单位由部署方定义，本层不换算 |
| 独立方案审计 + 版本绑定 | `prepare_audit` / `record_audit` / `audit_state` | 独立性靠宿主日志核（`host_evidence.DSHHost`，默认读 `~/.dsh`）；不注入或读不到就只记 `declared`，`verified` 要真读过新 spawn 子会话 |
| 每个计划跨版本最多 2 次、措辞不重审 | `prepare_audit` 按 `plan_id` 累计计数；超限直接拒 | 扩额没有自我通道（这是设计） |
| 用户专项意图 + 节点监控 | `create_intent`（原话 + 会话 + seq）· `record_monitor_report`（覆盖全部触发）· `check_start` · `close`（旧 proof / 自然语言 `--user-quote` 两路） | 判不了「这句话算不算专项意图」，那是语义判断；`achieved` / `unsatisfiable` 不能靠原话绕过，必须有 current 独立报告绑定同一句 |
| 预算可信时序 / 防复活 | `HostBudgetSource.trusted_ordering`（只读返回宿主核过的 session / seq / host_time）+ `Accounts.authorize` 在同一个 `BEGIN IMMEDIATE` 里判新旧 | 真实宿主日志的跨会话序与时钟仍由部署保证；不可判就 fail-closed |
| 复盘 / 经验候选四处置 | `add_lesson_candidate` · `confirm_lesson`（`REV` + `method` 绑定） | 判不了人是不是真心保留 |
| 修订使旧确认失效 | `revise_lesson`（`REV+1`）· `lesson_state`（列失效确认） | 判不了修订后的正文是不是更对 |
| 主会话推进 + 可选委派 | `record_delegation`（`rolepack_sha` 必须等于登记值；可注入宿主核验器核对父子谱系） | 没有委派专用的宿主适配器时，仍只记调用方自述，判不了子会话是不是真的新起 |
| 研究现状页八节 | `validate_status_page`（八节非空、四段顺序、来源标记） | 判不了内容真假 |
| 文档冷件 / 不丢 | `register_doc`（hot 必须给上限）· `roll_cold`（基线哈希、索引、不能覆盖） | 判不了搬走的是不是「已结案」 |
| append-only | 治理表带 `BEFORE UPDATE/DELETE` 触发器 | 数据库文件本身可以被替换（那是宿主层的事） |

⚠️ **JSON 文件不带文件头注释**：`kernel/contracts/plan.schema.json`、
`kernel/contracts/reading_required.json` 与 `kernel/templates/*.json` 改用
`x-notes`（schema）或 `_template` / `_usage`（模板）两个键说明来源与用法。
消费这些文件的代码必须忽略下划线开头的键。

---

## 3 抽自哪些组件（按相对文件名）

| 上游组件 | 抽成什么 | 丢掉了什么 |
|---|---|---|
| `CONTRACT.md`（不变量表） | `kernel/contracts/CONTRACT.md` 的 I1–I12 | 与本项目绑定的编号、渠道、事故细节 |
| `plan_audit.md` | `kernel/contracts/plan_audit.md` + `prepare/record/respond/waive/audit_state` | 具体实验号、宿主日志字段名、材料包上限 |
| `intent_monitor.md` | `kernel/contracts/intent_monitor.md` + 意图与监控方法 | 会话文件格式、具体 seq、个人通道 |
| `research_lessons.md` | `kernel/contracts/research_lessons.md` + 候选/确认方法 | 飞书卡片、具体专题目录 |
| `trajectory_review.md` | `kernel/contracts/trajectory_review.md` + 九节模板 | 具体脚本名、具体主题 |
| `reading_required.json` | `kernel/contracts/reading_required.json`（**重写**，条目指向本仓库路径） | 原项目的路径与角色包 |
| `CONTEXT_LOADING.md` | `kernel/contracts/reading_policy.md` + `kernel/instructions/` | 角色包机制、预期表 |
| `DOC_MAINTENANCE.md`（P1–P13） | `kernel/contracts/doc_maintenance.md` + `register_doc/roll_cold/doc_status` | 具体文件体积、滚动历史 |
| `approval.md`（机制） | `kernel/contracts/approval.md` | **具体外部通道**（默认不接任何通道） |
| `EXPERIMENT_RULES.md`（§1 事前设计） | `kernel/contracts/plan.md` + `kernel/instructions/plan_authoring.md` | 题量阈值、机时经验值、指定模型 |

⚠️ 上游的**个人渠道、具体机器、真实题号、历史读数与结论**一律没有带过来。

---

## 4 怎么用（最小例子）

```python
from kernel.autoresearch.runtime.store import Store
from kernel.autoresearch import governance as gov
from kernel.autoresearch.host_evidence import DSHHost

store = Store("state/gov.sqlite")
g = gov.Governance(store)                     # 没有 verifier ⇒ 一切自述都是 declared
g = gov.Governance(store, verifier=DSHHost()) # 只读真实宿主日志：读得到才可能 verified

errors = gov.validate_plan(plan)              # 纯函数：空列表才可冻结
frozen = g.freeze_plan(plan, actor="main")    # 指纹 + 版本
prep = g.prepare_audit(plan["id"], actor="main")
# 把 prep 交给一个**新会话**按七项审，收回报告后：
rec = g.record_audit(prep["prepared_id"], report, actor="reviewer")
state = g.audit_state(plan["id"])             # ok=True 才继续
if not state["ok"]:
    ...                                       # 按 state["problems"] 补
```

三类状态检查都是**只读**：`audit_state` · `check_start` · `lesson_state` / `doc_status`。

统一 CLI 用法：`python research.py governance --db <工作区>/.autoresearch/runtime.sqlite3 --help`。写操作必须显式加 `--commit`；不加时只显示计划模式。`audit-prepare` 成功就占用该计划一次审查额度。

`sync-user --file update.json` 接收 `source_session/seq/quote`；`intent-new --file intent.json` 接收 `intent_id/quote/source_session/seq`（可加 `acceptance`）。先按用户的实际回答决定是否登记意图。`assess-update --file assessment.json` 接收 `source_session/covers_seq/decision/quote`，判 `intent` 时提供 `intent_id`，判 `none` 时提供晚于原句的 `answer_seq`。这些 CLI 输入只是本地声明，不构成已认证的用户事件。意图闭合、豁免与用户确认等完整接口仍由 Python API 提供；需要可信验证器的路径在没有验证器时拒绝执行。
写操作全部 `store.transaction()` + `store.emit()`，事件进 `store` 的 append-only 事件流。

---

## 5 真实边界（⛔ 不要读大）

1. **已带只读宿主核验器，但没做完整生产验证。** `kernel/autoresearch/host_evidence.py` 的
   `DSHHost` 真的读宿主日志（默认 `~/.dsh`，**不接受调用方自带路径**），核用户命令逐键相等、
   独立子会话 spawn / 父子谱系，并拒绝 fork 继承。注入它之后「独立性 / 授权 / 宿主读证据」才可能
   记 `verified`；不注入、读不到或形状不可判时仍只能是 `declared`。
   已做的真实探针：一次独立子会话审计（spawn / 父子绑定 / 原文逐字相等均 `verified`）、一次 scribe
   （`verified=true`、`session_verified=true`、`delivery_verified=false`）与一次受管 job 的精确命令 + jobId
   绑定（只证明登记来源，不证明叫醒或存活）。**单次探针不等于完整真实链路**；
   真实 SSH / POSIX 停止、真实云机、平台提交、真实通道租户与完整冷启动未验。
   停止只覆盖本 job 进程组：子进程主动 `setsid` 逃逸不在内，更强隔离要部署 cgroup / namespace；Windows 无可靠组枚举判 `unknown`，group-stop 不等于整台机器停止。
2. **通道默认关闭，未做真实租户验证。** 审批机制在核心里；`channels/feishu.py` + `channels_cli.py` 是可离线驱动的真实 HTTP 适配层与命令入口：`doctor` 只读、`listen` 缺 AES 解密后端时起服务前 exit 2 且不建库、`poll` 一次性；加密事件用可选 `cryptography`（`requirements-feishu.txt`），先对原 body 验签再解密。官方 AES-256-CBC 的 challenge / 文字 / 卡点击可离线走到认证入账与真实 `DecisionService` 批准；**真实租户送达、收件与群 @ 未验**。
3. **不联网、不起进程、不读凭据。** 治理层只碰调用方给的 `Store`；通道与远端适配层各自有显式开关。`decisions status` / `overdue` / `authorize-check` 与 `channels doctor` 用只读构造，不建表、不建目录、不装通道台账；缺库或旧 schema 判「不可判」。
4. **机械判据不判科学。** `audit_state().ok=True` 只说明「该走的登记走了」，
   不说明假设对、也不构成实验授权。
5. **语义判断仍在人 / 主会话。** 什么算专项意图、什么算执行偏差、这次该不该滚动文档，
   都没有自动判据。V1–V3 的合成语义核也只是 3 个案例的一次独立核。
6. **`validate_plan` 不读文件。** CLI 的 `check-plan <path>` 负责读文件与解析 JSON；
   本函数只接 dict。生产主链的计划样例是 [workflow.plan.template.json](<../kernel/templates/workflow.plan.template.json>)；
   通用治理模板 [plan.template.json](<../kernel/templates/plan.template.json>) 通过 `check-plan` 不代表主链可起跑。

---

## 6 测试

```bash
python -m unittest discover -s tests -p 'test_governance.py' -v
python -m unittest discover -s tests -p 'test_audit_host_binding.py' -v
python -m unittest discover -s tests -p 'test_cold_start.py' -v
```

`test_audit_host_binding.py` 用合成宿主日志核独立性与绑定；`test_cold_start.py` 在同一个空工作区跑
init → Git/venv → Monitoring → Accounts/Admission → DSHHost → 真实子进程 → 复盘 → 报告投影 → scribe
字节回演。两者都用合成夹具，不接触真实会话。

覆盖的**拒绝路径**（不是只测通过）：

* 方案缺字段 / 判据无出处 / `threshold` 是 `NaN` 或 `true` / 多个 `hypothesis_id`；
* 三类对照重叠、类内重复、`previous_passes` 不在回归集合里；
* 三支分支行动完全相同；预算为负或布尔；`input_files` 是绝对路径或含 `..`；
* 审计者 = 负责人；报告缺审项；`issue` 没有对应 issue；新三态 `unknown` 缺阻断或非阻断理由；
* 同一计划跨版本审计超过 2 次；关闭审计 / 豁免 / 撤回式闭合在没有 verifier 时被拒；
* 意图闭合用非法理由、用 `on_track` 报告冒充 `unachievable`、缺用户原话；
* 报告没覆盖未处理触发；`blocked` 不等于 `closed`；
* 经验修订后旧确认失效、用旧 `REV` 确认被拒；
* 委派哈希与登记不符；热件没有上限；滚动基线哈希不符；
* append-only 触发器拒绝 `UPDATE` / `DELETE`。

全部用**临时文件数据库 + 合成数据**（`P1` / `H1` / `t1` / `r1` / `c1`），
不碰任何真实记录。

## 最新同步

三态审查、correct 小修与文件摘要绑定、历史阻断跨版本保存，见 [升级记录](UPGRADE_SYNC.md)。`audit-correct` 接收小修列表，`audit-file` 接收六键短声明文件，`audit-digest --file` 输出原始字节 SHA256。三者仍使用显式 `--db`，写入须 `--commit`。
