// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: DSH Host 插件 —— 对「运行时子 agent」按显式策略裁剪指令注入；主 agent 默认不受影响。
// ORIGIN: install/agent-instruction-filter/index.js（机制抽取；出厂策略改为 off/keep，实例路径与历史结论已剥离）
// CUMULATIVE-DELTA: 首版。config.mode 出厂值由「filter + 旧 drop 策略」改为 **'off'**（不注册任何监听器）；
//   策略读取失败/不合法时一律回落到**中性 keep 策略**，⛔ 绝不回落到任何旧 drop 策略；
//   保留 pre-step / 子级识别 / decision 改写 / 收件箱同步 / 异常回退。`strip` 与 `onError: drop` 仍是显式能力。
//
// 接线依据（宿主契约，升级宿主后要复核）：
//   · 指令注入由宿主内置指令插件在 `agent/pre-step` 瀑布里、`await next()` **之后**完成；
//     注入消息带 `source.kind === 'agent-instructions'` 且 `source.changes` 是数组。
//   · cordis 瀑布「listeners run outermost-first」，`prepend` 把监听器 unshift 到最前
//     ⇒ 本插件 await next() 之后才看得到注入结果。
//   · 子级判定 = 不在 `ctx.agents.roots()` 里；回退判据 = 会话头部的持久谱系 `parentSession`。
//
// 出厂行为（两条独立护栏）：config.mode 缺省 = 'off'（不注册），policy.mjs 的 rules = []（就算打开也逐字不动）。

import { readFileSync } from 'node:fs'
import { isAbsolute, resolve } from 'node:path'
import { filterInstructionText } from './filter-core.mjs'
import { policy as bundledPolicy, validatePolicy } from './policy.mjs'

const INSTRUCTION_KIND = 'agent-instructions'
const MODES = ['off', 'strip', 'filter']

function isInstructionMessage(message) {
  const source = message?.source
  return (
    typeof source === 'object' &&
    source !== null &&
    source.kind === INSTRUCTION_KIND &&
    Array.isArray(source.changes)
  )
}

/** 收口配置。⛔ 不抛：配置坏了插件也必须能起（缺省即 off，所以最坏情况是「什么都不做」）。 */
export function normalizeConfig(config) {
  const raw = config ?? {}
  const mode = MODES.includes(raw.mode) ? raw.mode : 'off'
  return {
    mode,
    childOnly: raw.childOnly !== false,
    policyPath: typeof raw.policyPath === 'string' && raw.policyPath.length > 0 ? raw.policyPath : null,
    // 异常回退：默认 keep（fail-open）；'drop' 是显式选择，⛔ 不是默认。
    onError: raw.onError === 'drop' ? 'drop' : 'keep',
  }
}

/**
 * 读策略。
 * 优先级：显式 policyPath > 出厂 policy.mjs。
 * 任何失败（读不到 / JSON 坏 / 结构不合法）⇒ 记 warn 并**回落中性 keep**。
 * @returns {{policy: object, source: 'bundled'|'file'|'fallback-keep', why?: string}}
 */
export function loadPolicy(config, ctx) {
  if (config.policyPath === null) return { policy: bundledPolicy, source: 'bundled' }
  const absolute = isAbsolute(config.policyPath)
    ? config.policyPath
    : resolve(process.cwd(), config.policyPath)
  let parsed
  try {
    parsed = JSON.parse(readFileSync(absolute, 'utf8'))
  } catch (error) {
    ctx.logger?.warn?.('agent-instruction-filter: policy unreadable (%s), falling back to keep: %o', absolute, error)
    return { policy: bundledPolicy, source: 'fallback-keep', why: 'unreadable' }
  }
  const verdict = validatePolicy(parsed)
  if (!verdict.ok) {
    ctx.logger?.warn?.('agent-instruction-filter: policy invalid (%s): %s; falling back to keep', absolute, verdict.why)
    return { policy: bundledPolicy, source: 'fallback-keep', why: verdict.why }
  }
  return { policy: parsed, source: 'file' }
}

/**
 * 子级判定。⛔ 不靠事件 payload：`agent/created` 只带 `{agent, source, signal}`（无 owner）。
 */
function isChildAgent(ctx, agent) {
  const agents = ctx.get('agents')
  if (agents !== undefined && typeof agents?.roots === 'function') {
    try {
      const roots = agents.roots()
      if (Array.isArray(roots) && roots.length > 0) {
        return !roots.some((root) => root?.id === agent?.id)
      }
    } catch {
      /* 回退到持久谱系 */
    }
  }
  const parentSession = agent?.session?.header?.parentSession
  return parentSession !== undefined && parentSession !== null
}

function rewriteMessage(message, policy, mode) {
  if (mode === 'strip') return null
  const blocks = Array.isArray(message.content) ? message.content : []
  const nextBlocks = []
  let touched = false
  for (const block of blocks) {
    if (block?.type !== 'text' || typeof block.text !== 'string') {
      nextBlocks.push(block)
      continue
    }
    const result = filterInstructionText(block.text, policy)
    if (result.empty) {
      touched = true
      continue
    }
    if (result.changed) touched = true
    nextBlocks.push(result.changed ? { ...block, text: result.text } : block)
  }
  if (nextBlocks.length === 0) return null
  return touched ? { ...message, content: nextBlocks } : message
}

/** 收件箱里的未投递副本也要同步，否则未裁剪的那份会在后续步骤重新进入。 */
function syncInbox(agent, policy, mode) {
  const inbox = agent?.inbox
  if (inbox === undefined || typeof inbox?.remove !== 'function') return
  const pending = Array.isArray(inbox.nextStep) ? inbox.nextStep.filter(isInstructionMessage) : []
  for (const message of pending) {
    const rewritten = rewriteMessage(message, policy, mode)
    if (rewritten === null) {
      inbox.remove(message.id)
      continue
    }
    if (rewritten !== message && typeof inbox.replace === 'function') inbox.replace(message.id, rewritten)
  }
}

function rewriteDecision(agent, decision, policy, mode) {
  if (!Array.isArray(decision?.messages)) return decision
  const messages = []
  let changed = false
  for (const message of decision.messages) {
    if (!isInstructionMessage(message)) {
      messages.push(message)
      continue
    }
    const rewritten = rewriteMessage(message, policy, mode)
    if (rewritten === null) {
      changed = true
      continue
    }
    if (rewritten !== message) changed = true
    messages.push(rewritten)
  }
  syncInbox(agent, policy, mode)
  return changed ? { ...decision, messages } : decision
}

function dropAllInstructions(decision) {
  if (!Array.isArray(decision?.messages)) return decision
  return { ...decision, messages: decision.messages.filter((message) => !isInstructionMessage(message)) }
}

export function apply(ctx, config) {
  const cfg = normalizeConfig(config)
  if (cfg.mode === 'off') return
  const loaded = loadPolicy(cfg, ctx)

  ctx.on(
    'agent/pre-step',
    async (payload, next) => {
      const decision = await next()
      try {
        const agent = payload?.agent
        if (decision?.kind === 'reject') return decision
        if (cfg.childOnly && !isChildAgent(ctx, agent)) return decision
        return rewriteDecision(agent, decision, loaded.policy, cfg.mode)
      } catch (error) {
        ctx.logger?.warn?.('agent-instruction-filter: %o', error)
        return cfg.onError === 'drop' ? dropAllInstructions(decision) : decision
      }
    },
    { prepend: true },
  )
}
