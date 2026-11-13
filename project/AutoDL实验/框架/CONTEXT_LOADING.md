<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-13（当前框架同步）
PURPOSE: 人读层级和机读触发路由
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 2026-11-13 接入观察者、补足推进器与当前读取导航。 2026-11-09 恢复本地中文目录，工具按明确布局标记适配； 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
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
| 接手专题 | AutoDL实验/实验分析/topics/AGENTS | 对应目标、REQUIREMENTS、TOPIC、计划与原始证据、MECHANISMS、WAITING、context_budget |

## 验证实际读取

从新独立只读会话打开根入口，要求找出规则住所、基线与运行状态差别、冷件反查、当前与历史区分。
记录真实工具读取路径、范围、答案、遗漏。选中的现行文档全文读。反向测试应能识别断链或指到旧快照。
静态链接通过不证明宿主自动注入；子 agent 自述也不算宿主证据。测量选定祖先入口与必读正文，核宿主捕获内容中是否缺失。
子会话默认不自动遍历本索引；负责人用短任务包提供适用约束和指定原文，保留审计独立性。

## 执行兼容边界

本组织模板保留最新的减负方向：普通写法与手续只提示、不逐条交账；不能靠历史入口恢复旧义务。
公共执行引擎已同步监控默认关闭与审计减负，版本及验证见[同步记录](<../../../docs/FRAMEWORK_SYNC.md>)。默认不创建监控开关，历史账保留；只有用户明确启用后才恢复监控义务。普通写法与手续不阻断研究，授权、安全、执行真实性和重大研究缺陷仍不能绕过。
文档模板也随安装复制。新专题由工具从版本模板创建三份骨架，旧专题用documents补缺；骨架不是已验收报告。本次组织安装不授予实验或平台权限。
