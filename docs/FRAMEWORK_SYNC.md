<!--
GENERATED-BY: portable latest framework synchronization
LAST-EDIT: 2026-11-09
PURPOSE: 最新源规则、可复用实现及本次验证范围。
ORIGIN: current upstream contracts, role prompts and topic scaffold; neutral extraction
CUMULATIVE-DELTA: 监控默认关闭、审计三项重点、判据来源可选、三份专题文档创建；保留109编号顺序。
-->
# 最新框架同步

这次同步同时更新现行文档、执行入口和测试。源工作区只读，公开仓库不包含私人实验数据、结果、旧授权、凭据及会话或机器身份。

| 更新 | 公开版行为 |
|---|---|
| 意图监控默认关闭 | 缺开关、坏JSON或非布尔值都关闭。只有用户明确要求后写顶层enabled=true才启用。关闭时事件不写账，不要求初始化、逐条问意图或派监控；历史active/blocked账保留且不拦起跑 |
| 审计减负 | focus只抓目标连接、设计实现测量的基本正确性、会改变决定的历史限制。coverage可省，不要求固定三组检索、ok逐项举证或unknown必建issue |
| 小修自查 | correct不占两次审计额度，不需逐字段minor_fix_paths授权。绑定字段的纯文字修正须声明wording_only。真实设计、执行参数、题单和实现文件身份或字节变更仍使旧审计失效 |
| 程序生成短声明 | audit-declaration从完整报告生成带报告字节SHA256的六键声明，登记核声明与文件一致；对用户讲关键理由，报告保留必要分析 |
| 研究判据 | source可选；新模板默认exploratory，有观察、解释边界、条件与停止条件，不强制数字阈值。确认性仍保留门槛和理由。旧计划缺mode继续按旧确认性解释 |
| 三份专题文档 | 新专题使用版本模板创建TOPIC、日期EVIDENCE与STATUS。旧专题documents只补缺失类别，已有证据页不另造当天空页，已有文件不覆盖。骨架标记不能通过研究报告验收 |
| 分层组织 | 固定project组织不变；templates与契约、指令和角色一起复制到生成工作区，链接重写并纳入来源回执 |
| 读取证据 | 规则要求完整实际返回；requested offset/limit、摘要、注入声明不能证明整份已读。尾截、中间缺号、单行截断均需补齐 |

命令行也不再在关闭监控时强制意图参数；本地、远端和下一轮预检都按同一开关判断，启用后仍需声明。

独立审计、scribe研究写页、两次审计额度、真实授权与预算、代码隔离、冻结argv和安全停机要求继续有效。审计前先本地读码排错，先测1题冒烟再放大；这项质量判断仍由研究者和独立审计者负责，结构检查不能证明已经做对。

现状页保留研究八节与假设→判据→设置→结果，最多最近三轮并包含当前实验。机器周期、心跳和审计额度留流程文件。空骨架不代表没有历史数据，也不代表分析完成。

源码入口：[监控](<../kernel/autoresearch/monitoring.py>)、[研究主链](<../kernel/autoresearch/workflow.py>)、[审计](<../kernel/autoresearch/governance.py>)、[文档生成](<../kernel/autoresearch/topic_documents.py>)、[专题命令](<../kernel/autoresearch/workspace_cli.py>)。操作见[运行手册](<RUNNING.md>)；具体规则只在[监控契约](<../kernel/contracts/intent_monitor.md>)、[审计契约](<../kernel/contracts/plan_audit.md>)、[现状页契约](<../kernel/contracts/status_page.md>)与[读取政策](<../kernel/contracts/reading_policy.md>)维护。

## 验证记录

【实测】完整selftest --plugins退出码0：Python共1,445项（1,434通过、11项按平台跳过），三个插件221条断言通过。109个编号与源表顺序逐项对比一致，保留原编号而更新其现行语义。[逐项验收表](<EXPECTATIONS_ACCEPTANCE.md>)保留真实部署未验项目。

【实测】全新分层安装通过：95个安装文件的哈希与字节数均核对，本地链接、12条路由路径及14条必读路径通过，初始化运行库为空。公开documents命令重复调用不覆盖旧TOPIC，也不重复建立EVIDENCE；空STATUS骨架被拒验收。源码未被安装器修改。

源版本记录在[中性指纹回执](<framework-sync-sources.json>)。只保留标签、字节数与SHA256。源预期表SHA256为`07ee2c49ddd9568ef5dcee4e0d24ced924e47a1697699363e6ba611d3a11e9a7`。

当前公共宿主读证据核来源与版本，尚未实现源读取闸的逐行覆盖判定。没有把文档规则写成该功能已自动实现。真实云机、SSH、通道租户、平台提交及宿主自动注入未在本轮验证。
