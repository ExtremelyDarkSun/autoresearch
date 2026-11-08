<!--
GENERATED-BY: repository creation, 2026-11-09 +08:00
LAST-EDIT: 2026-11-09 +08:00
PURPOSE: 新仓库的研究行为入口与完整读取要求。
ORIGIN: generic research and document-maintenance mechanisms
CUMULATIVE-DELTA: 分开内核、目标配置与实例；完整读取优先；默认无外部部署。
-->
# 研究项目入口

本仓库提供可复用的研究机制和 agent harness 目标配置。工作区、真实数据、结果和部署参数另建。

动手前，先查现有要求和未完成工作。读取 `kernel/instructions/` 与 `kernel/contracts/` 中适用的现行正文，再合并所选 profile 的 `[instructions].required`。根入口不取代这些正文。

使用 `python research.py instructions` 测量完整读取材料。需要注入材料时，用 `--bundle` 生成完整文本。预算必须容纳全部材料与包装；配置后读取宿主实际请求并用 `--captured` 核对。没有核对就不能宣称已完整注入。不得为了节省字数静默删除内核或 profile 义务。

主会话负责研究方向、执行、结论和下一步。委派是可选机制。角色文件存在不表示真实派出会话；记录真实宿主证据。人读报告与机器工作材料分开，结论附条件、原始证据和未解决问题。

修改文件前，保留待改版本的可核证据；用 Git 或显式归档留住来源。未知读数写未知；通过机械检查不表示科学假设成立。维护索引和读取义务时同步测试，不把历史归档当现行规则。

本地测试使用合成临时夹具。外部评测、云机、消息发送与平台提交需要配置对应适配器及实际授权政策。用户的停止要求优先。超时与没有回复不能视为批准。
