// GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
// LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
// PURPOSE: 观察者插件**接线**的离线自检 —— 用 test-sim-host.mjs 的假宿主驱动 index.mjs 的 apply()，
//          覆盖：正常 continue/complete、pause 拦截、失败继续、过时 revision / 用户暂停/改目标、
//          用户停止、卸载、不重复 next、通知默认关闭与节流、解析重试、完成需证据、真人输入优先、
//          已有观察者消息不重复、缓存、普通 goal 不介入、宿主读不到 goal 时 fail-open。
// ORIGIN: none
// CUMULATIVE-DELTA: 首版。⚠️ 这不是真机验收：假宿主只复现本插件碰到的那部分契约（事件顺序、CAS、chunk 协议），
//          不复现真实模型、真实 agent-loop、真实 cordis scope。真机三层验收见 README「未验证边界」。

import { strict as assert } from 'node:assert'
import { readFileSync } from 'node:fs'
import { apply, __setObserverClock, __setObserverSpawn } from './index.mjs'
import { SIM_NOW, createSimHost, createSpawnSpy } from './test-sim-host.mjs'

let passed = 0
const failures = []
const activeHosts = []

function answer(value) {
  return JSON.stringify(value)
}

function setup({ config = {}, driver = 'after', goal = {}, goalsGetError, goalsGetErrorOnCall, completeError, agentOptions = {} } = {}) {
  const host = createSimHost({ goal, goalsGetError, goalsGetErrorOnCall, completeError })
  const agent = host.createAgent(agentOptions)
  const controller = new AbortController()
  let driverStats
  if (driver === 'before') driverStats = host.installDriver()
  apply(host.ctx, { enabled: true, ...config })
  if (driver === 'after') driverStats = host.installDriver()
  activeHosts.push(host)
  return { host, agent, controller, driver: driverStats }
}

async function until(predicate, label, tries = 200) {
  for (let i = 0; i < tries; i++) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  throw new Error(`等待超时：${label}`)
}

function records(agent, kind) {
  return agent.session.events.filter((event) => event.type === 'user/message' && event.data?.source?.kind === kind)
}

function observerRecords(agent) {
  return records(agent, 'goal-observer')
}

function errorRecords(agent) {
  return records(agent, 'goal-observer-error')
}

async function check(name, fn) {
  __setObserverSpawn(null)
  __setObserverClock(() => SIM_NOW)
  try {
    await fn()
    passed++
  } catch (error) {
    failures.push(`${name}: ${error.stack ?? error.message}`)
  } finally {
    while (activeHosts.length > 0) {
      try {
        activeHosts.pop().dispose()
      } catch {
        /* ignore */
      }
    }
    __setObserverSpawn(null)
    __setObserverClock(() => SIM_NOW)
  }
}

// ══════════════════ 1. 正常 continue ══════════════════
await check('正常 continue：追加一条独立来源指令，goal 与 goal 轮原件都不变', async () => {
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '核实 runner 存活', evidence: '结果尚未齐全' }) })
  const messages = [h.host.goalRoundMessage({ round: 2 })]
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })

  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 2)
  assert.equal(decision.messages[0], messages[0], 'goal 轮消息必须是同一个对象，不能被改写')
  const observer = decision.messages[1]
  assert.equal(observer.source.kind, 'goal-observer')
  assert.equal(observer.source.goalId, 'goal-1')
  assert.equal(observer.source.revision, 1)
  assert.equal(observer.source.round, 2)
  assert.equal(observer.source.provider, 'sim-provider')
  assert.equal(observer.source.model, 'sim-model')
  assert.match(observer.content[0].text, /核实 runner 存活/)
  assert.match(observer.content[0].text, /不是新的真人授权|不要将其解释为新的真人授权/)

  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goal().revision, 1)
  assert.equal(h.host.goalCalls.complete.length, 0)
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.equal(h.host.goalCalls.block.length, 0)
  assert.equal(h.host.calls.terminal, 1, 'next 只应走到端点一次')
  assert.equal(h.host.llm.calls.length, 1)
  assert.equal(h.driver.blocks, 0)
  assert.equal(h.host.provided.goalObserver.version, '0.2.0')
})

