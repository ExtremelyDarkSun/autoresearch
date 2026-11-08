// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 插件**接线**的离线自检 —— 用假 ctx 驱动 index.js 的 apply()，验 prepend、子级判定、默认不裁、
//          显式裁剪、收件箱同步、策略读坏后的安全回落、异常回退。
// ORIGIN: install/agent-instruction-filter/selftest-plugin.mjs（期望值改为中性；补「读策略失败不回退 drop」）
// CUMULATIVE-DELTA: 首版。夹具改中性路径；新增三条关键反向对照：
//   ① 出厂配置不注册监听器；② policyPath 读不到 / JSON 坏 / 结构坏 ⇒ 一律保持原样（≠ 回退旧 drop）；
//   ③ 只有显式策略才裁。⚠️ 这不等于真机验证：假 ctx 只复现「谁在什么时候被调用」，
//   不复现宿主的真实事件分发与 scope；真机验收见 docs/dsh.md 的三层验收。

import { strict as assert } from 'node:assert'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { apply, loadPolicy, normalizeConfig } from './index.js'

const OPEN = '<system-reminder>'
const CLOSE = '</system-reminder>'

let passed = 0
const failures = []

async function check(name, fn) {
  try {
    await fn()
    passed += 1
  } catch (error) {
    failures.push(`${name}: ${error.message}`)
  }
}

const HEADER = ['<!--', 'GENERATED-BY: 某生成者', 'LAST-EDIT: 某生成者', 'PURPOSE: 说明', 'ORIGIN: none', 'CUMULATIVE-DELTA: 首版', '-->'].join('\n')

const SUB_DOC = [
  HEADER,
  '',
  '# 子系统规则',
  '',
  '## 跑评测（硬前置）',
  '',
  '> 开 GPU 之前必须先通读实验规则。',
  '',
  '### 已实测无效的改动',
  '',
  '| 改动 | 结果 |',
  '|---|---|',
  '| 加大调用上限 | 0 题受益 |',
].join('\n')

const INSTRUCTION_TEXT = [OPEN, 'The following workspace instructions may be relevant to your work.', '', 'Instructions from: sub\\AGENTS.md', '', SUB_DOC, CLOSE].join('\n')

function instructionMessage() {
  return {
    id: 'm-instructions-1',
    role: 'user',
    content: [{ type: 'text', text: INSTRUCTION_TEXT }],
    source: { kind: 'agent-instructions', form: 'instructions', baseline: true, changes: [] },
  }
}

function plainMessage() {
  return { id: 'm-plain-1', role: 'user', content: [{ type: 'text', text: '普通用户消息' }] }
}

function fakeAgent(id, header = {}) {
  const removed = []
  const replaced = []
  const nextStep = []
  return {
    id,
    session: { header },
    inbox: {
      nextStep,
      remove(messageId) {
        removed.push(messageId)
      },
      replace(messageId, replacement) {
        replaced.push([messageId, replacement])
      },
    },
    removed,
    replaced,
  }
}

function makeCtx(roots, { rootsThrows = false } = {}) {
  const registered = []
  const warnings = []
  const ctx = {
    on(name, handler, options) {
      registered.push({ name, handler, options })
    },
    get(name) {
      if (name !== 'agents') return undefined
      return {
        roots() {
          if (rootsThrows) throw new Error('registry unavailable')
          return roots
        },
      }
    },
    logger: { warn(...args) { warnings.push(args) } },
    effect(fn) {
      return fn()
    },
  }
  return { ctx, registered, warnings }
}

const runPreStep = (handler, agent, decision) => handler({ agent }, async () => decision)

const rootAgent = fakeAgent('root-1')
const childAgent = fakeAgent('child-1')

// 临时策略文件（离线）：显式 drop 只用来证明「能力还在」，⛔ 不是出厂默认。
const sandbox = mkdtempSync(join(tmpdir(), 'instruction-filter-selftest-'))
const dropPolicyPath = join(sandbox, 'drop-policy.json')
writeFileSync(
  dropPolicyPath,
  JSON.stringify({ rules: [{ match: '**/sub/AGENTS.md', mode: 'drop' }], defaultRule: { mode: 'keep' } }),
  'utf8',
)
const brokenJsonPath = join(sandbox, 'broken.json')
writeFileSync(brokenJsonPath, '{ not json', 'utf8')
const badShapePath = join(sandbox, 'bad-shape.json')
writeFileSync(badShapePath, JSON.stringify({ rules: 'nope' }), 'utf8')
const missingPath = join(sandbox, 'no-such-policy.json')

// ── 配置收口 ────────────────────────────────────────────────────────────
await check('出厂配置（不传 config）就是 off ⇒ 一个监听器都不注册', () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, undefined)
  assert.equal(registered.length, 0)
  assert.equal(normalizeConfig(undefined).mode, 'off')
})

