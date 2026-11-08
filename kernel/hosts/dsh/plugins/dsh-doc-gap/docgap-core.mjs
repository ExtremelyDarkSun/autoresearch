/**
 * GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * PURPOSE: dsh-doc-gap 的纯逻辑层 —— 配置收口 · 判定政策（范围/判据/结尾锚点）· 文档加载与超限诊断 ·
 *          判定规范化与门控 · 提示词与提示文本生成。不碰宿主、不联网。
 * ORIGIN: install/dsh-doc-gap/docgap-core.mjs（机制抽取；实例的六类范围、排除清单、项目路径与阈值已外置）
 * CUMULATIVE-DELTA: 首版。四处结构性改动：
 *   ① 出厂 `enabled: false`（**模型调用只在显式启用后发生**）；
 *   ② 判定范围 / 排除清单 / 三类判据 / 结尾锚点全部搬进 `policy`，出厂只给**通用中性**默认（⛔ 无任何实例硬编码）；
 *   ③ `projects` 由「子串包含」改为**规范化路径前缀**匹配（同名克隆不再互相命中）；
 *   ④ `loadDocs` 逐份返回 `truncated / totalChars / limit / overCap`，并提供 `docDiagnostics()`：
 *      **文档超过上限必须被诊断出来**（记日志 + 量具报告 + 真实文件关卡判红），⛔ 不许静默截断。
 *
 * 挂在 `agent/turn-stopping`（轮次关闭前）：
 *   1. 取本回合的人类用户提问 + 本回合 assistant 的最终答复；
 *   2. 取配置里的判据文档原文（超过上限就截断并**明确标注**）；
 *   3. 让判定后端回答「这个提问指向的那个知识点，文档里说清楚了没有」；
 *   4. 「没覆盖」且置信度 ≥ 阈值 → `agent.steer()` 一条提示，回合多跑一步。
 *
 * 所有失败一律 fail-open：没启用、没文档、没路由、超时、JSON 解析失败 —— 都不打扰用户。
 */

import fs from 'node:fs'

export const name = 'doc-gap'

/** 判定政策的**出厂中性默认**：任何项目专用范围都必须由部署方显式给，⛔ 不写在这里。 */
export const DEFAULT_POLICY = {
  /** 判定范围：只判落在 include 里的点；exclude 里的一律判 covered。 */
  scope: {
    include: [
      '本项目的目标、规则、流程、判据与约束',
      '本项目已定案的结论、已登记的限制与已踩过的坑',
    ],
    exclude: [
      '与项目文档无关的通识与闲聊',
      '对助手自身能力的提问',
      '一次性操作询问（这次该跑什么命令、这个报错怎么修）',
    ],
  },
  /** 三类判据的措辞（判定器要照着答）。 */
  criteria: {
    covered: '文档明确写了这一点，含理由或边界',
    partial: '提到了但含糊、缺理由或缺边界',
    missing: '完全没有提到',
  },
  /** 提示词首段的角色说明。 */
  systemIntro: [
    '你是一个「文档完备性判定器」。你只回答一个问题：用户这次提问所指向的那个知识点，',
    '在给定的项目文档里是否已经说明清楚。',
  ],
  /** 结尾锚点（放在提示词最后一段，用来压住范围与输出格式）。 */
  anchor: '先判范围：只有落在上面「判定范围」里的问题才是候选；范围外一律判 covered、p_missing = 0。',
}

