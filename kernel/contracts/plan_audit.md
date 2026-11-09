<!--
GENERATED-BY: portable framework synchronization
LAST-EDIT: portable framework synchronization, 2026-11-09（审计减负）
PURPOSE: 开跑前的独立方案审计 —— 三件事、基本正确性、最小报告、程序生成短声明、小修自查、版本绑定与复审。
ORIGIN: generic mechanisms from upstream plan_audit.md
CUMULATIVE-DELTA: 2026-11-09 同步审计减负：审计只抓三件事（问题/目标 · 设计实现测量的基本正确性 ·
  会改变决定的历史限制）；保留「本地读码排错 + 先测 1 个最小样例冒烟」；撤销七项逐项填表、
  固定检索组数、`ok` 项举证、未知必建 issue、判据原文背书、逐字段 `minor_fix_paths` 授权；
  报告最小集 + 程序生成六键短声明；`correct` 自查不占次数；核心设计与执行参数 / 实现的**真实变更**仍绑定
  （`implementation.files` 的路径与磁盘实际字节）；三态、首审 + 1 次复审不变。
-->
# 独立方案审计

> **默认必审。** 审计只说「这一关过没过」，⛔ 不是实验授权，也不是科学结论。
> 审计只抓**三件事**：这一轮问的是不是目标要问的 · 设计实现测量是否**基本正确** ·
> 有没有**会改变决定**的历史证据或实际限制被漏掉。⛔ 不是逐项打分的七张表。

## 1 政策：默认 required

未配置仍为 `required`（删策略文件、删那一行都还是必审）。关闭必须带**宿主核过的真实用户证据**，
且晚于该方案最后一次 `audit-prepare`；关闭后不能准备审计。⛔ 主会话不能自己关。安全停机不等审计。

## 2 谁审

派一个**新上下文**的只读会话：不用 fork、看不见主会话的自我论证。审计者不能是方案负责人；
只读方案、原始材料与自己检索的历史；不改方案、不启动实验、不对外发消息。
本地登记默认 `declared`；只有注入可信宿主验证器核过子会话证据才记 `verified`。
独立性只证结构（会话 / 派工 / 绑定），⛔ 证不了审查质量，也证不了 prompt 里没塞论证。

## 3 审什么：三件事

`focus` 恰好三项，各写一两句结论；不达标的地方落成 `issues`。

| 键 | 要回答的问题 | 主要看 |
|---|---|---|
| `question_goal` | 这一轮回答的是不是**研究问题 / 目标**要问的？读数变好与目标的连接在哪？ | `goal` · `hypothesis` · `change_set` · `criteria` |
| `design_interpretation` | **设计、实现、测量基本正确吗**：读数变好能否支持所声称的解释？跑的是不是这份改动？量的是不是被判分的对象？参数与返回是否如声明？ | `case_sets` · `criteria` · `execution_argv` / `execution` · `implementation` |
| `history_constraints` | 有没有**会改变这次决定**的历史证据或实际限制被漏掉？ | 自己检索到的历史；题量 / 预算 / 机器限制 |

三件事**没有逐项阈值**：判的是「有没有重大缺陷 / 决定性遗漏」，⛔ 不要求每项都留痕填表。

## 4 基本正确性：四条底线的口径

1. **先测 1 个最小样例，再扩大。** 方案要写明：先只跑**本轮最小的 1 个样例（冒烟）**，
   确认**实际采用**（改动真被调用 / 被加载）· **实际参数**（传的是这份）· **实际返回**
   （结果里出现要读的量），再决定是否放大。没写冒烟、或冒烟要看的量在结果里读不到 ⇒
   `design_interpretation` 出 issue；严重到放大也判不了 ⇒ `blocking=true`。
2. **读码可发现的错误先本地排掉。** 语法、路径、参数名、配置有没有人读、返回字段、题量、
   命令是否存在 —— 送审前作者自查。审计发现这类错是**明确问题**，⛔ 不接受「等跑起来再看」。
