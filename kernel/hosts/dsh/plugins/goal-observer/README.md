<!--
GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
PURPOSE: 说明观察者 Host 插件的分工、行为边界、配置、测试与未验证项。
ORIGIN: 部署模块 dsh-goal-observer-v013/index.mjs（2026-11-13 读码提取）与 docs/dsh.md 的装载/验收口径
CUMULATIVE-DELTA: 首版。相对部署版明确四件事：通知默认关闭且完全参数化；不声称实现【代答】；
  失败继续不授权高风险动作；不绕过宿主 goal/driver invariant。
-->
# goal-observer —— 原生 goal 的观察者 Host 插件

> 一句话：带 `objectivePrefix` 的 goal 进入后续自动轮时，观察者模型只**决定这一轮做什么**；
> 插件把决定作为一条**独立来源**的消息追加进去。它不改 goal 轮正文，不暂停 goal，不替用户授权。

## 1. 它做什么 / 不做什么

| 做 | 不做 |
|---|---|
| 在 `agent/pre-step` 认出 `source.kind === 'goal'` 的自动轮 | 不创建常驻观察者子会话 |
| 直接调 `ctx.llm.stream` 生成 `{action, instruction, evidence}` | 不执行工具、不读网页 |
| `continue`：追加一条 `source.kind='goal-observer'` 的指令消息 | 不改 goal 轮消息（driver invariant 要求正文逐字一致） |
| `complete`：证据非空时调 `ctx.goals.complete(agent, 当前 ref)` | 不直接写 `goal/change`、不碰 session projection |
| `pause`：**拦下**，只写一条给人看的提示，本轮照常跑 | ⛔ 不调用 `ctx.goals.pause` / `ctx.goals.block` |
| 任何失败 / 超时 / 解析失败：fail-open，本轮没有指令照常跑 | 不把失败升级成暂停，不返回 `{kind:'reject'}` 去触发 driver block |
| 通知：只有显式开启才 spawn 一个参数化命令 | 默认不发；不内置 聊天 ID、话题、私有路径、会话身份 |

**关于【代答】**：本插件**不实现**【代答】。它没有工具、不创建子会话、不接 `user-questions/request`，
不能替用户回答等待中的提问。遇到需要真人决定、付款、注册、验证码、重启或授权不足的事，观察者只把
「需要人决定：<事项>」写进 `evidence`，继续推进不需要授权的那部分；是否答复仍由真人决定。

**失败继续不等于授权**：fail-open 只是「这一轮没有观察者指令」，不会放宽宿主审批、沙箱、工具限制，
也不会把观察者消息变成新的用户授权。观察者消息正文自带这句话，UI 也原样保留 goal 内容。

## 2. 接线契约（升级宿主后要复核）

- 原生 goal 状态与额度在 `@deepseek-ai/dsh-goal`；自动轮消息由 `@deepseek-ai/dsh-goal-round-driver` 生成。
- driver 的 invariant 要求 goal 轮正文与包内 prompt **完全一致** ⇒ 本插件只**追加**消息，绝不改原消息。
- `agent/pre-step` 是瀑布：本插件默认注册（不 `prepend`）。不管排在 driver 前还是后，都先 `await next()` 再决定。
- 只有在两种情况下返回 `{kind:'reject'}`：
  1) 读到确定的 goal id/revision/phase/activation 变化（用户暂停/改目标）；
  2) `complete` 已被宿主接受。
  其余情况（包括 `pause` 与所有失败）都 fail-open：没调用过 `next()` 就 `return next()`，已经调用过就
  原样返回已消费的 decision；避免 driver 用 `prompt-rejected` 把 goal block 掉。
- `inspect()` 是**三态**，读不到不等于已变化：
  - `active`：继续；
  - `changed`：确定 revision/phase/activation 变化，可 restore+reject（driver 不会 block）；
  - `unknown`：读 goal 失败、插件已关闭、signal 中断、agent 不再 live、round 已前进 —— 保持**已消费的 decision 原样返回**，
    ⛔ 不 restore+reject（否则外层 driver 会把同一 active revision 判 blocked），也⛔ 不在 catch 里 restore 后再 `next()`（同一批真人消息会投两次）。
- 卸载时：中止在途观察，并 `disarm` 带前缀的 active goal，避免留下无人接管的自动轮。
  加载时已有 agent、收到 `agent/created`、收到任何 `agent/inbox/inserted`（包括 goal 轮）都会纳入 tracking，
  所以「goal 轮只排队、还没进 `pre-step` 就卸载」也能 disarm。卸载时逐个 agent 读 goal；读不到只报
  「未宣称已 disarm」，不假称成功。

