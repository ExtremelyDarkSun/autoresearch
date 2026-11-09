<!--
GENERATED-BY: portable framework synchronization
LAST-EDIT: portable framework synchronization, 2026-11-09
PURPOSE: 三态审计、小修、历史阻断与报告文件绑定。
ORIGIN: generic mechanisms from upstream plan_audit.md
CUMULATIVE-DELTA: 2026-11-09 同步研究判据、三态审计、小修、完整读取及研究报告规则。
-->
# 独立方案审计

## 1 默认必审

未配置仍为 `required`。关闭政策须可信验证器核实的用户证据，且满足现行准备版本的时间约束。关闭后不得准备审计。审计放行不代表实验授权或科学正确。安全停机不等待审计。

## 2 谁审

实际派出新上下文只读会话，不用 fork。审计者不能是负责人。只读取方案、原始材料与自己检索的历史，不继承主会话的自我论证。不修改方案，不启动实验，不对外发消息。

本地登记默认 `declared`。真实独立性只有注入可信宿主验证器后才能记 `verified`。它仍不能证明审查质量。

## 3 审哪七项

七项都要留痕：`hypothesis`、`design`、`criterion`、`history`、`cost`、`branches`、`activation`。

审查聚焦三个问题：研究问题与目标是否相连，研究设计与解释是否有重大缺陷，有没有会改变研究决定的历史证据或实际限制被遗漏。

探索性实验不强制数字门槛。小样本先判断机制是否存在。样本与重复须匹配解释范围，不因形式对称多跑同包副本。

## 4 报告字段

报告含 `prepared_id`、七键 `coverage`、`issues`、`resolves`、`decision`、`decision_reason`、`focus`，可含 `minor_fix_paths`。

每个 coverage 项含 `verdict=ok|issue|unknown`、`note`、非空 `sources[{path, locator}]`。`issue` 或 `unknown` 必须对应同 category 的问题。

每个问题含 `id`、`category`、布尔 `blocking`、`problem`、`consequence`、`suggestion` 和非空 `sources`。新三态报告中 `unknown` 可以非阻断，但必须写 `why_not_blocking`。阻断则写 `why_blocking`。旧格式缺少 decision 的报告按旧规则要求 unknown 阻断，不重写历史。

`focus` 恰好三键 `question_goal`、`design_interpretation`、`history_constraints`，各写审查结论。所有新三态重大阻断均须写 `why_blocking`。

三态：`proceed` 可以推进，`minor_fix` 主会话小修自查后推进，`blocked` 重大问题暂不能推进。前两态不能带 blocking 问题，blocked 必须带 blocking 问题，minor_fix 至少带一个问题。

报告 JSON 存文件。审计者只回传短声明，恰好包含 `prepared_id`、`decision`、`decision_reason`、`report_file`、`report_sha256`、`issues[{id,blocking}]`。`record_audit_file` 核字节哈希与声明一致性，真实可见消息仍须宿主验证器核。`audit-digest` 只读计算原始字节 SHA256。直接 `record_audit` 保留给旧调用方，本地登记不冒充宿主证明。

来源定位的形状由内核检查。原始文件存在、行号或片段真实可定位，仍需审计者及宿主适配器核实。不得把纯结构检查说成来源内容已验真。

## 5 版本绑定

每个 plan 跨版本累计默认最多 2 次准备。首审后最多一次增量复审。不得靠改版本归零额度。扩额需要真实用户决策，本层不提供自我扩额。

关键字段指纹绑定 `governance.KEY_FIELDS`，以及计划存在的 `execution_argv` 与 `implementation`。改措辞字段不重审。核心问题、比较、判据、实现链路改变须重新准备及复审。

`correct_audit(report_id, corrections, actor=...)` 记录非阻断小修和自查，不增加审计次数。每项写 `issue_id`（纯定位修正可省）、`what`、`self_check`、非空 `sources`、`paths`。

定位叶子 `.locator` 可以直接修。源文件路径若改变，必须另获按问题授权。普通路径须审计者写 `minor_fix_paths=[{path,issue_id,why}]`。实现链路、执行参数、判据机器项、题单、对照、假设与预算等实质路径不可授权为小修。路径检查不能判断措辞是否偷偷改变语义，这仍须负责人负责。

## 6 处置与阻断

`respond_audit` 每条写 `accepted|declined|needs_user`、理由与证据。correct 可以处理非阻断问题。拒绝非阻断建议不等于重大阻断。

所有历史报告的未关闭 blocking 持续拦截，不能用新报告或新指纹掩盖。只允许后续同计划报告的 `resolves[{report_id,issue_id,reason,sources}]` 或带可信用户证据的 `waive` 关闭。correct 不能关 blocking。用户豁免绑定当前关键指纹，实质变更后失效。

本内核未提供自动小样本豁免。不能用一篇说明消掉重大设计、测量或实现问题。

## 7 状态与边界

`audit_state` 返回 `ok`、`decision`、`decision_label`、原报告 `audit_decision`、`problems`、`blocking_open`、`audits_used`、`limit`、`independence` 和当前指纹。

只剩非阻断意见待处理时为 minor_fix，完成自查后才能 proceed。当前版本缺审计、未关闭重大问题或来源核验失败时 blocked。

绕过统一执行入口、审查科学质量、处置是否认真、用户是否实际看过报告，仍不能由本地登记保证。不得将这些纪律写成已经完成的自动验收。
