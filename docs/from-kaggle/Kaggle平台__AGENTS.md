<!-- 来源：Kaggle 工作区 `Kaggle平台/AGENTS.md`
     sha256=1442bfc572f98729…  24917 字节
     由 tools/publish_from_kaggle.py 发布；凭证扫描已通过 -->

<!--
GENERATED-BY: session-e7651e51-1d05-497a-9679-f0d4fa48511b (DSH agent, 2026-10-07 14:11 +08:00)
LAST-EDIT: session-832e8017-0841-4059-9d9e-b09e06bc456f (DSH agent, 2026-10-31 19:25 +0800)。恢复分阶段审批与大规模测试飞书 HTML 申请。
PREVIOUS-LAST-EDIT: DSH agent (2026-10-30)（① 头部变更史逐字搬进 archive/HEADERS_20261030/kaggle_AGENTS.header.md；② §2.8 红线 1 的审批通道改成「任何通道，只要用户对这一轮明确同意」——飞书停用）
PURPOSE: Kaggle 线的说明性内容 —— 数据源、信息源、取数方法、提交包、本线硬约束与本线的坑
ORIGIN: AGENTS.md（2026-10-07 按线拆出为根上的 `KAGGLE.md`）→ 2026-10-07 搬进 `Kaggle平台/` 改名 `AGENTS.md`，内容逐字未改
CUMULATIVE-DELTA: 2026-10-31：小样本验证自主推进，大规模测试先经飞书发送 HTML 并等待明确批准。仅更新规则与入口，不恢复旧多 agent 流程。 2026-10-30（17:3x）：§2.8 红线 1 补一句「反向不成立」—— 平台提交逐次审批这条**只管线上的提交**，**开 GPU / 跑实验免审批**（用户同日逐字授权）。2026-10-30：本文件的变更史（PREVIOUS-LAST-EDIT 链与旧 CUMULATIVE-DELTA）**逐字**搬进 `archive/HEADERS_20261030/kaggle_AGENTS.header.md`（10071 B）。此后本行只记最近一次改动的一句话；要查历史去那份冷件。
HEADER-HISTORY: archive/HEADERS_20261030/kaggle_AGENTS.header.md
-->

# Kaggle 线 —— 数据源 · 信息源 · 方法

> ⚠️ **本文件是 `Kaggle平台/` 这个目录的规矩。** DSH 在你第一次碰到本目录下任何文件时会自动把它注入 —— 不必谁去「读规矩」。
> 本线装**说明性**内容：什么是真的、东西在哪、怎么取。
> ⚠️ **全局规则**（文件头 · 证据标注 · 目录纪律 · 会话输出 · **术语表**）在 `RULES.md`，本文件不复述。**引用术语前先读 `RULES.md` §1.10**。
> ⚠️ **本线专属的硬约束**（平台提交逐次审批 · Kaggle 资源用完即停 · `data/` 只读）在 **§2.8** —— 它们**不在** `RULES.md` 里，别漏。
> **编号**：`§2.x` 在本文件 · `§1.x` 在 `RULES.md` · `§3.x` 在 `AutoDL实验/AGENTS.md`。

---

## 2.1 取 Kaggle 数据

### 2.1.1 能用的方法

- **官方页面原文**：内部 JSON RPC `competitions.PageService/ListPages`，匿名可读（比赛元数据、排行榜同理）。
- **讨论区**：RPC `discussions.DiscussionsService/*`；但**楼主正文只能靠 headless Chromium 读渲染后的 DOM**。
- **找 RPC 方法名**：下载 `app.js` 后 grep，别猜。
- **全量数据集**：`kaggle competitions download`。
- **错误码含义**：返回 HTML = 路由不存在；JSON 400 = 参数名错；JSON 403 = 参数名错**或**真没权限。

细节：`Kaggle平台/文档/KAGGLE_API_AND_SCRAPING.md`、`Kaggle平台/文档/FORUM_SCRAPING_PLAYBOOK.md`。

### 2.1.2 别再试的