const DEFAULTS = {
  /** ⚠️ 出厂关闭：不启用就不会注册监听器，因此**不会发生任何模型调用**。 */
  enabled: false,
  /** 命中阈值：判定「未覆盖」的概率 ≥ 此值才提示。 */
  threshold: 0.8,
  /** [{ label, path, maxChars? }]，path 支持绝对路径；相对路径按 cfg.cwd（缺省 cwd）解析。 */
  docs: [],
  maxDocChars: 20000,
  maxAnswerChars: 6000,
  maxQuestionChars: 2000,
  /** 同一会话两条提示之间的最小间隔，防连环打扰。 */
  minGapMs: 90000,
  /** 'hint' = 只提示；'ask' = 让 agent 用 ask_user_question 问你。 */
  notify: 'hint',
  /** 只在顶层 agent 上触发，子 agent 一律跳过（否则会白烧 token）。 */
  onlyRoot: true,
  /**
   * 项目范围（**规范化路径前缀**，不是子串）：会话工作目录必须等于其中一项或是它的子目录。
   * 空数组 = 不限项目。
   */
  projects: [],
  /** 相对路径的解析基准。不给就用 process.cwd()。 */
  cwd: undefined,
  logLevel: 'warn', // silent | warn | info | debug
  /** 判定政策（见 DEFAULT_POLICY）；部署方用 config.policy 覆盖任意子键。 */
  policy: DEFAULT_POLICY,
  judge: {
    backend: 'session', // session | jev | openai
    timeoutMs: 9000,
    /** 推理型模型的思考 token 也算在内，给小了会「只想不说」，一句 JSON 都拿不到。 */
    maxTokens: 4000,
    temperature: 0,
    /** 可显式指定推理强度（适配器声明的 id）；不填就沿用主模型默认。 */
    reasoningEffort: undefined,
    /** session 后端：省略则用本回合主模型的路由。 */
    provider: undefined,
    model: undefined,
    /** jev / openai 后端。凭据优先 apiKeyRef（环境变量名），其次 apiKey（不推荐写盘）。 */
    baseURL: undefined,
    apiKeyRef: undefined,
    apiKey: undefined,
  },
}

const LEVELS = { silent: 0, warn: 1, info: 2, debug: 3 }

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

function deepMerge(base, over) {
  if (over === undefined || over === null) return base
  if (typeof base !== 'object' || base === null || Array.isArray(base)) return over
  if (typeof over !== 'object' || Array.isArray(over)) return over
  const out = { ...base }
  for (const [key, value] of Object.entries(over)) {
    out[key] = key in base ? deepMerge(base[key], value) : value
  }
  return out
}

function stringList(value) {
  if (Array.isArray(value)) return value.filter((x) => typeof x === 'string' && x.trim().length > 0).map((x) => x.trim())
  if (typeof value === 'string' && value.trim().length > 0) return [value.trim()]
  return []
}

/** 收口配置。⛔ 不抛：配置坏了也必须能起（最坏情况是「不启用」）。 */
export function resolveConfig(raw) {
  const cfg = deepMerge(structuredClone(DEFAULTS), raw ?? {})
  cfg.enabled = cfg.enabled === true
  cfg.threshold = clamp01(cfg.threshold, DEFAULTS.threshold)
  cfg.docs = Array.isArray(cfg.docs)
    ? cfg.docs
        .map((doc) => (typeof doc === 'string' ? { path: doc, label: basename(doc) } : doc))
        .filter((doc) => doc && typeof doc.path === 'string' && doc.path.length > 0)
    : []
  cfg.projects = stringList(cfg.projects)
  cfg.maxDocChars = positiveInt(cfg.maxDocChars, DEFAULTS.maxDocChars)
  cfg.maxAnswerChars = positiveInt(cfg.maxAnswerChars, DEFAULTS.maxAnswerChars)
  cfg.maxQuestionChars = positiveInt(cfg.maxQuestionChars, DEFAULTS.maxQuestionChars)
  cfg.minGapMs = nonNegativeInt(cfg.minGapMs, DEFAULTS.minGapMs)
  cfg.notify = cfg.notify === 'ask' ? 'ask' : 'hint'
  cfg.onlyRoot = cfg.onlyRoot !== false

  // 政策：逐子键收口，任何缺失都回落到中性默认（⛔ 不因配置缺项而借用实例政策）
  const policy = deepMerge(structuredClone(DEFAULT_POLICY), cfg.policy ?? {})
  policy.scope = {
    include: stringList(policy.scope?.include),
    exclude: stringList(policy.scope?.exclude),
  }
  policy.criteria = {
    covered: String(policy.criteria?.covered ?? DEFAULT_POLICY.criteria.covered),
    partial: String(policy.criteria?.partial ?? DEFAULT_POLICY.criteria.partial),
    missing: String(policy.criteria?.missing ?? DEFAULT_POLICY.criteria.missing),
  }
  policy.systemIntro = Array.isArray(policy.systemIntro) && policy.systemIntro.length > 0
    ? policy.systemIntro.map(String)
    : DEFAULT_POLICY.systemIntro
  policy.anchor = typeof policy.anchor === 'string' && policy.anchor.trim().length > 0
    ? policy.anchor.trim()
    : DEFAULT_POLICY.anchor
  cfg.policy = policy
  return cfg
}

