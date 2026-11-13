// GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
// LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
// PURPOSE: 观察者插件的离线「模拟宿主」—— 复现本插件依赖的那部分 DSH 契约：
//          cordis 事件/effect、agent 注册表、goal 服务（CAS revision、pause/complete/block/disarm）、
//          llm.stream 的 chunk 协议、agent/pre-step 瀑布、收件箱、会话事件。
// ORIGIN: 读 @deepseek-ai/dsh-goal / @deepseek-ai/dsh-goal-round-driver 的源码契约（2026-11-13），
//         只实现观察者会碰到的那几个方法；⛔ 不是宿主替代品，也不模拟模型/网络。
// CUMULATIVE-DELTA: 首版。刻意保留的宿主不变量：
//   · goal 变更走 CAS（ref 对不上就抛 stale），complete/pause/block 只允许特定 phase；
//   · driver 的 pre-step 先校验 reservation、再 await next()、最后再校验一次；reject 且 goal 仍是
//     同一 active revision 时会 block（prompt-rejected）——正是本插件不能随便 reject 的原因；
//   · agent/pre-step 是瀑布：先注册的先跑（outermost-first），与 cordis 一致。

export const SIM_NOW = 1_800_000_000_000

export const DEFAULT_GOAL = Object.freeze({
  id: 'goal-1',
  revision: 1,
  objective: '[观察者] 模拟目标：分三步完成验收',
  phase: 'active',
  maxGoalRounds: 8,
  roundsStarted: 1,
  activation: 'armed',
})

export function createDeferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** 假 spawn：记录 argv/options，返回可 unref、可触发 error 的子进程替身。 */
export function createSpawnSpy() {
  const calls = []
  const children = []
  const spy = (file, argv, options) => {
    const child = {
      file,
      argv: [...(argv ?? [])],
      options,
      unrefCalled: false,
      listeners: new Map(),
      on(event, handler) {
        this.listeners.set(event, handler)
        return this
      },
      unref() {
        this.unrefCalled = true
      },
      emitError(error) {
        const handler = this.listeners.get('error')
        if (typeof handler === 'function') handler(error)
      },
    }
    calls.push({ file, argv: child.argv, options })
    children.push(child)
    return child
  }
  return { spy, calls, children }
}

function waitForAbort(signal) {
  if (!signal) return Promise.reject(new Error('模拟模型没有收到 AbortSignal'))
  if (signal.aborted) {
    return Promise.reject(signal.reason instanceof Error ? signal.reason : new Error('aborted'))
  }
  return new Promise((_, reject) => {
    signal.addEventListener('abort', () => {
      reject(signal.reason instanceof Error ? signal.reason : new Error('aborted'))
    }, { once: true })
  })
}

/**
 * 建一个模拟宿主。
 * @param {{now?: number, goal?: object, goalsGetError?: Error, completeError?: Error}} options
 */