await check('round 0（goal 初始提示）不触发观察者', async () => {
  const h = setup({ goal: { roundsStarted: 0 } })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 0 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.llm.calls.length, 0, 'round 0 不是自动轮')
  assert.equal(observerRecords(h.agent).length, 0)
  assert.equal(h.host.calls.terminal, 1)
})

await check('首个自动 round 1：roundsStarted=0 时正常接管并注入 round=1 指令', async () => {
  const h = setup({ goal: { roundsStarted: 0 } })
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '第一轮只做 A' }) })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 1 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 2)
  assert.equal(decision.messages[1].source.round, 1)
  assert.match(decision.messages[1].content[0].text, /第一轮只做 A/)
  assert.equal(h.host.goal().roundsStarted, 0)
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.llm.calls.length, 1)
  assert.equal(h.host.calls.terminal, 1)
})

// ══════════════════ 2. 正常 complete（完成需证据）══════════════════
await check('正常 complete：先让宿主落库成功，再写完成记录；不触发 driver block', async () => {
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'complete', evidence: '三轮输出齐全且各轮无附加内容' }) })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })

  assert.equal(decision.kind, 'reject')
  assert.equal(h.host.goal().phase, 'complete')
  assert.equal(h.host.goalCalls.complete.length, 1)
  assert.deepEqual(h.host.goalCalls.complete[0].ref, { id: 'goal-1', revision: 1 })
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.equal(h.host.goalCalls.block.length, 0)

  const record = observerRecords(h.agent).at(-1)
  assert.ok(record, '应写一条 goal-observer 记录')
  assert.equal(record.data.source.decision.action, 'complete')
  assert.match(record.data.content[0].text, /观察者判定：complete/)
  assert.equal((record.data.content[0].text.match(/三轮输出齐全/g) ?? []).length, 1, 'evidence 不能在指令和依据里各写一遍')
  assert.equal(h.driver.blocks, 0, 'goal 已 complete 时 driver 不应把它 block 掉')
  assert.equal(h.host.calls.terminal, 1)
})

// ══════════════════ 3. pause 拦截 ══════════════════
await check('pause 拦截：不改 goal、不发指令、只留给人看的提示；通知默认关闭不 spawn', async () => {
  const spawn = createSpawnSpy()
  __setObserverSpawn(spawn.spy)
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'pause', evidence: '需要人决定：付款' }) })
  const messages = [h.host.goalRoundMessage({ round: 2 })]
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })

  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 1)
  assert.equal(decision.messages[0], messages[0])
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goal().revision, 1)
  assert.equal(h.host.goalCalls.pause.length, 0, '⛔ 观察侧不得 pause')
  assert.equal(h.host.goalCalls.block.length, 0)
  assert.equal(h.host.goalCalls.complete.length, 0)

  const notice = errorRecords(h.agent).at(-1)
  assert.ok(notice, '应写一条给人看的 goal-observer-error 提示')
  assert.match(notice.data.content[0].text, /观察者原本想暂停/)
  assert.match(notice.data.content[0].text, /付款/)
  assert.match(notice.data.content[0].text, /不是新的授权/)
  assert.equal(spawn.calls.length, 0, '通知默认关闭，不得 spawn')
  assert.equal(h.host.calls.terminal, 1)
})

// ══════════════════ 4. 失败继续 ══════════════════
await check('失败继续：模型抛错 ⇒ 本轮没有观察者指令，goal 状态不变', async () => {
  const h = setup()
  h.host.llm.push({ error: new Error('模拟模型超时') })
  const messages = [h.host.goalRoundMessage({ round: 2 })]
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })

  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 1)
  assert.equal(decision.messages[0], messages[0])
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goal().revision, 1)
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.equal(h.host.goalCalls.block.length, 0)
  assert.equal(h.host.goalCalls.complete.length, 0)
  const record = errorRecords(h.agent).at(-1)
  assert.ok(record)
  assert.match(record.data.content[0].text, /观察者调用失败/)
  assert.match(record.data.content[0].text, /不是新的授权/)
  assert.equal(h.host.calls.terminal, 1)
  assert.match(h.host.logText(), /不因失败暂停 goal/)
})

