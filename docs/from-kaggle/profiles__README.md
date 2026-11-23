<!-- 来源：Kaggle 工作区 `profiles/README.md`
     sha256=7523f6f4fc6b8368…  12468 字节
     由 tools/publish_from_kaggle.py 发布；凭证扫描已通过 -->

<!--
GENERATED-BY: session-f6062ef1abdf (DSH agent, 2026-11-08 21:0x +08:00)
LAST-EDIT: session-f6062ef1abdf (DSH agent, 2026-11-08 21:5x +08:00)（§3 修正 AutoDL 归属：9 个后端工具从配置包移进内核，新增「本机配置」第三类；§3.1 记 run_eval 要拆不能搬）
PURPOSE: 「内核 / 配置包」这一层的唯一契约 —— 一条判据 · 全量归位表 · 工具脚本划分 · 已定决策 · 接线清单 · 改名策略
ORIGIN: none（新文件）。依据：用户 2026-11-08 选定「一个仓库，内核 + 两个配置包」并逐条确认灰区；规模数字来自同日盘查
CUMULATIVE-DELTA: 首版只有判据与四份配置；第二版补 §2 全量归位表、§3 工具脚本划分、§4 五条已定决策；第三版加第 6/7 条（证据全量进仓库 · 执行器先只支持 DSH）与 §4.1/§4.2；本版按用户 2026-11-08 的裁定修正 §3 —— AutoDL 后端属内核（换比赛不用改），只有凭证单列第三类，并记 run_eval.py 是要拆不能搬的文件。
-->

# 内核 / 配置包 —— 这一层管什么

> **这是契约，不是配置。** 本文件写「什么该放在哪」；真正的配置在 `profiles/<名字>/` 下。
> ⚠️ 2026-11-08 现状：**本层尚未接线**（内核还不读 `profiles/`）。接线清单见 §6。

## 1 一条判据

**换一个比赛（或换一个领域），这个文件要不要改？**

| 答案 | 归哪儿 |
|---|---|
| 要改 | `profiles/<比赛>/` |
| 不改 | 内核（现在叫 `AutoDL实验/框架/`，改名计划见 §7） |
| 都不是 | 历史层 `archive/`、工作区 `temp/` —— 不参与这次分层 |

⚠️ 只留这一条。多一条判据就会出现「两个文件都像是对的」，那是漂移的起点。

## 2 全量归位表

### 2.1 根

| 项 | 归 | 理由 |
|---|---|---|
| `RULES.md`（60 KB） | 内核 | 全局规则：东西放哪 · 文件头五要素 · 证据标注 · 怎么说话 |
| `DOC_MAINTENANCE.md`（28 KB） | 内核 | 文档分层与冷件滚动 |
| `INSTALL.md`（50 KB） | 内核 | 安装与两处 DSH 环境改动 |
| `README.md`（15 KB） | 内核 | 对外介绍——介绍的是这套框架，不是这个比赛 |
| `AGENTS.md`（33 KB） | **配置包** | 它是「Gemma 4 比赛」的索引；内核另写一份通用目录索引 |
| `REORG_MAP.md` | 历史层 | 2026-10-07 那次重组的旧地图，一次性 |
| `install/`（28 个文件） | 内核 | 两个 DSH 插件，可选装、可回滚 |

### 2.2 `AutoDL实验/`

| 项 | 归 | 理由 |
|---|---|---|
| `框架/`（code · contracts · roles · 六件文档） | **内核** | 八段流程、闸、台账、报告生成 |
| `tool/`（56 个脚本） | **拆** —— 见 §3 |
| `实验记录/`（67 个文件） | 配置包 | 这个比赛的证据：实验日志、基线、规则里带 12 小时预算的那些 |
| `实验分析/`（1,299 个文件） | 配置包 | 这个比赛的读数与专题 |
| `AGENTS.md`（36 KB） | 配置包 | 实验线规矩，写满了这个比赛的机器、卡型、题单 |
| `README.md` | 配置包 | 同上 |
| `archive/` | 历史层 | 已结案的冷件 |

### 2.3 `Kaggle平台/` 与别处

| 项 | 归 | 理由 |
|---|---|---|
| `Kaggle平台/`（5,135 个文件） | 配置包 | 官方文档 · 讨论区抓取 · 实验报告 · 抓取工具 |
| `submission/`（10 个文件） | 配置包 | prompt · skill · `eval_config.yaml` |
| `data/`（532 个文件） | 配置包 | 题集 · harness 说明 · 沙箱 · wheels |
| `archive/`（84,010 个文件） | 历史层 | 不进内核也不进配置包 |
| `temp/`（66,791 个文件） | 工作区 | 一次性的，随时可清 |

## 3 工具脚本的划分（实测，非估计）

`AutoDL实验/tool/` 下 56 个非测试脚本，**四分**：

