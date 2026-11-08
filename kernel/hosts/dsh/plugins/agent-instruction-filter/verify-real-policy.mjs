// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 真实文件关卡 —— 在**部署方给定的真实文件**上按内容逐条核策略，补合成夹具测不到的结构偏差。
// ORIGIN: install/agent-instruction-filter/verify-real-policy.mjs（去实例清单：文件与预期值都从输入取）
// CUMULATIVE-DELTA: 首版。三条判定分档：**通过 / 不符 / 未执行**，退出码 0 / 1 / 2 ——
//   ⛔ 「一个文件都读不到」不再算通过。预期值只认 `expect` 与 `must*` 两类，⛔ 不按行号。
//
// 用法：
//   node verify-real-policy.mjs --config verify.config.json [--policy ./policy.mjs]
//
// 配置形态（JSON，⛔ 不要在本文件里写死任何实例清单）：
//   {
//     "files": [
//       { "label": "全局偏好", "path": "<路径>", "expect": "keep",
//         "mustContain": ["用中文回复用户"] },
//       { "label": "项目根", "path": "<路径>", "expect": "drop" },
//       { "label": "另一份", "path": "<路径>", "expect": "present",
//         "mustNotContain": ["GENERATED-BY:"] }
//     ],
//     "combine": true
//   }
//
// expect 取值：
//   keep    裁剪后该段与原文逐字一致（只可能差尾部换行归一）
//   drop    裁剪后整段消失，且该文件任何正文都不残留
//   present 裁剪后该段仍在（必须与 mustContain 一起用才有意义）
//   absent  裁剪后该段不在
//
// 退出码：0 = 全部符合预期 · 1 = 有任一条不符（逐条点名）· 2 = 未执行（没给配置 / 配置读不到 / 策略读不到 / 一个文件都读不到）

import { readFileSync } from 'node:fs'
import { isAbsolute, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { filterInstructionText } from './filter-core.mjs'
import { policy as bundledPolicy } from './policy.mjs'

const OPEN = '<system-reminder>'
const CLOSE = '</system-reminder>'
const ALLOWED_EXPECT = new Set(['keep', 'drop', 'present', 'absent'])

function parseArgs(argv) {
  const out = { config: null, policy: null }
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--config') out.config = argv[++i]
    else if (argv[i] === '--policy') out.policy = argv[++i]
    else if (argv[i] === '--help' || argv[i] === '-h') out.help = true
    else out.unknown = argv[i]
  }
  return out
}

function notExecuted(message) {
  console.log(`NOT-EXECUTED: ${message}`)
  process.exit(2)
}

const args = parseArgs(process.argv.slice(2))
if (args.help === true) {
  console.log('真实文件关卡：按配置里的 files 逐条核策略。退出码 0 通过 / 1 不符 / 2 未执行。')
  process.exit(0)
}
if (args.unknown !== undefined) notExecuted(`不认识的参数 ${args.unknown}`)
if (!args.config) notExecuted('没有给 --config（不知道要核哪些真实文件）')

let doc
try {
  doc = JSON.parse(readFileSync(resolve(args.config), 'utf8').replace(/^\uFEFF/, ''))
} catch (error) {
  notExecuted(`配置文件读不到或不是 JSON：${args.config}（${error.message}）`)
}

const files = Array.isArray(doc?.files) ? doc.files : []
if (files.length === 0) notExecuted('配置里没有 files 列表')
for (const [index, entry] of files.entries()) {
  if (!entry || typeof entry.path !== 'string' || entry.path.length === 0) {
    notExecuted(`files[${index}] 没有 path`)
  }
  if (entry.expect !== undefined && !ALLOWED_EXPECT.has(entry.expect)) {
    notExecuted(`files[${index}].expect 非法：${String(entry.expect)}`)
  }
}

let activePolicy = bundledPolicy
if (args.policy) {
  const url = pathToFileURL(isAbsolute(args.policy) ? args.policy : resolve(args.policy)).href
  try {
    const mod = await import(url)
    if (!mod?.policy) notExecuted(`策略模块没有导出 policy：${args.policy}`)
    activePolicy = mod.policy
  } catch (error) {
    notExecuted(`策略模块加载失败：${args.policy}（${error.message}）`)
  }
}