function positiveInt(value, fallback) {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n) || n <= 0) return fallback
  return Math.floor(n)
}

/** 允许 0（0 = 不设最小间隔）。 */
function nonNegativeInt(value, fallback) {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n) || n < 0) return fallback
  return Math.floor(n)
}

function clamp01(value, fallback) {
  const n = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(n)) return fallback
  return Math.min(1, Math.max(0, n))
}

function basename(p) {
  const parts = String(p).replace(/\\/g, '/').split('/')
  return parts[parts.length - 1] || p
}

/**
 * 归一化路径用于比较：小写、统一斜杠、去掉空段与 `.`、折叠可折叠的 `..`、去尾部斜杠。
 * ⚠️ 这里只做**文本**归一，不解析符号链接；`..` 越不出根时原样保留（宁可比较失败，也不乱命中）。
 */
export function normalizePath(p) {
  if (typeof p !== 'string' || p.length === 0) return ''
  const raw = p.replace(/\\/g, '/').replace(/\/{2,}/g, '/')
  const parts = []
  for (const part of raw.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..' && parts.length > 0 && parts[parts.length - 1] !== '..') {
      parts.pop()
      continue
    }
    parts.push(part)
  }
  return parts.join('/').toLowerCase()
}

/**
 * 会话工作目录是否命中项目范围。
 * 判据 = **规范化路径前缀**：相等，或位于该前缀之下（`prefix/…`）。
 * ⛔ 不用子串：写一个裸名字会把「路径里恰好含这串字的另一份克隆」也算命中。
 * 空 projects ⇒ 恒 true（不限项目）。有 projects 但拿不到 cwd ⇒ false（宁可不触发）。
 */
export function matchesProject(projects, cwd) {
  const list = Array.isArray(projects) ? projects.map(normalizePath).filter(Boolean) : []
  if (list.length === 0) return true
  const target = normalizePath(cwd)
  if (!target) return false
  return list.some((prefix) => target === prefix || target.startsWith(`${prefix}/`))
}

export function makeLogger(cfg) {
  const limit = LEVELS[cfg.logLevel] ?? LEVELS.warn
  const write = (level, message) => {
    if ((LEVELS[level] ?? 99) > limit) return
    try {
      process.stderr.write(`[doc-gap] ${message}\n`)
    } catch {}
  }
  return {
    debug: (m) => write('debug', m),
    info: (m) => write('info', m),
    warn: (m) => write('warn', m),
  }
}

/** 从内容块里取纯文本（只认 text 块）。 */
export function textOf(message) {
  const blocks = message?.content
  if (!Array.isArray(blocks)) return ''
  return blocks
    .filter((block) => block && block.type === 'text' && typeof block.text === 'string')
    .map((block) => block.text)
    .join('\n')
}

export function truncate(text, max) {
  if (!Number.isFinite(max) || max <= 0) return text
  if (text.length <= max) return text
  return `${text.slice(0, max)}\n…[已截断，原长 ${text.length} 字符]`
}

/** 从模型输出里抠出第一个完整的 JSON 对象。 */
export function extractJson(text) {
  if (typeof text !== 'string') return null
  const start = text.indexOf('{')
  if (start < 0) return null
  let depth = 0
  let inString = false
  let escaped = false
  for (let i = start; i < text.length; i += 1) {
    const ch = text[i]
    if (inString) {
      if (escaped) escaped = false
      else if (ch === '\\') escaped = true
      else if (ch === '"') inString = false
      continue
    }
    if (ch === '"') inString = true
    else if (ch === '{') depth += 1
    else if (ch === '}') {
      depth -= 1
      if (depth === 0) {
        try {
          return JSON.parse(text.slice(start, i + 1))
        } catch {
          return null
        }
      }
    }
  }
  return null
}

