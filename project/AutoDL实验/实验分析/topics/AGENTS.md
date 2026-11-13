<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-13 专题入口写明等待簿与跨专题机制清单：两类内容都不留在专题目录（DESIGN_* 判红）
PURPOSE: 专题目录加载入口
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 2026-11-09 恢复本地中文目录，工具按明确布局标记适配； 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
  2026-11-13 加两行：等审批 / 等读数 / 等机器的事登记 [等待簿](<../WAITING.md>)，专题直接层 DESIGN_* 由
  `workspace documents --check` 判红（archive/ 历史件不追溯）；跨专题机制只在 [机制清单](<../MECHANISMS.md>) 补指针。
-->
# 专题工作入口

接手先读专题登记中的真实目标，再读该专题完整 OBJECTIVE、REQUIREMENTS、TOPIC及本轮计划。
判读前整份读[实验规则](<../../实验记录/EXPERIMENT_RULES.md>)。当前没有真实专题。

TOPIC是唯一科学主文档：目标、假设迭代、完成条件、研究边界、路线、证据索引、专题间关系。
每轮事前与事后更新并留快照。EVIDENCE原始证据带产生时间、前提与条件。
STATUS是人读研究页；AI_WORK为机器投影，不能冒充证据正文或免读规则。
等待期设计不进专题目录：等审批 / 等读数 / 等机器的事登记[等待簿](<../WAITING.md>)；专题直接层出现 `DESIGN_*` 由 `workspace documents --check` 判红，`archive/` 里的历史件不追溯。
机制跨专题共享时不留在专题目录：留正文给本轮，只在[机制清单](<../MECHANISMS.md>)补一行指针（改善哪个过程指标 · 适用与不适用 · 结论与证据）。
专题由 `workspace topic` 从版本模板创建TOPIC、日期证据页与STATUS，回执记模板哈希；骨架待填，不算已交付。旧专题用 `workspace documents` 只补缺失类别，已有文件不覆盖。不手工伪造已provision的回执。相关现行材料全文读，旧路线按冷件编号反查。