## 3. 配置

`cordis.patch.yml` 是**模板**，出厂 `enabled: false`。安装后必须显式改 `true` 才接管。

| 键 | 默认 | 范围 / 说明 |
|---|---|---|
| `enabled` | `false` | 必须显式 `true`；其他值都不注册监听器。 |
| `objectivePrefix` | `'[观察者]'` | 只接管 objective 以此开头的 goal。 |
| `provider` / `model` | 省略 | 必须同时给或同时省；省略则用会话当前 selection。 |
| `timeoutMs` | `60000` | 100..300000；两次观察调用共用。 |
| `maxTokens` | `1800` | 256..16000；thinking 与正文共用，偏小会 max-tokens。 |
| `contextChars` | `24000` | 2000..200000；历史输入上限。 |
| `policy` | `''` | 附加策略文本；不得覆盖用户授权与事实核查。 |
| `failureTolerance` | `1` | 旧键，**已失效**；保留只为兼容旧 yml。 |
| `notification.enabled` | `false` | 默认关闭。 |
| `notification.command` | `[]` | argv 前缀：`command[0]` 是可执行文件，其余是固定参数。 |
| `notification.cwd` | `null` | 可选工作目录。 |
| `notification.throttleMs` | `300000` | 0..86400000；最近 100 个 reason 内同 reason 只发一次，不同 reason 至少隔这么久。 |

## 4. 通知：默认关闭、完全参数化

开启后，插件做的是：

```yaml
notification:
  enabled: true
  command: ['C:/path/to/notifier.exe', '--source', 'goal-observer']
  cwd: null
  throttleMs: 300000
```

- 实参 = `command[1..]` + **一个** `reason` argv；`reason` 会压成单行（换行/制表→空格、连续空白合并、≤500 字）。
- `shell: false` · `detached: true` · `stdio: 'ignore'` · `unref()`；不 `await`、不读输出。
- 启动失败/子进程 `error` 只记 `warn`，绝不影响本轮 goal。
- 去重窗口是**最近 100 个 reason**（FIFO）：窗口内同 reason 不重发；被挤出窗口的旧 reason 可能再次发送。
- ⛔ argv 里不带 goalId、sessionId、聊天 ID、话题；要发到哪个群/话题由**部署侧脚本自己决定**。
- 例：`command: ['C:/path/to/node.exe', 'C:/path/to/notifier-adapter.mjs']`，
  适配器从最后一个 argv 取理由，再自行查配置决定目标。

**本仓库的测试不会真的发送**：自检注入假 `spawn`，只断言 argv/选项与节流。

## 5. 文件

| 文件 | 用途 |
|---|---|
| [index.mjs](<index.mjs>) | Host 插件接线：pre-step、inspect 三态判定、message/complete、通知、卸载。 |
| [observer-core.mjs](<observer-core.mjs>) | 纯函数：配置收口、候选 JSON 解析、历史/输入、通知 argv。 |
| [selftest-core.mjs](<selftest-core.mjs>) | 纯函数自检（不碰宿主）。 |
| [test-sim-host.mjs](<test-sim-host.mjs>) | 假宿主：cordis 事件/effect、goal CAS、driver 瀑布、llm chunk 协议。 |
| [selftest-plugin.mjs](<selftest-plugin.mjs>) | 接线自检：continue/complete/pause/失败/过时/取消/节流等。 |
| [cordis.patch.yml](<cordis.patch.yml>) | 补丁模板（出厂关闭）。 |
| [package.json](<package.json>) · [package.json.README.md](<package.json.README.md>) | 包元数据与字段说明。 |
| [PACKAGING.md](<PACKAGING.md>) · [pack.mjs](<pack.mjs>) | 可重建打包与清单校验。 |

运行环境：**Node >= 20.3**。`AbortSignal.any` 从 20.3 才加入；20.0/20.1/20.2 会直接不可用（不是 fallback）。

## 6. 自检（不需要宿主、不联网、不花 token）

```powershell
$node = (Get-Command node).Source
cd kernel\hosts\dsh\plugins\goal-observer
& $node selftest-core.mjs
& $node selftest-plugin.mjs
```

覆盖：正常 continue / complete（完成需证据）/ pause 拦截 / 失败继续 / 过时 revision / 用户暂停 / 用户改目标 /
用户停止 / 卸载 disarm / 不重复 `next` / 通知默认关闭与节流 / max-tokens 重问 / 同批次真人输入优先 /
已有观察者消息不重复 / 缓存 / 普通 goal 不介入 / 宿主读不到 goal 时 fail-open。