await check('失败继续：finish(error) 也只记 warn，不暂停', async () => {
  const h = setup()
  h.host.llm.push({ text: '', finish: { kind: 'error', failure: { message: 'provider boom' } } })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.match(h.host.logText(), /provider boom/)
})

await check('⭐ 模型异常 + catch 状态读取失败：不 restore 再 next，真人消息不重复', async () => {
  const h = setup({ driver: 'before', goalsGetErrorOnCall: 3 })
  h.host.llm.push({ error: new Error('模型爆炸') })
  const messages = [h.host.goalRoundMessage({ round: 2 }), h.host.userMessage('真人：只投一次')]
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })

  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 2)
  assert.equal(decision.messages[0], messages[0])
  assert.equal(decision.messages[1], messages[1])
  assert.equal(h.agent.inbox.nextStep.length, 0, 'catch 里不能 restore 再 next，否则同一消息会投两次')
  assert.equal(h.host.calls.terminal, 1)
  assert.equal(h.host.llm.calls.length, 1)
  assert.equal(h.driver.blocks, 0)
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(errorRecords(h.agent).length, 0, '状态不可判定时不写失败记录，避免误导')
  assert.match(h.host.logText(), /状态不可判定\/已变化/)
})

// ══════════════════ 5. 过时 revision / goal 已暂停 ══════════════════
await check('过时 revision：观察者不被调用，goal 也不被改', async () => {
  const h = setup()
  const messages = [h.host.goalRoundMessage({ round: 2 })]
  h.host.setGoal({ revision: 2 })
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '不该被用' }) })
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })
  assert.equal(decision.kind, 'reject')
  assert.equal(h.host.llm.calls.length, 0, '过时轮次不得调用模型')
  assert.equal(h.host.goal().revision, 2)
  assert.equal(h.host.goalCalls.complete.length, 0)
})

await check('用户已暂停：观察者不被调用，不产生指令', async () => {
  const h = setup()
  const messages = [h.host.goalRoundMessage({ round: 2 })]
  h.host.userPause({ silent: true })
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })
  assert.equal(decision.kind, 'reject')
  assert.equal(h.host.llm.calls.length, 0)
  assert.equal(h.host.goal().phase, 'paused')
  assert.equal(observerRecords(h.agent).length, 0)
})

await check('轮次不匹配：roundsStarted 前进后旧轮不接管', async () => {
  const h = setup()
  const messages = [h.host.goalRoundMessage({ round: 2 })]
  h.host.setGoal({ roundsStarted: 5 })
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })
  assert.equal(decision.kind, 'reject')
  assert.equal(h.host.llm.calls.length, 0)
})

await check('⭐ 后置瞬时读取失败（第 3 次 get）：保持已消费 decision，不 restore+reject，不触发 driver block', async () => {
  const h = setup({ driver: 'before', goalsGetErrorOnCall: 3 })
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '不该被注入' }) })
  const messages = [h.host.goalRoundMessage({ round: 2 }), h.host.userMessage('真人：别丢我')]
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })

  assert.equal(decision.kind, 'enter', '读不到状态时只能 fail-open 放行，不能 reject')
  assert.equal(decision.messages.length, 2)
  assert.equal(decision.messages[0], messages[0])
  assert.equal(decision.messages[1], messages[1])
  assert.equal(decision.messages.filter((message) => message.source.kind === 'goal-observer').length, 0)
  assert.equal(h.agent.inbox.nextStep.length, 0, '不能 restore 后再交回同一批消息')
  assert.equal(h.driver.blocks, 0, '同一 active revision 不能被 driver 判 blocked')
  assert.equal(h.host.calls.terminal, 1)
  assert.equal(h.host.llm.calls.length, 1)
  assert.equal(h.host.goal().phase, 'active')
  assert.ok(h.host.goalCalls.get >= 3, `应至少读 3 次 goal，实际 ${h.host.goalCalls.get}`)
  assert.match(h.host.logText(), /无法确认 goal 状态/)
})

