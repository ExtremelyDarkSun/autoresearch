/**
 * GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
 * PURPOSE: dsh-doc-gap 的真实文件关卡 —— 按部署方给的配置逐条核「文档不超上限 / 读得到 / 政策字段齐」，
 *          并用一份合成的超限样本做**反向对照**，证明这道关卡真的会红。
 * ORIGIN: install/dsh-doc-gap/verify-real-docs.mjs（去实例路径：配置里给文档与政策，⛔ 不内置清单）
 * CUMULATIVE-DELTA: 首版。① 判定分三档：通过 / 不符 / 未执行（退出码 0/1/2）；
 *   ② 文档**超过 maxDocChars 一律判不符**（这正是「超上限必须诊断出来」的机读版本）；
 *   ③ 政策（判定范围 / 判据 / 锚点）非空才算配好 —— 空政策等于没限定范围。
 *   ⛔ 只读：不写配置、不联网、不加载宿主。
 *
 * 用法：node verify-real-docs.mjs --config verify.config.json
 * 配置形态与 measure.config.json 相同，另可给 "policy": { "scope": {...}, "criteria": {...}, "anchor": "..." }。
 */

import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

function parseArgs(argv) {
  const out = {}
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--config') out.config = argv[++i]
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
  console.log('真实文件关卡：核文档是否存在、是否超上限、政策是否配齐。退出码 0 通过 / 1 不符 / 2 未执行。')
  process.exit(0)
}
if (args.unknown !== undefined) notExecuted(`不认识的参数 ${args.unknown}`)
if (!args.config) notExecuted('没有给 --config（不知道要核哪些文档）')

let cfg
try {
  cfg = JSON.parse(fs.readFileSync(path.resolve(args.config), 'utf8').replace(/^\uFEFF/, ''))
} catch (error) {
  notExecuted(`配置读不到或不是 JSON：${args.config}（${error.message}）`)
}

const docs = Array.isArray(cfg.docs) ? cfg.docs.filter((d) => d && typeof d.path === 'string') : []
if (docs.length === 0) notExecuted('配置里没有 docs 列表')

const maxDocChars = Number.isFinite(cfg.maxDocChars) && cfg.maxDocChars > 0 ? Math.floor(cfg.maxDocChars) : 20000

let passed = 0
const failures = []
const skipped = []

function check(label, ok, detail = '') {
  if (ok) {
    passed += 1
    console.log(`  ok   ${label}`)
  } else {
    failures.push(label)
    console.log(`  FAIL ${label}${detail ? `  —— ${detail}` : ''}`)
  }
}

/** 与插件同口径的截断判定：超过上限即为「材料不完整」。 */
function inspect(filePath, limit) {
  const text = fs.readFileSync(filePath, 'utf8')
  return { chars: text.length, overCap: text.length > limit, limit }
}

console.log('判据文档逐份核对')
for (const [index, doc] of docs.entries()) {
  const label = doc.label ?? `#${index + 1}`
  const limit = Number.isFinite(doc.maxChars) && doc.maxChars > 0 ? Math.floor(doc.maxChars) : maxDocChars
  if (!fs.existsSync(doc.path)) {
    skipped.push(`${label}（${doc.path}）`)
    continue
  }
  const info = inspect(doc.path, limit)
  check(`「${label}」非空`, info.chars > 0, `${info.chars} 字符`)
  check(`「${label}」未超上限`, !info.overCap, `原文 ${info.chars} > 上限 ${info.limit}（会被截断，判定材料不完整）`)
}

console.log('判定政策')
const policy = cfg.policy
if (policy === undefined) {
  skipped.push('未提供 policy（无法核判定范围是否外置）')
} else {
  const include = Array.isArray(policy?.scope?.include) ? policy.scope.include.filter((x) => typeof x === 'string' && x.trim()) : []
  const exclude = Array.isArray(policy?.scope?.exclude) ? policy.scope.exclude.filter((x) => typeof x === 'string' && x.trim()) : []
  const anchor = typeof policy?.anchor === 'string' ? policy.anchor.trim() : ''
  const criteria = policy?.criteria ?? {}
  check('判定范围 include 非空（范围必须由部署方给出）', include.length > 0, `读到 ${include.length} 条`)
  check('排除清单 exclude 非空', exclude.length > 0, `读到 ${exclude.length} 条`)
  check('三类判据都有措辞', Boolean(criteria.covered && criteria.partial && criteria.missing))
  check('结尾锚点非空', anchor.length > 0)
}

// 反向对照：故意造一份超限文档，检查器必须判红 —— 证明它不是永远绿
console.log('反向对照（证明关卡真的会红）')
{
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'docgap-verify-'))
  const big = path.join(dir, 'big.md')
  fs.writeFileSync(big, 'x'.repeat(500), 'utf8')
  const info = inspect(big, 100)
  check('超限的文档会被判成「材料不完整」', info.overCap === true, `${info.chars} vs ${info.limit}`)
  fs.rmSync(dir, { recursive: true, force: true })
}

console.log('')
if (skipped.length > 0) {
  console.log(`SKIP：${skipped.length} 项没核（读不到 / 未提供）`)
  for (const item of skipped) console.log(`  - ${item}`)
}
if (failures.length > 0) {
  console.log(`FAIL: ${passed} 条通过 · ${failures.length} 条不符`)
  for (const item of failures) console.log(`  ✗ ${item}`)
  process.exit(1)
}
if (skipped.length > 0) {
  console.log(`NOT-EXECUTED: 有 ${skipped.length} 项没核，判定不完整（通过 ${passed} 条）`)
  process.exit(2)
}
console.log(`PASS: 真实文件上 ${passed} 条逐条符合预期`)
process.exit(0)
