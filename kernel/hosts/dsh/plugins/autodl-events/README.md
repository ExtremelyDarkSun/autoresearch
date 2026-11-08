<!--
GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
PURPOSE: Host 插件 autodl-events 的通用装法、配置键、自检与回滚说明
ORIGIN: install/autodl-events/README.md（重写为通用说明；去掉本机路径、会话数量与实例事故）
CUMULATIVE-DELTA: 首版。⛔ 不含任何本机路径、实例机器名或历史读数；默认值改为 hold / 1 目标 / 无内置项目路径；
  验收分层（文件已改 / 宿主已加载 / 目标会话实际使用）分别写清，⛔ 不把「有文件」说成「已加载」。
-->

# autodl-events（Host 插件）

把后端判出来的「某台机器从开不了变成能开」事件，变成会话里的一条 user 消息。
**空闲会话也会开一个新回合**（`agent.steer`）——这是「立刻感知」的实现方式；`inject` 不叫醒，只塞进已有回合的下一步。

> 机制与协议正文见 [`docs/autodl.md`](../../../../../docs/autodl.md)（仓库内相对路径）。本文件只说**怎么装、怎么配、怎么验、怎么退**。

## 1. 它做什么 / 不做什么

| 做 | 不做 |
|---|---|
| 定时看一次 `eventDir`（默认 5 秒） | ⛔ 不联网、不查机器、不判事件（判据只有后端那一份实现） |
| 把事件排成一条消息 `steer` / `inject` 给**绑定的那个**会话 | ⛔ 不群发：`maxTargets` 默认 **1** |
| 投完把文件挪进 `delivered/`，写一行回执 | ⛔ 不重试风暴：挪不动的留在队列，下一轮再说 |
| 超过 `maxAgeMinutes` 的事件挪进 `expired/`、不投 | ⛔ 不造会话：没有能收的会话时事件留在队列里等 |

## 2. 装（推荐：用生成的加载配置，⛔ 不手改 profile）

本插件不是 bundle，按**生成的 patch 行**挂载。生成器在 `kernel/hosts/dsh/tools/gen-profile-patch.mjs`：

```powershell
node kernel\hosts\dsh\tools\gen-profile-patch.mjs --deployment <部署参数.json> --out <输出.yml>
# 人先复核输出文件，再按 docs/dsh.md §装载 走安装器的 install/apply 流程
```

生成出来的那一行大致长这样（值全部来自部署参数，⛔ 没有内置路径）：

```yaml
- insert:
    - id: autodl-events
      name: '<插件绝对路径或包名>/lib/index.js'
      config:
        eventDir: '<workspace>/.autoresearch/events/pending'
        deliveredDir: '<workspace>/.autoresearch/events/delivered'
        expiredDir: '<workspace>/.autoresearch/events/expired'
        logFile: '<workspace>/.autoresearch/events/delivered.jsonl'
        targetsFile: '<workspace>/.autoresearch/events/targets.json'
        projects: [ '<项目根（规范化路径前缀）>' ]
        delivery: steer
        maxTargets: 1
        fallback: hold
```

⚠️ 文件末尾**已经有一个 `insert:`** 时，再写一个新的 `- insert:` 列表项是合法的（同一数组的两个元素）；
⛔ 不要写成同一个 mapping 里的第二个 `insert:` 键。

## 3. 配置键

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | `false` = 不注册定时器，队列原样不动 |
| `eventDir` | 空（**必填**） | 待投递队列目录；空 = 插件什么都不做 |
| `deliveredDir` | `eventDir/../delivered` | 投递成功的落点 |
| `expiredDir` | `eventDir/../expired` | 太旧、被丢弃的落点 |
| `logFile` | 空 = 不记 | `apply` / `delivered` / `waiting` 三类记录各一行 JSON |
| `targetsFile` | 空 | 投给谁。后端会定期重写成 `{"sessions":[…],"authoritative":true,"source":"…"}`；也可人手写 `{"sessions":[…]} ` |
| `fallback` | **`hold`** | 没有可用的绑定会话时：`hold` = 一条都不发、留在队列等绑定出现；`pick` = 退回启发式挑 1 个 |
| `pollMs` | `5000` | 多久看一次队列（1 秒 ~ 10 分钟） |
| `maxAgeMinutes` | `45` | 超过这个分钟数的事件不投递 |
| `projects` | `[]`（不限） | **规范化路径前缀**匹配（不是子串）。写裸名字会把同名克隆的会话也算进来 |
| `delivery` | `steer` | `steer` = 空闲也叫醒；`inject` = 只塞进已有回合的下一个 step |
| `maxEventsPerMessage` | `3` | 一条消息最多带几条事件，超出的留到下一批 |
| `maxTargets` | `1` | 一次最多叫醒几个会话（上限 5） |
| `reviewHint` | 通用一句 | 消息末尾「怎么复核」。⛔ 插件不写死任何项目命令 |