await check('后置 goal 轮次前进（revision/phase 未变）：保持 fail-open，不 reject 成 driver block', async () => {
  const h = setup({ driver: 'before' })
  h.host.llm.push({
    text: answer({ action: 'continue', instruction: '旧轮指令' }),
    before: () => h.host.setGoal({ roundsStarted: 2 }),
  })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  // 观察者返回 enter（不注入），外层 driver 自己按 reservation 判 reject；两者都不 block。
  assert.equal(decision.kind, 'reject')
  assert.equal(h.driver.blocks, 0)
  assert.equal(h.driver.staleRejects, 1)
  assert.equal(observerRecords(h.agent).length, 0)
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.calls.terminal, 1)
})

// ══════════════════ 6. 观察期间用户暂停（无信号中断的竞态）══════════════════
await check('观察期间用户暂停：不注入指令、不完成，已认领真人消息放回 next-step', async () => {
  const h = setup({ driver: 'before' })
  const messages = [h.host.goalRoundMessage({ round: 2 }), h.host.userMessage('真人插话：先停')]
  h.host.llm.push({
    text: answer({ action: 'continue', instruction: '不该被注入' }),
    before: () => h.host.userPause({ silent: true }),
  })
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })

  assert.equal(decision.kind, 'reject')
  assert.equal(h.host.goal().phase, 'paused')
  assert.equal(h.host.goalCalls.complete.length, 0)
  assert.equal(observerRecords(h.agent).length, 0)
  assert.equal(h.driver.blocks, 0, 'goal 已 paused 时 driver 不应再 block')
  assert.equal(h.host.calls.terminal, 1)
  assert.ok(h.agent.inbox.nextStep.some((message) => message.content[0].text === '真人插话：先停'), '真人消息不能丢')
})

// ══════════════════ 7. 观察期间用户改目标 ══════════════════
await check('观察期间用户改目标：旧 revision 的指令不进入新目标', async () => {
  const h = setup({ driver: 'before' })
  const messages = [h.host.goalRoundMessage({ round: 2 }), h.host.userMessage('真人：改成新目标')]
  h.host.llm.push({
    text: answer({ action: 'continue', instruction: '旧目标指令' }),
    before: () => h.host.userEdit('[观察者] 新目标', { silent: true }),
  })
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })

  assert.equal(decision.kind, 'reject')
  assert.equal(h.host.goal().revision, 2)
  assert.equal(h.host.goal().objective, '[观察者] 新目标')
  assert.equal(h.host.goalCalls.complete.length, 0)
  assert.equal(observerRecords(h.agent).length, 0)
  assert.equal(h.driver.blocks, 0)
  assert.ok(h.agent.inbox.nextStep.some((message) => message.content[0].text === '真人：改成新目标'))
})

// ══════════════════ 8. 用户停止（host signal abort）══════════════════
await check('用户停止：signal 中断在途观察，不写指令/不改 goal，也不重试 next', async () => {
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '不该出现' }), hold: true })
  const pending = h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  await until(() => h.host.llm.calls.length === 1, '模型调用开始')
  h.controller.abort()
  await assert.rejects(pending)
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goal().revision, 1)
  assert.equal(h.host.goalCalls.complete.length, 0)
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.equal(h.host.goalCalls.block.length, 0)
  assert.equal(observerRecords(h.agent).length, 0)
  assert.equal(errorRecords(h.agent).length, 0, '用户停止不是观察者失败，不写失败记录')
  assert.equal(h.host.calls.terminal, 0, '观察者还没把控制权交给下游')
})