UI 插件的显示自检在 [`../goal-observer-ui/client.test.mjs`](<../goal-observer-ui/client.test.mjs>)。

## 7. 打包

见 [PACKAGING.md](<PACKAGING.md>)：`node pack.mjs` 生成 `dist/*.tgz` 与 `dist/manifest.json`；
`node pack.mjs --verify` 复核。⛔ 不提交 tgz，也不搬部署版的机器特定验收器。

## 8. 相对部署模块（0.1.3）的修正

读部署模块时发现并已用自检固化的问题：

| 问题 | 部署版行为 | 本版行为 |
|---|---|---|
| 解析候选提前放弃 | 第一个能解析成对象、但缺 `instruction` 的候选会立刻抛错，后面真正完整的候选不再尝试 | 失败记入 `lastError` 后继续尝试下一个候选 |
| 完成记录先于宿主落库 | 先 `session.append` 一条「观察者判定：complete」，再 `ctx.goals.complete`；宿主若因过时 revision/用户暂停拒绝，会留下看起来已完成的消息 | 先让 `ctx.goals.complete` 成功，再写完成记录；被拒绝时不写 |
| 通知节流是模块级、只记最后一条 reason | 多实例共享；A→B→A 会重发 A | 每个插件实例独立；最近 100 个 reason 内去重（FIFO），A→B→A 在 A 仍在窗口内时不会重发 |
| 通知带私有值 | 硬编码发送器路径、聊天 ID、话题、Python 解释器探测 | 完全参数化：`command` argv + 单一 `reason` argv；默认关闭；无私有值 |
| 后置读取失败被当成「已变化」 | 把读不到/关闭/abort 都折成 inactive，restore+reject；外层 driver 会把同一 active revision 判 blocked | `inspect()` 三态；`unknown` 保持已消费 decision 原样返回，只有确定 revision/phase/activation 变化才 reject |
| catch 里 restore 后再 next | 已认领的真人消息会再投递一次 | catch 不再 restore+next；读不到时返回已有 decision 或只 next 一次 |
| 卸载只跟踪非 goal 消息/进过 pre-step 的 agent | goal 轮只排队没进 pre-step 就卸载时 states 为空，前缀 active goal 留在 armed | 加载时用 `ctx.agents.list()`、`agent/created`、任何 `agent/inbox/inserted`（含 goal）都纳入 tracking；读 goal 失败明确报「未宣称已 disarm」 |
| complete 记录把 evidence 写两遍 | 指令里写一次、`依据：` 又写一次 | 指令只写「观察者判定：complete。」，evidence 只由 `依据：` 写一次 |
| 读取 goal 状态无兜底 | `ctx.goals.get` 抛错会冒泡成 pre-step 失败 | `inspect()`/初始 guard 包 try/catch，读不到就 fail-open |
| complete 缺二次证据检查 | 解析层保证，但后续改动可能放宽 | 调用 `complete` 前再检查一次非空 evidence |
| UI 缺原生渲染器直接 throw | 页面渲染崩掉 | 返回 `null` + 只 warn 一次 |
| UI 对畸形事件不设防 | `entries`/`data`/`content` 形状不对可能抛 | 全部形状容错 |

未搬入：部署版的飞书发送器、`live-probe*.mjs`、`integration-test-results.json` 等机器特定验收物。
需要安装包时用 `pack.mjs` 从源码重建（见 [PACKAGING.md](<PACKAGING.md>)）。

## 9. 未验证边界（⛔ 不要把自检当真实运行）

- **未在真实 DSH 宿主 + 真实模型上跑过**：本次交付环境把宿主与部署目录视为只读，且不联网。
- 假宿主只复现本插件碰到的契约（事件顺序、CAS、chunk 协议、driver 瀑布），**不覆盖**真实 agent-loop 的
  取消传播、cordis scope 卸载排序、session 持久化/回放竞态。
- 通知**没有实际发送**（按约束不发送）；只验证了 argv、节流与失败不影响本轮。
- `complete` 成功后真实 driver 的 idle/checkpoint 行为未真机验证；自检只覆盖到 pre-step 返回 reject。
- UI 只在 VM 里跑显示单测，**没有**在真实 Web GUI 里刷新验证 slot/CSS/交互。
- 宿主升级（`dsh-goal` / `dsh-goal-round-driver` / `agent/pre-step` 契约变化）后必须重跑自检并复核第 2 节。
