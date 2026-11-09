<!--
GENERATED-BY: framework synchronization
LAST-EDIT: 2026-11-09 本批：review v2、issues exp、questions、assurance、progress 跨轮预检与 D2 回复绑定
PURPOSE: 当前框架升级的迁移范围与边界。
ORIGIN: current source mechanisms, neutral extraction
CUMULATIVE-DELTA: 研究、审计、读取、报告、继续交接、普通提问、检查清单与进度推进同步；通用补项与部署未验分开。
-->
# 框架升级同步

这次从当前框架工作文件核对升级，不只依据上一次 Git 提交。原始内容指纹见 [同步清单](upgrade-sync.json)。清单只保存中性组件名和哈希，不附带真实实验、部署设置或会话记录。

| 升级 | 同步落点 |
|---|---|
| 探索性实验不编数字门槛 | plan 校验、JSON schema、方案模板与作者指令 |
| 三态审计和三个审查重点 | 报告校验、模板、独立审查指令与角色 |
| 小修自查不重审 | correct API / CLI、按问题授权叶子、硬实质不可豁免 |
| 报告文件与短声明绑定 | 原始字节 SHA256、六键声明检查、audit-file / audit-digest |
| 跨轮重大问题不消失 | 全部历史 blocking 持续检查，复审 resolves 或可信用户 waiver 关闭 |
| 审计额度不随改版本归零 | 每 plan 累计首审及最多一次增量复审 |
| 研究页与流程状态分离 | 研究八节、每实验四段、最近 3 轮、scribe 必派、机器回执承载版本 |
| 相关现行文档整份读取 | 读取政策、清单 full_document、完整材料生成与范围检查 |
| 专题研究边界 | 停止条件和解释范围属于研究内容，调度节奏与停机属于流程契约 |

旧页只读兼容使用 `validate_status_page(text, legacy=True)`。旧审计 JSON 调用方仍可登记旧格式，按旧的 unknown 阻断规则校验。新模板及文件短声明入口要求新三态。历史事件不改写。

当前迁移是机制拆分。上游与私有实例绑定的报表脚本、周期监控器、真实消息日志验证器不直接复制。其规则进入内核契约与通用方法，机器清单、时刻、当前结论和个人通道不进入内核或 agent 比赛配置。

以下是早期抽取批次的记录；当前可运行功能与未验边界以[迁移功能验收](<EXPECTATIONS_ACCEPTANCE.md>)和[验收记录](<VERIFICATION.md>)为准。当前已增加真实专题隔离、流程状态投影、可信 DSH 日志核验、研究执行主链、通道收件和受管远端入口。

已经抽取的事务运行时在抽取时按原始哈希逐件核对，随后加入通用预算与发送前复核。三个插件继续随内核提供。`.autoresearch/state/process_status/` 的本地投影已由生命周期服务生成；真实远端观测与宿主持续运行仍需部署配置和现场验收。

当前可信 DSH 日志适配器已核真实独立派工绑定、报告原文字节和 scribe 写页回演；错误 seq 不会自动改写成授权。生产通道租户与远端现场结果仍未验。声明登记本身不能替代可信来源核验。

## 本轮补齐

本轮以 `d764ce3` 为基线复核。此前清单中的 16 份来源均未变化。本轮扩大来源范围，补齐此前未提取的现行规则。指纹清单覆盖框架入口、代码、契约、角色、工具与插件；哈希盘点不表示这些源文件都被复制为可运行实现。

- 探索性方案必须写 `criteria.stop_condition` 与 `criteria.source`。仍不要求数字门槛。已有探索性方案在再次校验或冻结前须补齐两项。历史数据库记录不改写。
- planner 不再要求所有实验写数字门槛。停止条件属于判据实质，变化须重新审计，不能通过小修授权绕过。
- 机制是否执行与实现是否值得采用分开判断。不能凭失败实现的数量判整个方向无效。
- 条件化结论保留收益、代价和解释范围。整批预算检查区分保守上界与实际耗时风险。
- 内核增加上下文纪律：交付一次、例外复核、规则集中发布、单一推进来源、核实停止工作的理由。没有增加生产守护程序或自动日志审查器。
- agent 配置补齐工具替换要求：保留桩或同步移除宣传，检查替代路径成本和结果。特定比赛的历史实验数据不进入通用规则。
- 主会话指令明确：有有效专项意图时才派监控，执行被拦时继续不依赖该执行的已授权工作。

## 本批补齐（2026-11-09）

| 能力 | 落点 | 边界 |
|---|---|---|
| E5 复盘继续交接 | `review_continuation.py` + `workflow.review` schema2 | 要求下一步判据/证据、`cost_scope`、`next_owner`、`needs_approval`；公开 review 默认 require2，旧格式 `--legacy-record` 且缺字段 unknown；等待批准是合法交接，不等于已批 |
| E2 证据关联实验 | `issues.py --exp` / `status --exp` / `link`，`assurance` | `exp` 进查重键；旧无 exp 记录 unknown、显式空 unlinked；`link` 追加事件不改写 |
| E4 统一检查清单 | `assurance.checklist` / CLI | 只读聚合 workflow/monitoring/decisions/issues/questions/reporting/review.continuation；含 audit / budget reservation / remote lease，共16项；未启动复用共享准入与资源投影，已启动展示历史绑定；检查不替代启动事务 |
| E7 普通主动提问 | `questions.py` / CLI | 独立于审批；答复只认 Inbox 认证事件；默认 `blocking=false`，不是 D2；真实租户未验 |
| D2 入向超期闸 | `progress.outcome advance` + `decisions.reply_status` / `progress_host.read_overdue` | 超期或不可判拒绝 advance；回复按 `source + event_id` 配对，同号异源不串，未指定 source 旧回执不算回复，非有限时间/grace 判不可判 |
| A1 / §0.6 下一轮与阶段 | `progress preflight` / `next-round` / `schedule` | 只读给真实命令，不自动起进程、不新增 daemon；候选与 stage 显式或取冻结计划字段，不扫描生成计划；真实宿主唤醒/激活与机器空闲未验 |

专项测试已过：E5 review v2 14 项、workflow 63、lifecycle 13、冷启动 6（已升 require_version2）、Questions 39、Issues 34、assurance 39（含冻结预算绑定与租约阶段）、progress advancement 29 + 原 progress 32、remote helper 17 + 原 47、D2 回复绑定 5 + decisions 47 + progress_host 34。这些不是最终聚合总数；完整计数以[验收记录](<VERIFICATION.md>)为准。真实租户、SSH/POSIX worker、AutoDL/比赛、模型输入注入与 goal activation 仍未验。

## 验证范围

验证使用合成计划、临时数据库、模拟宿主与本机服务。没有运行真实比赛评测、没有更改云机或当前宿主。自动检查仍不能判断科学结论是否成立。
