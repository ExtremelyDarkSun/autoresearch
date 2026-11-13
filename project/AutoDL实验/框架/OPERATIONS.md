<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-13（当前框架同步）
PURPOSE: 实际工具入口与固定路径适配
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 2026-11-13 接入观察者、补足推进器与当前读取导航。 2026-11-09 恢复本地中文目录，工具按明确布局标记适配； 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
-->
# 操作入口

从框架仓库运行 `research.py`，每个动作显式传 `--workspace <项目目录>`。
所有参数以当前 `--help` 为准；这里不复制第二套命令协议。

| 任务 | 主入口 |
|---|---|
| 初始化固定项目与检查链接 | `init`、`layout-check` |
| 创建专题代码、环境与文档；快照 | `workspace topic`、`workspace snapshot` |
| 事前材料、实际启动、结束与复盘 | `workflow` |
| 独立审计与可核文档登记 | `governance` |
| 写研究页供料与验收 | `reporting` |
| 准入、共享预算、真实审批 | `budget`、`decisions` |
| 远端运行与租约 | `remote` |
| 下一轮只读建议 | `progress preflight`、`progress next-round`、`progress schedule` |
| 工程问题、普通提问 | `issues`、`questions` |

新工作区的专题正文在 `AutoDL实验/实验分析/topics/`，代码副本在同级 `worktrees/`，环境在 `AutoDL实验/实验记录/environments/`，计划在 `AutoDL实验/框架/state/plans/`。布局由 `.autoresearch/workspace-layout.json` 固定，不按目录是否存在猜测。旧无标记工作区保持原路径，不自动搬迁。运行库仍在 `.autoresearch/runtime.sqlite3`。
历史终点检查不恢复为必跑义务。部署未配置就写未配置；工具建议命令不表示已执行。

持续专题默认用带 `[观察者]` 前缀的原生 goal，推进器同时挂成外部事件补足器。建目标、resume、额度、停止和每跳重挂的完整步骤见[观察者与推进器](<../../../docs/dsh-goal-observer.md>)，不从旧快照恢复已废止口径。
挂起事项统一放[等待簿](<../实验分析/WAITING.md>)；设计下一轮前整份读[跨专题机制](<../实验分析/MECHANISMS.md>)。
