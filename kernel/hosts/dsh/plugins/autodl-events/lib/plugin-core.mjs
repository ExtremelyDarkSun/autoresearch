// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: autodl-events 插件的纯函数层 —— 读队列 · 判新旧 · 排消息 · 挑会话（绑定优先） · 挪文件 · 写回执
// ORIGIN: install/autodl-events/lib/plugin-core.mjs（机制抽取；实例项目路径、机器名与事件文案里的项目命令已剥离）
// CUMULATIVE-DELTA: 首版。三条默认值按「不猜、不广播」定：
//   ① `projects: []` —— ⛔ 不内置任何项目路径（空 = 不限项目，实际投递仍受 targets 约束）；
//   ② `fallback: 'hold'` —— 没有可用绑定会话时**一条都不发**，等绑定出现（⛔ 不退回启发式乱投）；
//   ③ `maxTargets: 1` —— 一次最多叫醒 1 个会话。
//   事件正文由后端生成（唯一实现）；本文件只加固定前言，⛔ 不再写任何项目命令。
//
// 职责边界：本文件只做「读队列 / 判新旧 / 排文字 / 挑会话」四件事，**不碰 ctx、不起定时器**。
// 接线（定时器、steer/inject、ctx.effect）在 `lib/plugin.js`；离线自检直接 import 这里。

