<!--
GENERATED-BY: repository creation, 2026-11-09 +08:00
LAST-EDIT: portable framework synchronization, 2026-11-09
PURPOSE: 新仓库的研究行为入口与完整读取要求。
ORIGIN: generic research and document-maintenance mechanisms
CUMULATIVE-DELTA: 2026-11-09 固定分层项目模板与默认初始化，保留显式bundle兼容；2026-11-09 加入完整行为预期与迁移缺口的直接入口；保留完整读取及研究报告规则。
-->
# 研究项目入口

本仓库提供可复用的研究机制和 agent harness 目标配置。[固定项目组织](<project/AGENTS.md>)随仓库交付；`init` 默认确定复制分层目录，不让 AI 重新设计。工作区、真实数据、结果和部署参数另建。

**迁移尚未完成。** 开局整份读取[框架行为预期](<kernel/contracts/framework_expectations.md>)；核对已实现范围时读取[功能迁移验收表](<docs/EXPECTATIONS_ACCEPTANCE.md>)。该表逐项保留缺口；生成指令、登记资料或局部测试通过均不能代替实际功能验收。

动手前，先查现有要求和未完成工作。读取 `kernel/instructions/` 与 `kernel/contracts/` 中适用的现行正文，再合并所选 profile 的 `[instructions].required`。根入口不取代这些正文。

使用 `python research.py instructions` 测量完整读取材料。需要注入材料时，用 `--bundle` 生成完整文本。预算必须容纳全部材料与包装；配置后读取宿主实际请求并用 `--captured` 核对。没有核对就不能宣称已完整注入。不得为了节省字数静默删除内核或 profile 义务。

主会话负责研究方向、执行、结论和下一步。执行任务的委派可选。方案审计须独立新会话，人读页正文须实际派出 scribe 会话。角色文件存在不表示真实派出会话；记录真实宿主证据。人读报告与机器工作材料分开，结论附条件、原始证据和未解决问题。

修改文件前，保留待改版本的可核证据；用 Git 或显式归档留住来源。未知读数写未知；通过机械检查不表示科学假设成立。维护索引和读取义务时同步测试，不把历史归档当现行规则。

本地测试使用合成临时夹具。外部评测、云机、消息发送与平台提交需要配置对应适配器及实际授权政策。用户的停止要求优先。超时与没有回复不能视为批准。
