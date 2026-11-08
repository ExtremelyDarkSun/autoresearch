<!--
GENERATED-BY: portable extraction / repository bootstrap (2026-11-09 +08:00)
LAST-EDIT: portable extraction / repository bootstrap (2026-11-09 +08:00)
PURPOSE: 经验候选块的写法模板（含确认记录段），照抄改值
ORIGIN: none（新文件）
CUMULATIVE-DELTA: 首版
-->

# 经验候选（模板）

> 一个专题一份候选文件。候选在收尾**之前**落盘；人的确认可以后到。
> 写入工具：`add_lesson_candidate()` / `revise_lesson()` / `confirm_lesson()`。
> ⛔ 未确认候选不得进正式文档，也不得当依据被引用。

---

## 候选块（照抄）

```markdown
### L-01 <一句话标题>
REV: 1
STATUS: pending

METHOD: <建议的检查或决策方法。这一行会被确认绑定，改它就要升 REV>

- 前提：<这条经验成立的前提：装置 / 量具 / 受控条件 / 版本。⛔ 不写「一般情况」>
- 适用场景：<什么情况下该用这条；要能让人判断「我这次算不算这个情景」>
- 依据：<路径 / 读数 / 实验号；没有就写「未验证」>
- 不适用边界：<什么条件下不成立>
- 证据等级：unverified | supported（supported 要带【实测】路径）
```

判据两条：

1. **删掉「前提」整行，这条还站得住吗？** 站得住 ⇒ 那是常识，不该收。
2. **「情景」能让人对照吗？** 写成具体条件，⛔ 不写「一般情况下」。

---

## 确认记录（append-only，写在候选文件末尾）

```markdown
### 确认记录 #1
- 时间：<YYYY-MM-DD HH:MM +08:00>
- 候选：L-01
- CONFIRMED_REV: <等于该候选当时的 REV>
- 确认选项：keep | keep-edited | keep-pending | drop
- 修改后保留时改了什么：<一句话；其它留空>
- source：gui | text | card
- 引用：<会话 id + 消息序号 / 事件 id>
- 人的原话（逐字，现读）：「<…>」
- 记录人：<会话 id>
- independence：declared（没有 verifier 核过就只能这么写）
```

**三项绑定**：候选 id + `CONFIRMED_REV` + `method` 逐字。任一项不符 ⇒ 回 `pending`。

⛔ 不许删确认记录、不许改旧记录（append-only）。
⛔ 不许把「倾向保留」写成「保留」；⛔ 不许自己替用户选。

---

## 状态取值

| STATUS | 含义 | 进正式文档吗 |
|---|---|---|
| `pending` | 待确认 | ⛔ 不进 |
| `confirmed`（keep / keep-edited） | 已确认·保留 | ✅ |
| `confirmed-pending`（keep-pending） | 已确认·待验证保留 | ✅ 但必须写「待验证」 |
| `dropped` | 不留 | ⛔ 不进；块留在文件里写明为什么 |
