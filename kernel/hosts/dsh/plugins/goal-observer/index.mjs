// GENERATED-BY: portable synchronization (DSH agent, 2026-11-13 +08:00)
// LAST-EDIT: portable synchronization (DSH agent, 2026-11-13 +08:00)
// PURPOSE: DSH Host 插件 —— 从原生 goal 的后续自动轮介入，直接调用模型生成本轮具体指令；只追加独立来源消息，
//          不改 goal 轮正文、不改消息原件、不暂停/不 block、不冒充用户授权。
// ORIGIN: 部署模块 dsh-goal-observer-v013/index.mjs（2026-11-13 读码提取；飞书发送器、私有路径、聊天 ID、话题、
//          Python 解释器探测、ctx.get('notifier') 通道全部移除）
// CUMULATIVE-DELTA: 首版。相对部署模块：
//   ① 通知完全参数化且默认关闭（command argv + 单一 reason argv；shell:false；不 await；失败只 warn）；
//   ② 解析改为候选逐个尝试（见 observer-core.mjs）；
//   ③ complete 先让宿主落库成功、再写「观察者判定」记录；宿主拒绝时 fail-open 且不写误导性的完成消息；
//   ④ inspect() 三态：读不到状态归 unknown，保持已消费 decision；只有确定 revision/phase/activation 变化才 reject；
//   ⑤ 卸载时中止在途观察并 disarm 带前缀的 active goal，避免留下无人接管的自动轮。
//
// 接线契约（升级宿主后要复核）：
//   · 原生 goal 状态与额度由 @deepseek-ai/dsh-goal 管；自动轮消息由 @deepseek-ai/dsh-goal-round-driver 生成，
//     其 invariant 要求 goal 轮正文与包内 prompt 完全一致 ⇒ 本插件**绝不修改** goal 消息，只追加 source.kind='goal-observer'。
//   · `agent/pre-step` 是瀑布：本插件默认注册（不 prepend）。无论排在 driver 前还是后，都先 await next() 再决定。
//   · 观察侧没有暂停权（部署方裁定）：观察者返回 pause、插件自身任何失败，一律 fail-open（本轮没有观察者指令照常跑）。
//     ⛔ 不调用 ctx.goals.pause / ctx.goals.block；这两个名字在本文件里只出现在注释和自检断言中。
//   · fail-open 只能 return next()；⛔ 不能 return {kind:'reject'}——driver 收到 reject 且 goal 仍是同一 active revision 时
//     会用 prompt-rejected 把 goal block 掉（比 paused 更难恢复）。只有在「goal 已不是同一 active revision」或
//     「完成已被宿主接受」时才返回 reject。

import { randomUUID } from 'node:crypto'
import { spawn } from 'node:child_process'
import {
  DEFAULT_POLICY,
  OBSERVER_ERROR_SOURCE,
  OBSERVER_SOURCE,
  buildObserverInput,
  goalKey,
  normalizeSettings,
  notificationArgv,
  parseDecision,
  retryFeedback,
  sanitizeReason,
} from './observer-core.mjs'

export const name = 'goal-observer'
export const inject = ['agents', 'goals', 'llm']
export const PLUGIN_VERSION = '0.2.0'

// 测试用注入点：替换 spawn 和时钟，避免任何真实发送、避免真等 5 分钟。
// 生产路径永远是 node:child_process.spawn + Date.now。
let spawnImpl = spawn
let clock = () => Date.now()
export function __setObserverSpawn(fn) {
  spawnImpl = typeof fn === 'function' ? fn : spawn
}
export function __setObserverClock(fn) {
  clock = typeof fn === 'function' ? fn : () => Date.now()
}

export { normalizeSettings, parseDecision }

function reasonOf(error) {
  return error instanceof Error ? error.message : String(error)
}

function makeLogger(ctx) {
  return {
    info(message) {
      try {
        ctx.logger?.info?.(message)
      } catch {
        /* 日志失败不影响本轮 */
      }
    },
    warn(message) {
      try {
        ctx.logger?.warn?.(message)
      } catch {
        /* 日志失败不影响本轮 */
      }
    },
  }
}