import { randomUUID } from 'node:crypto'
import { appendFileSync, mkdirSync, readdirSync, readFileSync, renameSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'

export const DEFAULTS = {
  enabled: true,
  /** 待投递队列目录（绝对路径）。后端默认写在 `<workspace>/.autoresearch/events/pending`。必填。 */
  eventDir: '',
  /** 已投递目录；空 = 与 eventDir 同级（`../delivered`）。 */
  deliveredDir: '',
  /** 已过期目录；空 = 与 eventDir 同级（`../expired`）。 */
  expiredDir: '',
  /** 投递回执（每行一条 JSON）。空 = 不记。默认建议 `<workspace>/.autoresearch/events/delivered.jsonl`。 */
  logFile: '',
  /** 多久看一次队列（毫秒，1 秒 ~ 10 分钟）。 */
  pollMs: 5000,
  /** 超过这个分钟数的事件不再投递（时间敏感通知压久了会骗人）。 */
  maxAgeMinutes: 45,
  /** 只在**这些工作目录（规范化路径前缀）**里生效；空列表 = 不限项目。 */
  projects: [],
  /** `steer` = 空闲会话也开一个新回合（能叫醒）；`inject` = 只塞进已有回合的下一个 step（不叫醒）。 */
  delivery: 'steer',
  /** 一条消息最多带几条事件；超出的留到下一批。 */
  maxEventsPerMessage: 3,
  /** 一次最多叫醒几个会话。默认 **1**（全量广播会把每个会话都开一个新回合）。 */
  maxTargets: 1,
  /**
   * 显式订阅名单（JSON 文件）。后端侧把「负责人会话」写进这个文件：
   * `{"sessions":[…], "authoritative":true, "source":"<来源名>"}`。
   * 人手写的 `{"sessions":[…]}` 也认。
   */
  targetsFile: '',
  /**
   * 没有可用的绑定会话时怎么办：
   * `hold`（**出厂默认**）= 一条都不发，事件留在队列里等绑定出现（⛔ 不改投别人）；
   * `pick` = 退回启发式挑 1 个（`status==='running'` 优先，其次 `session.seq` 最大）。
   */
  fallback: 'hold',
  /** 消息末尾那句「怎么复核」。⛔ 不写死任何项目命令；要指到具体入口就由部署方在 config 里给。 */
  reviewHint: '事件只说明「看起来能开」，没有试开机。要复核请用部署方提供的状态检查入口。',
}

const DELIVERY_MODES = new Set(['steer', 'inject'])

function asString(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function asNumber(value, fallback) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/** 把 profile patch 里的原始配置收成一份完整配置。⛔ 不抛异常（配置坏了插件也要能起）。 */
export function resolveConfig(raw) {
  const config = { ...DEFAULTS }
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return config
  if (typeof raw.enabled === 'boolean') config.enabled = raw.enabled
  for (const key of ['eventDir', 'deliveredDir', 'expiredDir', 'logFile', 'targetsFile', 'reviewHint']) {
    if (key in raw) config[key] = asString(raw[key])
  }
  if (asString(config.reviewHint).length === 0) config.reviewHint = DEFAULTS.reviewHint
  for (const key of ['pollMs', 'maxAgeMinutes', 'maxEventsPerMessage', 'maxTargets']) {
    if (key in raw) config[key] = asNumber(raw[key], DEFAULTS[key])
  }
  if ('delivery' in raw) {
    const mode = asString(raw.delivery).toLowerCase()
    config.delivery = DELIVERY_MODES.has(mode) ? mode : DEFAULTS.delivery
  }
  if ('fallback' in raw) {
    const mode = asString(raw.fallback).toLowerCase()
    config.fallback = mode === 'pick' ? 'pick' : 'hold'
  }
  if ('projects' in raw) {
    config.projects = Array.isArray(raw.projects)
      ? raw.projects.map((x) => asString(x)).filter(Boolean)
      : [asString(raw.projects)].filter(Boolean)
  }
  config.pollMs = Math.max(1000, Math.min(config.pollMs, 600000))
  config.maxAgeMinutes = Math.max(1, config.maxAgeMinutes)
  config.maxEventsPerMessage = Math.max(1, Math.min(Math.round(config.maxEventsPerMessage), 20))
  config.maxTargets = Math.max(1, Math.min(Math.round(config.maxTargets), 5))
  if (!config.deliveredDir && config.eventDir) config.deliveredDir = join(config.eventDir, '..', 'delivered')
  if (!config.expiredDir && config.eventDir) config.expiredDir = join(config.eventDir, '..', 'expired')
  return config
}

/**
 * 读目录里的事件文件（按文件名排序 = 按时间排序）。
 * 坏文件 / 读不到一律跳过并计入 `bad`，⛔ 不抛。
 * @returns {{events: Array<{file: string, event: object}>, bad: string[]}}
 */
export function listEvents(dir) {
  const out = { events: [], bad: [] }
  if (!dir) return out
  let names
  try {
    names = readdirSync(dir)
  } catch {
    return out
  }
  for (const name of names.sort()) {
    if (!name.endsWith('.json')) continue
    const file = join(dir, name)
    try {
      const event = JSON.parse(readFileSync(file, 'utf8'))
      if (event && typeof event === 'object' && typeof event.machine === 'string') {
        out.events.push({ file, event })
      } else {
        out.bad.push(file)
      }
    } catch {
      out.bad.push(file)
    }
  }
  return out
}

/** 事件太旧了吗？`at` 解析不出来时**当旧的**（宁可丢一条，也不要拿旧状态骗人）。 */
export function isStale(event, nowMs, maxAgeMs) {
  const raw = event?.at
  const at = Date.parse(typeof raw === 'string' ? raw : '')
  if (!Number.isFinite(at)) return true
  return nowMs - at > maxAgeMs
}

/** 一条事件的人话摘要（正文用后端生成的那份，这里只兜底）。 */
export function eventSummary(event) {
  const text = asString(event?.text)
  if (text) return text
  const machine = asString(event?.machine) || '某台机器'
  const to = event?.to?.verdict || '可用'
  return `${machine} 的开机可用性变成了「${to}」。`
}

/** 把一批事件排成一条给模型看的消息。 */
export function formatMessage(events, cfg = DEFAULTS) {
  const list = Array.isArray(events) ? events.filter(Boolean) : []
  const count = list.length
  const machines = list.map((e) => asString(e.machine)).filter(Boolean)
  const head = count === 1
    ? `[机器状态事件] ${machines[0] || '某台机器'}`
    : `[机器状态事件] ${count} 台机器（${machines.join('、')}）`
  const parts = [
    head,
    '',
    '这是本机监控脚本刚推进来的一条状态变化通知（不是用户说的话，也不是新任务）。',
    '',
  ]
  list.forEach((event, index) => {
    if (index > 0) parts.push('', `—— 第 ${index + 1} 条 ——`, '')
    parts.push(eventSummary(event))
  })
  parts.push('', asString(cfg.reviewHint) || DEFAULTS.reviewHint)
  return parts.join('\n')
}

/** 路径归一：小写 + 反斜杠转正斜杠 + 折叠重复斜杠 + 去尾部斜杠。 */
function normalizePath(value) {
  return String(value ?? '').trim().replace(/\\/g, '/').replace(/\/{2,}/g, '/').replace(/\/+$/, '').toLowerCase()
}

/**
 * 工作目录命中 `projects` 里的任一条目就返回 true；`projects` 为空 = 不限项目。
 *
 * ⚠️ 判据是**规范化路径前缀**（相等或位于其下），⛔ 不是子串：
 *    写裸名字会把「路径里恰好含这串字的另一份克隆」的会话也算命中。
 */
export function matchesProject(cwd, projects) {
  const list = Array.isArray(projects) ? projects.map(normalizePath).filter(Boolean) : []
  if (list.length === 0) return true
  const text = normalizePath(cwd)
  if (!text) return false
  return list.some((prefix) => text === prefix || text.startsWith(`${prefix}/`))
}

/**
 * 本项目里「能收这条消息」的候选会话。
 * `roots` 由 `lib/plugin.js` 从 `ctx.get('agents').roots()` 取。
 */
export function candidates(roots, cfg = DEFAULTS) {
  const list = Array.isArray(roots) ? roots : []
  return list.filter((agent) => {
    if (!agent) return false
    if (typeof agent.steer !== 'function') return false
    const cwd = agent?.session?.header?.cwd
    return matchesProject(cwd, cfg.projects)
  })
}

/** 会话事件条数，只当「谁更像主线」的排序键。取不到就 0。 */
function seqOf(agent) {
  const n = Number(agent?.session?.seq)
  return Number.isFinite(n) ? n : 0
}

/**
 * 读订阅名单（`targetsFile`）。
 *
 * 两种来源写的是同一个文件：
 * - **判出来的**（后端按负责人绑定重写）：`{"sessions":[…], "authoritative":true, "source":"<来源名>"}`
 * - **人手写的**：`{"sessions":[…]}`（没有 `authoritative`）
 *
 * 文件缺失 / 坏 / 字段不对 ⇒ 空名单（按 fallback 决定发不发）。⛔ 不抛。
 * @returns {{sessions: string[], authoritative: boolean, source: string|null}}
 */
export function readTargets(file) {
  const out = { sessions: [], authoritative: false, source: null }
  if (!file) return out
  try {
    const doc = JSON.parse(readFileSync(file, 'utf8'))
    const list = Array.isArray(doc) ? doc : doc?.sessions
    if (Array.isArray(list)) out.sessions = list.map((x) => String(x).trim()).filter(Boolean)
    if (doc && !Array.isArray(doc)) {
      out.authoritative = doc.authoritative === true
      out.source = typeof doc.source === 'string' ? doc.source : null
    }
    return out
  } catch {
    return out
  }
}

/**
 * 挑该收这条消息的会话。**默认只挑 1 个**（`maxTargets`）。
 *
 * 四条规矩：
 * 1. **绑定的会话优先**：`targetsFile` 里那串 id 就是判出来的绑定，**按名单顺序**取。
 * 2. **名单里一个都不在场**：`fallback` 决定 —— `hold`（出厂默认）就等，⛔ 不改投；`pick` 才退回启发式。
 * 3. **没有名单**：同样按 `fallback`。启发式 = ① `status === 'running'` 的 → ② 其中 `session.seq` 最大的。
 * 4. **只挑 1 个**：一次投递 = 一个会话开一个新回合，广播的代价是成倍的回合。
 */
export function pickAgents(roots, cfg = DEFAULTS, targets = null) {
  const cfgSafe = cfg ?? DEFAULTS
  const list = candidates(roots, cfgSafe)
  const cap = Math.max(1, Number(cfgSafe.maxTargets) || 1)
  const wanted = (Array.isArray(targets?.sessions) ? targets.sessions : [])
    .map((x) => String(x).trim()).filter(Boolean)
  const fallback = cfgSafe.fallback === 'pick' ? 'pick' : 'hold'

  if (wanted.length > 0) {
    const byId = new Map(list.map((agent) => [String(agent?.session?.id), agent]))
    const bound = wanted.map((id) => byId.get(id)).filter(Boolean)
    if (bound.length > 0) return bound.slice(0, cap)     // 按名单顺序，不按 roots 顺序
    if (fallback === 'hold') return []
  } else if (fallback === 'hold') {
    // 没有绑定名单，而且要求「没绑定就不发」⇒ 一条都不投
    return []
  }

  const running = list.filter((agent) => agent?.status === 'running')
  const pool = running.length > 0 ? running : list
  return [...pool].sort((a, b) => seqOf(b) - seqOf(a)).slice(0, cap)
}

/** 这次挑靶的过程摘要（写进回执，用来回答「为什么是这个会话」）。 */
export function pickSummary(roots, cfg = DEFAULTS, targets = null, picked = []) {
  const list = candidates(roots, cfg ?? DEFAULTS)
  const wanted = (Array.isArray(targets?.sessions) ? targets.sessions : []).filter(Boolean)
  const pickedIds = (Array.isArray(picked) ? picked : []).map((a) => a?.session?.id ?? null)
  return {
    candidates: list.length,
    bound: wanted.length,
    boundLive: wanted.filter((id) => list.some((agent) => String(agent?.session?.id) === String(id))).length,
    boundSource: targets?.source ?? null,
    picked: pickedIds,
    // 什么都没投（waiting）时不算「退回」；只有真投出去、但投的不是绑定会话才算。
    fellBack: pickedIds.length > 0 && pickedIds.every((id) => !wanted.includes(String(id))),
    running: list.filter((agent) => agent?.status === 'running').length,
  }
}

/** 手工构造一条不可变的 user 消息（不 import 宿主 llm 包，避免运行时解析耦合）。 */
export function makeUserMessage(text) {
  return Object.freeze({
    id: randomUUID(),
    role: 'user',
    content: Object.freeze([Object.freeze({ type: 'text', text })]),
    source: Object.freeze({ kind: 'autodl-events' }),
  })
}

/** 把一条事件文件挪走（投递成功 → delivered，太旧 → expired）。返回是否挪动。 */
export function moveEvent(file, targetDir) {
  if (!file || !targetDir) return false
  try {
    mkdirSync(targetDir, { recursive: true })
    const name = String(file).split(/[\\/]/).pop()
    renameSync(file, join(targetDir, name))
    return true
  } catch {
    return false
  }
}

/** 删掉一个坏文件（解析不出事件的那种）。⛔ 不把坏文件留在队列里刷日志。 */
export function dropBroken(file) {
  try {
    unlinkSync(file)
    return true
  } catch {
    return false
  }
}

/** 追加一行回执。⛔ 写不动不许抛。 */
export function appendLog(logFile, record) {
  if (!logFile) return false
  try {
    mkdirSync(String(logFile).replace(/[\\/][^\\/]*$/, ''), { recursive: true })
    appendFileSync(logFile, JSON.stringify(record) + '\n', 'utf8')
    return true
  } catch {
    return false
  }
}
