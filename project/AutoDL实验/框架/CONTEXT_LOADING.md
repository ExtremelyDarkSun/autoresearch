<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-09
PURPOSE: 人读层级和机读触发路由
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
-->
# 本项目上下文加载

这是组织路由说明；完整读取行为仍由[读取政策](<../../../kernel/contracts/reading_policy.md>)定义。
[context_routes.json](<context_routes.json>)是本项目的机读路由，根入口及各目录入口是对应的人读导航。

| 触发范围 | 主会话入口 | 行动前正文 |
|---|---|---|
| 项目开局 | 根 AGENTS | 根 RULES、PROJECT |
| 平台资料与工具 | Kaggle平台/AGENTS | 相应来源原文、所选profile规则 |
| 实验与结果判读 | AutoDL实验/AGENTS | EXPERIMENT_RULES、BASELINES、相关专题 |
| 框架与流程维护 | AutoDL实验/框架/AGENTS | CONTRACT、reading_policy、doc_maintenance |
| 接手专题 | topics/AGENTS | 对应目标、REQUIREMENTS、TOPIC、计划与原始证据 |

## 验证实际读取

从新独立只读会话打开根入口，要求找出规则住所、基线与运行状态差别、冷件反查、当前与历史区分。
记录真实工具读取路径、范围、答案、遗漏。选中的现行文档全文读。反向测试应能识别断链或指到旧快照。
静态链接通过不证明宿主自动注入；子 agent 自述也不算宿主证据。测量选定祖先入口与必读正文，核宿主捕获内容中是否缺失。
子会话默认不自动遍历本索引；负责人用短任务包提供适用约束和指定原文，保留审计独立性。

## 执行兼容边界

本组织模板保留最新的减负方向：普通写法与手续只提示、不逐条交账；不能靠历史入口恢复旧义务。
公共执行引擎当前仍使用已发布的监控检查及审计报告协议。原工作区最新的监控默认关闭、审计撤七项填表与逐字段小修授权，并未因复制目录而自动改变公共执行代码。
需要按最新减负政策执行时，先在实际引擎完成对应迁移与回归；不能只改本页或绕过检查。本次组织安装不授予实验或平台权限。
