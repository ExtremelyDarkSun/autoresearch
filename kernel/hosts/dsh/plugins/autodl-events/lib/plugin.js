// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: autodl-events 插件**入口**（接线层）—— 定时轮询事件队列，把事件用 `agent.steer()`（或 `inject`）
//          投给**绑定的那个**会话；没有绑定就 hold，一次最多 1 个会话。
// ORIGIN: install/autodl-events/lib/plugin.js（机制抽取；去实例项目路径、机器名与文案里的项目命令）
// CUMULATIVE-DELTA: 首版。出厂值：`projects: []`（不内置路径）· `fallback: 'hold'`（无绑定不投）· `maxTargets: 1`；
//   事件文案末尾的复核提示改为 `config.reviewHint`（⛔ 不写死任何项目命令）；steer 与 inject 两种能力都保留。
//
// 事件从哪来：后端（Python）在每次状态检查里判变化，落成 `<workspace>/.autoresearch/events/pending/<id>.json`。
// 本插件只轮询那个目录，⛔ 不联网、不查机器、不判事件。
//
// 三条边界：
//   1. ⛔ 本插件**不联网、不查机器、不判事件** —— 判据只有后端那一份实现。
//   2. ⛔ 投递失败不重试风暴：挪不动的文件留在队列里，下一轮再试。
//   3. ⛔ 太旧的事件（默认 45 分钟）不投递，挪进 `expired/`：一句「现在能开了」压太久就是骗人。

import {
  appendLog,
  dropBroken,
  formatMessage,
  isStale,
  listEvents,
  makeUserMessage,
  moveEvent,
  pickAgents,
  pickSummary,
  readTargets,
  resolveConfig,
} from './plugin-core.mjs'

export const name = 'autodl-events'

/** 只用到 `agents` 服务；取不到就自己安静待着，⛔ 不让宿主起不来。 */
export const inject = []