export function createSimHost(options = {}) {
  const listeners = new Map()
  const disposers = []
  const services = {}
  const provided = {}
  const agents = new Map()
  const logs = { info: [], warn: [], error: [], debug: [] }
  let goal = { ...DEFAULT_GOAL, createdAt: SIM_NOW, updatedAt: SIM_NOW, ...(options.goal ?? {}) }
  const calls = { terminal: 0, user: { pause: 0, edit: 0, resume: 0 } }
  const goalCalls = { get: 0, complete: [], pause: [], block: [], disarm: [] }

  const ctx = {
    logger: {
      info: (...args) => logs.info.push(args.map(String).join(' ')),
      warn: (...args) => logs.warn.push(args.map(String).join(' ')),
      error: (...args) => logs.error.push(args.map(String).join(' ')),
      debug: (...args) => logs.debug.push(args.map(String).join(' ')),
    },
    on(event, handler, opts) {
      const bucket = listeners.get(event) ?? []
      if (opts?.prepend === true) bucket.unshift(handler)
      else bucket.push(handler)
      listeners.set(event, bucket)
      return () => {
        const index = bucket.indexOf(handler)
        if (index >= 0) bucket.splice(index, 1)
      }
    },
    get(service) {
      return services[service]
    },
    provide(service, value) {
      provided[service] = value
    },
    effect(callback) {
      const cleanup = callback()
      if (typeof cleanup === 'function') disposers.push(cleanup)
      return cleanup
    },
    agents: {
      get(id) {
        return agents.get(id)
      },
      list() {
        return [...agents.values()]
      },
      roots() {
        return [...agents.values()]
      },
      currentInitiator() {
        return undefined
      },
    },
    sessions: {},
  }

  function emit(event, payload) {
    const handlers = [...(listeners.get(event) ?? [])]
    return Promise.all(handlers.map((handler) => handler(payload)))
  }

  const goals = {
    calls: goalCalls,
    get(agent) {
      goalCalls.get++
      if (!agents.has(agent?.id)) throw new Error(`agent "${agent?.id}" is not live`)
      if (options.goalsGetError) throw options.goalsGetError
      if (options.goalsGetErrorOnCall !== undefined) {
        const targets = Array.isArray(options.goalsGetErrorOnCall) ? options.goalsGetErrorOnCall : [options.goalsGetErrorOnCall]
        if (targets.includes(goalCalls.get)) throw new Error(`模拟 goal 读取瞬时失败（第 ${goalCalls.get} 次 get）`)
      }
      return { ...goal }
    },
    complete(agent, ref) {
      goalCalls.complete.push({ agent: agent?.id, ref: { ...ref } })
      if (!agents.has(agent?.id)) throw new Error('GOAL_AGENT_NOT_LIVE')
      if (!ref || ref.id !== goal.id || ref.revision !== goal.revision) throw new Error('GOAL_STALE_REVISION')
      if (!['active', 'paused', 'blocked'].includes(goal.phase)) throw new Error('GOAL_INVALID_TRANSITION')
      if (options.completeError) throw options.completeError
      goal = { ...goal, phase: 'complete', revision: goal.revision + 1, activation: 'disarmed', updatedAt: goal.updatedAt + 1 }
      void emit('goal/changed', { agent, change: { operation: 'complete', ref: { id: goal.id, revision: goal.revision }, goal: { ...goal } } })
      return { ...goal }
    },
    pause(agent, ref) {
      goalCalls.pause.push({ agent: agent?.id, ref: { ...ref } })
      if (!ref || ref.id !== goal.id || ref.revision !== goal.revision) throw new Error('GOAL_STALE_REVISION')
      if (goal.phase !== 'active') throw new Error('GOAL_INVALID_TRANSITION')
      goal = { ...goal, phase: 'paused', revision: goal.revision + 1, activation: 'disarmed', updatedAt: goal.updatedAt + 1 }
      void emit('goal/changed', { agent, change: { operation: 'pause', ref: { id: goal.id, revision: goal.revision }, goal: { ...goal } } })
      return { ...goal }
    },
    block(agent, ref, reason) {
      goalCalls.block.push({ agent: agent?.id, ref: { ...ref }, reason })
      if (!ref || ref.id !== goal.id || ref.revision !== goal.revision) throw new Error('GOAL_STALE_REVISION')
      if (goal.phase !== 'active') throw new Error('GOAL_INVALID_TRANSITION')
      goal = {
        ...goal,
        phase: 'blocked',
        revision: goal.revision + 1,
        activation: 'disarmed',
        blockedReason: { ...reason },
        updatedAt: goal.updatedAt + 1,
      }
      void emit('goal/changed', { agent, change: { operation: 'block', ref: { id: goal.id, revision: goal.revision }, goal: { ...goal } } })
      return { ...goal }
    },
    disarm(agent) {
      goalCalls.disarm.push({ agent: agent?.id })
      const changed = goal.activation !== 'disarmed'
      goal = { ...goal, activation: 'disarmed', updatedAt: goal.updatedAt + 1 }
      if (changed) void emit('goal/changed', { agent, change: { operation: 'disarm', ref: { id: goal.id, revision: goal.revision }, goal: { ...goal } } })
      return { ...goal }
    },
  }
  services.goals = goals
  ctx.goals = goals

  function createAgent(agentOptions = {}) {
    const events = agentOptions.events ?? []
    const inbox = {
      nextStep: [],
      nextTurn: [],
      prepend(bucket, message) {
        const list = bucket === 'next-turn' ? inbox.nextTurn : inbox.nextStep
        if (list.some((candidate) => candidate.id === message.id)) return
        list.unshift(message)
      },
    }
    const session = {
      events,
      append(type, data) {
        const event = { type, data, seq: events.length + 1 }
        events.push(event)
        return event
      },
      snapshotEvents() {
        return events
      },
      requestHeader() {
        return { config: { provider: agentOptions.provider ?? 'sim-provider', model: agentOptions.model ?? 'sim-model' } }
      },
    }
    const agent = {
      id: agentOptions.id ?? 'agent-1',
      session,
      inbox,
      options: { provider: agentOptions.provider ?? 'sim-provider', model: agentOptions.model ?? 'sim-model' },
      status: 'running',
    }
    agents.set(agent.id, agent)
    return agent
  }

  const llmCalls = []
  const llmQueue = []
  const llm = {
    calls: llmCalls,
    queue: llmQueue,
    push(item) {
      llmQueue.push(item)
      return llm
    },
    stream(params) {
      const item = llmQueue.shift()
      llmCalls.push({
        provider: params?.provider,
        model: params?.model,
        system: params?.system,
        maxTokens: params?.maxTokens,
        tools: params?.tools,
        userText: params?.messages?.[0]?.content?.[0]?.text,
      })
      if (!item) throw new Error('模拟模型没有排队的回答')
      return (async function* simulate() {
        if (item.error) throw item.error
        if (typeof item.before === 'function') await item.before(params)
        if (Array.isArray(item.chunks)) {
          for (const chunk of item.chunks) yield chunk
        } else if (item.text !== undefined) {
          yield { type: 'text-delta', index: 0, text: item.text }
        }
        if (item.hold === true) await waitForAbort(params?.signal)
        else if (item.wait) await item.wait
        if (typeof item.after === 'function') await item.after(params)
        yield { type: 'finish', reason: item.finish ?? { kind: 'stop' } }
      })()
    },
  }
  services.llm = llm
  ctx.llm = llm

  function goalRoundMessage({ round, agent, text } = {}) {
    const current = goal
    return {
      id: `m-goal-${current.id}-r${round ?? current.roundsStarted + 1}`,
      role: 'user',
      source: { kind: 'goal', goalId: current.id, revision: current.revision, round: round ?? current.roundsStarted + 1 },
      content: [{ type: 'text', text: text ?? `<goal_round>第 ${round ?? current.roundsStarted + 1} 轮</goal_round>` }],
    }
  }

  function userMessage(text, source = { kind: 'user-input' }) {
    return { id: `m-user-${Math.random().toString(36).slice(2, 10)}`, role: 'user', source, content: [{ type: 'text', text }] }
  }

  function dispatchPreStep({ agent, messages, signal }) {
    const hookList = [...(listeners.get('agent/pre-step') ?? [])]
    const payload = { agent, messages, signal }
    let index = -1
    const next = async () => {
      index++
      const hook = hookList[index]
      if (!hook) {
        calls.terminal++
        return { kind: 'enter', messages: [...(payload.messages ?? [])] }
      }
      return hook(payload, next)
    }
    return next()
  }

  /** 模拟真正的 goal-round-driver：reservation 校验 → await next() → 再校验；reject 时按宿主规则 block。 */
  function installDriver() {
    const driver = { calls: 0, rejects: 0, blocks: 0, staleRejects: 0 }
    const hook = async (payload, next) => {
      driver.calls++
      const submitted = (payload.messages ?? []).find((message) => message?.source?.kind === 'goal' && message.source.round > 0)
      if (!submitted) return next()
      const valid = () => goal.phase === 'active'
        && goal.activation === 'armed'
        && goal.id === submitted.source.goalId
        && goal.revision === submitted.source.revision
        && submitted.source.round === goal.roundsStarted + 1
      if (!valid()) {
        driver.rejects++
        return { kind: 'reject' }
      }
      let decision
      try {
        decision = await next()
      } catch (error) {
        driver.rejects++
        throw error
      }
      if (decision?.kind === 'reject') {
        driver.rejects++
        const sameActive = goal.phase === 'active'
          && goal.activation === 'armed'
          && goal.id === submitted.source.goalId
          && goal.revision === submitted.source.revision
        if (sameActive) {
          driver.blocks++
          goals.block(payload.agent, { id: goal.id, revision: goal.revision }, {
            code: 'prompt-rejected',
            message: '模拟 driver：Goal round was rejected before entering its step.',
          })
        }
        return decision
      }
      if (!valid()) {
        driver.staleRejects++
        return { kind: 'reject' }
      }
      return { ...decision, startsRequestSeries: true }
    }
    ctx.on('agent/pre-step', hook)
    return driver
  }

  function setGoal(patch) {
    goal = { ...goal, ...patch, updatedAt: goal.updatedAt + 1 }
    return { ...goal }
  }

  function userPause({ silent = false } = {}) {
    calls.user.pause++
    goal = { ...goal, phase: 'paused', revision: goal.revision + 1, activation: 'disarmed', updatedAt: goal.updatedAt + 1 }
    if (!silent) void emit('goal/changed', { agent: agents.values().next().value, change: { operation: 'pause', ref: { id: goal.id, revision: goal.revision } } })
    return { ...goal }
  }

  function userEdit(objective, { silent = false } = {}) {
    calls.user.edit++
    goal = { ...goal, revision: goal.revision + 1, objective: objective ?? goal.objective, updatedAt: goal.updatedAt + 1 }
    if (!silent) void emit('goal/changed', { agent: agents.values().next().value, change: { operation: 'edit', ref: { id: goal.id, revision: goal.revision } } })
    return { ...goal }
  }

  function userResume() {
    calls.user.resume++
    goal = { ...goal, phase: 'active', revision: goal.revision + 1, activation: 'armed', updatedAt: goal.updatedAt + 1 }
    return { ...goal }
  }

  function dispose() {
    for (const cleanup of disposers.splice(0).reverse()) {
      try {
        cleanup()
      } catch (error) {
        logs.error.push(String(error))
      }
    }
    listeners.clear()
  }

  return {
    ctx,
    agents,
    goals,
    llm,
    logs,
    calls,
    goalCalls,
    provided,
    goal: () => ({ ...goal }),
    setGoal,
    createAgent,
    goalRoundMessage,
    userMessage,
    dispatchPreStep,
    installDriver,
    userPause,
    userEdit,
    userResume,
    emit,
    dispose,
    logText: () => [...logs.info, ...logs.warn, ...logs.error].join('\n'),
  }
}
