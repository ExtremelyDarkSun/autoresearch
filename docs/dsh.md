<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: DSH 宿主侧三套插件 + 加载配置生成器 + 定时任务脚本的说明：版本能力契约、装载、三层验收、回滚、缺口
ORIGIN: install/agent-instruction-filter/* · install/dsh-doc-gap/* · install/autodl-events/* · INSTALL.md 的装法与验收口径
        （重写为通用说明；实例路径、会话数量、机器名与历史读数全部剥离）
CUMULATIVE-DELTA: 首版。⛔ 不含任何本机绝对路径、项目名、会话 ID 与历史结论；
  明确「文件在磁盘上 ≠ 已加载」「HMR 不保证旧会话切换」两条验收纪律。三套插件的**出厂默认**是「不启用 / 不注册 / hold」；
生成器会**照抄**部署参数里的值 —— 抄成 `enabled: true` 也只表示「配置这么写」，⛔ 不等于已加载、更不等于目标会话已用到。
-->

# DSH 宿主侧：三套插件与加载工具

> 本文件说明**这一套东西怎么装、怎么验、怎么退**，以及它依赖 DSH 的哪些能力。
> ⛔ **本次交付没有在运行宿主上安装、启用或重启任何插件。** 所有插件都在 `kernel/hosts/dsh/plugins/` 里，
> 是「随内核交付的源码 + 配置模板 + 自检 + 量具」，**不是「已加载」**。

---

## 1. 三套插件（全部随内核交付，启用与否由部署配置决定）

| 插件 | 做什么 | 出厂默认 | 关键配置 |
|---|---|---|---|
| `agent-instruction-filter` | 对**子 agent** 的指令注入按**显式策略**裁剪（默认不裁） | `mode: off`（不注册监听器）+ 策略 `rules: []`（全 keep） | `mode` / `childOnly` / `policyPath` / `onError` |
| `dsh-doc-gap` | 回合结束时判一次「这个提问指向的点，判据文档说了没有」，没说清就提示 | `enabled: false`（不注册 ⇒ 无模型调用） | `docs` / `maxDocChars` / `policy`（范围+判据+锚点）/ `judge` |
| `autodl-events` | 轮询事件队列，把「机器从开不了变成能开」的事件投给**绑定的那个**会话 | `fallback: hold` + `maxTargets: 1` + 无内置项目路径 | `eventDir` / `targetsFile` / `logFile` / `delivery` / `projects` |

**默认都不联网、不查云机**：指令过滤与事件投递不发起任何网络请求；`dsh-doc-gap` 在**显式启用**后才可能调模型 ——
`session` 后端复用本回合主模型（不新增端点），`openai` / `jev` 后端会向**部署方配置的 `baseURL`** 发 HTTP 请求。
⇒ 启用 doc-gap 前先确认端点是你要的、预算也是你要的；未启用时它连监听器都不注册。

### 1.1 「默认不漏应有规则」怎么保证

`agent-instruction-filter` 有两条**互相独立**的护栏：

1. 插件 `config.mode` 出厂是 `off` ⇒ 连监听器都不注册；
2. 就算被打开，出厂 `policy.mjs` 的 `rules` 是空数组 ⇒ **规则一条都不删**（格式仍会归一，见下）。

并且：**策略文件读不到 / JSON 坏 / 结构不合法时，一律回落到中性 keep 策略**，⛔ 绝不回落到任何「旧 drop 策略」。
这条有专门的离线反向对照（`selftest-plugin.mjs`：三种读坏方式都必须保持原样）。

⚠️ **keep 也不是逐字节复制**：注入文本会先做 CRLF→LF 归一，段的尾部空白会被裁掉（避免空行越积越多）。
这只是格式归一，**不减任何规则**；量具里那 1–2 B 的差就是它。

`drop` / `strip` / `onError: drop` 这三种裁剪能力**都还在**，但只有显式配置才会启用。
要写裁剪策略时，用 `docs/policy-classification.template.md` 的空表判一遍，再写外置策略 JSON。

### 1.2 doc-gap 的范围与材料

- **判定范围**（哪些话题算「可能与文档有关」）、**排除清单**、**三类判据措辞**、**结尾锚点**全部在 `config.policy` 里，
  插件内置的默认值是**通用中性**的（不含任何项目专用六类）。
- **项目范围**按**规范化路径前缀**隔离（相等或位于其下）；⛔ 不是子串 —— 否则同名克隆的会话会互相命中。
- **文档超上限必须被诊断出来**：`loadDocs()` 逐份返回 `truncated / totalChars / limit / overCap`；
  插件在超限时**无条件**往 stderr 写一条诊断（不受 `logLevel` 影响）；`measure.mjs` 与 `verify-real-docs.mjs` 分别按 1 和「不符」退出。
- **截断硬闸（机械执行）**：只要有一份文档超上限，判定就被降级 —— `missing → partial`、`p_missing` 封顶 `0.6`。
  ⇒ 默认阈值 `0.8` 下，材料不完整时**永远不可能**触发提示。要放宽阈值是部署方的显式选择，并在回执/日志里留痕。
- **模型调用只在显式启用后发生**：`enabled` 缺省 `false`，未启用时插件不注册监听器。

### 1.3 autodl-events 的默认口径

- **默认绑定目标**：投给 `targetsFile` 里的会话（后端生成）。
- **没有可用绑定 ⇒ `fallback: hold`**：一条都不发，事件留在队列里等（⛔ 不退回乱投）。
- **一次最多 1 个目标**（`maxTargets: 1`，上限 5）。
- 事件正文里的「怎么复核」由 `config.reviewHint` 给，⛔ 插件不写死任何项目命令。
- `steer` 与 `inject` 两种投递能力都保留：`steer` 会唤醒空闲会话，`inject` 只塞进已有回合的下一步。

---

## 2. 依赖的 DSH 能力（版本契约）

> 证据基线：本轮审计读取到的桌面版 / CLI / base 均为 **0.2.0-rc.2**（【读码 + 安装包版本】）。
> 下面每条都是**宿主契约**；升级 DSH 后要逐条复核，任何一条变了插件会**静默失效**。

| 能力 | 用在哪 | 复核方法 |
|---|---|---|
| `agent/pre-step` 瀑布 + 监听器 `{prepend: true}` | 指令过滤要在宿主注入**之后**看到 `decision.messages` | 派一个只回答、不读文件的子 agent，看它收到的注入块数 |
| 注入消息标记 `source.kind === 'agent-instructions'` | 过滤只改这类消息 | 同上 |
| `ctx.get('agents').roots()` | 判「子级 / 根级」、判可投递会话 | 过滤与事件插件都依赖 |
| 会话头 `session.header.parentSession` | `roots()` 不可用时的子级回退判据 | 同上 |
| `agent.steer(message)` / `agent.inject(message)` | 事件投递（也叫醒） | 回执里 `kind:"delivered"` |
| `agent/turn-stopping` + `agent/steer` | doc-gap 在回合末尾提示 | 需要真机验证（见 §5） |
| `ctx.llm.stream` / `llm` 服务 | doc-gap 的 `session` 后端 | 同上 |
| `ctx.effect(disposer)` | 事件插件的定时器随插件卸载停止 | 卸载后不再投递 |
| `session.seq` / `agent.status` | 事件插件挑目标时的排序键 | 回执 `pickSummary` |
| 插件 `config` 按**键替换**（不保证递归深合并） | 三套插件的 config 都要写全或依赖插件内部默认 | 读宿主版本说明；本内核的插件内部都有默认合并 |

### 2.1 关于「热加载」与「旧会话」

- 配置文件监听器会在宿主就绪后重新协调插件树，但**这不代表已经跑起来的会话会切换预设版本**：
  旧会话保留旧代际，子会话沿用父代际。⇒ **改动以后要新开一个会话验证**，⛔ 不要把 HMR 当成「旧会话已切换」的证据。
- 事件插件还有一层：ESM 模块按 URL 缓存。改**代码内容**后如果不重启宿主，看到的可能还是旧实现；
  ⇒ 本包保留了 `lib/index.js` / `lib/core.mjs` 两个转接层，换实现文件名可绕开缓存。
- 结论：**以真正重启宿主为准**。判断在跑哪一版：看回执 `kind:"apply"` 的 `pid` 与你写进 config 的字段。

### 2.2 指令预算

宿主对「每会话自动加载的指令」有总预算（当前版本 65,536 B，单源上限另计）。超了会**静默略过整份文件**。
⇒ 安装后必须核对**实际注入没有省略或截断**（见 §5 的行为验证），⛔ 不能只统计文件字节。
本内核**不承诺**某个具体预算值；以宿主实际报告为准。

---

## 3. 装载（人复核 + 安装器，⛔ 不手改 profile）

### 3.1 生成加载配置

```powershell
$node = (Get-Command node).Source   # 用部署机现有的 Node，⛔ 不复制别人的安装布局
& $node kernel\hosts\dsh\tools\gen-profile-patch.mjs --print-schema
& $node kernel\hosts\dsh\tools\gen-profile-patch.mjs `
    --deployment kernel\hosts\dsh\config\deployment.example.json `
    --out .\out\patch.generated.yml
```

- 生成器**只写你指定的文件**；⛔ 不写 profile、不调安装器、不重启宿主。
- 如果 `--out` 看着像 profile 路径（含 `.dsh` 或 `profiles…`），会**拒绝**；确要写那里得加 `--allow-profile-path`。
- **生成器不改部署参数**：你在 deployment 里写 `enabled: true`，它就抄 `true`；写 `false` 就抄 `false`。
  示例文件是**全部关闭**的形态，要启用哪套就改那一套自己的 `enabled` / `mode`，并按 §5 取三层证据。
- ⚠️ **`name` 的两种形态**：不给 `pluginPath` 时生成器写**包名**（如 `@autoresearch/dsh-*`）——
  这要求该包在宿主的解析路径里（通常由安装器装过）；只把源码放在仓库里时包名**解析不到**，要显式给 `pluginPath`（绝对路径）。
- 填 `docs`（判据文档）时，`maxDocChars` 必须大于每份文档的字符数，否则整份判「已截断」⇒ 插件会写超限诊断。

### 3.2 交给安装器

| 插件形态 | 怎么装 | 说明 |
|---|---|---|
| `agent-instruction-filter` / `dsh-doc-gap`（bundle，有 `dsh.bundle.patch`） | 安装器的 `install_bundle`，`target` = 插件目录 | 由安装器改 profile；⛔ 不手改 `package.json` / `cordis.patch.yml` |
| `autodl-events`（非 bundle） | 把生成器输出的 `- insert:` 段并入 patch（安装器的 apply/merge 入口） | 按绝对路径或包名挂载都行 |

⚠️ 安装动作本身需要授权/审批（各宿主权限模型不同，本内核不假设）。⚠️ 一次只加一段，别顺手整份重述预设
（`config` 是整键替换，重述容易把出货行整份换掉）。

### 3.3 重启与新建会话

1. 重启宿主（不是只重启窗口；判据 = 宿主进程启动时间变了）；
2. **新开一个会话**（旧会话不保证切换，见 §2.1）；
3. 按 §5 三层验收取证据。

---

## 4. 定时任务（事件生产）

见 [`docs/autodl.md`](<autodl.md>) §定时任务。要点：脚本默认**只预览/只生成**，注册必须显式 `-ConfirmRegister`；
任务与宿主无关（宿主关着事件照旧落盘），但**没有任何会话在跑时不会有投递**。

---

## 5. 验收：三层，⛔ 不能互相替代

```powershell
$node = (Get-Command node).Source   # 用部署机现有的 Node，⛔ 不复制别人的安装布局
& $node kernel\hosts\dsh\tools\check-loading.mjs `
    --patch .\out\patch.generated.yml `
    --receipt <workspace>\.autoresearch\events\delivered.jsonl
```

| 层 | 证据 | 说明 | 失败时通常是什么 |
|---|---|---|---|
| ① 配置已写 | 生成的 patch 文件 + 人工复核 | 只证明「准备装什么」 | 生成器输入不合法 / 路径不对 |
| ② 宿主已加载 | 回执里 `kind:"apply"`（带 `pid`） | 证明插件 `apply()` 跑过 | 没重启宿主 / patch 没并入 / 路径写错 |
| ③ 目标会话实际使用 | 回执里 `kind:"delivered"`，或会话里出现 `[机器状态事件]` 开头的消息 | **唯一**能证明「真的到了」 | 没有绑定会话（`hold` 生效）/ 没有可收会话 / 事件过期 |

`check-loading.mjs` 退出码：`0` 三层齐 · `1` 缺层 · `2` 未执行（patch 不存在）。

### 5.1 各插件自己的离线自检（不需要宿主、不联网、不花 token）

```powershell
& $node kernel\hosts\dsh\plugins\agent-instruction-filter\selftest.mjs
& $node kernel\hosts\dsh\plugins\agent-instruction-filter\selftest-plugin.mjs
& $node kernel\hosts\dsh\plugins\dsh-doc-gap\selftest.mjs
& $node kernel\hosts\dsh\plugins\dsh-doc-gap\selftest-plugin.mjs
& $node kernel\hosts\dsh\plugins\autodl-events\selftest-core.mjs
& $node kernel\hosts\dsh\plugins\autodl-events\selftest-plugin.mjs
```

期望：每条都 `PASS: …` 且 exit 0。这些只证明**代码与接线**对，⛔ 不证明宿主已加载。

### 5.2 行为验证（过滤插件，真机才做）

派一个**只回答、不读文件**的子 agent，让它报告收到的注入块数量与首行。三种配置各测一次：

| 配置 | 期望 |
|---|---|
| `mode: off`（出厂） | 子 agent 收到的块数 = 宿主正常会注入的数量（不受本插件影响） |
| `mode: filter` + 空策略 | 与 `off` **相同**（默认不漏规则） |
| `mode: filter` + 显式 drop 策略 | 只有被点名的段消失；其它段逐字保留 |

⚠️ 主 agent 不受影响这一条也要单独确认。

### 5.3 量具（只读）

| 工具 | 用途 | 退出码 |
|---|---|---|
| `agent-instruction-filter/measure.mjs --config <清单.json>` | 逐文件 before/after 字节 | 0 量完 · 2 未执行 |
| `agent-instruction-filter/verify-real-policy.mjs --config <清单.json> [--policy <策略>]` | 在真实文件上按内容核策略 | 0 通过 · 1 不符 · 2 未执行 |
| `dsh-doc-gap/measure.mjs --config <清单.json>` | 单回合上送字符、离截断余量 | 0 正常 · 1 有超限 · 2 有缺失/未执行 |
| `dsh-doc-gap/verify-real-docs.mjs --config <清单.json>` | 文档存在/未超限/政策配齐 | 0 通过 · 1 不符 · 2 未执行 |
| `autodl-events/dryrun.mjs --event-dir <...>` | 队列现在会排成哪条消息 | 0 预览 · 2 未执行 |

⚠️ 「未执行」（2）不等于通过。⛔ 不要把 2 读成绿。

---

## 6. 回滚

| 想退到哪 | 怎么做 |
|---|---|
| 只停某个插件 | 生成配置里那行 `enabled: false`（过滤插件用 `mode: off`），重启宿主 |
| 完全卸载 | 安装器卸载 bundle / 删掉 patch 里对应 `- insert:` 段，重启宿主 |
| 连事件生产也停 | 停掉定时任务（`schtasks.ps1 -Action uninstall -ConfirmUnregister`），否则事件会继续落盘 |

---

## 7. 缺口与不承诺（⛔ 读之前别当成已验证）

1. **本次没有在运行宿主上安装/加载/重启任何插件**：三套插件的「真机加载」与「目标会话实际使用」都**未验**。
2. **过滤插件的真实接线未验**：离线假 ctx 只复现「谁在什么时候被调用」，不复现宿主的事件分发与 scope。
3. **doc-gap 的真实回合未验**：它需要一次真实模型调用；本次只验了纯函数、接线与超限诊断。
4. **事件插件的 `apply` 回执是「已加载」的间接证据**：它证明 `apply()` 跑过，⛔ 不证明目标会话收到。
   第三层必须有 `delivered` 回执或会话里的消息。
5. **热加载不可依赖**：同一份配置改写后表现可能不一致；一律以真正重启宿主为准。
6. **宿主升级会静默失效**：§2 表里任何一条契约变了，插件异常被吞，只留 warn 与 `waiting` 回执。
7. **指令预算**只核对了「机制」，没有核对某个具体部署里每一份文档是否真的完整注入 —— 那要在真机会话里数注入块。
8. **`plugin_manager` 不一定在普通会话的工具表里**：没有它时按 §3.2 的 patch 方式装，并接受「配置整键替换」的风险。

---

## 8. 目录

| 路径 | 内容 |
|---|---|
| `kernel/hosts/dsh/plugins/agent-instruction-filter/` | 过滤插件（实现 · 策略模板 · 两个自检 · 量具 · 真实文件关卡 · 两个 docs） |
| `kernel/hosts/dsh/plugins/dsh-doc-gap/` | 文档遗漏插件（实现 · 政策模板 · 两个自检 · 两个量具） |
| `kernel/hosts/dsh/plugins/autodl-events/` | 事件投递插件（实现 · 纯函数/接线自检 · 只读预览 · README） |
| `kernel/hosts/dsh/tools/gen-profile-patch.mjs` | 参数化加载配置生成器（只生成，不写 profile） |
| `kernel/hosts/dsh/tools/schtasks.ps1` | 定时任务生成/预览/安装/卸载/查询（默认只预览与生成） |
| `kernel/hosts/dsh/tools/check-loading.mjs` | 三层验收助手 |
| `kernel/hosts/dsh/config/deployment.example.json` | 部署参数示例（占位符，无本机值） |