function renderOne(label, content) {
  const text = [OPEN, `Instructions from: ${label}`, '', content, CLOSE].join('\n')
  return filterInstructionText(text, activePolicy).text
}

let passed = 0
const failures = []
const skipped = []

function expect(label, condition, detail = '') {
  if (condition) {
    passed += 1
    console.log(`  OK    ${label}`)
  } else {
    failures.push(label)
    console.log(`  FAIL  ${label}${detail ? `  —— ${detail}` : ''}`)
  }
}

function resolvePath(p) {
  return isAbsolute(p) ? p : resolve(p)
}

const loaded = []
for (const entry of files) {
  const absolute = resolvePath(entry.path)
  const label = entry.label ?? entry.path
  let content
  try {
    content = readFileSync(absolute, 'utf8')
  } catch (error) {
    skipped.push(`${label}（${absolute}：${error.code ?? error.message}）`)
    continue
  }
  loaded.push({ label, absolute, content, expect: entry.expect, entry })
}

if (loaded.length === 0) {
  notExecuted(`清单里 ${files.length} 个文件一个都读不到：\n  ${skipped.join('\n  ')}`)
}

for (const item of loaded) {
  const one = renderOne(item.label, item.content)
  const mode = item.expect ?? 'present'
  console.log(`== ${item.label}（expect=${mode}）==`)
  if (mode === 'keep') {
    // 「保留」按**内容**判：正文要逐字出现在裁剪后的消息里（只允许 CRLF 归一）。
    // ⛔ 不拿整条消息的字符数与文件比 —— 消息本身还带 frame 与段标记。
    const normalized = item.content.replace(/\r\n/g, '\n')
    expect('正文逐字保留（只允许 CRLF 归一）', one.includes(normalized) || one.includes(normalized.trimEnd()),
      `${normalized.length} 字符的正文没有原样出现`)
  } else if (mode === 'drop') {
    expect('整段消失（含段标记与文件头）', one === '', `仍剩 ${one.length} 字符`)
  } else if (mode === 'absent') {
    expect('该段不在', one === '', `仍剩 ${one.length} 字符`)
  } else {
    expect('该段仍在', one.includes(`Instructions from: ${item.label}`))
  }
  for (const needle of item.entry.mustContain ?? []) {
    expect(`含「${needle}」`, one.includes(needle))
  }
  for (const needle of item.entry.mustNotContain ?? []) {
    expect(`不含「${needle}」`, !one.includes(needle))
  }
}

if (doc.combine === true && loaded.length > 1) {
  console.log('== 合成一条基线消息 ==')
  const body = loaded.map((item) => `Instructions from: ${item.label}\n\n${item.content.replace(/\s+$/, '')}`)
  const combined = filterInstructionText([OPEN, 'The following workspace instructions may be relevant to your work.', '', ...body, CLOSE].join('\n'), activePolicy).text
  expect('合成后仍是合法的 system-reminder 消息', combined.startsWith(OPEN) && combined.endsWith(CLOSE))
  for (const item of loaded) {
    if ((item.expect ?? 'present') === 'drop' || item.expect === 'absent') {
      expect(`合成后不含「${item.label}」段`, !combined.includes(`Instructions from: ${item.label}`))
    }
  }
}

console.log('')
if (skipped.length > 0) {
  console.log(`SKIP：${skipped.length} 个文件读不到（未核）`)
  for (const item of skipped) console.log(`  - ${item}`)
}
if (failures.length > 0) {
  console.log(`FAIL: ${passed} 条通过 · ${failures.length} 条不符（另有 ${skipped.length} 个文件未核）`)
  for (const item of failures) console.log(`  ✗ ${item}`)
  process.exit(1)
}
if (skipped.length > 0) {
  console.log(`NOT-EXECUTED: 有 ${skipped.length} 个文件读不到，判定不完整（通过 ${passed} 条）`)
  process.exit(2)
}
console.log(`PASS: 真实文件上 ${passed} 条逐条符合预期`)
process.exit(0)