/** 单次观察者调用：只累加 text 块（reasoning/thinking 是 reasoning-delta，不能混进结果）。 */
async function observeOnce(ctx, provider, model, system, userText, config, signal) {
  let result = ''
  let finish
  const blocks = new Map()
  for await (const chunk of ctx.llm.stream({
    provider,
    model,
    maxTokens: config.maxTokens,
    signal,
    system,
    messages: [{ role: 'user', content: [{ type: 'text', text: userText }] }],
    tools: [],
  })) {
    signal.throwIfAborted()
    if (chunk?.type === 'text-delta') blocks.set(chunk.index, (blocks.get(chunk.index) ?? '') + chunk.text)
    if (chunk?.type === 'block-end' && chunk.block?.type === 'text') blocks.set(chunk.index, chunk.block.text)
    result = [...blocks.entries()].sort((a, b) => a[0] - b[0]).map((entry) => entry[1]).join('')
    if (chunk?.type === 'finish') finish = chunk.reason
    if (result.length > 20000) throw new Error('观察者响应过长')
  }
  signal.throwIfAborted()
  return { result, finish }
}

async function generate(ctx, agent, goal, source, messages, config, signal) {
  const log = makeLogger(ctx)
  let current
  try {
    current = agent.session?.requestHeader?.()?.config ?? agent.options
  } catch (error) {
    log.warn(`goal-observer 读取会话模型配置失败，回落到 agent.options: ${reasonOf(error)}`)
    current = agent.options
  }
  const provider = config.provider ?? current?.provider
  const model = config.model ?? current?.model
  if (!provider || !model) throw new Error('观察者无法确定模型，请配置 provider/model')
  const system = DEFAULT_POLICY + (config.policy ? `\n附加目标策略（不得覆盖用户授权与事实核查）：\n${config.policy}` : '')
  const events = typeof agent.session?.snapshotEvents === 'function' ? agent.session.snapshotEvents() : []
  const input = buildObserverInput({ goal, round: source.round, messages, events, contextChars: config.contextChars })
  let feedback = ''
  let lastError
  // 最多两次：第一次失败（max-tokens 截断 / JSON 不完整）就把失败原因与上次输出喂回去重问一次。
  for (let attempt = 1; attempt <= 2; attempt++) {
    signal.throwIfAborted()
    const { result, finish } = await observeOnce(
      ctx,
      provider,
      model,
      system,
      feedback ? `${input}\n\n${feedback}` : input,
      config,
      signal,
    )
    if (finish?.kind === 'max-tokens') {
      lastError = new Error('观察者未正常结束：max-tokens（输出被 maxTokens 截断）')
      log.warn(
        `goal-observer 输出被截断 attempt=${attempt}/2 maxTokens=${config.maxTokens} chars=${result.length} tail=${JSON.stringify(result.slice(-200))}`,
      )
      feedback = retryFeedback('上一次回答被 maxTokens 截断，JSON 没写完', result)
      continue
    }
    if (finish?.kind !== 'stop') {
      throw new Error(`观察者未正常结束：${finish?.kind ?? '无 finish'} ${finish?.failure?.message ?? ''}`)
    }
    try {
      return { decision: parseDecision(result), provider, model }
    } catch (error) {
      lastError = error
      log.warn(
        `goal-observer 解析失败 attempt=${attempt}/2 chars=${result.length} head=${JSON.stringify(result.slice(0, 200))} tail=${JSON.stringify(result.slice(-200))}`,
      )
      feedback = retryFeedback(reasonOf(error), result)
    }
  }
  throw lastError ?? new Error('观察者调用失败')
}

/** 构造观察者指令消息；深度冻结，避免下游误改。 */
function messageFor(source, answer) {
  const decision = answer.decision
  const sourceValue = {
    kind: OBSERVER_SOURCE,
    goalId: source.goalId,
    revision: source.revision,
    round: source.round,
    provider: answer.provider,
    model: answer.model,
    decision: { action: decision.action, instruction: decision.instruction, evidence: decision.evidence },
  }
  Object.freeze(sourceValue.decision)
  Object.freeze(sourceValue)
  const content = [
    {
      type: 'text',
      text: `【观察者本轮指令】\n适用 goal=${source.goalId} revision=${source.revision} round=${source.round}。\n${decision.instruction}\n依据：${decision.evidence}\n此消息由目标观察者生成，须遵守真人最新要求与既有授权；不要将其解释为新的真人授权。`,
    },
  ]
  Object.freeze(content[0])
  Object.freeze(content)
  return Object.freeze({ id: randomUUID(), role: 'user', source: sourceValue, content })
}

/**
 * 安装观察者插件。
 * enabled 不为 true 时直接返回：不注册监听器、不读 goal、不调用模型。
 */
