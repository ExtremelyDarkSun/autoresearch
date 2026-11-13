// GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
// LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
// PURPOSE: goal-observer-ui 的离线回归 —— 按 goalId/revision/round 匹配、只生成显示副本、原消息与来源不变、
//          continue/complete/pause/error 四类记录、延迟注册、快照订阅、缺原生渲染器时的降级。
// ORIGIN: install/dsh-goal-observer-ui/client.test.mjs（2026-11-13 读码后重写；补形状容错与降级用例）
// CUMULATIVE-DELTA: 首版。原测试的七项行为全部保留；新增：节点/条目畸形不抛、缺原生渲染器返回 null 且只 warn 一次、
//          无 eventSource 时 getSnapshot 返回稳定 EMPTY、直接事件（无 {event} 包装）也能识别。

import vm from 'node:vm'
import fs from 'node:fs'
import assert from 'node:assert/strict'

let mod
let registration
let Component
let registered = true
let binding = () => ({ eventSource: source })
let snapshot = { entries: [] }
const warnings = []

const original = {
  component: props => props,
  options: { key: 'turn-trigger' },
  locale: 'chat',
}

const react = {
  createElement: (fn, props) => fn(props),
  useMemo: fn => fn(),
}

const source = { getSnapshot: () => snapshot, subscribe: listener => () => {} }

const context = {
  window: { __ModuleLoader__: { load: definition => { mod = definition.factory(() => react) } } },
  console: { warn: (...args) => warnings.push(args.join(' ')) },
}
vm.runInNewContext(fs.readFileSync(new URL('./client.js', import.meta.url), 'utf8'), context)

mod.apply({
  slots: {
    inject: (name, fn) => {
      assert.equal(name, 'conversation.chat.node')
      return fn()
    },
    entries: () => (registered ? [original] : []),
    register: (options, component) => {
      registration = options
      Component = component
      return () => {}
    },
  },
  sessions: { binding: sessionId => binding(sessionId) },
})

const src = { kind: 'goal', goalId: 'goal-1', revision: 2, round: 3 }
const goalNode = deepFreeze({
  data: {
    source: src,
    content: [{ type: 'text', text: '<goal_round>原生正文</goal_round>' }],
  },
})
const originalBytes = JSON.stringify(goalNode)

function deepFreeze(value) {
  if (value && typeof value === 'object') {
    Object.freeze(value)
    for (const child of Object.values(value)) deepFreeze(child)
  }
  return value
}

function record({ sourcePatch = {}, decision, content, kind = 'goal-observer', seq = 10 } = {}) {
  const data = {
    source: { ...src, kind, ...sourcePatch },
    content: content ?? [],
  }
  if (decision !== undefined) data.source.decision = decision
  return { event: { type: 'user/message', seq, data } }
}

function render({ node = goalNode, entries = snapshot.entries, hook = true } = {}) {
  const props = { node, t: key => key }
  if (hook) props.useGoalObserverEvents = selector => selector({ entries })
  return Component(props)
}

// ── 1. 未生成不伪造 ────────────────────────────────────────────────────
assert.equal(render({ entries: [] }).node, goalNode, '没有观察记录时必须是同一个 node')

// ── 2. 三元组隔离 ──────────────────────────────────────────────────────
snapshot = {
  entries: [
    record({ sourcePatch: { round: 4 }, decision: { action: 'continue', instruction: '轮次不对', evidence: '' } }),
    record({ sourcePatch: { revision: 1 }, decision: { action: 'continue', instruction: '版本不对', evidence: '' } }),
    record({ sourcePatch: { goalId: 'goal-2' }, decision: { action: 'continue', instruction: '目标不对', evidence: '' } }),
  ],
}
assert.equal(render().node, goalNode, 'goalId/revision/round 任一不匹配都不显示')
assert.equal(JSON.stringify(goalNode), originalBytes)

// ── 3. 正常 continue：只加显示副本，原内容块引用不变 ──────────────────
snapshot = {
  entries: [
    record({
      decision: { action: 'continue', instruction: '核实 runner 存活', evidence: '结果尚未齐全' },
      content: [{ type: 'text', text: '观察者原始 content（正常路径不用它）' }],
    }),
  ],
}
let result = render()
assert.notEqual(result.node, goalNode)
assert.equal(result.node.data.content.length, 2)
assert.equal(result.node.data.content[1], goalNode.data.content[0], '原消息块必须是同一个对象')
assert.equal(result.node.data.source, src, '来源必须是同一个对象')
assert.match(result.node.data.content[0].text, /核实 runner 存活/)
assert.match(result.node.data.content[0].text, /判断依据：\n结果尚未齐全/)
assert.match(result.node.data.content[0].text, /不代表新的用户授权/)
assert.equal(JSON.stringify(goalNode), originalBytes, '不得改动原消息（深冻结也不抛）')
assert.equal(result.t('test'), 'test', '其余 props 原样传给原渲染器')