const COVERAGE_ALIASES = new Map([
  ['covered', 'covered'],
  ['已覆盖', 'covered'],
  ['说明清楚', 'covered'],
  ['partial', 'partial'],
  ['部分', 'partial'],
  ['部分覆盖', 'partial'],
  ['missing', 'missing'],
  ['未覆盖', 'missing'],
  ['没有', 'missing'],
  ['遗漏', 'missing'],
])

/**
 * 把后端返回的原始对象规范化成判定结果；不合法返回 null。
 *
 * 注意：这里**不产出落点文档**。落点是会话自己的判断——它读过项目的落点规矩，
 * 比插件按 label 猜要准。
 */
export function normalizeVerdict(raw) {
  if (!raw || typeof raw !== 'object') return null
  const coverage = COVERAGE_ALIASES.get(String(raw.coverage ?? '').trim().toLowerCase())
    ?? COVERAGE_ALIASES.get(String(raw.coverage ?? '').trim())
    ?? null
  if (!coverage) return null

  const probability = clamp01(
    raw.p_missing ?? raw.pMissing ?? raw.probability ?? raw.confidence,
    NaN,
  )
  if (!Number.isFinite(probability)) return null

  const point = String(raw.point ?? '').trim().slice(0, 120)
  const why = String(raw.why ?? '').trim().slice(0, 200)
  return {
    coverage,
    probability,
    point: point || '(未概括)',
    why,
  }
}

/** 门控：只有「未说清 + 概率够高」才提示。落点由会话决定，不在这里卡。 */
export function gateVerdict(verdict, threshold) {
  if (!verdict) return { hit: false, reason: 'no-verdict' }
  if (verdict.coverage === 'covered') return { hit: false, reason: 'covered' }
  if (!(verdict.probability >= threshold)) return { hit: false, reason: 'below-threshold' }
  return { hit: true, reason: 'missing' }
}

/**
 * **截断硬闸**：只要有一份判据文档因超上限被截断，判定材料就不完整。
 * 机械执行三件事（⛔ 不靠提示词自觉）：
 *   ① `missing` 降级为 `partial`（你无法排除写在被截掉的部分）；
 *   ② `p_missing` 封顶 0.6；
 *   ③ 返回 `gated=true`，调用方据此记日志。
 * ⇒ 在默认阈值 0.8 下，截断材料**永远不可能**触发提示；要放宽阈值由部署方显式承担。
 * @returns {{verdict: object|null, gated: boolean, why: string|null}}
 */
export function applyTruncationGate(verdict, docs) {
  if (!verdict) return { verdict: null, gated: false, why: null }
  const truncated = (Array.isArray(docs) ? docs : []).filter((doc) => doc?.truncated === true)
  if (truncated.length === 0) return { verdict, gated: false, why: null }
  const next = { ...verdict }
  if (next.coverage === 'missing') next.coverage = 'partial'
  next.probability = Math.min(next.probability, 0.6)
  next.truncationGated = true
  return {
    verdict: next,
    gated: true,
    why: `有 ${truncated.length} 份文档被截断（${truncated.map((d) => `${d.label} ${d.totalChars}>${d.limit}`).join('; ')}）`,
  }
}

/** 从 Jev 的 `{answers:[{name, ...}]}` 里取 `coverage` 那一项的判定；缺项返回 null。 */
export function flattenJevAnswer(body) {
  const answers = Array.isArray(body?.answers) ? body.answers : []
  const flat = {}
  for (const entry of answers) {
    if (!entry || typeof entry !== 'object' || typeof entry.name !== 'string') continue
    if (entry.name !== 'coverage') continue
    const value = entry.choice ?? entry.value ?? entry.label ?? entry.answer
    flat.coverage = value
    const confidence = entry.confidence ?? entry.probability ?? topProbability(entry)
    flat.p_missing = coverageToMissing(value, confidence, entry)
  }
  if (flat.p_missing === undefined) return null
  flat.why = ''
  return flat
}

function topProbability(entry) {
  const probs = entry.probabilities ?? entry.probs
  if (!probs || typeof probs !== 'object') return undefined
  const values = Object.values(probs).filter((v) => typeof v === 'number')
  if (values.length === 0) return undefined
  return Math.max(...values)
}