| 归 | 数量 | 名单 |
|---|---|---|
| **内核 · 流程** | 22 | `exp_launch` · `exp_ledger` · `plan_audit` · `plan_audit_host` · `intent_monitor` · `topic_watchdog` · `loop_driver_job` · `watch_inbox_job` · `status_delivery` · `status_pack` · `ai_work` · `progress_probe` · `history_inputs` · `channel_census` · `make_sessions_index` · `check_worktree_ledger` · `check_file_headers` · `fingerprint_framework` · `check_stall` · `collect_run` · `judge_wall` · `probe_running` |
| **内核 · 执行后端（AutoDL）** | 9 | `autodl_availability` · `autodl_events` · `machine_probe` · `machine_usage` · `rsh` · `upload_subset` · `fetch_run` · `l8_capture_endpoint` · `l8_request_identity` |
| **本机配置（不进 git）** | 3 | `extract_autodl_token` · `scan_all_browsers` · `scan_chrome_autodl` —— 只负责**取凭证**，取到的值住在项目外 |
| **配置包** | 20 | `run_eval` · `run_eval_probe` · `trace_judge` · `agent_usability_gate` · `check_driver_sha` · `uid_marker_selftest` · `env_change_ledger` · `baseline_judge` · `make_stripped_tasklist` · `compare_runs` · `budget_replay` · `exp041_static_preflight` · `fp2_measure` · `fetch_run_selftest` · `merge_runs` · `audit_run5` · `audit_run5b` · `audit_graphs_resolver` · `audit_runtime` · `quarantine_oracle` |
| **历史层** | 2 | `_probe_zip` · `_verify_data_matches_zip` |

⚠️ **2026-11-08 修正过一次**：上一版把 9 个 AutoDL 工具判进了配置包，**错了**。判据是「换一个比赛要不要改」，而换一个比赛时 `autodl_availability` 一个字都不用改 —— 它们只在**换平台**时才要改。这是两种不同的变化，上一版混了。

⚠️ **边界项**：`compare_runs` · `merge_runs` · `env_change_ledger` · `judge_wall` 四个，判据是「它读的读数格式是谁的」——读的是这个 harness 的 `task_results.jsonl` 就进配置包，读的是通用台账就进内核。**现在的归属是推断，没有逐条读源码**，接线前要复核。

### 3.1 一个要拆、不能搬的文件

`run_eval.py`（3,709 行）一半是**运行器**（起沙箱、铺快照、跑命令、收结果 —— 内核），一半是**这个 harness 的评测协议**（题单格式、判分口径、12 小时预算 —— 配置包）。两者混在一个文件里，所以它不是「搬到哪」的问题，是**先拆再放**。拆分前 ⛔ 不动它。

## 4 七条已定决策（用户 2026-11-08 逐条确认）

| # | 决策 | 落地注意 |
|---|---|---|
| 1 | **飞书通道进内核，但默认关** | 机制通用、默认停用；别人不配就用不了。回滚开关写进 `INSTALL.md` |
| 2 | **审批：机制在内核，「哪些事要人批」进配置包** | 你比赛里「线上提交必须批」是配置包里的策略，内核只说「有审批这回事」 |
| 3 | **必读清单两层合并** | 内核写「改闸前必读契约」，配置包写「改 prompt 前必读比赛规则」；读取时合并，⛔ 不是两份互相覆盖 |
| 4 | **`data/` 进配置包** | 内核要能从配置项知道数据在哪，⛔ 不许写死 `data/` |
| 5 | **根的 `AGENTS.md` 进配置包** | 内核另写一份通用目录索引，否则别人打开就看到一个陌生比赛的代号 |
| 6 | **证据全量进仓库** | `实验记录/` + `实验分析/` 共 1,256 个文件 / 12.8 MB，全给。⚠️ 公开前必须脱敏，见 §4.1 |
| 7 | **执行器先只支持 DSH** | 不做多 runner 抽象。`runner.yaml` 只声明「用 DSH 会话跑」；代价见 §4.2 |

### 4.1 证据全量进仓库 ⇒ 脱敏变成必做项

盘查（2026-11-08）：**447 处机器容器 UUID，散在 139 份文档里**，集中在
`实验记录/SESSIONS.md`（52 处）· `EXPERIMENT_LOG.md`（41 处）· `BASELINES.md`（19 处）·
`EXPERIMENT_RULES.md`（16 处）。机器名（`A23机` 这类）对外人无害，容器 UUID 是实例标识，
公开仓库里没有意义。

⇒ 发布前统一替换成 `<容器ID>`，替换清单落 `profiles/gemma4-harness/scrub_report.md`。
⛔ 不在主线仓库里原地替换 —— 那会把「当时是什么机器」这条证据改掉。

### 4.2 只支持 DSH ⇒ 别人的前置条件变了

别人的门槛从「装一个 Python 包」变成「**先装 DSH 客户端**」。这必须在 `README.md` 第一屏写清，
⛔ 不能等别人 clone 完才发现跑不了。