await check('显式 mode=off ⇒ 不注册', () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'off', childOnly: true })
  assert.equal(registered.length, 0)
})

await check('非法 mode 被收口成 off（不是 filter）', () => {
  assert.equal(normalizeConfig({ mode: 'nuke' }).mode, 'off')
})

// ── 默认行为：不裁 ──────────────────────────────────────────────────────
await check('⭐ 子 agent + 出厂策略：注入消息逐字保留，decision 原样返回', async () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true })
  const decision = { kind: 'enter', messages: [instructionMessage(), plainMessage()] }
  const out = await runPreStep(registered[0].handler, childAgent, decision)
  assert.equal(out, decision, '没有规则命中时必须是同一个对象（不改内容）')
  assert.equal(out.messages.length, 2, '注入消息不能被丢')
  assert.ok(out.messages[0].content[0].text.includes('## 跑评测（硬前置）'))
})

await check('主 agent 不受影响：返回同一个 decision 对象', async () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true, policyPath: dropPolicyPath })
  const decision = { kind: 'enter', messages: [instructionMessage()] }
  const out = await runPreStep(registered[0].handler, rootAgent, decision)
  assert.equal(out, decision, '主 agent 路径必须原样返回')
  assert.equal(out.messages.length, 1, '主 agent 仍拿到注入消息')
})

// ── 显式裁剪 ────────────────────────────────────────────────────────────
await check('显式策略：子 agent 的命中段被整条丢弃，普通消息原样留下', async () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true, policyPath: dropPolicyPath })
  const plain = plainMessage()
  const decision = { kind: 'enter', messages: [instructionMessage(), plain] }
  const out = await runPreStep(registered[0].handler, childAgent, decision)
  assert.equal(out.messages.length, 1, '只剩普通消息')
  assert.equal(out.messages[0], plain, '留下的必须还是同一个对象')
  assert.ok(!out.messages.some((m) => m.source?.kind === 'agent-instructions'))
})

await check('收件箱里的未投递副本被移除（否则未裁的那份会重新进入）', async () => {
  const agent = fakeAgent('child-2')
  agent.inbox.nextStep.push(instructionMessage())
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true, policyPath: dropPolicyPath })
  await runPreStep(registered[0].handler, agent, { kind: 'enter', messages: [instructionMessage()] })
  assert.deepEqual(agent.removed, ['m-instructions-1'], '收件箱副本应被移除')
  assert.equal(agent.replaced.length, 0, '整条丢时不需要替换')
})

await check('mode=strip 是显式能力：子 agent 拿不到任何注入', async () => {
  const agent = fakeAgent('child-3')
  agent.inbox.nextStep.push(instructionMessage())
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'strip', childOnly: true })
  const out = await runPreStep(registered[0].handler, agent, { kind: 'enter', messages: [instructionMessage(), plainMessage()] })
  assert.equal(out.messages.length, 1)
  assert.deepEqual(agent.removed, ['m-instructions-1'])
})

await check('childOnly=false 时主 agent 也被裁（显式策略）', async () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: false, policyPath: dropPolicyPath })
  const out = await runPreStep(registered[0].handler, rootAgent, { kind: 'enter', messages: [instructionMessage(), plainMessage()] })
  assert.equal(out.messages.length, 1, '注入消息应被丢')
  assert.ok(!out.messages.some((m) => m.source?.kind === 'agent-instructions'))
})

// ── 策略读坏：安全回落 ──────────────────────────────────────────────────
for (const [label, path, why] of [
  ['文件不存在', missingPath, 'unreadable'],
  ['JSON 坏', brokenJsonPath, 'unreadable'],
  ['结构不合法', badShapePath, 'rules 不是数组'],
]) {
  await check(`⭐ 策略${label} ⇒ 回落 keep（不是回退旧 drop）`, async () => {
    const { ctx, registered, warnings } = makeCtx([rootAgent])
    apply(ctx, { mode: 'filter', childOnly: true, policyPath: path })
    const decision = { kind: 'enter', messages: [instructionMessage()] }
    const out = await runPreStep(registered[0].handler, childAgent, decision)
    assert.equal(out, decision, '读不到策略时不该改动任何注入')
    assert.equal(out.messages.length, 1)
    assert.ok(warnings.length >= 1, '应记一条 warn（出事时能查）')
    const loaded = loadPolicy({ policyPath: path }, ctx)
    assert.equal(loaded.source, 'fallback-keep')
    if (why === 'rules 不是数组') assert.match(String(loaded.why), /rules/)
  })
}

await check('出厂策略来源标记为 bundled，不是 fallback', () => {
  const { ctx } = makeCtx([rootAgent])
  const loaded = loadPolicy({ policyPath: null }, ctx)
  assert.equal(loaded.source, 'bundled')
  assert.deepEqual(loaded.policy.rules, [])
})

