/**
 * GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * PURPOSE: dsh-doc-gap 的只读量具 —— 按部署方给的文档清单算「单回合上送多少字符、离截断还有多远」。
 * ORIGIN: install/dsh-doc-gap/measure.mjs（去实例路径：文档清单与上限都从输入取）
 * CUMULATIVE-DELTA: 首版。⛔ 不内置任何文档路径；缺输入时按「未执行」退出。
 *   退出码：0 = 全部未超限 · 1 = 有文档超上限（必须诊断出来）· 2 = 未执行（清单读不到 / 文件缺失）
 *
 * 用法：node measure.mjs --config measure.config.json
 *
 * 配置形态：
 *   {
 *     "maxDocChars": 20000,
 *     "maxAnswerChars": 6000,
 *     "maxQuestionChars": 2000,
 *     "docs": [ { "label": "规则", "path": "<路径>", "maxChars": 8000 } ]
 *   }
 */

import fs from 'node:fs'
import path from 'node:path'

function parseArgs(argv) {
  const out = { config: null }
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--config') out.config = argv[++i]
    else if (argv[i] === '--help' || argv[i] === '-h') out.help = true
    else out.unknown = argv[i]
  }
  return out
}

function notExecuted(message) {
  console.error(`未执行：${message}`)
  console.error('用法：node measure.mjs --config <config.json>')
  process.exit(2)
}

const args = parseArgs(process.argv.slice(2))
if (args.help === true) {
  console.log('只读量具：按给定文档清单算上送字符与截断余量。无输入时按「未执行」退出（exit 2）。')
  process.exit(0)
}
if (args.unknown !== undefined) notExecuted(`不认识的参数 ${args.unknown}`)
if (!args.config) notExecuted('没有给 --config')

let cfg
try {
  cfg = JSON.parse(fs.readFileSync(path.resolve(args.config), 'utf8').replace(/^\uFEFF/, ''))
} catch (error) {
  notExecuted(`配置读不到或不是 JSON：${args.config}（${error.message}）`)
}

const docs = Array.isArray(cfg.docs) ? cfg.docs.filter((d) => d && typeof d.path === 'string') : []
if (docs.length === 0) notExecuted('配置里没有 docs 列表')

const maxDocChars = Number.isFinite(cfg.maxDocChars) && cfg.maxDocChars > 0 ? Math.floor(cfg.maxDocChars) : 20000
const maxAnswerChars = Number.isFinite(cfg.maxAnswerChars) && cfg.maxAnswerChars > 0 ? Math.floor(cfg.maxAnswerChars) : 6000
const maxQuestionChars = Number.isFinite(cfg.maxQuestionChars) && cfg.maxQuestionChars > 0 ? Math.floor(cfg.maxQuestionChars) : 2000

// 粗估：中日韩字符约 1 字符 ≈ 0.6 token，英文约 1 字符 ≈ 0.25 token。这里统一按 0.6 估，并标注是粗估。
const TOKENS_PER_CHAR = 0.6

const rows = []
let totalChars = 0
let anyOver = false
let anyMissing = false
let headroom = Infinity

for (const doc of docs) {
  const label = doc.label ?? path.basename(doc.path)
  const limit = Number.isFinite(doc.maxChars) && doc.maxChars > 0 ? Math.floor(doc.maxChars) : maxDocChars
  let chars = 0
  let exists = false
  try {
    chars = fs.readFileSync(doc.path, 'utf8').length
    exists = true
  } catch {}
  if (!exists) anyMissing = true
  const effective = exists ? Math.min(chars, limit) : 0
  const overCap = exists && chars > limit
  if (overCap) anyOver = true
  if (exists) headroom = Math.min(headroom, limit - chars)
  totalChars += effective
  rows.push({ label, path: doc.path, exists, chars, effective, limit, overCap })
}

console.log('判据文档')
console.log('| 文档 | 存在 | 字符数 | 上限 | 实际上送 | 状态 |')
console.log('|---|---|---:|---:|---:|---|')
for (const r of rows) {
  const status = !r.exists ? '**缺失（未执行）**' : r.overCap ? `**超过上限（截断，判定材料不完整）**` : '正常'
  console.log(`| ${r.label} | ${r.exists ? '是' : '否'} | ${r.chars} | ${r.limit} | ${r.effective} | ${status} |`)
}

const perTurnChars = totalChars + maxAnswerChars + maxQuestionChars
const perTurnTokens = Math.round(perTurnChars * TOKENS_PER_CHAR)

console.log('')
console.log(`单回合上送字符上限：${perTurnChars}（判据 ${totalChars} + 答复上限 ${maxAnswerChars} + 提问上限 ${maxQuestionChars}）`)
console.log(`粗估输入 token/回合：${perTurnTokens}（按 ${TOKENS_PER_CHAR} token/字符粗估，非实测）`)
if (Number.isFinite(headroom)) {
  console.log(headroom >= 0 ? `最紧的一份离上限还有 ${headroom} 字符` : `⚠️ 最紧的一份已超出上限 ${-headroom} 字符 —— 它会被截断，插件会在 stderr 写诊断`)
} else {
  console.log('⚠️ 没有一份文档读得到，算不出余量')
}
console.log('数值会随文档变动，别抄进任何文档，现跑为准。')

if (anyMissing) process.exit(2)
process.exit(anyOver ? 1 : 0)
