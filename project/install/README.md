<!--
GENERATED-BY: portable organization repair, 2026-11-09 +08:00
LAST-EDIT: 2026-11-09 +08:00
PURPOSE: 新电脑的安装配置入口及组件清单。
ORIGIN: existing three-plugin kernel and maintained project installation structure
CUMULATIVE-DELTA: 将部署配置归回install，提供四份项目入口过滤策略及明确验收。
-->
# 安装配置

安装步骤在[根安装说明](<../INSTALL.md>)。此目录存放可复用部署参数、恢复说明和私有配置的占位格式。插件源码仍只有内核中的一份，不复制第二套实现。

| 组件 | 公开内容 | 新电脑要填什么 | 验收 |
|---|---|---|---|
| 子会话入口过滤 | [四入口策略](<agent-instruction-filter.policy.json>) | 实际项目根与插件位置。主会话保留，子会话过滤 | 插件加载证据及真实子会话输入 |
| AutoDL事件 | 内核插件与worker | 实例配置引用、专题与新会话绑定、任务频率 | 无绑定不发，绑定后只投一个目标 |
| 文档遗漏提示 | 内核doc-gap插件 | 默认关闭。需要时显式配置文档范围与判据 | 启用和关闭各测一次，不冒充当前已加载 |
| 私有配置 | [字段约定](<private/README.md>) | 只在本机填写凭据引用与部署值 | 文件不进入Git，doctor不回显值 |
| 当前机器恢复 | [恢复说明](<../INSTALL.md#已有机器的私有恢复包>) | 在旧机导出，在新机验证后恢复到新目录 | 包内逐文件hash及真实宿主、运行入口现场验收 |

## 子会话过滤约定

只在指定工作区启用 `mode: filter`、`childOnly: true`，`policyPath`指向本目录的策略JSON。四份项目入口整份drop，其余默认keep。负责人必须交接任务相关约束与指定源文档。独立审计仍需读完整审查材料，不能靠删掉输入放行。

生成配置使用内核的 `gen-profile-patch.mjs`。生成器不会安装插件、写入宿主profile或重启宿主。安装后用 `check-loading.mjs` 和实际会话日志验证。
