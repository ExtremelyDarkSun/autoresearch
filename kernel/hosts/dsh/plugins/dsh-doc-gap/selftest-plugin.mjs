/**
 * GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * PURPOSE: dsh-doc-gap 的接线自测：用假 ctx 驱动 apply()，不联网、不加载宿主、不花 token。
 * ORIGIN: install/dsh-doc-gap/selftest-plugin.mjs（夹具改中性；补「默认不启用 / 前缀隔离 / 超限诊断」）
 * CUMULATIVE-DELTA: 首版。⛔ 不含任何实例项目名、文档绝对路径与历史读数。
 *
 * 用法：node selftest-plugin.mjs
 */

import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { apply } from './index.js'

let passed = 0
const test = async (label, fn) => {
  try {
    await fn()
    passed += 1
    console.log(`  ok   ${label}`)
  } catch (error) {
    console.log(`  FAIL ${label}\n       ${error.message}`)
    process.exitCode = 1
  }
}

// --- 假环境 ---------------------------------------------------------------

const CWD_HIT = 'C:\\Work\\Proj'
const CWD_CLONE = 'C:\\Temp\\proj'
const CWD_MISS = 'C:\\Other\\Project'

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'docgap-wire-'))

function makeDoc(text = '# 规则\n- 目标：可重复的本地基线\n', name = 'RULES.md') {
  const file = path.join(tmp, name)
  fs.writeFileSync(file, text, 'utf8')
  return file
}

/** 假 ctx：记录监听器，get() 只认 llm / agents。 */
function makeCtx({ llm, roots }) {
  const handlers = new Map()
  return {
    handlers,
    on(event, fn) {
      if (!handlers.has(event)) handlers.set(event, [])
      handlers.get(event).push(fn)
    },
    get(name) {
      if (name === 'llm') return llm
      if (name === 'agents') return { roots: () => roots }
      return undefined
    },
    async emit(event, ...args) {
      for (const fn of handlers.get(event) ?? []) await fn(...args)
    },
  }
}

/** 假 llm：把判词原样吐成 text-delta，然后正常收尾。 */
function makeLlm(verdict) {
  const calls = []
  return {
    calls,
    stream(options) {
      calls.push(options)
      const text = typeof verdict === 'function' ? verdict(options) : verdict
      return (async function* () {
        if (text instanceof Error) throw text
        yield { type: 'text-delta', index: 0, text }
        yield { type: 'finish', reason: { kind: 'stop' } }
      })()
    },
  }
}

function makeAgent({ cwd = CWD_HIT, sessionId = 's1' } = {}) {
  const steered = []
  return {
    steered,
    session: { id: sessionId, header: { cwd } },
    options: {},
    steer(message) {
      steered.push(message)
    },
  }
}

/** 一次完整回合：人类提问 → assistant 答复 → 轮次关闭。 */
async function runTurn(ctx, agent, { question = '这个项目的目的是什么？', answer = '为了……', turn = 1, source = 'user', route = { provider: 'test-provider', model: 'test-model' } } = {}) {
  await ctx.emit('session/event', agent.session, { type: 'turn/start', data: { turn } })
  await ctx.emit('session/event', agent.session, {
    type: 'user/message',
    data: { role: 'user', content: [{ type: 'text', text: question }], source: { kind: source } },
  })
  await ctx.emit('session/event', agent.session, {
    type: 'assistant/message',
    data: { turn, step: 1, message: { role: 'assistant', content: [{ type: 'text', text: answer }] } },
  })
  if (route) {
    await ctx.emit('agent/request', { agent, turn, step: 1, signal: AbortSignal.timeout(5000) }, async () => route)
  }
  await ctx.emit('agent/turn-stopping', { agent, turn, signal: AbortSignal.timeout(5000) })
}

/** 临时抓 stderr（用于证明「超限诊断无条件出现」）。 */
async function captureStderr(fn) {
  const chunks = []
  const original = process.stderr.write
  process.stderr.write = (chunk) => {
    chunks.push(String(chunk))
    return true
  }
  try {
    await fn()
  } finally {
    process.stderr.write = original
  }
  return chunks.join('')
}

const baseConfig = (docPath, over = {}) => ({
  enabled: true,
  threshold: 0.8,
  notify: 'hint',
  logLevel: 'silent',
  projects: [CWD_HIT],
  docs: [{ label: '规则', path: docPath }],
  judge: { backend: 'session', timeoutMs: 5000, maxTokens: 4000 },
  ...over,
})