| 想做什么 | 结论 |
|---|---|
| 用 API 拿楼主正文 | 拿不到，只能靠 DOM |
| 用官方 CLI 读讨论区 | 丢嵌套结构、无楼主正文 |
| `web_fetch`、Wayback、搜索引擎抓 Kaggle 页面或旧快照 | 抓不到（纯 SPA、无快照） |
| 浏览器里批量调 RPC | 会撞 429，间隔须 ≥500ms |
| 用 `kaggle kernels delete` 取消排队 | API 侧没有取消能力，delete 是唯一手段（见红线 2） |

---

## 2.2 信息源 —— 讨论区

| 要什么 | 在哪 |
|---|---|
| 话题索引（**先读这个**，全覆盖，带「必读/选读/跳过」分档） | `Kaggle平台/爬取过的资料/discussions/gemma-4-developer-agent/_TOPIC_INDEX.md` |
| ⚠️ **语料之后的新增话题与主办方新答复** | `Kaggle平台/文档/CORPUS_DELTA_2026-10-14.md`。⚠️ 上面两份语料本体停在 **2026-10-03**，之后的新话题**不在语料里**，落后量见该文件 §0 |
| 完整原文（含楼主正文，**权威源**） | 同目录 `topics/<id>__<slug>.md` |
| 逐条消息的原始 markdown | 同目录 `topics/<id>.json`——**只有回复，别用它重建原文**（会丢楼主正文） |
| 已归纳的证据级结论 | `Kaggle平台/文档/FORUM_CONCLUSIONS.md` |
| 对上述结论的对抗性查证（**引用前必读**） | `Kaggle平台/文档/CONCLUSIONS_VERIFICATION.md` |
| 主题综述与冲突表 | `Kaggle平台/文档/FORUM_SUMMARY.md` |

---

## 2.3 信息源 —— 参考 notebook

| 要什么 | 在哪 |
|---|---|
| 逐篇索引（**先读这个**，全覆盖） | `Kaggle平台/爬取过的资料/ref_kernels_all/_INDEX.md` |
| ⚠️ **语料之后新出现的 notebook 的逐篇分诊** | `Kaggle平台/文档/NOTEBOOK_TRIAGE_2026-10-14.md`。⚠️ 语料本体停在 **2026-10-03**，之后新出现的 notebook **不在语料里**，落后量见 `Kaggle平台/文档/CORPUS_DELTA_2026-10-14.md` §0 |
| 速读版（该 notebook 的全部 markdown cell，先读它省 token） | `Kaggle平台/文档/research/_nb_md/<slug>.md` |
| 原文 | `Kaggle平台/爬取过的资料/ref_kernels_all/<slug>/`（3 个目录是空的、3 个只有 `.py`） |

`ref_kernels/` 与 `references/`（备份快照）与主力集合内容重复，不要重复读。索引里作者自称的分数一律按【他人自述·未验证】处理。

---

## 2.4 信息源 —— 官方原文与数据

| 内容 | 路径 |
|---|---|
| harness 规范（671 行，最权威） | `data/HARNESS_README.md` |
| 官方页面原文（要原话时读这里） | `Kaggle平台/文档/sections/`（拆成 md，最好读）· `pages/`（原始 JSON）· `text/`（扁平文本） |
| harness 源码 | `data/whl/`（wheel）· `Kaggle平台/文档/research/_whl_swegemma/` 与 `_whl_adk/`（解包版） |
| 比赛数据集 | `data/`（入口 `tasks.jsonl`，行数即题数） |
| Kaggle 工具链与抓取脚本 | `Kaggle平台/tools/`（用法见其 `README.md`、`forum/README.md`） |

---

## 2.5 本线的报告、数据与「引用前必读」

### 2.5.1 产出与证据在哪

