<!--
GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
PURPOSE: 说明观察者指令的纯客户端显示插件：按 goalId/revision/round 匹配、只加显示副本、不改消息原件。
ORIGIN: install/dsh-goal-observer-ui/client.js 与 client.test.mjs（2026-11-13 读码提取）
CUMULATIVE-DELTA: 首版。相对原实现：缺原生渲染器时降级为 null + 只 warn 一次，不再 throw 把页面打崩；
  快照/条目/内容全做形状容错；匹配键归一化；其余显示口径（continue/complete/pause/error）保持。
-->
# goal-observer-ui —— 观察者指令的显示插件

> 一句话：把**已经存在**的 `goal-observer` / `goal-observer-error` 会话记录，拼到原生「继续执行目标」通知的展开体开头。
> 纯显示副本：不写回会话、不调用模型、不改 goal 状态、不改消息原件。

## 1. 它怎么匹配

只认 `user/message` 事件里 `source.kind` 为 `goal-observer` 或 `goal-observer-error` 的记录，按三元组匹配：

| 键 | 来源 |
|---|---|
| `goalId` | `message.source.goalId` |
| `revision` | `message.source.revision` |
| `round` | `message.source.round` |

显示时取 `data.source.kind === 'goal'` 的原生 goal 通知节点，三元组**全部相等**才插入观察者段落；
任一不等、没有记录、或节点不是 goal 通知，都原样返回同一个 node。

## 2. 不改原件

`presentationNode` 只创建新的显示对象：

- `node.data` / `node.data.source` 沿用原引用；
- 原有 `content` 块**按引用**接在观察者段落之后；
- 不调用 `session.append`、不重新排序、不入库、不发网络请求。

自检会深冻结原 node 并比对 JSON 字节，确保没有隐式改写。

## 3. 显示内容

| 记录 | 显示 |
|---|---|
| `goal-observer` + `decision.action='continue'` | `决定：继续` + 下一步 + 判断依据 |
| `goal-observer` + `decision.action='complete'` | `决定：完成` + 判断依据 |
| `goal-observer` + `decision.action='pause'` | `决定：暂停`（host 已拦下，仅显示历史记录） |
| `goal-observer-error`（无 decision） | 直接显示记录里的 text；空则给占位句 |
| 同 key 多条 | 取最新一条（事件顺序里的后者），仍只加一个段落 |

结尾固定写「此内容由观察者生成，不代表新的用户授权。」并保留「原生目标续跑提示」分隔。
**它不实现【代答】**：显示一条观察者提示，不等于替用户回答任何等待中的提问。

## 4. 接线

- 在 `conversation.chat.node` 槽位注册 `key: 'turn-trigger'` · `priority: -100` · `locale: 'chat'`。
- 通过 `ctx.sessions.binding(sessionId).eventSource` 取已有事件快照；`goalObserverEvents` hook 只读。
- 延迟注册安全：渲染时才查原 `turn-trigger` 组件；找不到时返回 `null` 并**只 warn 一次**，不把页面打崩。
- 缺 `eventSource` 时 `getSnapshot()` 返回稳定的 `{ entries: [] }`，`subscribe()` 返回空退订函数。

### 4.1 真正的开关是 Loader 的 `disabled`

DSH 的 `client-modules` 只把 **`entry.fiber !== undefined && !entry.disabled`** 的行组进页面
（见 `@deepseek-ai/dsh-client-modules` 的 `processOne`）。因此：

- `config.enabled: false` **拦不住浏览器 bundle**：行还活着，`client.js` 照样会被注入；
  宿主侧 `index.js` 只是空 `apply()`，也没有能力去关浏览器里的注册。
- 要真正关闭显示，必须让**这一行不加载**：`disabled: true`，或干脆不插这一行。
- 出厂模板 [cordis.patch.yml](<cordis.patch.yml>) 就是 `disabled: true`；启用时改成 `false`。换机准备工具另生成显式 `enabled:true` 的待审核部署输入，生成器据此输出 `disabled:false`；这些工具不修改当前宿主。

生成器/安装器必须按这个语义写：`enabled === true` ⇒ 不加 `disabled`（或 `disabled: false`）；
`enabled !== true` ⇒ `disabled: true`（或省略整行）。只写 `config.enabled: false` 不算关闭。

## 5. 文件

| 文件 | 用途 |
|---|---|
| [index.js](<index.js>) | 宿主侧空入口（`export const name` / `export function apply() {}`）。 |
| [client.js](<client.js>) | 浏览器端显示插件（`window.__ModuleLoader__.load`）。 |
| [client.test.mjs](<client.test.mjs>) | VM 离线回归：匹配、显示、原件不变、形状容错、降级、hook。 |
| [cordis.patch.yml](<cordis.patch.yml>) | 只插入 `goal-observer-ui` 一行。 |
| [package.json](<package.json>) · [package.json.README.md](<package.json.README.md>) | 包元数据与字段说明。 |
| [PACKAGING.md](<PACKAGING.md>) · [pack.mjs](<pack.mjs>) | 可重建打包与清单校验。 |

## 6. 自检

```powershell
$node = (Get-Command node).Source
cd kernel\hosts\dsh\plugins\goal-observer-ui
& $node client.test.mjs
```

覆盖：未生成不伪造 / 三元组隔离 / continue·complete·pause·error / 原消息·来源·块引用不变 /
畸形条目与缺字段不抛 / 注册与 hook / 缺原生渲染器降级。

## 7. 打包

见 [PACKAGING.md](<PACKAGING.md>)：`node pack.mjs` 生成 `dist/*.tgz` 与 `dist/manifest.json`；`node pack.mjs --verify` 复核。

## 8. 未验证边界

- **没有在真实 Web GUI 里刷新验证**：本环境把宿主与部署目录视为只读，只跑了 VM 单测。
- 没验证真实 `conversation.chat.node` 插槽的 CSS/折叠交互；只验证了传入 props/node 的形态与引用不变。
- 没验证浏览器端 `window.__ModuleLoader__` 的版本差异；只按现有出货包的加载协议复现。
- 宿主 UI 包升级（`dsh-client-ui-chat` 槽位签名变化）后必须重跑本目录自检。