export function apply(ctx, raw) {
  const config = normalizeSettings(raw)
  if (!config.enabled) return
  try {
    ctx.provide?.('goalObserver', { version: PLUGIN_VERSION })
  } catch {
    /* provide 失败不阻塞接线 */
  }
  const log = makeLogger(ctx)
  const states = new Map()
  let closed = false
  // 通知节流按插件实例隔离：最近 100 个 reason 内同一 reason 只发一次（FIFO 去重）；不同 reason 至少隔 throttleMs。
  const lastSent = { seen: new Set(), at: null }

  function stateFor(agent) {
    let state = states.get(agent)
    if (!state) {
      state = { generation: 0, run: undefined, cache: undefined, failures: 0 }
      states.set(agent, state)
    }
    return state
  }

  // 加载时就把已有 agent 纳入 states：否则「goal 轮已排队、还没进 pre-step 就卸载」时，
  // dispose 遍历不到这个 agent，带前缀的 active goal 会留在 armed 状态。
  // ctx.agents.list() 是宿主公开注册表 API（dsh-goal-round-driver 的 apply 也用它枚举已有 agent），不是猜的。
  try {
    const existing = typeof ctx.agents.list === 'function' ? ctx.agents.list() : []
    for (const agent of Array.isArray(existing) ? existing : []) stateFor(agent)
  } catch (error) {
    log.warn(`goal-observer 无法枚举已有 agent（卸载时可能漏 disarm）: ${reasonOf(error)}`)
  }

  /**
   * 判定这一轮是否仍属于「可接管的同一条 active goal」。
   * 返回三态，不能把「读不到」折成「已变化」：
   *  - active：可继续；
   *  - changed：读到了确定的 revision/phase/activation/round 变化（外层 driver 不会 block 这种情况）；
   *  - unknown：插件已关闭 / signal 中断 / agent 不再 live / 读 goal 抛错 —— 只能 fail-open，保持已消费的
   *    decision 原样返回，⛔ 不能 restore+reject（外层 driver 会把同一 active revision 判成 blocked）。
   */
  function inspect(agent, source, signal) {
    if (closed) return { state: 'unknown', why: 'plugin closed' }
    if (signal?.aborted) return { state: 'unknown', why: 'signal aborted' }
    try {
      if (ctx.agents.get(agent.id) !== agent) return { state: 'unknown', why: 'agent not live' }
      const goal = ctx.goals.get(agent)
      if (!goal) return { state: 'changed', why: 'no current goal' }
      if (goal.id !== source.goalId || goal.revision !== source.revision) {
        return { state: 'changed', why: `goal ref changed id=${goal.id} revision=${goal.revision}` }
      }
      if (goal.phase !== 'active' || goal.activation !== 'armed') {
        return { state: 'changed', why: `goal not active/armed phase=${goal.phase} activation=${goal.activation}` }
      }
      if (goal.roundsStarted + 1 !== source.round) {
        // 轮次前进不一定改 revision/phase；此时若 reject，外层 driver 仍可能把同一 active revision
        // 判 blocked。归到 unknown ⇒ 保持 decision 原样交给 driver 的 reservation 校验。
        return { state: 'unknown', why: `round moved roundsStarted=${goal.roundsStarted} sourceRound=${source.round}` }
      }
      return { state: 'active', goal }
    } catch (error) {
      log.warn(`goal-observer 无法读取 goal 状态（本轮 fail-open）: ${reasonOf(error)}`)
      return { state: 'unknown', why: reasonOf(error), error }
    }
  }

  /** 追加一条给人看的会话记录（不是授权，也不进本轮 decision.messages）。 */
  function appendRecord(agent, source, text) {
    try {
      if (typeof agent?.session?.append !== 'function') return false
      agent.session.append(
        'user/message',
        {
          id: randomUUID(),
          role: 'user',
          source: { kind: OBSERVER_ERROR_SOURCE, goalId: source.goalId, revision: source.revision, round: source.round },
          content: [{ type: 'text', text }],
        },
        { surfaceOp: 'append' },
      )
      return true
    } catch (error) {
      log.warn(`goal-observer 写入提示记录失败 session=${agent?.id ?? '?'}: ${reasonOf(error)}`)
      return false
    }
  }

  /**
   * 叫醒真人：只有 notification.enabled === true 才 spawn。
   * 完全非阻塞：不 await、不读输出、detached + stdio:'ignore' + unref；任何失败只 warn，
   * 绝不升级成 pause/block，也绝不影响这一轮 goal。⛔ 不发任何会话身份/聊天 ID/话题 参数。
   */
  function notifyHuman(agent, source, reason) {
    const notification = config.notification
    if (!notification.enabled) {
      log.info(`goal-observer 通知未启用（notification.enabled=false），本次不发送 session=${agent.id} round=${source.round}`)
      return false
    }
    const text = sanitizeReason(reason) || '（观察者未给出理由）'
    if (lastSent.seen.has(text)) {
      log.warn(`goal-observer 通知节流：这条理由已经通知过，不再重发 session=${agent.id} round=${source.round}`)
      return false
    }
    const now = clock()
    if (lastSent.at !== null && now - lastSent.at < notification.throttleMs) {
      log.warn(`goal-observer 通知节流：距上次发送不足 ${notification.throttleMs}ms，本次跳过 session=${agent.id} round=${source.round}`)
      return false
    }
    const [file, ...fixed] = notification.command
    const argv = notificationArgv(fixed, text)
    let child
    try {
      child = spawnImpl(file, argv, {
        cwd: notification.cwd ?? undefined,
        detached: true,
        stdio: 'ignore',
        windowsHide: true,
        shell: false,
      })
    } catch (error) {
      log.warn(`goal-observer 通知启动失败（不影响本轮继续）: ${reasonOf(error)}`)
      return false
    }
    // ⚠️ 必须挂 error 监听：没有监听时 spawn 失败（ENOENT 等）会抛 'error' 事件把整个进程带下去。
    if (child && typeof child.on === 'function') {
      child.on('error', (error) => log.warn(`goal-observer 通知执行失败（不影响本轮继续）: ${reasonOf(error)}`))
    }
    if (child && typeof child.unref === 'function') child.unref()
    // 最多记住 100 条理由，避免长期运行无限增长。
    if (lastSent.seen.size >= 100) lastSent.seen.delete(lastSent.seen.values().next().value)
    lastSent.seen.add(text)
    lastSent.at = now
    log.info(`goal-observer 已异步通知真人 session=${agent.id} round=${source.round}`)
    return true
  }

  /** 把下游已认领、但本轮被我们放弃的消息放回 next-step，避免真人输入丢失。 */
  function restoreClaimed(agent, messages) {
    if (!Array.isArray(messages) || typeof agent?.inbox?.prepend !== 'function') return
    for (const message of messages.toReversed()) {
      if (!message || typeof message.id !== 'string') continue
      const kind = message.source?.kind
      if (kind === 'goal' || kind === 'runtime-context' || kind === OBSERVER_SOURCE || kind === OBSERVER_ERROR_SOURCE) continue
      if (agent.inbox.nextStep.some((candidate) => candidate.id === message.id)
        || agent.inbox.nextTurn.some((candidate) => candidate.id === message.id)) continue
      agent.inbox.prepend('next-step', message)
    }
  }

  /** 写一条「完成」记录：只有宿主接受了 complete 才调用。 */
  function appendCompleted(agent, source, answer) {
    const record = {
      ...answer,
      decision: {
        ...answer.decision,
        // 只写动作；evidence 由 messageFor 的「依据：」写一次，避免同一段证据出现两遍。
        instruction: `观察者判定：${answer.decision.action}。`,
      },
    }
    try {
      if (typeof agent?.session?.append !== 'function') return false
      agent.session.append('user/message', messageFor(source, record), { surfaceOp: 'append' })
      return true
    } catch (error) {
      log.warn(`goal-observer 写入完成记录失败 session=${agent?.id ?? '?'}: ${reasonOf(error)}`)
      return false
    }
  }

  ctx.on('agent/created', ({ agent }) => {
    if (agent) stateFor(agent)
  })
  ctx.on('agent/inbox/inserted', ({ agent, message }) => {
    // 即使是 goal 轮也要纳入 states：只排队、还没进 pre-step 就卸载时，dispose 仍要能看到这个 agent。
    if (!agent) return
    const state = stateFor(agent)
    if (message?.source?.kind !== 'goal') state.generation++
  })
  ctx.on('agent/disposed', ({ agent }) => {
    states.get(agent)?.run?.abort()
    states.delete(agent)
  })
  ctx.on('goal/changed', ({ agent }) => {
    if (!agent) return
    // goal 变化本身也要纳入 tracking，避免「创建了 goal 但没排过 goal 轮」的 agent 漏在卸载 disarm 之外。
    const state = stateFor(agent)
    state.generation++
    state.cache = undefined
    state.run?.abort()
  })

  // 原 goal 消息不变。先等观察者，再调用 next：任意注册顺序下，driver 的最终校验都在等待之后。
  ctx.on('agent/pre-step', async ({ agent, messages, signal }, next) => {
    const list = Array.isArray(messages) ? messages : []
    const submitted = list.find((message) => message?.source?.kind === 'goal' && message.source.round > 0)
    if (!submitted) return next()

    let goal
    try {
      goal = ctx.goals.get(agent)
    } catch (error) {
      log.warn(`goal-observer 读取 goal 失败，本轮不介入: ${reasonOf(error)}`)
      return next()
    }
    if (!goal || typeof goal.objective !== 'string' || !goal.objective.startsWith(config.objectivePrefix)) return next()
    const preflight = inspect(agent, submitted.source, signal)
    if (preflight.state !== 'active') return next()

    const state = stateFor(agent)
    const generation = state.generation
    const source = submitted.source
    const key = goalKey(goal, source.round)
    const controller = new AbortController()
    state.run = controller
    const timeout = AbortSignal.timeout(config.timeoutMs)
    // AbortSignal.any 从 Node 20.3 才有；package.json 的 engines 因此是 >=20.3，20.0/20.1/20.2 不可用。
    const fused = AbortSignal.any([signal, controller.signal, timeout])
    let entered = false
    let decision

    try {
      let answer
      if (state.cache?.key === key && state.cache.generation === generation) {
        answer = state.cache.answer
      } else {
        answer = await generate(ctx, agent, goal, source, list, config, fused)
        state.cache = { key, generation, answer }
      }
      state.failures = 0
      fused.throwIfAborted()

      // 观察侧没有暂停权：观察者返回 pause ⇒ 不发指令、不改 goal 状态，只把理由报给人，本轮照常跑。
      if (answer.decision.action === 'pause') {
        const why = answer.decision.evidence || '（观察者未给出理由）'
        log.warn(`goal-observer 观察者想 pause，已按部署方规定拦下（观察侧无权暂停 goal，继续本轮）session=${agent.id} round=${source.round} 它原本的理由=${why}`)
        notifyHuman(agent, source, why)
        appendRecord(
          agent,
          source,
          `【观察者原本想暂停，已被拦下】按部署方规定观察侧无权暂停 goal，本轮照常继续。观察者给出的理由：${why}\n（这条是给真人看的提示，不是新的授权，也不改变本轮工作内容；要你拍板的事请直接回复。）`,
        )
        entered = true // 控制权已交给下游，catch 里不能再调一次 next()
        return next()
      }

      // 此后不再等待 I/O；下游 driver 因插话/改目标拒绝时保持其原有恢复输入逻辑。
      entered = true
      decision = await next()
      if (decision?.kind !== 'enter') return decision
      const verdict = inspect(agent, source, signal)
      if (verdict.state === 'changed') {
        restoreClaimed(agent, decision.messages)
        return { kind: 'reject' }
      }
      if (verdict.state === 'unknown') {
        // 读不到 / 已关闭 / signal 中断 / agent 不再 live：不能当成「已变化」去 reject，
        // 否则外层 driver 会把同一 active revision 判成 blocked。保持已消费的 decision 原样返回。
        log.warn(`goal-observer 无法确认 goal 状态，保持本轮 decision（fail-open）session=${agent.id} round=${source.round}: ${verdict.why}`)
        state.cache = undefined
        return decision
      }
      if (!Array.isArray(decision.messages) || !decision.messages.some((message) => message.id === submitted.id)) return decision
      if (state.generation !== generation) {
        state.cache = undefined
        return decision
      }
      if (decision.messages.some((message) => message.source?.kind === OBSERVER_SOURCE
        && message.source.goalId === goal.id
        && message.source.revision === goal.revision
        && message.source.round === source.round)) {
        return decision
      }
      // 同批次仍有新输入时先交给主 agent，避免停止判定吞掉真人内容。
      const pending = decision.messages.some((message) => !['goal', 'runtime-context', OBSERVER_SOURCE].includes(message.source?.kind))
      if (answer.decision.action !== 'continue' && pending) return decision
      if (answer.decision.action === 'continue') {
        log.info(`goal-observer instruction session=${agent.id} round=${source.round}`)
        return { ...decision, messages: [...decision.messages, messageFor(source, answer)] }
      }
      // 走到这里只可能是 complete（pause 已在上面被拦下并 fail-open）。
      if (answer.decision.action !== 'complete') {
        log.warn(`goal-observer 未知停止动作已忽略 session=${agent.id} round=${source.round} action=${answer.decision.action}`)
        return decision
      }
      // 完成需证据：解析层已保证非空；这里再兜一次，防以后有人改宽解析。
      if (!answer.decision.evidence) {
        log.warn(`goal-observer complete 缺证据，已忽略 session=${agent.id} round=${source.round}`)
        return decision
      }
      // 先让宿主落库成功，再写「观察者判定」记录：宿主若因过时 revision / 用户暂停拒绝，
      // 绝不留下一条看起来「已完成」的消息。
      let completed
      try {
        completed = ctx.goals.complete(agent, { id: goal.id, revision: goal.revision })
      } catch (error) {
        log.warn(`goal-observer complete 被宿主拒绝（本轮 fail-open，不写完成消息）session=${agent.id} round=${source.round}: ${reasonOf(error)}`)
        return decision
      }
      if (!completed || completed.phase !== 'complete') {
        log.warn(`goal-observer complete 未生效（phase=${completed?.phase ?? '?'}），不写完成消息 session=${agent.id} round=${source.round}`)
        return decision
      }
      appendCompleted(agent, source, answer)
      restoreClaimed(agent, decision.messages)
      return { kind: 'reject' }
    } catch (error) {
      if (signal?.aborted) throw error
      const reason = `观察者调用失败：${reasonOf(error)}`
      let stillMine = false
      try {
        stillMine = !closed && ctx.agents.get(agent.id) === agent
      } catch {
        stillMine = false
      }
      const verdict = inspect(agent, source, signal)
      if (!closed && verdict.state === 'active') {
        state.failures = (state.failures ?? 0) + 1
        if (stillMine) appendRecord(agent, source, `${reason}（本轮没有观察者指令，照常执行；此记录不是新的授权。）`)
        log.warn(`goal-observer failed session=${agent.id} round=${source.round} 连续失败=${state.failures}（按部署方规定不因失败暂停 goal）: ${reasonOf(error)}`)
        // 任何失败都 fail-open：这一轮没有观察者指令照常跑，只记 warn。
        // ⛔ 不能 return {kind:'reject'}：goal 仍是同一 active revision 时 driver 会 block 掉它。
        if (!entered) return next()
        if (decision) return decision
        throw error
      }
      log.warn(`goal-observer failed session=${agent.id}；状态不可判定/已变化（state=${verdict.state}: ${verdict.why}）: ${reasonOf(error)}`)
      if (verdict.state === 'changed') {
        // 确定的 revision/phase/activation 变化：driver 不会 block；把已认领的真人消息放回后拒绝本轮。
        if (decision) {
          restoreClaimed(agent, decision.messages)
          return { kind: 'reject' }
        }
        if (!entered) return next()
        throw error
      }
      // 读不到状态时既不能 restore 再 next（会把同一批真人消息投递两次），也不能 reject。
      // next 已经调用过就只返回已有 decision；没调用过就交给 next 一次。
      if (decision) return decision
      if (!entered) return next()
      throw error
    } finally {
      if (state.run === controller) state.run = undefined
    }
  })

  // 卸载：中止在途观察，并 disarm 带前缀的 active goal，避免留下无人接管的自动轮。
  const dispose = () => {
    closed = true
    let unresolved = 0
    for (const [agent, state] of states) {
      state.run?.abort()
      try {
        const goal = ctx.goals.get(agent)
        if (!goal || goal.phase !== 'active' || typeof goal.objective !== 'string' || !goal.objective.startsWith(config.objectivePrefix)) continue
        const view = ctx.goals.disarm(agent)
        if (view?.activation !== 'disarmed') {
          unresolved++
          log.warn(`goal-observer 卸载时 disarm 未确认 session=${agent?.id ?? '?'} activation=${view?.activation ?? '?'}`)
        }
      } catch (error) {
        unresolved++
        // 读不到就不宣称已经 disarm：明确留下失败诊断，由人复核。
        log.warn(`goal-observer 卸载时无法读取/确认 goal（未宣称已 disarm）session=${agent?.id ?? '?'}: ${reasonOf(error)}`)
      }
    }
    states.clear()
    if (unresolved > 0) log.warn(`goal-observer 卸载：${unresolved} 个 agent 未能确认 disarm，需要人工复核`)
  }
  if (typeof ctx.effect === 'function') ctx.effect(() => dispose)
}
