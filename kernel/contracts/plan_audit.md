<!--
GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
PURPOSE: 开跑前的独立方案审计 —— 七项审什么、报告字段、绑定关键字段指纹、每个版本最多 2 次、措辞不重审、放行与拦截
ORIGIN: none（新文件）。抽取自上游 contracts/plan_audit.md 的 §2/§3/§4/§6/§7/§8，去掉了具体实验号与宿主日志字段名
CUMULATIVE-DELTA: 首版。机械判据全部落在 governance.prepare_audit / record_audit / audit_state。
-->

# 独立方案审计

> 一句话：**开跑之前，换一个看不见你自我论证的会话，按七项清单挑毛病；
> 它的结论绑定在这一版方案的关键字段上，改关键字段就作废。**

---

## 1 默认必审，且不能无声跳过

| 情形 | 行为 |
|---|---|
| 什么都不配 | **照样要审**（`audit_policy()` 默认 `required`，fail-closed） |
| 显式写 `required` | 只是把「必审」落成可核记录，不改变默认 |
| 写 `off` | **必须有 verifier 核过的用户证据**；本地自述关不掉 |
| 政策是 `off` 时想 `prepare` | **直接拒**：不存在「关了又偷偷审」的半状态 |
| 关闭证据早于最后一次 prepare | 拒（I10：先准备、再偷偷关掉的路径必须堵住） |

---

## 2 谁审、怎么派

| 要件 | 要求 | 谁拒 |
|---|---|---|
| **新上下文** | 新起子会话，看不见主会话的论证过程 | 宿主 / 派工方 |
| **不用 fork** | 继承上下文的「审计」不算独立 | 派工方（本层看不见） |
| **只读** | 不改方案、不改文档、不改运行；只写自己那份报告 | 宿主权限 |
| **不启动** | 不开机、不跑评测、不对外发消息 | —— |
| **≠ 负责人** | 审计者不能是方案负责人 | `record_audit`（机械） |

⚠️ 独立性分两档：**结构独立**（新子会话，宿主保证）与**可核独立**
（verifier 给出 `verified`）。没有 verifier 时报告标 `declared` —— 别把
`declared` 写成「已验证独立」。

---

## 3 审哪七项

七项**必须全部留痕**（报告 `coverage` 恰好七个键）。有问题不必须 blocking，
但「没看」不行。

| # | 键 | 要回答的问题 | 不合格长什么样 |
|---|---|---|---|
| 1 | `hypothesis` | 什么观察会否定它？有没有把两件事混成一个假设？ | 读不出可否证 |
| 2 | `design` | 读数变好能否支持所声称的解释？对照/题单/设置撑得住吗？ | 变好但与假设无关 |
| 3 | `criterion` | 门槛有出处吗？样本与波动够不够？ | 门槛是拍的；小题单拿幅度当门槛 |
| 4 | `history` | 是否已试过并被否掉？旧结论在当前条件下还成立吗？ | 只读主会话给的摘要 |
| 5 | `cost` | 有没有更省的验证路径？哪些部分不必要？ | 直接上整臂验一个没验过的改动 |
| 6 | `branches` | 支撑/反驳/判不了三支都有合理行动？ | 某支写「再分析」；三支行动相同 |
| 7 | `activation` | 改动到读数要经过哪几跳？每跳有可观察证据？ | 声明的配置没人读；跑的不是这份包 |

**`unknown` 的用法**：判不出来就写 `unknown`，并且那项必须有 `blocking=true` 的 issue
（**判不出来不许默认通过**）。机械判据在 `record_audit`。

---

## 4 报告字段

```json
{
  "prepared_id": "PREP-…",
  "coverage": {
    "hypothesis": {"verdict": "ok", "note": "…", "sources": [{"path": "…", "locator": "L1"}]},
    "design":     {"verdict": "issue", "note": "…", "sources": [{"path": "…", "locator": "L2"}]},
    "criterion":  {"verdict": "ok", "note": "…", "sources": [{"path": "…", "locator": "L3"}]},
    "history":    {"verdict": "ok", "note": "…", "sources": [{"path": "…", "locator": "L4"}]},
    "cost":       {"verdict": "ok", "note": "…", "sources": [{"path": "…", "locator": "L5"}]},
    "branches":   {"verdict": "ok", "note": "…", "sources": [{"path": "…", "locator": "L6"}]},
    "activation": {"verdict": "ok", "note": "…", "sources": [{"path": "…", "locator": "L7"}]}
  },
  "issues": [],
  "resolves": []
}
```

