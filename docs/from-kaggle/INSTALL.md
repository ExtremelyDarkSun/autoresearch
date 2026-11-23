<!-- 来源：Kaggle 工作区 `INSTALL.md`
     sha256=dfa8a80b60343511…  69689 字节
     由 tools/publish_from_kaggle.py 发布；凭证扫描已通过 -->

<!--
GENERATED-BY: session-6bb9b0fe-a860-41f6-87c0-1940f98b7317 (DSH agent, 2026-10-16 23:15 +08:00)
LAST-EDIT: session-2a86b77b 主会话（2026-11-22 16:2x +08:00）。第10节「当前维护版」同步观察者 0.2.1 实际语义：按专题固定单一身份、飞书问人 2 分钟超时（除线上提交类外自动允许）、新机部署命令与加载验证判据（loaded.json 须含 0.2.1-timeout-allow 与 allow-except-submit）。其余节未改。改前件 `archive/INSTALL_before_observer_021_20261122_1615/INSTALL.md`（68,170 B · `721E1582…`）。
PREVIOUS-LAST-EDIT: session-d235e57c（2026-11-21）。观察者安装入口标明续接维护版，旧包为历史基线。报告审阅入口改为现读续接加载标记。
PREVIOUS-LAST-EDIT: session-025158e3-980b-4899-bfca-8d3224697f8d (DSH agent, 2026-11-20 17:47 +08:00)。§11 本机状态改为最终「已启用」：v5 最终验收 pass（44.686 s · host 8840 · scribe `96a0b05d…`），正式安装同一版本 `a7e5cc…`，生产 `loaded.json` `loadedAt` 09:46:31.163Z 且 `mountedSessions` 含本会话。⛔ 其它节未改。
PREVIOUS-LAST-EDIT: session-025158e3-980b-4899-bfca-8d3224697f8d (DSH agent, 2026-11-20 17:44 +08:00)。§11 本机状态改为「隔离夹具真实链路验收已通过（v4）」，记录 host pid 8840、46.239 s、scribe 会话、read-only、证据覆盖与快照哈希；明确 v4 记录版本已过、正式启用另核 loaded.json。⛔ 其余各节未改。
PREVIOUS-LAST-EDIT: session-025158e3-980b-4899-bfca-8d3224697f8d (DSH agent, 2026-11-20 17:29 +08:00)。新增 §11 项目报告审阅插件入口。按用户要求收窄 §11 回滚两行：不提删除，不写直接重启，源码入口改指 README 链接。⛔ 其余各节未改。
PREVIOUS-LAST-EDIT: session-cd1524a5-7f08-42bb-bdeb-dff899fcbb4e (DSH agent, 2026-11-11 最终版)。新增10.6：观察者指令在原目标通知内显示的安装、验收与回滚。
PREVIOUS-LAST-EDIT: session-e7f78106-cfca-4a15-90df-6a4c90c96330 (DSH agent, 2026-11-07 21:50 +08:00)（**新增 §7：第四处 DSH 改动 —— 把「AutoDL 机器从开不了变成能开」推进会话**（插件 `install/autodl-events` + 判定 `AutoDL实验/tool/autodl_events.py`），含两半组成、绝对路径 insert 装载、**改 config 热/改代码冷**、四层离线验证 + 一条真机验收、回滚与七条已知限制（含「投给 36 个会话」那次实测缺陷与修法）；§0 表格加一行。⛔ 原 §7／§8 只把编号改成 §8／§9，正文逐字未动。改前件 `archive/INSTALL_20261107_2045_改前_加机器事件推送.md`（41,081 B，与改前逐字节一致））。**上一版：** session-3d424527-23f3-43b8-9e4f-1cfa92064c35 (DSH agent, 2026-10-17 21:29 +08:00)（**新增 §6：第三处 DSH 改动（可选）—— 关掉会话自动压缩（compaction）**，含症状与实测数、`config.auto: false` 的落点、重启才生效的读码依据、离线验证的天花板、回滚与已知限制；§0 表格加一行并点明它是可选项。⛔ 原 §6／§7 只把编号改成 §7／§8，正文逐字未动。改前件 `archive/INSTALL_20261017_2126_改前_加compaction关闭节.md`（34,427 B，sha256 `ABFABD26B15CDDB28B951E2FB92D080917B9404CE8C27B7FF014515DCBBC31F5`，与改前逐字节一致））。**上一版：** session-8aa09d00-618d-477e-a8f2-a638e57cf48d (DSH agent, 2026-10-17 15:45 +08:00)（**新增 §5：第二处 DSH 环境改动 —— 回合结束的「文档遗漏提醒」插件 `dsh-doc-gap`**，含预期表现表、装载路径、四件套自检、回滚与已知限制；§0 表格与被覆盖声明同步点明「它同样尚未在本机装载」。⛔ 原 §5／§6 只把编号改成 §6／§7，正文逐字未动；改前件 `archive/INSTALL_20261017_1539_改前_加docgap节.md`（27,987 B，sha256 与改前逐字节一致））。**再上一版：** session-6bb9b0fe-a860-41f6-87c0-1940f98b7317 (DSH agent, 2026-10-16 23:57 +08:00)
PURPOSE: 在新机器上复现本项目的安装说明 —— 仓库自身的安装，加**两处必需**的 DSH 环境改动（① 对子 agent 停止注入项目级 AGENTS.md；② 回合结束时判「文档遗漏」并询问是否补写），另记**一处可选改动**（③ 关掉会话自动压缩；复现不需要它，但跑长会话时不知道它会误判成模型失忆）与**一处按需改动**（④ 把 AutoDL 机器可用性事件推进会话，见 §7）；另含**第五处可选改动**（⑤ 观察者接入原生goal，见第10节）与**第六处改动**（⑥ 项目报告审阅插件与未审 `STATUS.md` 链接拦截，见第11节）。
ORIGIN: archive/INSTALL_20261110_改前_加goal观察者插件.md
CUMULATIVE-DELTA: 2026-11-22：第10节「当前维护版」同步观察者 0.2.1 实际语义（按专题固定单一身份 · 飞书问人 2 分钟超时且除线上提交类外超时自动允许 · 新机部署命令 · loaded.json 加载验证判据）。 2026-11-21：观察者安装入口标明续接维护版，旧包为历史基线。报告审阅入口改为现读续接加载标记。 2026-11-20 17:47：§11 本机状态改为最终「已启用」——v5 最终验收 pass（44.686 s · host 8840 · scribe `96a0b05d-f499-4581-9e19-658043c27041`），正式安装同一版本 `a7e5cc696d…`，生产 `loaded.json` 的 `loadedAt` 为 2026-11-20T09:46:31.163Z、`mountedSessions` 含本会话。2026-11-20 17:44：§11 补 v4 隔离夹具真实链路验收（host pid 8840 · 46.239 s · read-only · read/glob · 证据覆盖与快照哈希通过），明确 v4 记录版本已过、正式启用另核 `loaded.json`；其余节未改。2026-11-20：新增第11节项目报告审阅插件的安装、验收与回滚入口，正文指向 `install/dsh-status-review/README.md`，§0 与 §1 各加一行指针，原第0至10节编号与正文保留。相对ORIGIN新增第10节观察者goal的安装、使用、验证、边界与回滚，更新范围和总览两处索引，纳入固定版本包与生成型验收报告。原第0至9节编号保留。新增10.6：纯客户端显示插件将已有观察记录展示在原目标通知内，不修改会话正文。
PREVIOUS-CUMULATIVE-DELTA: 2026-11-07：新增 **§7（第四处 DSH 改动：AutoDL 机器可用性事件 → 会话）** 与 §0 对应行；原 §7／§8 只把编号改成 §8／§9 并更新跨节引用。首版。记录注入过滤插件的机制依据、过滤判据、安装/验证/回滚步骤，以及未在本机装载验证这件事。2026-10-16 23:20：§4.2 补两条机制定案（发射不带 `this` ⇒ 不过滤 scope · `prepend` 是服务级 `unshift`）；§4.4 装载路径改写成创造模式（普通任务里没有 `plugin_manager`）；§4.5 加接线自检；§4.7 把「未验证」降级为「读码定案 + 离线接线自检」。2026-10-16 23:23：接线自检补两条**顺序对照**（按 `cordis/src/events.ts` 的瀑布语义建模，证明不带 `prepend` 时一行都裁不掉）⇒ 12 条；实测字节按复测更新并标了「会漂」。2026-10-16 23:26：新增 §3.5.1 —— 复现者跑五个校验器一定会撞见的**两条已知红**（`r232gate/` · `AutoDL实验/temp` · 22 处 `archive/` 断引用）及其**项目既有裁定**（不改归档、不放宽校验器），并实测证明本次改动对这两条红的贡献是 0。2026-10-16 23:37：**按用户裁定「读之前的实验是必须的」撤回旧动机** —— `policy.mjs` 移除建立在「别让子 agent 看到历史/旧结论」上的全部六处规则（§3.5.5 表体 6 行 · §3.5.4 · §3.5.6 · §3.4.3 · `Kaggle平台` §2.1.2 · §2.7.4），§4.1 逐字记下该裁定；实测由 46.6% 变为 **35.3%**；两个自检与分类表的期望值同步翻转（22 条 / 12 条）。2026-10-16 23:42：§3.5.1 按**同时进行的 purge** 改写 —— 另一会话（用户指示）在 23:26–23:40 做了 4 个 `purge` 提交 + 209 处未提交删除，**清掉了 `r232gate/` 与 `AutoDL实验/temp/`**（structure 因此转绿）、清空了 `EXPERIMENT_LOG.md` 与 `REQUESTS.md` 的正文、删除了我原先引用的三份记录 ⇒ 本节改为「4 绿 1 红 + 判据现跑 + 用 `git show` 回收原话」，⛔ 不再抄会漂的计数。同时给分类表加了「行号会漂、以 `verify-real-policy.mjs` 为活判据」的警告。2026-10-16 23:57：**用户第三次纠正口径** —— 原话「我们之前聊的不就是不读项目级 AGENTS.md 吗？只读项目子 agent.md」⇒ `policy.mjs` 从「按段裁剪」改为**对子 agent 整份 `drop` 项目级那四份**（只留 `~/.dsh/AGENTS.md`）；`filter-core.mjs` 补「一段都不剩 ⇒ 整条消息丢弃」；三个关卡与量具的期望值全部翻转（18 / 12 / 18 条；实测 70733 B → 1161 B = 98.4%）；`SECTION_CLASSIFICATION.md` 降级为存档。⛔ 那六处「按裁定要保留」的规则随之一并失效（整体不再注入，不存在「保留哪一节」的问题）。2026-10-16 23:47：§3.5.1 再改一次 —— **五个校验器现在全部 exit 0**（另一会话 `session-de21f953` 把 `archive/` 补进 `verify_agents_sections.py` 的 `SKIP_PARTS`，修的是「代码与自己的注释不一致」，判据是除 archive 命中外计数逐位不变）；本节改为「5/5 全绿 + 两次曾经红过的留痕 + 那条『归档不再被检查』的口径」。⚠️ **勘误**：本文件与 `install/agent-instruction-filter/` 下各文件的首版文件头时间是我手写的估计值，**超前真值，其中两份还写错了日期**；已按系统时钟统一核正为 `2026-10-16 23:15 +08:00`（本文件随后在 23:20 再改一次）。2026-10-17 15:45：**新增 §5（第二处 DSH 环境改动）** —— 回合结束的 `dsh-doc-gap` 插件：只在 cwd 含 `kaggle_competition` 的会话生效，在回合末尾让判定后端判一次「这次提问指向的知识点，项目文档说了没有」，命中阈值就 steer 一条消息让会话自己决定落点并 `ask_user_question` 问用户是否补写；**判定器不指定落点文件**（试过，会给 `target=none` 弃权）。同次改：§0 表格加一行、首部「最重要的一条」点明 §5 同样未装载、原 §5／§6 顺延为 §6／§7。⛔ 原 §5／§6 正文逐字未动。2026-10-17 21:29：**新增 §6（第三处 DSH 改动，可选）—— 关掉会话自动压缩**：症状与实测数（单次请求 prompt tokens 677,179 → 192,317）、阈值公式与实数（678,464 / 119,040）、`config.auto: false` 的落点与语义表、重启才生效的读码依据、离线验证的天花板（`--dump-config` 对 desktop 被拒）、回滚与已知限制；§0 表格加一行。⛔ 原 §6／§7 只把编号改成 §7／§8，正文逐字未动。
-->