const VERDICT_MISSING = JSON.stringify({ point: '项目目的', coverage: 'missing', p_missing: 0.92, why: '无目的段' })
const VERDICT_COVERED = JSON.stringify({ point: '项目目的', coverage: 'covered', p_missing: 0.05, why: '§1 有' })

const doc = makeDoc()

// --- 用例 -----------------------------------------------------------------

console.log('启用开关')
await test('⭐ 出厂配置（不传 config）⇒ 一个监听器都不注册 ⇒ 不会有模型调用', async () => {
  const llm = makeLlm(VERDICT_MISSING)
  const agent = makeAgent()
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, undefined)
  assert.equal(ctx.handlers.size, 0)
  await runTurn(ctx, agent)
  assert.equal(llm.calls.length, 0)
})
await test('enabled: false ⇒ 一个监听器都不注册', () => {
  const ctx = makeCtx({ llm: makeLlm(VERDICT_MISSING), roots: [] })
  apply(ctx, baseConfig(doc, { enabled: false }))
  assert.equal(ctx.handlers.size, 0)
})
await test('enabled: true 但没配文档 ⇒ 不注册（warn 后静默）', () => {
  const ctx = makeCtx({ llm: makeLlm(VERDICT_MISSING), roots: [] })
  apply(ctx, baseConfig(doc, { docs: [] }))
  assert.equal(ctx.handlers.size, 0)
})

console.log('项目范围（规范化路径前缀）')
await test('cwd 不在项目里 ⇒ 一次都不判定', async () => {
  const llm = makeLlm(VERDICT_MISSING)
  const agent = makeAgent({ cwd: CWD_MISS })
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(llm.calls.length, 0)
  assert.equal(agent.steered.length, 0)
})
await test('⭐ 另一份同名克隆（路径里含同名片段）⇒ 不判定', async () => {
  const llm = makeLlm(VERDICT_MISSING)
  const agent = makeAgent({ cwd: CWD_CLONE })
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(llm.calls.length, 0, '前缀不同 ⇒ 不该命中')
})
await test('真实目录边界：子目录命中、同前缀的兄弟目录不命中', async () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'docgap-real-'))
  const proj = path.join(base, 'proj')
  const sibling = path.join(base, 'proj-other')
  const child = path.join(proj, 'sub')
  fs.mkdirSync(child, { recursive: true })
  fs.mkdirSync(sibling, { recursive: true })

  const hitAgent = makeAgent({ cwd: child })
  const hitCtx = makeCtx({ llm: makeLlm(VERDICT_MISSING), roots: [hitAgent] })
  apply(hitCtx, baseConfig(doc, { projects: [proj] }))
  await runTurn(hitCtx, hitAgent)
  assert.equal(hitAgent.steered.length, 1, '子目录应命中')

  const missAgent = makeAgent({ cwd: sibling })
  const missLlm = makeLlm(VERDICT_MISSING)
  const missCtx = makeCtx({ llm: missLlm, roots: [missAgent] })
  apply(missCtx, baseConfig(doc, { projects: [proj] }))
  await runTurn(missCtx, missAgent)
  assert.equal(missLlm.calls.length, 0, '兄弟目录不该命中')

  fs.rmSync(base, { recursive: true, force: true })
})
await test('反向对照：cwd 命中（含子目录）⇒ 判定发生且 steer 一次', async () => {
  const llm = makeLlm(VERDICT_MISSING)
  const agent = makeAgent({ cwd: `${CWD_HIT}\\sub` })
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(llm.calls.length, 1)
  assert.equal(agent.steered.length, 1)
})
await test('拿不到 cwd ⇒ 不触发（宁可不判）', async () => {
  const llm = makeLlm(VERDICT_MISSING)
  const agent = makeAgent()
  agent.session.header = {}
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(agent.steered.length, 0)
})