`issues` 每项：`id` · `category`（七项之一）· `blocking`（布尔）· `problem` ·
`consequence` · `suggestion` · `sources`（非空）。

三条一致性硬规则（工具拒）：

1. `verdict` 是 `issue` / `unknown` ⇒ 必须有**同 category** 的 issue（只在 note 里说不算）；
2. `verdict=unknown` ⇒ 那一项必须有 `blocking=true` 的 issue；
3. 每个 `locator` 要能定位（行号或文件内逐字片段）。

⚠️ 处理记录与审计报告**分开存**：报告 append-only，处置另表（`respond_audit`）。

---

## 5 版本绑定 · 每个版本最多 2 次

| 规则 | 判据 |
|---|---|
| 绑定什么 | **关键字段**的指纹（`governance.KEY_FIELDS`），不是整份 plan |
| 一次准备 = 一次额度 | `prepare_audit` 计入次数，`index_no` 递增 |
| 上限 | 默认 **2**（首审 1 + 增量复审 1）；第 3 次直接拒 |
| 扩额 | 必须由用户在真实通道裁决；**本层不提供自我扩额入口** |
| 改了关键字段 | 指纹变 ⇒ 旧报告不覆盖新版本 ⇒ 重新 `prepare` + 复审 |
| 只改叙述字段 | 指纹不变 ⇒ `reaudit_required=False` ⇒ **不要求重新审计** |

⚠️ **为什么只绑关键字段**：实测教训是「改一句动机 ⇒ 指纹变 ⇒ 上一轮审计作废」，
结果**审计成本反过来压住了修改**，主会话把意见标成「不采纳，理由是改了会让审计作废」。
⇒ 绑定面收窄到关键字段，代价是叙述层可能与审计所见略有差异。这是明确接受的取舍。

⚠️ **不许 `touch` 文件让它「看起来是新的」**；不许把「已按建议调整」写进记录而方案没动。

---

## 6 主会话怎么处置意见

每条 issue 三选一 + 理由 + 依据：

| 处置 | 含义 | 能不能关 blocking |
|---|---|---|
| `accepted` | 采纳（改了哪一处） | 不能：还得由复审的 `resolves` 或用户 `waive` 关 |
| `declined` | 不采纳（为什么不采纳） | 不能：`declined` 不等于关掉 |
| `needs_user` | 需要用户决定 | 不能：一直红到用户表态 |

⛔ 同一份报告里同一条意见不许重复处置；
⛔ 不许只写「已考虑」；⛔ 不许把 `blocking` 改成 false 重发就当消掉（旧报告还在）。

---

## 7 放行与拦截

`audit_state(plan_id)` 返回 `{ok, problems, blocking_open, audits_used, limit, …}`。

| 情形 | 结论 |
|---|---|
| 政策 required 且当前版本有干净报告 | 放行（≠ 方案对、≠ 有实验授权） |
| 当前版本没审 / 关键字段变了 / 有未处置意见 / `needs_user` / 阻断未关 | **不放行**，`problems` 点名 |
| 政策 `off` | 不放行，且判词写明「不代表审过」 |
| 拿不到证据、判不了 | 不放行（判不出时不许默认安全） |
| 用户喊停 / 预算耗尽停机 | **不拦**：安全停机优先于任何审计动作 |

---

## 8 仍是纪律（⛔ 不许读成已验收）

1. **绕过统一入口**：直接调评测脚本仍可绕过 `check_start` 与审计检查。
2. **审计质量**：七项是清单，漏判 / 误判 / 泛泛建议都可能。
3. **处置是真心还是敷衍**：格式能过，语义判不了。
4. **派工是不是真的新会话**：本层只见登记，不见宿主动作；要 `verified` 只能靠 verifier。
5. **用户是否真看到结论**：报告落盘 ≠ 人看过。
