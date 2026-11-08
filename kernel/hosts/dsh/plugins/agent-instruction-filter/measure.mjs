// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 只读量具 —— 对**部署方给定的文件清单**跑一遍策略，输出逐文件 before/after 字节表。
// ORIGIN: install/agent-instruction-filter/measure.mjs（去实例路径：文件清单与策略都从输入取）
// CUMULATIVE-DELTA: 首版。⛔ 不内置任何文件清单；缺输入时**不猜**，按「未执行」退出（exit 2）。
//   只读：不写任何文件、不联网、不加载宿主。
//
// 用法：
//   node measure.mjs --config measure.config.json
//   node measure.mjs --config measure.config.json --policy ./policy.mjs
//   node measure.mjs --root <目录> --file <相对路径> [--file <相对路径> ...]
//
// 配置形态（JSON）：
//   { "files": [ {"label": "全局偏好", "path": "<绝对或相对路径>"} ] }
//   `path` 相对路径按 **当前工作目录** 解析；label 缺省用文件名。
//
// 退出码：0 = 量完了 · 2 = 未执行（没给输入 / 输入读不到）。

import { readFileSync } from 'node:fs'
import { isAbsolute, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { filterInstructionText } from './filter-core.mjs'
import { policy as bundledPolicy } from './policy.mjs'

const OPEN = '<system-reminder>'
const CLOSE = '</system-reminder>'

function parseArgs(argv) {
  const out = { config: null, policy: null, root: null, files: [] }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--config') out.config = argv[++i]
    else if (arg === '--policy') out.policy = argv[++i]
    else if (arg === '--root') out.root = argv[++i]
    else if (arg === '--file') out.files.push(argv[++i])
    else if (arg === '--help' || arg === '-h') out.help = true
    else out.unknown = arg
  }
  return out
}

function fail(message) {
  console.error(`未执行：${message}`)
  console.error('用法：node measure.mjs --config <config.json> [--policy <policy.mjs>]')
  console.error('   或：node measure.mjs --root <目录> --file <相对路径> [--file ...]')
  process.exit(2)
}

const args = parseArgs(process.argv.slice(2))
if (args.help === true) {
  console.log('只读量具：按给定文件清单套策略，输出 before/after 字节表。')
  console.log('用法见本文件头部注释；无输入时按「未执行」退出（exit 2）。')
  process.exit(0)
}
if (args.unknown !== undefined) fail(`不认识的参数 ${args.unknown}`)

/** 解析文件清单：要么 --config，要么 --root + --file。 */
function collectFiles() {
  if (args.config) {
    let doc
    try {
      doc = JSON.parse(readFileSync(resolve(args.config), 'utf8').replace(/^\uFEFF/, ''))
    } catch (error) {
      fail(`配置文件读不到或不是 JSON：${args.config}（${error.message}）`)
    }
    const list = Array.isArray(doc?.files) ? doc.files : []
    if (list.length === 0) fail('配置里没有 files 列表')
    return list
      .filter((entry) => entry && typeof entry.path === 'string' && entry.path.length > 0)
      .map((entry) => ({ label: entry.label ?? entry.path, path: entry.path }))
  }
  if (args.files.length > 0) {
    if (!args.root) fail('给了 --file 就必须同时给 --root')
    return args.files.map((rel) => ({ label: rel, path: join(args.root, rel) }))
  }
  fail('没有给文件清单（--config 或 --root + --file）')
  return []
}

async function resolvePolicy() {
  if (!args.policy) return bundledPolicy
  const url = pathToFileURL(isAbsolute(args.policy) ? args.policy : resolve(args.policy)).href
  try {
    const mod = await import(url)
    if (!mod?.policy) fail(`策略模块没有导出 policy：${args.policy}`)
    return mod.policy
  } catch (error) {
    fail(`策略模块加载失败：${args.policy}（${error.message}）`)
  }
  return null
}

const files = collectFiles()
const activePolicy = await resolvePolicy()

const bytes = (s) => Buffer.byteLength(s, 'utf8')
const renderOne = (label, content) => {
  // ⛔ 不裁尾部空白：字节账要能逐字对上，否则「keep 策略」也会算出被砍了几个字节。
  const text = [OPEN, `Instructions from: ${label}`, '', content, CLOSE].join('\n')
  return filterInstructionText(text, activePolicy).text
}

const rows = []
const missing = []
let totalBefore = 0
let totalAfter = 0
const openBytes = bytes(OPEN)
const closeBytes = bytes(CLOSE)

for (const file of files) {
  const absolute = isAbsolute(file.path) ? file.path : resolve(file.path)
  let content
  try {
    content = readFileSync(absolute, 'utf8')
  } catch {
    missing.push(absolute)
    continue
  }
  const one = renderOne(file.label, content)
  const b = bytes(content)
  // bytes(one) = OPEN + "\n" + marker + "\n\n" + 内容 + "\n" + CLOSE
  // ⇒ 内容字节 = 总量 − 两个标签 − 4 个换行 − marker 那一行
  const a = one === '' ? 0 : bytes(one) - openBytes - closeBytes - 4 - bytes(`Instructions from: ${file.label}`)
  totalBefore += b
  totalAfter += a
  rows.push({ label: file.label, path: absolute, before: b, after: a, cut: b - a })
}

if (rows.length === 0) fail('清单里一个文件都读不到（未执行）')

console.log('')
console.log('| 文件 | 原文 B | 裁剪后 B | 砍掉 B | 砍掉 % |')
console.log('|---|---|---|---|---|')
for (const row of rows) {
  const pct = row.before === 0 ? '0.0' : ((row.cut / row.before) * 100).toFixed(1)
  console.log(`| \`${row.label}\` | ${row.before} | ${row.after} | ${row.cut} | ${pct}% |`)
}
console.log(`| **合计（${rows.length} 份）** | **${totalBefore}** | **${totalAfter}** | **${totalBefore - totalAfter}** | **${totalBefore === 0 ? '0.0' : (((totalBefore - totalAfter) / totalBefore) * 100).toFixed(1)}%** |`)
console.log('')
console.log(`策略来源：${args.policy ? args.policy : 'bundled policy.mjs（出厂中性）'}`)
console.log('⚠️ 即便 keep 策略也可能差 1–2 B：CRLF 归一与段尾空白裁掉是本插件唯一的非策略性改动（不减任何规则）。')
if (missing.length > 0) {
  console.log(`⚠️ 清单里有 ${missing.length} 个文件读不到（未计入表）：`)
  for (const path of missing) console.log(`  - ${path}`)
}
console.log('数值会随被量文件变动，别抄进任何文档，现跑为准。')
process.exit(missing.length > 0 ? 2 : 0)