| 目录 | 关键产出 |
|---|---|
| `Kaggle平台/实验/kaggle_baseline/` | 题单 `solvable_tasks_v3.txt`（**`run_eval.py` 的默认题单就是它**）/ `eval_subset_v3_stratified.txt` / `autodl_5_tasks.txt` / **`autodl_subset12_v1.txt`（会话 002 / 003 / 004 的固定对照题单，跨会话对比必须用它；用 `--task-list` 显式传入，不是默认值）** / `autodl_lever7_v1.txt`（会话 004 三条 prompt 改动**实际能碰到**的 7 题，供「换模型」这类对照用；它是 12 题单的**子集**）· v2 时代的公开题上限探针与地板探针脚本及产物（`out/` `out2/` `out3/`）· `tmp_pytest/`（pip --target 装的外部件，不是临时件） |
| `Kaggle平台/实验/monitor/` | 云端取回的评测证据。⚠️ 其中 `outputs/gemma4-gold-patch-ceiling-probe/` 那份是 **v2**，**不是** v3 —— 别当 §2.6 的依据用（正确的 v3 在哪见 §2.6） |
| `Kaggle平台/实验/` | `AGENT_EVAL_REPORT.md`（agent 评测主报告）· `ref_l4_evidence/`（原始日志，索引见其 `_INDEX.md`）· **`ONLINE_VERIFIED_CHANGES.md`（线上改动判词台账：哪条改动线上证实可行 / 有害 / 待拆，选下一条改动与掉分回退时先读它）** · `SUBMISSIONS_LOG.md`（逐次平台提交的留痕，append-only） |
| `Kaggle平台/文档/research/` | 解包源码 · `_nb_md/`（notebook 速读版） |
| `submission/` 与 `data/sample_submission/` | 见 §2.7 |

### 2.5.2 动手前必读

| 文档 | 什么时候读 |
|---|---|
| `data/HARNESS_README.md` | 动任何与提交包或平台提交相关的东西之前 |
| `Kaggle平台/文档/SUBMIT_UPLOAD_NETWORK_FIX.md` | **要真做平台提交之前**（或平台提交挂死时）—— 本机到 `www.googleapis.com:443` 被墙，上传会永久挂起 |
| `Kaggle平台/文档/LORA_VERDICT.md` | 动 adapter 之前 |
| `Kaggle平台/文档/SUBMISSION_PACK_NOTES.md` | 改 `submission/` 之前 |
| `Kaggle平台/文档/CORPUS_INDEX_NOTES.md` | 改语料索引或加新语料之前 |
| `Kaggle平台/文档/KAGGLE_AGENT_SAMPLE_REPORT.md` | 只在要 accelerator 枚举证据 · `machine_shape` 合法性判别器 · 或 `SUBMISSION_PACK_NOTES.md` U5 那条时读。⚠️ §2.4 的 OOM 论证已作废 |

### 2.5.3 引用数字或结论之前必读

| 文档 | 内容 |
|---|---|
| `Kaggle平台/文档/CONCLUSIONS_VERIFICATION.md` | 对证据级结论的对抗性查证（有更正） |
| `AutoDL实验/实验分析/kaggle_baseline/CEILING_V3_REPORT.md` | 公开题集上限的实测与逐仓库拆解 |
| `Kaggle平台/文档/KAGGLE_BASELINE_REPORT.md` | 地板与失败分类 |
| `Kaggle平台/实验/AGENT_EVAL_REPORT.md` | agent 实测、T4 五轮与 HTTP 500 的根因、硬件判据 |
| ⭐ `Kaggle平台/实验/ONLINE_VERIFIED_CHANGES.md` | **线上改动判词台账** —— 引用任何「两次线上提交的分数差」之前先读它：⚠️ **2026-10-28 起口径已变** —— 同一份内容两次读数差 0.09（0.15 → 0.06）⇒ **位移 < 0.09 时不许把分差单独归因给一条改动**；可以写成写明不确定度的有条件推测（用户 2026-10-29：线上有波动，仍要据波动合理推测）。它同时把『可行 / 有害 / 待拆』三档与三种线上结果（出分 / 报错 / 超时）分开计数 |
| `Kaggle平台/文档/DOC_FIX_NOTES.md` | 已更正过的旧结论 |
| `Kaggle平台/文档/DISCUSSION_INTEL.md` | **已过时，不要引用**（写于楼主正文恢复之前） |
| `Kaggle平台/文档/KAGGLE_L4_REPORT.md` | **kernels 服务侧的平台事实**：两套 `kernels.*` 命名空间不相交 · 状态枚举完整取值 · `GetKernelSessionStatus` 返回 `{}` 就是 `QUEUED` · `print(gpu_quota)` 把 `108000s` 显示成 `21600s` 的 repr 坑。⚠️ 其余各节已被别处收纳，逐条去向见该文件头 |
| `Kaggle平台/文档/KAGGLE_AGENT_SAMPLE_REPORT.md` | 只在要 accelerator 枚举证据 · `machine_shape` 合法性判别器 · 或 `SUBMISSION_PACK_NOTES.md` U5 那条时读。⚠️ §2.4 的 OOM 论证已作废 |
| `Kaggle平台/文档/NIGHTLY_REPORT.md` | 只在要 `all_task_results.jsonl` 的 `task_index` 语义（**chunk 内局部序号、不是全局题号**）时读。⚠️ v2 口径 |
| `Kaggle平台/文档/AGENTS_V2_REVIEW.md` | 只在要 §8.6 那条**尚未落地**的更正（`requests_7315` 的正确测试名与行号）时读 |
| `Kaggle平台/文档/CORPUS_DELTA_2026-10-14.md` | 语料增量（新话题、新 notebook、主办方新答复）· **对 §2.7.5 的版本口径更正** · 与本文档冲突的若干处。⚠️ **引用 §2.7.5 或语料覆盖率之前先读它** |

