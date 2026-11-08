// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 参数化 DSH 加载配置生成器 —— 按一份部署参数生成 patch 片段，**写给人审核**；
//          ⛔ 不写 profile、不调安装器、不重启宿主、不联网。
// ORIGIN: 原项目 `install/*/cordis.patch.yml` 里被物化的本机值（绝对路径、项目名、队列目录）；
//          这里把这些值全部改成输入，只留结构
// CUMULATIVE-DELTA: 首版。四点边界：
//   ① 所有路径/项目范围/阈值/目标策略都来自 `--deployment` JSON，⛔ 生成器不内置任何本机值；
//   ② 输出默认打到 stdout；给了 `--out` 也只写**指定文件**；若目标路径看着像 profile（含 `.dsh` 或 profile 目录）就拒绝，需 `--allow-profile-path`；
//   ③ 出厂形态 = 全部关闭或 hold：doc-gap `enabled:false`、instruction-filter `mode:off`、autodl-events `fallback:hold`；
//   ④ ⛔ 生成成功不等于已加载 —— 加载要人复核后走安装器，验收见 docs/dsh.md 三层。
//
// 用法：
//   node gen-profile-patch.mjs --deployment deployment.json [--out patch.yml] [--allow-profile-path]
//   node gen-profile-patch.mjs --deployment deployment.json --only autodl-events
//   node gen-profile-patch.mjs --print-schema
//
// 退出码：0 = 生成完成 · 1 = 输入不合法（逐条点名）· 2 = 未执行（没给输入 / 读不到 / 拒绝写 profile 路径）

import { readFileSync, writeFileSync } from 'node:fs'
import { isAbsolute, normalize, resolve, sep } from 'node:path'

const PLUGIN_IDS = ['agent-instruction-filter', 'doc-gap', 'autodl-events']
const BUNDLED_NAMES = {
  'agent-instruction-filter': '@autoresearch/dsh-agent-instruction-filter',
  'doc-gap': '@autoresearch/dsh-doc-gap',
  'autodl-events': '@autoresearch/dsh-autodl-events',
}

function parseArgs(argv) {
  const out = { only: [] }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--deployment') out.deployment = argv[++i]
    else if (arg === '--out') out.out = argv[++i]
    else if (arg === '--only') out.only.push(argv[++i])
    else if (arg === '--allow-profile-path') out.allowProfilePath = true
    else if (arg === '--print-schema') out.printSchema = true
    else if (arg === '--help' || arg === '-h') out.help = true
    else out.unknown = arg
  }
  return out
}

function usage(code) {
  const text = [
    '用法：node gen-profile-patch.mjs --deployment <deployment.json> [--out <patch.yml>]',
    '                                [--only <id>] [--allow-profile-path] [--print-schema]',
    '',
    '它只**生成**配置文件，不写 profile、不装插件、不重启宿主。',
    '退出码：0 生成完成 · 1 输入不合法 · 2 未执行。',
  ].join('\n')
  if (code === 0) console.log(text)
  else console.error(text)
  process.exit(code)
}

const args = parseArgs(process.argv.slice(2))
if (args.help === true) usage(0)
if (args.unknown !== undefined) {
  console.error(`未执行：不认识的参数 ${args.unknown}`)
  usage(2)
}
if (args.printSchema === true) {
  console.log(JSON.stringify(schemaExample(), null, 2))
  process.exit(0)
}
if (!args.deployment) usage(2)

function schemaExample() {
  return {
    workspace: '<项目根绝对路径>',
    plugins: {
      'agent-instruction-filter': {
        enabled: false,
        mode: 'off',
        childOnly: true,
        policyPath: null,
        onError: 'keep',
      },
      'doc-gap': {
        enabled: false,
        pluginPath: null,
        threshold: 0.8,
        notify: 'hint',
        maxDocChars: 20000,
        projects: [],
        docs: [],
        policy: {
          scope: { include: [], exclude: [] },
          criteria: { covered: '', partial: '', missing: '' },
          anchor: '',
        },
        judge: { backend: 'session', timeoutMs: 20000, maxTokens: 4000 },
      },
      'autodl-events': {
        enabled: true,
        pluginPath: null,
        pollMs: 5000,
        maxAgeMinutes: 45,
        delivery: 'steer',
        maxTargets: 1,
        fallback: 'hold',
        projects: [],
      },
    },
  }
}

