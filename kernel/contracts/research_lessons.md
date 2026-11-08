<!--
GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
PURPOSE: 经验候选的四种处置、REV 与 METHOD 双重绑定、修订如何让旧确认失效、问人时的三种来源
ORIGIN: none（新文件）。抽取自上游 contracts/research_lessons.md 的 §3–§9，去掉了飞书卡片与具体目录名
CUMULATIVE-DELTA: 首版。机械判据落在 governance.add_lesson_candidate / revise_lesson / confirm_lesson / lesson_state。
-->

# 经验候选与确认

> 一句话：**一条经验只有在「人对某一版正文说过保留」之后才算数；
> 正文一改，那条确认就作废。**

---

## 1 候选先写，确认可以后到

| 事件 | 之后 |
|---|---|
| 一轮结束 | 不写经验（单轮结束 ≠ 收尾） |
| 一个专题的完成条件达成 | **才开始**写候选，且**先于**收尾落盘 |
| 收尾之后 | 人的确认照样可以落到文件里；⛔ 不许为了等确认把专题重新打开 |

⚠️ **候选必须先落盘**：收尾之后没有负责人，没人会回来补写。

⚠️ **没有候选也要写一句「本次无候选」+ 理由**；⛔ 不许把「本轮改了哪些文件」写成经验。

---

## 2 每条候选写什么

| 字段 | 判据 |
|---|---|
| `candidate_id` | 一次专题内**永不重用、永不重排**；删一条 = 标 `drop`，不删块 |
| `title` | 一句话 |
| `method` | **被确认绑定的那一行**：建议的检查或决策方法 |
| `premise` | **必填**：这条经验成立的前提（装置 / 量具 / 受控条件 / 版本） |
| `applicable_when` | 让人能判断「我这次算不算这个情景」 |
| `basis` | 依据路径 / 读数 / 实验号；未验证就写「未验证」 |
| `boundaries` | 不适用边界 |
| `evidence_level` | `unverified` / `supported` |

判据两条：

1. **删掉「前提」整行，这条还站得住吗？** 站得住 ⇒ 那是常识，不该收。
2. **「情景」能不能让人对照？** 写成具体条件；⛔ 不写「一般情况下」。

---

## 3 四种处置

| `option` | 中文 | 进正式文档吗 | 状态 |
|---|---|---|---|
| `keep` | 保留 | ✅ | `confirmed` |
| `keep-edited` | 修改后保留 | ✅（改完的正文） | `confirmed` |
| `keep-pending` | 待验证保留 | ✅，但状态必须写「待验证」 | `confirmed-pending` |
| `drop` | 不留 | ⛔ 不进；块留在候选文件里，写明为什么 | `dropped` |

⚠️ **「待验证保留」不等于已证实**：按 `status` 精确过滤（`lessons(status=…)`），
别把 `confirmed-pending` 当 `confirmed` 用。

---

## 4 确认记录与两项绑定

```json
{
  "candidate_id": "L-01",
  "rev": 2,
  "option": "keep",
  "quote": "保留，但把前提写清楚",
  "source": "gui | text | card",
  "source_session": "session-…",
  "source_seq": 42
}
```

**两项绑定**（`confirm_lesson` 机械判）：

1. `rev` 必须等于该候选的**当前最新 REV**；
2. `method_hash` 必须等于当前 REV 的 `method` 的哈希（由工具现算，调用方不用传）。

任一项不符 ⇒ 拒。那条确认是对**旧版本**说的。

**来源三档**：`card`（卡片点击）/ `text`（自由文本）/ `gui`（会话界面）。
⛔ 不许把 GUI 或自由文本写成卡片点击；⛔ 不许自己替用户选；
⛔ **没有原话就不许写确认**。`source` + 逐字 `quote` 是硬要求。

---

## 5 改了什么会作废旧确认

| 情形 | 做什么 |
|---|---|
| 改 `method` | `revise_lesson` ⇒ REV+1 ⇒ 旧 `method_hash` 失配 ⇒ 回 `pending` ⇒ 重新问 |
| 只改 `basis` / `boundaries` / `premise` | 同样 REV+1（靠 REV 失配判失效，不靠 method 不同） |
| 人给了**整句可直接采用**的新文 | 写进 `method`，REV+1，记录里 `option=keep-edited`，`quote` = 人那一句；不必再问 |
| 人的修改**含糊**（「稍微收紧一点」） | 按理解改 ⇒ REV+1 ⇒ **再问一次**；⛔ 不许自己改完自认已确认 |
| 只改标题 / 错别字 | 可以不动 REV，但要在候选文件顶部记一行「改了哪里」 |

**这条治的病**：旧确认自动覆盖修改后的方法。
**「已确认」永远只对它确认过的那一版正文成立。**

⚠️ `lesson_state()` 会把失效的旧确认列成 `stale_confirmations`，并给出
「必须重新确认」的 `problems`。⛔ 已确认条目扶正时**逐字搬**，不许改词。

---

## 6 问不到人

* 通道不可用 ⇒ 记 `ASK: not_sent` 一行，候选保持 `pending`。
* 人没回 ⇒ 候选保持 `pending`。
* **两种情形都允许收尾**：⛔ 不许拿「等人回」把专题卡住；
  ⛔ 不许写「已复盘」「已确认」；⛔ 未确认候选不得当依据被引用。

---

## 7 下一个人怎么读

| 要做什么 | 读什么 |
|---|---|
| 接手 / 设计下一轮 | 只看「适用场景」含本轮情景的条目 |
| 把结论推到别处 / 写进规则 | 状态必须是 `confirmed`，且依据路径现读得到 |
| 判「这条现在还能用吗」 | 看「不适用边界」 |
| 查有没有待确认候选 | `lessons()` 里 `status=pending` 的那些 |

⚠️ 跨专题检索时只读**正式文档**，不读候选文件 —— 未确认候选因此天然混不进依据。