### 2.5.4 专题

| 文档 | 内容 |
|---|---|
| `Kaggle平台/文档/GEMMA4_HARNESS_要点.md` | harness 要点 + `thinking_*` 完整证据表 |
| `Kaggle平台/文档/GEMMA4_AGENT_官方要点与计划.md` | 比赛要点与计划（含 2 处已标注被推翻的论证） |
| `Kaggle平台/文档/AGENT原理手册.md` | Agent 原理速成 |
| `Kaggle平台/文档/EVAL_PROMPT_FLOW.md` | **prompt 怎么组装并喂给模型**（真实 trace 实例、撞墙机制、工具声明、一次请求的四段构成） |
| `Kaggle平台/文档/USER_QUOTES_2026-10-23_只看线上.md` | **用户 2026-10-23 这一场会话的逐字原话（11 条）+ 并成的 9 条执行规矩 + 「只看线上证据」的分析口径**；报错这件事的下一步（A 提交后落盘观测 / B 同包重交判别）也在这一份 |
| `Kaggle平台/文档/ONLINE_FAILURE_MODES_2026-10-23.md` | **线上失败面清单**：能让一次平台提交报 `Notebook Threw Exception` 的机制 × 判据 × 判别实验（四列：机制 / 为什么它给异常类报文 / 线上怎么观测 / 判别实验花几次额度）· 按「排队调度 → 打包编译 → 模型服务 → 每任务执行 → 补丁判分 → 平台侧已知故障 → 契约差异」分层 · 下次提交必须记录的观测清单与脚本 `temp/watch_submission_online.py` · 永远拿不到的观测 · 只花一次额度的实验与判据 |
| `Kaggle平台/文档/research/starter/starter_dump.md`、`Kaggle平台/文档/research/lora_probe/starter_dump_utf8.md` | 官方 starter 反查 |

---

## 2.6 数值在哪

| 要什么 | 去哪 |
|---|---|
| 公开题集上限的逐仓库拆解与题单 | `AutoDL实验/实验分析/kaggle_baseline/CEILING_V3_REPORT.md` |
| **v3 公开题上限的逐题原始数据**（**不是** v2） | `AutoDL实验/实验分析/kaggle_baseline/out_v3/results_ceiling/all_task_results.jsonl`；汇总在同目录 `out_v3/baseline_summary.json`。⚠️ 字段名是 **`ceiling_resolved`**，**不是** `resolved` |
| ⚠️ **不是**公开题上限的证据（那是 **v2**） | `Kaggle平台/实验/monitor/outputs/gemma4-gold-patch-ceiling-probe/results_ceiling/all_task_results.jsonl` —— 仓库里**有多个同名文件**，引用前先数 `resolved=true` 的条数 |
| 本地 CV 逐题结果、失败分类、环境锁死题数 | `Kaggle平台/文档/DOC_FIX_NOTES.md` |
| agent 实测、硬件判据 | `Kaggle平台/实验/AGENT_EVAL_REPORT.md` |
| 隐藏测试集、async 覆盖率的两种口径 | `Kaggle平台/爬取过的资料/discussions/gemma-4-developer-agent/_TOPIC_INDEX.md` |
| 题集规模 | `data/tasks.jsonl`（行数即题数） |

**引用前先回读那个文件。不要凭记忆或旧文档写数字。**

---

## 2.7 提交包