// ── 4. complete / pause / error ────────────────────────────────────────
snapshot = { entries: [record({ decision: { action: 'complete', instruction: '', evidence: '三轮输出齐全' } })] }
assert.match(render().node.data.content[0].text, /决定：完成/)
assert.match(render().node.data.content[0].text, /判断依据：\n三轮输出齐全/)

snapshot = { entries: [record({ decision: { action: 'pause', instruction: '', evidence: '需要人付款' } })] }
assert.match(render().node.data.content[0].text, /决定：暂停/)

snapshot = {
  entries: [
    record({
      kind: 'goal-observer-error',
      content: [{ type: 'text', text: '观察者调用失败：timeout（本轮照常执行）' }],
    }),
  ],
}
const errorText = render().node.data.content[0].text
assert.match(errorText, /来源：goal-observer-error/)
assert.match(errorText, /timeout/)

// 没有 decision 也没有 text 块：给一句占位，不抛
snapshot = { entries: [record({ kind: 'goal-observer-error', content: [] })] }
assert.match(render().node.data.content[0].text, /没有留下文字说明/)

// ── 5. 其他来源的 node 原样返回 ────────────────────────────────────────
const other = deepFreeze({ data: { source: { kind: 'tool-jobs' }, content: [] } })
assert.equal(render({ node: other }).node, other)

// 缺 content / 缺 node / 畸形条目都不抛
assert.equal(render({ node: { data: { source: { ...src }, content: undefined } }, entries: snapshot.entries }).node.data.content.length, 1)
snapshot = { entries: [null, { event: { type: 'user/message', data: null } }, { event: { type: 'user/message', data: { source: { kind: 'goal-observer' } } } }] }
assert.equal(render({ entries: snapshot.entries }).node, goalNode)
assert.equal(render({ entries: undefined }).node, goalNode)
assert.equal(render({ hook: false }).node, goalNode, '没有 hook 时按空快照处理，不抛')

// 直接事件（没有 {event} 包装）也识别
snapshot = {
  entries: [
    { type: 'user/message', seq: 11, data: { source: { ...src, kind: 'goal-observer', decision: { action: 'continue', instruction: '直接事件', evidence: '' } }, content: [] } },
  ],
}
assert.match(render().node.data.content[0].text, /直接事件/)

// ── 6. 同 key 多条：取最新一条（Map 覆盖），不重复拼接 ────────────────
snapshot = {
  entries: [
    record({ seq: 1, decision: { action: 'continue', instruction: '旧指令', evidence: '' } }),
    record({ seq: 2, decision: { action: 'continue', instruction: '新指令', evidence: '' } }),
  ],
}
const latest = render().node.data.content[0].text
assert.match(latest, /新指令/)
assert.doesNotMatch(latest, /旧指令/)
assert.equal(render().node.data.content.length, 2, '仍然只加一个段落')

// ── 7. 注册与 hook 接线 ────────────────────────────────────────────────
assert.equal(registration.name, 'conversation.chat.node')
assert.equal(registration.key, 'turn-trigger')
assert.equal(registration.priority, -100)
assert.equal(registration.locale, original.locale)

const hooks = registration.inject('session-1').hooks.goalObserverEvents
assert.equal(hooks.getSnapshot(), snapshot)
let subscribed = false
const unsubscribe = hooks.subscribe(() => { subscribed = true })
assert.equal(typeof unsubscribe, 'function')
unsubscribe()

binding = () => ({})
const emptyHooks = registration.inject('session-2').hooks.goalObserverEvents
const emptySnapshot = emptyHooks.getSnapshot()
assert.equal(Array.isArray(emptySnapshot.entries), true)
assert.equal(emptySnapshot.entries.length, 0)
assert.equal(typeof emptyHooks.subscribe(() => {}), 'function')

// ── 8. 缺原生渲染器：返回 null，只 warn 一次，不把页面打崩 ────────────
registered = false
assert.equal(render(), null)
assert.equal(render(), null)
assert.equal(warnings.length, 1, '只 warn 一次')
assert.match(warnings[0], /缺少原生目标通知渲染器/)

// ── 9. 订阅回调真的被调用（假 eventSource）────────────────────────────
let received = 0
const source2 = {
  getSnapshot: () => snapshot,
  subscribe: listener => {
    listener(snapshot)
    return () => { received = -1 }
  },
}
binding = () => ({ eventSource: source2 })
const hooks2 = registration.inject('session-3').hooks.goalObserverEvents
hooks2.subscribe(() => { received += 1 })
assert.equal(received, 1)

console.log('PASS：未生成不伪造 · 三元组隔离 · continue/complete/pause/error · 原消息/来源/块引用不变 · 形状容错 · 注册与 hook · 缺渲染器降级')
