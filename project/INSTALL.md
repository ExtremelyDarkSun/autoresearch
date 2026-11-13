<!--
GENERATED-BY: portable recovery integration, 2026-11-09 +08:00
LAST-EDIT: 2026-11-13 +08:00（§1 目录树补 WAITING.md 与 MECHANISMS.md 安装落点）
PURPOSE: 新电脑部署固定结构及恢复现有实现的操作入口。
ORIGIN: maintained installation structure and verified public CLI interfaces
CUMULATIVE-DELTA: 区分新项目初始化和旧实现恢复，补实际参数生成、私有恢复包、逐项验收。
  2026-11-13 目录树写明 AutoDL实验/实验分析/ 下两份跨专题文档：WAITING.md（挂起事项唯一落点）与
  MECHANISMS.md（跨专题机制清单）；都随模板安装、进安装回执，不放在任何专题目录里。
-->
# 安装与恢复

如果要开一个新研究项目，按第1节初始化。如果要保留旧机当前的框架实现和专题，按第2节恢复私有包。`init`会建立空库，不能用它覆盖旧工程，也不能用空库替代旧实验记录。

## 这份文档是什么

**新电脑怎么把环境立起来**：分「开新项目」与「恢复旧工程」两条路，只写能照抄的步骤。
读者：换机器、重装、或要把工程搬到另一台机的人。时机：部署前，以及恢复失败时。

## 里面放什么

- 两条路的操作顺序，以及每个命令的期望结果
- 参数从哪来（只给占位符，不放本机实际值）
- 逐项验收：装完用什么证明真的装好了
- 恢复私有包时的保留边界

## 不放什么（放去哪）

| 不写 | 写到哪去 |
|---|---|
| 本机机器名、端口、token 路径、绝对路径 | 本机私有配置，不入仓库 |
| 研究规则与研究纪律 | `RULES.md` 与各线入口 |
| 题库、模型、实验结果、现有结论 | 研究工作区，不随公开仓库提供 |
| 「当前版本是多少」这类会漂的值 | 版本矩阵或锁文件 |

## 一条判据：这条信息该放哪

它决定**怎么把环境立起来** ⇒ 住这里。它决定**怎么做事** ⇒ 不在这里，去 `RULES.md`。

## 1 新项目与固定目录

1. 在新电脑安装Git、Python 3.11或以上、Node.js 24或以上、PowerShell 7和DSH。若要读取DSH压缩日志，Node.js必须支持zstd。加密飞书收件另需`cryptography`。
2. 克隆框架仓库。保留这个仓库，工作区的工具和部分文档引用它。
3. 在框架仓库根执行以下命令。目标目录必须尚不存在。

```powershell
python -X utf8 -B research.py init --workspace ../my-research
python -X utf8 -B research.py layout-check --workspace ../my-research
python -X utf8 -B research.py selftest --plugins
python -X utf8 -B tools/prepare_new_machine.py --workspace ../my-research
```

新工作区沿用原组织：

```text
my-research/
├─ AGENTS.md、RULES.md、DOC_MAINTENANCE.md、INSTALL.md
├─ Kaggle平台/               平台资料、工具、提交记录
├─ AutoDL实验/
│  ├─ tool/                  实验工具入口
│  ├─ 实验记录/              规则、基线、运行、成本、环境手册
│  │  ├─ 历史/               已写完的独立记录
│  │  └─ environments/       工具按需创建的专题环境
│  ├─ 实验分析/
│  │  ├─ WAITING.md           挂起事项的唯一落点（等审批 / 等读数 / 等机器）
│  │  ├─ MECHANISMS.md        跨专题机制清单（过程指标 / 适用边界 / 结论与证据）
│  │  ├─ topics/             专题主文档、证据、研究页和快照
│  │  ├─ worktrees/          工具按需创建的代码副本
│  │  └─ results/            原始运行产物
│  └─ 框架/                  契约、角色、模板、读取路由
│     └─ state/plans/        框架级事前计划
├─ install/                  插件策略、私有配置格式与恢复入口
├─ archive/、data/、submission/、temp/
└─ .autoresearch/            私有运行库、布局标记和机器回执
```

台账没有真实记录，但保留章节、表头、模板行、字段语义和证据要求。模板行不是已完成记录。新专题由`workspace topic`从版本模板建立三份文档，开始推进时再填写研究内容。

布局标记写在`.autoresearch/workspace-layout.json`。新工作区使用中文路径。旧版无标记工作区保持原路径，不自动移动已有实验。显式`init --layout bundle`只提供旧式单文件指令布局。

## 2 已有机器的私有恢复包