let deployment
try {
  // ⚠️ 去掉可能的 UTF-8 BOM：Windows 上不少编辑器/PowerShell 会写带 BOM 的 JSON，JSON.parse 不认。
  deployment = JSON.parse(readFileSync(resolve(args.deployment), 'utf8').replace(/^\uFEFF/, ''))
} catch (error) {
  console.error(`未执行：部署参数读不到或不是 JSON：${args.deployment}（${error.message}）`)
  process.exit(2)
}

const problems = []
const workspace = typeof deployment.workspace === 'string' ? deployment.workspace.trim() : ''
if (!workspace) problems.push('deployment.workspace 必填（项目根绝对路径；运行态落在 <workspace>/.autoresearch/）')
else if (!isAbsolute(workspace)) problems.push('deployment.workspace 必须是绝对路径')

const plugins = deployment.plugins && typeof deployment.plugins === 'object' ? deployment.plugins : {}
if (Object.keys(plugins).length === 0) problems.push('deployment.plugins 里至少要有一个插件的配置')

for (const id of Object.keys(plugins)) {
  if (!PLUGIN_IDS.includes(id)) problems.push(`不认识的插件 id：${id}（已知：${PLUGIN_IDS.join(', ')}）`)
}

const runtimeEvents = workspace
  ? joinPath(workspace, '.autoresearch', 'events')
  : '<workspace>/.autoresearch/events'

function joinPath(base, ...parts) {
  const text = [base.replace(/[\\/]+$/, ''), ...parts].join('/')
  return text.replace(/\/{2,}/g, '/')
}

function scalar(value) {
  if (value === null || value === undefined) return 'null'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return `'${String(value).replace(/'/g, "''")}'`
}

/** 极小 YAML 生成器：对象 / 标量数组 / 对象数组 / 嵌套对象。 */
function yaml(value, indent) {
  const pad = ' '.repeat(indent)
  if (Array.isArray(value)) {
    if (value.length === 0) return `${pad}[]`
    return value.map((item) => {
      if (item !== null && typeof item === 'object' && !Array.isArray(item)) {
        const entries = Object.entries(item).filter(([, entry]) => entry !== undefined)
        if (entries.length === 0) return `${pad}- {}`
        const lines = []
        entries.forEach(([key, entry], index) => {
          const prefix = index === 0 ? `${pad}- ` : `${pad}  `
          if (entry !== null && typeof entry === 'object') {
            lines.push(`${prefix}${key}:`)
            lines.push(yaml(entry, indent + 4))
          } else {
            lines.push(`${prefix}${key}: ${scalar(entry)}`)
          }
        })
        return lines.join('\n')
      }
      return `${pad}- ${scalar(item)}`
    }).join('\n')
  }
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value).filter(([, item]) => item !== undefined)
    if (entries.length === 0) return `${pad}{}`
    return entries.map(([key, item]) => {
      if (Array.isArray(item)) {
        if (item.length === 0) return `${pad}${key}: []`
        return `${pad}${key}:\n${yaml(item, indent + 2)}`
      }
      if (item !== null && typeof item === 'object') {
        return `${pad}${key}:\n${yaml(item, indent + 2)}`
      }
      return `${pad}${key}: ${scalar(item)}`
    }).join('\n')
  }
  return `${pad}${scalar(value)}`
}

function pluginName(id, config) {
  if (typeof config.pluginPath === 'string' && config.pluginPath.trim()) {
    const base = config.pluginPath.trim().replace(/[\\/]+$/, '')
    if (id === 'autodl-events' && !base.endsWith('.js')) return `${base}/lib/index.js`
    return base
  }
  return BUNDLED_NAMES[id]
}

function buildFilter(config) {
  // bundle 的两种挂法都合法：安装器 install_bundle（用包名）或按绝对路径 insert（用目录路径）。
  return {
    mode: config.mode === 'filter' || config.mode === 'strip' ? config.mode : 'off',
    childOnly: config.childOnly !== false,
    policyPath: typeof config.policyPath === 'string' && config.policyPath.trim() ? config.policyPath.trim() : null,
    onError: config.onError === 'drop' ? 'drop' : 'keep',
  }
}