export function apply(ctx, rawConfig) {
  const cfg = resolveConfig(rawConfig)
  const logger = ctx?.logger ?? console
  const warn = (msg) => {
    try {
      logger.warn?.(`[autodl-events] ${msg}`)
    } catch {}
  }
  const info = (msg) => {
    try {
      logger.info?.(`[autodl-events] ${msg}`)
    } catch {}
  }

  if (!cfg.enabled) {
    info('已禁用，不注册任何定时器')
    return
  }
  if (!cfg.eventDir) {
    warn('config.eventDir 没配，插件什么都不做')
    return
  }

  let busy = false
  let stopped = false
  /** 上一次「有事件但没人收」时打过的日志键：同一批只打一次，⛔ 不每次轮询都刷屏。 */
  let lastSkipKey = ''

  const deliverOnce = () => {
    if (busy || stopped) return
    busy = true
    try {
      run()
    } catch (error) {
      warn(`投递轮次异常（已忽略）：${error?.message ?? error}`)
    } finally {
      busy = false
    }
  }

  const run = () => {
    const { events, bad } = listEvents(cfg.eventDir)
    for (const file of bad) {
      if (dropBroken(file)) warn(`删掉一个读不出的事件文件：${file}`)
    }
    if (events.length === 0) return

    const now = Date.now()
    const maxAgeMs = cfg.maxAgeMinutes * 60_000
    const fresh = []
    for (const item of events) {
      if (isStale(item.event, now, maxAgeMs)) {
        if (moveEvent(item.file, cfg.expiredDir)) {
          info(`事件太旧（>${cfg.maxAgeMinutes} 分钟），丢进 expired/：${item.event.machine}`)
        }
        continue
      }
      fresh.push(item)
    }
    if (fresh.length === 0) return

    const roots = ctx?.get?.('agents')?.roots?.() ?? []
    const bound = readTargets(cfg.targetsFile)
    const agents = pickAgents(roots, cfg, bound)
    if (agents.length === 0) {
      // 没有能收的会话：事件留在队列里，等下一次（绑定会话回来 / 有会话开起来就会被送到）。
      // ⚠️ 这里要留痕，否则「什么都没发生」分不清是「没事件」「没会话」还是「绑定的人不在」。
      const key = fresh.map((x) => x.event.id ?? x.file).join('|')
      if (key !== lastSkipKey) {
        lastSkipKey = key
        appendLog(cfg.logFile, {
          at: new Date().toISOString(),
          kind: 'waiting',
          pending: fresh.length,
          machines: fresh.map((x) => x.event.machine),
          reason: bound.sessions.length > 0 ? 'bound-session-not-live' : 'no-bound-session',
          fallback: cfg.fallback,
          ...pickSummary(roots, cfg, bound),
        })
      }
      return
    }
    lastSkipKey = ''

    const batch = fresh.slice(0, cfg.maxEventsPerMessage)
    const text = formatMessage(batch.map((x) => x.event), cfg)

    let delivered = 0
    const targets = []
    for (const agent of agents) {
      try {
        // ⚠️ 每个会话一条**独立**的消息对象（同一个 id 出现在两个会话的日志里会被当成同一条）。
        const message = makeUserMessage(text)
        if (cfg.delivery === 'inject' && typeof agent.inject === 'function') agent.inject(message)
        else agent.steer(message)
        delivered += 1
        targets.push(agent?.session?.id ?? 'unknown')
      } catch (error) {
        warn(`投递给会话失败（${agent?.session?.id ?? 'unknown'}）：${error?.message ?? error}`)
      }
    }

    if (delivered === 0) return

    for (const item of batch) {
      if (!moveEvent(item.file, cfg.deliveredDir)) {
        warn(`事件已送出但没能挪走（下一轮可能重发）：${item.file}`)
      }
    }
    appendLog(cfg.logFile, {
      at: new Date().toISOString(),
      kind: 'delivered',
      machines: batch.map((x) => x.event.machine),
      triggers: batch.flatMap((x) => x.event.triggers ?? []),
      sessions: targets,
      delivery: cfg.delivery,
      remaining: fresh.length - batch.length,
      ...pickSummary(roots, cfg, bound, agents),
    })
    info(`已投递 ${batch.length} 条事件给 ${delivered} 个会话：${batch.map((x) => x.event.machine).join('、')}`)
  }

  // 组合一次就留一行痕迹：出问题时先确认「插件到底有没有被宿主挂上」。
  appendLog(cfg.logFile, {
    at: new Date().toISOString(),
    kind: 'apply',
    pid: process.pid,
    eventDir: cfg.eventDir,
    pollMs: cfg.pollMs,
    delivery: cfg.delivery,
    maxTargets: cfg.maxTargets,
    fallback: cfg.fallback,
    projects: cfg.projects,
    targetsFile: cfg.targetsFile || null,
  })

  // 定时轮询：`unref` 掉，绝不因为本插件让宿主进程活着不退。
  const timer = setInterval(deliverOnce, cfg.pollMs)
  timer.unref?.()

  ctx.effect(() => {
    // 挂上就先看一次（宿主刚起来时队列里可能已经攒了事件）。
    deliverOnce()
    return () => {
      stopped = true
      clearInterval(timer)
    }
  })

  info(`已挂上：每 ${Math.round(cfg.pollMs / 1000)} 秒看一次 ${cfg.eventDir}（投递方式 ${cfg.delivery}，无绑定 ${cfg.fallback}）`)
}

/** 只给 `dryrun.mjs` 与自检用：把事件目录排成一条消息，不投递、不挪文件。 */
export function preview(eventDir, rawConfig) {
  const cfg = resolveConfig({ ...rawConfig, eventDir })
  const { events } = listEvents(cfg.eventDir)
  const now = Date.now()
  const fresh = events.filter((x) => !isStale(x.event, now, cfg.maxAgeMinutes * 60_000))
  return { total: events.length, fresh: fresh.length, text: fresh.length ? formatMessage(fresh.map((x) => x.event), cfg) : '' }
}
