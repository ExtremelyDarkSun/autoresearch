<!--
GENERATED-BY: portable project organization
LAST-EDIT: 2026-11-09
PURPOSE: 维护入口不复制规则正文
ORIGIN: maintained layered workspace organization, neutral extraction
CUMULATIVE-DELTA: 固定项目组织模板；实例事实留空，现行规则直接链接唯一正文。
-->
# 文档维护入口

完整正文只有一份：[维护契约](<../kernel/contracts/doc_maintenance.md>)。涉及维护时整份读取，不以本入口替代。

## 这份文档是什么

**维护入口**：告诉你「要维护什么」时该翻哪份正文，本身不存规则。
读者：要切台账、滚冷件、改读取路径的人。时机：写文档与做结构维护之前。

## 里面放什么

- 一张「维护工作 → 正文落点」的表
- 三条最容易搞错的性质：冷件不是退休归档、改前快照不是冷件、结构改完必须用独立只读会话试真实读取

## 不放什么（放去哪）

| 不写 | 写到哪去 |
|---|---|
| 上限数值、滚动时机、冷件命名细节 | 维护契约正文 |
| 本项目当前哪些台账已登记为活跃 | 维护清单 |
| 某一份文档的实际内容 | 它自己的文件 |

## 一条判据：这条信息该放哪

它是**关于怎么维护的规则** ⇒ 正文在维护契约；它只是**告诉你去哪读** ⇒ 放这里一行。

| 工作 | 正文落点 |
|---|---|
| 高频文档按状态与时间滚动、默认上限、留证、冷件索引 | [文档维护](<../kernel/contracts/doc_maintenance.md>) |
| 选择文件、整份读取、重读、独立派工 | [读取政策](<../kernel/contracts/reading_policy.md>) |
| 当前项目入口与触发范围 | [加载图](<AutoDL实验/框架/CONTEXT_LOADING.md>) |
| 本项目哪些文档已登记为活跃台账 | [维护清单](<AutoDL实验/框架/MAINTENANCE_REGISTRY.md>) |
| 迁移来源、保留规则和空内容边界 | [组织抽取映射](<ORGANIZATION_MAP.md>) |

冷件不是退休归档。按条目编号反查；改前快照也不是冷件。结构维护后须用独立只读会话测试真实读取路径。