公开仓库只含通用实现和模板。旧机当前实现、未提交代码、专题正文和数据库必须用私有包保留。凭据、数据集、模型与提交包单独配置或备份。不能把这些内容上传公开GitHub。

在旧机的框架仓库根运行以下命令。`--workspace`填旧项目绝对路径。`--archive`指向框架仓库`.local`内尚不存在的ZIP。

```powershell
python -X utf8 -B research.py recovery export --workspace C:/Research/current-project --archive .local/current-framework.zip
python -X utf8 -B research.py recovery verify --archive .local/current-framework.zip
```

导出读取当前工作树，不以Git HEAD代替未提交代码。SQLite使用backup取得各数据库的一致性快照。普通文件逐个核字节，整个目录不是跨文件事务。导出前结束文档维护与结构调整，避免同时改动相关文件。运行实验的实际终态仍需另核。

默认排除凭据、DSH全局目录、数据集、提交包、根归档、临时产物、虚拟环境及node_modules。`--include`可显式选择非凭据材料。`--exclude`可排除不需迁移的大型产物，排除优先。恢复清单会列出遗漏项。源代码若依赖某个被排除的文件，必须另行取得并核版本，不能据此宣称已完整恢复。

把ZIP与导出回执复制到新电脑。在新电脑框架仓库根先校验，再恢复到尚不存在的目录：

```powershell
python -X utf8 -B research.py recovery verify --archive .local/current-framework.zip
python -X utf8 -B research.py recovery restore --archive .local/current-framework.zip --target ../restored-project
```

恢复不会把旧数据库导入新内核。它保存旧工程的路径与实现，继续使用旧工程自己的工具。默认不自动续跑、装插件、注册任务或连接云机。Git历史与工作树的重建须显式请求`--rebuild-git`，再核HEAD、分支与未提交代码。

原工程恢复后，在公共框架仓库根生成它的新机部署输入：

```powershell
python -X utf8 -B tools/prepare_new_machine.py --workspace ../restored-project --original
```

该模式保留原`topics.sqlite`与`temp/autodl_events`队列，给出原`topic_watchdog.py --availability-only --all --once --root <新路径>`的任务argv。缺原库时直接拒绝，不建立新库替代。不要把新项目worker的数据库和队列接到旧实现。

旧会话、leader绑定、资源租约、预算授权和云机状态不因字节恢复而重新有效。逐项检查后，用新会话重绑。虚拟环境在新路径重新创建并核安装包。DSH profile及计划任务按新路径重建，不直接复制旧绝对路径。

## 3 宿主与插件

[安装配置](<install/README.md>)提供当前项目的显式过滤策略：保留全局偏好，对子会话过滤四份项目入口。主会话不裁。负责人交接任务相关约束，独立审计仍读完整材料。

`prepare_new_machine.py`只生成`.autoresearch/setup/deployment.json`，并给出下一条生成profile片段的argv。它使用当前框架和工作区的真实路径。重复执行内容一致时幂等，已有不同配置时拒绝覆盖。

生成后的profile片段仍须按框架仓库的`docs/dsh.md`装载。装载后核插件apply日志、新宿主pid、主会话输入与子会话输入。文件存在和自检通过不证明宿主已加载。doc-gap默认关闭，不能从旧安装说明推断当前启用。AutoDL事件无会话绑定时hold，不随机挑会话。

## 4 新电脑验收

| 项目 | 通过证据 | 未通过时 |
|---|---|---|
| 目录与文档 | layout-check通过，专题在中文路径，台账有表头和模板 | 修目录和链接，不覆盖旧项目 |
| 当前实现与资料 | 私有包verify通过，恢复字节/hash一致，SQLite快照可读 | 查缺件与损坏，不导入新库冒充迁移 |
| 源码与环境 | HEAD及未提交代码核对，解释器和依赖重新探测 | 不把复制的venv当可用环境 |
| 主会话与子会话 | 捕获实际输入，主会话完整，子会话保留全局并过滤项目入口 | 查插件加载与路径匹配 |
| 研究行为 | 监控默认off、启用后生效、三项审计、小修不计次数、scribe独立写页 | 对照当前契约和真实回执 |
| 定时状态与事件 | 两tick更新process_status而不改STATUS，绑定后只投一个会话 | 缺绑定或数据写unknown/hold |
| 实际执行 | 一个本地样例、一个正式评测样例、真实停止和费用核对 | 禁止把模拟测试当现场通过 |
| 外部通道与提交 | 新机凭据、可信授权、送达及平台回读 | 未配置就保留未验，不自动提交 |

最终恢复结论须逐项附新电脑产生的证据。旧电脑和离线测试只能验证准备工作。框架仓库的`docs/NEW_MACHINE_RECOVERY.md`记录当前已验项和剩余现场项。