// ══════════════════ 9. 取消 / 卸载 ══════════════════
await check('卸载：中止在途观察、disarm 带前缀的 active goal、之后不再调用模型', async () => {
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '不该出现' }), hold: true })
  const pending = h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  await until(() => h.host.llm.calls.length === 1, '模型调用开始')
  h.host.dispose()
  const decision = await pending

  assert.equal(decision.kind, 'reject')
  assert.equal(h.host.goal().activation, 'disarmed')
  assert.equal(h.host.goal().phase, 'active', 'disarm 不改持久 phase')
  assert.equal(h.host.goalCalls.disarm.length, 1)
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.equal(h.host.goalCalls.block.length, 0)
  assert.equal(h.host.goalCalls.complete.length, 0)
  assert.equal(observerRecords(h.agent).length, 0)

  const before = h.host.llm.calls.length
  const after = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(after.kind, 'enter')
  assert.equal(h.host.llm.calls.length, before, '卸载后不得再调用模型')
  assert.equal(observerRecords(h.agent).length, 0)
})

await check('卸载：无前缀的 active goal 不被 disarm', async () => {
  const h = setup({ goal: { objective: '普通目标，不归观察者管' } })
  await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  h.host.dispose()
  assert.equal(h.host.goalCalls.disarm.length, 0)
})

await check('⭐ 加载时已存在的 agent：没进过 pre-step 就卸载，也要 disarm 前缀 active goal', async () => {
  const h = setup()
  // setup 里 agent 先于 apply 创建；apply 必须用 agents.list() 把它纳入 tracking。
  h.host.dispose()
  assert.equal(h.host.goalCalls.disarm.length, 1)
  assert.equal(h.host.goal().activation, 'disarmed')
})

await check('⭐ 只排了 goal 轮、没进 pre-step 就卸载：也要 disarm 前缀 active goal', async () => {
  const host = createSimHost()
  activeHosts.push(host)
  apply(host.ctx, { enabled: true })
  const agent = host.createAgent()
  const goalMessage = host.goalRoundMessage({ round: 2 })
  await host.emit('agent/inbox/inserted', { agent, message: goalMessage })
  // 刻意不 dispatch pre-step
  host.dispose()
  assert.equal(host.goalCalls.disarm.length, 1)
  assert.equal(host.goal().activation, 'disarmed')
})

await check('卸载时读 goal 失败：明确诊断，不宣称已 disarm', async () => {
  const h = setup({ goalsGetError: new Error('goal projection failed') })
  h.host.dispose()
  assert.equal(h.host.goalCalls.disarm.length, 0)
  assert.match(h.host.logText(), /未宣称已 disarm/)
  assert.match(h.host.logText(), /未能确认 disarm/)
})

// ══════════════════ 10. 不重复 next / 下游抛错 ══════════════════
await check('下游抛错：不重试 next，原样上抛，goal 状态不变', async () => {
  const h = setup()
  h.host.ctx.on('agent/pre-step', async () => {
    throw new Error('下游爆炸')
  })
  h.host.llm.push({ text: answer({ action: 'continue', instruction: 'x' }) })
  await assert.rejects(
    h.host.dispatchPreStep({
      agent: h.agent,
      messages: [h.host.goalRoundMessage({ round: 2 })],
      signal: h.controller.signal,
    }),
    /下游爆炸/,
  )
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.equal(h.host.goalCalls.block.length, 0)
  assert.equal(h.host.calls.terminal, 0)
  assert.equal(h.driver.calls, 1, 'driver 只应被调用一次')
  assert.ok(errorRecords(h.agent).length >= 1, '应留下失败记录给真人看')
})

await check('退出码：所有场景结束（占位，确保上面 await 全部完成）', () => {
  assert.ok(true)
})