好处是省掉一整层抽象：`run_chain.py` 第 6 段不用改成可插拔接口，`demo-local` 仍然由一个
DSH 会话驱动、跑 5 道本地 CPU 小任务（不用 Kaggle 账号、不用显卡）。

⚠️ 这一条以后要放开时，接口点是 `run_chain.py` 第 6 段（现在是固定 MOCK）与 `run_eval.py` 的命令行。

## 5 四份配置

配置包只需要四样东西。**字段名沿用 `plan.json` 已有的叫法**，⛔ 不另造一套：

| 文件 | 管什么 | 字段出处 |
|---|---|---|
| `tasklist.yaml` | 跑哪些题 / 哪些任务 | `plan.json` 的 `tasklist`（现在每轮已经在写） |
| `criteria.yaml` | 凭什么算这一轮成立 | `plan.json` 的 `criteria` / `criterion` / `expected_pass` |
| `runner.yaml` | 这一轮由谁执行 | 尚无配置文件；现在是 `run_chain.py` 第 6 段（固定 MOCK）与 `run_eval.py` 的命令行 |
| `submit.yaml` | 怎么把结果交上去 | 现在散在 `Kaggle平台/AGENTS.md` §2.7 与 `submission/eval_config.yaml` |

**题单与判据已经不用新造**：`plan.json` 每轮强制写这两样，且被闸检查。配置包的活主要是把**默认值**从规则文档搬进来，不是新增机制。

## 6 接线清单（下一步要改的代码）

按顺序做，每步独立可验：

| # | 改什么 | 怎么验 |
|---|---|---|
| 1 | 内核加 `--profile` 入口；缺省指向 `gemma4-harness` | 不带参数跑演练，行为与今天逐行一致（`run_chain.py --plan` 输出相同） |
| 2 | `tasklist` / `criteria` 的默认值从 profile 读，不再回落到规则文档 | 用故意写错的 profile，闸必须报错而不是静默取默认 |
| 3 | 执行器：`runner.yaml` 只声明「用 DSH 会话跑」，第 6 段维持现有路径（⛔ 不做可插拔接口） | `demo-local` 用 5 道本地 CPU 小任务，一个 DSH 会话跑完八段 |
| 4 | `submit.yaml` 接管「12 小时预算 / 每日提交次数」两个常量 | 改 profile 里的值，`gate_round_close` 的判词跟着变 |

⚠️ 第 3 步之前不要动 `run_chain.py` 的 MOCK 分支 —— 它是现在唯一能证明「闸会红」的东西。

## 7 改名：留到最后，且保两层

**现在不改名。** 盘查结果：`AutoDL实验/框架` 在 154 份文档里出现 3,894 次；改名是一次纯机械、三天量级、对「别人能不能用」贡献接近零的操作。

要改时按这个形态，**深度不变**：

```
hil/framework/code/...   ← 两层（现在是 AutoDL实验/框架/code/...，也是两层）
```

理由：33 处代码靠 `parents[4]` 定位仓库根目录；保持两层 ⇒ 这 33 处一行都不用动，只剩 68 处路径字符串（61 处 `"AutoDL实验" / "框架"` + 7 处字面量）。

⚠️ 反例：`gate_round_close.py:653` 靠「向上找名叫 `AutoDL实验` 的目录」定位自己 —— 改名时要一起改成找标记文件（如 `.git`），否则它会静默找错根。

## 8 搬家的验收基线（2026-11-08 20:5x 实测）

⚠️ **这一节是判据，不是结论。** 搬家前后必须各跑一次，逐条对比；有任何一条不同就回滚。

| 层 | 退出码 | 断言数 |
|---|---|---|
| `channel/SELFTEST.py` | 0 | 59 条全过 |
| `gates/SELFTEST.py` | 0 | 126 条全过 |
| `report/SELFTEST.py` | 0 | 92 项全过 |
| `orchestrator/SELFTEST.py` | **1** | 崩在 `t_timeout`（`AttributeError: 'NoneType'…endswith`），没打完 |
| `state/SELFTEST.py` | **1** | 96 项过 95，失败 1 条：读不到 `temp/hil/DECISION_LOG.md` |

合计 **372 条断言通过 · 2 条红**（两条红都是 2026-11-08 之前就存在的，不是搬家造成的）。

⚠️ **两条红都不能当"搬家弄坏的"证据，也不能当"没弄坏"的证据**：
- `orchestrator` 那组用例与**当前时刻**有关（夜间超时、迟到回复）⇒ 前测与后测必须**连着跑**（几分钟内），⛔ 不能隔天比。
- `state` 那条依赖一个本机临时文件；它在不在都能搬，但前后要一致。

⚠️ **搬家那次提交必须满足「0 行插入 / 0 行删除」**：`git show --stat --find-renames <sha>` 里框架文件应全是 `R100`。看到任何非零的插入删除行，说明那次提交混进了内容改动，拆开重做。