**投给谁（顺序）**：
1. **绑定的会话**（`targetsFile` 里的 id，按名单顺序，`maxTargets` 封顶）；
2. 没有绑定、或绑的那个不在场 ⇒ `fallback`：
   - `hold`（默认）：不投，事件留在队列里等它回来；
   - `pick`：① `status === 'running'` 的会话 → ② 其中 `session.seq` 最大的那个。
⛔ 不按「创建时间最新」挑 —— 那会挑到刚开的白纸会话。

## 4. 自检（不需要宿主、不联网、不花 token）

```powershell
$node = (Get-Command node).Source   # 用部署机现有的 Node，⛔ 不复制别人的安装布局
& $node <本目录>\selftest-core.mjs     # 纯函数层
& $node <本目录>\selftest-plugin.mjs   # 接线层（假 ctx 驱动 apply）
& $node <本目录>\dryrun.mjs --event-dir <.../events/pending>   # 只读预览
```

## 5. 验收分三层（⛔ 不要互相替代）

| 层 | 证据 | 说明 |
|---|---|---|
| ① 配置已写 | 生成的 patch 文件 + 人工复核记录 | 只证明「准备装什么」 |
| ② 宿主已加载 | 回执里多一行 `kind:"apply"`（带 `pid`） | 证明插件 `apply()` 真的跑过 |
| ③ 目标会话实际使用 | 会话里出现以 `[机器状态事件]` 开头的 user 消息 + 回执 `kind:"delivered"` | **唯一**能证明「真的叫醒了我」 |

⚠️ **热加载不可依赖**：同一份配置改写后，有时立刻生效、有时很久不动；ESM 模块还按 URL 缓存
（所以本包留了 `lib/index.js` / `lib/core.mjs` 两个转接层，换实现文件名可绕开缓存）。
判据 = `apply` 行的 `pid` 变了、且字段与你写的一致。**改动以后以真正重启宿主为准。**
⚠️ **旧会话不保证切换**：配置监听器会在宿主就绪后协调插件树，但**已经跑起来的会话保留旧预设代际**。
⇒ 新装/改配置后要**新开一个会话**验证；⛔ 不要把 HMR 当成「旧会话已切换」的证据。

## 6. 回滚

| 想退到哪 | 怎么做 |
|---|---|
| 只停投递、保留事件落盘 | 生成配置里该行加 `enabled: false`，重启宿主 |
| 完全卸载插件 | 删掉 profile patch 里 `id: autodl-events` 那一段，重启宿主 |
| 连事件判定也停 | 停掉后端的定时任务入口（见 `docs/autodl.md` §定时任务），⛔ 别只删插件（那样事件会一直堆在队列里） |

## 7. 已知限制

1. **宿主不在就没有投递**：事件照旧落盘，下次宿主起来补投；超过 `maxAgeMinutes` 的进 `expired/`。
2. **不做跨重启去重**：正常不会重发（投完就挪走），但宿主在「已投、还没挪走」之间崩掉时可能重发一次。
3. **一次投递 = 一个会话开一个新回合**：所以默认 `maxTargets=1` + 同批合成一条 + 45 分钟过期，三条都是护栏。
4. **宿主升级后要复核**：`agent.steer()` / `agent.inject()` / `agents.roots()` / `session.seq` / `ctx.effect()`
   任何一处变了，插件会静默失效（异常被吞，只记 warn 与 `waiting` 行）。