`submission/` 由另一个 agent 维护、是移动靶。结构、逐字段说明、缺口清单全在 `Kaggle平台/文档/SUBMISSION_PACK_NOTES.md`。

### 2.7.1 平台提交前必查

- ⭐ **先跑硬闸**：`python Kaggle平台/tools/verify_submission_pack.py`（**exit 1 就不要做平台提交**）。它调**官方** `validate_directory()` + 评分白名单（`swegemma/config.py` L20-30，**只有 7 个扩展名**）来验，另外查 `__pycache__`/`.pyc`、`!include` 目标、未知顶层键（schema 是 `extra="forbid"`）、工具名、`model` 名、skill 命名、`adapter:` 开关，以及下面那条预算陷阱。
  ⚠️ **为什么必须有这道闸**：`__pycache__/*.pyc` **不在白名单里** ⇒ `SubmissionValidationError` ⇒ **整个提交包被拒**。2026-10-04 真发生过：一次「确认自己的脚本还都能编译」的批量自检，把 1 个 `.pyc` 写进了 `submission/skills/patch-guard/scripts/`。任何编译/导入动作都会重建它 ⇒ **打包 zip 时也必须显式排除 `__pycache__`**（仓库目前没有打包脚本，`submission.zip` 是临时造的）。
- `submission/eval_config.yaml` **回读磁盘上的文件本体**，不要信旧文档里的数字。官方默认 60 分钟 / 100 次工具调用。
- **不要把 `data/sample_submission/eval_config.yaml` 当模板**：它写的是 1 分钟 / 10 次工具调用，复制后忘删就是每题 1 分钟，而且不报错。
- `submission/agent.yaml` 里的 `adapter:` 保持关闭（原因见 §2.7.4）。
- ⭐ **先把上传通道验通再交**：本机到 `www.googleapis.com:443` 被墙，而 Kaggle 返回的上传地址就在那个域上 ⇒ `kaggle competitions submit` 会**永久挂起且不输出任何东西**（额度不受损，挂死发生在记额度之前）。读 `Kaggle平台/文档/SUBMIT_UPLOAD_NETWORK_FIX.md`，里面有无消耗额度的单点只读探针（先验后交）。

### 2.7.2 eval_config 与 skills

- `eval_config.yaml` 的权威值 = 磁盘文件本体；引用前回读。
- `submission/skills/` 的结构已读码确认可用：`adk_submission/discovery.py` 按文件名 `SKILL.md` 全树查找，目录名任意，单个 skill 上限 50 MiB。

### 2.7.3 submission 现状

`submission/` 是移动靶，不要以本节或任何旧快照为准确认「有没有某个文件」。以磁盘和 `SUBMISSION_PACK_NOTES.md` 为准。

### 2.7.4 adapter 不能用

官方 sample 的 LoRA 是未训练的占位：`lora_B` 逐 bit 恒为 0 ⇒ `ΔW ≡ 0` ⇒ 对模型输出零影响。两个 `.safetensors` 还逐字节相同。所以**用官方 sample 测 LoRA 只能得到假阴性**，`adapter:` 必须保持关闭。证据链见 `Kaggle平台/文档/LORA_VERDICT.md`。

### 2.7.5 thinking_* 语义

> 讨论区与至少 3 篇 notebook 给出**互相冲突**的解释 ⇒ 以下用一手证据定案：
> 【官方文档】`data/HARNESS_README.md` **L148-149**。【读码】`Kaggle平台/文档/research/_whl_adk/adk_submission/resolvers/generation.py` **L291-300 / L329-337 / L341-344**。

1. `include_thoughts: false` 关掉的是**思考本身**——不存在「仍在思考、只是推理不进上下文」那一档。
2. `thinking_budget` 为正数才启用思考；`include_thoughts: false` 时它被丢弃。
3. `thinking_level: "NONE"` 与 `include_thoughts: false` 等价（同一个 `or` 分支）。
4. ⚠️ **版本口径（2026-10-14 补）**：上面三条在 `adk_submission ≤0.2.12` 成立。**`0.2.13` 起 `apply_thinking_config_to_model` 先查 `thinking_budget` 再查 `include_thoughts`** ⇒ `include_thoughts: false` **不再关闭思考**；要关必须显式写 `thinking_budget: 0`。【官方】主办方承认这是**有意改动**（讨论区 `747914`，Ryan Holbrook 2026-10-10 逐字：「This was intended. My apologies.」…「will update the readme」）。⚠️ 这条是【他人复现·未验证】+【官方】承认，**不是我们实测**。现行写法（`include_thoughts: true` + `thinking_budget: 4096`）**不受影响**。逐字引文见 `Kaggle平台/文档/CORPUS_DELTA_2026-10-14.md` §1.2。

