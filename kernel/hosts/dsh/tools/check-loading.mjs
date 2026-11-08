// GENERATED-BY: portable extraction (agent, 2026-11-09 01:20 +08:00)
// LAST-EDIT: portable extraction (agent, 2026-11-09 01:20 +08:00)
// PURPOSE: 加载验收助手 —— 把「文件已改 / 宿主已加载 / 目标会话实际使用」三层证据分开检查，
//          ⛔ 不用一条证据冒充另一条。
// ORIGIN: INSTALL.md 与 autodl-events/README 里的三层验收口径（把它们做成可跑工具）
// CUMULATIVE-DELTA: 首版。三层各自独立判定：
//   ① 配置已写：patch 文件存在、含预期的插件 id、config 键齐全；
//   ② 宿主已加载：回执里有 `kind:"apply"`（带 pid 与本次配置里的特征字段）；
//   ③ 目标会话已使用：回执里有 `kind:"delivered"`（或）事件已离开 pending 进 delivered/。
//   退出码：0 = 三层都有证据 · 1 = 只有前两层（或只有第一层）· 2 = 未执行（没给 patch / 文件读不到）。
//
// 用法：
//   node check-loading.mjs --patch <生成的 patch.yml> [--receipt <.../events/delivered.jsonl>]
//                          [--plugin autodl-events]
//
// ⚠️ 本工具只读文件。它不能证明「宿主此刻真的在跑」；那是 `schtasks /Query` 与进程表的事。

import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

function parseArgs(argv) {
  const out = { plugins: [] }
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i]
    if (arg === '--patch') out.patch = argv[++i]
    else if (arg === '--receipt') out.receipt = argv[++i]
    else if (arg === '--plugin') out.plugins.push(argv[++i])
    else if (arg === '--help' || arg === '-h') out.help = true
    else out.unknown = arg
  }
  return out
}

function usage(code) {
  const text = [
    '用法：node check-loading.mjs --patch <patch.yml> [--receipt <delivered.jsonl>] [--plugin <id>]',
    '',
    '三层验收：① 配置已写 ② 宿主已加载 ③ 目标会话实际使用。',
    '退出码：0 三层齐 · 1 缺后两层 · 2 未执行。',
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
if (!args.patch) usage(2)

const patchPath = resolve(args.patch)
if (!existsSync(patchPath)) {
  console.error(`未执行：patch 文件不存在：${patchPath}`)
  process.exit(2)
}

const patchText = readFileSync(patchPath, 'utf8').replace(/^\uFEFF/, '')
const ids = [...patchText.matchAll(/^\s*-\s*id:\s*(\S+)\s*$/gm)].map((match) => match[1])
const wanted = args.plugins.length > 0 ? args.plugins : ids

let layer1 = true
if (ids.length === 0) layer1 = false
for (const id of wanted) {
  if (!ids.includes(id)) layer1 = false
}
if (/- insert:/.test(patchText) === false) layer1 = false

console.log(`层 ① 配置已写：${layer1 ? 'OK' : 'MISSING'}（${ids.length} 个插件条目：${ids.join('、') || '无'}）`)

let receipts = []
let layer2 = false
let layer3 = false
if (args.receipt) {
  const receiptPath = resolve(args.receipt)
  if (existsSync(receiptPath)) {
    for (const line of readFileSync(receiptPath, 'utf8').replace(/^\uFEFF/, '').split('\n')) {
      const text = line.trim()
      if (!text) continue
      try {
        const entry = JSON.parse(text)
        if (entry && typeof entry === 'object') receipts.push(entry)
      } catch {
        // 坏行跳过：回执是 append-only，允许有半行
      }
    }
    layer2 = receipts.some((entry) => entry.kind === 'apply')
    layer3 = receipts.some((entry) => entry.kind === 'delivered')
  } else {
    console.log(`层 ②③：回执文件不存在（${receiptPath}）—— 说明还没跑过或路径不对`)
  }
} else {
  console.log('层 ②③：没有给 --receipt，无法判断「已加载」与「已使用」')
}

console.log(`层 ② 宿主已加载：${layer2 ? 'OK（回执里有 kind:"apply"）' : 'MISSING（没有 apply 回执）'}`)
console.log(`层 ③ 目标会话已使用：${layer3 ? 'OK（回执里有 kind:"delivered"）' : 'MISSING（没有 delivered 回执）'}`)
console.log('')
console.log('⚠️ 层 ① 只证明「准备装什么」，⛔ 不能当已加载；层 ② 只证明 apply() 跑过，⛔ 不能证明目标会话收到；')
console.log('   层 ③ 才是「真的叫醒/投递到了」的证据。热加载不可依赖，改动以后以真正重启宿主为准。')

if (!layer1) process.exit(1)
if (args.receipt && layer2 && layer3) process.exit(0)
if (layer2) process.exit(1)
process.exit(1)
