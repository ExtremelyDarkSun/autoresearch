/**
 * GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * PURPOSE: dsh-doc-gap（Host 插件本体）—— 回合结束时判一次「这个提问指向的点，项目文档说了没有」，
 *          没说清就 steer 一条提示，把**落点**交给会话自己判断。
 * ORIGIN: install/dsh-doc-gap/index.js（机制抽取；去掉实例项目匹配、固定判据与文档绝对路径）
 * CUMULATIVE-DELTA: 首版。① 出厂 `enabled: false`：未显式启用时**一个监听器都不注册 ⇒ 不会发生模型调用**；
 *   ② 系统提示由 `cfg.policy` 现拼（范围/判据/锚点外置），本文件不含任何项目专用范围；
 *   ③ 文档超上限时**无条件**往 stderr 写一条诊断（不受 logLevel 影响），⛔ 不许静默截断；
 *   ④ 项目范围按规范化路径前缀隔离（实现在 docgap-core.mjs）。
 *   纯函数与提示词全在 `docgap-core.mjs`；本文件只做接线（事件、后端、steer）。
 */

import { randomUUID } from 'node:crypto'

import {
  applyTruncationGate,
  buildHint,
  buildJudgePrompt,
  buildJudgeSystem,
  docDiagnostics,
  extractJson,
  flattenJevAnswer,
  gateVerdict,
  loadDocs,
  makeLogger,
  matchesProject,
  normalizeVerdict,
  resolveConfig,
  textOf,
  truncate,
} from './docgap-core.mjs'

export const name = 'doc-gap'

/** 超限/读不到必须能被看见：这条不走 logLevel。 */
function reportDiagnostics(diag) {
  if (!diag || !diag.message) return
  try {
    process.stderr.write(`[doc-gap] ${diag.message}\n`)
  } catch {}
}

// ---------------------------------------------------------------------------
// 判定后端
// ---------------------------------------------------------------------------

async function judgeViaSession(ctx, cfg, logger, input, route, signal) {
  const llm = ctx.get('llm')
  if (!llm || typeof llm.stream !== 'function') {
    logger.debug('没有 llm 服务，跳过')
    return null
  }
  const provider = cfg.judge.provider ?? route.provider
  const model = cfg.judge.model ?? route.model
  if (!provider || !model) {
    logger.debug('本回合还没解析出 provider/model，跳过')
    return null
  }
  let text = ''
  let finishKind
  const options = {
    provider,
    model,
    system: input.system,
    messages: [{ role: 'user', content: [{ type: 'text', text: buildJudgePrompt(input) }] }],
    maxTokens: cfg.judge.maxTokens,
    temperature: cfg.judge.temperature,
    signal,
  }
  // 推理型模型的思考 token 也算在 maxTokens 里；需要关掉思考时由配置显式指定。
  if (cfg.judge.reasoningEffort !== undefined) options.reasoningEffort = cfg.judge.reasoningEffort
  for await (const chunk of llm.stream(options)) {
    if (chunk?.type === 'text-delta' && typeof chunk.text === 'string') text += chunk.text
    else if (chunk?.type === 'finish') finishKind = chunk.reason?.kind ?? finishKind
  }
  if (finishKind && finishKind !== 'stop' && text.trim().length === 0) {
    const kind = String(finishKind)
    logger.warn(
      `判定没有产出文本（结束原因 ${kind}）。${
        kind.includes('token') || kind === 'length'
          ? `思考把 maxTokens=${cfg.judge.maxTokens} 吃光了，调大 judge.maxTokens。`
          : ''
      }`,
    )
    return null
  }
  logger.debug(`session 判定原文：${text.slice(0, 400)}`)
  return normalizeVerdict(extractJson(text))
}