// ══════════════════ 11. 通知：显式开启 + 节流 ══════════════════
await check('通知开启：argv = 固定前缀 + 单一 reason；detached/ignore/shell:false/unref；无会话身份', async () => {
  const spawn = createSpawnSpy()
  __setObserverSpawn(spawn.spy)
  __setObserverClock(() => SIM_NOW)
  const h = setup({
    config: { notification: { enabled: true, command: ['fake-notifier', '--kind', 'observer'], throttleMs: 300000 } },
  })
  h.host.llm.push({ text: answer({ action: 'pause', evidence: '需要人决定：付款' }) })
  await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })

  assert.equal(spawn.calls.length, 1)
  const call = spawn.calls[0]
  assert.equal(call.file, 'fake-notifier')
  assert.deepEqual(call.argv, ['--kind', 'observer', '需要人决定：付款'])
  assert.equal(call.options.shell, false)
  assert.equal(call.options.detached, true)
  assert.equal(call.options.stdio, 'ignore')
  assert.equal(call.options.windowsHide, true)
  assert.equal(spawn.children[0].unrefCalled, true)
  assert.ok(!call.argv.some((item) => /agent-1|goal-1|聊天 ID|话题/i.test(item)), 'argv 不得带会话身份/聊天 ID/话题')
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.equal(h.host.goal().phase, 'active')
})

await check('通知节流：同一 reason 只发一次；不同 reason 至少隔 throttleMs', async () => {
  const spawn = createSpawnSpy()
  __setObserverSpawn(spawn.spy)
  let now = SIM_NOW
  __setObserverClock(() => now)
  const h = setup({
    config: { notification: { enabled: true, command: ['fake-notifier'], throttleMs: 300000 } },
  })

  async function pauseRound(round, reason) {
    h.host.setGoal({ roundsStarted: round - 1 })
    h.host.llm.push({ text: answer({ action: 'pause', evidence: reason }) })
    await h.host.dispatchPreStep({
      agent: h.agent,
      messages: [h.host.goalRoundMessage({ round })],
      signal: h.controller.signal,
    })
  }

  await pauseRound(2, '需要人决定：付款')
  assert.equal(spawn.calls.length, 1)

  now += 1000
  await pauseRound(3, '需要人决定：付款')
  assert.equal(spawn.calls.length, 1, '同一 reason 永远只发一次')

  now += 1000
  await pauseRound(4, '需要人决定：注册账号')
  assert.equal(spawn.calls.length, 1, '不同 reason 在 5 分钟内也节流')

  now += 300000
  await pauseRound(5, '需要人决定：注册账号')
  assert.equal(spawn.calls.length, 2, '超过窗口后允许新 reason')

  now += 300000
  await pauseRound(6, '需要人决定：付款')
  assert.equal(spawn.calls.length, 2, '旧 reason 仍按 reason 去重')

  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.equal(h.host.goal().phase, 'active')
})

await check('通知失败：spawn 抛错只记 warn，不影响本轮', async () => {
  __setObserverSpawn(() => {
    throw new Error('ENOENT: notifier 不存在')
  })
  const h = setup({ config: { notification: { enabled: true, command: ['missing-notifier'] } } })
  h.host.llm.push({ text: answer({ action: 'pause', evidence: '需要人决定：重启' }) })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.match(h.host.logText(), /ENOENT/)
})

await check('通知子进程 error 事件只记 warn，不影响本轮', async () => {
  const spawn = createSpawnSpy()
  __setObserverSpawn(spawn.spy)
  const h = setup({ config: { notification: { enabled: true, command: ['fake-notifier'] } } })
  h.host.llm.push({ text: answer({ action: 'pause', evidence: '需要人决定：验证码' }) })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  spawn.children[0].emitError(new Error('permission denied'))
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.goal().phase, 'active')
  assert.match(h.host.logText(), /permission denied/)
})