function coverageToMissing(value, confidence, entry) {
  const label = String(value ?? '').toLowerCase()
  const probs = entry?.probabilities ?? entry?.probs
  if (probs && typeof probs === 'object') {
    const missing = probs.missing ?? probs['未覆盖'] ?? probs['遗漏']
    if (typeof missing === 'number') return missing
    const covered = probs.covered ?? probs['已覆盖']
    if (typeof covered === 'number') return 1 - covered
  }
  const c = Number.isFinite(confidence) ? confidence : 0.5
  if (label.includes('missing') || label.includes('未覆盖')) return c
  if (label.includes('partial') || label.includes('部分')) return Math.min(c, 0.7)
  return 1 - c
}

// ---------------------------------------------------------------------------
// 提示词（全部由 policy 生成；⛔ 本文件不内置任何项目专用范围）
// ---------------------------------------------------------------------------

/** 由政策拼出判定系统提示。 */
export function buildJudgeSystem(policy) {
  const p = policy ?? DEFAULT_POLICY
  const lines = [...(p.systemIntro ?? DEFAULT_POLICY.systemIntro), '']
  lines.push('## 第一步：先判范围')
  if (p.scope.include.length > 0) {
    lines.push('只有落在下面这些方面的候选才算「可能与文档有关」：')
    for (const item of p.scope.include) lines.push(`  - ${item}`)
  } else {
    lines.push('范围未限定：按「与项目文档有关」的一般理解判。')
  }
  if (p.scope.exclude.length > 0) {
    lines.push('下面这些一律判 covered、p_missing = 0，不要犹豫，也不要在 why 里辩解：')
    for (const item of p.scope.exclude) lines.push(`  - ${item}`)
  }
  lines.push('拿不准时判 covered：宁可不提，也不要拿不相干的问题打扰用户。')
  lines.push('')
  lines.push('## 第二步：再判覆盖')
  lines.push('1. 只依据给定文档判断，不要用你自己的领域知识补「应该写了什么」。')
  lines.push('2. 只要文档里有段落涉及同一主题（哪怕细节不全、没有逐条论证），就判 covered。')
  lines.push('   写得不够漂亮不算遗漏；只有「文档里真的没有这个东西」才算。')
  lines.push(`3. ${p.criteria.covered} = covered；${p.criteria.partial} = partial；${p.criteria.missing} = missing。`)
  lines.push('4. 不要判断这个点应该写进哪份文档——那是别人的工作，你只管「说了没有」。')
  lines.push('5. p_missing 是「未说清」的置信度，取值 0 到 1，要敢给低值。')
  lines.push('6. 标了「已截断」的文档只给了开头一部分：你无法排除这一点写在未给出的部分，')
  lines.push('   这种情况不许判 missing，最多判 partial，且 p_missing 不得超过 0.6。')
  lines.push('7. 只输出一个 JSON 对象，不要输出 JSON 以外的任何字符，不要用代码块包裹。')
  return lines.join('\n')
}

export function buildJudgePrompt({ question, answer, docs, policy }) {
  const p = policy ?? DEFAULT_POLICY
  const parts = []
  parts.push('## 用户这次的提问')
  parts.push(question)
  parts.push('')
  parts.push('## 助手本回合的最终答复')
  parts.push(answer)
  if (docs.length > 0) {
    parts.push('')
    parts.push('## 项目文档')
    for (const doc of docs) {
      const note = doc.truncated
        ? `（已截断：只给了前 ${doc.text.length} 字符，原文 ${doc.totalChars} 字符）`
        : ''
      parts.push(`### ${doc.label}${note}（${doc.path}）`)
      parts.push(doc.text)
      parts.push('')
    }
  }
  parts.push('## 输出')
  parts.push(p.anchor)
  parts.push('严格按下面的字段输出 JSON：')
  parts.push(
    [
      '{',
      '  "point": "用不超过 30 字概括这次提问指向的那个知识点",',
      '  "coverage": "covered | partial | missing",',
      '  "p_missing": 0.0,',
      '  "why": "不超过 40 字，说明依据（可引用文档原句片段）"',
      '}',
    ].join('\n'),
  )
  return parts.join('\n')
}