⇒ `configs/sampling.yaml` 不要写 `thinking_level`，改用 `include_thoughts: true` + `thinking_budget: 4096`。
⚠️ **原因（别以为它只是无效）**：【官方】经 vLLM 的 OpenAI 兼容 `/v1` 端点时**应省略 `thinking_level`**。它会被映射成 `reasoning_effort`，而 `swegemma` 设了 **`litellm.drop_params = True`** ⇒ **静默丢弃：不报错、也不生效**。
完整证据表见 `Kaggle平台/文档/GEMMA4_HARNESS_要点.md`。

### 2.7.6 一次平台提交可以合并多个专题的改动（2026-10-25 用户裁定）

> 用户原话（2026-10-25，逐字）：「有时候我们只继续一个专题，但一个专题的影响面较小，因此我们也可以在有改进证据的情况下，同时把多个专题的改动进行合并从而进行线上提交」。

**为什么允许**：一个专题独占当天唯一一次额度，等于用一整天换一个很小的位移。⚠️ **线上分数不可复现 —— 同一份内容两次读数已实测差 0.09**（`57262060` 出 0.15，它的内容重放 `57678078` 出 0.06，2026-10-28 07:50 回读；口径与被推翻的「不存在抖动」裁定见 `Kaggle平台/实验/ONLINE_VERIFIED_CHANGES.md` §0 与 §4）。⇒ **本次提交出分后，位移小于 0.09 时不许把分差单独归因给某一条改动**（用户 2026-10-25 的「分差只可能是改动造成的」已出反例）。⚠️ **但允许写明不确定度的有条件推测**（用户 2026-10-29 定：线上有波动，仍要据波动合理推测）—— 写法是「方向与 X 一致，幅度落在已知不可复现范围内（0.09），因此只能算方向性提示」，⛔ 不许写成「X 让分数涨了 0.0x」，也⛔ 不许写成「有波动所以什么都判不了」。**合并是为了让这一次额度换一个更大的位移**，不是为了省事。

**四条前置（缺一条就不许合）**：

1. **每条改动各有本地证据，且效应量超出本地尺子的分辨率。** ⛔ 不许把「本地都没测出差别」的改动塞进来借别人的光 —— 那只是把几个没证明的东西叠在一起，读数一坏就查不出是谁。
2. **这些改动已经同轮合测过一次** —— 见 `AutoDL实验/实验记录/EXPERIMENT_RULES.md` §1.2.2。⛔ 不许把「各自单独测过」当「合起来测过」。
3. **先算时长账再交。** 合并后的总耗时必须在 12 小时【2026-10-25 · 官方 Evaluation 页】的墙内，并留出容器搭建的余量（算式见 `AutoDL实验/AGENTS.md` §3.5.3）。⛔ 省时的改动与费时的改动叠在一起时必须**重算**，不许把各轮的数字直接相加。
4. **留一个「上一个已知好的整包」**：提交前记下它的 zip sha256 与平台 ref，否则掉分时无法回退。

**提交时必须记录**（落 `Kaggle平台/实验/SUBMISSIONS_LOG.md` 与相关专题页）：

- 这次合并了哪几个专题、每个专题贡献哪一处改动（逐条写到文件 / 字段 / 文本）；
- 一句「本次提交不用于归因」；
- 回退目标：上一个已知好整包的指纹。

**掉分时怎么处置**：先回退到那个整包，再逐个单测。⛔ 不许在合并包上做「猜哪一条坏了」的二分 —— 一天一次额度，二分要花掉好几天。

⚠️ **这不改变 §2.8 红线**：合并提交仍然是平台提交，仍然逐次审批。⛔ 「有改进证据」不构成免审批的理由。

---

## 2.8 本线的硬约束

> 这三条**只对本线生效**，所以它们住在本文件而不在 `RULES.md` 的全局红线里。