3. **明显的前提 / 实现错误必须判问题，⛔ 不许称「探索未知」。** 例：引用的文件不存在 ·
   改的配置运行时没人读 · 跑的包不是这份 · 判据读的不是被判分的量 · 两条假设在装置上分不开。
   这些读码或一次冒烟就能否掉 ⇒ 必须 issue；导致「跑完也判不了 / 结论必然说不清」⇒ `blocking=true`。
4. **生效链路按需核，不填五条表。** 改动会不会被加载 / 触发、配置是否真被读、跑的是不是这份包、
   指令是否冲突 —— 有可疑就写清证据；没有可疑写一句「未见异常」即可。

⚠️ **未加载不能直接否定能力效果**：区分未加载、未触发、已使用后无改善。假设本身就是
「只登记能否让模型自主发现」时，未调用可以是该假设的结果；⛔ 不得事后加强制调用再声称检验了同一假设。
⚠️ **⛔ 不强制单变量**：允许「一个假设下的整组改动」；判的是每处改动各自的生效通路。

## 5 历史检索：按需，不固定组数

审计者**自己检索**历史 / 证据文件（别只读主会话给的摘要）。报告里记一句「搜了什么、命中什么、
是否改变决定」即可；⛔ 不要求三组关键词，⛔ 不要求 `queries` / `roots` 入库；搜不到是合法结果。

## 6 什么才算 `blocking`

只有两种：① 跑完也**判不了**这个假设；② 结论**必然说不清**（判据读的不是同一个量、装置链路断了、
两条假设分不开、明显前提已被否掉）。⇒ 每条 blocking 必须写 `why_blocking`（一句话）。

⛔ 不许给 blocking：建议更省的做法 · 措辞 · 可读性 · 「没达到某个细碎阈值」 · 条件化探索里
本来就要等读数才能判的科学未知。⚠️ 但「读码就能发现的错」不属于科学未知。

## 7 报告：最小集 + 完整分析写文件

**必填**：`decision` · `decision_reason` · `focus`（三项）· `issues`（没问题写 `[]`）。
`prepared_id` / `auditor_session` 可省略（`record` 从 `--prepared-id` 参数与 `--proof` 会话补齐），
写了就必须对得上。

| 键 | 内容 |
|---|---|
| `decision` | `proceed` / `minor_fix` / `blocked`（三态） |
| `decision_reason` | 1–3 句关键研究理由（主会话直接转述） |
| `focus` | 恰好三项（§3），各一两句 |
| `issues[]` | `id` · `category`（七类只是分类词，可省）· 布尔 `blocking` · `problem` · `consequence` · `suggestion` · `sources`（可省略或 `[]`，写了每条给真实 `path`；`locator` 可选、失配只作诊断） · `blocking=true` 时 `why_blocking` |

**可选**：`coverage`（想留痕就只写实际看过的项）· `history_search`（一句话）· `resolves`（复核关闭旧问题）。

**硬规则（工具会拒）**：① `decision` 与 `blocking` 一致（`proceed` / `minor_fix` 不许带 blocking，
`blocked` 至少一条，`minor_fix` 至少一条 issue）；② 每条 blocking 有 `why_blocking`；
③ `focus` 三项非空；④ `respond` 的 `sources` 仍非空；⑤ ⛔ 不接受 `minor_fix_paths`。

**已撤销的旧硬规则**：`coverage` 恰好七键 · 每个 `verdict` 都要 `note` + 非空 `sources` ·
`verdict=unknown` 必建 issue · `locator` 必须行号或逐字片段 · 逐字段 `minor_fix_paths` 授权。

⚠️ **完整分析写报告文件**：依据、为什么不是别的解释、还缺什么证据都写文件；
`decision_reason` 与汇报只留 1–3 句。

## 8 短声明：由程序生成

审计者写完报告、确认是最终版后运行：

```powershell
python research.py governance --db <ws>/.autoresearch/runtime.sqlite3 audit-declaration --plan-id <plan> --report <报告文件> [--prepared-id <id>]
```

