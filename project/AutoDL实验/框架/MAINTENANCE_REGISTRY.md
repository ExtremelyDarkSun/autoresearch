<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-09
PURPOSE: 活跃文档与冷热维护登记
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
-->
# 文档维护登记

默认限额及滚动正文只在[维护契约](<../../../kernel/contracts/doc_maintenance.md>)，本表不复制数字。

| 热件 | 形态 | 冷件索引 | 当前情况 |
|---|---|---|---|
| [工程未决](<REQUESTS.md>) | 未结案/已结案 | [编号反查](<REQUESTS_cold/INDEX.md>) | 空模板，无真实工程条目 |
| [工作日志](<../实验记录/EXPERIMENT_LOG.md>) | append-only，按时间滚动 | [日志历史](<../实验记录/历史/INDEX.md>) | 空模板 |
| [运行登记](<../实验记录/EXPERIMENT_STATUS.md>) | 当前运行与历史索引 | [日志历史](<../实验记录/历史/INDEX.md>) | 未运行 |
| 各专题 TOPIC | 仅已完成路线可滚动，未决仍在主文档 | 每专题创建 TOPIC_cold/INDEX | 未创建专题 |

规则和契约不按结案冷热切；改结构按动作触发拆分，整份读取义务不变。实际台账增长后登记各自限额，不自动截断。
