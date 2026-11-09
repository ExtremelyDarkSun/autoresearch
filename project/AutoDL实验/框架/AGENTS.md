<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-09
PURPOSE: 框架子项目入口与维护规则
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
-->
# 框架入口

动框架前整份读[不变量](<../../../kernel/contracts/CONTRACT.md>)、[读取政策](<../../../kernel/contracts/reading_policy.md>)和本项目[读取路由](<CONTEXT_LOADING.md>)。
主负责人读[完整预期](<../../../kernel/contracts/framework_expectations.md>)；不能把文件名齐全当成功能完成。

| 动作 | 直接正文 |
|---|---|
| 定义框架范围与研究层 | [项目组织映射](<../../ORGANIZATION_MAP.md>) |
| 写计划与独立方案审计 | [方案契约](<../../../kernel/contracts/plan.md>)、[审计契约](<../../../kernel/contracts/plan_audit.md>) |
| 起跑、状态、复盘与下一轮 | [操作入口](<OPERATIONS.md>) |
| 给人写页与给AI供料 | [研究页契约](<../../../kernel/contracts/status_page.md>) |
| 做轨迹复盘与经验确认 | [轨迹复盘](<../../../kernel/contracts/trajectory_review.md>)、[经验](<../../../kernel/contracts/research_lessons.md>) |
| 控制重复交付与上下文成本 | [上下文预算](<../../../kernel/contracts/context_budget.md>) |
| 改规则、滚动台账 | [维护正文](<../../../kernel/contracts/doc_maintenance.md>)、[维护登记](<MAINTENANCE_REGISTRY.md>) |
| 找工程缺陷 | [问题入口](<REQUESTS.md>) |

用户叫停优先。机械通过不证明科学结论。工作区代码、环境、落点须独立；无可靠停止证据不可报已停。
维护先核Git工作树、留改前件，再最小修改，更新头部与所有受影响入口，跑相关检查。
历史运行包不重写。已有失败先与改前件对照。索引不要写会过期的正文数量。