function buildDocGap(config) {
  const docs = Array.isArray(config.docs) ? config.docs : []
  for (const [index, doc] of docs.entries()) {
    if (!doc || typeof doc.path !== 'string' || !doc.path.trim()) {
      problems.push(`doc-gap.docs[${index}] 缺少 path`)
      continue
    }
    if (!isAbsolute(doc.path)) problems.push(`doc-gap.docs[${index}].path 必须是绝对路径：${doc.path}`)
  }
  const include = Array.isArray(config.policy?.scope?.include) ? config.policy.scope.include : []
  const exclude = Array.isArray(config.policy?.scope?.exclude) ? config.policy.scope.exclude : []
  if (config.enabled === true) {
    if (docs.length === 0) problems.push('doc-gap.enabled=true 但 docs 为空：启用后什么也不会判')
    if (include.length === 0) problems.push('doc-gap.enabled=true 但 policy.scope.include 为空：判定范围必须由部署方给出')
    if (exclude.length === 0) problems.push('doc-gap.enabled=true 但 policy.scope.exclude 为空：排除清单必须由部署方给出')
    if (typeof config.policy?.anchor !== 'string' || !config.policy.anchor.trim()) {
      problems.push('doc-gap.enabled=true 但 policy.anchor 为空：结尾锚点必须由部署方给出')
    }
  }
  return {
    enabled: config.enabled === true,
    threshold: Number.isFinite(config.threshold) ? config.threshold : 0.8,
    notify: config.notify === 'ask' ? 'ask' : 'hint',
    logLevel: typeof config.logLevel === 'string' ? config.logLevel : 'info',
    projects: Array.isArray(config.projects) ? config.projects : [],
    docs: docs.filter((doc) => doc && typeof doc.path === 'string' && doc.path.trim())
      .map((doc) => ({ label: doc.label ?? undefined, path: doc.path, maxChars: doc.maxChars })),
    maxDocChars: Number.isFinite(config.maxDocChars) ? config.maxDocChars : 20000,
    policy: {
      scope: {
        include,
        exclude,
      },
      criteria: {
        covered: config.policy?.criteria?.covered ?? '',
        partial: config.policy?.criteria?.partial ?? '',
        missing: config.policy?.criteria?.missing ?? '',
      },
      anchor: config.policy?.anchor ?? '',
    },
    judge: {
      backend: ['session', 'jev', 'openai'].includes(config.judge?.backend) ? config.judge.backend : 'session',
      timeoutMs: Number.isFinite(config.judge?.timeoutMs) ? config.judge.timeoutMs : 20000,
      maxTokens: Number.isFinite(config.judge?.maxTokens) ? config.judge.maxTokens : 4000,
    },
  }
}

function buildEvents(config) {
  const projects = Array.isArray(config.projects) && config.projects.length > 0
    ? config.projects
    : (workspace ? [workspace] : [])
  for (const entry of projects) {
    if (!isAbsolute(entry)) problems.push(`autodl-events.projects 里的每一项都必须是绝对路径（规范化路径前缀）：${entry}`)
  }
  const eventDir = typeof config.eventDir === 'string' && config.eventDir.trim()
    ? config.eventDir.trim()
    : joinPath(runtimeEvents, 'pending')
  const deliveredDir = typeof config.deliveredDir === 'string' && config.deliveredDir.trim()
    ? config.deliveredDir.trim()
    : joinPath(runtimeEvents, 'delivered')
  const expiredDir = typeof config.expiredDir === 'string' && config.expiredDir.trim()
    ? config.expiredDir.trim()
    : joinPath(runtimeEvents, 'expired')
  const logFile = typeof config.logFile === 'string' && config.logFile.trim()
    ? config.logFile.trim()
    : joinPath(runtimeEvents, 'delivered.jsonl')
  const targetsFile = typeof config.targetsFile === 'string' && config.targetsFile.trim()
    ? config.targetsFile.trim()
    : joinPath(runtimeEvents, 'targets.json')
  for (const [key, path] of Object.entries({ eventDir, deliveredDir, expiredDir, logFile, targetsFile })) {
    if (!isAbsolute(path)) problems.push(`autodl-events.${key} 必须是绝对路径：${path}`)
  }
  return {
    enabled: config.enabled !== false,
    eventDir,
    deliveredDir,
    expiredDir,
    logFile,
    targetsFile,
    pollMs: Number.isFinite(config.pollMs) ? config.pollMs : 5000,
    maxAgeMinutes: Number.isFinite(config.maxAgeMinutes) ? config.maxAgeMinutes : 45,
    delivery: config.delivery === 'inject' ? 'inject' : 'steer',
    maxTargets: Number.isFinite(config.maxTargets) ? config.maxTargets : 1,
    fallback: config.fallback === 'pick' ? 'pick' : 'hold',
    projects,
    reviewHint: typeof config.reviewHint === 'string' && config.reviewHint.trim()
      ? config.reviewHint.trim()
      : '事件只说明「看起来能开」，没有试开机。要复核请用部署方提供的状态检查入口。',
  }
}