// ══════════════════ 12. 解析重试 / 完成需证据 ══════════════════
await check('解析重试：max-tokens 截断后按反馈重问一次，成功则用第二次结果', async () => {
  const h = setup()
  h.host.llm.push({ text: '{"action":"continue"', finish: { kind: 'max-tokens' } })
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '第二步：跑验收' }), finish: { kind: 'stop' } })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.llm.calls.length, 2)
  assert.match(h.host.llm.calls[1].userText, /上一次回答/)
  assert.match(h.host.llm.calls[1].userText, /maxTokens/)
  const observer = decision.messages.at(-1)
  assert.match(observer.content[0].text, /第二步：跑验收/)
})

await check('解析重试：非 JSON 后重问，第二次 complete 成功', async () => {
  const h = setup()
  h.host.llm.push({ text: 'not json at all' })
  h.host.llm.push({ text: answer({ action: 'complete', evidence: '验收记录齐全' }) })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'reject')
  assert.equal(h.host.goal().phase, 'complete')
  assert.equal(h.host.llm.calls.length, 2)
})

await check('完成需证据：两次都缺证据 ⇒ fail-open，不 complete', async () => {
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'complete', evidence: '' }) })
  h.host.llm.push({ text: answer({ action: 'complete' }) })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goalCalls.complete.length, 0)
  assert.equal(h.host.llm.calls.length, 2)
  assert.match(errorRecords(h.agent).at(-1).data.content[0].text, /观察者调用失败/)
})

await check('宿主拒绝 complete（过时/并发暂停）⇒ fail-open，且不写误导性的完成记录', async () => {
  const h = setup({ completeError: new Error('GOAL_STALE_REVISION') })
  h.host.llm.push({ text: answer({ action: 'complete', evidence: '看起来齐了' }) })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goalCalls.complete.length, 1)
  assert.equal(observerRecords(h.agent).length, 0, 'complete 没被接受就不能写完成记录')
  assert.match(h.host.logText(), /complete 被宿主拒绝/)
})

// ══════════════════ 13. 真人输入优先 / 已有观察者消息不重复 ══════════════════
await check('同批次真人输入：complete 不吞掉真人内容，改为放行', async () => {
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'complete', evidence: '看起来齐了' }) })
  const messages = [h.host.goalRoundMessage({ round: 2 }), h.host.userMessage('真人：先别完成')]
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })
  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 2)
  assert.equal(h.host.goal().phase, 'active')
  assert.equal(h.host.goalCalls.complete.length, 0)
  assert.equal(h.host.calls.terminal, 1)
})

await check('同批次真人输入 + continue：保留真人消息，观察者指令追加在后面', async () => {
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '先回答真人问题' }) })
  const messages = [h.host.goalRoundMessage({ round: 2 }), h.host.userMessage('真人：这个指标怎么算？')]
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })
  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 3)
  assert.equal(decision.messages[1], messages[1])
  assert.equal(decision.messages[2].source.kind, 'goal-observer')
})

await check('已有同 key 观察者消息：不重复注入', async () => {
  const h = setup()
  const existing = {
    id: 'm-observer-existing',
    role: 'user',
    source: {
      kind: 'goal-observer',
      goalId: 'goal-1',
      revision: 1,
      round: 2,
      provider: 'p',
      model: 'm',
      decision: { action: 'continue', instruction: '旧指令', evidence: '' },
    },
    content: [{ type: 'text', text: '旧指令' }],
  }
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '新指令' }) })
  const messages = [h.host.goalRoundMessage({ round: 2 }), existing]
  const decision = await h.host.dispatchPreStep({ agent: h.agent, messages, signal: h.controller.signal })
  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.filter((message) => message.source.kind === 'goal-observer').length, 1)
  assert.equal(decision.messages.at(-1), existing)
})

await check('观察期间真人插话（inbox 事件）使本轮缓存失效，不注入旧判断', async () => {
  const h = setup()
  h.host.llm.push({
    text: answer({ action: 'continue', instruction: '旧判断' }),
    before: () => h.host.emit('agent/inbox/inserted', { agent: h.agent, message: h.host.userMessage('插话') }),
  })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(decision.messages.length, 1, 'generation 变了就不注入本轮判断')
  assert.equal(observerRecords(h.agent).length, 0)
})