它**只向 stdout 打印恰好 6 个键**（`prepared_id` / `decision` / `decision_reason` /
`report_file` / `report_sha256` / `issues[{id,blocking}]`），**不写任何记录**。审计者把 stdout
**原样**作为唯一一条消息发出（⛔ 不手写、不增删键、不改 sha、不另发收尾消息）。
`record` / `record_audit_file` 再核「声明 ↔ 报告文件原始字节」，宿主仍核可见正文。

## 9 三态与放行

`audit_state` 输出 `ok` / `decision` / `decision_label` / `problems` / `blocking_open` /
`audits_used` / `limit` / `independence` / `key_fingerprint`；`ok=false` ⇒ CLI exit 9。

| 三态 | 中文判词 | 什么时候 | `ok` |
|---|---|---|---|
| `proceed` | 可以推进 | 没有未处理项、没有 blocking | true |
| `minor_fix` | 主会话小修自查后推进 | 只剩非阻断意见待处理 / 待小修 | false 直到处理完 |
| `blocked` | 重大问题暂不能推进 | 当前版本缺审计、有未关闭 blocking、材料不可核 | false |

⛔ `ok=true` ≠ 方案对、≠ 实验授权。安全停机优先：用户喊停 / 故障 / 预算耗尽 ⇒ 审计不拦。

## 10 版本绑定与复审（只绑研究实质）

`key_fingerprint` 绑 `KEY_FIELDS`，以及计划里出现的 `execution_argv` / `implementation` /
`authorization` / `execution`。**有 workspace 时** `implementation.files` 的路径与**磁盘实际字节**
也进绑定（`gov_audit_materials`）：准备后文件一变，旧审计不覆盖当前实现。

- **要重新 prepare + 复审**：核心研究问题 / 假设 / 对照 / 判据机器读项 / 题单 / 执行参数 /
  实现文件路径或字节 / 装置身份变了。
- **不因这些重审**：`title` / `notes` / 说明文字 / `locator` / 证据引用换文件 / 普通措辞。

### 10.1 小修 `correct`：主会话自查，不占次数

`correct_audit(report_id, corrections, actor=<负责人>)`，每条
`{issue_id?, what, self_check, sources?, wording_only?}`：

| 改动 | 怎么记 | 判据 |
|---|---|---|
| `locator` · `input_files` 证据引用换文件 · `criteria.source` · 说明文字（`why` / `note` / `rationale` / `summary` / `implication` / `goal_link` / `selection_reason` / `mechanism_step`） | 直接 `correct`，`sources` 可 `[]` | **自动小修** |
| 绑定字段的**说明文字**：`hypothesis(.text)` · `implementation` 五字段（`expected_behavior` / `load_path` / `trigger` / `observation` / `non_activation`）· `branches` 字符串叶子 | 逐字段 `wording_only: [{path, before, after, why}]`；`before`=被审版原文、`after`=当前原文 | 只核字段位置与前后原文；⛔ 判不了语义 |
| 判据机器读项 / 题单 / 执行参数 / 实现路径或字节 / 装置身份 / 研究问题 / 对照 | ⛔ 谁都不能当小修 | 重新 prepare + 复审 |

⚠️ 残余风险（明说）：工具只核 before/after 与字段位置，判不了「真的只是措辞」；
这靠 `what` / `self_check` 与人的复核，是**纪律**，不是闸。小修**不占**审计额度。

## 11 次数上限

一个方案（跨版本累计）默认最多 **2 次**：首审 + 一次增量复审。第 3 次起 `audit-prepare` 直接拒；
扩额必须由用户在真实消息里发一条新的 `audit-extend` 命令（命令含 `used` / `limit` /
`key_fingerprint`，旧指令不能反复用）。本层**没有**自我扩额入口。

## 12 旧报告与边界

已登记的旧报告只读兼容、⛔ 不追改旧记录；旧报告缺逐叶表或实现字节绑定时，不能走 `correct`，
要重新 `prepare` + 复审。没有 `decision` 的旧格式不再新收。

绕过统一执行入口、审查科学质量、处置是否认真、用户是否实际看过报告，仍不能由本地登记保证；
⛔ 不得把这些纪律写成已经完成的自动验收。核验失败 ≠ 放行。