export function buildHint(verdict, mode, docLabels = []) {
  const percent = Math.round(verdict.probability * 100)
  const where = docLabels.length > 0 ? `（我对照的是：${docLabels.join(' / ')}）` : ''
  const head = `[doc-gap] 本轮你回答的「${verdict.point}」这一点，在当前项目文档里${verdict.coverage === 'missing' ? '没有写' : '写得不够清楚'}（置信度 ${percent}%）${where}。`
  const landing = '落点由你判断：按本项目的文档维护规矩，自己决定该补进哪份文件、哪一节。'
  if (mode === 'ask') {
    return [
      head,
      landing,
      '先调 ask_user_question 征求用户同意，不要自己动文件：',
      `- header 用「补文档」；question 写清三件事：要补什么（把「${verdict.point}」展开成一句可落盘的话）、你打算写进哪个文件哪一节、为什么判它缺失。`,
      '- 两个选项，按顺序：「补（Recommended）」和「先不补」。',
      '- 用户选「补」：按该文件现有章节结构追加，只加这一次的内容，不要重排全文。',
      '- 用户选「不补」：不要改动任何文件。',
      '- 工具超时或无人应答：不要重试，也不要自行落盘，如实告诉用户这次没有确认。',
    ].join('\n')
  }
  return `${head}\n${landing}\n要补的话告诉我补进哪份文件，我按它的现有结构写；不需要就忽略这条。`
}

// ---------------------------------------------------------------------------
// 文档读取与超限诊断
// ---------------------------------------------------------------------------

function resolvePath(p, cwd) {
  if (/^([a-zA-Z]:[\\/]|\\\\|\/)/.test(p)) return p
  const base = cwd || process.cwd()
  return `${base.replace(/[\\/]+$/, '')}/${p}`
}

/**
 * 读判据文档。逐份给出上限与截断事实。
 * @returns {Array<{label,path,text,truncated,totalChars,limit,overCap,error?}>}
 *   `overCap=true` 表示原文长度已超过该份的实际上限 ⇒ **调用方必须把它诊断出来**。
 */
export function loadDocs(cfg) {
  const out = []
  for (const doc of cfg.docs) {
    const full = resolvePath(doc.path, cfg.cwd)
    let raw
    try {
      raw = fs.readFileSync(full, 'utf8')
    } catch (error) {
      out.push({
        label: doc.label ?? basename(doc.path),
        path: full,
        text: '',
        truncated: false,
        totalChars: 0,
        limit: 0,
        overCap: false,
        error: String(error?.message ?? error),
      })
      continue
    }
    const limit = Number.isFinite(doc.maxChars) && doc.maxChars > 0 ? Math.floor(doc.maxChars) : cfg.maxDocChars
    const text = truncate(raw, limit)
    const truncated = text !== raw
    out.push({
      label: doc.label ?? basename(doc.path),
      path: full,
      text,
      truncated,
      totalChars: raw.length,
      limit,
      overCap: raw.length > limit,
    })
  }
  return out
}

/**
 * 文档诊断：哪些读不到、哪些超上限被截断。
 * ⛔ 这不是可选日志 —— 超上限时必须让调用方（插件 warn / 量具 / 真实文件关卡）看到。
 * @returns {{missing: Array, overCap: Array, truncated: Array, ok: boolean, message: string}}
 */
export function docDiagnostics(docs, cfg) {
  const missing = (docs ?? []).filter((d) => d.error)
  const overCap = (docs ?? []).filter((d) => d.overCap === true)
  const truncated = (docs ?? []).filter((d) => d.truncated === true)
  const parts = []
  if (missing.length > 0) {
    parts.push(`读不到 ${missing.length} 份：${missing.map((d) => `${d.path}${d.error ? ` (${d.error})` : ''}`).join('; ')}`)
  }
  if (overCap.length > 0) {
    const detail = overCap.map((d) => `${d.label} 原文 ${d.totalChars} 字符 > 上限 ${d.limit}`).join('; ')
    parts.push(`文档超过上限（已截断，判定材料不完整，只能最多判 partial 且 p_missing ≤ 0.6）：${detail}`)
  }
  return {
    missing,
    overCap,
    truncated,
    ok: missing.length === 0 && overCap.length === 0,
    message: parts.join(' | '),
  }
}