// ══════════════════ 14. 缓存 / 普通 goal / 关闭 / 配置错误 ══════════════════
await check('同 key 同 generation：第二次复用缓存，不重复调用模型', async () => {
  const h = setup()
  h.host.llm.push({ text: answer({ action: 'continue', instruction: '缓存指令' }) })
  const first = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  const second = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(first.kind, 'enter')
  assert.equal(second.kind, 'enter')
  assert.equal(h.host.llm.calls.length, 1)
  assert.match(second.messages.at(-1).content[0].text, /缓存指令/)
})

await check('普通 goal（无前缀）不调用观察者', async () => {
  const h = setup({ goal: { objective: '普通目标，不归观察者管' } })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.llm.calls.length, 0)
  assert.equal(observerRecords(h.agent).length, 0)
})

await check('enabled 缺省/false：不注册监听器，不调用模型', async () => {
  const host = createSimHost()
  host.createAgent()
  activeHosts.push(host)
  apply(host.ctx, {})
  assert.equal((host.ctx.agents.list().length), 1)
  host.llm.push({ text: answer({ action: 'continue', instruction: '不该用' }) })
  const decision = await host.dispatchPreStep({
    agent: host.ctx.agents.list()[0],
    messages: [host.goalRoundMessage({ round: 2 })],
    signal: new AbortController().signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(host.llm.calls.length, 0)
})

await check('配置错误在装载期暴露：provider/model 只给一个、通知开启但无 command', () => {
  const host = createSimHost()
  activeHosts.push(host)
  assert.throws(() => apply(host.ctx, { enabled: true, provider: 'p' }), /provider 与 model/)
  assert.throws(() => apply(host.ctx, { enabled: true, notification: { enabled: true } }), /notification\.command/)
})

await check('宿主读不到 goal 状态：fail-open，本轮照常跑', async () => {
  const h = setup({ goalsGetError: new Error('goal projection failed') })
  const decision = await h.host.dispatchPreStep({
    agent: h.agent,
    messages: [h.host.goalRoundMessage({ round: 2 })],
    signal: h.controller.signal,
  })
  assert.equal(decision.kind, 'enter')
  assert.equal(h.host.llm.calls.length, 0)
  assert.equal(h.host.goalCalls.pause.length, 0)
  assert.match(h.host.logText(), /读取 goal 失败/)
})

// ══════════════════ 15. 源码级边界 ══════════════════
await check('源码不含 ctx.goals.pause/block 调用、私有路径/聊天 ID/飞书常量', () => {
  // 注释里会写「已移除 聊天 ID/飞书」，所以先剥掉块注释再只扫代码行，避免把说明文字当成实现。
  const codeOnly = (path) => readFileSync(new URL(path, import.meta.url), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n')
    .filter((line) => !line.trimStart().startsWith('//'))
    .join('\n')
  const source = codeOnly('./index.mjs')
  assert.doesNotMatch(source, /ctx\.goals\.(pause|block)\s*\(/)
  assert.doesNotMatch(source, /HIL_ALLOW_SEND|聊天 ID|聊天 ID|FEISHU|feishu|lark/i)
  assert.doesNotMatch(source, /[A-Za-z]:\\\\Users|C:\/Users|C:\/Python|Program Files/i)
  const core = codeOnly('./observer-core.mjs')
  assert.doesNotMatch(core, /HIL_ALLOW_SEND|聊天 ID|聊天 ID|FEISHU|feishu|lark/i)
})

// ── 结果 ────────────────────────────────────────────────────────────────
if (failures.length > 0) {
  console.error(`FAIL: ${failures.length} 条未通过（通过 ${passed}）`)
  for (const failure of failures) console.error(`  ✗ ${failure}`)
  process.exit(1)
}
console.log(`PASS: ${passed} 条接线自检全通过（continue · complete · pause 拦截 · 失败继续 · 过时/用户暂停 · 取消/卸载 · 不重复 next · 通知关闭/节流 · 解析重试 · 完成证据）`)