const builders = {
  'agent-instruction-filter': buildFilter,
  'doc-gap': buildDocGap,
  'autodl-events': buildEvents,
}

const selected = args.only.length > 0 ? args.only : PLUGIN_IDS.filter((id) => id in plugins)
for (const id of args.only) {
  if (!PLUGIN_IDS.includes(id)) problems.push(`--only 里有不认识的插件 id：${id}`)
}

// ⚠️ 先跑 build（各 build 会往 problems 里加「启用但没配齐」这类问题），再统一判。
const blocks = []
for (const id of selected) {
  if (!(id in plugins)) {
    console.error(`未执行：--only 指定了 ${id}，但部署参数里没有它`)
    process.exit(2)
  }
  const config = builders[id](plugins[id])
  blocks.push({ id, name: pluginName(id, plugins[id]), config })
}

if (problems.length > 0) {
  console.error(`输入不合法（${problems.length} 条），未生成：`)
  for (const problem of problems) console.error(`  ✗ ${problem}`)
  process.exit(1)
}

function localStamp() {
  const now = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  const offset = -now.getTimezoneOffset()
  const sign = offset >= 0 ? '+' : '-'
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} `
    + `${pad(now.getHours())}:${pad(now.getMinutes())} `
    + `${sign}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`
}

const header = [
  '# GENERATED-BY: gen-profile-patch.mjs（参数化生成，供人工复核）',
  `# GENERATED-AT: ${localStamp()}`,
  '# PURPOSE: DSH 插件加载配置片段。⛔ 本文件不会自动生效，也不会被本工具写进 profile。',
  '# 使用方式见 docs/dsh.md §装载：人复核 → 安装器 install_bundle / apply patch → 重启宿主 → 三层验收。',
  '# ⚠️ 生成成功 ≠ 已加载；文件已改 ≠ 目标会话已使用。',
]

const body = blocks.map((block) => {
  const configYaml = yaml(block.config, 8)
  return [
    '- insert:',
    `    - id: ${block.id}`,
    `      name: ${scalar(block.name)}`,
    configYaml.length > 0 ? '      config:' : null,
    configYaml.length > 0 ? configYaml : null,
  ].filter(Boolean).join('\n')
}).join('\n')

const output = `${header.join('\n')}\n\n${body}\n`

if (args.out) {
  const outPath = resolve(args.out)
  const normalized = normalize(outPath)
  const looksLikeProfile = normalized.split(sep).some((part) => part === '.dsh' || part.startsWith('profiles'))
  if (looksLikeProfile && args.allowProfilePath !== true) {
    console.error(`未执行：输出路径看起来在 DSH profile 里（${outPath}）。`)
    console.error('本工具只生成「给人审核」的文件。确要写那里，请加 --allow-profile-path，并自行承担后果。')
    process.exit(2)
  }
  try {
    writeFileSync(outPath, output, 'utf8')
  } catch (error) {
    console.error(`未执行：写不进 ${outPath}（${error.message}）`)
    process.exit(2)
  }
  console.error(`已生成：${outPath}（${blocks.length} 个插件条目）。⛔ 仍要人复核 + 安装器装载。`)
} else {
  process.stdout.write(output)
}

console.log('')
console.log(`条目：${blocks.map((block) => block.id).join('、')}`)
console.log('下一步（人做）：① 复核本文件 ② 按 docs/dsh.md 走安装器 ③ 重启宿主 ④ 按三层验收取证据。')