1. **平台提交必须逐次审批。** 每天只有 1 次额度，`kaggle competitions submit` 只在用户明确要求时执行；`kaggle_auto.py` 默认 dry-run，不要擅自加 `--commit`。
   ⚠️ **2026-10-17 用户收紧：即使给了「无穷权限 / 一切权限都批准」，也**不含**线上提交。**
   ⚠️ **2026-10-30 改通道**：原来这句要求「向飞书发出申请」；用户当天裁定**飞书目前不需要了** ⇒ **通道不再限定**，界面上说一句也算。
   ⇒ **判据**：任何一次平台提交（`kaggle competitions submit` · `kaggle_auto.py submit --commit` · `temp/netfix_submit/submit_with_netfix.py --commit`）**必须先得到用户在这一轮里的明确同意**（任何通道都行，但要是他对「这次提交」说的，不是泛泛的「都批准」）；唯一例外是该会话里用户**逐字**说过「线上提交权限也直接通过」。
   ⚠️ **违规判例（2026-10-17）**：本会话在「无穷权限」那句授权下**自主提交**了 ref `57293698`（平台提交序号 6）。按本条它属**越权** —— 那句授权没有说线上提交也直接通过。⛔ 不许把「无穷权限」「无不批准」这类话当成涵盖平台提交。
   **实验审批边界（2026-10-31 用户更新）**：小样本机制验证免逐轮审批。大规模测试必须先通过飞书发送 HTML 并申请，等用户明确同意。详见 `AutoDL实验/实验记录/EXPERIMENT_RULES.md` §1.14.1。不得把旧的开实验整体免审批解释成大规模测试授权。
2. **Kaggle 资源用完即停。** 停掉所有后台轮询作业；若真有排队中的 kernel，**先 `kaggle kernels output` 取回结果，再 `kaggle kernels delete`**——delete 会连带删掉云端 output。
   ⚠️ **取消排队在 API 侧不可行**（RPC `kernels.KernelsService/CancelKernelSession` → 403，该权限是网页 UI 专属）。完整调查见 `archive/kaggle_notebook/AGENTS_removed_sections.md` §3.4。
3. **`data/` 只读**，新产物写到新目录。

---

## 2.9 本线的坑

| 坑 | 后果与做法 |
|---|---|
| `page.goto` 用 `domcontentloaded` 抓 SPA | 永远等不到；改用 `waitUntil:'commit'` 加轮询 DOM。导航超时后仍要继续轮询 |
| 用 class 选择器抓未知 DOM | 全不命中；用文本锚点 |
| 阈值拍脑袋 | 曾把最小长度设成 40，误杀正文仅 32 字符的真实楼主正文 |
| 把 `Permission 'kernels.get' was denied` 当成「kernel 不存在」 | 该消息对「从未存在的 slug」「别人的 kernel」「读不到的私有 notebook」逐字相同。只有自己名下的才能当已删除，且要用 `kaggle kernels list --user <owner>` 复核（`AGENTS_removed_sections.md` §3.4.7） |
| `kernel-metadata.json` 的 `id` 与 `title` 不一致 | 实际 slug 由 `title` 派生，会在别的 slug 下操作；重建前先统一（`AGENTS_removed_sections.md` §3.4.5.1） |
| **`kaggle competitions submit` 在本机永久挂起、无任何输出** | **不是 CLI 的 bug**：上传地址落在 `www.googleapis.com`，而本机到它 TCP 443 超时（同机器的 `storage.googleapis.com` / `*.kaggle.com` 都通，所以**只有写会挂、读不会**）。做法见 `Kaggle平台/文档/SUBMIT_UPLOAD_NETWORK_FIX.md`。⚠️ 它也解释了「额度没被消耗」——挂死发生在记额度之前 |

---

## 2.10 下一步

① 查清 `requests` 为何装了 `pytest-httpbin` 仍低通过率（全部 ceiling log 已在手。候选：缺 `trustme`、或 `--doctest-modules` 与 harness 覆写 pytest.ini 的交互）。
② 测「评分环境里的公开题上限」（gold 补丁在那个环境能过多少题），需真 Docker 3.13 沙箱。

逐题 ID、失败分类与核对过程见 `Kaggle平台/文档/DOC_FIX_NOTES.md`。