# INSTALL —— 在新机器上复现本项目

> 本文件是**复现入口**。它只写「怎么装起来」，不写项目规矩。
> 项目规矩的入口见 `AGENTS.md`（索引）与 `RULES.md`（正文）。
> 证据等级按 `RULES.md` §1.4：【实测】跑过 · 【读码】读源码 · 【读文档】抄自落盘件 · 【未验证】。

---

## 0. 这份文档覆盖什么

| 覆盖 | 不覆盖 |
|---|---|
| 仓库自身的安装（数据、凭据、脚本、校验器） | 比赛规则与提交纪律（见 `Kaggle平台/AGENTS.md`） |
| **一处 DSH 环境改动**：对子 agent 过滤 `AGENTS.md` 类注入 | 实验框架（HIL）的部署（见 `AutoDL实验/框架/README.md`） |
| **第二处 DSH 环境改动**：回合结束时判「文档遗漏」并询问是否补写（§5） | Kaggle 平台提交（见 `Kaggle平台/实验/SUBMISSIONS_LOG.md`） |
| **第三处 DSH 改动（可选）**：关掉会话自动压缩，治「上下文突然掉一截、早期细节被摘要吃掉」（§6） | — |
| **第四处 DSH 改动**：把「AutoDL 某台机器从开不了变成能开」这条事件推进会话（§7，插件 `install/autodl-events`） | — |
| **第五处 DSH 改动（可选）**：原生 goal 管状态，观察者生成每轮指令（[第10节](<#10-可选观察者接入原生-goal>)） | 等待中的提问工具代答 |
| **第六处 DSH 改动**：项目报告审阅插件，未审 `STATUS.md` 链接不进正文（§11） | — |
| 各项改动的验证与回滚 | |

⚠️ **本文档最重要的一条**：第 4 节的插件**从未在本机装载过**。原因见 §4.7。装载与验证由读者执行。

⚠️ **同样重要**：第 5 节的插件**同样从未在本机装载过**。它在 `install/dsh-doc-gap/` 里备好了，但 `desktop` profile 只能由 Electron 应用自己改，CLI 会直接拒绝（§5.2 有实测报错原文）；装载与验证同样由读者在应用内执行。

⚠️ **第 7 节同理**：插件已写好、离线自检全绿，但**没有在本机重启 DSH 验收过**（§7.5）。事件判定那一段是实测跑通的（计划任务每分钟都在跑它）。

---

## 1. 复现总览

| # | 做什么 | 在哪 |
|---|---|---|
| 1 | 装 DSH 并确认 profile | §2 |
| 2 | 克隆仓库、装 Python 依赖、放数据与凭据 | §3 |
| 3 | 跑五个校验器确认仓库自洽 | §3.5 |
| 4 | 装「子 agent 注入过滤」bundle | §4.4 |
| 5 | 验证过滤生效、并跑反向对照 | §4.5 |
| 6 | 按需安装观察者 goal 插件，验证三轮推进与完成 | [第10节](<#10-可选观察者接入原生-goal>) |
| 7 | 按需安装项目报告审阅插件 | §11 |

**本次新增项的状态〔2026-11-10〕**：第10节插件已在 DSH `0.2.0-rc.2` 的 `desktop` profile 热加载并完成真实模型验收。上文关于第4、5、7节的旧版装载记录不适用于第10节。当前仓库只配置了本地 Git，没有 GitHub remote，本节使用随项目保存的本地包。

---

## 2. 前置：DSH 与 profile

- DSH 安装目录（本机实例）：`C:\Users\Administrator\AppData\Local\Programs\DeepSeek Harness\`
  - 本体在 `resources\app.asar`（**不是**目录；读它要按 asar 格式解）。
  - 原生模块在 `resources\app.asar.unpacked\dsh\node_modules\`。
  - 自带 node：`resources\runtime\primary-runtime\dependencies\node\bin\node.exe`。
    ⚠️ `resources\runtime\bin\node.cmd` 在参数含空格时会报 `'""' is not recognized` ⇒ 用上面的 `node.exe`。
- profile 名与目录从环境变量读，**不要猜**：

```powershell
$env:DSH_PROFILE        # 本机实测 = desktop
$env:DSH_PROFILE_DIR    # 本机实测 = C:\Users\Administrator\.dsh\profiles\desktop
```

⚠️ **本机的运行 profile 是 `desktop`，不是 `web`**〔实测 2026-10-16〕。两个 profile 的补丁层不同：
`profiles\desktop\cordis.patch.yml` 有 241 行（含逐字重述的 `preset-standard`），`profiles\web\cordis.patch.yml` 是空的 `[]`。

---

## 3. 仓库自身的安装

### 3.1 数据与凭据（只读）

| 东西 | 位置 | 注意 |
|---|---|---|
| 比赛数据集 | `data/` | **只读**；入口 `data/tasks.jsonl`（行数即题数） |
| harness 规范与源码 | `data/HARNESS_README.md` · `data/whl/` | 最权威的官方口径 |
| Kaggle 凭据 | `.kaggle_auth/` | 只读，**不入 git** |

### 3.2 Python 环境

- 本机是 Windows + `pwsh`。脚本一律**从仓库根**调用（脚本靠 `__file__` 反推仓库根，进子目录会失效）。
- 设两个环境变量，避免中文输出抛 `UnicodeEncodeError`：

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONDONTWRITEBYTECODE='1'
```

### 3.3 打包与提交（只在要真提交时）

- 先把上传通道验通：`Kaggle平台/文档/SUBMIT_UPLOAD_NETWORK_FIX.md`。
  本机到 `www.googleapis.com:443` 超时，而 Kaggle 给的上传地址就在那个域 ⇒ 直接提交会**永久挂起且无输出**。
- 提交包硬闸：`python Kaggle平台/tools/verify_submission_pack.py`（**exit 1 就不要交**）。

### 3.4 实验线

`AutoDL实验/` 的一切动作先读 `AutoDL实验/AGENTS.md`；要开 GPU 之前必须通读
`AutoDL实验/实验记录/EXPERIMENT_RULES.md`。开关机脚本在 `C:\Program Files\harness\autodl.ps1`（在仓库外）。

### 3.5 五个校验器（改完文档或目录必跑）

```powershell
python Kaggle平台/tools/verify_structure.py
python Kaggle平台/tools/verify_agents_links.py
python Kaggle平台/tools/verify_agents_sections.py
python Kaggle平台/tools/verify_agents_data.py
python Kaggle平台/tools/verify_indexes.py
```

⚠️ 五个都靠正则，**会漏报**；报 0 处不等于合规。

#### 3.5.1 跑完会看到什么（**现在 5/5 全绿**；附两次「曾经红过」的来龙去脉）

**当前结论**〔2026-10-16 23:46 实测〕：**五条全部 exit 0。**

| 校验器 | 现状 | 曾经红过（留痕，别当现在的状态） |
|---|---|---|
| 五条 | ✅ **exit 0** | — |
| `verify_structure.py` | ✅ 0 处 | 23:40 之前一直报 2 处：根上的 `r232gate/`（198 文件，R232 工作区夹具，既定落点在仓库**外**的 `C:\r232gate`）与 `AutoDL实验/temp/`（21 文件，`AutoDL实验/tool/test_lease_wiring.py` 落点写错的产物）。**用户让另一个会话做 purge 时清掉了** |
| `verify_agents_links.py` | ✅ 0 处 | 23:37–23:43 短暂红 6 处（purge 删了被引文件、引用还没同步），随后自行恢复 |
| `verify_agents_sections.py` | ✅ **断引用 0 处** | 长期红 20–22 处，**全部在 `AutoDL实验/框架/archive/` 的归档件里**（18 在 `HIL_REASONING_20261015_1705_…md`，2 在两个 `run_eval_*.py`）。2026-10-17 02:00 由另一会话 `session-de21f953` 修掉：**把 `archive/` 补进 `SKIP_PARTS`** |
| `verify_agents_data.py` · `verify_indexes.py` | ✅ 0 处 | — |

**那次 sections 修复的口径（值得知道，别以为是「放宽判据」）**：修的是**代码与它自己的注释不一致** —— 该脚本 L50 的注释本来就写着「扫哪些文档（**live 文档；archive 与语料除外**）」，而 `SKIP_PARTS` 里从来没有 `archive/`。判据 = **改前/改后除 archive 命中外，所有计数逐位不变**；改前件 `archive/tools_verify_agents_sections.py_2026-10-17_改前_加archive跳过.md`（13,264 B）。

⚠️ **因此有一条要知道的口径**：`verify_agents_sections.py` 现在**跳过 `archive/`**（`SKIP_PARTS` 含 `"archive/"`）⇒ **归档件里的陈旧 § 引用这条闸不再有信号**。这是设计（归档是历史，编号按当时口径保留），但复现者要知道：绿不等于「归档里没有陈旧引用」。

⚠️ **本节原先抄过「不改归档、不放宽校验器」那条既定裁定的五份出处，现在磁盘上取不到了** —— purge（`git log` 里 4 个 `purge` 提交，23:26 → 23:35）把 `AutoDL实验/实验记录/EXPERIMENT_LOG.md` 与 `框架/code/orchestrator/REQUESTS.md` 的**正文清空**（实测 725,283 B → 7,568 B · 517,215 B → 17,252 B），删除了 `EXP043_APPROVAL_CHAIN_FIX_2026-10-14.md` · `MAIN_REVIEW.md` · `109_杂记109_….md`。
⇒ 回看原话：文件**仍在 HEAD 里**，用 `git show HEAD:<路径>`；实测三个被删文件都能取回（`git cat-file -s HEAD:<路径>` 分别返回 68,620 / 26,382 / 19,178 B）。

⚠️ **purge 与别的会话仍在动这些文件 ⇒ 读数会变**：判据是**现跑**，⛔ 不要抄本节的历史值。

```powershell
git status --short | Select-String '^ ?D '   # 未提交的删除数（本次峰值 209，其中 206 在 实验分析/；之后回落到 207）
git log --oneline | Select-String purge       # purge 提交（本次实测 4 个）
```

✅ **本次改动（`install/` 与根 `INSTALL.md`）对任何红都无贡献**〔2026-10-16 实测〕：`verify_structure.py` 的白名单已登记 `install` 与 `INSTALL.md`（同步进 `RULES.md` §1.1.1），`verify_agents_sections.py` 的输出里**完全不含**这两个路径（`Select-String INSTALL|install` 命中 0 处）。

---

## 4. DSH 环境改动：对子 agent 过滤指令注入

### 4.1 问题（为什么要改）与**现行口径**

DSH 会把 `AGENTS.md` 族当指令基线注入每个 agent。子 agent（`planner` / `orchestrator` 这类负责思考与派工的角色）拿到的是**和主 agent 一模一样的那整份**。

**⭐ 现行策略（用户 2026-10-16 23:55 裁定）**：**子 agent 整份不注入任何项目级 `AGENTS.md`。**
用户原话：「**我们之前聊的不就是不读项目级 AGENTS.md 吗？只读项目子 agent.md**」

⇒ 子 agent 的注入面剩下**一份**：`~/.dsh/AGENTS.md`（全局偏好：语言与工作方式）。项目级那四份 —— 项目根 `AGENTS.md` · `AutoDL实验/AGENTS.md` · `AutoDL实验/框架/AGENTS.md` · `Kaggle平台/AGENTS.md` —— **整份不给**。
⇒ 它自己的「子 agent 文件」**不是 DSH 注入的**：框架把 `state/<exp>/rolepacks/<role>.md`（逐字内嵌 `roles/<role>.md`）当 **prompt** 交给它，与注入无关、不受本插件影响。

⚠️⚠️ **两条被推翻的旧口径，必须记在这里（⛔ 别照它们办）**

1. **「历史负结果对子 agent 有害」**（我最初的动机）—— 用户 23:37 撤回，原话：「**我觉得读之前的实验是必须的**，我之前提到的那个不看历史实验的需求你别管了，如果你之前考虑到了这点请把他移除。」⇒ 建立在它上面的六处规则已全部移除。
2. **「保留文件、只按段裁掉操作噪声」**（我 23:37–23:43 实现的版本）—— 用户 23:55 纠正：那不是他说过的。⇒ 现行是**整份不注入**，不是按段裁。`SECTION_CLASSIFICATION.md` 那张 76 行的逐段表因此**不再是策略依据**，只作存档（切回 `filter` 模式时可用）。

⚠️ **一处代价，得知道（我没擅自改）**：`AutoDL实验/框架/CONTEXT_LOADING.md` §0 把「公共底线」定义为**两样**：① **已注入的** `框架/AGENTS.md` 硬约束十条 ② 本角色角色包。子 agent 不再被注入 ⇒ ① 这半要改成「自己去读 `AutoDL实验/框架/AGENTS.md`」。实测 `roles/planner.md` · `runner.md` · `feishu.md` 里**都没有**「硬约束」或 `I11/I16/I18/I19/I20` 这些标识（各 0 处）⇒ 不改这一条，这三个角色不会自己去读。
⇒ 建议改法（一行）：把 §0 第 1 条改成「**硬约束十条**：读 `AutoDL实验/框架/AGENTS.md`（不再假设它已被注入）」。**这一步属于框架契约文本，且另一个会话正在改那些文件，所以我不动它。**
⇒ **本改动此后只针对「操作层面的噪声」与上下文预算，⛔ 不再以「历史 / 旧结论」为任何丢弃理由。**

【实测 2026-10-16】一个只回答、不读文件的子 agent 逐字回报：它收到了 `~/.dsh/AGENTS.md` 与项目根 `AGENTS.md` 两个注入块（块首 `Instructions from: AGENTS.md`）。⇒ **子 agent 确实读到了主 agent 那批注入**。

【实测】本会话主 agent 在读了 `AutoDL实验/框架/` 下的文件之后，被**追加注入**了 `AutoDL实验\AGENTS.md` 与 `AutoDL实验\框架\AGENTS.md` 两个块 —— 注入是按「碰到某目录下文件」触发的，不是它主动去读。

### 4.2 机制（读码结论，决定了改在哪一层）

| 事实 | 出处 |
|---|---|
| 注入由股票插件 `@deepseek-ai/dsh-agent-instructions` 在 **`agent/pre-step`** 瀑布里完成，它在 `await next()` **之后**把注入消息插进 `decision.messages` | `lib/index.js` **L1272-1290** |
| 注入消息带来源标记 `source.kind === 'agent-instructions'` + `source.changes` 数组 | 同文件 **L798-800** · **L1171-1180** |
| cordis 瀑布「**listeners run outermost-first**」，且监听器选项 `prepend` 把它 `unshift` 到最前 | `@deepseek-ai/cordis/src/events.ts` **L225-244** · **L255-261** |
| 子级 = 不在 `ctx.agents.roots()` 里（`roots()` = `owner === undefined` 的存活 agent） | `@deepseek-ai/dsh-agent/lib/index.js` **L616-624** |
| 子会话头部带持久谱系 `parentSession`（fork 子级一定有） | `dsh-subagent/lib/types/child-agent.js` **L117** |
| 注入文本的分段标记固定：`Instructions from: <path>` / `Additional instructions from:` / `Updated instructions from:` / `Instructions removed:` | 同 L131-133 · L192-201 · L213-224 |
| 子 agent **继承父的 preset**（`composedPreset(parent.ctx)`）⇒ 没法只给子 agent 换 preset | `dsh-subagent/lib/types/child-agent.js` **L113** · **L158** |
| ⭐ **`agent/pre-step` 发射时不带 `this`** ⇒ `dispatch` 的 `thisArg = null` ⇒ `filter = undefined` ⇒ `!filter` 为真 ⇒ **不按 scope 过滤**，任何上下文注册的监听器都收得到 | `dsh-agent-loop/lib/index.js` **L912**（`this.dispatch.waterfall("agent/pre-step", {…}, …)`，第一个实参是**字符串**事件名） · `cordis/src/events.ts` **L166-176** |
| ⭐ **监听器列表是服务级共享数组**，`prepend: true` 做的是 `unshift` ⇒ 从任何上下文注册都会排到**最前** | `cordis/src/events.ts` **L133** · **L255-261** |

⇒ 结论：**唯一的可行切口是自己挂一个 `agent/pre-step` 监听器，用 `{prepend: true}` 站到最外层**，在股票插件注入完之后把子 agent 的那条消息裁掉或改写。⛔ 不要试图「只给子 agent 换 preset」—— 那条路不存在。

### 4.3 过滤判据

判据只有一份：`install/agent-instruction-filter/policy.mjs`。**现行 = 对子 agent 整份 `drop` 项目级那四份。**

| 文件 | 子 agent 的处置 | 落地形态 |
|---|---|---|
| `~/.dsh/AGENTS.md`（全局偏好：语言等） | **保留**（1,161 B） | `mode: 'keep'` |
| 项目根 `AGENTS.md` | **整份不注入** | `mode: 'drop'` |
| `AutoDL实验/AGENTS.md` | **整份不注入** | `mode: 'drop'` |
| `AutoDL实验/框架/AGENTS.md` | **整份不注入** | `mode: 'drop'` |
| `Kaggle平台/AGENTS.md` | **整份不注入** | `mode: 'drop'` |

⚠️ **`mode: 'drop'` 的作用面**：它把**那一段**（段标记 + 文件头 + 全文）一起删掉；**一段都不剩时整条消息被丢弃** —— 实测包括「只剩一句 intro」的那种空壳消息也会判空，不会留一句没有内容的 `The following workspace instructions …`。

⚠️ **想切回「保留文件、只按段裁操作噪声」**：把对应行的 `mode` 换成 `'filter'`，并照 `SECTION_CLASSIFICATION.md` 补 `stripLeadingComment` / `dropSections` / `dropLines`。那张 76 行逐段表就是为这个备选留的存档。

**实测效果**（`node install/agent-instruction-filter/measure.mjs`，读真实文件）：

```
五份合计 70733 B → 1161 B（砍 69572 B = 98.4%）〔2026-10-16 23:57 · 当前内容 · 见 measure.mjs〕
逐份：~/.dsh/AGENTS.md 1162 → 1161（−0.1%，只少一个尾换行）
      项目根 AGENTS.md 13267 → 0 · AutoDL实验 27731 → 0 · 框架 7947 → 0 · Kaggle平台 20626 → 0（各 100%）
整条基线消息（含 <system-reminder> 框与前言）：71044 B → 1303 B
    （剩下那 1303 B = frame + 前言 + 全局偏好那一段）
```

⚠️ **这些数会漂**：五份 `AGENTS.md` 一改就变，而且 purge 仍在进行。引用前**现跑** `measure.mjs`。

⚠️ **这两个数每次都可能不同**：五份 `AGENTS.md` 一改就变，而**你让另一个会话正在做 purge**（它会删文件、并就地编辑 `AutoDL实验/AGENTS.md`）⇒ 我在本会话里已连续复测三次（46.6% → 35.3% → 36.2%）。引用前**现跑** `measure.mjs`，⛔ 不要抄这里。

⚠️ **为什么只有 36.2%**：分类表里按「对 planner 无用/有害」本该砍 64.4%，其中 **8,373 B** 属**用户裁定要保留**的那六处（§3.4.3 1,720 + §3.5.4 2,286 + §3.5.5 表体 1,132 + §3.5.6 2,428 + `Kaggle平台` §2.1.2 469 + §2.7.4 338）⇒ 差额里最大的一块是**判据变更**，不是没实现。其余差额（无标题的引用块、表格内的单行）登记在分类表末节「未落地」。

⚠️ 规则顺序**必须「具体在前、通配在后」**：第一条命中生效，`**/AGENTS.md` 会吞掉所有同名文件 —— 这条有反向对照钉住。

⚠️ **去文件头 + 按标题丢，全部依赖把 CRLF 归一成 LF**。JS 的 `.` 不匹配 `\r`，所以 `/^(#{1,6})\s+(.*)$/` 在行尾带 `\r` 的真实文件上**一条标题都匹配不到**（2026-10-16 实测：`AutoDL实验/AGENTS.md` 的 21 条标题 0 命中）。`filter-core.mjs` 在入口做归一化，代价 = 被改写过的注入消息统一成 LF。

### 4.4 安装步骤

**⛔ 不要手改 profile 的 `package.json` 或 `cordis.patch.yml`。** 随 DSH 出货的插件开发指引逐字写着：`install_bundle` performs those steps。走 `plugin_manager`：

```jsonc
// plugin_manager 调用参数（target 用绝对路径）
{ "action": "install_bundle",
  "target": "C:\\Program Files\\agent\\kaggle_competition\\install\\agent-instruction-filter" }
```

bundle 内容（本仓库内，`install/agent-instruction-filter/`）：

| 文件 | 作用 |
|---|---|
| `package.json` | manifest；`dsh.bundle.patch` 这一行使本目录成为 bundle |
| `cordis.patch.yml` | 只 `insert` 自己的那一行；⛔ 不覆盖任何出货行 |
| `index.js` | Host 插件：`agent/pre-step` + `{prepend: true}` |
| `filter-core.mjs` | 纯函数：切段、按标题丢小节、按行摘行、重组 |
| `policy.mjs` | **落地判据**（§4.3，现行 = 整份 `drop`） |
| `SECTION_CLASSIFICATION.md` | **逐段分类表**（76 行）—— ⚠️ **已不是策略依据**，只作存档（切回 `filter` 模式时可用） |
| `selftest.mjs` | 离线自检：**纯函数**（切段 · 标题丢弃 · 行摘除 · 文件头剥离 · 重组）+ 策略期望值，18 条（含 6 条反向对照） |
| `selftest-plugin.mjs` | 离线自检：**接线**（用假 ctx 驱动 `apply()`），12 条 —— 含 2 条**顺序对照**（证明没有 `prepend` 就一条都丢不掉） |
| `verify-real-policy.mjs` | ⭐ **真实文件关卡**：直接读五份真 `AGENTS.md`，按**内容**逐条断言（**18 条**）。补的是合成夹具测不到的结构偏差，⛔ 不依赖行号 |
| `measure.mjs` | 只读量具：拿真实文件算裁剪前后字节 |

**离线自检（不需要 DSH，随时可跑）**：

```powershell
$node = "C:\Users\Administrator\AppData\Local\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\node\bin\node.exe"
& $node install\agent-instruction-filter\selftest.mjs
# 期望：PASS: 18 条全通过 · exit 0（纯函数层 + 策略期望值）

& $node install\agent-instruction-filter\selftest-plugin.mjs
# 期望：PASS: 12 条全通过 · exit 0（接线层：prepend、子级判定、整条丢弃、收件箱移除、异常回退、顺序对照）

& $node install\agent-instruction-filter\verify-real-policy.mjs .
# 期望：PASS: 真实文件上 18 条逐条符合预期 · exit 0
# ⭐ 这一条最重要：它读**真文件**、按内容判。能抓住「合成夹具与真实结构不一致」这类假通过
#   （2026-10-16 真发生过一次：夹具漏了真实的父标题 `## 3.4`，于是 `### 3.4.3` 被 `## 3.3` 的丢弃范围吞掉）

& $node install\agent-instruction-filter\measure.mjs .
# 期望：exit 0，并打出一张逐文件 before/after 表（数值会随五份 AGENTS.md 变动，别抄历史值）
```

⚠️ **审批**：`plugin_manager` 的**每个** action（含只读的 `list_plugins`）在没有 Full access 时都要审批。

⚠️⚠️ **而且这个工具不一定在当前会话里存在。** 本机 `profiles/desktop/cordis.patch.yml` 的 `preset-standard` 里 `tool-plugin-manager` 是 `disabled: true`；它只在 **`cordis` preset** 下按 `!!js "!ctx.get('profileContext')"` 启用。⇒ **装载要走创造模式（Creator）：那里开的是 `cordis` preset 的新任务，`plugin_manager` 与 `cordis_inspect_query` 都在。** 在当前这种普通任务里，它既不在工具表里、也没法靠审批放行。

### 4.5 验证

**① 行是否装上**（`cordis_inspect_query` 不需要审批；但它只在 `cordis` preset 下才有该工具）：

```
cordis_inspect_query  → 查 id 为 agent-instruction-filter 的行是否存在、fiberPhase 是否为 active
```

**② 行为验证（决定性，不需要审批）** —— 派一个只回答、不读文件的子 agent，题目逐字：

> 这是一个环境探测任务。**不要读任何文件、不要运行命令**，只根据你收到的系统指令回答：
> 1. 你收到的注入块**一共有几个**？把每个块的开头那一行逐字抄出来。
> 2. 有没有任何块的内容提到 `AutoDL` 或 `Kaggle` 这两个字？
> 3. 有没有任何块出现 `用中文回复用户` 这句话？

期望（现行口径生效）：第 1 问 = **只有 1 个块**，开头是 `Instructions from: ~/.dsh/AGENTS.md` · 第 2 问「**没有**」· 第 3 问「**有**」。
**反向对照**：把 `cordis.patch.yml` 的 `config.mode` 改成 `off`（或把 `policy.mjs` 的四条 `drop` 改成 `keep`）后重跑同一探测 —— 这次应当**收到 2 个块**（`~/.dsh/AGENTS.md` + 根 `AGENTS.md`），第 2 问变成「有」。两问都照预期才算通过。

**③ 主 agent 不受影响**：同一会话里读一次 `AutoDL实验/AGENTS.md`，`§3.5.5` 必须**仍然在**（主 agent 不在本插件的处置范围内）。

### 4.6 回滚

| 想退到哪 | 怎么做 |
|---|---|
| 只停过滤、保留插件 | `cordis.patch.yml` 的 `config.mode: off`，重新 install |
| 完全卸载 | `plugin_manager` `remove_bundle`（本 bundle） |
| 临时应急 | 无需 DSH 操作：把 `policy.mjs` 的每条规则改成 `mode: 'keep'` |

⚠️ 改完 `config` 或代码后，**新建会话**才生效；已有 agent 的插件与提示词不变【读文档：`dsh-agent-preset-registry` README】。

### 4.7 已知限制与未验证项（⛔ 读之前别当成已验证）

1. **本插件从未在本机装载过** ⇒ 真机行为未实测。原因不是审批，而是 `plugin_manager` **不在普通任务的工具表里** —— 装载路径见 §4.4（创造模式 / `cordis` preset）。
2. **两条关键机制结论已定位到源码（【读码】，仍非实测）**：① `prepend: true` 做的是 `unshift` 到**服务级共享**监听器数组的最前；② `agent/pre-step` 发射时不带 `this` ⇒ 不按 scope 过滤 ⇒ profile 级监听器也收得到。**接线层已用假 ctx 离线自检 12/12**（`selftest-plugin.mjs`），**策略已用真实文件逐条核过 55/55**（`verify-real-policy.mjs`），但「真机上事件确实到达、注入确实被改写」仍要按 §4.5 的 ② 实测。
   ⇒ 若真机上没生效，退化路径 = 把这一行插进 preset 的 `plugins` 列表（那要整份重述 `preset-standard`，因为 `config` 是**整份替换、不深合并**）。本机 `profiles/desktop/cordis.patch.yml` 已经是那种形态，可作模板。
3. **分段按行首标记切**：若某个 `AGENTS.md` 的正文里有一行以 `Instructions from: ` 开头，会被误判成新段起点。
4. **标题丢弃不看代码围栏**：围栏里长得像 `### 3.5.5 …` 的行也会参与判断。
5. **进程外子 agent 管不到**：`subagent_codex` / `subagent_claude_code` / `subagent-acp` 是独立进程或外部 CLI，本插件不作用其上【推断】。
6. **升级 DSH 后要复核**：股票插件的注入形态（段标记、`source.kind`）变了，本插件会静默失效（fail-open，见下条）。
7. **异常策略默认 fail-open**（`onError: keep`）：插件报错时保留原注入，⛔ 不会因为插件坏了而弄坏 agent 循环。想反过来的话把 `config.onError` 改成 `drop`。

---

## 5. DSH 环境改动（之二）：回合结束的「文档遗漏」提醒插件

`install/dsh-doc-gap/` —— 一个 Host 插件。它只做一件事：**在回合即将关闭时判一次「你这次问的那个点，项目文档说了没有」**，
没说清就问你要不要补。**插件不指定写进哪份文件** —— 落点由会话按项目规矩（`AGENTS.md` 的落点表、`DOC_MAINTENANCE.md` 的分层规则）自己判断。

### 5.1 预期表现（照这张表验收）

| 条件 | 预期表现 | 证据 |
|---|---|---|
| 会话 cwd 不含 `kaggle_competition` | 完全静默；只有 `logLevel: debug` 时才打一行「不在目标项目内」 | 【实测】 |
| cwd 命中、普通回合结束 | 发起一次判定；判据文档 3.7 万字符，粗估多约 2.7 万输入 token、阻塞 1–5 秒 | 【实测】 |
| 判定 `covered`，或 `p_missing` < 阈值 `0.8` | 静默，回合不多跑一步 | 【实测】 |
| 判定 `missing`／`partial` 且 `p_missing` ≥ `0.8` | 回合末多一步：agent 调 `ask_user_question` 问「补（Recommended）／先不补」 | 【实测】 |
| 你答「补」 | agent 按**它自己判断的落点**、按该文件现有章节结构追加 | 【实测】一次实测里它选了 `EXPERIMENT_RULES.md` §1.8，并逐条说明为什么不是 `RULES.md`／`AGENTS.md`／`EXPERIMENT_LOG.md`／`PARALLEL_AND_COST.md` |
| 你答「先不补」／工具超时／无 answerer | 一个文件都不改 | 【实测】 |
| 子 agent 的回合 | 不触发 | 【读码】`onlyRoot` 用 `agents.roots()` 过滤 |

### 5.2 装载（⛔ 不能用 CLI）

```jsonc
// plugin_manager 调用参数（target 用绝对路径）
{ "action": "install_bundle",
  "target": "C:\\Program Files\\agent\\kaggle_competition\\install\\dsh-doc-gap" }
```

在**创造模式（`cordis` preset）**里调 `plugin_manager`，理由与 §4.4 同款。

⛔ **CLI 装不了 `desktop`**：实测 `dsh plugin --profile desktop add <路径>` 直接被拒 ——
`error: profile "desktop" is managed exclusively by the Electron application`。
临时试装可以塞进 `web` profile，但**那不会被当前应用加载**（本机 `DSH_PROFILE=desktop`）。

### 5.3 自检（不需要 DSH、不联网、不花 token）

```powershell
$node = "C:\Users\Administrator\AppData\Local\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\node\bin\node.exe"
& $node install\dsh-doc-gap\selftest.mjs           # 期望：PASS: 34 条（纯函数层）
& $node install\dsh-doc-gap\selftest-plugin.mjs    # 期望：PASS: 17 条（接线层：假 ctx 驱动 apply，含反向对照）
& $node install\dsh-doc-gap\verify-real-docs.mjs . # 期望：PASS: 真实文件上 16 条（含 1 条反向对照）
& $node install\dsh-doc-gap\measure.mjs .          # 期望：exit 0 + 一张「字符数／是否会被截断」表（数值会漂，别抄）
```

### 5.4 回滚

| 想退到哪 | 怎么做 |
|---|---|
| 只停提醒、保留插件 | `cordis.patch.yml` 的 `config.enabled: false`，重新 install |
| 只停某个项目 | 从 `config.projects` 去掉该项目（**空列表 = 所有项目都生效**，不是「都不生效」）|
| 完全卸载 | `plugin_manager` 的 `remove_bundle`（本 bundle）|

改完 `config` 要**新建会话**才生效【读文档：`dsh-agent-preset-registry` README】。

### 5.5 已知限制（⛔ 读之前别当成已验证）

1. **从未在本机装载过** ⇒ 真机行为未实测。接线层自检覆盖了「事件到达 → steer」，但真机验收仍要按 §5.1 走一遍。
2. **判据文档必须小于 `maxDocChars`**，否则整份判「已截断」，判定被强制降为 `partial` 且 `p ≤ 0.6` ⇒ **永不触发**。现跑 `measure.mjs` 取余量；改那份被当判据的文档（现为 `EXPERIMENT_RULES.md`）后也要重跑。
3. **`judge.maxTokens` 给小了会「只想不说」**：推理模型的思考 token 也算在内，输出为空 ⇒ 整条链路静默失败。默认 4000；实测 `400` 直接失败。
4. **`session` 后端的 `p_missing` 是模型自报置信度**，不是校准概率【读码】。要真概率得换 `judge.backend: jev` 或 `openai`。
5. **`jev` / `openai` 两个后端从未实测**【未验证】。
6. **只有 Web 侧挂 `ask_user_question`**：`headless` profile 没有这个工具，`notify: ask` 会退化成文字提问【实测】。
7. **判定阻塞回合关闭 1–5 秒**：它跑在被 await 的 `agent/turn-stopping` 里【读码 + 实测】。嫌慢调 `judge.timeoutMs`（超时即放弃，不打断回合）。
8. **判据是「几份文档」不是「整个仓库」**：只比对 `config.docs` 列出的那些；别的文档里写了它照样判 missing。加文档 = 加准确性，也加每回合 token。

---

## 6. DSH 改动（之三，可选）：关掉会话自动压缩（compaction）

> **为什么标「可选」**：它不改仓库、不改提交包、不改任何实验读数 —— 只改本机 DSH 的行为。复现本项目**不需要**做这一步。但跑长会话的人一定会撞上它，撞上时若不知道有这个开关，会把「模型忘了前面的事」错记成模型能力问题。

### 6.1 症状（本机实测数）

`standard` preset 默认挂 `@deepseek-ai/dsh-compaction-basic`：上下文涨过阈值时，**最旧的一段历史被一条摘要替换**，近期尾部逐字保留。表现在界面上就是**上下文占用突然掉一大截**；日志里留下 `compaction/start` → `compaction/summary` → `compaction/end` 三条事件（`compaction/*` 只进日志，不上面）。

{实测 2026-10-17 · 本机 · `session-cf41890d-…` turn 22}：一次压缩把单次请求的 prompt tokens 从 **677,179 掉到 192,317**（-72%）。同一次之后的请求里 `cacheRead` 从 676,992 掉到 6,912 —— 替换点之后的前缀缓存全部失效 ⇒ **那一次请求更慢也更贵**。

阈值与保留量的公式【读码】`dsh-compaction-basic/lib/index.js` L130-134：

```
threshold = floor(min(W × 0.8, W − O − 65536))
retain    = floor((W − O) × 0.16)
```

本机 `deepseek-flash` 的 `W = 1,000,000`（`dsh-llm-deepseek` 的 `DEFAULT_CONTEXT_WINDOW`），本机路由请求的 `maxTokens = 256,000` ⇒ **触发阈值 678,464 · 逐字保留 119,040**。`auto` 默认 `true`。

### 6.2 改哪里（本机已按此法改过）

改 `$env:DSH_PROFILE_DIR\cordis.patch.yml`：`preset-standard` → `compaction` 组 → `compaction-basic` 那一行，给它加 `config.auto: false`。

```yaml
          - id: compaction-basic
            name: '@deepseek-ai/dsh-compaction-basic'
            config:
              auto: false
```

语义（【读文档】该包 README「自动 vs 仅手动」+【读码】`config.ts` 的 `auto must be a boolean`）：

| `auto` | 压力到阈值自动压 | 溢出（`CONTEXT_WINDOW_EXCEEDED`）后压一次再重试 | `/compact` 手动 |
|---|---|---|---|
| `true`（默认） | 有 | 有 | 有 |
| `false` | **无** | **无** | 仍有 |

⚠️ 同一组里另两行**不要动**：`command-compact` 提供手动 `/compact`；`compaction-tool-result-pruner` 只在压缩过程中修剪超大工具输出（`thresholdChars: 8192`）⇒ `auto: false` 之后它只在手动压缩时跑。

### 6.3 生效与验证

**必须重启 Electron 应用才生效。** 依据【读码】：patch 层只在 host 启动时组装一次（`dsh-app-boot` 的 `applyPatches`）；`dsh-app-boot` · `cordis-plugin-loader` · `@deepseek-ai/cordis` 三处都没有配置文件监听器，也没有「重载 profile」的 API。改完不重启**不会有任何提示**。

**离线验证只能到文件层** —— `--dump-config` 对 `desktop` 被拒（与 §5.2 是同一条限制）：

```powershell
# 期望输出（原文）：error: profile "desktop" is managed exclusively by the Electron application
dsh --profile desktop --dump-config
```

代替做法：用任意 YAML 解析器读那份 patch，判两点。⚠️ 该文件含 `!!js` 表达式，解析器要先注册 `tag:yaml.org,2002:js` 这个标量类型，否则解析直接抛错（不是配置错）。

| 判据 | 期望 |
|---|---|
| `compaction` 组三行 | `compaction-basic={"auto":false}` · `command-compact` · `tool-result-pruner={thresholdChars:8192,headChars:4096,tailChars:1024}` |
| 结构与改前一致（本机实测） | 顶层 patch 条目 **9** 条 · `preset-standard` 下 **19** 个插件 |

**重启后的验收**：跑一个长会话；上下文占用涨过 678K **仍不往下掉** = 生效；日志里再出现 `compaction/start` = 没生效（或改错了位置）。

### 6.4 回滚

| 想退到哪 | 怎么做 |
|---|---|
| 恢复默认（自动压缩全开） | 删掉 `config` 那三行，或把 `auto: false` 改成 `true`，然后重启 |
| 连手动 `/compact` 一起关 | 给 `command-compact` 那行加 `disabled: true`，然后重启 |

本机改前的副本：`%DSH_PROFILE_DIR%\cordis.patch.yml.bak-20261017-2124`（10,051 B）。⚠️ 这是**本机实例**的文件名，别的机器不会有。

### 6.5 已知限制（⛔ 读之前别当成已验证）

1. **关掉就没有兜底了**：会话涨到接近窗口上限（本机 ~1,000,000）时 provider 直接拒请求，DSH 报上下文超限且**不再自动压缩重试**。届时只能手动 `/compact` 或新开会话。
2. **界面上的占比圆环照旧工作**：它读 `ctx.tokenMeter`，与压缩无关 ⇒ 关掉后你**看不到**「掉下去」，别把这当成「没生效」。
3. **不重启等于没改**，且没有提示（§6.3 有读码依据）。
4. **`desktop` profile 只能由 Electron 应用改**：CLI 的 `dsh plugin --profile desktop …` 会被拒（§5.2 的同一条报错）。
5. **本机改后未重启验证**〔2026-10-17 21:29〕：我只做了文件层校验（YAML 能解析、`auto: false` 就位、结构与改前一致），**没有重启应用**（会掐断当轮会话）⇒ 真机行为【未验证】。

---

## 7. DSH 改动（之四）：把「AutoDL 机器从开不了变成能开」推进会话

> **用户 2026-11-07 原话**：「目前 status.md 中间虽然有脚本一直在维护 autodl 服务器是否可用，但是我想让某些服务器从不可用变成可用的时候向 dsh 发送一条事件，让模型能立刻感知到。」

**为什么值得装**：这台机器上页面顶部的机器表每分钟都在刷新，但**只有人打开页面才看得见**。本项目此前没有宿主动推送 —— 能用的只有「后台作业结束 ⇒ DSH 通知会话」这种一次性叫醒（`AutoDL实验/框架/code/orchestrator/REQUESTS.md` R336 登记过）。这个插件把那个缺口接上：状态一变，会话里自己多一条消息，**空闲会话也会被叫醒**（用户同日选定）。

**判据与链路正文**在 [`AutoDL实验/框架/AUTODL_EVENTS.md`](AutoDL实验/框架/AUTODL_EVENTS.md)（什么算一条事件 · 为什么只发「变成可用」 · 冷却期 · 为什么用 `steer`）。本节只说**怎么装、怎么验、怎么退**。

### 7.1 它由两半组成

| 半 | 在哪 | 谁跑 | 装法 |
|---|---|---|---|
| 事件判定 | `AutoDL实验/tool/autodl_events.py` | Windows 计划任务每分钟那条 `topic_watchdog.py --availability-only --all --once` **顺手调** | **不用装**：2026-11-02 起那条计划任务就在跑，改动自动生效 |
| 投给谁（专题绑定） | 同一 tick 里读 `topics.sqlite` 的 `leader_session_id`，写 `temp/autodl_events/targets.json` | 同上 | **不用装** |
| 事件投递 | `install/autodl-events/`（Host 插件） | DSH 宿主进程 | 下面 §7.2 那一行 + 重启 DSH |

⇒ 只装投递、不装判定 = 队列永远是空的；判定那半已经在了。

### 7.2 装载（绝对路径 `insert` 一行）

本机有一条已经跑通的先例：`dsh-stt-whisper` 就是用绝对路径挂进 `profiles/desktop/cordis.patch.yml` 的【实测 · 该行在该文件里，语音转写工作正常】。照同一形态：

```yaml
# $env:DSH_PROFILE_DIR\cordis.patch.yml 末尾（本机 = C:\Users\Administrator\.dsh\profiles\desktop）
- insert:
    - id: autodl-events
      name: 'C:/Program Files/agent/kaggle_competition/install/autodl-events/lib/index.js'
      config:
        eventDir: 'C:/Program Files/agent/kaggle_competition/temp/autodl_events/pending'
        logFile: 'C:/Program Files/agent/kaggle_competition/temp/autodl_events/delivered.jsonl'
        # 投给谁：这个文件由 Python 侧每分钟从「专题负责人绑定」重写（⛔ 不要手改）
        targetsFile: 'C:/Program Files/agent/kaggle_competition/temp/autodl_events/targets.json'
        fallback: pick        # 没有绑定 / 绑的人不在场时：pick = 退回挑 1 个；hold = 不发
        pollMs: 5000
        maxAgeMinutes: 45
        projects: [ 'C:/Program Files/agent/kaggle_competition' ]
        delivery: steer
        maxTargets: 1
```

⚠️ 文件末尾**已经有一个 `insert:`**（`dsh-stt-whisper` 那一段）⇒ 再写一个 `- insert:` **列表项**是合法的（两者是同一数组的两个元素）；⛔ 不要写成同一个 mapping 里的第二个 `insert:` 键。

⚠️ **投给谁**：绑定来自 `topics.sqlite` 的 `topics.leader_session_id`（`status='active'` 且非空），每分钟刷进 `targets.json`。
**本机按用户 2026-11-07 的决定用 `fallback: hold`**：没有绑定会话时**一条都不发**（事件留在队列，45 分钟后过期）。
⇒ 2026-11-07 21:43 现读 `combo-optimize` 的负责人是**空**，所以此刻事件不会推进任何会话 —— 这是选定口径，不是故障。
要恢复「没绑定也挑一个」，把 `fallback` 改回 `pick`。

⛔ **不要试图用 CLI 装 `desktop`**：`dsh plugin --profile desktop add <路径>` 直接被拒（报错原文见 §5.2）。

### 7.3 生效条件：改 config 热、改代码冷

| 改了什么 | 要不要重启 DSH | 判据 |
|---|---|---|
| `config` 里的值 | **不要依赖热加载** | ⚠️ **实测结论改了两次**：20:42 / 21:44 / 21:49 三次改完 patch 后**立刻**出现 `kind:"apply"`，所以当时写成「改 config 热生效」；但 22:22 那次改完 **100 秒内没有任何 `apply`** ⇒ 触发条件不明（可能是窗口重载/设置页面等别的动作引起的），⛔ **不能依赖**。以「真正重启」为准 |
| 插件代码 | **要** | 模块被 ESM 缓存：`apply` 行里要是没有新加的字段，说明跑的还是旧代码〔实测〕。换文件名可以绕过缓存（本机就为此把入口改成 `lib/plugin.js`，旧路径留转接层） |

**判据（唯一可靠）**：`temp/autodl_events/delivered.jsonl` 最后一行 `kind:"apply"` 的 **`pid` 变了**、且字段里有 `maxTargets` / `projects` ⇒ 新代码在跑；`pid` 没变就是没重启成功。

⚠️ **2026-11-07 22:2x 的现场教训**：用户说「我重启了」，但宿主进程 `pid 20720` 的启动时间还是 **10-31 09:49:43**、台账里没有新的 `apply` ⇒ 那次只重启了窗口/会话，**宿主进程没换**。⇒ 判「有没有真重启」不要问，看 pid。

### 7.4 验证（四层离线 + 一条真机）

```powershell
# ① 事件判定（13 组夹具）
python -X utf8 -B AutoDL实验/tool/autodl_events.py --selftest        # 期望：PASS: 13 组全通过

# ② 插件纯函数 + ③ 插件接线（假 ctx 驱动 apply）
$node = "C:\Users\Administrator\AppData\Local\Programs\DeepSeek Harness\resources\runtime\primary-runtime\dependencies\node\bin\node.exe"
& $node install\autodl-events\selftest-core.mjs                        # 期望：PASS: … 47 条
& $node install\autodl-events\selftest-plugin.mjs                      # 期望：PASS: … 33 条

# ④ 真宿主装载（⛔ 不碰 profile：用一次性覆盖层）
& $node "C:\Program Files\dsh\apps\cli\lib\bin.js" --profile headless `
    --patch "C:\Program Files\agent\kaggle_competition\temp\autodl_events_overlay.yml" "只回四个字母：OKAY"
# 期望：正常答 OKAY；随后 temp/autodl_events/delivered.jsonl 多一行 {"kind":"apply",...}
```

**真机验收（唯一能证明「这台机器的 DSH 会叫醒我」的证据）**：往 `temp/autodl_events/pending/` 放一条**当前时间**的事件 JSON（字段照 `autodl_events.py` 注释里的样例；⛔ 别用 PowerShell 的 `Set-Content -Encoding utf8` 写 —— 5.1 会加 BOM，插件会把它当坏文件删掉），然后：

| 判据 | 期望 |
|---|---|
| 会话 | 5 秒内多一条 `[AutoDL 机器事件] …` 开头的 user 消息（**只发给一个会话**） |
| 队列 | 那个文件从 `pending/` 消失，出现在 `delivered/` |
| 台账 | `delivered.jsonl` 多一行 `kind:"delivered"`，带 `machines` / `sessions` / `candidates` |

⚠️ 三样都对才算装好。只看「消息没来」分不清是「没装上」「没有事件」还是「被 `waiting` 挡住」—— 台账的 `waiting` 行会写原因。

### 7.5 回滚

| 想退到哪 | 怎么做 |
|---|---|
| 只停投递、保留事件落盘 | 那一行加 `enabled: false`，重启 DSH |
| 完全卸载 | 删掉 `insert` 里 `id: autodl-events` 那一项，重启 DSH |
| 连事件判定也停 | 改 `topic_watchdog.py` 的 `--availability-only` 分支（⛔ 那会动到每分钟刷机器表那条既有链路，改前先归档） |

### 7.6 已知限制（⛔ 读之前别当成已验证）

1. **本机已装、已验到「事件进会话」**〔实测 2026-11-07 20:39〕：计划任务 20:39:34 判出「008机 服务器腾出显卡」（`idle` 触发，0→1），插件 20:39:38 把它投进了桌面宿主，会话里真的收到那条消息。改前件 `cordis.patch.yml.bak-20261107-2130-before-autodl-events`（12,602 B，本机实例文件名）。
2. **那次同时暴露两个缺陷，都已改**：① 投给了 **36 个**会话（首版没有 `maxTargets`）；② 会话筛选按**子串**匹配工作目录，把 `…\Temp\kaggle_competition` 那份克隆的会话也算进来。⇒ 现在 `maxTargets: 1` + `projects` 写**绝对路径前缀**。⚠️ 旧代码还在宿主内存里（ESM 缓存），新代码要**重启 DSH** 才生效；在那之前旧代码会因路径前缀不匹配而把事件**留在队列里不动**（不会群发）。
3. **宿主不在就没有投递**：DSH 关着时事件照旧落盘（判定跑在 Windows 计划任务里），下次 DSH 起来补投；超过 `maxAgeMinutes`（默认 45 分钟）的直接进 `expired/` 丢掉。
4. **不做跨重启去重**：正常不会重发（投完就把文件挪走），但宿主若在「已 steer、还没挪走」之间崩掉，同一条可能再投一次。
5. **「能开」永远是推断**：判词来自只读接口字段，**没有试开机**。事件说「看起来能开」，⛔ 不等于「一定开得起来」，也⛔ 不等于已经开机。
6. **一次投递 = 一个会话开一个新回合**（有 token 成本）。这是「叫醒」的代价，护栏是 `maxTargets=1` + 同一批合并成一条 + 45 分钟以上不投。
7. **DSH 升级后要复核**：`agent.steer()` / `agents.roots()` / `session.seq` / `ctx.effect()` 任何一处变了，插件会静默失效（异常被吞，只记 warn 与 `waiting` 行）。

---

## 8. 换机器时的差异点

| 项 | 为什么不写死 |
|---|---|
| profile 名与目录 | 本机是 `desktop`，别的机器可能不同 ⇒ 一律读 `$env:DSH_PROFILE` / `$env:DSH_PROFILE_DIR` |
| DSH 安装路径 | 上面 §2 的路径是本机实测值，别处要做同样的事得先定位 `app.asar` |
| AutoDL 实例、SSH 端口、机型 | 在 `AutoDL实验/实验记录/AUTODL_SETUP.md` |
| 平台提交 ref 与分数 | 会漂，在 `Kaggle平台/实验/SUBMISSIONS_LOG.md` 与 `AutoDL实验/实验记录/BASELINES.md` |

---

## 9. 参考：本仓库的规矩入口

| 要什么 | 去哪 |
|---|---|
| 入口索引（五份上下文各有什么） | `AGENTS.md` |
| 全局规则正文（文件头 · 目录纪律 · 证据标注 · 术语表） | `RULES.md` |
| 文档怎么分层、热件上限 | `DOC_MAINTENANCE.md` |
| Kaggle 线（数据源 · 信息源 · 提交） | `Kaggle平台/AGENTS.md` |
| 实验线（硬前置 · 抓手 · 成本） | `AutoDL实验/AGENTS.md` |
| 实验框架（HIL） | `AutoDL实验/框架/AGENTS.md` · `AutoDL实验/框架/CONTEXT_LOADING.md` |

---

## 10. 可选：观察者接入原生 goal

**变更说明〔2026-11-10〕**：新增第五项 DSH 环境改动。原生 goal 负责目标、轮数与暂停恢复，观察者负责每轮下一步。本节使用已通过三轮验收的固定版本安装包。原第0至9节编号保留。

**当前维护版〔2026-11-22 更新〕**：观察者现为**按专题固定的单一续接子会话**（同一专题跨 goal 切换、跨 revision、跨会话交接都复用同一身份；预算默认 48 任务 / 3.6M 字符，真溢出才轮换）；遇到需要真人决定、付款、开机或授权不足的事，经现有飞书动作问人（带选项发审批卡、无选项发文本），默认等 2 分钟——**超时未回复：线上提交类按未批，其余一律自动允许**（有选项按第一项执行）；主会话上下文见底改派收尾交接。现行机制、部署、加载核验与本机宿主坑只维护在[观察者说明](<install/dsh-goal-observer/README.md>)的「按专题固定 + 问人版」。新机部署用 `install/dsh-goal-observer/install-continuable.mjs`（自动识别升级或全新安装；要求 2 分钟内的只读宿主核验）。加载验证【实测判据】：`temp/goal_observer_runtime/loaded.json` 须含 `version: 0.2.1-timeout-allow` 与 `timeoutDefault: allow-except-submit`，且 `pid` 为当前宿主进程——历史存在不算已加载。本节10.1至10.5保留0.1.3包的历史基线，不用旧包覆盖本机后续修复。

### 10.1 安装物与适用范围

| 项 | 固定版本或落点 |
|---|---|
| bundle | `dsh-goal-observer-v013@0.1.3` |
| 项目内安装包 | [dsh-goal-observer-v013-0.1.3.tgz](<install/dsh-goal-observer/dsh-goal-observer-v013-0.1.3.tgz>) |
| 发布物来源与哈希 | [发布物索引](<install/dsh-goal-observer/README.md>) |
| 声明的宿主依赖【读包声明】 | DSH agent / goal / LLM / session 均为 `0.2.0-rc.2`，Cordis 为 `~4.0.4` |
| 实测环境 | Windows，DSH `0.2.0-rc.2`，`desktop` profile〔2026-11-10 · 原桌面宿主〕 |
| 生效范围【读码】 | 当前 profile 内 objective 以 `[观察者]` 开头的原生 goal；不按 Kaggle 工作目录筛选 |

先由 agent 读取实际 profile 和宿主版本。版本不兼容时不要强行豁免。当前主机已安装，不需要为阅读本说明重复安装。

### 10.2 新机器安装

让带有 `plugin_manager` 的 agent 执行。它先调用 `list_bundles` 和 `list_plugins` 获取当前条目，再安装项目内的包。

```json
{
  "action": "install_bundle",
  "target": "C:\\Program Files\\agent\\kaggle_competition\\install\\dsh-goal-observer\\dsh-goal-observer-v013-0.1.3.tgz",
  "enabled": true,
  "registry": "https://registry.npmjs.org",
  "approvedBuilds": [],
  "acceptRisk": false
}
```

`target` 改为新机器的仓库绝对路径。此包来自本项目，不是已发布的 npm 包，也不需要猜 GitHub URL。

安装后检查以下条目：

| 条目 | 期望 |
|---|---|
| `include:goal-observer` | `moduleName=dsh-goal-observer-v013/v013`、`enabled=true`、`fiberPhase=active` |
| `include:goal-observer-probe` | `enabled=false`。包内验收器包含原机器的工作目录与输出路径，新机器不直接启用 |
| 旧 `include:turn-watch`（若存在） | `enabled=false`，避免旧子会话接力与新观察同时推进 |

通过管理器停用旧版的调用是 `set_plugin`，`target=include:turn-watch`，`enabled=false`。仅在列表确认该条目存在时执行。

**生效判据【实测】**：本版本通过独立包名和模块入口在当前桌面宿主热加载成功，没有重启。安装器报告 `restart-required` 或条目不是 active 时，不宣称已生效。相同包名更新会受到模块及导出表缓存影响；后续升级使用新的包命名空间，或获得用户重启授权后再更新同名包。仅改磁盘版本号不算验证。

### 10.3 怎么用与控制

用户直接说：

> 用观察者模式推进：目标是……；验收标准是……；最多自动执行20轮。

主 agent 创建原生 goal：

```json
{
  "objective": "[观察者] 目标：……。验收标准：……。",
  "max_goal_rounds": 20
}
```

`[观察者]` 必须在 objective 开头。普通 goal 不调用观察者。创建目标的当前真人轮沿用原生行为，观察者从后续自动 goal 轮开始介入。

| 操作或结果 | 行为【读码与集成测试】 |
|---|---|
| 继续执行 | 观察者返回具体 instruction，同一步追加独立来源的指令，再由主 agent 执行 |
| 完成 | 观察者提供证据，记录后将原生 goal 标为 complete，不再执行下一轮 |
| 暂停或异常 | 旧0.1.3包曾暂停。当前维护版保留后来用户要求的不暂停、重试与失败放行，见节首说明 |
| 用户点击停止 | 取消当前工作，插件不把已取消的真人输入重新入队 |
| 用户要求改目标 | 主 agent 读取当前 id/revision 后 edit，保留前缀；旧 revision 的观察结论作废 |
| 用户要求恢复 | 主 agent 读取当前 id/revision 后 resume；累计轮数不清零，额度需有剩余 |

插件保留原生 goal 固定消息，追加 `source.kind=goal-observer` 的输入。它不关闭宿主 invariant。当前维护版使用独立可续接无工具子会话，具体规则见节首的观察者说明。项目的审批与授权规则继续适用。

### 10.4 验证

**已有验收〔2026-11-10 · 复制原桌面宿主报告，未在本 Kaggle cwd 重跑〕**：

| 证据 | 设置与结果 |
|---|---|
| [集成测试报告](<install/dsh-goal-observer/integration-test-results.json>) | 真实宿主模块、五个 invariant、确定性模型 adapter。16项通过，覆盖两种注册顺序、额度停止、用户停止、插话、改目标、暂停恢复、异常响应及取消输入不复活 |
| [真实模型报告](<install/dsh-goal-observer/live-probe-v3.json>) | 当前桌面宿主 `gpt/gpt-6.1-sol`，无工具会话。实际读取运行版本0.1.3。三轮分别输出“验收一：青”“验收二：蓝”“验收三：紫”，随后 goal complete，roundsStarted=3，errors=[] |

**新机器行为验收**：另建无项目操作的测试会话，创建以下目标，设置 `max_goal_rounds=4`：

> [观察者] 分三个自动执行轮次，依次只输出“验收一：青”“验收二：蓝”“验收三：紫”。每次只安排当前缺少的一项，不合并。历史记录已有三项准确输出后，观察者引用证据并判定完成。主 agent 不使用工具，不修改文件。

检查三条输出分属三个执行轮次，日志有三个不同的观察指令，最终 goal 为 complete，roundsStarted=3，且没有第四次主 agent 执行。原验收用四次观察调用、三次主 agent 执行；第四次观察只检查完成，不增加执行轮数。

**为什么额度设4**：若额度正好是3，第三轮结束后原生 driver 会先按额度封顶，标为 `blocked(round-limit)`。本版不突破额度再做完成检查。此处多留一个候选轮供观察者判定完成。

真实模型报告只覆盖三轮推进和完成。停止及竞争输入用确定性 adapter 验证，不将它们写成真实模型停止实测。

### 10.5 限制、故障检查与回滚

| 情况 | 处理 |
|---|---|
| 等待用户回答的提问工具 | 本版未接 `user-questions/request`，不能直接代答。不要将它当成已支持的无人值守功能 |
| 长期目标遗漏旧证据 | 默认只读取最近24000字符历史。让主 agent 补查并输出明确证据；完成仍是模型判断 |
| 观察调用超时 | 默认60000毫秒后发取消信号，结束速度依赖 adapter 响应取消 |
| 普通目标没有观察指令 | 核对前缀，以及 goal 是否 active、armed 且有剩余额度 |
| goal 已暂停 | 查看 `goal-observer` 或 `goal-observer-error` 来源消息，处理原因后由用户要求恢复 |
| 宿主重启后的恢复 | 沿用原生 goal 的重新授权语义；本轮尚未端到端实测从 JSONL 重建并恢复 |
| 停用或回滚 | 先暂停正在运行的观察者目标，再用管理器 `set_plugin` 停用 `include:goal-observer`，或 `set_bundle` 停用 `dsh-goal-observer-v013` |

停用插件不会删除原生 goal 台账。先暂停可避免卸载后仍按原生方式续跑。若要卸载包，使用管理器 `remove_bundle`，目标为列表里的准确 bundle 名。

### 10.6 在目标通知中显示观察者指令

显示插件 [dsh-goal-observer-ui](<install/dsh-goal-observer-ui/package.README.md>) 把已有观察记录显示在“继续执行目标”的展开体内。它按目标 ID、版本与轮次匹配独立的 `goal-observer` 消息。展示内容包括决定、下一步与判断依据。模型调用失败时显示已有错误原文。

**只改显示副本。** 原生 goal 消息和观察者记录都保留原样。插件不调用模型、不恢复目标，也不把观察者消息改为真人来源。已有历史轮次可以显示，前提是对应记录已加载到浏览器。没有匹配记录的通知保留原样。

安装时让带 `plugin_manager` 的 agent 执行：

```json
{
  "action": "install_bundle",
  "target": "C:\\Program Files\\agent\\kaggle_competition\\install\\dsh-goal-observer-ui",
  "enabled": true,
  "registry": "https://registry.npmjs.org",
  "approvedBuilds": [],
  "acceptRisk": false
}
```

也可以在应用的“插件”页安装同一绝对路径。安装器必须报告 `application=applied`，随后刷新现有 GUI 页面。无需新服务器。若报告需要重启，不将磁盘改动当成已经生效，也不自动重启宿主。

离线回归命令：

```powershell
node --test install/dsh-goal-observer-ui/client.test.mjs
```

界面验收：打开一个已有观察记录的会话，展开“继续执行目标”。顶部应显示“观察者本轮指令”，且轮次与本通知一致。原生 `<goal_round>` 正文保留在其后。折叠后仍保留原通知标题和时间。

【实测 2026-11-11】通过当前桌面应用管理器安装，返回 `application=applied`，没有重启宿主。独立浏览器访问现有 `http://127.0.0.1:19387`，在“Combo专题分析与抓手探索”会话的历史通知中确认第3至6轮显示了匹配的观察记录。第7轮原本没有对应记录，通知保持原样。

回滚只需在插件管理器停用 `@local/dsh-goal-observer-ui` bundle，然后刷新页面。不必暂停观察目标，因为本插件仅负责显示。

本节改前件：[INSTALL_20261111_改前_观察者指令显示.md](<archive/INSTALL_20261111_改前_观察者指令显示.md>)。该件与改前正文 SHA256 一致，安装步骤没有改写此前的观察者运行机制。

---

## 11. DSH 改动（之六）：项目报告审阅插件

插件源码与正文入口在
[install/dsh-status-review/README.md](<install/dsh-status-review/README.md>)。安装、验收、回滚与判据都写在那份 README。本节只给入口与范围事实，⛔ 不复制 README。

### 11.1 安装入口

| 项 | 事实 |
|---|---|
| 装载形态 | 往 profile 补丁层加一行，形态与 §7 相同。它不是独立 profile。 |
| 生效范围 | 插件随 profile 全局加载。行为只作用于 `projectRoot` 内的 root agent。 |
| 其它会话 | 不注册 `submit_topic_report`，也不改 assistant 文本。 |
| 本机状态 | **续接版已加载**：当前正式加载标记见[scribe标记](<temp/status_review_runtime/loaded.json>)。续接夹具、部署版本与边界见[插件说明](<install/dsh-status-review/README.md>)。下段v5是历史验收，不作为当前版本性能数据。 |

安装步骤、离线自检命令与 `allowReportPath` 的接线要求见 README。

**隔离夹具验收（v4 → v5，2026-11-20）**：v4 结果 [`status-review-probe-v4-result.json`](<temp/status_review_verification/status-review-probe-v4-result.json>)，host pid 8840，46.239 s，scribe 会话 `97bacb82-59f5-429d-960d-8b6b9c3aa974`。**v5 最终验收** [`status-review-probe-v5-result.json`](<temp/status_review_verification/status-review-probe-v5-result.json>) 为 `pass: true`：44.686 s，scribe 会话 `96a0b05d-f499-4581-9e19-658043c27041`，子会话实际沙箱 `read-only`，实际可见工具 `read`/`glob`，证据全文覆盖与快照哈希校验均通过。夹具根在 `temp/status_review_verification/` 下，真实研究页未动。同一版本 `a7e5cc696d…` 已正式安装；当时的正式加载时间为2026-11-20T09:46:31.163Z。当前版本另看生产加载标记，不沿用该历史时间。

### 11.2 验收入口

三条都看到才算通过。只看「插件行是 active」不算验收。

1. `submit_topic_report` 只出现在目标项目主会话的工具表里，别的会话没有。
2. 未审 `STATUS.md` 链接在 assistant 文本里被换成提示句。`present` 参数夹带未审路径时，guard 拒绝。
3. 审阅通过后返回的快照链接可以显示。

夹具与期望输出见 README 的验收节。

### 11.3 回滚入口

| 想退到哪 | 怎么做 |
|---|---|
| 回滚安装（恢复安装前的配置） | 用 README 的安装记录回滚入口。配置并发变化时拒绝覆盖。 |
| 只停行为、保留插件行 | `config.enabled: false`，检查当前宿主是否实际卸载。若需重启，先问用户。 |

回滚后新建会话才干净。已经显示出去的文本不能撤回。

本节改前件：[archive/INSTALL_20261120_改前_加报告审阅插件入口.md](<archive/INSTALL_20261120_改前_加报告审阅插件入口.md>)。该件是改前的逐字节副本，只多一行 `SUPERSEDED-BY`。