console.log('子 agent')
await test('子 agent（不在 roots 里）⇒ 不判定', async () => {
  const llm = makeLlm(VERDICT_MISSING)
  const agent = makeAgent()
  const ctx = makeCtx({ llm, roots: [makeAgent({ sessionId: 'root' })] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(agent.steered.length, 0)
})

console.log('门控与提示')
await test('missing + p=0.92 ⇒ steer 一条提示，不点名落点文件', async () => {
  const agent = makeAgent()
  const ctx = makeCtx({ llm: makeLlm(VERDICT_MISSING), roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  const text = agent.steered[0].content[0].text
  assert.match(text, /落点由你判断/)
  assert.match(text, /不需要就忽略这条/)
  assert.equal(agent.steered[0].role, 'user')
  assert.equal(agent.steered[0].source.kind, 'doc-gap')
  assert.equal(Object.isFrozen(agent.steered[0]), true, 'steer 的消息必须不可变')
})
await test('covered ⇒ 不 steer', async () => {
  const agent = makeAgent()
  const ctx = makeCtx({ llm: makeLlm(VERDICT_COVERED), roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(agent.steered.length, 0)
})
await test('p 低于阈值 ⇒ 不 steer', async () => {
  const agent = makeAgent()
  const low = JSON.stringify({ point: 'x', coverage: 'partial', p_missing: 0.55 })
  const ctx = makeCtx({ llm: makeLlm(low), roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(agent.steered.length, 0)
})
await test('notify: ask ⇒ 文本要求去 ask_user_question；hint 不含它', async () => {
  const askAgent = makeAgent()
  const askCtx = makeCtx({ llm: makeLlm(VERDICT_MISSING), roots: [askAgent] })
  apply(askCtx, baseConfig(doc, { notify: 'ask' }))
  await runTurn(askCtx, askAgent)
  assert.match(askAgent.steered[0].content[0].text, /ask_user_question/)

  const hintAgent = makeAgent()
  const hintCtx = makeCtx({ llm: makeLlm(VERDICT_MISSING), roots: [hintAgent] })
  apply(hintCtx, baseConfig(doc, { notify: 'hint' }))
  await runTurn(hintCtx, hintAgent)
  assert.doesNotMatch(hintAgent.steered[0].content[0].text, /ask_user_question/)
})

console.log('政策外置到判定输入')
await test('自定义政策出现在发给模型的 system 与 prompt 里', async () => {
  const agent = makeAgent()
  const llm = makeLlm(VERDICT_MISSING)
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(doc, {
    policy: {
      scope: { include: ['只看甲类'], exclude: ['不看乙类'] },
      criteria: { covered: 'C', partial: 'P', missing: 'M' },
      anchor: '锚点：只判甲类。',
    },
  }))
  await runTurn(ctx, agent)
  const sent = llm.calls[0]
  assert.match(sent.system, /只看甲类/)
  assert.match(sent.system, /不看乙类/)
  assert.match(sent.system, /C = covered/)
  const prompt = sent.messages[0].content[0].text
  assert.match(prompt, /锚点：只判甲类。/)
})

console.log('文档超限诊断')
await test('⭐ 文档超上限：即使 logLevel=silent 也往 stderr 写诊断', async () => {
  const big = makeDoc('y'.repeat(500), 'big.md')
  const agent = makeAgent()
  const llm = makeLlm(VERDICT_MISSING)
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(big, { maxDocChars: 100 }))
  const stderr = await captureStderr(async () => {
    await runTurn(ctx, agent)
  })
  assert.match(stderr, /文档超过上限/)
  assert.match(stderr, /500/)
  assert.match(stderr, /100/)
  // 判定照旧发生，但材料被标注「已截断」
  const sent = llm.calls[0].messages[0].content[0].text
  assert.match(sent, /已截断/)
})
await test('⭐ 截断硬闸：模型判 missing 0.95，默认阈值下也不 steer', async () => {
  const big = makeDoc('z'.repeat(500), 'big-gate.md')
  const agent = makeAgent()
  const llm = makeLlm(JSON.stringify({ point: '项目目的', coverage: 'missing', p_missing: 0.95 }))
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(big, { maxDocChars: 100 }))
  const stderr = await captureStderr(async () => {
    await runTurn(ctx, agent)
  })
  assert.equal(agent.steered.length, 0, '材料不完整时不许提示')
  assert.match(stderr, /硬闸|截断/, '硬闸降级要留痕')
})
await test('截断硬闸：阈值放宽到 0.5 时仍提示，但置信度被压到 60%', async () => {
  const big = makeDoc('w'.repeat(500), 'big-gate2.md')
  const agent = makeAgent()
  const llm = makeLlm(JSON.stringify({ point: '项目目的', coverage: 'missing', p_missing: 0.97 }))
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(big, { maxDocChars: 100, threshold: 0.5 }))
  await captureStderr(async () => {
    await runTurn(ctx, agent)
  })
  assert.equal(agent.steered.length, 1)
  const text = agent.steered[0].content[0].text
  assert.match(text, /置信度 60%/)
  assert.match(text, /写得不够清楚/, '被降到 partial')
})
await test('文档读不到 ⇒ 不 steer（不是诬告），并写诊断', async () => {
  const agent = makeAgent()
  const llm = makeLlm(VERDICT_MISSING)
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(path.join(tmp, 'no-such-file.md')))
  const stderr = await captureStderr(async () => {
    await runTurn(ctx, agent)
  })
  assert.equal(agent.steered.length, 0)
  assert.equal(llm.calls.length, 0)
  assert.match(stderr, /读不到/)
})

console.log('fail-open')
await test('判定模型抛异常 ⇒ 不 steer、不把异常抛出去', async () => {
  const agent = makeAgent()
  const ctx = makeCtx({ llm: makeLlm(new Error('boom')), roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(agent.steered.length, 0)
})
await test('判定返回垃圾文本 ⇒ 不 steer', async () => {
  const agent = makeAgent()
  const ctx = makeCtx({ llm: makeLlm('我觉得还行'), roots: [agent] })
  apply(ctx, baseConfig(doc))
  await runTurn(ctx, agent)
  assert.equal(agent.steered.length, 0)
})

console.log('回合边界')
await test('注入的非人类消息（source.kind ≠ user）不覆盖提问', async () => {
  const agent = makeAgent()
  const llm = makeLlm(VERDICT_MISSING)
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(doc, { minGapMs: 0 }))
  await runTurn(ctx, agent, { question: '真正的人类问题' })
  await runTurn(ctx, agent, { question: '这是定时注入', source: 'cron', turn: 2 })
  assert.equal(llm.calls.length, 2)
  const second = llm.calls[1].messages[0].content[0].text
  assert.match(second, /真正的人类问题/, '应沿用上一次真正的人类提问')
  assert.doesNotMatch(second, /定时注入/)
})
await test('没有人类提问的回合 ⇒ 不判定', async () => {
  const agent = makeAgent()
  const llm = makeLlm(VERDICT_MISSING)
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(doc))
  await ctx.emit('session/event', agent.session, { type: 'turn/start', data: { turn: 1 } })
  await ctx.emit('session/event', agent.session, {
    type: 'assistant/message',
    data: { turn: 1, step: 1, message: { role: 'assistant', content: [{ type: 'text', text: '自说自话' }] } },
  })
  await ctx.emit('agent/turn-stopping', { agent, turn: 1, signal: AbortSignal.timeout(5000) })
  assert.equal(llm.calls.length, 0)
})
await test('同一会话连开两个回合，minGapMs 内只提示一次', async () => {
  const agent = makeAgent()
  const ctx = makeCtx({ llm: makeLlm(VERDICT_MISSING), roots: [agent] })
  apply(ctx, baseConfig(doc, { minGapMs: 60000 }))
  await runTurn(ctx, agent, { turn: 1 })
  await runTurn(ctx, agent, { turn: 2 })
  assert.equal(agent.steered.length, 1)
})
await test('回合结束时 signal 已中止 ⇒ 不判定', async () => {
  const agent = makeAgent()
  const llm = makeLlm(VERDICT_MISSING)
  const ctx = makeCtx({ llm, roots: [agent] })
  apply(ctx, baseConfig(doc))
  await ctx.emit('session/event', agent.session, { type: 'turn/start', data: { turn: 1 } })
  await ctx.emit('session/event', agent.session, {
    type: 'user/message',
    data: { role: 'user', content: [{ type: 'text', text: '问题' }], source: { kind: 'user' } },
  })
  await ctx.emit('session/event', agent.session, {
    type: 'assistant/message',
    data: { turn: 1, step: 1, message: { role: 'assistant', content: [{ type: 'text', text: '答复' }] } },
  })
  const controller = new AbortController()
  controller.abort()
  await ctx.emit('agent/turn-stopping', { agent, turn: 1, signal: controller.signal })
  assert.equal(llm.calls.length, 0)
})

fs.rmSync(tmp, { recursive: true, force: true })

console.log(`\n${passed} passed`)
