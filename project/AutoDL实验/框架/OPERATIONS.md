<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-09
PURPOSE: 实际工具入口与固定路径适配
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
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

固定工具路径不改：专题正文在根 `topics`，运行库在 `.autoresearch/runtime.sqlite3`。中文实验分析目录只索引，不复制专题正文。
历史终点检查不恢复为必跑义务。部署未配置就写未配置；工具建议命令不表示已执行。