// ── 异常与回退 ──────────────────────────────────────────────────────────
await check('异常 + 默认 onError=keep ⇒ 原样返回，不影响回合', async () => {
  const agent = fakeAgent('child-4')
  Object.defineProperty(agent.inbox, 'nextStep', { get() { throw new Error('boom') } })
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true })
  const decision = { kind: 'enter', messages: [instructionMessage()] }
  const out = await runPreStep(registered[0].handler, agent, decision)
  assert.equal(out, decision, 'fail-open')
})

await check('异常 + 显式 onError=drop ⇒ 丢掉指令消息但保留普通消息', async () => {
  const agent = fakeAgent('child-5')
  Object.defineProperty(agent.inbox, 'nextStep', { get() { throw new Error('boom') } })
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true, onError: 'drop' })
  const plain = plainMessage()
  const out = await runPreStep(registered[0].handler, agent, { kind: 'enter', messages: [instructionMessage(), plain] })
  assert.equal(out.messages.length, 1)
  assert.equal(out.messages[0], plain)
})

await check('roots() 抛错时回退到持久谱系 parentSession', async () => {
  const lineageChild = fakeAgent('child-6', { parentSession: 'root-1' })
  const { ctx, registered } = makeCtx([rootAgent], { rootsThrows: true })
  apply(ctx, { mode: 'filter', childOnly: true, policyPath: dropPolicyPath })
  const out = await runPreStep(registered[0].handler, lineageChild, { kind: 'enter', messages: [instructionMessage()] })
  assert.equal(out.messages.length, 0, '有 parentSession ⇒ 判为子级 ⇒ 注入消息被丢')
})

await check('没有 parentSession 且 roots() 抛错 ⇒ 判不出子级 ⇒ 保守放过', async () => {
  const unknown = fakeAgent('unknown-1')
  const { ctx, registered } = makeCtx([rootAgent], { rootsThrows: true })
  apply(ctx, { mode: 'filter', childOnly: true, policyPath: dropPolicyPath })
  const decision = { kind: 'enter', messages: [instructionMessage()] }
  const out = await runPreStep(registered[0].handler, unknown, decision)
  assert.equal(out, decision, '判不出来时按主 agent 处理（fail-open）')
})

await check('decision.kind=reject 时原样返回', async () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true, policyPath: dropPolicyPath })
  const decision = { kind: 'reject', messages: [instructionMessage()] }
  const out = await runPreStep(registered[0].handler, childAgent, decision)
  assert.equal(out, decision)
})

// ── 顺序对照：为什么必须有 prepend ──────────────────────────────────────
function makeWaterfall() {
  const hooks = []
  return {
    on(handler, { prepend = false } = {}) {
      if (prepend) hooks.unshift(handler)
      else hooks.push(handler)
    },
    dispatch(payload, inner) {
      const cbs = [...hooks]
      const next = () => {
        const cb = cbs.shift() ?? inner
        return cb(payload, next)
      }
      return next()
    },
  }
}

const stockPlugin = async (payload, next) => {
  const decision = await next()
  return { ...decision, messages: [...decision.messages, instructionMessage()] }
}

const innerDecision = async () => ({ kind: 'enter', messages: [] })

await check('顺序对照（正）：带 prepend 时本插件看得到注入消息 ⇒ 能把它丢掉', async () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true, policyPath: dropPolicyPath })
  const wf = makeWaterfall()
  wf.on(stockPlugin)
  wf.on(registered[0].handler, { prepend: true })
  const out = await wf.dispatch({ agent: childAgent }, innerDecision)
  assert.equal(out.messages.length, 0, '注入消息应被本插件丢掉')
})

await check('顺序对照（反）：不带 prepend 时本插件看不到注入消息 ⇒ 它照旧进上下文', async () => {
  const { ctx, registered } = makeCtx([rootAgent])
  apply(ctx, { mode: 'filter', childOnly: true, policyPath: dropPolicyPath })
  const wf = makeWaterfall()
  wf.on(stockPlugin)
  wf.on(registered[0].handler)
  const out = await wf.dispatch({ agent: childAgent }, innerDecision)
  assert.equal(out.messages.length, 1, '这正是 prepend 存在的理由：排在宿主插件后面就什么都裁不掉')
})

rmSync(sandbox, { recursive: true, force: true })

// ── 结果 ────────────────────────────────────────────────────────────────
if (failures.length > 0) {
  console.error(`FAIL: ${failures.length} 条未通过（通过 ${passed}）`)
  for (const failure of failures) console.error('  ✗ ' + failure)
  process.exit(1)
}
console.log(`PASS: ${passed} 条全通过（接线层：默认不裁 · 显式裁剪 · 读坏回落 keep · fail-open）`)