async function judgeViaOpenAI(cfg, logger, input, signal) {
  const { baseURL, apiKey, apiKeyRef, model } = cfg.judge
  const key = apiKey ?? (apiKeyRef ? process.env[apiKeyRef] : undefined)
  if (!baseURL || !model || !key) {
    logger.debug('openai 后端缺 baseURL/model/key，跳过')
    return null
  }
  const response = await fetch(`${baseURL.replace(/\/+$/, '')}/chat/completions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
    body: JSON.stringify({
      model,
      temperature: cfg.judge.temperature,
      max_tokens: cfg.judge.maxTokens,
      messages: [
        { role: 'system', content: input.system },
        { role: 'user', content: buildJudgePrompt(input) },
      ],
    }),
    signal,
  })
  if (!response.ok) {
    logger.warn(`openai 后端 HTTP ${response.status}`)
    return null
  }
  const body = await response.json()
  const text = body?.choices?.[0]?.message?.content ?? ''
  return normalizeVerdict(extractJson(text))
}

async function judgeViaJev(cfg, logger, input, signal) {
  const { baseURL, apiKey, apiKeyRef, model } = cfg.judge
  const key = apiKey ?? (apiKeyRef ? process.env[apiKeyRef] : undefined)
  if (!baseURL || !key) {
    logger.debug('jev 后端缺 baseURL/key，跳过')
    return null
  }
  const criteria = {
    covered: cfg.policy.criteria.covered,
    partial: cfg.policy.criteria.partial,
    missing: cfg.policy.criteria.missing,
  }
  const questions = [
    {
      name: 'coverage',
      type: 'choice',
      instructions: '这次提问指向的知识点，在给定项目文档里的覆盖情况',
      criteria,
    },
  ]
  const response = await fetch(`${baseURL.replace(/\/+$/, '')}/v1/systemone`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: `Bearer ${key}` },
    body: JSON.stringify({ model: model ?? 'jev-latest', state: buildJudgePrompt(input), questions }),
    signal,
  })
  if (!response.ok) {
    logger.warn(`jev 后端 HTTP ${response.status}`)
    return null
  }
  const body = await response.json()
  return normalizeVerdict(flattenJevAnswer(body))
}

// ---------------------------------------------------------------------------
// 插件本体
// ---------------------------------------------------------------------------

export function apply(ctx, rawConfig) {
  const cfg = resolveConfig(rawConfig)
  const logger = makeLogger(cfg)
  if (!cfg.enabled) {
    // 出厂路径：不注册任何监听器 ⇒ 不会有任何模型调用、不会有任何截断诊断。
    logger.info('未启用（enabled 缺省为 false），不注册任何监听器')
    return
  }
  if (cfg.docs.length === 0) {
    logger.warn('已启用但没有配判据文档（config.docs），不会判定任何回合')
    return
  }

  // 每会话：本回合的人类提问 + 本回合 assistant 文本 + 上次提示时间
  const sessions = new Map()
  // 每 agent：本回合实际使用的 provider/model
  const routes = new WeakMap()

  const stateFor = (id) => {
    let state = sessions.get(id)
    if (!state) {
      state = { question: '', answers: [], lastHintAt: 0 }
      sessions.set(id, state)
    }
    return state
  }

  ctx.on('session/event', (session, event) => {
    try {
      const id = session?.id
      if (!id || !event || typeof event.type !== 'string') return
      const state = stateFor(id)
      switch (event.type) {
        case 'turn/start':
          state.answers = []
          break
        case 'user/message': {
          const message = event.data
          if (message?.source?.kind !== 'user') return
          const text = textOf(message).trim()
          if (text) state.question = text
          break
        }
        case 'assistant/message': {
          const text = textOf(event.data?.message).trim()
          if (text) state.answers.push(text)
          break
        }
        default:
          break
      }
    } catch (error) {
      logger.debug(`session/event 处理失败：${error?.message ?? error}`)
    }
  })

  // 记下本回合真正用的路由，供 session 后端的判定复用同一个模型
  ctx.on('agent/request', async (payload, next) => {
    const config = await next()
    try {
      if (payload?.agent && config) routes.set(payload.agent, { provider: config.provider, model: config.model })
    } catch {}
    return config
  })

  ctx.on('agent/turn-stopping', async (payload) => {
    try {
      await handleTurnStopping(payload)
    } catch (error) {
      logger.warn(`turn-stopping 处理失败（已忽略）：${error?.message ?? error}`)
    }
  })

  async function handleTurnStopping(payload) {
    const agent = payload?.agent
    const sessionId = agent?.session?.id
    if (!agent || !sessionId) return
    if (payload.signal?.aborted) return
    if (cfg.onlyRoot) {
      const roots = ctx.get('agents')?.roots?.()
      if (Array.isArray(roots) && !roots.includes(agent)) {
        logger.debug('子 agent，跳过')
        return
      }
    }

    if (cfg.projects.length > 0) {
      const sessionCwd = agent.session?.header?.cwd
      if (!matchesProject(cfg.projects, sessionCwd)) {
        logger.debug(`不在目标项目内（cwd=${sessionCwd ?? '未知'}），跳过`)
        return
      }
    }

    const state = sessions.get(sessionId)
    if (!state) return

    const question = truncate(state.question.trim(), cfg.maxQuestionChars)
    const answer = truncate(state.answers.join('\n\n').trim(), cfg.maxAnswerChars)
    if (!question || !answer) {
      logger.debug('本回合没有人类提问或没有可见答复，跳过')
      return
    }

    const now = Date.now()
    if (now - state.lastHintAt < cfg.minGapMs) {
      logger.debug('距上次提示太近，跳过')
      return
    }

    const docs = loadDocs(cfg)
    const diag = docDiagnostics(docs, cfg)
    // ⭐ 超上限 / 读不到必须被诊断出来 —— 无条件写 stderr，不受 logLevel 影响。
    reportDiagnostics(diag)
    const usable = docs.filter((doc) => doc.text.trim().length > 0)
    if (usable.length === 0) {
      logger.warn('没有读到任何文档内容，跳过判定')
      return
    }

    const input = {
      question,
      answer,
      docs: usable,
      system: buildJudgeSystem(cfg.policy),
      policy: cfg.policy,
    }
    const route = routes.get(agent) ?? agent.options ?? {}
    const signal = AbortSignal.timeout(cfg.judge.timeoutMs)

    let verdict = null
    if (cfg.judge.backend === 'jev') verdict = await judgeViaJev(cfg, logger, input, signal)
    else if (cfg.judge.backend === 'openai') verdict = await judgeViaOpenAI(cfg, logger, input, signal)
    else verdict = await judgeViaSession(ctx, cfg, logger, input, route, signal)

    // ⭐ 截断硬闸（机械执行，⛔ 不靠提示词自觉）：材料被截断时 missing→partial 且 p≤0.6。
    const gatedVerdict = applyTruncationGate(verdict, usable)
    if (gatedVerdict.gated) {
      logger.warn(`判定材料被截断，已按硬闸降级：${gatedVerdict.why}`)
      verdict = gatedVerdict.verdict
    }

    const gate = gateVerdict(verdict, cfg.threshold)
    if (!gate.hit) {
      logger.info(`不提示：${gate.reason}${verdict ? `（coverage=${verdict.coverage} p=${verdict.probability.toFixed(2)}）` : ''}`)
      return
    }

    const text = buildHint(verdict, cfg.notify, usable.map((doc) => doc.label))
    state.lastHintAt = now
    agent.steer(freezeUserMessage(text))
    logger.info(`已提示：${verdict.point}（${verdict.coverage} p=${verdict.probability.toFixed(2)}，落点交给会话判断）`)
  }
}

/** 手工构造一条不可变的 user 消息（不 import 宿主 llm 包，避免运行时解析耦合）。 */
function freezeUserMessage(text) {
  return Object.freeze({
    id: randomUUID(),
    role: 'user',
    content: Object.freeze([Object.freeze({ type: 'text', text })]),
    source: Object.freeze({ kind: 'doc-gap' }),
  })
}
